# Vértice — Framework de Building Blocks de IA

Plataforma modular de agentes em Python/FastAPI seguindo Spec-Driven Development e arquitetura hexagonal. Cada funcionalidade é um *building block* registrado dinamicamente, com guardrails parametrizáveis (entrada → system prompt → saída), FinOps granular e observabilidade nativa.

> Versão 2.0.0 · Hexagonal · Python 3.11+ · FastAPI · PostgreSQL (asyncpg) · LangGraph · Deep-Agent Harness

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
│  Outbound adapters (PostgreSQL · LLMs · LangFuse · OPA) │
└─────────────────────────────────────────────────────────┘
```

Tudo que cruza a fronteira do core passa por uma porta. Adaptadores são plugáveis: trocar PostgreSQL por outro RDBMS, LangFuse por outro tracer, OpenAI por Sabiá-4, exige só substituir a implementação do adaptador — o core não muda.

## 3. Stack

| Camada | Tecnologia |
|---|---|
| Web | FastAPI, Jinja2, HTMX, Alpine.js, Tailwind (CDN) |
| Persistência | PostgreSQL 14+ (asyncpg + pool) |
| LLMs | OpenAI GPT-4.1, Maritaca Sabiá-4, Gemma GAIA 4Bi |
| Orquestração de agentes | LangGraph, Deep-Agent Harness |
| Observabilidade | OpenTelemetry, LangFuse, MLflow |
| Política | OPA (Open Policy Agent) |
| Auth | OAuth2 + JWT, bcrypt + salt |

## 4. Quickstart

### Opção 1 — Docker Compose (recomendado)

```bash
git clone https://github.com/sergiogaiotto/vertice.git
cd vertice
cp .env.example .env          # preencher API keys (opcional para dev)
docker compose up --build     # sobe Postgres + app
```

O `docker-compose.yml` inicia o PostgreSQL com healthcheck e só sobe a app
quando o banco responde. O schema/seed/módulos default são aplicados pelo
`init_db()` no lifespan do FastAPI — idempotente.

### Opção 2 — Postgres local + venv

```bash
git clone https://github.com/sergiogaiotto/vertice.git
cd vertice
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# pré-requisito: PostgreSQL 14+ rodando em postgresql://vertice:vertice@localhost:5432/vertice
createdb vertice              # ou via psql / pgAdmin
python scripts/init_db.py     # cria schema + seed + bootstrap
uvicorn app.main:app --reload
```

Acesse `http://localhost:8000`.
Login default: `admin / vertice2026` (trocar imediatamente em produção).

Sem API keys configuradas, os adaptadores LLM rodam em **modo mock** — todas as funcionalidades da plataforma ficam navegáveis para desenvolvimento offline.

### Performance & throughput

A camada de persistência foi calibrada para alto throughput:

- **Pool asyncpg** com prepared statements automáticos por conexão
  (`PG_POOL_MIN_SIZE=5`, `PG_POOL_MAX_SIZE=20` por padrão; ajuste em
  `.env` conforme `max_connections` do PG).
- **JSONB nativo** em colunas de payload (audit, presentations, módulos,
  finops policies) com índices GIN onde faz diferença operacional.
- **TIMESTAMPTZ** com timezone (UTC), `BOOLEAN` nativo e `IDENTITY` para PKs
  — sem mais hacks de inteiro 0/1 ou `lastrowid`.
- Índices compostos onde mais ajudam: `audit_events(category, ts DESC)`,
  `finops_ledger(created_at DESC, model_name)`, parcial em
  `audit_events(status_code) WHERE status_code >= 400`.

## 5. Estrutura

```
app/
├── main.py                 entrypoint FastAPI
├── config.py               settings via pydantic-settings (pool, DSN)
├── core/
│   ├── domain/             entidades puras (sem dependência externa)
│   ├── ports/              interfaces que o core consome
│   └── services/           use cases (regras de negócio)
├── adapters/
│   ├── db/                 PostgreSQL/asyncpg + repositórios
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

### Dev local

```bash
docker compose up --build      # Postgres + app
```

### Produção (VPS Hostinger ou similar)

Stack pronta com Caddy (TLS automático Let's Encrypt) + app + Postgres +
backup diário, em [docs/DEPLOY-HOSTINGER.md](docs/DEPLOY-HOSTINGER.md).
Resumo:

```bash
# na VPS, como root:
curl -fsSL https://raw.githubusercontent.com/SEU_USER/SEU_REPO/main/scripts/install_docker.sh \
     -o install_docker.sh && chmod +x install_docker.sh && ./install_docker.sh

# como usuário deploy:
git clone https://github.com/SEU_USER/SEU_REPO.git vertice && cd vertice
cp .env.production.example .env.production && chmod 600 .env.production
nano .env.production               # preencher TROCAR_*
./scripts/deploy.sh
```

Para Kubernetes, use os manifestos `app/adapters/db/schema.sql` + `seed.sql` direto e crie um Helm chart genérico (FastAPI + Postgres operator). Não há manifestos K8s incluídos nesta versão.

## 11. Testes

Os testes exercitam o mesmo SQL que vai pra produção — não há equivalente
in-memory de PostgreSQL. Por isso, é preciso ter um servidor PG acessível.
O `tests/conftest.py` cria um schema isolado por sessão e dropa com
CASCADE no teardown, então o banco usado fica limpo.

```bash
# 1) Garanta que o Postgres de teste existe (uma vez):
createdb vertice_test

# 2) Aponte para ele e rode:
export TEST_DATABASE_URL="postgresql://vertice:vertice@localhost:5432/vertice_test"
pytest -q
```

Para usar o mesmo Postgres do `docker compose`:

```bash
docker compose up -d postgres
docker compose exec postgres createdb -U vertice vertice_test
TEST_DATABASE_URL="postgresql://vertice:vertice@localhost:5432/vertice_test" pytest -q
```

## 12. Licença

MIT.
