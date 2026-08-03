let selectedAssetId = localStorage.getItem("portfolio.selectedAssetId") || "";

const disabledHint = "请先选择或创建一个资产。";

function setLink(id, href, enabled) {
  const link = document.getElementById(id);
  link.href = enabled ? href : "#";
  link.classList.toggle("disabled", !enabled);
  link.setAttribute("aria-disabled", String(!enabled));
  link.title = enabled ? "" : disabledHint;
  link.tabIndex = enabled ? 0 : -1;
}

function setAssetButton(enabled) {
  const button = document.getElementById("generate-report");
  button.disabled = !enabled;
  button.title = enabled ? "" : disabledHint;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[char]));
}

function money(value) {
  return `¥${Number(value || 0).toLocaleString("zh-CN", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
}

function signedPercent(value) {
  const number = Number(value || 0);
  return `${number >= 0 ? "+" : ""}${number.toFixed(2)}%`;
}

async function renderSelectedAsset(selected) {
  const cards = document.getElementById("asset-summary");
  if (!selected) {
    cards.innerHTML = `<article class="empty-state"><h3>暂无资产</h3><p>添加资产后即可开始监控。</p></article>`;
    return;
  }
  let data;
  try {
    const response = await fetch(`/api/assets/summary?asset_id=${encodeURIComponent(selected.id)}`, {cache: "no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    data = await response.json();
  } catch (error) {
    cards.innerHTML = `<article class="status-item asset-card selected-asset-card bad"><strong>${escapeHtml(selected.name)}</strong><p>资产摘要暂时无法读取；运行状态仍可正常查看。</p><div class="button-row"><a class="secondary-button" href="/asset/manage?asset_id=${encodeURIComponent(selected.id)}">编辑</a><a class="secondary-button" href="/report?asset_id=${encodeURIComponent(selected.id)}">查看报告</a></div></article>`;
    return;
  }
  const position = data.position || {};
  const profit = Number(position.profit_amount || 0);
  const profitClass = profit >= 0 ? "positive" : "negative";
  const pendingDates = (data.pending_buy_dates || []).join("、");
  const pending = Number(data.pending_buy_count || 0) > 0
    ? `<p class="today-buy-note">待确认买入 ${money(data.pending_buy_amount)}（${escapeHtml(pendingDates || "日期待确认")}），尚未纳入已投入成本和收益计算。${escapeHtml(data.settlement?.message || "")}</p>`
    : "";
  cards.innerHTML = `<article class="status-item asset-card selected-asset-card ${selected.enabled ? "ok" : "bad"}">
    <strong>${escapeHtml(selected.name)}</strong>
    <p>持有代码：${escapeHtml(selected.asset_code)} · 跟踪代码：${escapeHtml(selected.tracking_code)}</p>
    <div class="asset-performance-grid">
      <div><span>当前已投入成本</span><b>${money(position.invested_amount)}</b></div>
      <div class="${profitClass}"><span>当前收益率</span><b>${signedPercent(position.profit_pct)}</b></div>
      <div class="${profitClass}"><span>当前收益金额</span><b>${money(profit)}</b></div>
    </div>
    <p class="earnings-as-of">${escapeHtml(data.message || "收益数据暂不可用")}</p>
    <p class="muted valuation-basis">${escapeHtml(data.valuation_basis_label || "收益按跟踪标的价格估算")}</p>
    ${pending}
    <div class="button-row"><a class="secondary-button" href="/asset/manage?asset_id=${encodeURIComponent(selected.id)}">编辑</a><a class="secondary-button" href="/report?asset_id=${encodeURIComponent(selected.id)}">查看报告</a></div>
  </article>`;
}

async function loadAssets() {
  const data = await fetch("/api/assets", {cache: "no-store"}).then(r => r.json());
  const assets = data.assets || [];
  const selector = document.getElementById("asset-selector");
  selector.innerHTML = '<option value="">请选择资产</option>' + assets.map(a => `<option value="${a.id}">${a.name}（${a.asset_code}）</option>`).join("");
  if (!assets.some(a => a.id === selectedAssetId)) selectedAssetId = "";
  selector.value = selectedAssetId;
  selector.disabled = !assets.length;
  selector.title = assets.length ? "" : "请先创建一个资产。";

  const selected = assets.find(a => a.id === selectedAssetId);
  await renderSelectedAsset(selected);

  const id = encodeURIComponent(selectedAssetId);
  setLink("schedule-link", `/schedules?asset_id=${id}`, !!selected);
  setAssetButton(!!selected);

  selector.onchange = async () => {
    selectedAssetId = selector.value;
    localStorage.setItem("portfolio.selectedAssetId", selectedAssetId);
    await loadAssets();
    await loadRunningStatus();
  };
}

function statusCard(title, ok, message) {
  return `<article class="status-item ${ok ? "ok" : "bad"}"><strong>${title}</strong><p>${message || "-"}</p></article>`;
}

async function loadRunningStatus() {
  const assetQuery = selectedAssetId ? `?asset_id=${encodeURIComponent(selectedAssetId)}` : "";
  const data = await fetch(`/api/environment${assetQuery}`, {cache: "no-store"}).then(r => r.json());
  const power = data.keep_awake || {};
  const market = data.market || {};
  const latest = data.latest || {};
  const cards = [
    statusCard("市场状态", market.ok, market.message),
    statusCard("自动监控", true, "后台服务运行中"),
    statusCard("保持系统唤醒", power.enabled, power.enabled ? "已启用" : "未启用"),
    statusCard("当前交易窗口", power.active, power.active ? "活动中" : (power.next_window || "非活动")),
    statusCard("最近成功运行", latest.ok, latest.message),
  ];
  const grid = document.getElementById("running-status");
  grid.dataset.count = String(cards.length);
  grid.innerHTML = cards.join("");
}

function onboardingItem(item) {
  const title = escapeHtml(item.label);
  return `<article class="parameter-onboarding-item ${item.kind}">
    <div><strong>${title}</strong><span>${escapeHtml(item.description)}</span></div>
    <a class="button-link secondary" href="${escapeHtml(item.settings_url)}">前往设置</a>
  </article>`;
}

function onboardingGroups(items) {
  const groups = new Map();
  items.forEach(item => {
    const key = item.asset_id || item.asset_name;
    if (!groups.has(key)) groups.set(key, {assetName: item.asset_name, items: []});
    groups.get(key).items.push(item);
  });
  return [...groups.values()].map(group => {
    const requiredCount = group.items.filter(item => item.kind === "required").length;
    const optionalCount = group.items.length - requiredCount;
    const summary = `${group.items.length} 项待处理${requiredCount ? ` · 必填 ${requiredCount}` : ""}${optionalCount ? ` · 选填 ${optionalCount}` : ""}`;
    return `<details class="parameter-onboarding-group">
      <summary><span>${escapeHtml(group.assetName)} · 参数检查</span><span>${summary}</span></summary>
      <div class="parameter-onboarding-group-items">${group.items.map(onboardingItem).join("")}</div>
    </details>`;
  }).join("");
}

async function dismissOptionalOnboardingItems(items) {
  if (!items.length) return;
  const response = await fetch("/api/parameter-onboarding/dismiss-optionals", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({items: items.map(item => ({asset_id: item.asset_id, id: item.id}))}),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
}

async function loadParameterOnboarding() {
  const modal = document.getElementById("parameter-onboarding-modal");
  const itemsPanel = document.getElementById("parameter-onboarding-items");
  const intro = document.getElementById("parameter-onboarding-intro");
  const dismissRow = document.getElementById("parameter-onboarding-dismiss-row");
  const dismissCheckbox = document.getElementById("parameter-onboarding-dismiss-optionals");
  const laterButton = document.getElementById("parameter-onboarding-later");
  const closeButton = document.getElementById("parameter-onboarding-close");
  const toast = document.getElementById("parameter-onboarding-toast");
  const toastMessage = document.getElementById("parameter-onboarding-toast-message");
  let data;
  try {
    const response = await fetch("/api/parameter-onboarding", {cache: "no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    data = await response.json();
  } catch (error) {
    console.warn("参数检查暂时不可用", error);
    return;
  }

  const required = data.required || [];
  const optional = data.optional || [];
  if (!required.length && !optional.length) {
    modal.hidden = true;
    toast.hidden = true;
    return;
  }

  itemsPanel.innerHTML = onboardingGroups([
    ...required.map(item => ({...item, kind: "required"})),
    ...optional.map(item => ({...item, kind: "optional"})),
  ]);
  dismissRow.hidden = !optional.length;
  dismissCheckbox.checked = false;
  if (required.length) {
    intro.textContent = "以下必填参数尚未具备有效配置。请进入相应资产的触发条件页面填写；在完成前，每次打开主页都会继续提醒。";
    laterButton.hidden = !optional.length;
    laterButton.textContent = "保存选填项选择";
  } else {
    intro.textContent = "以下为选填参数。它们不会阻断报告或策略；你可以填写，也可以选择暂不设置。";
    laterButton.hidden = false;
    laterButton.textContent = "本次稍后处理";
  }
  toast.hidden = false;
  toastMessage.textContent = `${required.length ? `必填 ${required.length} 项` : ""}${required.length && optional.length ? " · " : ""}${optional.length ? `选填 ${optional.length} 项` : ""}`;
  toast.onclick = () => {
    modal.hidden = false;
    toast.setAttribute("aria-expanded", "true");
  };
  const closeModal = () => {
    modal.hidden = true;
    toast.setAttribute("aria-expanded", "false");
  };
  closeButton.onclick = closeModal;
  modal.onclick = event => { if (event.target === modal) closeModal(); };

  laterButton.onclick = async () => {
    laterButton.disabled = true;
    try {
      if (dismissCheckbox.checked) await dismissOptionalOnboardingItems(optional);
      await loadParameterOnboarding();
      closeModal();
    } catch (error) {
      intro.textContent = `无法保存选填项的提示偏好：${error}`;
    } finally {
      laterButton.disabled = false;
    }
  };
}

function showRunResult(message, success) {
  const panel = document.getElementById("run-result");
  panel.hidden = false;
  panel.className = `result-box ${success ? "success" : "error"}`;
  panel.textContent = message;
}

async function runReport(mode) {
  if (!selectedAssetId) return;
  const panel = document.getElementById("run-result");
  panel.hidden = false;
  panel.className = "result-box";
  panel.textContent = "正在生成报告…";
  const response = await fetch("/api/report/run", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({mode, asset_id:selectedAssetId})});
  const data = await response.json();
  panel.className = `result-box ${data.success ? "success" : "error"}`;
  panel.textContent = data.message || "运行结束。";
  if (data.success && data.report_url) setTimeout(() => location.href = data.report_url, 500);
}

document.querySelectorAll("[data-run-mode]").forEach(button => button.addEventListener("click", () => runReport(button.dataset.runMode)));
document.getElementById("refresh-status").addEventListener("click", loadRunningStatus);
document.addEventListener("click", event => {
  if (event.target.closest("a.disabled")) event.preventDefault();
});
(async () => {
  try {
    await loadAssets();
    await loadRunningStatus();
    await loadParameterOnboarding();
  } catch (error) {
    document.getElementById("running-status").textContent = `状态读取失败：${error}`;
  }
})();
