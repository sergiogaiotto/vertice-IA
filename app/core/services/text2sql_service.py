"""Use case: Text-to-SQL via Deep Agents.

Cria agentes Deep Agents (LangGraph + LangChain OpenAI) com escopo de
tabelas restrito, executa perguntas em linguagem natural e devolve resposta
estruturada (understanding + SQL + tabela de resultados + sugestões).

Padrão segue o exemplo oficial deepagents/examples/text-to-sql-agent.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any


# Tabelas do schema da Vértice que NUNCA devem ser expostas via Text-to-SQL
# (seguem a mesma lista do schema_service para coerência semântica)
_HIDDEN_TABLES = {
    "users", "roles", "permissions", "user_roles", "role_permissions",
    "audit_events", "api_endpoints", "api_calls", "presentations",
    "finops_ledger", "failsafe_actions",
    # SQLite internals
    "sqlite_master", "sqlite_sequence", "sqlite_temp_master",
}


def _validate_table_names(tables: list[str], db) -> list[str]:
    """Retorna apenas tabelas que existem E são permitidas."""
    from sqlalchemy import inspect
    inspector = inspect(db._engine)
    real_tables = set(inspector.get_table_names())
    return [t for t in tables if t in real_tables and t not in _HIDDEN_TABLES]


@dataclass
class SqlAgentResult:
    understanding: str
    sql: str
    result_columns: list[str]
    result_rows: list[list]
    row_count: int
    analyses: list[str]
    raw_text: str            # JSON cru devolvido pelo LLM
    model_used: str
    tokens_input: int
    tokens_output: int
    cost_estimated: float
    error: str | None = None


class Text2SqlService:

    def __init__(self):
        # text2sql/ está em app/text2sql; este service está em app/core/services/
        self._base_dir = os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "text2sql",
        ))
        self._db_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "vertice.db")
        )

    def _build_db(self, allowed_tables: list[str]):
        """Cria SQLDatabase do langchain-community restrita às tabelas permitidas."""
        from langchain_community.utilities import SQLDatabase

        db = SQLDatabase.from_uri(
            f"sqlite:///{self._db_path}",
            include_tables=allowed_tables,
            sample_rows_in_table_info=3,
        )
        return db

    def _build_agent(self, allowed_tables: list[str], model_name: str | None = None):
        """Cria o Deep Agent com SQL toolkit restrito + skills + AGENTS.md.

        Lê OPENAI_API_KEY do .env (via settings). Se ausente, lança ValueError
        com mensagem clara em vez do erro críptico do SDK.
        """
        from deepagents import create_deep_agent
        from deepagents.backends import FilesystemBackend
        from langchain_openai import ChatOpenAI
        from langchain_community.agent_toolkits import SQLDatabaseToolkit

        from app.config import get_settings
        settings = get_settings()

        api_key = settings.openai_api_key
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY não configurada. "
                "Adicione no arquivo .env (na raiz do projeto): "
                "OPENAI_API_KEY=sk-... "
                "Obtenha em https://platform.openai.com/api-keys"
            )

        model_name = model_name or settings.openai_model

        db = self._build_db(allowed_tables)

        # Force temperature=0 para SQL determinístico
        model = ChatOpenAI(
            model=model_name,
            temperature=0,
            max_tokens=4096,
            api_key=api_key,
        )

        toolkit = SQLDatabaseToolkit(db=db, llm=model)
        sql_tools = toolkit.get_tools()

        # Bug do deepagents: usa PurePosixPath em paths Windows, e PurePosixPath('C:\\…\\skills\\name').name
        # devolve o caminho INTEIRO em vez do último componente. Isso quebra o
        # _validate_skill_name e gera warning falso "name must match directory name '<path>'".
        # Workaround: passamos paths POSIX (forward slashes), assim PurePosixPath funciona
        # corretamente independente do SO.
        agents_md = os.path.join(self._base_dir, "AGENTS.md").replace("\\", "/")
        skills_dir = (os.path.join(self._base_dir, "skills") + "/").replace("\\", "/")

        agent = create_deep_agent(
            model=model,
            memory=[agents_md],
            skills=[skills_dir],
            tools=sql_tools,
            subagents=[],
            backend=FilesystemBackend(root_dir=self._base_dir.replace("\\", "/")),
        )
        return agent

    def _extract_json_response(self, text: str) -> dict | None:
        """Robust parser: tenta direto, depois extrai entre primeiro { e último }."""
        if not text:
            return None
        text = text.strip()
        # remove ```json ... ``` se houver
        if text.startswith("```"):
            lines = text.split("\n")
            if lines and lines[-1].strip().startswith("```"):
                text = "\n".join(lines[1:-1]).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # tenta fatiar entre { e }
        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last > first:
            try:
                return json.loads(text[first:last + 1])
            except json.JSONDecodeError:
                pass
        return None

    async def list_available_tables(self, feature: str = "radar") -> list[dict]:
        """Lista tabelas disponíveis para Text-to-SQL no escopo da feature.

        Reaproveita o SchemaService da plataforma (tem heurísticas por feature).
        """
        from app.core.services.schema_service import SchemaService
        svc = SchemaService()
        all_tables = await svc.list_tables(feature=feature)
        # filtra hidden adicional
        return [t for t in all_tables if t["name"] not in _HIDDEN_TABLES]

    async def ask(
        self,
        question: str,
        allowed_tables: list[str],
        history: list[dict] | None = None,
        feature: str = "radar",
        user_id: str | None = None,
        case_number: str = "",
        username: str = "",
    ) -> SqlAgentResult:
        """Roda o Deep Agent para uma pergunta com contexto de conversa.

        Histórico é uma lista [{role: 'user'|'assistant', content: str}, ...]
        que é prepended ao prompt como contexto.

        case_number/username: filtros de contexto que o agente é instruído a
        aplicar como WHERE obrigatório (caso/transcript posicionado em tela
        e usuário logado). Se vazios, nenhuma restrição extra é injetada.
        """
        from app.config import get_settings
        model_used_str = get_settings().openai_model

        if not question or not question.strip():
            raise ValueError("pergunta vazia")
        if not allowed_tables:
            raise ValueError("nenhuma tabela autorizada")

        # validação extra: confirma que tabelas existem
        from sqlalchemy import create_engine, inspect
        engine = create_engine(f"sqlite:///{self._db_path}")
        try:
            inspector = inspect(engine)
            real = set(inspector.get_table_names())
            valid = [t for t in allowed_tables if t in real and t not in _HIDDEN_TABLES]
        finally:
            engine.dispose()

        if not valid:
            raise ValueError(
                f"nenhuma das tabelas selecionadas é válida: {allowed_tables}"
            )

        # monta prompt com escopo + histórico + pergunta atual
        scope_section = "# Escopo autorizado\n\nVocê SÓ pode consultar estas tabelas:\n" + \
                        "\n".join(f"- `{t}`" for t in valid)

        # ----- Filtros de CONTEXTO -----
        # As tabelas dinâmicas (geradas por módulos response_type='table') sempre
        # têm as colunas de auditoria _case_number e _username. As tabelas
        # estáticas do Radar têm equivalentes: bko_cases.case_number,
        # transcripts.verint_nr_contrato (= case), contracts.contract_number.
        # Pedimos ao agente para aplicar WHERE quando essas colunas existirem.
        ctx_clauses: list[str] = []
        case_clean = (case_number or "").strip()
        user_clean = (username or "").strip()
        if case_clean:
            ctx_clauses.append(
                f"- O usuário está posicionado no caso/registro `{case_clean}`. "
                f"SEMPRE filtre os resultados por esse caso quando a tabela "
                f"tiver uma coluna que o identifique. Convenções por tabela:\n"
                f"  - dinâmicas (`*__radar`): WHERE `_case_number` = '{case_clean}'\n"
                f"  - `bko_cases`: WHERE `case_number` = '{case_clean}'\n"
                f"  - `transcripts`: WHERE `verint_nr_contrato` = '{case_clean}'\n"
                f"  - `contracts`: WHERE `contract_number` = '{case_clean}'\n"
                f"  - `analysis_cards`: WHERE `contract_number` = '{case_clean}'\n"
                f"Se a tabela não tiver coluna correspondente, NÃO use a tabela."
            )
        if user_clean:
            ctx_clauses.append(
                f"- O usuário logado é `{user_clean}`. Para tabelas dinâmicas "
                f"(`*__radar`) com a coluna `_username`, adicione "
                f"`AND _username = '{user_clean}'`. Não filtre por usuário em "
                f"tabelas estáticas (bko_cases, transcripts, contracts, "
                f"analysis_cards) — elas não têm essa coluna."
            )
        context_section = ""
        if ctx_clauses:
            context_section = (
                "# Filtros de contexto OBRIGATÓRIOS\n\n"
                "Aplique estas restrições no SQL gerado (são imutáveis):\n\n"
                + "\n\n".join(ctx_clauses)
                + "\n\n"
            )

        history_section = ""
        if history:
            lines = ["# Histórico da conversa (para contexto/refinamento)\n"]
            for turn in history[-6:]:  # últimos 6 turnos para não estourar contexto
                role = "USUÁRIO" if turn.get("role") == "user" else "AGENTE"
                content = turn.get("content", "")[:1500]
                lines.append(f"## {role}\n{content}\n")
            history_section = "\n".join(lines)

        full_prompt = (
            f"{scope_section}\n\n"
            f"{context_section}"
            f"{history_section}"
            f"# Pergunta atual\n{question.strip()}\n\n"
            "Devolva APENAS o JSON estrito conforme AGENTS.md."
        )

        # roda o agente em thread (langchain é síncrono em parte do fluxo)
        loop = asyncio.get_event_loop()
        agent = self._build_agent(valid)

        def _run():
            return agent.invoke({
                "messages": [{"role": "user", "content": full_prompt}],
            })

        try:
            result = await loop.run_in_executor(None, _run)
        except Exception as e:
            return SqlAgentResult(
                understanding=f"Falha ao executar o agente: {type(e).__name__}",
                sql="",
                result_columns=[],
                result_rows=[],
                row_count=0,
                analyses=["Tente reformular a pergunta", "Verifique se as tabelas selecionadas têm dados"],
                raw_text="",
                model_used=model_used_str,
                tokens_input=0, tokens_output=0, cost_estimated=0.0,
                error=str(e),
            )

        # extrai mensagem final do agente
        final_msg = result["messages"][-1] if result.get("messages") else None
        raw_text = ""
        if final_msg:
            raw_text = (
                final_msg.content if hasattr(final_msg, "content")
                else str(final_msg)
            )

        # tenta extrair texto do content se for lista (alguns provedores devolvem assim)
        if isinstance(raw_text, list):
            parts = []
            for block in raw_text:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            raw_text = "\n".join(parts)

        # tenta parsear JSON
        parsed = self._extract_json_response(raw_text)

        # estima tokens/custo agregado das mensagens
        tokens_in = tokens_out = 0
        cost = 0.0
        try:
            for m in result.get("messages", []):
                meta = getattr(m, "usage_metadata", None) or getattr(m, "response_metadata", {}).get("usage", {})
                if isinstance(meta, dict):
                    tokens_in += meta.get("input_tokens", 0) or meta.get("prompt_tokens", 0) or 0
                    tokens_out += meta.get("output_tokens", 0) or meta.get("completion_tokens", 0) or 0
            # estimativa GPT-4.1: $2/M input, $8/M output
            cost = (tokens_in / 1_000_000) * 2.0 + (tokens_out / 1_000_000) * 8.0
        except Exception:
            pass

        if not parsed:
            return SqlAgentResult(
                understanding="O agente não devolveu JSON válido. Resposta bruta abaixo.",
                sql="",
                result_columns=[],
                result_rows=[],
                row_count=0,
                analyses=["Tente reformular a pergunta de modo mais específico"],
                raw_text=raw_text,
                model_used=model_used_str,
                tokens_input=tokens_in, tokens_output=tokens_out, cost_estimated=cost,
                error="JSON inválido na resposta do agente",
            )

        # normaliza resultado
        rows_raw = parsed.get("result_rows", []) or []
        # garante que rows são listas (não tuplas, não dicts)
        rows: list[list] = []
        for r in rows_raw:
            if isinstance(r, list):
                rows.append([self._safe_cell(c) for c in r])
            elif isinstance(r, dict):
                rows.append([self._safe_cell(v) for v in r.values()])
            else:
                rows.append([str(r)])

        return SqlAgentResult(
            understanding=str(parsed.get("understanding", "")).strip(),
            sql=str(parsed.get("sql", "")).strip(),
            result_columns=[str(c) for c in (parsed.get("result_columns", []) or [])],
            result_rows=rows,
            row_count=int(parsed.get("row_count", len(rows)) or len(rows)),
            analyses=[str(a) for a in (parsed.get("analyses", []) or [])][:5],
            raw_text=raw_text,
            model_used=model_used_str,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost_estimated=cost,
        )

    @staticmethod
    def _safe_cell(value) -> Any:
        """Converte valor de célula para tipo serializável JSON."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)


_global = Text2SqlService()


def get_text2sql_service() -> Text2SqlService:
    return _global


def check_dependencies() -> tuple[bool, str | None]:
    """Tenta importar TODAS as dependências do Deep Agent text-to-sql.

    Retorna (True, None) se OK, ou (False, mensagem_erro_detalhada com versões).
    Útil para diagnóstico rápido antes de o usuário enviar uma pergunta.
    """
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    # (módulo Python que vamos importar, nome do pacote pip, versão mínima esperada)
    checks = [
        ("deepagents",                        "deepagents",         "0.4.0"),
        ("langchain_openai",                  "langchain-openai",   "0.2.0"),
        ("langchain_community.utilities",     "langchain-community", "0.4.0"),
        ("langchain_community.agent_toolkits","langchain-community", "0.4.0"),
        ("langgraph",                         "langgraph",          "1.0.0"),
        ("langgraph.prebuilt.tool_node",      "langgraph-prebuilt", "1.0.0"),
        ("sqlalchemy",                        "sqlalchemy",         "2.0.0"),
    ]
    seen_pkgs: dict[str, str | None] = {}
    failures: list[str] = []
    for module_path, pkg_name, min_ver in checks:
        # versão instalada do pacote pip (cache para não repetir)
        if pkg_name not in seen_pkgs:
            try:
                seen_pkgs[pkg_name] = _pkg_version(pkg_name)
            except PackageNotFoundError:
                seen_pkgs[pkg_name] = None
        installed = seen_pkgs[pkg_name]

        try:
            __import__(module_path, fromlist=["_"])
        except ImportError as e:
            ver_info = f"instalado={installed or 'AUSENTE'}, requer>={min_ver}"
            failures.append(f"{module_path} [{pkg_name}: {ver_info}] → {e}")

    if failures:
        return False, " | ".join(failures)
    return True, None
