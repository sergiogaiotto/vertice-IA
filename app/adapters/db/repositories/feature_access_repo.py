"""Repositório PostgreSQL da matriz "Funcionalidades por Perfil".

Sidecar simples: cada linha de `feature_access` é uma regra explícita
``(role, department, feature_key) -> access (bool)``. O repo expõe CRUD
sobre essa matriz; a semântica de resolução (mais específico vence,
default allow, root bypass) vive em ``FeatureAccessService``.

Decisão de design: NÃO modela "tudo ou nada" como única regra global —
queremos granularidade por (role × dept) para futuras políticas como
"analista_n3 do dept vendas tem /radar, mas do dept compras não".
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.adapters.db.postgres import connect


class PgFeatureAccessRepository:
    """CRUD da tabela `feature_access`.

    Cada operação assume que `role`, `department`, `feature_key` são
    strings limpas (caller responsável por sanear). `department=''` é o
    wildcard "qualquer dept".
    """

    _COLS = (
        "id, role, department, feature_key, access, "
        "created_by_id, created_by_username, "
        "updated_by_id, updated_by_username, "
        "created_at, updated_at"
    )

    async def list_all(self) -> list[dict]:
        """Devolve a matriz completa, ordenada por (role, department,
        feature_key). Usado pela tela /access pra renderizar a grid.
        """
        async with connect() as db:
            rows = await db.fetch(
                f"SELECT {self._COLS} FROM feature_access "
                "ORDER BY role, department, feature_key"
            )
            return [self._row_to_dict(r) for r in rows]

    async def list_for_role(self, role: str) -> list[dict]:
        """Apenas regras para o role dado — usado pelo helper de resolução
        em hot path (cache friendly: 1 query por role do user)."""
        async with connect() as db:
            rows = await db.fetch(
                f"SELECT {self._COLS} FROM feature_access "
                "WHERE role = $1 "
                "ORDER BY department, feature_key",
                role,
            )
            return [self._row_to_dict(r) for r in rows]

    async def upsert(
        self,
        role: str,
        department: str,
        feature_key: str,
        access: bool,
        actor_id: str | None = None,
        actor_username: str | None = None,
    ) -> dict:
        """Cria ou atualiza uma regra. UNIQUE (role, department, feature_key)
        garante idempotência — segundo POST com mesma chave atualiza
        `access` e `updated_*` em vez de duplicar.

        Retorna a linha resultante (após upsert) — útil pra UI feedback.
        """
        async with connect() as db:
            new_id = str(uuid4())
            row = await db.fetchrow(
                f"""
                INSERT INTO feature_access
                  (id, role, department, feature_key, access,
                   created_by_id, created_by_username,
                   updated_by_id, updated_by_username,
                   updated_at)
                VALUES ($1::uuid, $2, $3, $4, $5,
                        $6, $7, $6, $7, NOW())
                ON CONFLICT (role, department, feature_key) DO UPDATE SET
                  access              = EXCLUDED.access,
                  updated_by_id       = EXCLUDED.updated_by_id,
                  updated_by_username = EXCLUDED.updated_by_username,
                  updated_at          = NOW()
                RETURNING {self._COLS}
                """,
                new_id, role, department, feature_key, access,
                actor_id, actor_username,
            )
            return self._row_to_dict(row)

    async def delete(
        self, role: str, department: str, feature_key: str
    ) -> bool:
        """Remove a regra (volta ao default = allow). Retorna True se
        algo foi removido, False se já não havia regra.
        """
        async with connect() as db:
            result = await db.execute(
                "DELETE FROM feature_access "
                "WHERE role = $1 AND department = $2 AND feature_key = $3",
                role, department, feature_key,
            )
            # `DELETE N` — testa N > 0
            try:
                n = int(result.split()[-1])
                return n > 0
            except (ValueError, IndexError):
                return False

    @staticmethod
    def _row_to_dict(row: Any) -> dict:
        return {
            "id":                  str(row["id"]) if row["id"] else None,
            "role":                row["role"],
            "department":          row["department"] or "",
            "feature_key":         row["feature_key"],
            "access":              bool(row["access"]),
            "created_by_id":       row["created_by_id"],
            "created_by_username": row["created_by_username"],
            "updated_by_id":       row["updated_by_id"],
            "updated_by_username": row["updated_by_username"],
            "created_at":          row["created_at"],
            "updated_at":          row["updated_at"],
        }
