"""Cleanup do bug de vazamento de cards entre usuários (radar).

Antes do fix, se dois usuários usavam o mesmo browser, o cliente do segundo
poderia enviar (no PUT /api/radar/state) os cards remanescentes em localStorage
que pertenciam ao primeiro. O backend então:

  - Marcava o segundo user como dono via `sync_owner_cards` — mas o INSERT
    estourava por PK (card_uid já existia) e o UPDATE só atualizava
    `owner_username` (não `owner_id`). Resultado: linhas com `owner_id` do
    dono original mas `owner_username` do invasor.
  - Salvava o card no `radar_user_state.state_json` do invasor, fazendo o
    card aparecer também na tela dele.

Este script corrige ambos os efeitos colaterais:

  1. Em `radar_card_visibility`, ressincroniza `owner_username` baseado em
     `users.username` para o `owner_id` registrado. Se o user não existe mais,
     limpa o username (NULL).
  2. Em `radar_user_state`, percorre o `state_json` de cada usuário e remove
     cards cujo `card_uid` aponta para outro dono em `radar_card_visibility`.

Idempotente. Rode quantas vezes precisar até a varredura não ter mais nada
para corrigir. Faz commit em transação — em caso de erro nada é salvo.

Uso:
    python scripts/fix_radar_owner_leak.py            # dry-run (default)
    python scripts/fix_radar_owner_leak.py --apply    # aplica as mudanças
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Garante import a partir da raiz do repo, mesmo se rodado por path absoluto.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.db.postgres import close_pool, connect  # noqa: E402


async def fix_owner_username(apply: bool) -> tuple[int, int]:
    """Conserta `owner_username` divergente em radar_card_visibility.

    Devolve (linhas_inspecionadas, linhas_corrigidas).
    """
    inspected = 0
    fixed = 0
    async with connect() as db:
        rows = await db.fetch(
            """
            SELECT rcv.card_uid,
                   rcv.owner_id,
                   rcv.owner_username AS current_username,
                   u.username         AS canonical_username
            FROM radar_card_visibility rcv
            LEFT JOIN users u
              ON u.id::text = rcv.owner_id
            """
        )
        inspected = len(rows)
        for r in rows:
            if r["current_username"] == r["canonical_username"]:
                continue
            fixed += 1
            print(
                f"  · card {r['card_uid']}: owner_id={r['owner_id']} "
                f"username '{r['current_username']}' → '{r['canonical_username']}'"
            )
            if apply:
                await db.execute(
                    "UPDATE radar_card_visibility "
                    "SET owner_username = $1 WHERE card_uid = $2",
                    r["canonical_username"], r["card_uid"],
                )
    return inspected, fixed


async def fix_state_json(apply: bool) -> tuple[int, int]:
    """Remove cards do `state_json` que apontam para dono diferente.

    Devolve (usuários_inspecionados, usuários_corrigidos).
    """
    inspected = 0
    fixed = 0
    async with connect() as db:
        users_rows = await db.fetch(
            "SELECT user_id, state_json, version FROM radar_user_state"
        )
        inspected = len(users_rows)
        # Map de uid → owner_id canônico
        vis_rows = await db.fetch(
            "SELECT card_uid, owner_id FROM radar_card_visibility"
        )
        owner_of: dict[str, str] = {r["card_uid"]: r["owner_id"] for r in vis_rows}

        for u in users_rows:
            user_id = u["user_id"]
            try:
                state = json.loads(u["state_json"] or "[]")
            except Exception:
                continue
            if not isinstance(state, list):
                continue

            removed_uids: list[str] = []
            new_state = []
            for group in state:
                if not isinstance(group, dict):
                    new_state.append(group)
                    continue
                new_cards = []
                for card in (group.get("cards") or []):
                    if not isinstance(card, dict):
                        new_cards.append(card)
                        continue
                    uid = card.get("uid")
                    if uid and uid in owner_of and owner_of[uid] != user_id:
                        removed_uids.append(uid)
                        continue
                    new_cards.append(card)
                group = {**group, "cards": new_cards}
                new_state.append(group)

            if not removed_uids:
                continue
            fixed += 1
            print(
                f"  · user {user_id}: removendo {len(removed_uids)} card(s) "
                f"de outros donos: {removed_uids}"
            )
            if apply:
                new_json = json.dumps(new_state, ensure_ascii=False)
                # Bump de versão — clientes ativos vão notar e refazer load.
                await db.execute(
                    "UPDATE radar_user_state "
                    "SET state_json = $1, version = version + 1, updated_at = NOW() "
                    "WHERE user_id = $2",
                    new_json, user_id,
                )
    return inspected, fixed


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="aplica as mudanças (default: dry-run)",
    )
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[radar leak cleanup · {mode}]")
    print()

    try:
        print("1/2 · radar_card_visibility.owner_username vs users.username")
        ins, fix = await fix_owner_username(args.apply)
        print(f"     {ins} linha(s) inspecionada(s), {fix} divergente(s).")
        print()

        print("2/2 · radar_user_state.state_json: cards de outros donos")
        ins, fix = await fix_state_json(args.apply)
        print(f"     {ins} usuário(s) inspecionado(s), {fix} com cards de outros donos.")
        print()

        if not args.apply:
            print("Nenhuma mudança aplicada (dry-run). Re-rode com --apply.")
    finally:
        # Close the pool inside the same loop that opened it.
        await close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
