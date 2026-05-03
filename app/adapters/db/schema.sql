-- ============================================================
-- Vértice — schema SQLite v1.0.0
-- ============================================================

-- Auth & RBAC
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,                       -- UUID
    username TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    salt TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL                  -- ex: 'admin', 'analista_n3'
);

CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL                  -- ex: 'execute:agent_analysis'
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id TEXT NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, role_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id INTEGER NOT NULL,
    permission_id INTEGER NOT NULL,
    PRIMARY KEY (role_id, permission_id),
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE,
    FOREIGN KEY (permission_id) REFERENCES permissions(id) ON DELETE CASCADE
);

-- Module Registry
CREATE TABLE IF NOT EXISTS modules (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    endpoint_url TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    config_params TEXT,                        -- JSON
    description TEXT DEFAULT '',
    skill_path TEXT,
    response_type TEXT DEFAULT 'text',         -- 'text' | 'api' | 'table'
    response_config TEXT,                      -- JSON: {api_endpoint_id: '...'} ou {feature: 'radar'}
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- API Endpoints externos (configurados pelo admin para uso por módulos response_type='api')
CREATE TABLE IF NOT EXISTS api_endpoints (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT DEFAULT '',
    url TEXT NOT NULL,                         -- URL completa, ex: https://api.exemplo.com/v1/predict
    method TEXT DEFAULT 'POST',                -- método HTTP
    headers TEXT,                              -- JSON dict {"Authorization": "Bearer xxx", ...}
    timeout_seconds INTEGER DEFAULT 30,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_by_user TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Histórico de chamadas a APIs externas (auditoria + debug)
CREATE TABLE IF NOT EXISTS api_calls (
    id TEXT PRIMARY KEY,
    api_endpoint_id TEXT NOT NULL,
    module_id TEXT,
    user_id TEXT,
    request_body TEXT,                         -- JSON enviado
    response_status INTEGER,
    response_body TEXT,                        -- JSON ou texto retornado
    duration_ms REAL,
    error TEXT,
    called_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (api_endpoint_id) REFERENCES api_endpoints(id)
);
CREATE INDEX IF NOT EXISTS idx_api_calls_endpoint ON api_calls(api_endpoint_id, called_at DESC);

-- Prompts (guardrail-system-guardrail)
CREATE TABLE IF NOT EXISTS prompts (
    id TEXT PRIMARY KEY,
    module_name TEXT,                 -- legado (mantido para compat); nova coluna é module_names
    module_names TEXT,                -- JSON array: ["radar", "churn", ...]
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    input_guardrail TEXT,
    system_prompt TEXT NOT NULL,
    output_guardrail TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (name, version)
);

CREATE INDEX IF NOT EXISTS idx_prompts_name ON prompts(name);

-- ===== BKO Inteligente: casos (XLSX) + transcrições (JSON) =====
-- Join: bko_cases.contract_msisdn ↔ transcripts.verint_nr_contrato

CREATE TABLE IF NOT EXISTS bko_cases (
    case_number TEXT PRIMARY KEY,
    created_by TEXT,
    owner TEXT,
    phone TEXT,
    opened_at TIMESTAMP,
    contract_msisdn TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bko_cases_contract ON bko_cases(contract_msisdn);

CREATE TABLE IF NOT EXISTS transcripts (
    transaction_id TEXT PRIMARY KEY,
    verint_nr_contrato TEXT,
    transcription_text TEXT,
    started_at TIMESTAMP,
    duration_s REAL,
    segment TEXT,
    msisdn TEXT,
    ani TEXT,
    cpf TEXT,
    employee TEXT,
    raw_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_transcripts_nr_contrato ON transcripts(verint_nr_contrato);

-- ===== Rastreabilidade (Audit Trail) =====
-- Registra TODAS as ações: chamadas HTTP, execuções de módulo, mudanças de
-- configuração, uploads, etc. Usado pela página /audit (Monitoramento).

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_id TEXT,           -- UUID do usuário (nullable para chamadas anônimas)
    username TEXT,          -- snapshot do username no momento (sobrevive a deletes)
    category TEXT NOT NULL, -- 'http' | 'module_run' | 'config' | 'upload' | 'auth' | 'finops' | ...
    action TEXT NOT NULL,   -- verbo curto: 'GET', 'POST', 'create', 'update', 'login', 'run' ...
    target TEXT,            -- recurso afetado: '/api/radar/run-module', 'modules/radar', etc
    status_code INTEGER,    -- código HTTP quando aplicável
    duration_ms REAL,       -- duração da operação em milissegundos
    feature TEXT,           -- 'radar' | 'churn' | 'modules' | etc (extraído do path)
    payload TEXT,           -- JSON com input/output/diff/contexto adicional
    error TEXT,             -- mensagem de erro se status >= 400
    ip TEXT,                -- IP de origem
    user_agent TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_category ON audit_events(category, ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_events(user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_feature ON audit_events(feature, ts DESC);

-- ===== Galeria de Apresentações =====
-- Apresentações geradas e armazenadas para consulta/download/conversa posterior

CREATE TABLE IF NOT EXISTS presentations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    subtitle TEXT,
    feature TEXT,                  -- radar | churn | ...
    case_number TEXT,              -- caso BKO usado de contexto, se houver
    sections TEXT NOT NULL,        -- JSON: [{title, body, source_card_uid?}, ...]
    insights TEXT,                 -- JSON: [{type, content}, ...] (executive summary)
    visuals TEXT,                  -- JSON: [{title, type, image_b64, caption, source_card_uid}, ...]
    chat_history TEXT,             -- JSON: histórico de conversa sobre a presentation
    created_by_user TEXT,
    created_by_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    cost_estimated REAL DEFAULT 0,
    tokens_input INTEGER DEFAULT 0,
    tokens_output INTEGER DEFAULT 0,
    model_used TEXT
);

CREATE INDEX IF NOT EXISTS idx_presentations_created ON presentations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_presentations_user ON presentations(created_by_id);
CREATE INDEX IF NOT EXISTS idx_presentations_feature ON presentations(feature);

-- Contracts (Radar Voz do Cliente)
CREATE TABLE IF NOT EXISTS contracts (
    contract_number TEXT PRIMARY KEY,
    call_id TEXT,
    contact_id TEXT,
    operator TEXT,
    contact_at TIMESTAMP,
    segment TEXT DEFAULT 'RESIDENCIAL',
    transcript TEXT DEFAULT '',
    extra TEXT,                                -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_contracts_contact_at ON contracts(contact_at DESC);
CREATE INDEX IF NOT EXISTS idx_contracts_operator ON contracts(operator);

-- Analysis Cards
CREATE TABLE IF NOT EXISTS analysis_cards (
    id TEXT PRIMARY KEY,
    contract_number TEXT NOT NULL,
    name TEXT NOT NULL,
    output_type TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    expected_size TEXT DEFAULT '',
    model_used TEXT,
    result TEXT,
    confidence REAL,
    tokens_input INTEGER DEFAULT 0,
    tokens_output INTEGER DEFAULT 0,
    cost_estimated REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (contract_number) REFERENCES contracts(contract_number) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cards_contract ON analysis_cards(contract_number);

-- Churn taxonomy
CREATE TABLE IF NOT EXISTS churn_nodes (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    parent_id TEXT,
    depth INTEGER DEFAULT 0,
    examples TEXT,                             -- JSON array
    occurrences INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_id) REFERENCES churn_nodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_churn_parent ON churn_nodes(parent_id);

CREATE TABLE IF NOT EXISTS churn_classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_number TEXT NOT NULL,
    path TEXT NOT NULL,                        -- JSON array
    confidence REAL,
    rationale TEXT,
    classified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- FinOps Ledger
-- Dimensões de chargeback/showback modernas: domain (negócio),
-- product (linha de produto), agent (agente AI específico), flow (fluxo
-- conversacional), prompt_id, integration (API externa consumida),
-- environment (prod/staging/dev), latency_ms (observabilidade), storage_bytes
-- (auditoria de consumo). Cabe expandir o ledger sem quebrar gravações
-- existentes — todos os novos campos são opcionais e gravados via migração
-- idempotente em sqlite.py.
CREATE TABLE IF NOT EXISTS finops_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    module_id TEXT,
    model_name TEXT,
    tokens_input INTEGER DEFAULT 0,
    tokens_output INTEGER DEFAULT 0,
    cost_estimated REAL DEFAULT 0,
    context_tag TEXT DEFAULT '',
    domain TEXT,
    product TEXT,
    agent TEXT,
    flow TEXT,
    prompt_id TEXT,
    integration TEXT,
    environment TEXT DEFAULT 'production',
    latency_ms REAL,
    storage_bytes INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_finops_created ON finops_ledger(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_finops_model ON finops_ledger(model_name);
-- Índices das colunas novas (domain, agent, environment) são criados na
-- migração em sqlite.py — DEPOIS dos ALTER TABLE que adicionam as colunas
-- em bancos existentes. Colocá-los aqui faz o executescript() falhar com
-- "no such column" quando a tabela já existe sem as colunas novas.

-- FinOps Budgets — orçamentos por escopo (módulo, modelo, usuário, domain,
-- environment) e período (daily/weekly/monthly), com threshold de aviso e
-- opção hard_stop para bloquear chamadas quando estourado.
CREATE TABLE IF NOT EXISTS finops_budgets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scope_type TEXT NOT NULL,                -- 'global'|'module'|'model'|'user'|'domain'|'environment'|'agent'
    scope_value TEXT,                        -- valor (NULL p/ scope='global')
    period TEXT NOT NULL DEFAULT 'monthly',  -- 'daily'|'weekly'|'monthly'
    limit_brl REAL NOT NULL,
    warning_threshold REAL NOT NULL DEFAULT 0.8,
    hard_stop INTEGER NOT NULL DEFAULT 0,    -- 0/1 booleano sqlite
    notes TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_budgets_scope ON finops_budgets(scope_type, scope_value);

-- FinOps Model Policies — política explícita de uso por modelo (risco × valor
-- × custo). O cost-aware router consulta esta tabela antes de despachar para
-- decidir bloqueio, downgrade ou simples warn.
CREATE TABLE IF NOT EXISTS finops_model_policies (
    id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL UNIQUE,
    risk_tier TEXT NOT NULL DEFAULT 'medium',     -- 'low'|'medium'|'high'
    value_tier TEXT NOT NULL DEFAULT 'medium',    -- 'low'|'medium'|'high'
    max_cost_per_call REAL,                       -- BRL — NULL = sem cap
    max_tokens_per_call INTEGER,
    allowed_features TEXT,                        -- JSON array; NULL = todas
    rationale TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- FinOps Alerts — trilha de auditoria toda vez que um budget cruza warning
-- ou crítico. Permite reconstruir histórico de excessos para o relatório
-- mensal e para alertas a stakeholders externos.
CREATE TABLE IF NOT EXISTS finops_alerts (
    id TEXT PRIMARY KEY,
    budget_id TEXT NOT NULL,
    severity TEXT NOT NULL,                       -- 'warning'|'critical'
    cost_observed REAL NOT NULL,
    limit_reference REAL NOT NULL,
    period_start TIMESTAMP,
    period_end TIMESTAMP,
    triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    FOREIGN KEY (budget_id) REFERENCES finops_budgets(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_alerts_budget ON finops_alerts(budget_id, triggered_at DESC);

-- ===== Raio X Cliente =====
-- Boards (pranchetas): grid 3xN de gráficos Plotly persistidos por usuário.
CREATE TABLE IF NOT EXISTS raiox_boards (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    owner_id TEXT,
    is_shared INTEGER NOT NULL DEFAULT 1,        -- 0=privado, 1=visível pra todos
    layout_json TEXT,                             -- JSON com layout/posições
    filters_json TEXT,                            -- JSON com filtros globais persistidos
    cover_emoji TEXT DEFAULT '🩻',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_raiox_boards_owner ON raiox_boards(owner_id);

-- Charts: cada tile do grid. position_row/col em [0..9 x 0..2], span_cols 1..3, span_rows 1..2.
CREATE TABLE IF NOT EXISTS raiox_charts (
    id TEXT PRIMARY KEY,
    board_id TEXT NOT NULL,
    title TEXT,
    chart_type TEXT NOT NULL,                     -- 'bar'|'line'|'scatter'|'pie'|...
    position_row INTEGER NOT NULL DEFAULT 0,
    position_col INTEGER NOT NULL DEFAULT 0,
    span_cols INTEGER NOT NULL DEFAULT 1,
    span_rows INTEGER NOT NULL DEFAULT 1,
    query_spec_json TEXT NOT NULL,                -- JSON: tabela, colunas, agg, filtros
    plotly_config_json TEXT,                      -- overrides do tema/layout Plotly
    created_by_ai INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (board_id) REFERENCES raiox_boards(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_raiox_charts_board ON raiox_charts(board_id);

-- Relacionamentos entre tabelas: detectados (confidence>0) ou confirmados pelo usuário.
CREATE TABLE IF NOT EXISTS raiox_relationships (
    id TEXT PRIMARY KEY,
    table_a TEXT NOT NULL,
    column_a TEXT NOT NULL,
    table_b TEXT NOT NULL,
    column_b TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'one_to_many',     -- '1:1'|'1:N'|'N:1'|'N:N'
    confidence REAL DEFAULT 0.0,                  -- 0..1, da heurística
    confirmed_by_user TEXT,                       -- NULL = sugestão; preenchido = aprovado
    confirmed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (table_a, column_a, table_b, column_b)
);
CREATE INDEX IF NOT EXISTS idx_raiox_rel_a ON raiox_relationships(table_a);
CREATE INDEX IF NOT EXISTS idx_raiox_rel_b ON raiox_relationships(table_b);

-- Failsafe inbox
CREATE TABLE IF NOT EXISTS failsafe_actions (
    id TEXT PRIMARY KEY,
    module_name TEXT NOT NULL,
    description TEXT NOT NULL,
    payload TEXT,                              -- JSON
    confidence REAL,
    status TEXT DEFAULT 'pending',
    requested_by TEXT,
    decided_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_failsafe_status ON failsafe_actions(status);
