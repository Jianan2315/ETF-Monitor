let selectedAssetId = new URLSearchParams(location.search).get("asset_id") || localStorage.getItem("portfolio.selectedAssetId") || "";
const selector = document.getElementById("asset-selector");
const settingsForm = document.getElementById("replay-settings");
const replayResult = document.getElementById("replay-result");
const comparisonCard = document.getElementById("comparison-card");
const eventsCard = document.getElementById("replay-events-card");
const backToAssetManage = document.getElementById("back-to-asset-manage");
const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[character]));
const currency = value => `¥${Number(value || 0).toLocaleString("zh-CN", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
const percent = value => `${Number(value || 0).toFixed(2)}%`;
const api = path => `${path}?asset_id=${encodeURIComponent(selectedAssetId)}`;

function settingsPayload() {
  return {
    start_date: settingsForm.elements.start_date.value,
    initial_capital: settingsForm.elements.initial_capital.value,
    initial_buy_amount: settingsForm.elements.initial_buy_amount.value,
    review_buy_amount: settingsForm.elements.review_buy_amount.value,
    deep_drawdown_buy_amount: settingsForm.elements.deep_drawdown_buy_amount.value,
    first_sell_pct: settingsForm.elements.first_sell_pct.value,
    second_sell_pct: settingsForm.elements.second_sell_pct.value,
    execute_downtrend_sell: settingsForm.elements.execute_downtrend_sell.checked,
  };
}

function fillSettings(settings = {}) {
  Object.entries(settings).forEach(([key, value]) => {
    const field = settingsForm.elements[key];
    if (!field) return;
    if (field.type === "checkbox") field.checked = Boolean(value); else field.value = value ?? "";
  });
}

function showResult(message, success) {
  replayResult.hidden = false;
  replayResult.className = `result-box ${success ? "success" : "error"}`;
  replayResult.textContent = message;
}

function updateAssetLink() {
  backToAssetManage.href = selectedAssetId ? `/asset/manage?asset_id=${encodeURIComponent(selectedAssetId)}` : "/asset/manage";
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok && data.success !== false) data.success = false;
  return data;
}

async function loadAssets() {
  const data = await requestJson("/api/assets");
  const assets = data.assets || [];
  if (!assets.some(asset => asset.id === selectedAssetId)) selectedAssetId = assets[0]?.id || "";
  selector.innerHTML = assets.map(asset => `<option value="${escapeHtml(asset.id)}">${escapeHtml(asset.name)}（${escapeHtml(asset.asset_code)}）</option>`).join("");
  selector.value = selectedAssetId;
  localStorage.setItem("portfolio.selectedAssetId", selectedAssetId);
  updateAssetLink();
}

function renderEvents(events) {
  eventsCard.hidden = false;
  const root = document.getElementById("replay-events");
  root.innerHTML = events.length ? events.slice().reverse().map(event => {
    const action = event.action === "BUY" ? "模拟买入" : "模拟卖出";
    return `<article class="transaction-item"><strong>${escapeHtml(event.execution_date)} · ${action}</strong><p>信号日：${escapeHtml(event.signal_date)} · 原因：${escapeHtml(event.reason_code)}</p><p>价格：${Number(event.price).toFixed(4)} · 金额：${currency(event.amount)} · 份额：${Number(event.shares).toFixed(4)}</p></article>`;
  }).join("") : "<p class='empty-state'>在当前参数和历史区间内，没有形成可执行的模拟订单。</p>";
}

function renderReplay(data) {
  if (!data.success) {
    comparisonCard.hidden = true;
    eventsCard.hidden = true;
    fillSettings(data.settings || data.defaults || {});
    showResult(data.message || "无法计算策略模拟。", false);
    return;
  }
  replayResult.hidden = true;
  fillSettings(data.settings || {});
  const {actual, strategy, difference, period} = data;
  comparisonCard.hidden = false;
  document.getElementById("comparison-period").textContent = `模拟区间：${period.start_date} 至 ${period.end_date} · 最新收盘价 ${Number(period.latest_price).toFixed(4)} · ${period.trading_days} 个交易日`;
  document.getElementById("actual-net-value").textContent = currency(actual.net_asset_value);
  document.getElementById("actual-profit").textContent = currency(actual.profit_amount);
  document.getElementById("actual-return").textContent = percent(actual.return_pct);
  document.getElementById("strategy-net-value").textContent = currency(strategy.net_asset_value);
  document.getElementById("strategy-profit").textContent = currency(strategy.profit_amount);
  document.getElementById("strategy-return").textContent = percent(strategy.return_pct);
  const winner = difference.profit_amount > 0 ? "策略模拟较高" : difference.profit_amount < 0 ? "实际操作较高" : "两者相同";
  document.getElementById("difference-summary").textContent = `${winner}：${currency(Math.abs(difference.profit_amount))}（${percent(Math.abs(difference.return_pct))}）`;
  document.getElementById("comparison-note").textContent = difference.directly_comparable ? "模拟初始资金与实际累计买入金额一致，收益率可直接参考。" : "模拟初始资金与实际累计买入金额不同，收益率仅供情景参考。";
  document.getElementById("comparison-details").textContent = `策略模拟：现金 ${currency(strategy.cash)}，持有 ${strategy.shares} 份，已执行 ${strategy.executed_order_count} 笔；实际记录：累计买入 ${currency(actual.gross_buy_amount)}，卖出回款 ${currency(actual.sale_proceeds)}，忽略待确认记录 ${actual.pending_transactions_ignored} 笔。`;
  renderEvents(data.events || []);
}

async function loadReplay() {
  if (!selectedAssetId) return;
  const data = await requestJson(api("/api/strategy-replay"));
  renderReplay(data);
}

selector.addEventListener("change", async event => {
  selectedAssetId = event.target.value;
  localStorage.setItem("portfolio.selectedAssetId", selectedAssetId);
  history.replaceState(null, "", `?asset_id=${encodeURIComponent(selectedAssetId)}`);
  updateAssetLink();
  await loadReplay();
});

document.getElementById("run-replay").addEventListener("click", async () => {
  const data = await requestJson(api("/api/strategy-replay/run"), {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({asset_id: selectedAssetId, settings: settingsPayload()}),
  });
  renderReplay(data);
});

document.getElementById("save-replay-settings").addEventListener("click", async () => {
  const data = await requestJson(api("/api/strategy-replay/settings"), {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({asset_id: selectedAssetId, settings: settingsPayload()}),
  });
  showResult(data.message || "保存失败。", Boolean(data.success));
  if (data.success) fillSettings(data.settings || {});
});

(async () => {
  try { await loadAssets(); await loadReplay(); }
  catch (error) { showResult(`读取失败：${error}`, false); }
})();
