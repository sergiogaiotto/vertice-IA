"""Use case: BKO Inteligente — ingestão de casos (XLSX) e transcrições (JSON)."""

from __future__ import annotations

import io
import json
import hashlib
from datetime import datetime
from typing import Iterable

from app.adapters.db.repositories.bko_repo import PgBkoRepository
from app.core.domain.entities import BkoCase, TranscriptRecord


_CASE_HEADER_MAP = {
    # nome no xlsx (lowercase) -> attr no BkoCase
    "número do caso": "case_number",
    "numero do caso": "case_number",
    "criado por": "created_by",
    "proprietário do caso": "owner",
    "proprietario do caso": "owner",
    "telefone/contrato": "phone",
    "data de abertura": "opened_at",
    "contrato/msisdn": "contract_msisdn",
}


class BkoService:
    def __init__(self, repo: PgBkoRepository | None = None):
        self.repo = repo or PgBkoRepository()

    # ---------- ingest XLSX (casos) ----------

    async def ingest_cases_xlsx(self, file_bytes: bytes) -> dict:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return {"imported": 0, "updated": 0, "skipped_duplicates": 0}
        header = [str(h).strip().lower() if h else "" for h in rows[0]]
        idx_map: dict[str, int] = {}
        for i, h in enumerate(header):
            attr = _CASE_HEADER_MAP.get(h)
            if attr:
                idx_map[attr] = i

        if "case_number" not in idx_map:
            raise ValueError(
                "planilha não contém coluna 'Número do caso' — verifique o cabeçalho"
            )

        cases: list[BkoCase] = []
        for row in rows[1:]:
            try:
                case_number_raw = row[idx_map["case_number"]]
                if case_number_raw is None or case_number_raw == "":
                    continue
                case = BkoCase(case_number=str(case_number_raw))
                for attr, i in idx_map.items():
                    if attr == "case_number":
                        continue
                    val = row[i] if i < len(row) else None
                    if val is None or val == "":
                        continue
                    if attr == "opened_at":
                        case.opened_at = val if isinstance(val, datetime) else None
                    else:
                        setattr(case, attr, str(val).strip())
                cases.append(case)
            except Exception:
                continue
        return await self.repo.upsert_cases(cases)

    # ---------- ingest JSON (transcrição única ou múltiplas) ----------

    async def ingest_transcript_json(self, file_bytes: bytes) -> dict:
        """Ingere um arquivo JSON único. Retorna {transaction_id, outcome}."""
        try:
            data = json.loads(file_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError(f"JSON inválido: {e}")
        if not isinstance(data, dict):
            raise ValueError("JSON precisa ser um objeto (dict)")

        tx_id = data.get("transactionId") or data.get("transaction_id")
        if not tx_id:
            raise ValueError("JSON sem 'transactionId'")

        started = data.get("verint_dataHoraInicio") or ""
        started_dt = None
        if started:
            try:
                base = str(started).split(".")[0].strip()
                started_dt = datetime.strptime(base, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                started_dt = None

        duration = 0.0
        try:
            duration = float(data.get("verint_duracao") or 0)
        except (TypeError, ValueError):
            duration = 0.0

        record = TranscriptRecord(
            transaction_id=str(tx_id),
            verint_nr_contrato=str(data.get("verint_nrContrato") or ""),
            transcription_text=str(data.get("transcription_text") or ""),
            started_at=started_dt,
            duration_s=duration,
            segment=str(data.get("verint_segmento") or ""),
            msisdn=str(data.get("verint_msisdn") or ""),
            ani=str(data.get("verint_ani") or ""),
            cpf=str(data.get("verint_cpf") or ""),
            employee=str(data.get("verint_funcionario") or ""),
            raw_json=json.dumps(data, ensure_ascii=False),
        )
        outcome = await self.repo.upsert_transcript(record)
        return {"transaction_id": record.transaction_id, "outcome": outcome}

    async def ingest_transcript_files(self, files: Iterable[tuple[str, bytes]]) -> dict:
        """Ingere múltiplos arquivos JSON.

        Retorna {imported, updated, skipped_duplicates, failed: [{filename, error}]}.
        """
        imported = 0
        updated = 0
        skipped = 0
        failed: list[dict] = []
        seen_content_hashes: set[str] = set()
        for filename, content in files:
            try:
                content_hash = hashlib.sha256(content).hexdigest()
                if content_hash in seen_content_hashes:
                    skipped += 1
                    continue
                seen_content_hashes.add(content_hash)
                r = await self.ingest_transcript_json(content)
                if r["outcome"] == "imported":
                    imported += 1
                elif r["outcome"] == "updated":
                    updated += 1
                elif r["outcome"] == "skipped":
                    skipped += 1
            except Exception as e:  # noqa: BLE001
                failed.append({"filename": filename, "error": str(e)[:200]})
        return {"imported": imported, "updated": updated, "skipped_duplicates": skipped, "failed": failed}

    # ---------- listagens / lookups ----------

    async def list_cases_with_status(self, limit: int = 500) -> list[dict]:
        cases = await self.repo.list_cases(limit)
        with_transcript = await self.repo.contracts_with_transcript()
        out: list[dict] = []
        for c in cases:
            out.append({
                "case_number": c.case_number,
                "created_by": c.created_by,
                "owner": c.owner,
                "phone": c.phone,
                "opened_at": c.opened_at,
                "contract_msisdn": c.contract_msisdn,
                "has_transcript": c.contract_msisdn in with_transcript,
            })
        return out

    async def search_cases(self, q: str = "", limit: int = 100) -> list[dict]:
        """Busca casos por case_number, contract_msisdn ou owner. Sem `q`, devolve os mais recentes."""
        cases = await self.repo.search_cases(q, limit)
        with_transcript = await self.repo.contracts_with_transcript()
        return [
            {
                "case_number": c.case_number,
                "owner": c.owner,
                "contract_msisdn": c.contract_msisdn,
                "has_transcript": c.contract_msisdn in with_transcript,
            }
            for c in cases
        ]

    async def get_case_with_transcript(self, case_number: str) -> dict | None:
        case = await self.repo.get_case(case_number)
        if not case:
            return None
        transcripts = await self.repo.find_transcripts_by_contract(case.contract_msisdn) if case.contract_msisdn else []
        primary = transcripts[0] if transcripts else None
        return {
            "case": case,
            "transcript": primary,
            "all_transcripts_for_contract": transcripts,
        }

    async def get_transcript(self, transaction_id: str) -> TranscriptRecord | None:
        return await self.repo.get_transcript(transaction_id)

    async def stats(self) -> dict:
        return {
            "cases": await self.repo.count_cases(),
            "transcripts": await self.repo.count_transcripts(),
        }
