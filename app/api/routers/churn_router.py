"""Router HTTP do módulo Churn."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_churn_service, require_roles, require_user
from app.api.schemas.churn import (
    ClassificationOut,
    ClassifyRequest,
    CreateNodeRequest,
    NodeOut,
    RenameNodeRequest,
)
from app.api.schemas.standard import StandardRequest, StandardResponse
from app.core.domain.entities import ChurnNode, User
from app.core.services.churn_service import ChurnService

router = APIRouter()


def _to_node_out(n: ChurnNode) -> NodeOut:
    return NodeOut(
        id=str(n.id),
        label=n.label,
        parent_id=str(n.parent_id) if n.parent_id else None,
        depth=n.depth,
        occurrences=n.occurrences,
        children=[_to_node_out(c) for c in n.children],
    )


@router.get("/taxonomy", response_model=list[NodeOut])
async def get_taxonomy(
    churn: ChurnService = Depends(get_churn_service),
    user: User = Depends(require_user),
):
    roots = await churn.get_taxonomy()
    return [_to_node_out(r) for r in roots]


@router.post("/nodes", response_model=NodeOut)
async def add_node(
    body: CreateNodeRequest,
    churn: ChurnService = Depends(get_churn_service),
    user: User = Depends(require_roles("admin", "supervisor")),
):
    parent = UUID(body.parent_id) if body.parent_id else None
    node = await churn.add_node(label=body.label, parent_id=parent)
    return _to_node_out(node)


@router.patch("/nodes/{node_id}", response_model=NodeOut)
async def rename_node(
    node_id: UUID,
    body: RenameNodeRequest,
    churn: ChurnService = Depends(get_churn_service),
    user: User = Depends(require_roles("admin", "supervisor")),
):
    try:
        node = await churn.rename_node(node_id, body.label)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return _to_node_out(node)


@router.delete("/nodes/{node_id}")
async def delete_node(
    node_id: UUID,
    churn: ChurnService = Depends(get_churn_service),
    user: User = Depends(require_roles("admin", "supervisor")),
):
    await churn.delete_node(node_id)
    return {"ok": True}


@router.post("/classify", response_model=ClassificationOut)
async def classify(
    body: ClassifyRequest,
    churn: ChurnService = Depends(get_churn_service),
    user: User = Depends(require_user),
):
    c = await churn.classify(body.contract_number, body.transcript, user_id=user.id)
    return ClassificationOut(
        contract_number=c.contract_number,
        path=c.path,
        confidence=c.confidence,
        rationale=c.rationale,
    )


@router.post("/v1/process", response_model=StandardResponse)
async def standard_process(
    body: StandardRequest,
    churn: ChurnService = Depends(get_churn_service),
    user: User = Depends(require_user),
):
    data = body.input_data
    if "transcript" not in data or "contract_number" not in data:
        raise HTTPException(400, "input_data requer 'transcript' e 'contract_number'")
    c = await churn.classify(data["contract_number"], data["transcript"], user_id=user.id)
    return StandardResponse(
        output_data={"path": c.path, "confidence": c.confidence, "rationale": c.rationale},
    )
