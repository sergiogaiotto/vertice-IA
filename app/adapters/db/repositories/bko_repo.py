"""Repositório PostgreSQL para casos do BKO e transcrições."""

from __future__ import annotations

import json
from datetime import datetime

from app.adapters.db.postgres import connect
from app.core.domain.entities import BkoCase, TranscriptRecord


def _row_to_case(row) -> BkoCase:
    return BkoCase(
        case_number=str(row["case_number"]),
        created_by=row["created_by"] or "",
        owner=row["owner"] or "",
        phone=row["phone"] or "",
        opened_at=row["opened_at"] if isinstance(row["opened_at"], datetime) else None,
        contract_msisdn=row["contract_msisdn"] or "",
    )


def _row_to_transcript(row) -> TranscriptRecord:
    raw = row["raw_json"]
    # `raw_json` pode vir como dict (JSONB decodificado) ou string (legado).
    # O dataclass espera string — serializa de volta se necessário.
    if isinstance(raw, (dict, list)):
        raw_str = json.dumps(raw, ensure_ascii=False, default=str)
    elif raw is None:
        raw_str = ""
    else:
        raw_str = str(raw)

    return TranscriptRecord(
        transaction_id=row["transaction_id"],
        verint_nr_contrato=row["verint_nr_contrato"] or "",
        transcription_text=row["transcription_text"] or "",
        started_at=row["started_at"] if isinstance(row["started_at"], datetime) else None,
        duration_s=float(row["duration_s"] or 0),
        segment=row["segment"] or "",
        msisdn=row["msisdn"] or "",
        ani=row["ani"] or "",
        cpf=row["cpf"] or "",
        employee=row["employee"] or "",
        raw_json=raw_str,
    )


_CASE_SELECT = (
    "SELECT case_number, created_by, owner, phone, opened_at, contract_msisdn "
    "FROM bko_cases"
)
_TRANS_SELECT = (
    "SELECT transaction_id, verint_nr_contrato, transcription_text, started_at, "
    "duration_s, segment, msisdn, ani, cpf, employee, raw_json FROM transcripts"
)


def _raw_to_jsonb(raw: str) -> dict | list | None:
    """Converte string JSON (formato herdado) em dict/list para JSONB.
    Tolera vazio e payloads não-JSON (raros — guardamos como string num campo
    artificial)."""
    if not raw:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"_raw_text": str(raw)[:50000]}


class PgBkoRepository:

    # ---------- cases ----------

    @staticmethod
    def _case_fingerprint(c: BkoCase) -> str:
        """Hash determinístico dos campos materiais — para detectar duplicata exata."""
        import hashlib
        payload = "|".join([
            (c.created_by or "").strip(),
            (c.owner or "").strip(),
            (c.phone or "").strip(),
            c.opened_at.isoformat() if c.opened_at else "",
            (c.contract_msisdn or "").strip(),
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def upsert_cases(self, cases: list[BkoCase]) -> dict:
        """Insere/atualiza casos detectando duplicatas idênticas.

        Devolve {imported, updated, skipped_duplicates}.
        """
        imported = 0
        updated = 0
        skipped = 0
        async with connect() as db:
            async with db.transaction():
                for c in cases:
                    existing_row = await db.fetchrow(
                        f"{_CASE_SELECT} WHERE case_number = $1", c.case_number
                    )
                    new_fp = self._case_fingerprint(c)
                    if existing_row:
                        existing_case = _row_to_case(existing_row)
                        if self._case_fingerprint(existing_case) == new_fp:
                            skipped += 1
                            continue
                        updated += 1
                    else:
                        imported += 1
                    await db.execute(
                        """
                        INSERT INTO bko_cases (case_number, created_by, owner,
                                               phone, opened_at, contract_msisdn)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (case_number) DO UPDATE SET
                            created_by      = EXCLUDED.created_by,
                            owner           = EXCLUDED.owner,
                            phone           = EXCLUDED.phone,
                            opened_at       = EXCLUDED.opened_at,
                            contract_msisdn = EXCLUDED.contract_msisdn
                        """,
                        c.case_number, c.created_by, c.owner, c.phone,
                        c.opened_at, c.contract_msisdn,
                    )
        return {"imported": imported, "updated": updated, "skipped_duplicates": skipped}

    async def list_cases(self, limit: int = 500) -> list[BkoCase]:
        async with connect() as db:
            rows = await db.fetch(
                f"{_CASE_SELECT} ORDER BY opened_at DESC NULLS LAST, "
                "case_number DESC LIMIT $1",
                limit,
            )
            return [_row_to_case(r) for r in rows]

    async def search_cases(self, q: str = "", limit: int = 100) -> list[BkoCase]:
        """Busca por case_number, contract_msisdn ou owner. Sem q, devolve os
        mais recentes."""
        q = (q or "").strip()
        async with connect() as db:
            if not q:
                rows = await db.fetch(
                    f"{_CASE_SELECT} ORDER BY opened_at DESC NULLS LAST, "
                    "case_number DESC LIMIT $1",
                    limit,
                )
            else:
                pattern = f"%{q}%"
                # ILIKE é case-insensitive em PG (SQLite usava LIKE
                # case-insensitive por default + LOWER() para owner).
                rows = await db.fetch(
                    f"{_CASE_SELECT} WHERE "
                    "    case_number ILIKE $1 "
                    " OR contract_msisdn ILIKE $1 "
                    " OR owner ILIKE $1 "
                    "ORDER BY "
                    "  CASE WHEN case_number = $2 THEN 0 "
                    "       WHEN case_number ILIKE $3 THEN 1 "
                    "       ELSE 2 END, "
                    "  opened_at DESC NULLS LAST LIMIT $4",
                    pattern, q, f"{q}%", limit,
                )
            return [_row_to_case(r) for r in rows]

    async def get_case(self, case_number: str) -> BkoCase | None:
        async with connect() as db:
            row = await db.fetchrow(
                f"{_CASE_SELECT} WHERE case_number = $1", case_number
            )
            return _row_to_case(row) if row else None

    async def count_cases(self) -> int:
        async with connect() as db:
            n = await db.fetchval("SELECT COUNT(*) FROM bko_cases")
            return int(n or 0)

    # ---------- transcripts ----------

    @staticmethod
    def _transcript_fingerprint(t: TranscriptRecord) -> str:
        """Hash do conteúdo material da transcrição (ignora raw_json)."""
        import hashlib
        payload = "|".join([
            (t.verint_nr_contrato or "").strip(),
            (t.transcription_text or "").strip(),
            t.started_at.isoformat() if t.started_at else "",
            f"{t.duration_s:.3f}",
            (t.segment or "").strip(),
            (t.msisdn or "").strip(),
            (t.ani or "").strip(),
            (t.cpf or "").strip(),
            (t.employee or "").strip(),
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def upsert_transcript(self, t: TranscriptRecord) -> str:
        """Upsert com detecção de duplicata."""
        async with connect() as db:
            existing_row = await db.fetchrow(
                f"{_TRANS_SELECT} WHERE transaction_id = $1", t.transaction_id
            )
            new_fp = self._transcript_fingerprint(t)
            outcome = "imported"
            if existing_row:
                existing = _row_to_transcript(existing_row)
                if self._transcript_fingerprint(existing) == new_fp:
                    return "skipped"
                outcome = "updated"
            await db.execute(
                """
                INSERT INTO transcripts (transaction_id, verint_nr_contrato,
                                         transcription_text, started_at,
                                         duration_s, segment, msisdn, ani, cpf,
                                         employee, raw_json)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                ON CONFLICT (transaction_id) DO UPDATE SET
                    verint_nr_contrato = EXCLUDED.verint_nr_contrato,
                    transcription_text = EXCLUDED.transcription_text,
                    started_at         = EXCLUDED.started_at,
                    duration_s         = EXCLUDED.duration_s,
                    segment            = EXCLUDED.segment,
                    msisdn             = EXCLUDED.msisdn,
                    ani                = EXCLUDED.ani,
                    cpf                = EXCLUDED.cpf,
                    employee           = EXCLUDED.employee,
                    raw_json           = EXCLUDED.raw_json
                """,
                t.transaction_id, t.verint_nr_contrato, t.transcription_text,
                t.started_at, t.duration_s, t.segment, t.msisdn, t.ani,
                t.cpf, t.employee, _raw_to_jsonb(t.raw_json),
            )
            return outcome

    async def get_transcript(self, transaction_id: str) -> TranscriptRecord | None:
        async with connect() as db:
            row = await db.fetchrow(
                f"{_TRANS_SELECT} WHERE transaction_id = $1", transaction_id
            )
            return _row_to_transcript(row) if row else None

    async def find_transcripts_by_contract(self, verint_nr_contrato: str) -> list[TranscriptRecord]:
        async with connect() as db:
            rows = await db.fetch(
                f"{_TRANS_SELECT} WHERE verint_nr_contrato = $1 "
                "ORDER BY started_at DESC NULLS LAST",
                str(verint_nr_contrato),
            )
            return [_row_to_transcript(r) for r in rows]

    async def count_transcripts(self) -> int:
        async with connect() as db:
            n = await db.fetchval("SELECT COUNT(*) FROM transcripts")
            return int(n or 0)

    async def contracts_with_transcript(self) -> set[str]:
        """Set de verint_nr_contrato com pelo menos uma transcrição."""
        async with connect() as db:
            rows = await db.fetch(
                "SELECT DISTINCT verint_nr_contrato FROM transcripts "
                "WHERE verint_nr_contrato IS NOT NULL AND verint_nr_contrato != ''"
            )
            return {r["verint_nr_contrato"] for r in rows if r["verint_nr_contrato"]}
