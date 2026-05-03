# PLAN — Módulo `Raio X Cliente`

> **Status:** Draft v1 · aguardando aprovação para execução
> **Owner:** Sergio Gaiotto
> **Versão alvo:** Vértice 1.1.0
> **Data:** 2026-05-02

---

## 1. Objetivo

Entregar um módulo de Funcionalidade chamado **Raio X Cliente** que transforma o catálogo de tabelas do Vértice (estáticas + dinâmicas geradas por Módulos) em uma **prancheta clínica interativa** com gráficos Plotly, copiloto de IA e relacionamentos editáveis.

**Critério de sucesso (goal-backward).** Um supervisor entra em `/raiox`, escolhe duas tabelas, recebe sugestão automática de 3 gráficos, monta um board 3×N com crossfilter, salva e exporta para `Galeria/Apresentações`. Um analista_n3 entra no mesmo board e consegue interagir, mas não editar.

---

## 2. Princípios não-negociáveis

1. **Hexagonal preservado.** Domain → Ports → Adapters. Nada do core depende de Plotly ou de SQLite.
2. **Sem build step.** UI continua HTMX + Alpine + Tailwind CDN, igual aos demais módulos.
3. **Reuso máximo.** `SchemaService`, `Text2SqlService`, `finops_ledger`, `AuditMiddleware`, `Galeria/Apresentações`.
4. **SKILL.md como contrato.** Toda decisão de IA passa por `app/skills/raiox_advisor.md`.
5. **RBAC consistente.** `admin`/`supervisor` editam, `analista_n3` somente lê e interage. Mesmo padrão `_require_any_role` do `pages.py`.
6. **FinOps em tudo.** Toda chamada de IA grava no `finops_ledger` com `feature='raiox'`.
7. **Bandeira de fase.** Cada fase é entregável navegável e reversível.

---

## 3. Arquitetura — Visão de alto nível

```
┌─────────────────────────────────────────────────────────────────────┐
│ Browser                                                             │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ /raiox  (Jinja2 + Alpine.js)                                 │   │
│  │   ├ Header: Boards▾ · Filtros globais · IA💬 · + chart       │   │
│  │   ├ Mapa de Tecidos (drawer lateral)                         │   │
│  │   ├ Mesa de Exames: grid 3×N de tiles Plotly.js              │   │
│  │   └ Insight Queue (rodapé colapsável)                        │   │
│  │  raiox.js: wrapper Plotly + crossfilter + drag&drop          │   │
│  └─────────────────────────────────┬────────────────────────────┘   │
│                                    │ HTMX / fetch                    │
└─────────────────────────────────────┼───────────────────────────────┘
                                      │
┌─────────────────────────────────────▼───────────────────────────────┐
│ FastAPI · /api/raiox/*                                              │
│  raiox_router.py                                                    │
│   ├ /boards         CRUD                                            │
│   ├ /charts         CRUD                                            │
│   ├ /relationships  CRUD + auto-detect                              │
│   ├ /query          executa join paramétrico → DataFrame → JSON     │
│   ├ /copilot/recommend-chart   LLM sugere config                    │
│   ├ /copilot/insights          EDA proativa                         │
│   └ /copilot/ask               NL → SQL → Plotly fig                │
├─────────────────────────────────────────────────────────────────────┤
│ Core domain                                                         │
│   raiox_service.py · raiox_advisor_service.py                       │
│   ports: BoardRepo · ChartRepo · RelationshipRepo                   │
├─────────────────────────────────────────────────────────────────────┤
│ Adapters                                                            │
│   raiox_repo.py (sqlite)                                            │
│   reuso: SchemaService · Text2SqlService · build_clients            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Schema novo (3 tabelas)

Adicionado ao final de `app/adapters/db/schema.sql` + migração idempotente em `sqlite.py`.

```sql
-- ===== Raio X Cliente =====

CREATE TABLE IF NOT EXISTS raiox_boards (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    owner_id TEXT,                    -- users.id
    is_shared INTEGER NOT NULL DEFAULT 1,   -- 0=privado, 1=visível pra todos
    layout_json TEXT,                 -- {cols: 3, rows: [...positions...]}
    filters_json TEXT,                -- filtros globais persistidos
    cover_emoji TEXT DEFAULT '🩻',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_raiox_boards_owner ON raiox_boards(owner_id);

CREATE TABLE IF NOT EXISTS raiox_charts (
    id TEXT PRIMARY KEY,
    board_id TEXT NOT NULL,
    title TEXT,
    chart_type TEXT NOT NULL,         -- 'bar'|'line'|'scatter'|'sankey'|...
    position_row INTEGER NOT NULL DEFAULT 0,
    position_col INTEGER NOT NULL DEFAULT 0,
    span_cols INTEGER NOT NULL DEFAULT 1,    -- 1..3
    span_rows INTEGER NOT NULL DEFAULT 1,    -- 1..2
    query_spec_json TEXT NOT NULL,    -- {tables, joins, label_col, value_col, agg, filters}
    plotly_config_json TEXT,          -- overrides do tema/layout Plotly
    created_by_ai INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (board_id) REFERENCES raiox_boards(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_raiox_charts_board ON raiox_charts(board_id);

CREATE TABLE IF NOT EXISTS raiox_relationships (
    id TEXT PRIMARY KEY,
    table_a TEXT NOT NULL,
    column_a TEXT NOT NULL,
    table_b TEXT NOT NULL,
    column_b TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'one_to_many',  -- '1:1'|'1:N'|'N:1'|'N:N'
    confidence REAL DEFAULT 0.0,      -- 0..1, da heurística
    confirmed_by_user TEXT,           -- NULL = sugestão, preenchido = aprovado
    confirmed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (table_a, column_a, table_b, column_b)
);
```

Não há `DROP` — migração só adiciona. Reverter = `DROP TABLE` manual no SQLite.

---

## 5. Plano de fases

### Fase 0 — Fundação navegável (entrega 1)

**Objetivo:** `/raiox` aparece no menu, abre sem erro, mostra 3 charts demo a partir de tabelas existentes.

| # | Task | Arquivo | Saída |
|---|---|---|---|
| 0.1 | Adicionar `plotly==5.24.1` ao `requirements.txt` | `requirements.txt` | dep adicionada |
| 0.2 | Schema novo + migração | `app/adapters/db/schema.sql`, `app/adapters/db/sqlite.py` | tabelas criadas em init |
| 0.3 | Domain entities + ports | `app/core/domain/entities.py` (extensão), `app/core/ports/` | `Board`, `Chart`, `Relationship` |
| 0.4 | Repository SQLite | `app/adapters/db/repositories/raiox_repo.py` | CRUD básico |
| 0.5 | `RaioXService` (core) | `app/core/services/raiox_service.py` | métodos: `list_boards`, `create_board`, `add_chart`, `query_series` (estende `fetch_series` para 2D) |
| 0.6 | Schemas Pydantic | `app/api/schemas/raiox.py` | `BoardOut`, `ChartIn/Out`, `QuerySpec` |
| 0.7 | Router HTTP | `app/api/routers/raiox_router.py` | `/api/raiox/boards`, `/charts`, `/query` |
| 0.8 | Wire-up | `app/main.py`, `app/api/deps.py` | `raiox_router` incluso, deps registradas |
| 0.9 | Item de menu | `app/templates/partials/nav_left.html` | "Raio X Cliente" abaixo de Gestão Churn |
| 0.10 | Página + grid básico | `app/templates/raiox/index.html`, `app/api/routers/pages.py` | renderiza 3 charts demo (Plotly.js CDN) |
| 0.11 | JS wrapper | `app/static/js/raiox.js` | `renderChart(el, fig)`, layout do tema Vértice |
| 0.12 | Mapa de Tecidos read-only | partial `_tissue_map.html` | drawer lateral lista tabelas + colunas + samples |
| 0.13 | Smoke test | `tests/test_raiox_phase0.py` | testa CRUD board + render de 1 chart |

**Definition of done Fase 0:**
- `pytest -q tests/test_raiox_phase0.py` verde
- login → `/raiox` abre sem erro 500
- visíveis 3 charts (bar/line/scatter) com dados reais (`bko_cases` por exemplo)
- analista_n3 vê a página, mas botão "+ chart" desabilitado

---

### Fase 1 — Mesa de Exames completa

**Objetivo:** boards persistidos com grid 3×10 drag-and-drop, catálogo completo de tipos de chart, crossfilter global.

| # | Task | Saída |
|---|---|---|
| 1.1 | Editor de board (Alpine state machine) | drag/drop, span 1-3 cols × 1-2 rows |
| 1.2 | Catálogo de chart types | bar, line, scatter, pie/donut, treemap, sunburst, sankey, heatmap, box, violin, parallel_coords, scatter_matrix, candlestick, gauge/KPI, funnel, waterfall, choropleth, network |
| 1.3 | Construtor de query com **join** | `query_spec` aceita `joins: [{from:tableA.col, to:tableB.col}]` consultando `raiox_relationships` |
| 1.4 | Crossfilter global | clicar fatia em chart A emite `filter` no Alpine store → todos os charts re-fetcham com filtro |
| 1.5 | Filtros globais persistidos | range slider de datas, multi-select por coluna, salvo em `raiox_boards.filters_json` |
| 1.6 | Permissões | `_require_any_role` no `pages.py` + flag `can_edit` no template |
| 1.7 | Auditoria | toda edição grava em `audit_events` via `AuditMiddleware` (já automático) |
| 1.8 | Testes | `tests/test_raiox_phase1.py` — boards CRUD, joins, crossfilter |

**Definition of done Fase 1:**
- Board "Visão Churn 360" criado a partir de 4 tabelas distintas
- Clicar uma operadora num bar chart filtra os outros 5 charts simultaneamente
- analista_n3 logado consegue interagir mas todos os controles de edição estão `disabled`

---

### Fase 2 — Copiloto Diagnóstico (IA)

**Objetivo:** IA sugere chart ideal, gera insights proativamente, responde NL→fig.

| # | Task | Saída |
|---|---|---|
| 2.1 | `app/skills/raiox_advisor.md` | SKILL.md com identidade/inputs/saída/guardrails (mirror `radar_intent.md`) |
| 2.2 | `RaioXAdvisorService` | métodos: `recommend_chart(table, columns)`, `detect_relationships(tables)`, `generate_insights(board)`, `nl_to_fig(question, allowed_tables)` |
| 2.3 | Endpoint `/copilot/recommend-chart` | recebe `{table, columns}` → IA devolve `{chart_type, label_col, value_col, agg, rationale}` |
| 2.4 | Endpoint `/copilot/ask` | reuso `Text2SqlService.ask()` → resultado adaptado para `plotly.express` → `fig.to_json()` |
| 2.5 | Endpoint `/copilot/insights` | varre board, faz EDA leve (correlações, outliers via IQR, top-shifts no tempo) → fila de cards |
| 2.6 | UI: drawer "💬 IA" | input de texto + sugestões + botão "transformar em chart" |
| 2.7 | UI: Insight Queue | rodapé colapsável com cards de achados ranqueados por surpresa |
| 2.8 | FinOps | toda chamada IA grava `feature='raiox'`, `agent='raiox_advisor'` no ledger |
| 2.9 | Testes | mock do LLM via `MockLLMClient`, asserções de schema de saída |

**Definition of done Fase 2:**
- Pergunta "qual operadora teve mais cancelamentos em março?" gera chart Plotly válido sem intervenção manual
- "Insights" propõe pelo menos 1 achado por board com >5 charts
- ledger mostra entradas com `feature='raiox'`

---

### Fase 3 — Storyline & integrações

**Objetivo:** boards viram apresentações narradas; relacionamentos têm editor visual; opcional DuckDB-Wasm.

| # | Task | Saída |
|---|---|---|
| 3.1 | Storyline mode | sequência ordenada de charts + texto narrativo por capítulo |
| 3.2 | Export → Galeria | botão "exportar como apresentação" → cria registro em `presentations` reaproveitando `presentation_service` |
| 3.3 | Editor visual de relacionamentos | grafo Plotly Network — clicar aresta confirma/edita/exclui |
| 3.4 | (opcional) DuckDB-Wasm | para slices >100k linhas, joins acontecem no browser |
| 3.5 | Testes E2E | `tests/test_raiox_e2e.py` — fluxo completo de criação à exportação |

**Definition of done Fase 3:**
- Board exportado vira apresentação navegável em `/gallery/{id}`
- Editor de relacionamentos detecta 5 FKs prováveis em `bko_cases` ↔ `transcripts` com confidence > 0.8

---

## 6. Verificação goal-backward

Para cada fase, o gate de saída valida o **resultado**, não o **task list**:

- **F0:** abre página, render 3 charts, RBAC funciona. Se sim → Fase 1.
- **F1:** board "Visão Churn 360" salvo + crossfilter ativo. Se sim → Fase 2.
- **F2:** NL→chart e Insight Queue funcionando com dados reais. Se sim → Fase 3.
- **F3:** export para Galeria + editor de relacionamentos. Encerra v1.1.0.

---

## 7. Dependências, riscos, mitigações

| Risco | Probabilidade | Mitigação |
|---|---|---|
| Plotly bundle pesado (~3MB) atrasa primeiro paint | Média | Carregar via `<script defer>` + spinner no grid; lazy-load `plotly-basic` (1MB) na fase 0 e upgrade para full em fases avançadas |
| Joins ad-hoc gerados pelo usuário causam queries pesadas | Alta | `LIMIT` obrigatório, timeout 5s, `EXPLAIN QUERY PLAN` antes de executar, índice automático no `column_a`/`column_b` confirmados |
| IA inventa chart_type inválido | Média | Validar contra enum no Pydantic; fallback para `bar` |
| Mudança de schema das tabelas dinâmicas quebra boards salvos | Média | `query_spec_json` valida colunas no momento do load — colunas faltantes mostram badge "coluna ausente" e charts continuam renderizando os outros |
| analista_n3 consegue burlar RBAC pelo endpoint | Alta | Gate duplo: `_require_any_role` no router + verificação por role na função de mutação. Testes de autorização cobrem cada endpoint mutante |

---

## 8. Open questions (decidir antes da F2)

- **Q1.** Insights Queue deve persistir em tabela ou ser efêmera por sessão? *Sugestão: efêmera para MVP; tabela só na F3 se virar inbox.*
- **Q2.** Filtros globais devem ser compartilhados entre boards ou isolados? *Sugestão: isolados por board.*
- **Q3.** Exportar para Galeria gera **PNG** dos charts (server-side via `plotly.io.to_image`) ou só JSON spec? *Sugestão: PNG para fidelidade visual; requer `kaleido` como dep.*

---

## 9. Não-objetivos (fora de escopo da v1.1)

- Edição em tempo real multi-usuário (CRDT/WebSocket)
- Conexão com bancos externos além do SQLite local
- Dashboards com >30 tiles (limite hard 3×10)
- Autenticação granular por chart (apenas por board)
- Mobile-first (desktop é prioridade — mobile só "view-only" sem responsividade rica)

---

## 10. Aceite

- [ ] Sergio aprova arquitetura e fases
- [ ] OK adicionar `plotly==5.24.1` (e `kaleido` apenas na F3) ao `requirements.txt`
- [ ] OK criar 3 tabelas novas (`raiox_boards`, `raiox_charts`, `raiox_relationships`) via migração idempotente
- [ ] Fase 0 começa após este aceite

---

*Documento gerado para suporte ao Spec-Driven Development do Vértice.*
