# Vértice — Framework de Building Blocks de IA

Plataforma modular de agentes em Python/FastAPI seguindo Spec-Driven Development e arquitetura hexagonal. Cada funcionalidade é um *building block* registrado dinamicamente, com guardrails parametrizáveis (entrada → system prompt → saída), FinOps granular e observabilidade nativa.

> Versão 1.0.0 · Hexagonal · Python 3.11+ · FastAPI · SQLite · LangGraph · Deep-Agent Harness

---

## 1. Princípios

- **Modularidade.** Building blocks independentes e reutilizáveis.
- **Segurança por design.** Auth robusta, OPA, sanitização contra prompt injection.
- **Transparência de custo.** FinOps ledger por chamada, modelo, módulo e tag.
- **Observabilidade nativa.** OpenTelemetry + LangFuse + MLflow.
- **SKILL.md como contrato executável.** A "alma semântica" de cada agente.

## 2. Arquitetura

```
┌─────────────────────────────────────────────────────────┐
│  Inbound adapters (FastAPI · Jinja2 · HTMX · Alpine)    │
├─────────────────────────────────────────────────────────┤
│  Core domain (entidades + ports + use cases)            │
├─────────────────────────────────────────────────────────┤
│  Outbound adapters (SQLite · LLMs · LangFuse · OPA)     │
└─────────────────────────────────────────────────────────┘
```

Tudo que cruza a fronteira do core passa por uma porta. Adaptadores são plugáveis: trocar SQLite por Postgres, LangFuse por outro tracer, OpenAI por Sabiá-4, exige só substituir a implementação do adaptador — o core não muda.

## 3. Stack

| Camada | Tecnologia |
|---|---|
| Web | FastAPI, Jinja2, HTMX, Alpine.js, Tailwind (CDN) |
| Persistência | SQLite (aiosqlite) |
| LLMs | OpenAI GPT-4.1, Maritaca Sabiá-4, Gemma GAIA 4Bi |
| Orquestração de agentes | LangGraph, Deep-Agent Harness |
| Observabilidade | OpenTelemetry, LangFuse, MLflow |
| Política | OPA (Open Policy Agent) |
| Auth | OAuth2 + JWT, bcrypt + salt |

## 4. Quickstart

```bash
git clone https://github.com/sergiogaiotto/vertice.git
cd vertice
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # preencher API keys (opcional para dev)
python scripts/init_db.py     # cria SQLite + seed
uvicorn app.main:app --reload
```

Acesse `http://localhost:8000`.
Login default: `admin / vertice2026` (trocar imediatamente em produção).

Sem API keys configuradas, os adaptadores LLM rodam em **modo mock** — todas as funcionalidades da plataforma ficam navegáveis para desenvolvimento offline.

## 5. Estrutura

```
app/
├── main.py                 entrypoint FastAPI
├── config.py               settings via pydantic-settings
├── core/
│   ├── domain/             entidades puras (sem dependência externa)
│   ├── ports/              interfaces que o core consome
│   └── services/           use cases (regras de negócio)
├── adapters/
│   ├── db/                 SQLite + repositórios
│   ├── llm/                OpenAI, Maritaca, GAIA + roteador
│   ├── guardrails/         input sanitizer + output validator
│   ├── observability/      LangFuse, MLflow, OTel
│   └── policy/             OPA
├── api/
│   ├── routers/            HTTP routes + páginas
│   └── schemas/            contratos Pydantic (Standard Module Contract)
├── skills/                 SKILL.md — contratos declarativos por agente
├── templates/              Jinja2 (UI 3 colunas)
└── static/                 CSS + JS (command palette, canvas)
```

## 6. Módulos incluídos

| Módulo | Descrição |
|---|---|
| `radar` | Voz do Cliente — upload Excel → seleção de contrato → transcrição → cards de análise via prompt |
| `churn` | Taxonomia hierárquica (motivo → submotivo → sub-submotivo) com classificador |
| `prompts` | Editor centralizado guardrail→system→guardrail por módulo, versionado |
| `finops` | Ledger e cockpit de custos por modelo/módulo/usuário/tag |
| `failsafe` | Inbox de aprovações human-in-the-loop |
| `modules` | Registry para descoberta dinâmica |

## 7. Standard Module Contract

Todo módulo expõe `POST /v1/process` seguindo o contrato OpenAPI em `app/api/schemas/standard.py`. Isso garante interoperabilidade no Spec-Driven Development e permite que módulos sejam compostos em pipelines.

## 8. SKILL.md

Cada agente é definido por um `SKILL.md` em `app/skills/`. O arquivo é o **contrato executável**: o System Prompt Canônico carrega o SKILL.md em runtime e o usa para decidir quais ferramentas invocar, em que ordem, sob quais condições e com qual contrato de saída. Veja `app/skills/README.md`.

## 9. UI

Layout de três colunas (referência: `https://agente-inteligencia.onrender.com`):

- **Esquerda** — navegação por módulos, colapsável.
- **Centro** — operação do módulo ativo (canvas de cards, taxonomia, editor).
- **Direita** — contexto detalhado do item selecionado.
- **Topo** — Command Palette (⌘K) para invocar qualquer ação por linguagem natural.
- **Rodapé** — Cost Pulse Bar com decomposição em tempo real.

## 10. Deploy

```bash
docker build -t vertice:1.0.0 .
docker run -p 8000:8000 --env-file .env vertice:1.0.0
```

Para Kubernetes (AI Mesh), aplique os manifestos em `deploy/k8s/` (não incluído nesta versão; usar Helm chart genérico de FastAPI + Istio sidecar para mTLS).

## 11. Testes

```bash
pytest -q
```

## 12. Licença

MIT.
