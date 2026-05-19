"""Use case: Knowledge Base.

Orquestra o ciclo de vida de uma KB e seus documentos:

  1. **Cadastro de KB**: cria registro em `knowledge_bases` com parâmetros
     do chunker e do modelo de embeddings.
  2. **Upload de documento**: persiste raw bytes em `knowledge_documents`
     com status='pending'.
  3. **Pipeline de processamento (async)**: Docling → chunker → embeddings
     → bulk insert em `knowledge_chunks`. Atualiza status final (ready/failed).
  4. **Retrieval**: dado um texto de query, embed → cosine search → top-K
     chunks com score.

O processamento roda em FastAPI BackgroundTasks no router (não bloqueia o
HTTP 201 do upload). Falhas no pipeline são persistidas em `error` do
documento — o usuário vê na UI e pode reprocessar.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from app.adapters.db.repositories.knowledge_repo import (
    PgKnowledgeBaseRepository,
    PgKnowledgeChunkRepository,
    PgKnowledgeDocumentRepository,
)
from app.adapters.llm.factory import build_embedding_client
from app.core.domain.entities import (
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentStatus,
    new_uuid,
)
from app.core.ports.embeddings import EmbeddingClient
from app.core.services.chunker import chunk_markdown
from app.core.services.docling_extractor import extract

logger = logging.getLogger("vertice.knowledge")


# Cap de payload aceito no upload (25 MB). Aplicado no router antes de
# entregar bytes pra cá, mas duplicado aqui como defesa em profundidade.
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024


class KnowledgeService:
    def __init__(
        self,
        kb_repo: PgKnowledgeBaseRepository | None = None,
        doc_repo: PgKnowledgeDocumentRepository | None = None,
        chunk_repo: PgKnowledgeChunkRepository | None = None,
        embedder: EmbeddingClient | None = None,
    ):
        self.kb_repo = kb_repo or PgKnowledgeBaseRepository()
        self.doc_repo = doc_repo or PgKnowledgeDocumentRepository()
        self.chunk_repo = chunk_repo or PgKnowledgeChunkRepository()
        self.embedder = embedder or build_embedding_client()

    # ===== Knowledge Bases =====

    async def list_bases(self) -> list[dict]:
        bases = await self.kb_repo.list_all()
        out = []
        for b in bases:
            stats = await self.kb_repo.stats(b.id)
            out.append({**self._kb_to_dict(b), "stats": stats})
        return out

    async def get_base(self, kb_id: UUID) -> KnowledgeBase | None:
        return await self.kb_repo.get(kb_id)

    async def create_base(
        self,
        *,
        name: str,
        description: str,
        created_by_id: str | None,
        created_by_username: str | None,
    ) -> KnowledgeBase:
        if not name or not name.strip():
            raise ValueError("nome da base de conhecimento é obrigatório")
        name = name.strip()
        existing = await self.kb_repo.get_by_name(name)
        if existing:
            raise ValueError(f"já existe uma base de conhecimento '{name}'")
        kb = KnowledgeBase(
            id=new_uuid(),
            name=name,
            description=(description or "").strip(),
            embedding_model=self.embedder.model_name,
            embedding_dims=self.embedder.dimensions,
            created_by_id=created_by_id,
            created_by_username=created_by_username,
        )
        return await self.kb_repo.create(kb)

    async def update_base(
        self,
        kb_id: UUID,
        *,
        description: str | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> KnowledgeBase | None:
        return await self.kb_repo.update(
            kb_id,
            description=description,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    async def delete_base(self, kb_id: UUID) -> None:
        await self.kb_repo.delete(kb_id)

    # ===== Documents =====

    async def list_documents(self, kb_id: UUID) -> list[dict]:
        docs = await self.doc_repo.list_for_kb(kb_id)
        return [self._doc_to_dict(d) for d in docs]

    async def upload_document(
        self,
        *,
        kb_id: UUID,
        filename: str,
        mime_type: str,
        content: bytes,
        uploaded_by_id: str | None,
        uploaded_by_username: str | None,
    ) -> KnowledgeDocument:
        if len(content) > MAX_DOCUMENT_BYTES:
            raise ValueError(
                f"arquivo excede o limite de {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB"
            )
        kb = await self.kb_repo.get(kb_id)
        if not kb:
            raise ValueError("base de conhecimento não encontrada")

        doc = KnowledgeDocument(
            id=new_uuid(),
            knowledge_base_id=kb_id,
            filename=filename,
            mime_type=mime_type or "application/octet-stream",
            size_bytes=len(content),
            raw_content=content,
            status=KnowledgeDocumentStatus.pending,
            uploaded_by_id=uploaded_by_id,
            uploaded_by_username=uploaded_by_username,
        )
        return await self.doc_repo.create(doc)

    async def delete_document(self, doc_id: UUID) -> None:
        await self.doc_repo.delete(doc_id)

    # ===== Pipeline (background) =====

    async def process_document(self, doc_id: UUID) -> None:
        """Pipeline async: extrai (Docling) → chunca → embed → persiste.

        Idempotente o suficiente: se chamada duas vezes, a segunda apenas
        re-processa (chunks antigos são substituídos via DELETE prévio).
        """
        doc = await self.doc_repo.get(doc_id, include_raw=True)
        if not doc:
            logger.warning("process_document: doc %s não existe", doc_id)
            return
        if not doc.raw_content:
            await self.doc_repo.mark_failed(doc_id, "raw_content vazio")
            return

        kb = await self.kb_repo.get(doc.knowledge_base_id)
        if not kb:
            await self.doc_repo.mark_failed(doc_id, "KB referenciada não existe")
            return

        await self.doc_repo.mark_processing(doc_id)

        # Idempotência: reprocessar um doc não pode acumular chunks duplicados.
        # Apaga chunks anteriores ANTES de extrair (se a extração falhar, o
        # doc fica sem chunks, alinhado com status=failed).
        await self.chunk_repo.delete_for_document(doc_id)

        # Docling roda em thread separada — é CPU-bound e bloqueia o loop.
        await self.doc_repo.update_progress(
            doc_id, f"extraindo markdown com Docling ({doc.size_bytes} bytes)"
        )
        try:
            result = await asyncio.to_thread(extract, doc.raw_content, doc.filename)
        except Exception as e:  # noqa: BLE001
            logger.exception("extract crashed for doc %s", doc_id)
            await self.doc_repo.mark_failed(doc_id, f"crash na extração: {e}")
            return

        if result.error:
            await self.doc_repo.mark_failed(doc_id, result.error)
            return

        if not result.markdown.strip():
            await self.doc_repo.mark_failed(
                doc_id, "Docling não extraiu conteúdo do arquivo (markdown vazio)"
            )
            return

        # Chunking — markdown-aware com params da KB.
        await self.doc_repo.update_progress(
            doc_id,
            f"chunking (markdown={len(result.markdown)} chars · "
            f"size={kb.chunk_size}/{kb.chunk_overlap})",
        )
        chunks_data = chunk_markdown(
            result.markdown,
            chunk_size=kb.chunk_size,
            chunk_overlap=kb.chunk_overlap,
        )
        if not chunks_data:
            await self.doc_repo.mark_failed(
                doc_id, "chunker produziu 0 chunks"
            )
            return

        # Embeddings em batch — falha aqui não é fatal pra extração, mas
        # marca o doc como failed (sem embeddings, retrieval não funciona).
        await self.doc_repo.update_progress(
            doc_id,
            f"gerando embeddings de {len(chunks_data)} chunks "
            f"({'mock' if getattr(self.embedder, 'is_mock', False) else self.embedder.model_name})",
        )
        try:
            texts = [c.content for c in chunks_data]
            vectors = await self.embedder.embed(texts)
        except Exception as e:  # noqa: BLE001
            logger.exception("embeddings falharam para doc %s", doc_id)
            await self.doc_repo.mark_failed(doc_id, f"embeddings falharam: {e}")
            return

        if len(vectors) != len(chunks_data):
            await self.doc_repo.mark_failed(
                doc_id,
                f"embedder devolveu {len(vectors)} vetores para "
                f"{len(chunks_data)} chunks",
            )
            return

        await self.doc_repo.update_progress(
            doc_id, f"salvando {len(chunks_data)} chunks vetorizados no Postgres"
        )
        chunks_to_save = [
            KnowledgeChunk(
                id=new_uuid(),
                knowledge_base_id=kb.id,
                document_id=doc.id,
                chunk_index=i,
                content=c.content,
                metadata=c.metadata,
                embedding=v,
                tokens_estimated=c.tokens_estimated,
            )
            for i, (c, v) in enumerate(zip(chunks_data, vectors))
        ]
        await self.chunk_repo.bulk_insert(chunks_to_save)

        await self.doc_repo.mark_ready(
            doc_id,
            markdown=result.markdown,
            structure=result.structure,
            chunks_count=len(chunks_to_save),
        )

    # ===== Retrieval =====

    async def search(
        self,
        kb_id: UUID,
        query: str,
        *,
        top_k: int = 5,
        doc_ids: list[UUID] | None = None,
    ) -> list[dict]:
        """Retorna os top-K chunks mais relevantes para a query.

        Se `doc_ids` for passado, restringe a busca a esses documentos —
        permite que o consumidor (ex.: módulo Radar 'Base de Conhecimento')
        filtre por seleção do usuário.
        """
        if not query.strip():
            return []
        vec = await self.embedder.embed_one(query)
        if not vec:
            return []
        return await self.chunk_repo.search(
            kb_id, vec, top_k=top_k, doc_ids=doc_ids
        )

    async def build_context(
        self,
        kb_id: UUID,
        query: str,
        *,
        top_k: int = 5,
        max_chars: int = 8000,
        doc_ids: list[UUID] | None = None,
    ) -> str:
        """Constrói um bloco de contexto formatado para injeção em system prompt.

        Cada chunk vem precedido por seu `section_path` quando disponível.
        Trunca o agregado em `max_chars` para não estourar context window.
        """
        hits = await self.search(kb_id, query, top_k=top_k, doc_ids=doc_ids)
        if not hits:
            return ""
        parts: list[str] = []
        total = 0
        for h in hits:
            header = ""
            meta = h.get("metadata") or {}
            section = meta.get("section_path")
            if section:
                header = f"[Seção: {section}]\n"
            piece = f"{header}{h['content']}".strip()
            if total + len(piece) > max_chars:
                # Adiciona o que couber e para.
                remaining = max_chars - total
                if remaining > 200:
                    parts.append(piece[:remaining])
                break
            parts.append(piece)
            total += len(piece)
        return "\n\n---\n\n".join(parts)

    # ===== Serializers (DTO) =====

    @staticmethod
    def _kb_to_dict(kb: KnowledgeBase) -> dict:
        return {
            "id": str(kb.id),
            "name": kb.name,
            "description": kb.description,
            "embedding_model": kb.embedding_model,
            "embedding_dims": kb.embedding_dims,
            "chunk_size": kb.chunk_size,
            "chunk_overlap": kb.chunk_overlap,
            "created_by_username": kb.created_by_username,
            "created_at": kb.created_at.isoformat() if kb.created_at else None,
            "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
        }

    @staticmethod
    def _doc_to_dict(d: KnowledgeDocument) -> dict:
        return {
            "id": str(d.id),
            "knowledge_base_id": str(d.knowledge_base_id),
            "filename": d.filename,
            "mime_type": d.mime_type,
            "size_bytes": d.size_bytes,
            "status": d.status.value,
            "error": d.error,
            "chunks_count": d.chunks_count,
            "progress_message": d.progress_message,
            "processing_started_at": (
                d.processing_started_at.isoformat() if d.processing_started_at else None
            ),
            "uploaded_by_username": d.uploaded_by_username,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "processed_at": d.processed_at.isoformat() if d.processed_at else None,
            "structure": d.structure_json or {},
            "markdown_preview": (d.markdown_extracted or "")[:500],
        }
