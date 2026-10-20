let selectedAssetId = new URLSearchParams(location.search).get("asset_id") || localStorage.getItem("portfolio.selectedAssetId") || "";
const selector = document.getElementById("asset-selector");
const checklist = document.getElementById("review-checklist");
const latestContext = document.getElementById("latest-context");
const reviewResult = document.getElementById("review-result");
const reviewHistory = document.getElementById("review-history");
const backToAssetManage = document.getElementById("back-to-asset-manage");
const newsContext = document.getElementById("news-context");
const newsArticles = document.getElementById("news-articles");
const searchNewsButton = document.getElementById("search-news");
const reviewNewsLookbackDays = document.getElementById("review-news-lookback-days");
const reviewNewsQueries = document.getElementById("review-news-queries");
const saveNewsSettingsButton = document.getElementById("save-news-settings");
let currentChecklist = [];
const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[character]));
const api = path => `${path}?asset_id=${encodeURIComponent(selectedAssetId)}`;

function showResult(message, success) { reviewResult.hidden = false; reviewResult.className = `result-box ${success ? "success" : "error"}`; reviewResult.textContent = message; }

function updateAssetManageLink() {
  backToAssetManage.href = selectedAssetId
    ? `/asset/manage?asset_id=${encodeURIComponent(selectedAssetId)}`
    : "/asset/manage";
}

async function loadAssets() {
  const data = await fetch("/api/assets", {cache:"no-store"}).then(response => response.json());
  const assets = data.assets || [];
  if (!assets.some(asset => asset.id === selectedAssetId)) selectedAssetId = assets[0]?.id || "";
  selector.innerHTML = assets.map(asset => `<option value="${escapeHtml(asset.id)}">${escapeHtml(asset.name)}（${escapeHtml(asset.asset_code)}）</option>`).join("");
  selector.value = selectedAssetId;
  updateAssetManageLink();
}

function renderHistory(reviews, checklistItems) {
  const labels = new Map((checklistItems || []).map(item => [item.id, item.label]));
  const conclusions = {NO_ACTION:"暂不操作", WAIT:"继续等待", ADD:"人工决定加仓", REDUCE:"人工决定减仓", SELL:"人工决定卖出"};
  reviewHistory.innerHTML = reviews.length ? reviews.map(item => {
    const checks = item.checks || {};
    const ids = [...new Set([...labels.keys(), ...Object.keys(checks)])];
    const checkItems = ids.map(id => `<li><strong>${checks[id] ? "✓" : "✗"}</strong> ${escapeHtml(labels.get(id) || id)}</li>`).join("");
    return `<article class="transaction-item"><strong>${escapeHtml(item.created_at)} · ${escapeHtml(conclusions[item.conclusion] || item.conclusion)}</strong><p>关联报告：${escapeHtml(item.report_generated_at || "未关联")}</p><p>报告状态：${escapeHtml(item.strategy_status)} · 原因：${escapeHtml(item.reason_code)}</p><p><strong>复核项目：</strong></p><ul class="review-checks">${checkItems || "<li>无已保存勾选项</li>"}</ul>${item.notes ? `<p><strong>备注：</strong>${escapeHtml(item.notes)}</p>` : ""}</article>`;
  }).join("") : "<p class='empty-state'>尚无已保存的人工复核记录。</p>";
}

function renderNews(news) {
  const snapshot = news || {};
  newsContext.hidden = false;
  newsContext.className = `result-box ${snapshot.status === "FAILED" ? "error" : ""}`;
  if (!snapshot.status) {
    newsContext.textContent = "尚未执行手动资讯检索。";
    newsArticles.innerHTML = "";
    return;
  }
  newsContext.textContent = snapshot.message || "资讯检索状态未知。";
  const summary = snapshot.summary ? `<p><strong>本地摘要：</strong>${escapeHtml(snapshot.summary)}</p>` : "";
  const summaryFailure = snapshot.summary_status === "FAILED" ? "<p>本地摘要失败，以下保留原始来源。</p>" : "";
  const articles = (snapshot.articles || []).map(article => `<li><a href="${escapeHtml(article.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(article.title)}</a><br><small>${escapeHtml(article.source)} · ${escapeHtml(article.published_at || "日期不详")}</small>${article.snippet ? `<br>${escapeHtml(article.snippet)}` : ""}</li>`).join("");
  newsArticles.innerHTML = `${summary}${summaryFailure}${articles ? `<ul>${articles}</ul>` : ""}`;
}

function renderNewsSettings(settings) {
  const current = settings || {};
  reviewNewsLookbackDays.value = current.lookback_days ?? 7;
  reviewNewsQueries.value = (current.queries || []).join("\n");
}

async function saveNewsSettings() {
  saveNewsSettingsButton.disabled = true;
  try {
    const response = await fetch(api("/api/news/settings/save"), {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({
        asset_id: selectedAssetId,
        lookback_days: reviewNewsLookbackDays.value,
        queries: reviewNewsQueries.value.split(/\r?\n/),
      }),
    });
    const data = await response.json();
    showResult(data.message || "资讯检索设置已保存。", Boolean(data.success));
    if (data.success) renderNewsSettings(data.settings || {});
  } catch (error) {
    showResult(`保存资讯检索设置失败：${error}`, false);
  } finally {
    saveNewsSettingsButton.disabled = false;
  }
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
  renderHistory(data.reviews || [], currentChecklist);
  renderNews(data.news || {});
  renderNewsSettings(data.news_settings || {});
}

selector.addEventListener("change", async event => { selectedAssetId = event.target.value; localStorage.setItem("portfolio.selectedAssetId", selectedAssetId); window.history.replaceState(null, "", `?asset_id=${encodeURIComponent(selectedAssetId)}`); updateAssetManageLink(); await loadReview(); });
document.getElementById("save-review").addEventListener("click", async () => {
  const checks = Object.fromEntries(currentChecklist.map(item => [item.id, Boolean(checklist.querySelector(`[data-check="${item.id}"]`)?.checked)]));
  const response = await fetch(api("/api/reviews/save"), {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({asset_id:selectedAssetId, checks, conclusion:document.getElementById("review-conclusion").value, notes:document.getElementById("review-notes").value})});
  const data = await response.json();
  showResult(data.message || "保存失败。", Boolean(data.success));
  if (data.success) { document.getElementById("review-notes").value = ""; await loadReview(); }
});
saveNewsSettingsButton.addEventListener("click", saveNewsSettings);
searchNewsButton.addEventListener("click", async () => {
  searchNewsButton.disabled = true;
  const original = searchNewsButton.textContent;
  searchNewsButton.textContent = "正在检索…";
  try {
    const response = await fetch(api("/api/news/search"), {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({asset_id:selectedAssetId})});
    const data = await response.json();
    renderNews(data.news || {status:"FAILED", message:data.message || "资讯检索失败。"});
  } catch (error) {
    renderNews({status:"FAILED", message:`资讯检索失败：${error}`});
  } finally {
    searchNewsButton.disabled = false;
    searchNewsButton.textContent = original;
  }
});
(async () => { await loadAssets(); await loadReview(); })().catch(error => showResult(`读取失败：${error}`, false));
