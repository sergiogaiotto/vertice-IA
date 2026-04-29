"""Router HTTP da Galeria de Apresentações VIP."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from app.api.deps import get_radar_service, require_user
from app.core.domain.entities import User
from app.core.services.presentation_service import (
    PresentationService,
    get_presentation_service,
)
from app.core.services.radar_service import RadarService

router = APIRouter()


# ===================== Schemas =====================


class CardInput(BaseModel):
    """Card enviado pelo cliente para alimentar a geração."""
    uid: str
    module_name: str
    module_id: str | None = None
    input_label: str = ""
    content: str = ""             # texto unificado: result | chat history serializado | etc


class VisualInput(BaseModel):
    """Imagem capturada de um card (chart canvas, diagram SVG)."""
    title: str
    type: str = "VISUAL"          # GRÁFICO | DIAGRAMA | VISUAL
    image_b64: str                # data:image/png;base64,XYZ ou XYZ puro
    caption: str = ""
    source_card_uid: str | None = None


class GeneratePreviewRequest(BaseModel):
    feature: str = "radar"
    case_number: str = ""
    title_hint: str = ""
    audience: str = "Cliente VIP"
    tone: str = "executivo"
    cards: list[CardInput]
    visuals: list[VisualInput] = []  # imagens já renderizadas no front


class InsightOut(BaseModel):
    type: str
    content: str


class SectionOut(BaseModel):
    title: str
    body: str
    source_card_uid: str | None = None


class GeneratePreviewResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    title: str
    subtitle: str
    insights: list[InsightOut]
    sections: list[SectionOut]
    visuals: list[VisualInput] = []   # ecoa as imagens recebidas (front pode mostrar preview)
    model_used: str
    tokens_input: int
    tokens_output: int
    cost_estimated: float


class SaveRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    feature: str = "radar"
    case_number: str = ""
    title: str
    subtitle: str = ""
    insights: list[InsightOut]
    sections: list[SectionOut]
    visuals: list[VisualInput] = []
    tokens_input: int = 0
    tokens_output: int = 0
    cost_estimated: float = 0.0
    model_used: str = ""


class UpdateRequest(BaseModel):
    title: str
    subtitle: str = ""
    insights: list[InsightOut]
    sections: list[SectionOut]
    visuals: list[VisualInput] = []


class PresentationOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: str
    title: str
    subtitle: str
    feature: str
    case_number: str
    sections: list[SectionOut]
    insights: list[InsightOut]
    visuals: list[VisualInput] = []
    chat_history: list[dict]
    created_by_user: str
    created_at: str
    updated_at: str
    cost_estimated: float
    tokens_input: int
    tokens_output: int
    model_used: str


def _to_out(p, include_visuals_b64: bool = True) -> PresentationOut:
    visuals = []
    for v in (p.visuals or []):
        # No detail page (include_visuals_b64=True) entrega tudo.
        # Na lista (False), omite a imagem para não pesar JSON com 5MB+
        b64 = v.get("image_b64", "") if include_visuals_b64 else ""
        visuals.append(VisualInput(
            title=v.get("title", "Visual"),
            type=v.get("type", "VISUAL"),
            image_b64=b64,
            caption=v.get("caption", ""),
            source_card_uid=v.get("source_card_uid"),
        ))
    return PresentationOut(
        id=p.id,
        title=p.title,
        subtitle=p.subtitle,
        feature=p.feature,
        case_number=p.case_number,
        sections=[SectionOut(**s) for s in p.sections],
        insights=[InsightOut(**i) for i in p.insights],
        visuals=visuals,
        chat_history=p.chat_history,
        created_by_user=p.created_by_user,
        created_at=p.created_at.isoformat(),
        updated_at=p.updated_at.isoformat(),
        cost_estimated=p.cost_estimated,
        tokens_input=p.tokens_input,
        tokens_output=p.tokens_output,
        model_used=p.model_used,
    )


# ===================== Geração via LLM =====================


_SYSTEM_PROMPT = """Você é um redator executivo sênior gerando uma apresentação para Cliente VIP.

A partir dos cards enviados (resultados de análises) e dos VISUAIS já capturados
(gráficos e diagramas em formato imagem), sintetize uma apresentação ELEGANTE,
CONCISA e ACIONÁVEL.

IMPORTANTE: cada VISUAL listado no input vai virar uma página/slide PRÓPRIA na
apresentação final (renderizado como imagem real). Você NÃO precisa descrever
o conteúdo do visual nas seções — ele aparece sozinho. Mas DEVE criar uma seção
introdutória ou conclusiva que faça referência ao que o visual mostra.

Devolva EXCLUSIVAMENTE um JSON com este schema:

{
  "title": "título principal — máx 80 chars, impactante",
  "subtitle": "uma frase contextual — máx 140 chars",
  "insights": [
    { "type": "OPORTUNIDADE | RISCO | DESTAQUE | ATENÇÃO | RECOMENDAÇÃO", "content": "frase única e poderosa, máx 200 chars" }
  ],
  "sections": [
    { "title": "Título da seção", "body": "markdown rico: ## subtítulos, listas, **negrito**, > citações", "source_card_uid": "uid_do_card_origem ou null" }
  ]
}

REGRAS:
- 3 a 6 insights — cada um vai num "card" da capa executiva
- 4 a 6 seções — cada uma vira um slide/página textual (NÃO ULTRAPASSE 6)
- body em markdown válido (## h2, ### h3, listas, **bold**, > blockquote)
- Tom: profissional, direto, sem jargão técnico desnecessário
- Não invente dados que não estão nos cards — síntese fiel
- Não use emojis — apresentação corporativa
- Comece pelo PORQUÊ (contexto), depois O QUE (achados), depois COMO (recomendações)
- Os visuais aparecem APÓS as seções textuais — você pode citá-los como "ver gráfico anexo"

REGRAS CRÍTICAS DE FORMATAÇÃO JSON (SIGA RIGOROSAMENTE):
1. Devolva APENAS o JSON, SEM ```json ao redor, SEM texto antes ou depois.
2. Aspas duplas DENTRO de strings DEVEM ser escapadas como \\" (barra invertida + aspas).
3. Quebras de linha dentro de strings (markdown body) DEVEM ser \\n (barra invertida + n), nunca quebra real.
4. NÃO use aspas inteligentes (" ") — apenas aspas ASCII retas (").
5. Se citar algo do conteúdo entre aspas, use aspas SIMPLES dentro do texto: 'palavra'.
6. Mantenha cada `body` com MENOS de 1500 caracteres para evitar truncamento.

Exemplo correto:
{ "body": "## Contexto\\n\\nO cliente solicitou 'redução de valor'. **Resultado**: aceito." }
"""


def _robust_json_parse(text: str) -> dict:
    """Parser tolerante a saídas de LLM imperfeitas.

    Estratégia em camadas:
    1. Parse direto
    2. Se falhar com 'Unterminated string', tenta fechar a estrutura (string + arrays/objetos)
    3. Se falhar com 'Expecting value', tenta extrair o último objeto bem-formado
    4. Última tentativa: substitui aspas simples internas por escape e re-parseia
    """
    text = (text or "").strip()
    if not text:
        raise json.JSONDecodeError("vazio", text, 0)

    # tenta direto
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # tenta fechar estruturas abertas em caso de truncamento
    closed = _try_close_json(text)
    if closed:
        try:
            return json.loads(closed)
        except json.JSONDecodeError:
            pass

    # última tentativa: regex-extrai o primeiro objeto raiz e tenta sanear aspas
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        candidate = text[first:last + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # tenta fechar versão recortada
            closed2 = _try_close_json(candidate)
            if closed2:
                try:
                    return json.loads(closed2)
                except json.JSONDecodeError:
                    pass

    # se nada funcionou, repropaga o erro original
    return json.loads(text)


def _try_close_json(text: str) -> str | None:
    """Tenta fechar JSON truncado: string aberta + arrays/objetos abertos.

    Caminha pelo texto contando { [ " e identifica posição do último delimitador
    válido. Não é perfeito (não trata escape em strings perfeitamente), mas
    cobre 90% dos casos de truncamento de LLM.
    """
    in_str = False
    escape = False
    stack = []  # pilha de '{' '['

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch == "}" or ch == "]":
            if stack and stack[-1] == ch:
                stack.pop()

    repair = text
    if in_str:
        # fecha a string aberta
        repair += '"'
    # fecha estruturas abertas (em ordem reversa)
    while stack:
        repair += stack.pop()

    return repair if repair != text else None


@router.post("/preview", response_model=GeneratePreviewResponse)
async def generate_preview(
    body: GeneratePreviewRequest,
    radar: RadarService = Depends(get_radar_service),
    user: User = Depends(require_user),
):
    """Gera preview da apresentação via LLM. Não persiste."""
    if not body.cards:
        raise HTTPException(400, "envie pelo menos 1 card como insumo")

    cards_text = []
    total_chars = 0
    MAX_TOTAL = 18000  # limite global para evitar prompts gigantes que causam truncamento
    for c in body.cards:
        if not c.content.strip():
            continue
        snippet = c.content[:2000]
        if total_chars + len(snippet) > MAX_TOTAL:
            snippet = snippet[:max(0, MAX_TOTAL - total_chars)]
            if not snippet:
                break
        cards_text.append(
            f"### Card uid={c.uid} · {c.module_name}\n"
            f"_input: {c.input_label}_\n\n{snippet}"
        )
        total_chars += len(snippet)

    if not cards_text and not body.visuals:
        raise HTTPException(400, "envie pelo menos 1 card ou visual com conteúdo")

    visuals_text = ""
    if body.visuals:
        visuals_lines = ["\n# Visuais já capturados (entrarão como imagens APÓS as seções):\n"]
        for i, v in enumerate(body.visuals, 1):
            visuals_lines.append(f"{i}. **{v.type}** — {v.title}" + (f" · {v.caption}" if v.caption else ""))
        visuals_text = "\n".join(visuals_lines)

    user_prompt = (
        f"# Funcionalidade: {body.feature}\n"
        f"# Caso (se houver): {body.case_number or 'N/A'}\n"
        f"# Audiência: {body.audience}\n"
        f"# Tom: {body.tone}\n"
        + (f"# Sugestão de título: {body.title_hint}\n" if body.title_hint else "")
        + "\n# Cards a sintetizar:\n\n"
        + "\n\n---\n\n".join(cards_text)
        + visuals_text
    )

    llm = await radar.router.complete(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        output_type="SUMARIO",
    )

    text = (llm.text or "").strip()
    # tira ```json ``` se LLM enfiou
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[-1].strip().startswith("```"):
            text = "\n".join(lines[1:-1]).strip()
    # se não começou com {, tenta extrair primeiro objeto
    if not text.startswith("{"):
        first = text.find("{"); last = text.rfind("}")
        if first >= 0 and last > first:
            text = text[first:last + 1]

    try:
        data = _robust_json_parse(text)
    except json.JSONDecodeError as e:
        raise HTTPException(
            502,
            f"LLM devolveu JSON inválido: {e}. "
            "Tente regenerar — o conteúdo pode estar muito longo. "
            "Reduza o número de cards-fonte se persistir."
        )

    # registra no FinOps
    from app.core.services.finops_service import FinOpsService
    await FinOpsService(radar.finops).record(
        user_id=user.id, module_id=None, model_name=llm.model,
        tokens_input=llm.tokens_input, tokens_output=llm.tokens_output,
        cost_estimated=llm.cost_estimated, context_tag=f"presentation/{body.feature}/preview",
    )

    return GeneratePreviewResponse(
        title=data.get("title", "Apresentação")[:80],
        subtitle=data.get("subtitle", "")[:140],
        insights=[InsightOut(type=i.get("type", "INSIGHT"), content=i.get("content", "")) for i in data.get("insights", [])][:8],
        sections=[SectionOut(
            title=s.get("title", ""),
            body=s.get("body", ""),
            source_card_uid=s.get("source_card_uid"),
        ) for s in data.get("sections", [])][:12],
        visuals=body.visuals,  # ecoa as imagens recebidas — front mostra preview
        model_used=llm.model,
        tokens_input=llm.tokens_input,
        tokens_output=llm.tokens_output,
        cost_estimated=llm.cost_estimated,
    )


# ===================== CRUD =====================


@router.get("/", response_model=list[PresentationOut])
async def list_presentations(
    q: str = "",
    feature: str = "",
    limit: int = 100,
    svc: PresentationService = Depends(get_presentation_service),
    user: User = Depends(require_user),
):
    items = await svc.list_all(limit=limit, q=q, feature=feature)
    return [_to_out(p, include_visuals_b64=False) for p in items]


@router.get("/stats")
async def presentations_stats(
    svc: PresentationService = Depends(get_presentation_service),
    user: User = Depends(require_user),
):
    return await svc.stats()


@router.get("/{presentation_id}", response_model=PresentationOut)
async def get_presentation(
    presentation_id: str,
    svc: PresentationService = Depends(get_presentation_service),
    user: User = Depends(require_user),
):
    p = await svc.get(presentation_id)
    if not p:
        raise HTTPException(404, "apresentação não encontrada")
    return _to_out(p)


@router.post("/", response_model=PresentationOut, status_code=201)
async def save_presentation(
    body: SaveRequest,
    svc: PresentationService = Depends(get_presentation_service),
    user: User = Depends(require_user),
):
    p = svc.new(
        title=body.title, subtitle=body.subtitle,
        feature=body.feature, case_number=body.case_number,
        sections=[s.model_dump() for s in body.sections],
        insights=[i.model_dump() for i in body.insights],
        visuals=[v.model_dump() for v in body.visuals],
        user=user.username, user_id=str(user.id),
        tokens_in=body.tokens_input, tokens_out=body.tokens_output,
        cost=body.cost_estimated, model=body.model_used,
    )
    saved = await svc.save(p)
    return _to_out(saved)


@router.patch("/{presentation_id}", response_model=PresentationOut)
async def update_presentation(
    presentation_id: str,
    body: UpdateRequest,
    svc: PresentationService = Depends(get_presentation_service),
    user: User = Depends(require_user),
):
    p = await svc.get(presentation_id)
    if not p:
        raise HTTPException(404, "apresentação não encontrada")
    p.title = body.title
    p.subtitle = body.subtitle
    p.sections = [s.model_dump() for s in body.sections]
    p.insights = [i.model_dump() for i in body.insights]
    p.visuals = [v.model_dump() for v in body.visuals]
    updated = await svc.update(p)
    return _to_out(updated)


@router.delete("/{presentation_id}")
async def delete_presentation(
    presentation_id: str,
    svc: PresentationService = Depends(get_presentation_service),
    user: User = Depends(require_user),
):
    await svc.delete(presentation_id)
    return {"ok": True}


# ===================== Export =====================


def _safe_filename(title: str | None, ext: str) -> tuple[str, str]:
    """Devolve (ascii_name, utf8_name) — ambos seguros para Content-Disposition.

    ascii_name: removido de tudo que não é ASCII alfanumérico — vai no `filename=`
    utf8_name:  versão completa, vai no `filename*=UTF-8''<percent-encoded>`
                seguindo RFC 5987 (suporta acentos, em-dash, etc).

    Headers HTTP só aceitam Latin-1 puro — em-dash (—), aspas tipográficas e
    outros caracteres Unicode quebram o Starlette.
    """
    import re
    import unicodedata
    from urllib.parse import quote

    raw = (title or "apresentacao").strip()
    # ascii: normaliza NFKD, remove combining chars, troca espaços por _
    nfkd = unicodedata.normalize("NFKD", raw)
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")
    ascii_only = re.sub(r"[^a-zA-Z0-9_-]+", "_", ascii_only).strip("_").lower()[:40] or "apresentacao"

    # utf8: percent-encoded para o filename*= (RFC 5987)
    utf8_full = re.sub(r"[\\/*?:\"<>|\r\n\t]+", "_", raw)[:80]
    utf8_quoted = quote(utf8_full + "." + ext, safe="")

    return f"{ascii_only}.{ext}", utf8_quoted


def _content_disposition(title: str | None, ext: str) -> str:
    ascii_name, utf8_quoted = _safe_filename(title, ext)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_quoted}"


@router.get("/{presentation_id}/download.docx")
async def download_docx(
    presentation_id: str,
    svc: PresentationService = Depends(get_presentation_service),
    user: User = Depends(require_user),
):
    p = await svc.get(presentation_id)
    if not p:
        raise HTTPException(404, "apresentação não encontrada")
    try:
        content = svc.export_docx(p)
    except ImportError:
        raise HTTPException(
            500,
            "Biblioteca python-docx não instalada. Execute "
            "`pip install -r requirements.txt` no servidor e reinicie o uvicorn."
        )
    except Exception as e:
        raise HTTPException(500, f"falha ao gerar DOCX: {e}")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": _content_disposition(p.title, "docx")},
    )


@router.get("/{presentation_id}/download.pptx")
async def download_pptx(
    presentation_id: str,
    svc: PresentationService = Depends(get_presentation_service),
    user: User = Depends(require_user),
):
    p = await svc.get(presentation_id)
    if not p:
        raise HTTPException(404, "apresentação não encontrada")
    try:
        content = svc.export_pptx(p)
    except ImportError:
        raise HTTPException(
            500,
            "Biblioteca python-pptx não instalada. Execute "
            "`pip install -r requirements.txt` no servidor e reinicie o uvicorn."
        )
    except Exception as e:
        raise HTTPException(500, f"falha ao gerar PPTX: {e}")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": _content_disposition(p.title, "pptx")},
    )


# ===================== Chat sobre apresentação =====================


class ChatTurn(BaseModel):
    role: str
    content: str


class PresentationChatRequest(BaseModel):
    message: str
    history: list[ChatTurn] = []


class PresentationChatResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    answer: str
    model_used: str
    tokens_input: int
    tokens_output: int
    cost_estimated: float


@router.post("/{presentation_id}/chat", response_model=PresentationChatResponse)
async def chat_about(
    presentation_id: str,
    body: PresentationChatRequest,
    svc: PresentationService = Depends(get_presentation_service),
    radar: RadarService = Depends(get_radar_service),
    user: User = Depends(require_user),
):
    """Chat sobre a apresentação. Histórico é persistido para consulta posterior."""
    p = await svc.get(presentation_id)
    if not p:
        raise HTTPException(404, "apresentação não encontrada")

    # monta contexto: insights + seções (resumo)
    ctx_parts = [f"# {p.title}"]
    if p.subtitle:
        ctx_parts.append(p.subtitle)
    if p.insights:
        ctx_parts.append("\n## Insights")
        for i in p.insights:
            ctx_parts.append(f"- **{i.get('type', 'INSIGHT')}**: {i.get('content', '')}")
    if p.sections:
        ctx_parts.append("\n## Seções")
        for s in p.sections:
            body_short = (s.get('body', '') or '')[:1500]
            ctx_parts.append(f"### {s.get('title', '')}\n{body_short}")
    ctx = "\n\n".join(ctx_parts)

    system = (
        "Você é o assistente da apresentação abaixo. Responda perguntas baseando-se "
        "EXCLUSIVAMENTE no conteúdo dela. Se a pergunta exigir dados ausentes, diga isso.\n\n"
        f"# Apresentação\n{ctx}"
    )
    history_str = ""
    if body.history:
        lines = [f"{t.role.upper()}: {t.content}" for t in body.history[-10:]]
        history_str = "\n\n# Histórico\n" + "\n".join(lines)
    user_prompt = f"{history_str}\n\n# Nova pergunta\n{body.message}".strip()

    llm = await radar.router.complete(
        system_prompt=system, user_prompt=user_prompt, output_type="SUMARIO",
    )

    # persiste histórico atualizado
    p.chat_history.append({"role": "user", "content": body.message})
    p.chat_history.append({"role": "assistant", "content": llm.text})
    await svc.update(p)

    from app.core.services.finops_service import FinOpsService
    await FinOpsService(radar.finops).record(
        user_id=user.id, module_id=None, model_name=llm.model,
        tokens_input=llm.tokens_input, tokens_output=llm.tokens_output,
        cost_estimated=llm.cost_estimated, context_tag=f"presentation/{presentation_id}/chat",
    )

    return PresentationChatResponse(
        answer=llm.text,
        model_used=llm.model,
        tokens_input=llm.tokens_input,
        tokens_output=llm.tokens_output,
        cost_estimated=llm.cost_estimated,
    )
