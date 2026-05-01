import json

import pytest

from app.adapters.db.sqlite import init_db
from app.core.services.bko_service import BkoService


@pytest.mark.asyncio
async def test_ingest_transcript_files_skips_duplicate_json_in_same_batch():
    await init_db()
    svc = BkoService()

    payload = {
        "transactionId": "tx-123",
        "verint_nrContrato": "ctr-1",
        "transcription_text": "cliente solicitou segunda via",
    }
    content = json.dumps(payload).encode("utf-8")

    result = await svc.ingest_transcript_files([
        ("transcript-a.json", content),
        ("transcript-a-copia.json", content),
    ])

    assert result["imported"] == 1
    assert result["updated"] == 0
    assert result["skipped_duplicates"] == 1
    assert result["failed"] == []
