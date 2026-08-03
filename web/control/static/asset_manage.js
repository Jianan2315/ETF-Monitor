let selectedAssetId = new URLSearchParams(location.search).get("asset_id") || localStorage.getItem("portfolio.selectedAssetId") || "";

const form = document.getElementById("asset-form");
const assetResult = document.getElementById("asset-result");
const txResult = document.getElementById("tx-result");
const transactionList = document.getElementById("transaction-list");
const transactionCount = document.getElementById("transaction-count");
const selectAllTransactions = document.getElementById("select-all-transactions");
const deleteSelectedTransactions = document.getElementById("delete-selected-transactions");
const strategyKeys = ["strategy_type", "normal_add_amount", "enhanced_add_amount"];
let currentChartPayload = null;
let selectedChartRange = "ALL";

const api = path => `${path}?asset_id=${encodeURIComponent(selectedAssetId)}`;
const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[character]));

function setv(name, value) {
  const field = form.elements[name];
  if (field) field.type === "checkbox" ? field.checked = value !== false : field.value = value ?? "";
}

function getv(name) {
  const field = form.elements[name];
  return field.type === "checkbox" ? field.checked : field.value;
}

function showTxResult(data) {
  txResult.hidden = false;
  txResult.className = `result-box ${data.success ? "success" : "error"}`;
  txResult.textContent = data.message || (data.success ? "操作完成。" : "操作失败。");
}

function updateLinks() {
  const paths = {
    "asset-report-link":"/report",
    "asset-history-link":"/history",
    "asset-schedule-link":"/schedules",
    "asset-strategy-replay-link":"/strategy-replay",
    "asset-trigger-link":"/trigger-conditions",
  };
  Object.entries(paths).forEach(([id, path]) => {
    document.getElementById(id).href = `${path}?asset_id=${encodeURIComponent(selectedAssetId)}`;
  });
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok && data.success !== false) data.success = false;
  return data;
}

async function loadAssetList() {
  const data = await requestJson("/api/assets");
  const assets = data.assets || [];
  const selector = document.getElementById("asset-selector");
  selector.innerHTML = assets.map(asset => (
    `<option value="${escapeHtml(asset.id)}">${escapeHtml(asset.name)}（${escapeHtml(asset.asset_code)}）</option>`
  )).join("");
  if (!assets.some(asset => asset.id === selectedAssetId)) selectedAssetId = assets[0]?.id || "";
  selector.value = selectedAssetId;
  localStorage.setItem("portfolio.selectedAssetId", selectedAssetId);
}

async function loadSettings() {
  const data = await requestJson(api("/api/settings"));
  const asset = data.asset || {};
  const strategy = asset.strategy || {};
  document.getElementById("page-title").textContent = asset.name || "资产";
  setv("asset_name", asset.name);
  setv("asset_code", asset.asset_code);
  setv("tracking_name", asset.tracking_name);
  setv("tracking_code", asset.tracking_code || asset.symbol);
  strategyKeys.forEach(key => setv(key, strategy[key]));
}

function selectedTransactionIds() {
  return [...transactionList.querySelectorAll("[data-transaction-select]:checked")].map(input => input.value);
}

function syncTransactionSelection() {
  const checkboxes = [...transactionList.querySelectorAll("[data-transaction-select]")];
  const selectedCount = selectedTransactionIds().length;
  selectAllTransactions.checked = checkboxes.length > 0 && selectedCount === checkboxes.length;
  selectAllTransactions.indeterminate = selectedCount > 0 && selectedCount < checkboxes.length;
  deleteSelectedTransactions.disabled = selectedCount === 0;
  deleteSelectedTransactions.textContent = selectedCount ? `删除已选记录（${selectedCount}）` : "删除已选记录";
}

const transactionStatusLabels = {
  PENDING: "待补充价格",
  PENDING_CLOSE: "等待交易日收盘",
  SETTLED: "已结算",
};

function transactionStatusLabel(status) {
  const normalized = String(status || "").toUpperCase();
  return transactionStatusLabels[normalized] || normalized || "-";
}

function renderTransactions(records) {
  transactionCount.textContent = records.length ? `（${records.length} 条）` : "";
  selectAllTransactions.checked = false;
  selectAllTransactions.indeterminate = false;
  deleteSelectedTransactions.disabled = true;
  deleteSelectedTransactions.textContent = "删除已选记录";
  transactionList.innerHTML = records.length ? records.map(record => {
    const transactionType = record.type === "BUY" ? "买入" : "卖出";
    const note = record.note ? `<p>${escapeHtml(record.note)}</p>` : "";
    return `<article class="transaction-item">
      <div class="transaction-item-main">
        <label class="transaction-select"><input type="checkbox" data-transaction-select value="${escapeHtml(record.id)}" aria-label="选择 ${escapeHtml(record.date)} 的${transactionType}记录"></label>
        <div class="transaction-item-content">
          <div class="transaction-title-row"><strong>${escapeHtml(record.date || "-")} · ${transactionType}</strong></div>
          <p>金额：${escapeHtml(record.amount ?? "-")} · 状态：${escapeHtml(transactionStatusLabel(record.status))}</p>
          ${note}
        </div>
        <button class="secondary-button" data-delete-tx="${escapeHtml(record.id)}">删除</button>
      </div>
    </article>`;
  }).join("") : "<p class='empty-state'>暂无交易记录</p>";
}

function chartRangeStart(lastDate, range) {
  if (range === "ALL") return "";
  const cutoff = new Date(`${lastDate}T00:00:00`);
  if (range.endsWith("Y")) cutoff.setFullYear(cutoff.getFullYear() - Number(range.slice(0, -1)));
  if (range.endsWith("M")) cutoff.setMonth(cutoff.getMonth() - Number(range.slice(0, -1)));
  if (range.endsWith("D")) cutoff.setDate(cutoff.getDate() - Number(range.slice(0, -1)));
  return cutoff.toISOString().slice(0, 10);
}

function drawTransactionChart() {
  const canvas = document.getElementById("transaction-chart");
  const empty = document.getElementById("transaction-chart-empty");
  const history = currentChartPayload?.history || [];
  const transactions = currentChartPayload?.transactions || [];
  const allPoints = history
    .map(point => ({ date: String(point.date || "").slice(0, 10), price: Number(point.price) }))
    .filter(point => point.date && Number.isFinite(point.price) && point.price > 0)
    .sort((left, right) => left.date.localeCompare(right.date));
  const rangeStart = allPoints.length ? chartRangeStart(allPoints.at(-1).date, selectedChartRange) : "";
  const points = rangeStart ? allPoints.filter(point => point.date >= rangeStart) : allPoints;

  if (points.length < 2) {
    canvas.hidden = true;
    empty.hidden = false;
    return;
  }
  canvas.hidden = false;
  empty.hidden = true;
  const wrap = canvas.parentElement;
  const width = Math.max(320, Math.floor(wrap.clientWidth));
  const height = 270;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  canvas.style.height = `${height}px`;
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);

  const padding = { top: 18, right: 18, bottom: 36, left: 62 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const prices = points.map(point => point.price);
  let minPrice = Math.min(...prices);
  let maxPrice = Math.max(...prices);
  const priceRange = maxPrice - minPrice || Math.max(maxPrice * 0.04, 1);
  minPrice -= priceRange * 0.08;
  maxPrice += priceRange * 0.08;
  const start = Date.parse(points[0].date);
  const end = Date.parse(points[points.length - 1].date);
  const timeRange = end - start || 1;
  const xFor = date => padding.left + ((Date.parse(date) - start) / timeRange) * chartWidth;
  const yFor = price => padding.top + ((maxPrice - price) / (maxPrice - minPrice)) * chartHeight;

  context.font = "12px system-ui, sans-serif";
  context.fillStyle = "#64748b";
  context.strokeStyle = "#e4eaf0";
  context.lineWidth = 1;
  for (let index = 0; index <= 4; index += 1) {
    const y = padding.top + (chartHeight * index) / 4;
    const price = maxPrice - ((maxPrice - minPrice) * index) / 4;
    context.beginPath();
    context.moveTo(padding.left, y);
    context.lineTo(width - padding.right, y);
    context.stroke();
    context.fillText(price.toFixed(2), 4, y + 4);
  }
  context.fillText(points[0].date, padding.left, height - 12);
  const endLabel = points[points.length - 1].date;
  context.fillText(endLabel, width - padding.right - context.measureText(endLabel).width, height - 12);

  context.beginPath();
  points.forEach((point, index) => {
    const x = xFor(point.date);
    const y = yFor(point.price);
    if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
  });
  context.strokeStyle = "#137a9b";
  context.lineWidth = 2;
  context.stroke();

  const priceByDate = new Map(points.map(point => [point.date, point.price]));
  transactions.forEach(transaction => {
    const date = String(transaction.date || "").slice(0, 10);
    const type = transaction.type;
    if (!priceByDate.has(date) || !["BUY", "SELL"].includes(type)) return;
    context.beginPath();
    context.arc(xFor(date), yFor(priceByDate.get(date)), 5, 0, Math.PI * 2);
    context.fillStyle = type === "BUY" ? "#d92d20" : "#1769aa";
    context.fill();
    context.strokeStyle = "#ffffff";
    context.lineWidth = 1.5;
    context.stroke();
  });
}

async function loadTransactionChart() {
  const data = await requestJson(api("/api/transactions/chart"));
  currentChartPayload = data.success === false ? { history: [], transactions: [] } : data;
  drawTransactionChart();
}

async function loadTransactions() {
  const data = await requestJson(api("/api/transactions"));
  renderTransactions(data.transactions || []);
  await loadTransactionChart();
}

async function reload() {
  updateLinks();
  await loadSettings();
  await loadTransactions();
}

async function saveAsset(includeStrategy) {
  const asset = {
    name: getv("asset_name"), asset_code: getv("asset_code"),
    tracking_name: getv("tracking_name"), tracking_code: getv("tracking_code"),
  };
  const strategy = includeStrategy ? Object.fromEntries(strategyKeys.map(key => [key, getv(key)])) : {};
  assetResult.hidden = false;
  assetResult.className = "result-box";
  assetResult.textContent = "正在保存…";
  const data = await requestJson(api("/api/settings/save"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asset_id: selectedAssetId, asset, strategy }),
  });
  assetResult.className = `result-box ${data.success ? "success" : "error"}`;
  assetResult.textContent = data.message;
  if (data.success) {
    await loadAssetList();
    await loadSettings();
  }
}

document.getElementById("asset-selector").addEventListener("change", async event => {
  selectedAssetId = event.target.value;
  localStorage.setItem("portfolio.selectedAssetId", selectedAssetId);
  history.replaceState(null, "", `?asset_id=${encodeURIComponent(selectedAssetId)}`);
  await reload();
});
document.getElementById("save-asset").addEventListener("click", () => saveAsset(false));
document.getElementById("save-strategy").addEventListener("click", () => saveAsset(true));

document.getElementById("save-transaction").addEventListener("click", async () => {
  const payload = {
    asset_id: selectedAssetId,
    type: document.getElementById("tx-type").value,
    date: document.getElementById("tx-date").value,
    pricing_mode: document.getElementById("tx-pricing-mode").value,
    amount: document.getElementById("tx-amount").value,
    note: document.getElementById("tx-note").value,
  };
  const data = await requestJson(api("/api/transactions/save"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  showTxResult(data);
  if (data.success) await loadTransactions();
});
document.getElementById("refresh-transactions").addEventListener("click", loadTransactions);

document.querySelectorAll("[data-chart-range]").forEach(button => button.addEventListener("click", () => {
  selectedChartRange = button.dataset.chartRange;
  document.querySelectorAll("[data-chart-range]").forEach(item => {
    const active = item === button;
    item.classList.toggle("active", active);
    item.setAttribute("aria-pressed", String(active));
  });
  drawTransactionChart();
}));

transactionList.addEventListener("change", event => {
  if (event.target.matches("[data-transaction-select]")) syncTransactionSelection();
});
selectAllTransactions.addEventListener("change", () => {
  transactionList.querySelectorAll("[data-transaction-select]").forEach(input => { input.checked = selectAllTransactions.checked; });
  syncTransactionSelection();
});
transactionList.addEventListener("click", async event => {
  const button = event.target.closest("[data-delete-tx]");
  if (!button || !confirm("确定删除这条交易记录？此操作不可恢复。")) return;
  const data = await requestJson(api("/api/transactions/delete"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: button.dataset.deleteTx, asset_id: selectedAssetId }),
  });
  showTxResult(data);
  if (data.success) await loadTransactions();
});
deleteSelectedTransactions.addEventListener("click", async () => {
  const ids = selectedTransactionIds();
  if (!ids.length || !confirm(`确定删除所选 ${ids.length} 条交易记录？此操作不可恢复。`)) return;
  const data = await requestJson(api("/api/transactions/delete-batch"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids, asset_id: selectedAssetId }),
  });
  showTxResult(data);
  if (data.success) await loadTransactions();
});

document.getElementById("delete-asset").addEventListener("click", async () => {
  if (!confirm("此操作会删除该资产的配置、交易、报告、缓存、计划和资产日志，且不可恢复。是否继续？")) return;
  const data = await requestJson("/api/assets/delete", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ asset_id: selectedAssetId }),
  });
  assetResult.hidden = false;
  assetResult.textContent = data.message;
  if (data.success) {
    localStorage.removeItem("portfolio.selectedAssetId");
    setTimeout(() => { location.href = "/"; }, 400);
  }
});
window.addEventListener("resize", () => { if (currentChartPayload) drawTransactionChart(); });

(async () => {
  try {
    await loadAssetList();
    await reload();
    document.getElementById("tx-date").value = new Date().toISOString().slice(0, 10);
  } catch (error) {
    assetResult.hidden = false;
    assetResult.className = "result-box error";
    assetResult.textContent = `读取失败：${error}`;
  }
})();
