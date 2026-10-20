let selectedAssetId = new URLSearchParams(location.search).get("asset_id") || localStorage.getItem("portfolio.selectedAssetId") || "";
const form = document.getElementById("trigger-form");
const result = document.getElementById("result");
const strategyKeys = [
  "strategy_type", "cycle_confirmation_pct",
  "new_low_window_days",
  "stable_days_required", "stable_range_pct",
  "downtrend_watch_percentile", "downtrend_risk_percentile",
  "same_price_band_pct", "same_price_band_max_buys", "planned_investment_reference",
  "relative_strength_benchmark_code", "relative_strength_benchmark_name",
  "volume_high_ratio", "volume_low_ratio", "volume_required_window_count",
  "volume_move_notice_percentile", "volume_move_very_large_percentile", "volume_move_history_lookback_days",
  "normal_add_amount", "enhanced_add_amount",
  "rebound_recovery_threshold_pct", "deep_drawdown_percentile", "sell_first_percentile",
  "sell_second_percentile", "sell_early_alert_points",
  "sell_first_stop_reduction_pct", "sell_residual_position_pct", "sell_residual_max_reminder_days",
];
const defaults = {strategy_type:"CYCLICAL", cycle_confirmation_pct:8, rebound_recovery_threshold_pct:80, deep_drawdown_percentile:85, downtrend_watch_percentile:38, downtrend_risk_percentile:62, same_price_band_pct:3, same_price_band_max_buys:5, volume_high_ratio:1.2, volume_low_ratio:0.8, volume_required_window_count:2, volume_move_notice_percentile:60, volume_move_very_large_percentile:80, volume_move_history_lookback_days:120, sell_first_percentile:50, sell_second_percentile:90, sell_early_alert_points:3, sell_first_stop_reduction_pct:50, sell_residual_position_pct:10, sell_residual_max_reminder_days:5};

function setValue(name, value) {
  const field = form.elements[name];
  if (field) field.value = value ?? "";
}

function getValue(name) {
  return form.elements[name].value;
}

function api(path) {
  return `${path}?asset_id=${encodeURIComponent(selectedAssetId)}`;
}

async function loadAssets() {
  const data = await fetch("/api/assets", {cache: "no-store"}).then(response => response.json());
  const assets = data.assets || [];
  const selector = document.getElementById("asset-selector");
  if (!assets.some(asset => asset.id === selectedAssetId)) selectedAssetId = assets[0]?.id || "";
  selector.innerHTML = assets.map(asset => `<option value="${asset.id}">${asset.name}（${asset.asset_code}）</option>`).join("");
  selector.value = selectedAssetId;
  selector.disabled = !selectedAssetId;
}

async function loadConditions() {
  if (!selectedAssetId) return;
  const data = await fetch(api("/api/settings"), {cache: "no-store"}).then(response => response.json());
  const strategy = data.asset?.strategy || {};
  strategyKeys.forEach(key => setValue(key, strategy[key] ?? defaults[key]));
}

function showResult(message, success) {
  result.hidden = false;
  result.className = `result-box ${success ? "success" : "error"}`;
  result.textContent = message;
}

document.getElementById("asset-selector").addEventListener("change", async event => {
  selectedAssetId = event.target.value;
  localStorage.setItem("portfolio.selectedAssetId", selectedAssetId);
  history.replaceState(null, "", `?asset_id=${encodeURIComponent(selectedAssetId)}`);
  await loadConditions();
});

document.getElementById("save-trigger-conditions").addEventListener("click", async () => {
  if (!selectedAssetId) return;
  showResult("正在保存……", true);
  try {
    const strategy = Object.fromEntries(strategyKeys.map(key => [key, getValue(key)]));
    const response = await fetch(api("/api/settings/save"), {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({asset_id: selectedAssetId, strategy}),
    });
    const data = await response.json();
    showResult(data.message || "保存完成。", Boolean(data.success));
    if (data.success) await loadConditions();
  } catch (error) {
    showResult(`保存失败：${error}`, false);
  }
});

document.getElementById("stop-sell-reminders").addEventListener("click", async () => {
  if (!selectedAssetId) return;
  const response = await fetch("/api/sell-reminders/stop", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({asset_id:selectedAssetId, reason:document.getElementById("stop-reminder-reason").value.trim()})});
  const data = await response.json();
  showResult(data.message || "操作完成。", Boolean(data.success));
});

(async () => {
  try {
    await loadAssets();
    await loadConditions();
  } catch (error) {
    showResult(`读取失败：${error}`, false);
  }
})();
