/**
 * Raio X Cliente — Alpine app + Plotly renderer.
 * Tema alinhado à paleta Vértice (brand-600 #DC2626, neutral warm gray).
 */

const RAIOX_PALETTE = [
  '#DC2626', '#534AB7', '#185FA5', '#92660A', '#7F1D1D',
  '#993C1D', '#3C3489', '#7F77DD', '#E0A82B', '#D85A30',
];

const RAIOX_LAYOUT_BASE = {
  margin: { t: 8, r: 12, b: 36, l: 44 },
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  font: { family: 'Inter, sans-serif', size: 11, color: '#292524' },
  xaxis: { gridcolor: '#F5F5F4', zerolinecolor: '#E7E5E4', tickfont: { size: 10 } },
  yaxis: { gridcolor: '#F5F5F4', zerolinecolor: '#E7E5E4', tickfont: { size: 10 } },
  showlegend: false,
};

const RAIOX_CONFIG = {
  responsive: true,
  displaylogo: false,
  modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d'],
  displayModeBar: 'hover',
};

// Charts cuja layout é "categorial" (sem eixos cartesianos): pie, donut, treemap, sunburst, indicator
const RAIOX_CATEGORICAL_TYPES = new Set([
  'pie', 'donut', 'treemap', 'sunburst', 'indicator', 'funnel', 'waterfall',
]);

function buildPlotlyTrace(chartType, series) {
  const labels = series.labels || [];
  const values = series.values || [];
  const customdata = labels.map(l => ({ label: l }));

  switch (chartType) {
    case 'bar':
      return [{
        type: 'bar',
        x: labels, y: values,
        customdata,
        marker: { color: RAIOX_PALETTE[0] },
        hovertemplate: '<b>%{x}</b><br>%{y}<extra></extra>',
      }];
    case 'line':
      return [{
        type: 'scatter', mode: 'lines+markers',
        x: labels, y: values, customdata,
        line: { color: RAIOX_PALETTE[0], width: 2 },
        marker: { size: 5, color: RAIOX_PALETTE[0] },
        hovertemplate: '<b>%{x}</b><br>%{y}<extra></extra>',
      }];
    case 'area':
      return [{
        type: 'scatter', mode: 'lines',
        x: labels, y: values, customdata,
        fill: 'tozeroy',
        line: { color: RAIOX_PALETTE[1], width: 2 },
        fillcolor: 'rgba(83, 74, 183, 0.18)',
        hovertemplate: '<b>%{x}</b><br>%{y}<extra></extra>',
      }];
    case 'scatter':
      return [{
        type: 'scatter', mode: 'markers',
        x: labels, y: values, customdata,
        marker: { size: 9, color: RAIOX_PALETTE[1], opacity: 0.7 },
        hovertemplate: '<b>%{x}</b><br>%{y}<extra></extra>',
      }];
    case 'pie':
      return [{
        type: 'pie', labels, values, customdata,
        marker: { colors: RAIOX_PALETTE },
        textfont: { size: 10 },
        hovertemplate: '<b>%{label}</b><br>%{value} (%{percent})<extra></extra>',
      }];
    case 'donut':
      return [{
        type: 'pie', labels, values, customdata, hole: 0.55,
        marker: { colors: RAIOX_PALETTE },
        textfont: { size: 10 },
        hovertemplate: '<b>%{label}</b><br>%{value} (%{percent})<extra></extra>',
      }];
    case 'treemap':
      return [{
        type: 'treemap', labels, values,
        parents: labels.map(() => ''),
        textinfo: 'label+value',
        marker: { colors: RAIOX_PALETTE },
        hovertemplate: '<b>%{label}</b><br>%{value}<extra></extra>',
      }];
    case 'sunburst':
      return [{
        type: 'sunburst', labels, values,
        parents: labels.map(() => ''),
        marker: { colors: RAIOX_PALETTE },
        hovertemplate: '<b>%{label}</b><br>%{value}<extra></extra>',
      }];
    case 'funnel':
      return [{
        type: 'funnel', y: labels, x: values, customdata,
        marker: { color: RAIOX_PALETTE[0] },
        hovertemplate: '<b>%{y}</b><br>%{x}<extra></extra>',
      }];
    case 'waterfall':
      return [{
        type: 'waterfall', x: labels, y: values, customdata,
        connector: { line: { color: '#A8A29E' } },
        increasing: { marker: { color: RAIOX_PALETTE[0] } },
        decreasing: { marker: { color: RAIOX_PALETTE[3] } },
        hovertemplate: '<b>%{x}</b><br>%{y}<extra></extra>',
      }];
    case 'histogram':
      return [{
        type: 'histogram',
        x: values.length > 0 ? values : labels,
        marker: { color: RAIOX_PALETTE[2] },
      }];
    case 'box':
      return [{
        type: 'box', y: values, name: '',
        marker: { color: RAIOX_PALETTE[3] }, boxmean: true,
      }];
    case 'violin':
      return [{
        type: 'violin', y: values, box: { visible: true }, points: 'outliers',
        marker: { color: RAIOX_PALETTE[1] }, name: '',
      }];
    case 'heatmap':
      return [{
        type: 'heatmap',
        z: [values],
        x: labels,
        y: [series.value_column || 'valor'],
        colorscale: [[0, '#FECACA'], [0.5, '#F87171'], [1, '#7F1D1D']],
        showscale: false,
      }];
    case 'indicator': {
      const total = values.reduce((a, b) => a + b, 0);
      return [{
        type: 'indicator', mode: 'number',
        value: total,
        number: { font: { size: 32, color: RAIOX_PALETTE[0] } },
        title: { text: series.label_column || 'total', font: { size: 11 } },
      }];
    }
    default:
      return [{ type: 'bar', x: labels, y: values, customdata }];
  }
}

function renderRaioxChart(elementId, chartType, series, onPointClick) {
  const el = document.getElementById(elementId);
  if (!el) return;
  const traces = buildPlotlyTrace(chartType, series);
  const layout = JSON.parse(JSON.stringify(RAIOX_LAYOUT_BASE));

  if (RAIOX_CATEGORICAL_TYPES.has(chartType)) {
    layout.margin = { t: 8, r: 8, b: 8, l: 8 };
    delete layout.xaxis;
    delete layout.yaxis;
    if (chartType === 'pie' || chartType === 'donut') {
      layout.showlegend = true;
      layout.legend = { font: { size: 10 }, orientation: 'v', x: 1, y: 0.5 };
    }
  }
  if (chartType === 'heatmap') {
    layout.yaxis = { showticklabels: true, tickfont: { size: 9 } };
  }

  // eslint-disable-next-line no-undef
  Plotly.react(el, traces, layout, RAIOX_CONFIG);

  // Crossfilter (F1.4): clique em ponto/fatia emite filtro global
  if (typeof onPointClick === 'function') {
    el.removeAllListeners?.('plotly_click');
    el.on('plotly_click', (ev) => {
      const pt = ev.points?.[0];
      if (!pt) return;
      const value = pt.customdata?.label ?? pt.label ?? pt.x ?? pt.y;
      if (value !== undefined && value !== null) onPointClick(String(value));
    });
  }
}

async function apiFetch(path, options = {}) {
  const opts = {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  };
  const r = await fetch(path, opts);
  if (!r.ok) {
    const detail = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(detail.detail || `HTTP ${r.status}`);
  }
  if (r.status === 204) return null;
  return await r.json();
}

function _defaultNewChart() {
  return {
    title: '',
    chart_type: 'bar',
    span_cols: 1,
    span_rows: 1,
    skill_path: '',
    query_spec: {
      table: '',
      label_column: '',
      value_column: '',
      value_expr: '',     // variável calculada (sobrescreve value_column)
      value_label: '',    // rótulo amigável da var calculada
      aggregate: 'count',
      order_by: 'value_desc',
      limit: 30,
    },
  };
}

window.raioxApp = function (canEdit, initialBoardId) {
  return {
    canEdit,
    loading: true,
    boards: [],
    currentBoard: null,
    charts: [],
    chartStates: {},
    tables: [],
    expandedTables: {},
    tissueFilter: '',
    tissueMapOpen: false,
    boardDropdownOpen: false,
    newBoardModalOpen: false,
    newChartModalOpen: false,
    newBoard: { name: '', description: '', is_shared: true, cover_emoji: '🩻', allowed_roles: [], allowed_departments: [] },
    newBoardScope: 'shared',  // 'shared' | 'restricted'
    scopeOptions: { roles: [], departments: [] },
    skillsOptions: [],
    // Estado de edição: quando setado, o modal "Novo gráfico" salva via PATCH
    editingChartId: null,
    // Análise inteligente — drawer + histórico persistido
    analysisDrawerOpen: false,
    analysisLoading: false,
    analysisResult: null,        // análise atualmente exibida
    analysisHistory: [],          // lista resumida (id, created_at, username, totals, charts_count)
    analysisHistoryLoading: false,
    analysisView: 'history',      // 'history' (lista) | 'detail' (uma análise)
    // Drag feedback
    dragOverChartId: null,
    newChart: _defaultNewChart(),
    // Estado do modal redesenhado
    nc: {
      tableFilter: '',
      expandedTables: {},        // {table_name: bool}
      columnTypes: {},           // {col_name: {kind, type, is_pk}} para tabela atual
      previewSeries: null,       // {labels, values, ...} ou null
      previewLoading: false,
      previewError: null,
      copilotLoading: false,
      copilotRationale: '',
      intentHint: '',
      derivedOpen: false,
      derivedDraft: { name: '', expr: '' },
      lastEditedRole: '',        // 'label' | 'value' (para o click toggle)
    },
    // Crossfilter: pilha de predicados aplicados a todos os charts compatíveis
    // do board. Cada item: {column, value, sourceChartId, sourceChartTitle}.
    crossfilters: [],

    async loadScopeOptions() {
      try {
        this.scopeOptions = await apiFetch('/api/raiox/scope-options');
      } catch (e) {
        this.scopeOptions = { roles: [], departments: [] };
      }
    },

    async loadSkillsOptions() {
      try {
        this.skillsOptions = await apiFetch('/api/raiox/skills-options');
      } catch (e) {
        this.skillsOptions = [];
      }
    },

    async init() {
      try {
        await Promise.all([
          this.loadTables(),
          this.loadBoards(),
          this.loadScopeOptions(),
          this.loadSkillsOptions(),
        ]);
        if (initialBoardId) {
          await this.selectBoard(initialBoardId);
        } else if (this.boards.length > 0) {
          await this.selectBoard(this.boards[0].id);
        }
      } catch (e) {
        if (window.toast) toast(`Erro ao iniciar: ${e.message}`, 'error');
      } finally {
        this.loading = false;
      }
    },

    async loadTables() {
      this.tables = await apiFetch('/api/raiox/tables');
    },

    async loadBoards() {
      this.boards = await apiFetch('/api/raiox/boards');
    },

    get sortedCharts() {
      return [...this.charts].sort((a, b) =>
        (a.position_row - b.position_row) || (a.position_col - b.position_col),
      );
    },

    get filteredTables() {
      const q = (this.tissueFilter || '').trim().toLowerCase();
      if (!q) return this.tables;
      return this.tables.filter(t =>
        t.name.toLowerCase().includes(q) ||
        t.label.toLowerCase().includes(q) ||
        t.columns.some(c => c.name.toLowerCase().includes(q)),
      );
    },

    tableColumns(tableName) {
      const t = this.tables.find(x => x.name === tableName);
      return t ? t.columns : [];
    },

    /** Tabelas usadas pelos charts do board atual. */
    get _activeTablesInBoard() {
      return Array.from(new Set(
        (this.charts || [])
          .map(c => c.query_spec?.table)
          .filter(Boolean)
      ));
    },

    /** Colunas que existem em pelo menos uma das tabelas em uso pelo board.
     *  Usado pelo select de "coluna" do filtro global — evita escolher coluna
     *  que não bate com nenhum chart. */
    get globalFilterColumnOptions() {
      const seen = new Map(); // column_name -> { name, types: Set, tables: Set }
      for (const tableName of this._activeTablesInBoard) {
        const t = this.tables.find(x => x.name === tableName);
        if (!t) continue;
        for (const col of (t.columns || [])) {
          const existing = seen.get(col.name) || { name: col.name, type: col.type, tables: new Set() };
          existing.tables.add(t.label || t.name);
          seen.set(col.name, existing);
        }
      }
      return Array.from(seen.values())
        .map(x => ({ name: x.name, type: x.type, tablesLabel: Array.from(x.tables).join(', ') }))
        .sort((a, b) => a.name.localeCompare(b.name));
    },

    /** Quantos charts seriam afetados por um filtro {column}. */
    chartsCoveredBy(column) {
      let n = 0;
      for (const c of (this.charts || [])) {
        const cols = this.tableColumns(c.query_spec?.table || '').map(x => x.name);
        if (cols.includes(column)) n++;
      }
      return n;
    },

    async selectBoard(boardId) {
      try {
        this.currentBoard = await apiFetch(`/api/raiox/boards/${boardId}`);
        this.charts = await apiFetch(`/api/raiox/boards/${boardId}/charts`);
        this.chartStates = {};
        // Atualiza a URL sem recarregar
        history.replaceState({}, '', `/raiox?board=${boardId}`);
        this.crossfilters = [];
        await this.$nextTick();
        await this.renderAllCharts();
      } catch (e) {
        if (window.toast) toast(`Erro ao abrir dashboard: ${e.message}`, 'error');
      }
    },

    // Compõe filters explícitos do chart + filtros globais do board + crossfilters
    // (na ordem: próprios → globais → crossfilter). Só inclui aqueles cuja coluna
    // existe na tabela do chart.
    _composedFilters(chart) {
      const cols = new Set(this.tableColumns(chart.query_spec.table).map(c => c.name));
      const own = chart.query_spec.filters || [];
      const global = (this.currentBoard?.filters?.global || [])
        .filter(f => cols.has(f.column))
        .map(f => ({ column: f.column, op: '=', value: f.value }));
      const cross = this.crossfilters
        .filter(f => f.sourceChartId !== chart.id && cols.has(f.column))
        .map(f => ({ column: f.column, op: '=', value: f.value }));
      return [...own, ...global, ...cross];
    },

    async renderChart(chart) {
      this.chartStates[chart.id] = { loaded: false, error: null };
      try {
        const spec = { ...chart.query_spec, filters: this._composedFilters(chart) };
        const series = await apiFetch('/api/raiox/query', {
          method: 'POST',
          body: JSON.stringify(spec),
        });
        const onPointClick = (value) => this.applyCrossfilter(chart, value);
        renderRaioxChart(`raiox-chart-${chart.id}`, chart.chart_type, series, onPointClick);
        this.chartStates[chart.id] = { loaded: true, error: null };
      } catch (e) {
        this.chartStates[chart.id] = { loaded: false, error: e.message };
      }
    },

    async renderAllCharts() {
      for (const c of this.charts) await this.renderChart(c);
    },

    applyCrossfilter(sourceChart, value) {
      const column = sourceChart.query_spec.label_column;
      if (!column) return;
      // Toggle: se já existe filtro mesma column+value vindo do mesmo chart, remove
      const existingIdx = this.crossfilters.findIndex(
        f => f.sourceChartId === sourceChart.id && f.column === column && f.value === value,
      );
      if (existingIdx >= 0) {
        this.crossfilters.splice(existingIdx, 1);
      } else {
        // só um crossfilter ativo por chart-fonte (sobrescreve)
        this.crossfilters = this.crossfilters.filter(f => f.sourceChartId !== sourceChart.id);
        this.crossfilters.push({
          column, value,
          sourceChartId: sourceChart.id,
          sourceChartTitle: sourceChart.title || sourceChart.query_spec.table,
        });
      }
      this.renderAllCharts();
    },

    removeCrossfilter(idx) {
      this.crossfilters.splice(idx, 1);
      this.renderAllCharts();
    },

    clearCrossfilters() {
      this.crossfilters = [];
      this.renderAllCharts();
    },

    // ---------- Renomear/excluir board (M3) ----------
    renameBoardModalOpen: false,
    renameBoardDraft: { id: null, name: '' },

    openRenameBoard(b) {
      this.renameBoardDraft = { id: b.id, name: b.name };
      this.renameBoardModalOpen = true;
    },

    async submitRenameBoard() {
      if (!this.canEdit) return;
      const { id, name } = this.renameBoardDraft;
      if (!id || !name?.trim()) return;
      try {
        const updated = await apiFetch(`/api/raiox/boards/${id}`, {
          method: 'PATCH',
          body: JSON.stringify({ name: name.trim() }),
        });
        await this.loadBoards();
        if (this.currentBoard?.id === id) this.currentBoard = updated;
        this.renameBoardModalOpen = false;
        if (window.toast) toast('Dashboard renomeado.', 'success');
      } catch (e) {
        if (window.toast) toast(`Erro: ${e.message}`, 'error');
      }
    },

    async deleteBoard(b) {
      if (!this.canEdit) return;
      if (!confirm(`Excluir o dashboard "${b.name}" e todos os seus charts? Essa ação não pode ser desfeita.`)) return;
      try {
        await apiFetch(`/api/raiox/boards/${b.id}`, { method: 'DELETE' });
        await this.loadBoards();
        if (this.currentBoard?.id === b.id) {
          this.currentBoard = null;
          this.charts = [];
          history.replaceState({}, '', '/raiox');
        }
        if (window.toast) toast('Dashboard excluído.', 'success');
      } catch (e) {
        if (window.toast) toast(`Erro: ${e.message}`, 'error');
      }
    },

    async createBoard() {
      try {
        const restricted = this.newBoardScope === 'restricted';
        const payload = {
          name: this.newBoard.name,
          description: this.newBoard.description,
          cover_emoji: this.newBoard.cover_emoji,
          is_shared: !restricted,
          allowed_roles: restricted ? this.newBoard.allowed_roles : [],
          allowed_departments: restricted ? this.newBoard.allowed_departments : [],
        };
        const created = await apiFetch('/api/raiox/boards', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        await this.loadBoards();
        this.newBoardModalOpen = false;
        this.newBoard = { name: '', description: '', is_shared: true, cover_emoji: '🩻', allowed_roles: [], allowed_departments: [] };
        this.newBoardScope = 'shared';
        await this.selectBoard(created.id);
        if (window.toast) toast('Dashboard criado.', 'success');
      } catch (e) {
        if (window.toast) toast(`Erro: ${e.message}`, 'error');
      }
    },

    // ---------- Modal "Novo Chart" — state machine ----------
    get ncFilteredTables() {
      const q = (this.nc.tableFilter || '').trim().toLowerCase();
      if (!q) return this.tables;
      return this.tables.filter(t =>
        t.name.toLowerCase().includes(q) ||
        t.label.toLowerCase().includes(q),
      );
    },

    ncOpenModal(chartToEdit = null) {
      this.editingChartId = chartToEdit?.id || null;
      if (chartToEdit) {
        // Pre-popula com os dados do chart existente
        this.newChart = {
          title: chartToEdit.title || '',
          chart_type: chartToEdit.chart_type,
          span_cols: chartToEdit.span_cols,
          span_rows: chartToEdit.span_rows,
          skill_path: chartToEdit.skill_path || '',
          query_spec: {
            table: chartToEdit.query_spec?.table || '',
            label_column: chartToEdit.query_spec?.label_column || '',
            value_column: chartToEdit.query_spec?.value_column || '',
            value_expr: chartToEdit.query_spec?.value_expr || '',
            value_label: chartToEdit.query_spec?.value_label || '',
            aggregate: chartToEdit.query_spec?.aggregate || 'count',
            order_by: chartToEdit.query_spec?.order_by || 'value_desc',
            limit: chartToEdit.query_spec?.limit || 30,
          },
        };
      } else {
        this.newChart = _defaultNewChart();
      }
      this.nc = {
        tableFilter: '',
        expandedTables: chartToEdit ? { [chartToEdit.query_spec?.table]: true } : {},
        columnTypes: {},
        previewSeries: null,
        previewLoading: false,
        previewError: null,
        copilotLoading: false,
        copilotRationale: '',
        intentHint: '',
        derivedOpen: false,
        derivedDraft: { name: '', expr: '' },
        lastEditedRole: '',
      };
      this.newChartModalOpen = true;
      // Se está editando, dispara preview imediato
      if (chartToEdit) {
        this.$nextTick(() => this.ncRefreshPreview());
      }
    },

    openEditChart(chart) {
      this.ncOpenModal(chart);
    },

    ncSelectTable(tableName) {
      this.newChart.query_spec.table = tableName;
      this.newChart.query_spec.label_column = '';
      this.newChart.query_spec.value_column = '';
      this.newChart.query_spec.value_expr = '';
      this.newChart.query_spec.value_label = '';
      this.nc.expandedTables = { [tableName]: true };
      this.nc.previewSeries = null;
      this.nc.previewError = null;
      this.nc.columnTypes = {};
    },

    /** Click em coluna: alterna entre Label (1º click) ou Value (2º click).
     *  Heurística: se o último editado foi 'label', próximo vira 'value'. */
    ncToggleColumn(tableName, col) {
      if (this.newChart.query_spec.table !== tableName) {
        this.ncSelectTable(tableName);
      }
      const qs = this.newChart.query_spec;
      const isNumeric = ['INT','REAL','FLOAT','NUMERIC','DECIMAL'].some(t => (col.type || '').toUpperCase().includes(t));
      // Se está como label, remove
      if (qs.label_column === col.name) {
        qs.label_column = '';
        this.nc.lastEditedRole = '';
        return;
      }
      // Se está como value, remove
      if (qs.value_column === col.name && !qs.value_expr) {
        qs.value_column = '';
        this.nc.lastEditedRole = '';
        return;
      }
      // Decide se vai pra label ou value
      const wantValue = (this.nc.lastEditedRole === 'label') || (!qs.label_column && isNumeric);
      if (wantValue && !qs.label_column) {
        // não tem label ainda — vai pra label
        qs.label_column = col.name;
        this.nc.lastEditedRole = 'label';
      } else if (wantValue) {
        qs.value_column = col.name;
        qs.value_expr = '';
        qs.value_label = '';
        this.nc.lastEditedRole = 'value';
      } else if (!qs.label_column) {
        qs.label_column = col.name;
        this.nc.lastEditedRole = 'label';
      } else {
        // já tem label, vira value
        qs.value_column = col.name;
        qs.value_expr = '';
        qs.value_label = '';
        this.nc.lastEditedRole = 'value';
      }
    },

    ncColumnRole(tableName, col) {
      if (this.newChart.query_spec.table !== tableName) return '';
      const qs = this.newChart.query_spec;
      if (qs.label_column === col.name) return 'L';
      if (qs.value_column === col.name && !qs.value_expr) return 'V';
      return '';
    },

    async ncCallCopilot() {
      const qs = this.newChart.query_spec;
      if (!qs.table) {
        if (window.toast) toast('Escolha uma tabela primeiro.', 'warning');
        return;
      }
      this.nc.copilotLoading = true;
      this.nc.copilotRationale = '';
      try {
        const r = await apiFetch('/api/raiox/copilot/recommend', {
          method: 'POST',
          body: JSON.stringify({
            table: qs.table,
            label_column: qs.label_column || null,
            value_column: qs.value_column || null,
            intent_hint: this.nc.intentHint || null,
          }),
        });
        this.newChart.chart_type = r.chart_type;
        qs.label_column = r.label_column || qs.label_column;
        qs.value_column = r.value_column || '';
        qs.value_expr = '';
        qs.aggregate = r.aggregate;
        if (!this.newChart.title) this.newChart.title = r.title;
        this.nc.columnTypes = r.column_types || {};
        this.nc.copilotRationale = r.rationale;
        await this.ncRefreshPreview();
        if (window.toast) toast('IA aplicou uma sugestão. Revise e ajuste.', 'success');
      } catch (e) {
        if (window.toast) toast(`IA: ${e.message}`, 'error');
      } finally {
        this.nc.copilotLoading = false;
      }
    },

    async ncRefreshPreview() {
      const qs = this.newChart.query_spec;
      if (!qs.table || !qs.label_column) {
        this.nc.previewSeries = null;
        this.nc.previewError = null;
        return;
      }
      this.nc.previewLoading = true;
      this.nc.previewError = null;
      try {
        const series = await apiFetch('/api/raiox/query', {
          method: 'POST',
          body: JSON.stringify({
            table: qs.table,
            label_column: qs.label_column,
            value_column: qs.value_column,
            value_expr: qs.value_expr,
            value_label: qs.value_label,
            aggregate: qs.aggregate,
            order_by: qs.order_by,
            limit: Math.min(qs.limit || 20, 30),
          }),
        });
        this.nc.previewSeries = series;
        // Espera até 5 ticks para o x-show revelar o div #nc-preview
        for (let i = 0; i < 5; i++) {
          await this.$nextTick();
          const el = document.getElementById('nc-preview');
          if (el && el.offsetWidth > 0 && window.Plotly) {
            renderRaioxChart('nc-preview', this.newChart.chart_type, series);
            return;
          }
          await new Promise(r => setTimeout(r, 50));
        }
        // Fallback: renderiza mesmo se offsetWidth=0; Plotly aceita responsive
        const el = document.getElementById('nc-preview');
        if (el && window.Plotly) {
          renderRaioxChart('nc-preview', this.newChart.chart_type, series);
        }
      } catch (e) {
        this.nc.previewError = e.message;
        this.nc.previewSeries = null;
      } finally {
        this.nc.previewLoading = false;
      }
    },

    ncAddDerivedVariable() {
      const d = this.nc.derivedDraft;
      if (!d.name?.trim() || !d.expr?.trim()) return;
      const qs = this.newChart.query_spec;
      qs.value_column = '';
      qs.value_expr = d.expr.trim();
      qs.value_label = d.name.trim();
      this.nc.lastEditedRole = 'value';
      this.nc.derivedDraft = { name: '', expr: '' };
      this.nc.derivedOpen = false;
      this.ncRefreshPreview();
    },

    async createChart() {
      if (!this.currentBoard) return;
      const qs = this.newChart.query_spec;
      if (!qs.table || !qs.label_column) {
        if (window.toast) toast('Escolha tabela e coluna de label.', 'warning');
        return;
      }
      const isEdit = !!this.editingChartId;
      try {
        const title = this.newChart.title?.trim()
          || (qs.aggregate === 'count'
              ? `Contagem por ${qs.label_column}`
              : `${qs.aggregate.toUpperCase()}(${qs.value_label || qs.value_column || qs.value_expr || '*'}) por ${qs.label_column}`);

        if (isEdit) {
          const patch = {
            chart_type: this.newChart.chart_type,
            title,
            span_cols: this.newChart.span_cols,
            span_rows: this.newChart.span_rows,
            query_spec: { ...qs },
            skill_path: this.newChart.skill_path || '',
          };
          await apiFetch(`/api/raiox/charts/${this.editingChartId}`, {
            method: 'PATCH',
            body: JSON.stringify(patch),
          });
          this.charts = await apiFetch(`/api/raiox/boards/${this.currentBoard.id}/charts`);
          this.newChartModalOpen = false;
          const editedId = this.editingChartId;
          this.editingChartId = null;
          this.newChart = _defaultNewChart();
          await this.$nextTick();
          const c = this.charts.find(x => x.id === editedId);
          if (c) await this.renderChart(c);
          if (window.toast) toast('Chart atualizado.', 'success');
        } else {
          const occupied = new Set(this.charts.map(c => `${c.position_row},${c.position_col}`));
          let row = 0, col = 0, found = false;
          for (let r = 0; r < 10 && !found; r++) {
            for (let c = 0; c < 3 && !found; c++) {
              if (!occupied.has(`${r},${c}`)) { row = r; col = c; found = true; }
            }
          }
          const payload = {
            chart_type: this.newChart.chart_type,
            title,
            position_row: row,
            position_col: col,
            span_cols: this.newChart.span_cols,
            span_rows: this.newChart.span_rows,
            query_spec: { ...qs },
            plotly_config: {},
            skill_path: this.newChart.skill_path || '',
          };
          await apiFetch(`/api/raiox/boards/${this.currentBoard.id}/charts`, {
            method: 'POST',
            body: JSON.stringify(payload),
          });
          this.charts = await apiFetch(`/api/raiox/boards/${this.currentBoard.id}/charts`);
          this.newChartModalOpen = false;
          this.newChart = _defaultNewChart();
          await this.$nextTick();
          const last = this.charts[this.charts.length - 1];
          if (last) await this.renderChart(last);
          if (window.toast) toast('Chart adicionado.', 'success');
        }
      } catch (e) {
        if (window.toast) toast(`Erro: ${e.message}`, 'error');
      }
    },

    /** Abre o drawer e mostra o histórico de análises do board. Não dispara
     *  uma nova execução — usuário escolhe entre histórico ou "+ nova análise". */
    async openAnalysisDrawer() {
      if (!this.currentBoard) return;
      this.analysisDrawerOpen = true;
      this.analysisView = 'history';
      this.analysisResult = null;
      await this.loadAnalysisHistory();
    },

    async loadAnalysisHistory() {
      if (!this.currentBoard) return;
      this.analysisHistoryLoading = true;
      try {
        this.analysisHistory = await apiFetch(`/api/raiox/boards/${this.currentBoard.id}/analyses`);
      } catch (e) {
        this.analysisHistory = [];
        if (window.toast) toast(`Erro ao carregar histórico: ${e.message}`, 'error');
      } finally {
        this.analysisHistoryLoading = false;
      }
    },

    async openAnalysisDetail(analysisId) {
      this.analysisLoading = true;
      this.analysisView = 'detail';
      try {
        this.analysisResult = await apiFetch(`/api/raiox/analyses/${analysisId}`);
      } catch (e) {
        if (window.toast) toast(`Erro ao abrir análise: ${e.message}`, 'error');
        this.analysisView = 'history';
      } finally {
        this.analysisLoading = false;
      }
    },

    backToHistory() {
      this.analysisView = 'history';
      this.analysisResult = null;
    },

    async deleteAnalysis(analysisId, ev) {
      if (ev) { ev.stopPropagation(); ev.preventDefault(); }
      if (!this.canEdit) return;
      if (!confirm('Excluir esta análise do histórico? A ação não pode ser desfeita.')) return;
      try {
        await apiFetch(`/api/raiox/analyses/${analysisId}`, { method: 'DELETE' });
        this.analysisHistory = this.analysisHistory.filter(a => a.id !== analysisId);
        if (this.analysisResult?.id === analysisId) {
          this.analysisResult = null;
          this.analysisView = 'history';
        }
        if (window.toast) toast('Análise removida do histórico.', 'success');
      } catch (e) {
        if (window.toast) toast(`Erro: ${e.message}`, 'error');
      }
    },

    // ---------- Exportar análise → Apresentação VIP (módulo padrão da plataforma) ----------
    presentationExporting: false,

    async exportToPresentation() {
      if (!this.analysisResult || !this.currentBoard) return;
      if (!window.Plotly) {
        if (window.toast) toast('Plotly não carregado.', 'error');
        return;
      }
      this.presentationExporting = true;
      try {
        // 1) Captura PNG de cada chart visível no DOM via Plotly.toImage
        const visuals = [];
        for (const c of this.charts) {
          const el = document.getElementById(`raiox-chart-${c.id}`);
          if (!el) continue;
          // Skip se Plotly ainda não desenhou
          if (!el.querySelector('.plot-container')) continue;
          try {
            const dataUrl = await window.Plotly.toImage(el, {
              format: 'png',
              width: 1200,
              height: 700,
            });
            visuals.push({
              title: c.title || `${c.chart_type} · ${c.query_spec?.table || ''}`,
              type: 'GRÁFICO',
              image_b64: dataUrl,
              caption: c.query_spec
                ? `${c.query_spec.aggregate}(${c.query_spec.value_column || c.query_spec.value_label || '*'}) por ${c.query_spec.label_column}`
                : '',
              source_card_uid: c.id,
            });
          } catch (e) {
            // ignora chart que não conseguiu rasterizar
          }
        }

        // 2) Monta sections (1 por chart + 4 da síntese)
        const sections = [];
        for (const ca of (this.analysisResult.per_chart || [])) {
          if (!ca.analysis) continue;
          sections.push({
            title: ca.title || ca.chart_type,
            body: ca.analysis,
            source_card_uid: ca.chart_id,
          });
        }
        const synth = this.analysisResult.synthesis || {};
        if (synth.correlations) sections.push({ title: '🔗 Correlações', body: synth.correlations });
        if (synth.patterns) sections.push({ title: '📐 Padrões', body: synth.patterns });
        if (synth.risks) sections.push({ title: '⚠️ Riscos', body: synth.risks });
        if (synth.opportunities) sections.push({ title: '🚀 Oportunidades', body: synth.opportunities });

        // 3) Insights (resumo executivo)
        const totals = this.analysisResult.totals || {};
        const created = this.analysisResult.created_at
          ? new Date(this.analysisResult.created_at + 'Z').toLocaleString('pt-BR')
          : '';
        const insights = [
          { type: 'CONTEXTO', content: `Dashboard "${this.currentBoard.name}" · ${this.charts.length} chart(s) analisado(s)` },
          { type: 'CUSTO', content: `Modelo ${totals.model_used || '?'} · US$ ${(totals.cost || 0).toFixed(5)} · ${(totals.tokens_input || 0) + (totals.tokens_output || 0)} tokens` },
        ];
        if (this.analysisResult.username) {
          insights.unshift({ type: 'AUTOR', content: `Análise gerada por ${this.analysisResult.username}${created ? ' em ' + created : ''}` });
        }

        // 4) Salva apresentação
        const payload = {
          feature: 'raiox',
          case_number: String(this.currentBoard.id),
          title: `Apresentação VIP · ${this.currentBoard.name}`,
          subtitle: created
            ? `Análise com IA gerada em ${created}`
            : `Dashboard com ${this.charts.length} chart(s)`,
          insights,
          sections,
          visuals,
          tokens_input: totals.tokens_input || 0,
          tokens_output: totals.tokens_output || 0,
          cost_estimated: totals.cost || 0,
          model_used: totals.model_used || '',
        };

        const created_pres = await apiFetch('/api/presentations/', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        if (window.toast) toast(`Apresentação salva na Galeria — clique para abrir`, 'success');
        // Abre a apresentação na Galeria em nova aba
        if (created_pres?.id) {
          window.open(`/gallery/${created_pres.id}`, '_blank', 'noopener');
        }
      } catch (e) {
        if (window.toast) toast(`Falha ao exportar: ${e.message}`, 'error');
      } finally {
        this.presentationExporting = false;
      }
    },

    /** Roda uma nova análise com IA e persiste no histórico. */
    async runBoardAnalysis() {
      if (!this.currentBoard) return;
      if (!this.charts.length) {
        if (window.toast) toast('Adicione charts ao dashboard antes.', 'warning');
        return;
      }
      this.analysisDrawerOpen = true;
      this.analysisView = 'detail';
      this.analysisLoading = true;
      this.analysisResult = null;
      try {
        const r = await apiFetch(`/api/raiox/boards/${this.currentBoard.id}/analyze`, {
          method: 'POST',
          body: JSON.stringify({}),
        });
        this.analysisResult = r;
        // Atualiza histórico para incluir a nova
        await this.loadAnalysisHistory();
      } catch (e) {
        if (window.toast) toast(`Erro na análise: ${e.message}`, 'error');
        this.analysisResult = { error: e.message };
      } finally {
        this.analysisLoading = false;
      }
    },

    async deleteChart(chartId) {
      if (!confirm('Remover este chart?')) return;
      try {
        await apiFetch(`/api/raiox/charts/${chartId}`, { method: 'DELETE' });
        this.charts = this.charts.filter(c => c.id !== chartId);
        delete this.chartStates[chartId];
        if (window.toast) toast('Chart removido.', 'success');
      } catch (e) {
        if (window.toast) toast(`Erro: ${e.message}`, 'error');
      }
    },

    // ---------- Drag & Drop (F1.2) ----------
    dragSourceId: null,

    onDragStart(chartId, ev) {
      if (!this.canEdit) return;
      this.dragSourceId = chartId;
      ev.dataTransfer.effectAllowed = 'move';
    },

    onDragOver(targetChartId, ev) {
      if (!this.canEdit || !this.dragSourceId) return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = 'move';
      if (targetChartId !== this.dragSourceId) {
        this.dragOverChartId = targetChartId;
      }
    },

    onDragLeave(targetChartId) {
      if (this.dragOverChartId === targetChartId) {
        this.dragOverChartId = null;
      }
    },

    onDragEnd() {
      this.dragSourceId = null;
      this.dragOverChartId = null;
    },

    async onDrop(targetChartId, ev) {
      ev.preventDefault();
      if (!this.canEdit) return;
      const sourceId = this.dragSourceId;
      this.dragSourceId = null;
      this.dragOverChartId = null;
      if (!sourceId || sourceId === targetChartId) return;
      const source = this.charts.find(c => c.id === sourceId);
      const target = this.charts.find(c => c.id === targetChartId);
      if (!source || !target) return;
      // Swap de posições — atomic patch nos dois charts
      const sPos = { row: source.position_row, col: source.position_col };
      const tPos = { row: target.position_row, col: target.position_col };
      try {
        await apiFetch(`/api/raiox/charts/${source.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ position_row: tPos.row, position_col: tPos.col }),
        });
        await apiFetch(`/api/raiox/charts/${target.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ position_row: sPos.row, position_col: sPos.col }),
        });
        source.position_row = tPos.row; source.position_col = tPos.col;
        target.position_row = sPos.row; target.position_col = sPos.col;
        if (window.toast) toast('Posições trocadas.', 'success');
      } catch (e) {
        if (window.toast) toast(`Erro: ${e.message}`, 'error');
      }
    },

    async resizeChart(chartId, span_cols, span_rows) {
      if (!this.canEdit) return;
      const chart = this.charts.find(c => c.id === chartId);
      if (!chart) return;
      try {
        await apiFetch(`/api/raiox/charts/${chartId}`, {
          method: 'PATCH',
          body: JSON.stringify({ span_cols, span_rows }),
        });
        chart.span_cols = span_cols;
        chart.span_rows = span_rows;
        await this.$nextTick();
        await this.renderChart(chart);
      } catch (e) {
        if (window.toast) toast(`Erro: ${e.message}`, 'error');
      }
    },

    // ---------- Filtros globais (F1.5) ----------
    globalFiltersOpen: false,
    newGlobalFilter: { table: '', column: '', value: '' },

    async addGlobalFilter() {
      if (!this.canEdit || !this.currentBoard) return;
      const f = this.newGlobalFilter;
      if (!f.column || f.value === '') return;
      const filters = { ...(this.currentBoard.filters || {}) };
      filters.global = filters.global || [];
      filters.global.push({ column: f.column, op: '=', value: f.value });
      // Pré-checa cobertura
      const covered = this.chartsCoveredBy(f.column);
      const totalCharts = this.charts.length;
      try {
        const updated = await apiFetch(`/api/raiox/boards/${this.currentBoard.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ filters }),
        });
        this.currentBoard.filters = updated.filters;
        this.newGlobalFilter = { table: '', column: '', value: '' };
        await this.renderAllCharts();
        if (window.toast) {
          if (covered === 0) {
            toast(`Filtro salvo, mas a coluna "${f.column}" não existe em nenhum dos ${totalCharts} chart(s) deste board — nada foi afetado.`, 'warn');
          } else if (covered < totalCharts) {
            toast(`Filtro aplicado em ${covered}/${totalCharts} chart(s) — os demais não têm a coluna "${f.column}".`, 'success');
          } else {
            toast(`Filtro aplicado em todos os ${totalCharts} chart(s).`, 'success');
          }
        }
      } catch (e) {
        if (window.toast) toast(`Erro: ${e.message}`, 'error');
      }
    },

    async removeGlobalFilter(idx) {
      if (!this.canEdit || !this.currentBoard) return;
      const filters = { ...(this.currentBoard.filters || {}) };
      filters.global = (filters.global || []).slice();
      filters.global.splice(idx, 1);
      try {
        const updated = await apiFetch(`/api/raiox/boards/${this.currentBoard.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ filters }),
        });
        this.currentBoard.filters = updated.filters;
        await this.renderAllCharts();
      } catch (e) {
        if (window.toast) toast(`Erro: ${e.message}`, 'error');
      }
    },

    async renameChart(chartId, newTitle) {
      if (!this.canEdit) return;
      const chart = this.charts.find(c => c.id === chartId);
      if (!chart || chart.title === newTitle) return;
      try {
        await apiFetch(`/api/raiox/charts/${chartId}`, {
          method: 'PATCH',
          body: JSON.stringify({ title: newTitle }),
        });
        chart.title = newTitle;
      } catch (e) {
        if (window.toast) toast(`Erro: ${e.message}`, 'error');
      }
    },
  };
};
