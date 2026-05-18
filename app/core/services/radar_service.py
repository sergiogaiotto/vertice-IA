"""Use case: Radar Voz do Cliente."""

from __future__ import annotations

import io
import json
import re
import time
from datetime import datetime

from app.core.domain.entities import (
    AnalysisCard,
    Contract,
    CustomerSegment,
    OutputType,
    new_uuid,
)
from app.core.ports.guardrails import InputGuardrail, OutputGuardrail
from app.core.ports.repositories import (
    AnalysisRepository,
    ContractRepository,
    FinOpsRepository,
)
from app.core.services.finops_service import FinOpsService
from app.core.services.model_router import ModelRouter

# instruções de saída por tipo
_OUTPUT_HINTS: dict[OutputType, str] = {
    OutputType.summary: "Devolva um sumário em PT-BR com até 80 palavras.",
    OutputType.resume: "Devolva um resumo em PT-BR com até 30 palavras.",
    OutputType.intent: "Devolva apenas a intenção principal em uma frase curta.",
    OutputType.one_word: "Devolva exatamente UMA palavra em PT-BR.",
    OutputType.score: "Devolva apenas um número inteiro entre 0 e 100.",
    OutputType.terms: "Devolva apenas uma lista de até 8 termos separados por vírgula.",
}


# Marker visível para o LLM saber que o trecho intermediário foi removido,
# evitando que ele tente "completar" o contexto faltante por inferência.
_TRUNCATE_MARKER = "\n\n[... trecho intermediário da transcrição omitido ...]\n\n"


def _smart_truncate_transcript(
    text: str,
    max_chars: int = 12000,
    tail_ratio: float = 0.4,
) -> str:
    """Encurta uma transcrição preservando início E final.

    Transcrições de atendimento têm estrutura típica:
      - INÍCIO: saudação, identificação, abertura do problema
      - MEIO: idas e vindas, confirmações, repetições (frequentemente redundante)
      - FIM: oferta de solução, aceite/recusa, desfecho

    Truncar só pelo início (`text[:N]`) descarta o desfecho — justamente o
    trecho mais relevante para análise de intenção, churn, resolução, etc.
    Esta função pega ``(1 - tail_ratio) * max_chars`` do INÍCIO e
    ``tail_ratio * max_chars`` do FINAL, concatenados com um marker que
    avisa o LLM que houve corte.

    Args:
        text: a transcrição (ou qualquer texto livre).
        max_chars: tamanho-alvo do resultado (incluindo o marker). Default
                   12000 ≈ 3000 tokens — bem dentro do contexto do sabia-4
                   (32k tokens) com folga para system_prompt + output.
        tail_ratio: fração do max_chars reservada para o FINAL. Default 0.4
                    (40% fim, 60% início). Para módulos onde o final é
                    crítico (resolução), aumentar; para classificação de
                    tópico (início basta), diminuir.

    Returns:
        - texto original se ``len(text) <= max_chars``
        - ``head + marker + tail`` caso contrário
        - string vazia se ``text`` for None/empty
    """
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= len(_TRUNCATE_MARKER):
        # caso degenerado: max_chars muito pequeno para acomodar o marker
        # — devolve só o início, sem marker (fallback compatível).
        return text[:max_chars]
    available = max_chars - len(_TRUNCATE_MARKER)
    tail_len = int(available * tail_ratio)
    head_len = available - tail_len
    head = text[:head_len]
    tail = text[-tail_len:] if tail_len > 0 else ""
    return f"{head}{_TRUNCATE_MARKER}{tail}"


class RadarService:
    def __init__(
        self,
        contracts: ContractRepository,
        analyses: AnalysisRepository,
        finops: FinOpsRepository,
        router: ModelRouter,
        input_guard: InputGuardrail,
        output_guard: OutputGuardrail,
    ):
        self.contracts = contracts
        self.analyses = analyses
        self.finops = finops
        self.router = router
        self.input_guard = input_guard
        self.output_guard = output_guard

    # ---------- ingest ----------

    async def ingest_excel(self, file_bytes: bytes) -> int:
        """Importa contratos de uma planilha Excel.

        Colunas esperadas (case-insensitive):
          datetime | call_id | contact_id | operator | contract_number |
          segment | transcript
        """
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return 0
        header = [str(h).strip().lower() if h else "" for h in rows[0]]
        idx = {name: header.index(name) for name in header if name}

        def _get(row, key, default=""):
            i = idx.get(key)
            if i is None or i >= len(row):
                return default
            return row[i] if row[i] is not None else default

        contracts: list[Contract] = []
        for row in rows[1:]:
            try:
                contract_number = str(_get(row, "contract_number") or _get(row, "contrato") or "")
                if not contract_number:
                    continue
                dt_raw = _get(row, "datetime") or _get(row, "data")
                if isinstance(dt_raw, datetime):
                    contact_at = dt_raw
                else:
                    try:
                        contact_at = datetime.strptime(str(dt_raw), "%d/%m/%Y %H:%M:%S")
                    except (ValueError, TypeError):
                        contact_at = datetime.utcnow()
                seg_raw = str(_get(row, "segment") or "RESIDENCIAL").upper()
                segment = CustomerSegment(seg_raw) if seg_raw in CustomerSegment._value2member_map_ else CustomerSegment.residential
                contracts.append(
                    Contract(
                        contract_number=contract_number,
                        call_id=str(_get(row, "call_id") or ""),
                        contact_id=str(_get(row, "contact_id") or ""),
                        operator=str(_get(row, "operator") or ""),
                        contact_at=contact_at,
                        segment=segment,
                        transcript=str(_get(row, "transcript") or ""),
                    )
                )
            except Exception:
                continue
        return await self.contracts.bulk_upsert(contracts)

    # ---------- listing ----------

    async def list_contracts(self, limit: int = 200):
        return await self.contracts.list_recent(limit=limit)

    async def get_contract(self, contract_number: str):
        return await self.contracts.get(contract_number)

    async def list_cards(self, contract_number: str):
        return await self.analyses.list_for_contract(contract_number)

    # ---------- create card ----------

    async def create_analysis_card(
        self,
        contract_number: str,
        name: str,
        prompt_text: str,
        output_type: OutputType,
        expected_size: str = "",
        user_id=None,
    ) -> AnalysisCard:
        contract = await self.contracts.get(contract_number)
        if not contract:
            raise ValueError(f"Contrato {contract_number} não encontrado.")

        guard_in = self.input_guard.check(prompt_text)
        if not guard_in.ok:
            raise ValueError(f"Guardrail de entrada bloqueou: {guard_in.reason}")

        hint = _OUTPUT_HINTS.get(output_type, "")
        system = (
            "Você é um analista de Voz do Cliente para uma operadora de telecom. "
            "Responda em PT-BR, com fidelidade ao trecho transcrito. "
            f"{hint} {('Tamanho esperado: ' + expected_size) if expected_size else ''}"
        ).strip()

        user_msg = f"""Análise solicitada: {name}

Instrução do usuário:
{guard_in.sanitized}

Transcrição (parcial):
\"\"\"{contract.transcript[:6000]}\"\"\"
"""

        llm = await self.router.complete(
            system_prompt=system,
            user_prompt=user_msg,
            output_type=output_type.value,
        )

        guard_out = self.output_guard.check(llm.text, expected_format=output_type.value)
        result_text = guard_out.sanitized if guard_out.ok else "[bloqueado pelo guardrail de saída]"

        card = AnalysisCard(
            id=new_uuid(),
            contract_number=contract_number,
            name=name,
            output_type=output_type,
            prompt_text=prompt_text,
            expected_size=expected_size,
            model_used=llm.model,
            result=result_text,
            tokens_input=llm.tokens_input,
            tokens_output=llm.tokens_output,
            cost_estimated=llm.cost_estimated,
        )
        saved = await self.analyses.save(card)

        await FinOpsService(self.finops).record(
            user_id=user_id,
            module_id=None,
            model_name=llm.model,
            tokens_input=llm.tokens_input,
            tokens_output=llm.tokens_output,
            cost_estimated=llm.cost_estimated,
            context_tag=f"radar/{name}",
        )
        return saved

    async def delete_card(self, card_id) -> None:
        await self.analyses.delete(card_id)

    # ---------- run module on transcript ----------

    async def run_module_on_text(
        self,
        transcript_text: str,
        module,
        skill_content: str | None,
        prompt,
        user_id=None,
        input_label: str = "transcription_text",
        username: str | None = None,
        case_number: str | None = None,
        transaction_id: str | None = None,
        feature: str | None = None,
    ) -> dict:
        """Aplica um módulo (skill + prompt) a um texto livre de transcrição."""
        from app.core.services.artifact_store import get_artifact_store
        from app.core.services.skill_service import SkillService

        guard_in = self.input_guard.check(transcript_text or "")
        sanitized_transcript = guard_in.sanitized if guard_in.ok else (transcript_text or "")

        # detecta formato declarado na skill
        output_meta = SkillService.detect_output_format(skill_content)

        # detecta cedo se módulo precisa de JSON estruturado (table/api)
        # — afeta system_prompt (reforço) e max_tokens (espaço pra JSON inteiro)
        early_rtype = getattr(module, "response_type", "text") or "text"
        is_structured = early_rtype in ("api", "table")

        system_parts = []
        if skill_content:
            system_parts.append(f"# Skill Contract\n{skill_content}")
        if prompt and prompt.system_prompt:
            system_parts.append(prompt.system_prompt)
        else:
            system_parts.append(
                f"Você é o módulo '{module.name}'. Analise o conteúdo em PT-BR "
                "respeitando a descrição do módulo e a skill carregada."
            )
        if prompt and prompt.input_guardrail:
            system_parts.append(f"# Guardrail de entrada\n{prompt.input_guardrail}")
        if prompt and prompt.output_guardrail:
            system_parts.append(f"# Guardrail de saída\n{prompt.output_guardrail}")

        # Knowledge Base — quando o módulo tem KB associada, busca os chunks
        # mais relevantes para o texto de entrada e injeta como contexto. A
        # query do retrieval é a própria transcrição (já sanitizada); o
        # embedder produz um vetor que captura o tema geral, e o retrieval
        # devolve trechos de documentos da KB relevantes para o assunto.
        # Failure-mode: erro no retrieval (KB vazia, embeddings em mock,
        # extensão pgvector ausente) NÃO bloqueia a execução do módulo —
        # apenas pula a injeção e loga.
        kb_id = getattr(module, "knowledge_base_id", None)
        if kb_id:
            try:
                from app.core.services.knowledge_service import KnowledgeService
                kb_svc = KnowledgeService()
                # Top-3 com cap de 6000 chars para não roubar muito espaço do
                # input (transcrição) no contexto. Ajustável via config_params
                # do módulo se quiser tunar caso a caso.
                kb_top_k = 3
                kb_max_chars = 6000
                try:
                    if isinstance(module.config_params, dict):
                        kb_top_k = int(module.config_params.get("kb_top_k") or 3)
                        kb_max_chars = int(module.config_params.get("kb_max_chars") or 6000)
                except Exception:
                    pass
                kb_context = await kb_svc.build_context(
                    kb_id,
                    sanitized_transcript[:2000],  # query do retrieval
                    top_k=kb_top_k,
                    max_chars=kb_max_chars,
                )
                if kb_context:
                    system_parts.append(
                        "# Base de Conhecimento (contexto recuperado)\n"
                        "Os trechos abaixo foram recuperados da base de conhecimento "
                        "associada a este módulo. Use-os como referência primária. "
                        "Se a informação necessária não estiver neles, indique a "
                        "lacuna explicitamente em vez de inventar.\n\n"
                        f"{kb_context}"
                    )
            except Exception as e:  # noqa: BLE001
                import logging
                logging.getLogger("vertice.knowledge").warning(
                    "kb retrieval falhou para módulo %s: %s", module.name, e
                )

        # instrução de formatação adaptada ao formato detectado
        # OVERRIDE: response_type='table' ou 'api' SEMPRE exigem JSON estrito,
        # independente do que a skill declarou
        fmt = output_meta["format"]
        if is_structured:
            fmt = "json"  # força JSON para o despacho funcionar
            system_parts.append(
                "# Formato de saída — OBRIGATÓRIO JSON ESTRITO\n"
                f"Este módulo é response_type='{early_rtype}', portanto a resposta DEVE ser JSON válido e completo. "
                "REGRAS INVIOLÁVEIS:\n"
                "1. A resposta começa com `{` e termina com `}` (objeto JSON único — sem array no nível raiz).\n"
                "2. Sem markdown, sem ```json, sem texto antes/depois.\n"
                "3. Strings entre aspas duplas; valores ausentes usam `null` (nunca string vazia ou 'N/A').\n"
                "4. Sem trailing commas. Sem comentários.\n"
                "5. Se faltar espaço, PRIORIZE menos campos com JSON COMPLETO em vez de muitos campos truncados — "
                "JSON incompleto causa falha do módulo.\n"
                "6. Aninhe quanto precisar; objetos serão achatados em dot notation no banco."
            )
        elif fmt == "json":
            system_parts.append(
                "# Formato de saída\n"
                "Devolva APENAS o JSON estrito declarado na skill. "
                "Sem markdown, sem comentários, sem texto antes ou depois. "
                "A primeira char deve ser `{` ou `[`."
            )
        elif fmt == "csv":
            system_parts.append(
                "# Formato de saída\n"
                "Devolva APENAS o CSV (UTF-8, separador vírgula, "
                "primeira linha com cabeçalho). Sem markdown."
            )
        elif fmt == "html":
            system_parts.append(
                "# Formato de saída\n"
                "Devolva APENAS o HTML (sem ```html ao redor)."
            )
        elif fmt == "xml":
            system_parts.append(
                "# Formato de saída\n"
                "Devolva APENAS o XML válido."
            )
        else:
            system_parts.append(
                "# Formato de saída\n"
                "Estruture sua resposta em **Markdown** legível (títulos, listas, "
                "destaques, tabelas quando comparar, blockquotes para citações). "
                "Não envolva tudo em um único bloco de código."
            )
        system = "\n\n".join(system_parts)

        # Tipo de saída do guardrail: vem do `config_params.output_type` do
        # módulo quando response_type='text'. Default 'SUMARIO' por compat.
        # Para api/table sempre 'JSON' (extração estrita do bloco JSON).
        # Cada tipo tem cap próprio no guardrail (LIVRE = sem corte).
        # Resolvido ANTES do truncate de input pra que o cap de chars do
        # input respeite a promessa do output_type — em particular, LIVRE
        # ("sem corte · todo o texto preservado" no UI) recebe cap alto.
        configured_output = "SUMARIO"
        try:
            if isinstance(module.config_params, dict):
                ot = (module.config_params.get("output_type") or "").strip().upper()
                if ot:
                    configured_output = ot
        except Exception:
            pass

        # max_tokens proporcional ao cap do output_type para não desperdiçar
        # tokens (UMA_PALAVRA não precisa de 800) nem cortar respostas longas
        # (RELATORIO/LIVRE precisam de mais espaço).
        TOKEN_BUDGETS = {
            "UMA_PALAVRA": 50,
            "SCORE":       30,
            "TERMOS":     200,
            "INTENCAO":   300,
            "RESUMO":     400,
            "SUMARIO":    800,
            "ANALISE":   1800,
            "RELATORIO": 3500,
            "LIVRE":     4096,
            "JSON":      4096,
        }
        text_max_tokens = TOKEN_BUDGETS.get(configured_output, 800)

        # Cap de chars do INPUT, em função do output_type escolhido no módulo.
        # Casos a respeitar:
        #   - LIVRE: UI promete "sem corte · todo o texto preservado". Cap
        #            alto (50000) — cobre 99% das transcrições reais sem
        #            cortar. Em ligações extremamente longas (>50k chars),
        #            ainda há head+tail pra evitar overflow do contexto.
        #            50000 chars ≈ 12500 tokens, deixa folga pra system_prompt
        #            + 4096 output dentro do contexto sabia-4 (32k).
        #   - JSON:  outputs estruturados não precisam do final inteiro pra
        #            decidir uma classificação — 12000 chega.
        #   - demais: 12000 default (~3000 tokens), proporcional ao tamanho
        #            do output esperado.
        INPUT_CHAR_BUDGETS = {
            "LIVRE":      50000,
            "RELATORIO":  20000,  # análises longas se beneficiam de mais contexto
            "ANALISE":    15000,
        }
        input_max_chars = INPUT_CHAR_BUDGETS.get(configured_output, 12000)

        # Antes: hard-cap em 6000 chars do início. Para transcrições longas
        # (>6000 chars, frequentes em ligações de atendimento) isso descartava
        # o FINAL — justamente onde fica o desfecho, oferta de solução,
        # aceite/recusa. _smart_truncate_transcript preserva início + fim
        # com marker de omissão no meio. Tamanho do cap depende do
        # `output_type` (ver INPUT_CHAR_BUDGETS acima).
        truncated_for_llm = _smart_truncate_transcript(
            sanitized_transcript, max_chars=input_max_chars
        )
        user_msg = (
            f"Módulo: {module.name}\n"
            f"Descrição: {module.description}\n"
            f"Campo de entrada: {input_label}\n\n"
            f"Conteúdo:\n\"\"\"{truncated_for_llm}\"\"\""
        )

        guardrail_format = "JSON" if is_structured else configured_output

        # Temperature configurável por módulo via `config_params.temperature`.
        # Sem o campo: usa 0.2 (default histórico do router). Caller pode
        # forçar determinismo (0.0) para módulos cuja inconsistência entre
        # execuções é problemática — ex.: o módulo `radar` (Voz do Cliente),
        # onde a mesma transcrição gerava respostas de tamanho/estrutura
        # diferentes entre auto-execute (troca de caso) e re-executar manual.
        # Aceita 0.0 a 1.0; valores fora viram default seguro.
        cfg_temp = None
        try:
            if isinstance(module.config_params, dict):
                raw = module.config_params.get("temperature")
                if isinstance(raw, (int, float)) and 0.0 <= float(raw) <= 1.0:
                    cfg_temp = float(raw)
        except Exception:
            pass
        temperature = cfg_temp if cfg_temp is not None else 0.2

        llm = await self.router.complete(
            system_prompt=system,
            user_prompt=user_msg,
            output_type=configured_output if not is_structured else "SUMARIO",
            max_tokens=4096 if is_structured else text_max_tokens,
            temperature=temperature,
            # JSON mode da OpenAI/Maritaca garante saída sintaticamente válida
            force_json=is_structured,
        )
        guard_out = self.output_guard.check(llm.text, expected_format=guardrail_format)
        result_text = guard_out.sanitized if guard_out.ok else "[bloqueado pelo guardrail de saída]"

        # gera artefato se a skill exige formato downloadable
        artifact_id = None
        artifact_filename = None
        if output_meta["is_downloadable"] and result_text and not result_text.startswith("["):
            cleaned = _strip_codefence(result_text, output_meta["format"])
            store = get_artifact_store()
            ts = int(time.time())
            filename = f"{module.name}_{ts}.{output_meta['file_extension']}"
            art = await store.put(
                content=cleaned,
                filename=filename,
                mime_type=output_meta["mime_type"],
            )
            artifact_id = art.id
            artifact_filename = art.filename

        await FinOpsService(self.finops).record(
            user_id=user_id,
            module_id=module.id,
            model_name=llm.model,
            tokens_input=llm.tokens_input,
            tokens_output=llm.tokens_output,
            cost_estimated=llm.cost_estimated,
            context_tag=f"{module.name}/run/{input_label}",
        )

        # ===== Despacho por response_type =====
        # 'text' (default): apenas devolve o markdown/json/etc — fluxo padrão
        # 'api':  envia o JSON estruturado para um endpoint externo configurado
        # 'table': persiste o JSON estruturado numa tabela dinâmica do banco
        response_action = None
        rtype = getattr(module, "response_type", "text") or "text"
        rconfig = getattr(module, "response_config", {}) or {}
        if rtype in ("api", "table") and result_text and not result_text.startswith("["):
            try:
                # extrai o JSON estruturado da resposta
                raw_json = _strip_codefence(result_text, "json")
                # parse robusto com 4 estratégias em cascata:
                # 1) parse direto
                # 2) fatiar entre {} e parsear
                # 3) reparar JSON truncado (fechar chaves/colchetes abertos)
                # 4) remover trailing comma antes de fechar
                structured = _resilient_json_parse(raw_json)
                if structured is None:
                    raise json.JSONDecodeError("não foi possível reparar o JSON", raw_json, 0)

                if rtype == "api":
                    from app.core.services.api_endpoint_service import get_api_endpoint_service
                    api_svc = get_api_endpoint_service()
                    endpoint_id = rconfig.get("api_endpoint_id")
                    if not endpoint_id:
                        response_action = {"kind": "api", "ok": False,
                                           "error": "api_endpoint_id não configurado no módulo"}
                    else:
                        endpoint = await api_svc.get(endpoint_id)
                        if not endpoint:
                            response_action = {"kind": "api", "ok": False,
                                               "error": f"endpoint {endpoint_id} não encontrado"}
                        elif not endpoint.is_active:
                            response_action = {"kind": "api", "ok": False,
                                               "error": f"endpoint '{endpoint.name}' está inativo"}
                        else:
                            # POST body {"input": <json estruturado>} conforme spec
                            api_result = await api_svc.call(
                                endpoint=endpoint,
                                body={"input": structured},
                                module_id=str(module.id),
                                user_id=str(user_id) if user_id else None,
                            )
                            response_action = {
                                "kind": "api",
                                "ok": api_result["ok"],
                                "endpoint_name": endpoint.name,
                                "endpoint_url": endpoint.url,
                                "status": api_result["status"],
                                "duration_ms": api_result["duration_ms"],
                                "response_body": api_result["body"],
                                "error": api_result["error"],
                                "call_id": api_result["call_id"],
                            }

                elif rtype == "table":
                    from app.core.services.dynamic_table_service import get_dynamic_table_service
                    dt_svc = get_dynamic_table_service()
                    eff_feature = feature or rconfig.get("feature") or "default"
                    table_name = dt_svc.table_name(module.name, eff_feature)
                    row_id = await dt_svc.insert(
                        table=table_name,
                        data=structured,
                        user_id=str(user_id) if user_id else None,
                        username=username,
                        case_number=case_number,
                        transaction_id=transaction_id,
                        feature=eff_feature,
                    )
                    response_action = {
                        "kind": "table",
                        "ok": True,
                        "table": table_name,
                        "row_id": row_id,
                        "fields_persisted": len(structured) if isinstance(structured, dict) else 0,
                    }
            except json.JSONDecodeError as e:
                response_action = {"kind": rtype, "ok": False,
                                   "error": f"resposta do LLM não é JSON válido: {e}. "
                                            "Ajuste a skill para devolver JSON estrito."}
            except Exception as e:  # noqa: BLE001
                response_action = {"kind": rtype, "ok": False,
                                   "error": f"{type(e).__name__}: {e}"}

        return {
            "module_id": str(module.id),
            "module_name": module.name,
            "module_description": module.description or "",
            "result": result_text,
            "model_used": llm.model,
            "tokens_input": llm.tokens_input,
            "tokens_output": llm.tokens_output,
            "cost_estimated": llm.cost_estimated,
            "prompt_used": (prompt.name if prompt else None),
            "prompt_version": (prompt.version if prompt else None),
            "skill_used": bool(skill_content),
            "output_format": output_meta["format"],
            "artifact_id": artifact_id,
            "artifact_filename": artifact_filename,
            "response_type": rtype,
            "response_action": response_action,
        }

    async def run_module_on_transcript(
        self,
        contract_number: str,
        module,
        skill_content: str | None,
        prompt,
        user_id=None,
    ) -> dict:
        """Wrapper legado: busca contrato em `contracts` e chama run_module_on_text."""
        contract = await self.contracts.get(contract_number)
        if not contract:
            raise ValueError(f"Contrato {contract_number} não encontrado.")
        return await self.run_module_on_text(
            transcript_text=contract.transcript or "",
            module=module,
            skill_content=skill_content,
            prompt=prompt,
            user_id=user_id,
        )


_CODEFENCE_RE = re.compile(r"^```(?:[a-zA-Z]+)?\n(.*?)\n```\s*$", re.DOTALL)


def _strip_codefence(text: str, fmt: str) -> str:
    """Remove ```json ... ``` (ou similar) que o LLM possa ter adicionado em volta."""
    if not text:
        return text
    t = text.strip()
    m = _CODEFENCE_RE.match(t)
    if m:
        return m.group(1).strip()
    # se o formato é JSON e o texto não começa com { ou [, tenta extrair primeiro bloco JSON
    if fmt == "json":
        first = t.find("{")
        last = t.rfind("}")
        if first >= 0 and last > first:
            return t[first:last + 1]
        first = t.find("[")
        last = t.rfind("]")
        if first >= 0 and last > first:
            return t[first:last + 1]
    return t


def _resilient_json_parse(text: str) -> dict | list | None:
    """Parse JSON com 4 estratégias em cascata para responder a JSONs imperfeitos do LLM.

    1. Parse direto.
    2. Fatiar entre {} (ou []) e parsear.
    3. Remover trailing commas antes de }/].
    4. Reparar JSON truncado: fechar strings abertas, fechar chaves/colchetes pendentes.

    Retorna None se nenhuma estratégia funcionar.
    """
    if not text:
        return None

    # 1) parse direto
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2) fatiar entre primeiro { e último } (ou [ ])
    candidates = []
    for opener, closer in [("{", "}"), ("[", "]")]:
        first = text.find(opener)
        last = text.rfind(closer)
        if first >= 0 and last > first:
            candidates.append(text[first:last + 1])

    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            pass

    # 3) remover trailing commas antes de } ou ]
    target = candidates[0] if candidates else text
    no_trailing = re.sub(r",\s*([}\]])", r"\1", target)
    if no_trailing != target:
        try:
            return json.loads(no_trailing)
        except json.JSONDecodeError:
            pass

    # 4) reparar JSON truncado: fechar strings/chaves/colchetes pendentes
    repaired = _repair_truncated_json(no_trailing)
    if repaired is not None:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    return None


def _repair_truncated_json(text: str) -> str | None:
    """Tenta reparar JSON truncado pela meio (caso típico de max_tokens cortando).

    Estratégia:
    - Percorre o texto rastreando profundidade de {}, [] e se está dentro de string
    - Se acaba dentro de string, descarta a chave/valor pendente até o último ',' ou '{'
    - Fecha colchetes/chaves pendentes na ordem certa
    """
    if not text:
        return None
    depth_stack: list[str] = []   # stack de '{' e '['
    in_string = False
    escape = False
    last_safe_pos = 0             # última posição "segura" (após `,` fora de string)

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth_stack.append("{")
        elif ch == "[":
            depth_stack.append("[")
        elif ch == "}":
            if depth_stack and depth_stack[-1] == "{":
                depth_stack.pop()
        elif ch == "]":
            if depth_stack and depth_stack[-1] == "[":
                depth_stack.pop()
        elif ch == "," and len(depth_stack) > 0:
            # vírgula fora de string = fim de elemento — última posição segura
            last_safe_pos = i

    if not depth_stack and not in_string:
        return text  # texto já está balanceado

    # se acabou no meio de uma string, ou no meio de um valor, recua até `last_safe_pos`
    if in_string or last_safe_pos > 0:
        # corta no último vírgula seguro
        truncated = text[:last_safe_pos] if last_safe_pos > 0 else text
        # recalcula depth_stack para a posição truncada
        depth_stack = []
        in_string = False
        escape = False
        for ch in truncated:
            if escape:
                escape = False; continue
            if ch == "\\" and in_string:
                escape = True; continue
            if ch == '"':
                in_string = not in_string; continue
            if in_string:
                continue
            if ch == "{":
                depth_stack.append("{")
            elif ch == "[":
                depth_stack.append("[")
            elif ch == "}":
                if depth_stack and depth_stack[-1] == "{":
                    depth_stack.pop()
            elif ch == "]":
                if depth_stack and depth_stack[-1] == "[":
                    depth_stack.pop()
        text = truncated

    # fecha o que ficou aberto, na ordem reversa
    closing = []
    for opener in reversed(depth_stack):
        closing.append("]" if opener == "[" else "}")

    return text.rstrip(", \n\t") + "".join(closing) if closing else text
