"""Router HTTP da Knowledge Base.

Endpoints:

  GET  /api/knowledge/bases                       — lista KBs + stats
  POST /api/knowledge/bases                       — cria KB
  GET  /api/knowledge/bases/{id}                  — detalhe + docs
  PATCH /api/knowledge/bases/{id}                 — edita descrição/chunker
  DELETE /api/knowledge/bases/{id}                — deleta KB (CASCADE)

  GET  /api/knowledge/bases/{id}/documents        — lista documentos
  POST /api/knowledge/bases/{id}/documents        — upload doc (multipart)
  DELETE /api/knowledge/documents/{doc_id}        — deleta doc
  POST /api/knowledge/documents/{doc_id}/reprocess — re-roda pipeline

  POST /api/knowledge/bases/{id}/search           — busca semântica (teste)

Política: root + admin podem tudo. Outros perfis são 403.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from pydantic import BaseModel, Field

from app.api.deps import get_knowledge_service, require_roles, require_user
from app.core.domain.entities import User
from app.core.services.knowledge_service import (
    MAX_DOCUMENT_BYTES,
    KnowledgeService,
)

router = APIRouter()


# ============================================================
# Lookup leve (qualquer user autenticado) — usado pelo combo
# de Módulos para escolher a KB associada
# ============================================================


@router.get("/options")
async def list_options(
    svc: KnowledgeService = Depends(get_knowledge_service),
    user: User = Depends(require_user),
):
    """Lista (id, name, description) das KBs cadastradas — usado no combobox
    do formulário de Módulos. Não exige role admin (mas requer auth)."""
    bases = await svc.kb_repo.list_all()
    return [
        {"id": str(b.id), "name": b.name, "description": b.description}
        for b in bases
    ]


# ============================================================
# Schemas
# ============================================================


class CreateKnowledgeBaseRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = ""


class UpdateKnowledgeBaseRequest(BaseModel):
    description: str | None = None
    chunk_size: int | None = Field(None, ge=200, le=4000)
    chunk_overlap: int | None = Field(None, ge=0, le=500)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    top_k: int = Field(5, ge=1, le=20)


# ============================================================
# KB CRUD
# ============================================================


@router.get("/bases")
async def list_bases(
    svc: KnowledgeService = Depends(get_knowledge_service),
    user: User = Depends(require_roles("root", "admin")),
):
    return await svc.list_bases()


@router.post("/bases", status_code=201)
async def create_base(
    body: CreateKnowledgeBaseRequest,
    svc: KnowledgeService = Depends(get_knowledge_service),
    user: User = Depends(require_roles("root", "admin")),
):
    try:
        kb = await svc.create_base(
            name=body.name,
            description=body.description,
            created_by_id=str(user.id),
            created_by_username=user.username,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return svc._kb_to_dict(kb)


@router.get("/bases/{kb_id}")
async def get_base(
    kb_id: UUID,
    svc: KnowledgeService = Depends(get_knowledge_service),
    user: User = Depends(require_roles("root", "admin")),
):
    kb = await svc.get_base(kb_id)
    if not kb:
        raise HTTPException(404, "base de conhecimento não encontrada")
    stats = await svc.kb_repo.stats(kb_id)
    docs = await svc.list_documents(kb_id)
    return {**svc._kb_to_dict(kb), "stats": stats, "documents": docs}


@router.patch("/bases/{kb_id}")
async def update_base(
    kb_id: UUID,
    body: UpdateKnowledgeBaseRequest,
    svc: KnowledgeService = Depends(get_knowledge_service),
    user: User = Depends(require_roles("root", "admin")),
):
    kb = await svc.update_base(
        kb_id,
        description=body.description,
        chunk_size=body.chunk_size,
        chunk_overlap=body.chunk_overlap,
    )
    if not kb:
        raise HTTPException(404, "base de conhecimento não encontrada")
    return svc._kb_to_dict(kb)


@router.delete("/bases/{kb_id}")
async def delete_base(
    kb_id: UUID,
    svc: KnowledgeService = Depends(get_knowledge_service),
    user: User = Depends(require_roles("root", "admin")),
):
    kb = await svc.get_base(kb_id)
    if not kb:
        raise HTTPException(404, "base de conhecimento não encontrada")
    await svc.delete_base(kb_id)
    return {"ok": True}


# ============================================================
# Documents
# ============================================================


@router.get("/bases/{kb_id}/documents")
async def list_documents(
    kb_id: UUID,
    svc: KnowledgeService = Depends(get_knowledge_service),
    user: User = Depends(require_roles("root", "admin")),
):
    kb = await svc.get_base(kb_id)
    if not kb:
        raise HTTPException(404, "base de conhecimento não encontrada")
    return await svc.list_documents(kb_id)


@router.post("/bases/{kb_id}/documents", status_code=201)
async def upload_document(
    kb_id: UUID,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    svc: KnowledgeService = Depends(get_knowledge_service),
    user: User = Depends(require_roles("root", "admin")),
):
    # Lê o body uma vez. UploadFile.read sem chunk vai até o fim — checagem
    # de tamanho é feita depois. FastAPI já limita por Content-Length se
    # o proxy for configurado; defesa em profundidade abaixo.
    content = await file.read()
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(
            413,
            f"arquivo excede o limite de {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB",
        )
    try:
        doc = await svc.upload_document(
            kb_id=kb_id,
            filename=file.filename or "documento",
            mime_type=file.content_type or "application/octet-stream",
            content=content,
            uploaded_by_id=str(user.id),
            uploaded_by_username=user.username,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Agenda o pipeline async — o cliente recebe 201 imediato com
    # status=pending. UI fica fazendo polling em GET /documents.
    background.add_task(svc.process_document, doc.id)

    return svc._doc_to_dict(doc)


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: UUID,
    svc: KnowledgeService = Depends(get_knowledge_service),
    user: User = Depends(require_roles("root", "admin")),
):
    doc = await svc.doc_repo.get(doc_id)
    if not doc:
        raise HTTPException(404, "documento não encontrado")
    await svc.delete_document(doc_id)
    return {"ok": True}


@router.post("/documents/{doc_id}/reprocess")
async def reprocess_document(
    doc_id: UUID,
    background: BackgroundTasks,
    svc: KnowledgeService = Depends(get_knowledge_service),
    user: User = Depends(require_roles("root", "admin")),
):
    """Re-roda o pipeline (extract → chunk → embed) num documento existente.

    Útil quando: o doc travou em 'processing' (BackgroundTask sumiu);
    o Docling/embeddings falharam e foi corrigida a config; o chunker mudou;
    o modelo de embedding foi atualizado (raro).

    `process_document` é idempotente — chunks antigos são apagados ANTES da
    nova extração. Chamar reprocess em um doc 'ready' não acumula duplicatas.
    """
    doc = await svc.doc_repo.get(doc_id)
    if not doc:
        raise HTTPException(404, "documento não encontrado")
    background.add_task(svc.process_document, doc_id)
    return {"ok": True, "status": "queued"}


# ============================================================
# Search (RAG retrieval teste)
# ============================================================


@router.post("/bases/{kb_id}/search")
async def search(
    kb_id: UUID,
    body: SearchRequest,
    svc: KnowledgeService = Depends(get_knowledge_service),
    user: User = Depends(require_roles("root", "admin")),
):
    """Busca semântica de teste — útil para validar a KB antes de
    associar a um módulo. Retorna os top-K chunks com score."""
    kb = await svc.get_base(kb_id)
    if not kb:
        raise HTTPException(404, "base de conhecimento não encontrada")
    return await svc.search(kb_id, body.query, top_k=body.top_k)
