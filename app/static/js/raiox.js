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
    query_spec: {
      table: '',
      label_column: '',
      value_column: '',
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
    newBoard: { name: '', description: '', is_shared: true, cover_emoji: '🩻' },
    newChart: _defaultNewChart(),
    // Crossfilter: pilha de predicados aplicados a todos os charts compatíveis
    // do board. Cada item: {column, value, sourceChartId, sourceChartTitle}.
    crossfilters: [],

    async init() {
      try {
        await Promise.all([this.loadTables(), this.loadBoards()]);
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
        if (window.toast) toast(`Erro ao abrir prancheta: ${e.message}`, 'error');
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

    async createBoard() {
      try {
        const created = await apiFetch('/api/raiox/boards', {
          method: 'POST',
          body: JSON.stringify(this.newBoard),
        });
        await this.loadBoards();
        this.newBoardModalOpen = false;
        this.newBoard = { name: '', description: '', is_shared: true, cover_emoji: '🩻' };
        await this.selectBoard(created.id);
        if (window.toast) toast('Prancheta criada.', 'success');
      } catch (e) {
        if (window.toast) toast(`Erro: ${e.message}`, 'error');
      }
    },

    async createChart() {
      if (!this.currentBoard) return;
      try {
        const occupied = new Set(this.charts.map(c => `${c.position_row},${c.position_col}`));
        let row = 0, col = 0, found = false;
        for (let r = 0; r < 10 && !found; r++) {
          for (let c = 0; c < 3 && !found; c++) {
            if (!occupied.has(`${r},${c}`)) { row = r; col = c; found = true; }
          }
        }
        const payload = {
          ...this.newChart,
          position_row: row,
          position_col: col,
          plotly_config: {},
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
      } catch (e) {
        if (window.toast) toast(`Erro: ${e.message}`, 'error');
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

    onDragOver(ev) {
      if (!this.canEdit || !this.dragSourceId) return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = 'move';
    },

    async onDrop(targetChartId, ev) {
      ev.preventDefault();
      if (!this.canEdit) return;
      const sourceId = this.dragSourceId;
      this.dragSourceId = null;
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
      try {
        const updated = await apiFetch(`/api/raiox/boards/${this.currentBoard.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ filters }),
        });
        this.currentBoard.filters = updated.filters;
        this.newGlobalFilter = { table: '', column: '', value: '' };
        await this.renderAllCharts();
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
