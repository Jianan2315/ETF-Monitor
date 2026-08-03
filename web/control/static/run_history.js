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

const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[character]));
const api = path => path + "?asset_id=" + encodeURIComponent(selectedAssetId);

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
  head.innerHTML = "<tr><th><input id=\"select-all-records\" type=\"checkbox\" aria-label=\"全选\"></th>"
    + columns.map(column => "<th>" + escapeHtml(column) + "</th>").join("") + "<th>操作</th></tr>";
  empty.hidden = records.length > 0;
  body.innerHTML = records.length ? [...records].reverse().map(record => {
    const values = columns.map(column => "<td>" + escapeHtml(record.values?.[column] ?? "-") + "</td>").join("");
    return "<tr><td><input type=\"checkbox\" data-row-select value=\"" + record.row_index
      + "\" aria-label=\"选择记录\"></td>" + values
      + "<td><button type=\"button\" class=\"secondary-button\" data-edit-row=\"" + record.row_index
      + "\">编辑</button></td></tr>";
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
  document.getElementById("editor-title").textContent = record ? "编辑记录" : "新增记录";
  editorFields.innerHTML = columns.map(column => {
    const value = record ? (record.values?.[column] ?? "") : "";
    const isMultiline = ["reasons", "data_source"].includes(column);
    const control = isMultiline
      ? "<textarea name=\"" + escapeHtml(column) + "\" rows=\"3\">" + escapeHtml(value) + "</textarea>"
      : "<input name=\"" + escapeHtml(column) + "\" value=\"" + escapeHtml(value) + "\">";
    return "<label>" + escapeHtml(column) + control + "</label>";
  }).join("");
  editor.hidden = false;
  editor.scrollIntoView({ behavior: "smooth", block: "start" });
}

function closeEditor() {
  editor.hidden = true;
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
