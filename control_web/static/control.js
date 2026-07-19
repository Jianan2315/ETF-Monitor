let selectedAssetId = localStorage.getItem("portfolio.selectedAssetId") || "";

function setLink(id, href, enabled) {
  const link = document.getElementById(id);
  link.href = enabled ? href : "#";
  link.classList.toggle("disabled", !enabled);
  link.setAttribute("aria-disabled", String(!enabled));
}

async function loadAssets() {
  const data = await fetch("/api/assets", {cache: "no-store"}).then(r => r.json());
  const assets = data.assets || [], selector = document.getElementById("asset-selector");
  selector.innerHTML = '<option value="">请选择资产</option>' + assets.map(a => `<option value="${a.id}">${a.name}（${a.asset_code}）</option>`).join("");
  if (!assets.some(a => a.id === selectedAssetId)) selectedAssetId = "";
  selector.value = selectedAssetId;
  const selected = assets.find(a => a.id === selectedAssetId);
  const cards = document.getElementById("asset-summary");
  cards.innerHTML = assets.length ? assets.map(a => `<article class="status-item asset-card ${a.enabled ? "ok" : "bad"} ${a.id === selectedAssetId ? "selected" : ""}" data-select="${a.id}"><strong>${a.name}</strong><p>持有代码：${a.asset_code}</p><p>跟踪名称：${a.tracking_name || "-"}</p><p>跟踪代码：${a.tracking_code}</p><div class="button-row"><a class="secondary-button" href="/asset/manage?asset_id=${encodeURIComponent(a.id)}">编辑</a><a class="secondary-button" href="/report?asset_id=${encodeURIComponent(a.id)}">报告</a></div></article>`).join("") : "<article class='empty-state'><h3>暂无资产</h3><a class='button-link' href='/asset/new'>添加第一个资产</a></article>";
  const empty = document.getElementById("asset-empty-note"); empty.hidden = !!assets.length;
  if (!assets.length) empty.textContent = "添加资产后即可建立独立的交易、缓存、报告和日志目录。";
  const id = encodeURIComponent(selectedAssetId);
  setLink("edit-asset-link", `/asset/manage?asset_id=${id}`, !!selected);
  setLink("latest-report-link", `/report?asset_id=${id}`, !!selected);
  setLink("schedule-link", `/schedules?asset_id=${id}`, !!selected);
  selector.onchange = async () => { selectedAssetId = selector.value; localStorage.setItem("portfolio.selectedAssetId", selectedAssetId); await loadAssets(); await loadRunningStatus(); };
  cards.onclick = async e => { const card = e.target.closest("[data-select]"); if (!card || e.target.closest("a")) return; selectedAssetId = card.dataset.select; localStorage.setItem("portfolio.selectedAssetId", selectedAssetId); await loadAssets(); await loadRunningStatus(); };
}

function statusCard(title, ok, message) { return `<article class="status-item ${ok ? "ok" : "bad"}"><strong>${title}</strong><p>${message || "-"}</p></article>`; }
async function loadRunningStatus() {
  const data = await fetch("/api/environment", {cache: "no-store"}).then(r => r.json());
  const p = data.keep_awake || {}, market = data.market || {}, latest = data.latest || {};
  document.getElementById("running-status").innerHTML = [
    statusCard("调度器", true, "运行中"),
    statusCard("保持系统唤醒", p.enabled, p.enabled ? "已启用" : "未启用"),
    statusCard("当前窗口", p.active, p.active ? "活动中" : (p.next_window || "非活动")),
    statusCard("市场状态", market.ok, market.message),
    statusCard("最近成功运行", latest.ok, latest.message),
  ].join("");
}
async function runReport(mode) {
  if (!selectedAssetId) return alert("请先选择一个资产。");
  const panel = document.getElementById("run-result"); panel.hidden = false; panel.className = "result-box"; panel.textContent = "正在生成报告…";
  const response = await fetch("/api/report/run", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({mode, asset_id:selectedAssetId})});
  const data = await response.json(); panel.className = `result-box ${data.success ? "success" : "error"}`; panel.textContent = data.message || "运行结束。";
  if (data.success && data.report_url) setTimeout(() => location.href = data.report_url, 500);
}
document.querySelectorAll("[data-run-mode]").forEach(button => button.addEventListener("click", () => runReport(button.dataset.runMode)));
document.getElementById("refresh-status").addEventListener("click", loadRunningStatus);
(async () => { try { await loadAssets(); await loadRunningStatus(); } catch (error) { document.getElementById("running-status").textContent = `状态读取失败：${error}`; } })();
