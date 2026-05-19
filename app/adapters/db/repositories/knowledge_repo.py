"""Repositório PostgreSQL da Knowledge Base.

Três entidades, três queries:
  * `knowledge_bases`  — CRUD direto
  * `knowledge_documents` — CRUD + mudanças de status do pipeline async
  * `knowledge_chunks` — bulk insert + busca vetorial cosine via pgvector

A busca semântica usa o operador `<=>` (cosine distance) do pgvector,
indexado por `idx_knowledge_chunks_embedding` (ivfflat). Para KBs pequenas
(<1k chunks) o planner pode escolher seq scan — é aceitável e não exige
ajuste do `lists`.

Codec do tipo `vector` é registrado em `_init_connection` de postgres.py
(módulo `pgvector.asyncpg.register_vector`). Listas Python `list[float]`
vão e voltam diretamente — não precisamos serializar manualmente.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.adapters.db.postgres import connect
from app.core.domain.entities import (
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentStatus,
)

_KB_COLS = (
    "id::text AS id, name, description, embedding_model, embedding_dims, "
    "chunk_size, chunk_overlap, created_by_id, created_by_username, "
    "created_at, updated_at"
)
_DOC_COLS = (
    "id::text AS id, knowledge_base_id::text AS knowledge_base_id, filename, "
    "mime_type, size_bytes, markdown_extracted, structure_json, status, "
    "error, chunks_count, uploaded_by_id, uploaded_by_username, "
    "created_at, processed_at"
)
_DOC_COLS_WITH_RAW = _DOC_COLS + ", raw_content"


def _row_to_kb(row) -> KnowledgeBase:
    return KnowledgeBase(
        id=UUID(row["id"]),
        name=row["name"],
        description=row["description"] or "",
        embedding_model=row["embedding_model"],
        embedding_dims=row["embedding_dims"],
        chunk_size=row["chunk_size"],
        chunk_overlap=row["chunk_overlap"],
        created_by_id=row["created_by_id"],
        created_by_username=row["created_by_username"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_doc(row, include_raw: bool = False) -> KnowledgeDocument:
    return KnowledgeDocument(
        id=UUID(row["id"]),
        knowledge_base_id=UUID(row["knowledge_base_id"]),
        filename=row["filename"],
        mime_type=row["mime_type"] or "application/octet-stream",
        size_bytes=row["size_bytes"] or 0,
        raw_content=bytes(row["raw_content"]) if include_raw and row.get("raw_content") else None,
        markdown_extracted=row["markdown_extracted"] or "",
        structure_json=row["structure_json"] or {},
        status=KnowledgeDocumentStatus(row["status"]),
        error=row["error"],
        chunks_count=row["chunks_count"] or 0,
        uploaded_by_id=row["uploaded_by_id"],
        uploaded_by_username=row["uploaded_by_username"],
        created_at=row["created_at"],
        processed_at=row["processed_at"],
    )


class PgKnowledgeBaseRepository:
    """CRUD da entidade KnowledgeBase."""

    async def list_all(self) -> list[KnowledgeBase]:
        async with connect() as db:
            rows = await db.fetch(
                f"SELECT {_KB_COLS} FROM knowledge_bases ORDER BY name"
            )
            return [_row_to_kb(r) for r in rows]

    async def get(self, kb_id: UUID) -> KnowledgeBase | None:
        async with connect() as db:
            row = await db.fetchrow(
                f"SELECT {_KB_COLS} FROM knowledge_bases WHERE id = $1::uuid",
                str(kb_id),
            )
            return _row_to_kb(row) if row else None

    async def get_by_name(self, name: str) -> KnowledgeBase | None:
        async with connect() as db:
            row = await db.fetchrow(
                f"SELECT {_KB_COLS} FROM knowledge_bases WHERE name = $1", name
            )
            return _row_to_kb(row) if row else None

    async def create(self, kb: KnowledgeBase) -> KnowledgeBase:
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO knowledge_bases (
                    id, name, description, embedding_model, embedding_dims,
                    chunk_size, chunk_overlap, created_by_id, created_by_username
                ) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                str(kb.id), kb.name, kb.description, kb.embedding_model,
                kb.embedding_dims, kb.chunk_size, kb.chunk_overlap,
                kb.created_by_id, kb.created_by_username,
            )
            return kb

    async def update(
        self,
        kb_id: UUID,
        *,
        description: str | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> KnowledgeBase | None:
        # Patch parcial — só atualiza campos não-None. Modelo de embedding
        # NÃO é mutável (mudar invalidaria todos os chunks; o caminho
        # correto seria recriar a KB).
        sets = []
        args: list = []
        if description is not None:
            args.append(description)
            sets.append(f"description = ${len(args)}")
        if chunk_size is not None:
            args.append(chunk_size)
            sets.append(f"chunk_size = ${len(args)}")
        if chunk_overlap is not None:
            args.append(chunk_overlap)
            sets.append(f"chunk_overlap = ${len(args)}")
        if not sets:
            return await self.get(kb_id)
        sets.append("updated_at = NOW()")
        args.append(str(kb_id))
        sql = (
            f"UPDATE knowledge_bases SET {', '.join(sets)} "
            f"WHERE id = ${len(args)}::uuid"
        )
        async with connect() as db:
            await db.execute(sql, *args)
        return await self.get(kb_id)

    async def delete(self, kb_id: UUID) -> None:
        # CASCADE remove documents + chunks via FK ON DELETE CASCADE.
        async with connect() as db:
            await db.execute(
                "DELETE FROM knowledge_bases WHERE id = $1::uuid", str(kb_id)
            )

    async def stats(self, kb_id: UUID) -> dict:
        """Contagens agregadas para UI: total de docs por status + total de chunks."""
        async with connect() as db:
            doc_row = await db.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'pending')    AS pending,
                    COUNT(*) FILTER (WHERE status = 'processing') AS processing,
                    COUNT(*) FILTER (WHERE status = 'ready')      AS ready,
                    COUNT(*) FILTER (WHERE status = 'failed')     AS failed,
                    COUNT(*)                                       AS total,
                    COALESCE(SUM(size_bytes), 0)                   AS total_bytes,
                    COALESCE(SUM(chunks_count), 0)                 AS total_chunks
                FROM knowledge_documents WHERE knowledge_base_id = $1::uuid
                """,
                str(kb_id),
            )
            return {
                "pending": doc_row["pending"] or 0,
                "processing": doc_row["processing"] or 0,
                "ready": doc_row["ready"] or 0,
                "failed": doc_row["failed"] or 0,
                "total": doc_row["total"] or 0,
                "total_bytes": doc_row["total_bytes"] or 0,
                "total_chunks": doc_row["total_chunks"] or 0,
            }


class PgKnowledgeDocumentRepository:
    """CRUD de documentos + mudanças de status do pipeline."""

    async def list_for_kb(self, kb_id: UUID) -> list[KnowledgeDocument]:
        async with connect() as db:
            rows = await db.fetch(
                f"SELECT {_DOC_COLS} FROM knowledge_documents "
                "WHERE knowledge_base_id = $1::uuid ORDER BY created_at DESC",
                str(kb_id),
            )
            return [_row_to_doc(r) for r in rows]

    async def get(self, doc_id: UUID, *, include_raw: bool = False) -> KnowledgeDocument | None:
        cols = _DOC_COLS_WITH_RAW if include_raw else _DOC_COLS
        async with connect() as db:
            row = await db.fetchrow(
                f"SELECT {cols} FROM knowledge_documents WHERE id = $1::uuid",
                str(doc_id),
            )
            return _row_to_doc(row, include_raw=include_raw) if row else None

    async def create(self, doc: KnowledgeDocument) -> KnowledgeDocument:
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO knowledge_documents (
                    id, knowledge_base_id, filename, mime_type, size_bytes,
                    raw_content, status, uploaded_by_id, uploaded_by_username
                ) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9)
                """,
                str(doc.id), str(doc.knowledge_base_id), doc.filename,
                doc.mime_type, doc.size_bytes, doc.raw_content,
                doc.status.value, doc.uploaded_by_id, doc.uploaded_by_username,
            )
            return doc

    async def mark_processing(self, doc_id: UUID) -> None:
        async with connect() as db:
            await db.execute(
                "UPDATE knowledge_documents SET status = 'processing' "
                "WHERE id = $1::uuid",
                str(doc_id),
            )

    async def mark_ready(
        self,
        doc_id: UUID,
        *,
        markdown: str,
        structure: dict,
        chunks_count: int,
    ) -> None:
        async with connect() as db:
            await db.execute(
                """
                UPDATE knowledge_documents SET
                    status = 'ready',
                    markdown_extracted = $2,
                    structure_json = $3::jsonb,
                    chunks_count = $4,
                    processed_at = NOW(),
                    error = NULL
                WHERE id = $1::uuid
                """,
                str(doc_id), markdown, structure, chunks_count,
            )

    async def mark_failed(self, doc_id: UUID, error: str) -> None:
        async with connect() as db:
            await db.execute(
                """
                UPDATE knowledge_documents SET
                    status = 'failed', error = $2, processed_at = NOW()
                WHERE id = $1::uuid
                """,
                str(doc_id), error[:2000],
            )

    async def delete(self, doc_id: UUID) -> None:
        # CASCADE remove chunks do documento.
        async with connect() as db:
            await db.execute(
                "DELETE FROM knowledge_documents WHERE id = $1::uuid", str(doc_id)
            )


class PgKnowledgeChunkRepository:
    """Bulk insert + busca vetorial."""

    async def delete_for_document(self, doc_id: UUID) -> int:
        async with connect() as db:
            row = await db.fetchrow(
                "WITH d AS (DELETE FROM knowledge_chunks WHERE document_id = $1::uuid RETURNING 1) "
                "SELECT COUNT(*)::int AS n FROM d",
                str(doc_id),
            )
            return int(row["n"]) if row else 0

    async def bulk_insert(self, chunks: list[KnowledgeChunk]) -> int:
        if not chunks:
            return 0
        async with connect() as db:
            # `copy_records_to_table` é dramaticamente mais rápido que
            # `executemany` para arrays grandes (centenas de chunks por doc).
            # No entanto, copy_records_to_table não suporta `vector` type
            # diretamente — usamos executemany para ficar portável e seguro.
            async with db.transaction():
                await db.executemany(
                    """
                    INSERT INTO knowledge_chunks (
                        id, knowledge_base_id, document_id, chunk_index,
                        content, metadata, embedding, tokens_estimated
                    ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::jsonb,
                              $7, $8)
                    """,
                    [
                        (
                            str(c.id),
                            str(c.knowledge_base_id),
                            str(c.document_id),
                            c.chunk_index,
                            c.content,
                            c.metadata,
                            c.embedding,
                            c.tokens_estimated,
                        )
                        for c in chunks
                    ],
                )
        return len(chunks)

    async def search(
        self,
        kb_id: UUID,
        query_embedding: list[float],
        *,
        top_k: int = 5,
    ) -> list[dict]:
        """Top-K chunks mais próximos da query, ordenados por cosine distance.

        Retorna dicts (não entidades) porque a UI/contexto consome só
        `content`, `metadata` e `score` — o vetor completo seria desperdício
        de banda.

        `score` = 1 - distance (mais alto = mais similar; ∈ [0, 2]
        considerando cosine; tipicamente ∈ [0, 1]).
        """
        async with connect() as db:
            rows = await db.fetch(
                """
                SELECT
                    id::text AS id,
                    document_id::text AS document_id,
                    chunk_index,
                    content,
                    metadata,
                    1 - (embedding <=> $2) AS score
                FROM knowledge_chunks
                WHERE knowledge_base_id = $1::uuid
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> $2
                LIMIT $3
                """,
                str(kb_id), query_embedding, top_k,
            )
            return [
                {
                    "id": r["id"],
                    "document_id": r["document_id"],
                    "chunk_index": r["chunk_index"],
                    "content": r["content"],
                    "metadata": r["metadata"] or {},
                    "score": float(r["score"]),
                }
                for r in rows
            ]
