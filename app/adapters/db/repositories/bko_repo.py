"""Repositório SQLite para casos do BKO e transcrições."""

from __future__ import annotations

import json
from datetime import datetime

from app.adapters.db.sqlite import connect
from app.core.domain.entities import BkoCase, TranscriptRecord


def _parse_dt(v) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).split(".")[0].strip()  # remove fração de segundo
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _row_to_case(row) -> BkoCase:
    return BkoCase(
        case_number=str(row[0]),
        created_by=row[1] or "",
        owner=row[2] or "",
        phone=row[3] or "",
        opened_at=_parse_dt(row[4]),
        contract_msisdn=row[5] or "",
    )


def _row_to_transcript(row) -> TranscriptRecord:
    return TranscriptRecord(
        transaction_id=row[0],
        verint_nr_contrato=row[1] or "",
        transcription_text=row[2] or "",
        started_at=_parse_dt(row[3]),
        duration_s=float(row[4] or 0),
        segment=row[5] or "",
        msisdn=row[6] or "",
        ani=row[7] or "",
        cpf=row[8] or "",
        employee=row[9] or "",
        raw_json=row[10] or "",
    )


_CASE_SELECT = "SELECT case_number, created_by, owner, phone, opened_at, contract_msisdn FROM bko_cases"
_TRANS_SELECT = (
    "SELECT transaction_id, verint_nr_contrato, transcription_text, started_at, "
    "duration_s, segment, msisdn, ani, cpf, employee, raw_json FROM transcripts"
)


class SqliteBkoRepository:

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

        Devolve {imported, updated, skipped_duplicates}:
        - imported: linhas novas no banco
        - updated: existiam mas mudaram (pelo menos um campo material diferente)
        - skipped_duplicates: idênticas → não tocadas
        """
        imported = 0
        updated = 0
        skipped = 0
        async with connect() as db:
            for c in cases:
                # busca registro atual para comparar fingerprint
                cur = await db.execute(
                    f"{_CASE_SELECT} WHERE case_number = ?", (c.case_number,)
                )
                existing_row = await cur.fetchone()
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
                    "INSERT INTO bko_cases (case_number, created_by, owner, phone, opened_at, contract_msisdn) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(case_number) DO UPDATE SET "
                    "  created_by = excluded.created_by, owner = excluded.owner, "
                    "  phone = excluded.phone, opened_at = excluded.opened_at, "
                    "  contract_msisdn = excluded.contract_msisdn",
                    (
                        c.case_number, c.created_by, c.owner, c.phone,
                        c.opened_at.isoformat() if c.opened_at else None,
                        c.contract_msisdn,
                    ),
                )
            await db.commit()
        return {"imported": imported, "updated": updated, "skipped_duplicates": skipped}

    async def list_cases(self, limit: int = 500) -> list[BkoCase]:
        async with connect() as db:
            cur = await db.execute(
                f"{_CASE_SELECT} ORDER BY opened_at DESC, case_number DESC LIMIT ?",
                (limit,),
            )
            return [_row_to_case(r) for r in await cur.fetchall()]

    async def search_cases(self, q: str = "", limit: int = 100) -> list[BkoCase]:
        """Busca por case_number, contract_msisdn ou owner. Sem q, devolve os mais recentes."""
        q = (q or "").strip()
        async with connect() as db:
            if not q:
                cur = await db.execute(
                    f"{_CASE_SELECT} ORDER BY opened_at DESC, case_number DESC LIMIT ?",
                    (limit,),
                )
            else:
                pattern = f"%{q}%"
                cur = await db.execute(
                    f"{_CASE_SELECT} WHERE "
                    "  case_number LIKE ? OR contract_msisdn LIKE ? OR LOWER(owner) LIKE LOWER(?) "
                    "ORDER BY "
                    "  CASE WHEN case_number = ? THEN 0 "  # exact match primeiro
                    "       WHEN case_number LIKE ? THEN 1 "
                    "       ELSE 2 END, "
                    "  opened_at DESC LIMIT ?",
                    (pattern, pattern, pattern, q, f"{q}%", limit),
                )
            return [_row_to_case(r) for r in await cur.fetchall()]

    async def get_case(self, case_number: str) -> BkoCase | None:
        async with connect() as db:
            cur = await db.execute(f"{_CASE_SELECT} WHERE case_number = ?", (case_number,))
            row = await cur.fetchone()
            return _row_to_case(row) if row else None

    async def count_cases(self) -> int:
        async with connect() as db:
            cur = await db.execute("SELECT COUNT(*) FROM bko_cases")
            return int((await cur.fetchone())[0])

    # ---------- transcripts ----------

    @staticmethod
    def _transcript_fingerprint(t: TranscriptRecord) -> str:
        """Hash do conteúdo material da transcrição (ignora raw_json para tolerar reformatações)."""
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
        """Upsert com detecção de duplicata. Retorna 'imported', 'updated' ou 'skipped'."""
        async with connect() as db:
            cur = await db.execute(f"{_TRANS_SELECT} WHERE transaction_id = ?", (t.transaction_id,))
            existing_row = await cur.fetchone()
            new_fp = self._transcript_fingerprint(t)
            outcome = "imported"
            if existing_row:
                existing = _row_to_transcript(existing_row)
                if self._transcript_fingerprint(existing) == new_fp:
                    return "skipped"
                outcome = "updated"
            await db.execute(
                "INSERT INTO transcripts (transaction_id, verint_nr_contrato, transcription_text, "
                "started_at, duration_s, segment, msisdn, ani, cpf, employee, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(transaction_id) DO UPDATE SET "
                "  verint_nr_contrato = excluded.verint_nr_contrato, "
                "  transcription_text = excluded.transcription_text, "
                "  started_at = excluded.started_at, duration_s = excluded.duration_s, "
                "  segment = excluded.segment, msisdn = excluded.msisdn, "
                "  ani = excluded.ani, cpf = excluded.cpf, employee = excluded.employee, "
                "  raw_json = excluded.raw_json",
                (
                    t.transaction_id, t.verint_nr_contrato, t.transcription_text,
                    t.started_at.isoformat() if t.started_at else None,
                    t.duration_s, t.segment, t.msisdn, t.ani, t.cpf, t.employee, t.raw_json,
                ),
            )
            await db.commit()
            return outcome

    async def get_transcript(self, transaction_id: str) -> TranscriptRecord | None:
        async with connect() as db:
            cur = await db.execute(f"{_TRANS_SELECT} WHERE transaction_id = ?", (transaction_id,))
            row = await cur.fetchone()
            return _row_to_transcript(row) if row else None

    async def find_transcripts_by_contract(self, verint_nr_contrato: str) -> list[TranscriptRecord]:
        async with connect() as db:
            cur = await db.execute(
                f"{_TRANS_SELECT} WHERE verint_nr_contrato = ? ORDER BY started_at DESC",
                (str(verint_nr_contrato),),
            )
            return [_row_to_transcript(r) for r in await cur.fetchall()]

    async def count_transcripts(self) -> int:
        async with connect() as db:
            cur = await db.execute("SELECT COUNT(*) FROM transcripts")
            return int((await cur.fetchone())[0])

    async def contracts_with_transcript(self) -> set[str]:
        """Set de verint_nr_contrato que têm pelo menos uma transcrição — para flag has_transcript."""
        async with connect() as db:
            cur = await db.execute(
                "SELECT DISTINCT verint_nr_contrato FROM transcripts WHERE verint_nr_contrato != ''"
            )
            return {r[0] for r in await cur.fetchall() if r[0]}
