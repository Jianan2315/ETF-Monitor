let selectedAssetId = new URLSearchParams(location.search).get("asset_id") || localStorage.getItem("portfolio.selectedAssetId") || "";
const selector = document.getElementById("asset-selector");
const checklist = document.getElementById("review-checklist");
const latestContext = document.getElementById("latest-context");
const reviewResult = document.getElementById("review-result");
const reviewHistory = document.getElementById("review-history");
let currentChecklist = [];
const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[character]));
const api = path => `${path}?asset_id=${encodeURIComponent(selectedAssetId)}`;

function showResult(message, success) { reviewResult.hidden = false; reviewResult.className = `result-box ${success ? "success" : "error"}`; reviewResult.textContent = message; }

async function loadAssets() {
  const data = await fetch("/api/assets", {cache:"no-store"}).then(response => response.json());
  const assets = data.assets || [];
  if (!assets.some(asset => asset.id === selectedAssetId)) selectedAssetId = assets[0]?.id || "";
  selector.innerHTML = assets.map(asset => `<option value="${escapeHtml(asset.id)}">${escapeHtml(asset.name)}（${escapeHtml(asset.asset_code)}）</option>`).join("");
  selector.value = selectedAssetId;
}

function renderHistory(reviews) {
  reviewHistory.innerHTML = reviews.length ? reviews.map(item => `<article class="transaction-item"><strong>${escapeHtml(item.created_at)} · ${escapeHtml(item.conclusion)}</strong><p>报告状态：${escapeHtml(item.strategy_status)} · 原因：${escapeHtml(item.reason_code)}</p>${item.notes ? `<p>${escapeHtml(item.notes)}</p>` : ""}</article>`).join("") : "<p class='empty-state'>尚无已保存的人工复核记录。</p>";
}

async function loadReview() {
  if (!selectedAssetId) return;
  const data = await fetch(api("/api/reviews"), {cache:"no-store"}).then(response => response.json());
  if (!data.success) return showResult(data.message || "读取人工复核失败。", false);
  currentChecklist = data.checklist || [];
  const latest = data.latest || {};
  latestContext.hidden = false;
  latestContext.className = "result-box";
  latestContext.textContent = latest.generated_at ? `关联报告：${latest.generated_at} · ${latest.status} · ${latest.status_label || latest.reason_code}` : "尚未找到最新报告；仍可保存独立的人工复核记录。";
  checklist.innerHTML = currentChecklist.map(item => `<label class="checkbox-row"><input type="checkbox" data-check="${escapeHtml(item.id)}">${escapeHtml(item.label)}</label>`).join("");
  renderHistory(data.reviews || []);
}

selector.addEventListener("change", async event => { selectedAssetId = event.target.value; localStorage.setItem("portfolio.selectedAssetId", selectedAssetId); window.history.replaceState(null, "", `?asset_id=${encodeURIComponent(selectedAssetId)}`); await loadReview(); });
document.getElementById("save-review").addEventListener("click", async () => {
  const checks = Object.fromEntries(currentChecklist.map(item => [item.id, Boolean(checklist.querySelector(`[data-check="${item.id}"]`)?.checked)]));
  const response = await fetch(api("/api/reviews/save"), {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({asset_id:selectedAssetId, checks, conclusion:document.getElementById("review-conclusion").value, notes:document.getElementById("review-notes").value})});
  const data = await response.json();
  showResult(data.message || "保存失败。", Boolean(data.success));
  if (data.success) { document.getElementById("review-notes").value = ""; await loadReview(); }
});
(async () => { await loadAssets(); await loadReview(); })().catch(error => showResult(`读取失败：${error}`, false));
