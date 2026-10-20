let selectedAssetId = new URLSearchParams(location.search).get("asset_id") || localStorage.getItem("portfolio.selectedAssetId") || "";
let dataset = "history";
let columns = [];
let records = [];
let editingRowIndex = null;

const selector = document.getElementById("asset-selector");
const resultBox = document.getElementById("run-history-result");
const editor = document.getElementById("record-editor");
const editorFields = document.getElementById("editor-fields");
const recordForm = document.getElementById("record-form");
const labels = {
  history: { title: "指标历史", description: "每次生成报告时写入的指标快照。" },
  decisions: { title: "决策历史", description: "每次生成报告时写入的策略判断与通知状态。" },
};

const hiddenColumns = new Set(["symbol", "tracking_code", "action_type"]);
const historyDisplayOrder = [
  "generated_at",
  "daily_and_last_buy_change_summary",
  "recent_change_summary",
  "period_drawdown_summary",
  "rebound_and_recovery_summary",
  "current_drawdown_summary",
  "current_rise_summary",
];
const historySummaryDefinitions = {
  daily_and_last_buy_change_summary: {
    fields: ["daily_change_pct", "drawdown_from_last_buy_pct"],
    labels: ["当日", "较上次买入"],
  },
  rebound_and_recovery_summary: {
    fields: ["rebound_pct", "recovery_pct"],
    labels: ["反弹幅度", "反弹修复"],
  },
  recent_change_summary: {
    fields: ["five_day_change_pct", "ten_day_change_pct", "twenty_day_change_pct"],
    labels: ["近 5 日", "近 10 日", "近 20 日"],
  },
  period_drawdown_summary: {
    fields: ["one_month_drawdown_pct", "three_month_drawdown_pct", "six_month_drawdown_pct"],
    labels: ["近 1 月", "近 3 月", "近 6 月"],
  },
  current_drawdown_summary: {
    fields: ["current_drawdown_pct", "current_drawdown_percentile"],
    labels: ["回撤", "分位"],
  },
  current_rise_summary: {
    fields: ["current_rise_pct", "current_rise_percentile"],
    labels: ["上涨", "分位"],
  },
};
const decisionDisplayOrder = [
  "generated_at", "status", "reasons",
];
const columnLabels = {
  daily_and_last_buy_change_summary: "当日 / 较上次买入涨跌幅（%）",
  recent_change_summary: "近 5 / 10 / 20 日涨跌幅（%）",
  period_drawdown_summary: "近 1 / 3 / 6 月回撤（%）",
  current_drawdown_summary: "当前回撤及分位（%）",
  current_rise_summary: "当前上涨及分位（%）",
  rebound_and_recovery_summary: "反弹幅度 / 修复（%）",
  generated_at: "生成时间",
  name: "资产名称",
  price: "价格",
  daily_change_pct: "当日涨跌幅（%）",
  five_day_change_pct: "近 5 日涨跌幅（%）",
  ten_day_change_pct: "近 10 日涨跌幅（%）",
  twenty_day_change_pct: "近 20 日涨跌幅（%）",
  one_month_drawdown_pct: "近 1 月回撤（%）",
  three_month_drawdown_pct: "近 3 月回撤（%）",
  six_month_drawdown_pct: "近 6 月回撤（%）",
  drawdown_from_last_buy_pct: "较上次买入涨跌幅（%）",
  consecutive_down_days: "连续下跌天数",
  consecutive_up_days: "连续上涨天数",
  rebound_pct: "反弹幅度（%）",
  is_new_10d_low: "近 10 日新低",
  stable_days: "横盘天数",
  cycle_sample_size: "周期样本数",
  current_drawdown_pct: "当前回撤（%）",
  current_drawdown_percentile: "当前回撤分位（%）",
  current_rise_pct: "当前上涨幅度（%）",
  current_rise_percentile: "当前上涨分位（%）",
  recovery_pct: "反弹修复（%）",
  fund_flow_status: "资金流状态",
  invested_amount: "投入成本（元）",
  average_cost: "平均成本",
  profit_pct: "收益率（%）",
  profit_amount: "收益金额（元）",
  data_source: "数据来源",
  status: "策略状态",
  suggested_amount: "建议金额（元）",
  reasons: "判断原因",
  should_notify: "发送通知",
  reason_code: "原因代码",
};
const columnHelp = {
  rebound_and_recovery_summary: "反弹幅度：最近一段连续上涨行情从起涨日到当前的累计涨幅。反弹修复：当前价格已收复上一段“确认高点→确认低点”跌幅的比例；例如从 100 跌到 80 后回到 90，修复为 50%。",
};
const numericColumns = new Set([
  "price", "daily_change_pct", "five_day_change_pct", "ten_day_change_pct", "twenty_day_change_pct",
  "one_month_drawdown_pct", "three_month_drawdown_pct", "six_month_drawdown_pct",
  "drawdown_from_last_buy_pct", "rebound_pct", "current_drawdown_pct", "current_drawdown_percentile",
  "current_rise_pct", "current_rise_percentile", "recovery_pct", "invested_amount", "average_cost",
  "profit_pct", "profit_amount", "suggested_amount",
]);
const statusLabels = {
  OBSERVE: "观察",
  REVIEW_BUY: "人工复核加仓",
  REVIEW_SELL: "人工复核下行风险",
  PARTIAL_SELL: "部分卖出",
};

const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[character]));
const api = path => path + "?asset_id=" + encodeURIComponent(selectedAssetId);

function visibleColumns() {
  if (dataset === "decisions") {
    return decisionDisplayOrder.filter(column => columns.includes(column));
  }
  return historyDisplayOrder.filter(column => {
    const summary = historySummaryDefinitions[column];
    return summary ? summary.fields.some(field => columns.includes(field)) : columns.includes(column);
  });
}

function columnClass(column) {
  return "history-column-" + String(column).replace(/[^a-z0-9_-]/gi, "-");
}

function columnHeading(column) {
  const label = escapeHtml(columnLabels[column] || column);
  const help = columnHelp[column];
  if (!help) return label;
  return `<span class="history-tooltip" tabindex="0" aria-label="${escapeHtml(help)}">${label}<span class="history-tooltip-icon" aria-hidden="true">?</span><span class="history-tooltip-content" role="tooltip">${escapeHtml(help)}</span></span>`;
}

function formatTimestamp(value) {
  const match = String(value ?? "").match(/^(\d{4}-\d{2}-\d{2})(?:[T\s]+(\d{2}:\d{2}:\d{2}))?/);
  if (!match) return escapeHtml(value ?? "-");
  return `<time class="table-datetime"><span>${match[1]}</span>${match[2] ? `<span>${match[2]}</span>` : ""}</time>`;
}

function formatValue(column, value) {
  if (value === null || value === undefined || value === "") return "-";
  if (column === "generated_at") return formatTimestamp(value);
  if (numericColumns.has(column)) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(4) : escapeHtml(value);
  }
  if (column === "should_notify" || column === "is_new_10d_low") {
    return ["true", "1"].includes(String(value).toLowerCase()) ? "是" : "否";
  }
  if (column === "status") return escapeHtml(statusLabels[String(value).toUpperCase()] || value);
  if (column === "reasons") {
    return `<div class="reason-cell">${escapeHtml(value).replace(/；\s*/g, "；<br>")}</div>`;
  }
  return escapeHtml(value);
}

function formatRecordValue(column, record) {
  const summary = historySummaryDefinitions[column];
  if (!summary) return formatValue(column, record.values?.[column]);
  const lines = summary.fields.map((field, index) => (
    `<span>${summary.labels[index]} / ${formatValue(field, record.values?.[field])}</span>`
  ));
  return `<div class="summary-cell">${lines.join("")}</div>`;
}

function showResult(data) {
  resultBox.hidden = false;
  resultBox.className = "result-box " + (data.success ? "success" : "error");
  resultBox.textContent = data.message || (data.success ? "操作完成。" : "操作失败。");
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) data.success = false;
  return data;
}

function selectedRows() {
  return [...document.querySelectorAll("[data-row-select]:checked")].map(input => Number(input.value));
}

function syncSelection() {
  const selected = selectedRows();
  const button = document.getElementById("delete-selected");
  button.disabled = selected.length === 0;
  button.textContent = selected.length ? "删除已选记录（" + selected.length + "）" : "删除已选记录";
}

function renderRecords() {
  const head = document.getElementById("records-head");
  const body = document.getElementById("records-body");
  const empty = document.getElementById("records-empty");
  const displayColumns = visibleColumns();
  head.innerHTML = "<tr><th><input id=\"select-all-records\" type=\"checkbox\" aria-label=\"全选\"></th>"
    + displayColumns.map(column => `<th class="${columnClass(column)}">${columnHeading(column)}</th>`).join("") + "<th class=\"history-column-actions\">操作</th></tr>";
  empty.hidden = records.length > 0;
  body.innerHTML = records.length ? [...records].reverse().map(record => {
    const values = displayColumns.map(column => `<td class="${columnClass(column)}">${formatRecordValue(column, record)}</td>`).join("");
    return "<tr><td><input type=\"checkbox\" data-row-select value=\"" + record.row_index
      + "\" aria-label=\"选择记录\"></td>" + values
      + "<td class=\"history-column-actions\"><button type=\"button\" class=\"secondary-button history-view-link\" data-edit-row=\"" + record.row_index
      + "\">查看 / 编辑</button></td></tr>";
  }).join("") : "";
  document.getElementById("select-all-records").addEventListener("change", event => {
    document.querySelectorAll("[data-row-select]").forEach(input => { input.checked = event.target.checked; });
    syncSelection();
  });
  document.querySelectorAll("[data-row-select]").forEach(input => input.addEventListener("change", syncSelection));
  document.querySelectorAll("[data-edit-row]").forEach(button => button.addEventListener("click", () => {
    openEditor(records.find(record => record.row_index === Number(button.dataset.editRow)));
  }));
  syncSelection();
}

function openEditor(record = null) {
  editingRowIndex = record ? record.row_index : null;
  document.getElementById("editor-title").textContent = record ? "查看 / 编辑记录" : "新增记录";
  editorFields.innerHTML = columns.map(column => {
    const value = record ? (record.values?.[column] ?? "") : "";
    const isMultiline = ["reasons", "data_source"].includes(column);
    const control = isMultiline
      ? "<textarea name=\"" + escapeHtml(column) + "\" rows=\"3\">" + escapeHtml(value) + "</textarea>"
      : "<input name=\"" + escapeHtml(column) + "\" value=\"" + escapeHtml(value) + "\">";
    return "<label>" + escapeHtml(column) + control + "</label>";
  }).join("");
  editor.showModal();
}

function closeEditor() {
  if (editor.open) editor.close();
  editorFields.innerHTML = "";
  editingRowIndex = null;
}

async function loadRecords() {
  const data = await requestJson(api("/api/run-history") + "&dataset=" + encodeURIComponent(dataset));
  if (!data.success) {
    showResult(data);
    return;
  }
  columns = data.columns || [];
  records = data.records || [];
  document.querySelector(".run-history-page").dataset.dataset = dataset;
  document.getElementById("dataset-title").textContent = labels[dataset].title;
  document.getElementById("dataset-description").textContent = labels[dataset].description;
  renderRecords();
}

async function loadAssets() {
  const data = await requestJson("/api/assets");
  const assets = data.assets || [];
  selector.innerHTML = assets.map(asset => "<option value=\"" + escapeHtml(asset.id) + "\">"
    + escapeHtml(asset.name) + "（" + escapeHtml(asset.asset_code) + "）</option>").join("");
  if (!assets.some(asset => asset.id === selectedAssetId)) selectedAssetId = assets[0]?.id || "";
  selector.value = selectedAssetId;
  localStorage.setItem("portfolio.selectedAssetId", selectedAssetId);
  document.getElementById("back-to-asset-manage").href = selectedAssetId
    ? "/asset/manage?asset_id=" + encodeURIComponent(selectedAssetId)
    : "/asset/manage";
}

selector.addEventListener("change", async () => {
  selectedAssetId = selector.value;
  localStorage.setItem("portfolio.selectedAssetId", selectedAssetId);
  history.replaceState(null, "", "?asset_id=" + encodeURIComponent(selectedAssetId));
  closeEditor();
  await loadRecords();
});

document.querySelectorAll("[data-dataset]").forEach(button => button.addEventListener("click", async () => {
  dataset = button.dataset.dataset;
  document.querySelectorAll("[data-dataset]").forEach(item => item.classList.toggle("active", item === button));
  closeEditor();
  await loadRecords();
}));

document.getElementById("add-record").addEventListener("click", () => openEditor());
document.getElementById("cancel-edit").addEventListener("click", closeEditor);
editor.addEventListener("click", event => {
  if (event.target === editor) closeEditor();
});

recordForm.addEventListener("submit", async event => {
  event.preventDefault();
  const values = Object.fromEntries(columns.map(column => [column, recordForm.elements[column]?.value ?? ""]));
  const data = await requestJson(api("/api/run-history/save"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dataset, row_index: editingRowIndex, values }),
  });
  showResult(data);
  if (data.success) {
    closeEditor();
    columns = data.columns || columns;
    records = data.records || [];
    renderRecords();
  }
});

document.getElementById("delete-selected").addEventListener("click", async () => {
  const rowIndices = selectedRows();
  if (!rowIndices.length || !confirm("确定删除所选 " + rowIndices.length + " 条" + labels[dataset].title + "记录？此操作不可恢复。")) return;
  const data = await requestJson(api("/api/run-history/delete"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dataset, row_indices: rowIndices }),
  });
  showResult(data);
  if (data.success) {
    columns = data.columns || columns;
    records = data.records || [];
    renderRecords();
  }
});

(async () => {
  try {
    await loadAssets();
    if (selectedAssetId) await loadRecords();
  } catch (error) {
    showResult({ success: false, message: "读取失败：" + error });
  }
})();
