"""Router HTTP do Radar Voz do Cliente (BKO Inteligente)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict

from app.api.deps import (
    get_bko_service,
    get_prompt_service,
    get_radar_service,
    get_registry_service,
    get_skill_service,
    require_user,
)
from app.api.schemas.radar import CardOut, ContractOut, CreateCardRequest
from app.api.schemas.standard import StandardRequest, StandardResponse
from app.core.domain.entities import OutputType, User
from app.core.services.prompt_service import PromptService
from app.core.services.radar_service import RadarService
from app.core.services.registry_service import RegistryService
from app.core.services.skill_service import SkillService

router = APIRouter()


# ============================================================
# BKO Inteligente — upload XLSX (casos) e JSON (transcrições)
# ============================================================


@router.post("/upload-cases")
async def upload_cases_xlsx(
    file: UploadFile = File(...),
    bko=Depends(get_bko_service),
    user: User = Depends(require_user),
):
    """Upload da planilha de casos do BKO (.xlsx). Detecta duplicatas idênticas."""
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "arquivo deve ser .xlsx")
    try:
        stats = await bko.ingest_cases_xlsx(await file.read())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return stats


@router.post("/upload-transcripts")
async def upload_transcripts_json(
    files: list[UploadFile] = File(...),
    bko=Depends(get_bko_service),
    user: User = Depends(require_user),
):
    """Upload de um ou mais arquivos JSON de transcrição (Verint/WhisperX)."""
    payload = []
    for f in files:
        if not (f.filename or "").lower().endswith(".json"):
            continue
        payload.append((f.filename, await f.read()))
    if not payload:
        raise HTTPException(400, "envie pelo menos um arquivo .json")
    result = await bko.ingest_transcript_files(payload)
    return result


@router.get("/cases")
async def list_bko_cases(
    bko=Depends(get_bko_service),
    user: User = Depends(require_user),
):
    """Lista casos com flag de presença de transcrição."""
    items = await bko.list_cases_with_status(limit=500)
    # serializa datetime
    for it in items:
        if it["opened_at"]:
            it["opened_at"] = it["opened_at"].isoformat()
    return items


@router.get("/cases/search")
async def search_bko_cases(
    q: str = "",
    limit: int = 100,
    bko=Depends(get_bko_service),
    user: User = Depends(require_user),
):
    """Busca server-side por case_number, contract_msisdn ou owner.

    Importante: registrada ANTES de `/cases/{case_number}` para evitar match incorreto.
    """
    return await bko.search_cases(q=q, limit=min(limit, 500))


# ============================================================
# Schema introspection — tabelas e colunas como inputs
# ============================================================

from app.api.deps import get_schema_service  # noqa: E402


@router.get("/schema/tables")
async def schema_tables(
    feature: str | None = None,
    schema=Depends(get_schema_service),
    user: User = Depends(require_user),
):
    """Lista tabelas + colunas + samples. Use ?feature=radar|churn|admin para filtrar."""
    return await schema.list_tables(feature=feature)


@router.get("/schema/value")
async def schema_value(
    table: str,
    column: str,
    pk_column: str,
    pk_value: str,
    schema=Depends(get_schema_service),
    user: User = Depends(require_user),
):
    """Lê o valor de uma coluna específica para uma linha identificada pela PK."""
    try:
        v = await schema.get_value(table, column, pk_column, pk_value)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if v is None:
        raise HTTPException(404, "valor não encontrado")
    return {"value": v}


@router.get("/schema/series")
async def schema_series(
    table: str,
    label_column: str,
    value_column: str,
    aggregate: str = "sum",  # 'sum' | 'count' | 'avg' | 'min' | 'max' | 'none'
    order_by: str = "label_asc",  # 'label_asc' | 'label_desc' | 'value_desc'
    limit: int = 50,
    schema=Depends(get_schema_service),
    user: User = Depends(require_user),
):
    """Devolve uma série {labels: [...], values: [...]} pronta para Chart.js."""
    try:
        result = await schema.fetch_series(
            table=table, label_column=label_column, value_column=value_column,
            aggregate=aggregate, order_by=order_by, limit=min(limit, 200),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.get("/cases/{case_number}")
async def get_bko_case(
    case_number: str,
    bko=Depends(get_bko_service),
    user: User = Depends(require_user),
):
    detail = await bko.get_case_with_transcript(case_number)
    if not detail:
        raise HTTPException(404, "caso não encontrado")
    case = detail["case"]
    transcript = detail["transcript"]
    return {
        "case": {
            "case_number": case.case_number,
            "created_by": case.created_by,
            "owner": case.owner,
            "phone": case.phone,
            "opened_at": case.opened_at.isoformat() if case.opened_at else None,
            "contract_msisdn": case.contract_msisdn,
        },
        "transcript": ({
            "transaction_id": transcript.transaction_id,
            "verint_nr_contrato": transcript.verint_nr_contrato,
            "transcription_text": transcript.transcription_text,
            "started_at": transcript.started_at.isoformat() if transcript.started_at else None,
            "duration_s": transcript.duration_s,
            "segment": transcript.segment,
            "msisdn": transcript.msisdn,
            "ani": transcript.ani,
            "cpf": transcript.cpf,
            "employee": transcript.employee,
        } if transcript else None),
        "alternates_count": len(detail["all_transcripts_for_contract"]),
    }


@router.get("/stats")
async def bko_stats(
    bko=Depends(get_bko_service),
    user: User = Depends(require_user),
):
    return await bko.stats()


# ============================================================
# Legado: endpoints antigos (Excel de contratos antigo)
# ============================================================


@router.post("/upload")
async def upload_excel(
    file: UploadFile = File(...),
    radar: RadarService = Depends(get_radar_service),
    user: User = Depends(require_user),
):
    content = await file.read()
    n = await radar.ingest_excel(content)
    return {"imported": n}


@router.get("/contracts", response_model=list[ContractOut])
async def list_contracts(
    radar: RadarService = Depends(get_radar_service),
    user: User = Depends(require_user),
):
    contracts = await radar.list_contracts(limit=200)
    return [
        ContractOut(
            contract_number=c.contract_number,
            call_id=c.call_id,
            contact_id=c.contact_id,
            operator=c.operator,
            contact_at=c.contact_at,
            segment=c.segment.value,
            transcript_preview=(c.transcript or "")[:160],
        )
        for c in contracts
    ]


# ============================================================
# Run module on transcript (efêmero) — agora aceita transaction_id
# ============================================================


class RunModuleRequest(BaseModel):
    transaction_id: str
    module_id: UUID
    input_text: str | None = None     # se fornecido, usa em vez da transcription_text
    input_label: str | None = None    # rótulo do campo escolhido (apenas para auditoria/contexto)


class RunModuleResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    module_id: str
    module_name: str
    module_description: str
    result: str
    model_used: str
    tokens_input: int
    tokens_output: int
    cost_estimated: float
    prompt_used: str | None = None
    prompt_version: int | None = None
    skill_used: bool = False
    input_label: str | None = None
    input_chars: int = 0
    output_format: str = "markdown"          # 'markdown' | 'json' | 'csv' | 'html' | 'xml'
    artifact_id: str | None = None
    artifact_filename: str | None = None
    artifact_url: str | None = None
    response_type: str = "text"              # 'text' | 'api' | 'table'
    response_action: dict | None = None      # {kind, ok, error?, ...} resultado do despacho


@router.post("/run-module", response_model=RunModuleResponse)
async def run_module(
    body: RunModuleRequest,
    radar: RadarService = Depends(get_radar_service),
    reg: RegistryService = Depends(get_registry_service),
    prompts: PromptService = Depends(get_prompt_service),
    skills: SkillService = Depends(get_skill_service),
    bko=Depends(get_bko_service),
    user: User = Depends(require_user),
):
    """Executa um módulo aplicando suas regras (prompt ativo + skill) sobre um texto.

    Se `input_text` for enviado, é usado diretamente.
    Caso contrário, busca a transcrição pelo `transaction_id` e usa `transcription_text`.
    """
    module = await reg.get(body.module_id)
    if not module:
        raise HTTPException(404, "módulo não encontrado")
    if module.status.value != "active":
        raise HTTPException(400, f"módulo está {module.status.value} — só ativos podem ser executados")

    # determina o texto de input
    if body.input_text is not None and body.input_text != "":
        input_text = body.input_text
        input_label = body.input_label or "input customizado"
    else:
        transcript = await bko.get_transcript(body.transaction_id)
        if not transcript:
            raise HTTPException(404, "transcrição não encontrada e nenhum input_text fornecido")
        input_text = transcript.transcription_text or ""
        input_label = "transcription_text"

    # carrega skill
    skill_content = None
    if module.skill_path:
        stem = module.skill_path.rsplit("/", 1)[-1].replace(".md", "")
        skill_obj = skills.get(stem)
        if skill_obj:
            skill_content = skill_obj.content

    module_prompts = await prompts.list_for_module(module.name)
    active_prompt = next((p for p in module_prompts if p.is_active), None)

    # contexto adicional para módulos response_type='table'/'api'
    case_number_ctx = None
    transaction_id_ctx = body.transaction_id if body.transaction_id else None
    if transaction_id_ctx:
        try:
            tx_obj = await bko.get_transcript(transaction_id_ctx)
            if tx_obj:
                case_number_ctx = tx_obj.verint_nr_contrato
        except Exception:
            pass

    try:
        result = await radar.run_module_on_text(
            transcript_text=input_text,
            module=module,
            skill_content=skill_content,
            prompt=active_prompt,
            user_id=user.id,
            input_label=input_label,
            username=user.username,
            case_number=case_number_ctx,
            transaction_id=transaction_id_ctx,
            feature="radar",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    artifact_url = None
    if result.get("artifact_id"):
        artifact_url = f"/api/radar/artifacts/{result['artifact_id']}"

    return RunModuleResponse(
        **result,
        input_label=input_label,
        input_chars=len(input_text),
        artifact_url=artifact_url,
    )


# ============================================================
# Legado: cards e Standard Module Contract
# ============================================================


@router.post("/cards", response_model=CardOut)
async def create_card(
    body: CreateCardRequest,
    radar: RadarService = Depends(get_radar_service),
    user: User = Depends(require_user),
):
    try:
        ot = OutputType(body.output_type.upper())
    except ValueError:
        raise HTTPException(400, f"output_type inválido: {body.output_type}")
    try:
        card = await radar.create_analysis_card(
            contract_number=body.contract_number,
            name=body.name,
            prompt_text=body.prompt_text,
            output_type=ot,
            expected_size=body.expected_size,
            user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _to_card_out(card)


@router.delete("/cards/{card_id}")
async def delete_card(
    card_id: UUID,
    radar: RadarService = Depends(get_radar_service),
    user: User = Depends(require_user),
):
    await radar.delete_card(card_id)
    return {"ok": True}


@router.post("/v1/process", response_model=StandardResponse)
async def standard_process(
    body: StandardRequest,
    radar: RadarService = Depends(get_radar_service),
    user: User = Depends(require_user),
):
    """Endpoint compatível com o Standard Module Contract."""
    data = body.input_data
    required = {"contract_number", "name", "prompt_text", "output_type"}
    if not required.issubset(data):
        raise HTTPException(400, f"input_data requer chaves: {required}")
    try:
        ot = OutputType(str(data["output_type"]).upper())
    except ValueError:
        raise HTTPException(400, "output_type inválido")
    card = await radar.create_analysis_card(
        contract_number=data["contract_number"],
        name=data["name"],
        prompt_text=data["prompt_text"],
        output_type=ot,
        expected_size=data.get("expected_size", ""),
        user_id=user.id,
    )
    return StandardResponse(
        output_data={"card_id": str(card.id), "result": card.result, "output_type": card.output_type.value},
        model_used=card.model_used,
        tokens_input=card.tokens_input,
        tokens_output=card.tokens_output,
        cost_estimated=card.cost_estimated,
    )


def _to_card_out(c) -> CardOut:
    return CardOut(
        id=str(c.id),
        contract_number=c.contract_number,
        name=c.name,
        output_type=c.output_type.value,
        result=c.result,
        model_used=c.model_used,
        confidence=c.confidence,
        tokens_input=c.tokens_input,
        tokens_output=c.tokens_output,
        cost_estimated=c.cost_estimated,
        created_at=c.created_at,
    )


# ============================================================
# Artifact download — arquivos gerados por execuções de módulo
# ============================================================

from fastapi.responses import Response  # noqa: E402

from app.core.services.artifact_store import get_artifact_store  # noqa: E402


@router.get("/artifacts/{artifact_id}")
async def download_artifact(
    artifact_id: str,
    user: User = Depends(require_user),
):
    """Devolve o arquivo gerado por uma execução de módulo (TTL 30min)."""
    store = get_artifact_store()
    art = await store.get(artifact_id)
    if not art:
        raise HTTPException(404, "artefato não encontrado ou expirado")
    return Response(
        content=art.content,
        media_type=art.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{art.filename}"'},
    )


# ============================================================
# Chat — módulo virtual conversacional com contexto multi-campo
# ============================================================


class ChatField(BaseModel):
    label: str
    value: str


class ChatTurn(BaseModel):
    role: str  # 'user' | 'assistant'
    content: str


class ChatRequest(BaseModel):
    feature: str = "radar"        # qual funcionalidade (radar/churn/...)
    fields: list[ChatField] = []  # campos de contexto carregados pelo usuário
    history: list[ChatTurn] = []  # turnos anteriores na conversa
    message: str                  # nova mensagem do usuário


class ChatResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    answer: str
    model_used: str
    tokens_input: int
    tokens_output: int
    cost_estimated: float


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    radar: RadarService = Depends(get_radar_service),
    user: User = Depends(require_user),
):
    """Endpoint do módulo virtual Chat.

    Diferenças vs /run-module:
    - Mantém histórico de turnos passado pelo client (stateless no servidor)
    - Aceita N campos de contexto (não 1 só) — todos viram parte do system prompt
    - Não usa skill nem prompt de módulo — comportamento conversacional fixo
    - O escopo (`feature`) entra no system prompt para a IA limitar respostas
    """
    if not body.message or not body.message.strip():
        raise HTTPException(400, "mensagem vazia")

    # monta system prompt com contexto da funcionalidade + campos
    system_parts = [
        f"Você é o assistente conversacional da funcionalidade '{body.feature}' "
        f"da plataforma Vértice. Responda em PT-BR de forma concisa e útil.",
        "Use APENAS os campos de contexto abaixo. Se a pergunta exigir dados "
        "ausentes do contexto, diga isso explicitamente em vez de inventar.",
        "Mantenha respostas focadas, sem repetir o contexto a cada turno.",
    ]
    if body.fields:
        ctx_lines = ["# Contexto carregado"]
        for f in body.fields:
            preview = (f.value or "")[:4000]  # limita por campo
            ctx_lines.append(f"## {f.label}\n{preview}")
        system_parts.append("\n".join(ctx_lines))
    else:
        system_parts.append(
            "# Contexto\nNenhum campo carregado — responda apenas perguntas "
            "gerais sobre a funcionalidade ou peça que o usuário adicione contexto."
        )
    system = "\n\n".join(system_parts)

    # monta user prompt com histórico
    history_str = ""
    if body.history:
        lines = []
        for turn in body.history[-10:]:  # últimos 10 turnos para não estourar contexto
            lines.append(f"{turn.role.upper()}: {turn.content}")
        history_str = "\n\n# Histórico recente\n" + "\n".join(lines)
    user_prompt = f"{history_str}\n\n# Nova pergunta do usuário\n{body.message}".strip()

    llm = await radar.router.complete(
        system_prompt=system,
        user_prompt=user_prompt,
        output_type="SUMARIO",
    )

    # registra no FinOps
    from app.core.services.finops_service import FinOpsService  # noqa: E402
    await FinOpsService(radar.finops).record(
        user_id=user.id,
        module_id=None,
        model_name=llm.model,
        tokens_input=llm.tokens_input,
        tokens_output=llm.tokens_output,
        cost_estimated=llm.cost_estimated,
        context_tag=f"chat/{body.feature}",
    )

    return ChatResponse(
        answer=llm.text,
        model_used=llm.model,
        tokens_input=llm.tokens_input,
        tokens_output=llm.tokens_output,
        cost_estimated=llm.cost_estimated,
    )


# ============================================================
# Generate Skill — gera SKILL.md a partir do contexto + histórico do chat
# ============================================================


class GenerateSkillRequest(BaseModel):
    feature: str = "radar"
    fields: list[ChatField] = []
    history: list[ChatTurn] = []
    instruction: str = ""  # opcional — usuário pode dar diretrizes adicionais


class GenerateSkillResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    suggested_name: str         # slug sugerido para nome do arquivo
    suggested_title: str        # título humano
    skill_content: str          # markdown completo da skill
    model_used: str
    tokens_input: int
    tokens_output: int
    cost_estimated: float


_SKILL_GEN_SYSTEM = """Você é um arquiteto de skills da plataforma Vértice.

A partir de um histórico de conversa entre usuário e assistente sobre dados de
uma funcionalidade, você gera um arquivo SKILL.md que **destila** o que foi
aprendido em um contrato declarativo reutilizável.

Formato OBRIGATÓRIO (Markdown):

```
# {Título da Skill}

## Identidade
Quem é o agente, em uma frase. Define escopo e tom.

## Inputs aceitos
- `campo1` (tipo): descrição
- `campo2` (tipo): descrição

## Saída esperada
Schema preciso. Se for JSON, mostre o schema entre ```json. Se markdown, descreva as seções.

## Ferramentas autorizadas
- nome_ferramenta(params): condição de uso (ou "Nenhuma" se inferência pura)

## Política de roteamento
- Default: `sabia-4`
- Fallback: `gpt-4.1`

## Guardrails

### Entrada
- regra 1
- regra 2

### Saída
- regra 1
- regra 2

## Sinais de Failsafe
- condição que dispara revisão humana
```

Regras:
- O título deve refletir o domínio da conversa (ex: "Análise de Sentimento de Tweets", não "Skill Gerada").
- Inputs devem espelhar os campos de contexto fornecidos.
- Saída deve refletir o que o usuário pareceu querer nas perguntas.
- Guardrails devem ser específicos (não genéricos como "validar entrada").
- NÃO envolva o markdown em ```markdown ao redor.

Devolva APENAS o conteúdo do SKILL.md — primeira linha deve ser `# Título`.
"""


def _slugify_skill_name(text: str) -> str:
    import re, unicodedata
    nfkd = unicodedata.normalize("NFKD", text or "")
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9_]+", "_", ascii_text).strip("_")
    return slug[:40] or "nova_skill"


@router.post("/chat/generate-skill", response_model=GenerateSkillResponse)
async def generate_skill_from_chat(
    body: GenerateSkillRequest,
    radar: RadarService = Depends(get_radar_service),
    user: User = Depends(require_user),
):
    """Gera uma SKILL.md a partir do contexto + histórico do chat.

    Não persiste — devolve preview para o usuário validar/ajustar antes de salvar.
    Salvar é feito via POST /api/skills/ com o `skill_content` editado.
    """
    if not body.history and not body.instruction:
        raise HTTPException(400, "histórico ou instrução são necessários")

    # monta user_prompt rico
    parts = [f"# Funcionalidade: {body.feature}"]
    if body.fields:
        parts.append("\n## Campos de contexto utilizados na conversa")
        for f in body.fields:
            preview = (f.value or "")[:500]
            parts.append(f"### {f.label}\n```\n{preview}{'…' if len(f.value or '') > 500 else ''}\n```")

    if body.history:
        parts.append("\n## Histórico da conversa")
        for turn in body.history[-15:]:
            parts.append(f"**{turn.role.upper()}:** {turn.content}")

    if body.instruction.strip():
        parts.append(f"\n## Instrução adicional do usuário\n{body.instruction.strip()}")

    parts.append(
        "\n## Tarefa\n"
        "Gere o SKILL.md que codifique o padrão observado na conversa acima. "
        "O título deve sugerir um nome reutilizável; os inputs devem espelhar "
        "os campos de contexto; a saída deve refletir o que o usuário pediu."
    )
    user_prompt = "\n\n".join(parts)

    llm = await radar.router.complete(
        system_prompt=_SKILL_GEN_SYSTEM,
        user_prompt=user_prompt,
        output_type="SUMARIO",
    )

    # extrai título (primeira linha #) e gera slug
    skill_md = (llm.text or "").strip()
    # remove ```markdown ... ``` se o LLM tiver envolvido
    if skill_md.startswith("```"):
        lines = skill_md.split("\n")
        if lines[-1].strip().startswith("```"):
            skill_md = "\n".join(lines[1:-1]).strip()

    title = "Nova Skill"
    for line in skill_md.split("\n"):
        if line.startswith("# "):
            title = line[2:].strip()
            break

    slug = _slugify_skill_name(title)

    # registra no FinOps
    from app.core.services.finops_service import FinOpsService  # noqa: E402
    await FinOpsService(radar.finops).record(
        user_id=user.id,
        module_id=None,
        model_name=llm.model,
        tokens_input=llm.tokens_input,
        tokens_output=llm.tokens_output,
        cost_estimated=llm.cost_estimated,
        context_tag=f"chat/{body.feature}/skill_gen",
    )

    return GenerateSkillResponse(
        suggested_name=slug,
        suggested_title=title,
        skill_content=skill_md,
        model_used=llm.model,
        tokens_input=llm.tokens_input,
        tokens_output=llm.tokens_output,
        cost_estimated=llm.cost_estimated,
    )


# ============================================================
# Diagram — gera notação Mermaid a partir de texto/contexto
# ============================================================


class DiagramRequest(BaseModel):
    source_text: str            # texto-fonte (resultado de outro módulo)
    source_label: str = ""      # de onde veio (auditoria)
    diagram_type: str = "flowchart"  # flowchart|sequence|class|state|er|gantt|mindmap|journey
    instruction: str = ""       # diretriz adicional opcional


class DiagramResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    mermaid_code: str
    diagram_type: str
    title: str
    model_used: str
    tokens_input: int
    tokens_output: int
    cost_estimated: float


_DIAGRAM_TYPE_HINTS = {
    "flowchart": "flowchart TD\n  Inicio --> Fim",
    "sequence":  "sequenceDiagram\n  participant A\n  participant B\n  A->>B: mensagem",
    "class":     "classDiagram\n  class Foo { +bar() }",
    "state":     "stateDiagram-v2\n  [*] --> Estado1\n  Estado1 --> [*]",
    "er":        "erDiagram\n  CLIENTE ||--o{ PEDIDO : faz",
    "gantt":     "gantt\n  title Cronograma\n  dateFormat YYYY-MM-DD\n  Tarefa1 :a1, 2026-01-01, 30d",
    "mindmap":   "mindmap\n  root((Tópico))\n    Sub1\n    Sub2",
    "journey":   "journey\n  title Jornada\n  section Etapa1\n    Ação: 5: Cliente",
}

_DIAGRAM_SYSTEM = (
    "Você é um especialista em diagramação Mermaid.js. Receba o conteúdo "
    "abaixo e gere SOMENTE código Mermaid válido — sem ``` ao redor, sem "
    "explicação, sem markdown adicional.\n\n"
    "REGRAS DURAS:\n"
    "1. A primeira linha DEVE ser uma diretiva Mermaid válida (flowchart TD, "
    "sequenceDiagram, classDiagram, etc).\n"
    "2. Use IDs ASCII curtos sem espaços (A, B, n1, etc); rótulos vão entre "
    "colchetes ou aspas.\n"
    "3. Mantenha o diagrama legível — máximo 25 nós/passos. Agrupe se houver mais.\n"
    "4. Para rótulos com acentos/parênteses, use aspas duplas: A[\"Cliente (BKO)\"].\n"
    "5. NÃO use caracteres não-ASCII em IDs — só nos rótulos entre aspas.\n"
)


@router.post("/diagram", response_model=DiagramResponse)
async def generate_diagram(
    body: DiagramRequest,
    radar: RadarService = Depends(get_radar_service),
    user: User = Depends(require_user),
):
    """Converte texto livre em código Mermaid.

    Source pode ser o resultado de qualquer módulo (texto markdown, JSON,
    transcrição). O LLM destila a estrutura observada no formato escolhido.
    """
    if not body.source_text or not body.source_text.strip():
        raise HTTPException(400, "source_text vazio")
    diagram_type = body.diagram_type if body.diagram_type in _DIAGRAM_TYPE_HINTS else "flowchart"
    type_hint = _DIAGRAM_TYPE_HINTS[diagram_type]

    system = (
        f"{_DIAGRAM_SYSTEM}\n\n"
        f"# Tipo de diagrama solicitado: {diagram_type}\n\n"
        f"# Exemplo do formato esperado:\n```\n{type_hint}\n```"
    )

    user_parts = []
    if body.source_label:
        user_parts.append(f"# Origem do conteúdo: {body.source_label}")
    user_parts.append(f"# Conteúdo a diagramar\n\n{body.source_text[:6000]}")
    if body.instruction.strip():
        user_parts.append(f"# Diretriz adicional\n{body.instruction.strip()}")
    user_parts.append(
        f"\n# Tarefa\nGere o diagrama Mermaid do tipo `{diagram_type}` que "
        "represente a estrutura observada no conteúdo. Devolva APENAS o código."
    )
    user_prompt = "\n\n".join(user_parts)

    llm = await radar.router.complete(
        system_prompt=system,
        user_prompt=user_prompt,
        output_type="SUMARIO",
    )

    # limpa code fence se LLM tiver adicionado mesmo proibido
    code = (llm.text or "").strip()
    if code.startswith("```"):
        lines = code.split("\n")
        # remove primeira linha "```mermaid" e última "```"
        if lines and lines[-1].strip().startswith("```"):
            code = "\n".join(lines[1:-1]).strip()

    # extrai título se houver "title" na primeira linha (gantt/journey usam)
    title = f"Diagrama {diagram_type}"
    for line in code.split("\n")[:5]:
        if line.lower().strip().startswith("title "):
            title = line.split(" ", 1)[1].strip()
            break

    from app.core.services.finops_service import FinOpsService
    await FinOpsService(radar.finops).record(
        user_id=user.id,
        module_id=None,
        model_name=llm.model,
        tokens_input=llm.tokens_input,
        tokens_output=llm.tokens_output,
        cost_estimated=llm.cost_estimated,
        context_tag=f"diagram/{diagram_type}",
    )

    return DiagramResponse(
        mermaid_code=code,
        diagram_type=diagram_type,
        title=title,
        model_used=llm.model,
        tokens_input=llm.tokens_input,
        tokens_output=llm.tokens_output,
        cost_estimated=llm.cost_estimated,
    )


# ============================================================
# Text-to-SQL — Deep Agent que explora dados em linguagem natural
# ============================================================


class Text2SqlChatTurn(BaseModel):
    role: str
    content: str


class Text2SqlAskRequest(BaseModel):
    question: str
    tables: list[str]                       # tabelas autorizadas no escopo
    history: list[Text2SqlChatTurn] = []
    feature: str = "radar"
    # Filtros de contexto: o agente DEVE limitar resultados a estes valores
    case_number: str = ""
    username: str = ""


class Text2SqlAskResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    understanding: str
    sql: str
    result_columns: list[str]
    result_rows: list[list]
    row_count: int
    analyses: list[str]
    raw_text: str
    model_used: str
    tokens_input: int
    tokens_output: int
    cost_estimated: float
    error: str | None = None


@router.get("/sql/health")
async def sql_health_check(user: User = Depends(require_user)):
    """Diagnóstico das dependências do Deep Agent text-to-sql.

    Retorna {ok, error?, instructions?} — UI usa para alertar antes de mostrar
    o modal de configuração.
    """
    import sys
    from app.core.services.text2sql_service import check_dependencies
    ok, err = check_dependencies()
    if ok:
        return {"ok": True, "python": sys.executable, "venv": sys.prefix}
    return {
        "ok": False,
        "python": sys.executable,
        "venv": sys.prefix,
        "error": err,
        "instructions": (
            f"O uvicorn está rodando em: {sys.executable}. "
            "PARE o uvicorn (Ctrl+C). "
            "Rode no PowerShell, dentro da MESMA venv: "
            "`python scripts/fix_text2sql.py --fix` "
            "(diagnostica + reinstala as deps com força). "
            "Reinicie: `uvicorn app.main:app --reload`."
        ),
    }


@router.get("/sql/tables")
async def sql_available_tables(
    feature: str = "radar",
    user: User = Depends(require_user),
):
    """Lista tabelas disponíveis para Text-to-SQL no escopo da feature."""
    from app.core.services.text2sql_service import check_dependencies, get_text2sql_service
    ok, err = check_dependencies()
    if not ok:
        raise HTTPException(503, f"Deep Agent indisponível: {err}. Veja /api/radar/sql/health para instruções.")
    svc = get_text2sql_service()
    tables = await svc.list_available_tables(feature=feature)
    return {"tables": tables}


@router.post("/sql/ask", response_model=Text2SqlAskResponse)
async def sql_ask(
    body: Text2SqlAskRequest,
    radar: RadarService = Depends(get_radar_service),
    user: User = Depends(require_user),
):
    """Roda o Deep Agent Text-to-SQL com escopo restrito + histórico."""
    from app.core.services.text2sql_service import check_dependencies, get_text2sql_service

    # check eager — retorna 503 com instruções ANTES de tentar instanciar o agente
    ok, err = check_dependencies()
    if not ok:
        raise HTTPException(
            503,
            f"Deep Agent indisponível. Ambiente atual: {err}. "
            "PARE o uvicorn e rode no PowerShell DENTRO da venv: "
            "`python scripts/fix_text2sql.py --fix` "
            "(reinstala forçadamente as deps no Python correto). "
            "Reinicie o servidor depois."
        )

    svc = get_text2sql_service()
    try:
        result = await svc.ask(
            question=body.question,
            allowed_tables=body.tables,
            history=[t.model_dump() for t in body.history],
            feature=body.feature,
            user_id=str(user.id) if user.id else None,
            case_number=body.case_number or "",
            username=body.username or user.username or "",
        )
    except ImportError as e:
        # fallback caso check_dependencies tenha passado mas algum import lazy falhar
        raise HTTPException(
            500,
            f"Falha ao carregar Deep Agent: {e}. "
            "Provavelmente o uvicorn precisa ser reiniciado após `pip install`."
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    # registra no FinOps
    if result.cost_estimated > 0:
        from app.core.services.finops_service import FinOpsService
        await FinOpsService(radar.finops).record(
            user_id=user.id,
            module_id=None,
            model_name=result.model_used,
            tokens_input=result.tokens_input,
            tokens_output=result.tokens_output,
            cost_estimated=result.cost_estimated,
            context_tag=f"text2sql/{body.feature}",
        )

    return Text2SqlAskResponse(
        understanding=result.understanding,
        sql=result.sql,
        result_columns=result.result_columns,
        result_rows=result.result_rows,
        row_count=result.row_count,
        analyses=result.analyses,
        raw_text=result.raw_text,
        model_used=result.model_used,
        tokens_input=result.tokens_input,
        tokens_output=result.tokens_output,
        cost_estimated=result.cost_estimated,
        error=result.error,
    )
