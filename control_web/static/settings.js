let originalSettings = null;
let pendingSettingsPayload = null;
let pendingTransaction = null;
let transactionRecords = [];

const form = document.getElementById("settings-form");
const previewBox = document.getElementById("settings-preview");
const saveButton = document.getElementById("save-settings");
const dialog = document.getElementById("confirm-dialog");
const confirmSummary = document.getElementById("confirm-summary");

function setValue(name, value) {
  const input = form.elements[name];
  if (!input) return;
  if (input.type === "checkbox") {
    input.checked = Boolean(value);
  } else {
    input.value = value ?? "";
  }
}

function getValue(name) {
  const input = form.elements[name];
  if (!input) return null;
  return input.type === "checkbox" ? input.checked : input.value;
}

function fillSettings(data) {
  originalSettings = structuredClone(data);
  const asset = data.asset || {};
  const strategy = asset.strategy || {};
  const email = data.email || {};
  const llm = data.llm || {};
  const dataCfg = data.data || {};
  const report = data.report || {};
  const schedule = data.schedule || {};
  const triggers = email.triggers || {};

  setValue("target_amount", asset.position?.target_amount);

  [
    "drop_from_last_buy_trigger_pct",
    "stable_days_required",
    "stable_range_pct",
    "consecutive_down_warning_days",
    "normal_add_amount",
    "enhanced_add_amount",
    "reversal_confirmation_days",
    "minimum_rebound_pct",
    "first_take_profit_pct",
    "first_sell_position_pct",
    "second_take_profit_pct",
    "second_sell_position_pct",
    "minimum_core_position_pct",
  ].forEach(key => setValue(key, strategy[key]));

  setValue("llm_enabled", llm.enabled);
  setValue("llm_model", llm.model);
  setValue("llm_timeout_seconds", llm.timeout_seconds);
  setValue("llm_max_summary_chars", llm.max_summary_chars);
  setValue("llm_num_ctx", llm.num_ctx);
  setValue("llm_num_predict", llm.num_predict);
  setValue("llm_keep_alive", llm.keep_alive);

  setValue("email_enabled", email.enabled);
  setValue("email_recipient", email.recipient);
  setValue("email_include_llm_summary", email.include_llm_summary);
  setValue("email_include_raw_metrics", email.include_raw_metrics);
  setValue("email_force_send", email.debug?.force_send_email);
  setValue("email_data_error_dedup_minutes", email.data_error_dedup_minutes);

  ["WAIT", "WATCH", "HOLD", "REVIEW_BUY", "PARTIAL_SELL"]
    .forEach(key => setValue(`trigger_${key}`, triggers[key]));

  setValue("history_days", dataCfg.history_days);
  setValue("retry_max_attempts", dataCfg.retry?.max_attempts);
  setValue("retention_days", report.retention_days);
  setValue("max_history_files", report.max_history_files);
  setValue("intraday_slots", (schedule.intraday_slots || []).join(", "));
  setValue("close_slot", schedule.close_slot);
}

function buildPayload() {
  const triggerKeys = ["WAIT", "WATCH", "HOLD", "REVIEW_BUY", "PARTIAL_SELL"];
  return {
    asset: {
      target_amount: getValue("target_amount"),
    },
    strategy: {
      drop_from_last_buy_trigger_pct: getValue("drop_from_last_buy_trigger_pct"),
      stable_days_required: getValue("stable_days_required"),
      stable_range_pct: getValue("stable_range_pct"),
      consecutive_down_warning_days: getValue("consecutive_down_warning_days"),
      normal_add_amount: getValue("normal_add_amount"),
      enhanced_add_amount: getValue("enhanced_add_amount"),
      reversal_confirmation_days: getValue("reversal_confirmation_days"),
      minimum_rebound_pct: getValue("minimum_rebound_pct"),
      first_take_profit_pct: getValue("first_take_profit_pct"),
      first_sell_position_pct: getValue("first_sell_position_pct"),
      second_take_profit_pct: getValue("second_take_profit_pct"),
      second_sell_position_pct: getValue("second_sell_position_pct"),
      minimum_core_position_pct: getValue("minimum_core_position_pct"),
    },
    llm: {
      enabled: getValue("llm_enabled"),
      model: getValue("llm_model"),
      timeout_seconds: getValue("llm_timeout_seconds"),
      max_summary_chars: getValue("llm_max_summary_chars"),
      num_ctx: getValue("llm_num_ctx"),
      num_predict: getValue("llm_num_predict"),
      keep_alive: getValue("llm_keep_alive"),
    },
    email: {
      enabled: getValue("email_enabled"),
      recipient: getValue("email_recipient"),
      include_llm_summary: getValue("email_include_llm_summary"),
      include_raw_metrics: getValue("email_include_raw_metrics"),
      force_send_email: getValue("email_force_send"),
      data_error_dedup_minutes: getValue("email_data_error_dedup_minutes"),
      triggers: Object.fromEntries(
        triggerKeys.map(key => [key, getValue(`trigger_${key}`)])
      ),
    },
    data: {
      history_days: getValue("history_days"),
      retry_max_attempts: getValue("retry_max_attempts"),
    },
    report: {
      retention_days: getValue("retention_days"),
      max_history_files: getValue("max_history_files"),
    },
    schedule: {
      intraday_slots: getValue("intraday_slots")
        .split(",")
        .map(value => value.trim())
        .filter(Boolean),
      close_slot: getValue("close_slot").trim(),
    },
  };
}

function flattenForComparison(settings) {
  const asset = settings.asset || {};
  const strategy = asset.strategy || {};
  const email = settings.email || {};
  const llm = settings.llm || {};
  const dataCfg = settings.data || {};
  const report = settings.report || {};
  const schedule = settings.schedule || {};

  return {
    target_amount: asset.position?.target_amount,
    drop_from_last_buy_trigger_pct: strategy.drop_from_last_buy_trigger_pct,
    stable_days_required: strategy.stable_days_required,
    stable_range_pct: strategy.stable_range_pct,
    consecutive_down_warning_days: strategy.consecutive_down_warning_days,
    normal_add_amount: strategy.normal_add_amount,
    enhanced_add_amount: strategy.enhanced_add_amount,
    reversal_confirmation_days: strategy.reversal_confirmation_days,
    minimum_rebound_pct: strategy.minimum_rebound_pct,
    first_take_profit_pct: strategy.first_take_profit_pct,
    first_sell_position_pct: strategy.first_sell_position_pct,
    second_take_profit_pct: strategy.second_take_profit_pct,
    second_sell_position_pct: strategy.second_sell_position_pct,
    minimum_core_position_pct: strategy.minimum_core_position_pct,

    llm_enabled: llm.enabled,
    llm_model: llm.model,
    llm_timeout_seconds: llm.timeout_seconds,
    llm_max_summary_chars: llm.max_summary_chars,
    llm_num_ctx: llm.num_ctx,
    llm_num_predict: llm.num_predict,
    llm_keep_alive: llm.keep_alive,

    email_enabled: email.enabled,
    email_recipient: email.recipient,
    email_include_llm_summary: email.include_llm_summary,
    email_include_raw_metrics: email.include_raw_metrics,
    email_force_send: email.debug?.force_send_email,
    email_data_error_dedup_minutes: email.data_error_dedup_minutes,

    trigger_WAIT: email.triggers?.WAIT,
    trigger_WATCH: email.triggers?.WATCH,
    trigger_HOLD: email.triggers?.HOLD,
    trigger_REVIEW_BUY: email.triggers?.REVIEW_BUY,
    trigger_PARTIAL_SELL: email.triggers?.PARTIAL_SELL,

    history_days: dataCfg.history_days,
    retry_max_attempts: dataCfg.retry?.max_attempts,
    retention_days: report.retention_days,
    max_history_files: report.max_history_files,
    intraday_slots: (schedule.intraday_slots || []).join(", "),
    close_slot: schedule.close_slot,
  };
}

function previewChanges() {
  if (!originalSettings) return;

  const original = flattenForComparison(originalSettings);
  const current = Object.fromEntries(
    Object.keys(original).map(key => [key, getValue(key)])
  );

  const labels = {
    target_amount: "目标仓位",
    drop_from_last_buy_trigger_pct: "回撤触发阈值",
    stable_days_required: "横盘确认天数",
    stable_range_pct: "横盘波动范围",
    consecutive_down_warning_days: "连续下跌警告天数",
    normal_add_amount: "普通加仓额度",
    enhanced_add_amount: "增强加仓额度",
    reversal_confirmation_days: "反转确认天数",
    minimum_rebound_pct: "最小反弹幅度",
    first_take_profit_pct: "第一止盈阈值",
    first_sell_position_pct: "第一卖出比例",
    second_take_profit_pct: "第二止盈阈值",
    second_sell_position_pct: "第二卖出比例",
    minimum_core_position_pct: "最小核心仓位比例",
    llm_enabled: "启用本地模型",
    llm_model: "主模型",
    llm_timeout_seconds: "模型超时",
    llm_max_summary_chars: "摘要最大长度",
    llm_num_ctx: "上下文长度",
    llm_num_predict: "最大生成Token",
    llm_keep_alive: "keep_alive",
    email_enabled: "启用邮件",
    email_recipient: "收件人",
    email_include_llm_summary: "包含模型摘要",
    email_include_raw_metrics: "包含原始指标",
    email_force_send: "强制发送邮件",
    email_data_error_dedup_minutes: "DATA_ERROR邮件去重分钟",
    trigger_WAIT: "WAIT触发",
    trigger_WATCH: "WATCH触发",
    trigger_HOLD: "HOLD触发",
    trigger_REVIEW_BUY: "REVIEW_BUY触发",
    trigger_PARTIAL_SELL: "PARTIAL_SELL触发",
    history_days: "历史交易日数量",
    retry_max_attempts: "数据重试次数",
    retention_days: "报告保留天数",
    max_history_files: "历史报告最大数量",
    intraday_slots: "盘中运行时点",
    close_slot: "收盘运行时点",
  };

  const changes = [];
  Object.keys(original).forEach(key => {
    const before = original[key] ?? "";
    const after = current[key] ?? "";
    if (String(before) !== String(after)) {
      changes.push(`${labels[key] || key}: ${before} → ${after}`);
    }
  });

  pendingSettingsPayload = buildPayload();
  previewBox.hidden = false;

  if (!changes.length) {
    previewBox.className = "result-box";
    previewBox.textContent = "没有检测到修改。";
    saveButton.disabled = true;
    return;
  }

  previewBox.className = "result-box success";
  previewBox.innerHTML = `
    <strong>待保存修改</strong>
    <ol class="change-list">${changes.map(item => `<li>${item}</li>`).join("")}</ol>
  `;
  saveButton.disabled = false;
}

async function saveSettings() {
  const response = await fetch("/api/settings/save", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(pendingSettingsPayload),
  });
  const data = await response.json();

  previewBox.hidden = false;
  previewBox.className = `result-box ${data.success ? "success" : "error"}`;
  previewBox.innerHTML = data.success
    ? `<strong>${data.message}</strong><ol class="change-list">${(data.changes || []).map(item => `<li>${item}</li>`).join("")}</ol>`
    : data.message;

  if (data.success) {
    const refreshed = await fetch("/api/settings").then(response => response.json());
    fillSettings(refreshed);
    saveButton.disabled = true;
  }
}

function transactionPayload() {
  return {
    id: document.getElementById("tx-id").value || undefined,
    type: document.getElementById("tx-type").value,
    date: document.getElementById("tx-date").value,
    pricing_mode: document.getElementById("tx-pricing-mode").value,
    amount: document.getElementById("tx-amount").value,
    note: document.getElementById("tx-note").value,
  };
}

function pricingModeLabel(mode) {
  return {
    CONFIRMED: "已确认",
    PENDING: "待确认",
    CLOSE_ESTIMATE: "按收盘价估算",
  }[mode] || mode;
}

function transactionStatusLabel(status) {
  return {
    CONFIRMED: "真实确认",
    PENDING: "待确认",
    PENDING_CLOSE: "等待收盘价",
    ESTIMATED: "收盘价估算",
  }[status] || status || "未知";
}

function formatNumber(value, digits = 4) {
  const number = Number(value);
  return Number.isFinite(number) && number !== 0
    ? number.toFixed(digits)
    : "-";
}


function updateTransactionMode() {
  const type = document.getElementById("tx-type").value;
  const mode = document.getElementById("tx-pricing-mode").value;
  const amountLabel = document.getElementById("tx-amount-label");
  const guidance = document.getElementById("transaction-mode-guidance");

  amountLabel.childNodes[0].nodeValue =
    type === "BUY"
      ? "买入金额（元） "
      : "卖出到账金额（元） ";

  guidance.textContent =
    mode === "CLOSE_ESTIMATE"
      ? "系统会读取该交易日收盘价，自动计算份额和均价。若当天收盘数据尚不可用，将暂存并在后续报告运行时自动补齐。"
      : "这笔记录先作为待确认保存，不进入正式仓位计算；之后可修改为按收盘价自动计算。";
}


function resetTransactionForm() {
  document.getElementById("tx-id").value = "";
  document.getElementById("tx-type").value = "BUY";
  document.getElementById("tx-pricing-mode").value = "CLOSE_ESTIMATE";
  resetTransactionForm();
loadTransactions();
  document.getElementById("tx-amount").value = "";
  document.getElementById("tx-note").value = "";
  document.getElementById("transaction-form-title").textContent = "新增交易记录";
  document.getElementById("cancel-transaction-edit").hidden = true;
  pendingTransaction = null;
  updateTransactionMode();
}


function validateTransactionPayload(payload) {
  if (!payload.date) return "交易日期不能为空。";
  const amount = Number(payload.amount || 0);
  if (!(amount > 0)) return "交易金额必须大于0。";
  return null;
}


function previewTransaction() {
  const payload = transactionPayload();
  const preview = document.getElementById("transaction-preview");
  const error = validateTransactionPayload(payload);
  if (error) {
    preview.hidden = false;
    preview.className = "result-box error";
    preview.textContent = error;
    return;
  }

  const amount = Number(payload.amount || 0);
  const calculatedPrice =
    payload.pricing_mode === "CLOSE_ESTIMATE"
      ? "由交易日收盘价自动计算"
      : "待确认后处理";

  pendingTransaction = payload;
  preview.hidden = false;
  preview.className = "result-box success";
  preview.innerHTML = `
    <strong>${payload.id ? "待修改交易记录" : "待新增交易记录"}</strong>
    <ol class="change-list">
      <li>类型：${payload.type === "BUY" ? "买入" : "卖出"}</li>
      <li>日期：${payload.date}</li>
      <li>价格方式：${pricingModeLabel(payload.pricing_mode)}</li>
      <li>金额：${amount > 0 ? amount.toFixed(2) : "待确认"} 元</li>
      <li>份额与均价：${calculatedPrice}</li>
      <li>备注：${payload.note || "无"}</li>
    </ol>
    <button id="confirm-transaction">${payload.id ? "确认修改" : "确认保存"}</button>
  `;
  document.getElementById("confirm-transaction")
    .addEventListener("click", saveTransaction);
}

async function saveTransaction() {
  const response = await fetch("/api/transactions/save", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(pendingTransaction),
  });
  const data = await response.json();
  const preview = document.getElementById("transaction-preview");
  preview.className = `result-box ${data.success ? "success" : "error"}`;
  preview.textContent = data.message;

  if (data.success) {
    resetTransactionForm();
    await loadTransactions();
  }
}

function transactionCard(record) {
  const statusClass =
    record.status === "CONFIRMED"
      ? "ok-text"
      : (
          record.status === "ESTIMATED"
            ? "warning-text"
            : "bad-text"
        );

  return `
    <article class="transaction-item">
      <div class="transaction-item-main">
        <div>
          <div class="transaction-title-row">
            <strong>${record.type === "BUY" ? "买入" : "卖出"} · ${record.date}</strong>
            <span class="transaction-badge ${record.estimated ? "estimated" : ""}">
              ${transactionStatusLabel(record.status)}
            </span>
          </div>
          <p>
            金额：${formatNumber(record.amount, 2)} 元 ·
            份额：${formatNumber(record.shares, 4)} ·
            均价：${formatNumber(record.price, 6)}
          </p>
          <p class="${statusClass}">
            价格来源：${record.price_source || "-"} ·
            ${record.note || "无备注"}
          </p>
        </div>
        <div class="button-row">
          <button class="secondary-button" data-tx-action="edit" data-id="${record.id}">修改</button>
          <button class="danger-button" data-tx-action="delete" data-id="${record.id}">删除</button>
        </div>
      </div>
    </article>
  `;
}

async function loadTransactions() {
  const list = document.getElementById("transaction-list");
  list.innerHTML = "<p>正在读取交易记录……</p>";
  try {
    const response = await fetch(`/api/transactions?t=${Date.now()}`, {
      cache: "no-store",
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.message || `HTTP ${response.status}`);
    }
    transactionRecords = data.records || [];
    list.innerHTML = transactionRecords.length
      ? transactionRecords.map(transactionCard).join("")
      : '<div class="empty-state">尚无交易记录。</div>';
  } catch (error) {
    list.innerHTML = `<div class="result-box error">读取交易记录失败：${error}</div>`;
  }
}

function editTransaction(id) {
  const record = transactionRecords.find(item => item.id === id);
  if (!record) return;

  document.getElementById("tx-id").value = record.id;
  document.getElementById("tx-type").value = record.type;
  document.getElementById("tx-date").value = record.date;
  document.getElementById("tx-pricing-mode").value =
    record.pricing_mode === "PENDING"
      ? "PENDING"
      : "CLOSE_ESTIMATE";
  document.getElementById("tx-amount").value = record.amount || "";
  document.getElementById("tx-note").value = record.note || "";
  document.getElementById("transaction-form-title").textContent = "修改交易记录";
  document.getElementById("cancel-transaction-edit").hidden = false;
  updateTransactionMode();
  window.scrollTo({top: 0, behavior: "smooth"});
}

async function deleteTransaction(id) {
  const record = transactionRecords.find(item => item.id === id);
  if (!record) return;
  if (!confirm(`确定删除 ${record.date} 的这笔${record.type === "BUY" ? "买入" : "卖出"}记录吗？`)) {
    return;
  }

  const response = await fetch("/api/transactions/delete", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({id}),
  });
  const data = await response.json();
  const preview = document.getElementById("transaction-preview");
  preview.hidden = false;
  preview.className = `result-box ${data.success ? "success" : "error"}`;
  preview.textContent = data.message;
  if (data.success) {
    resetTransactionForm();
    await loadTransactions();
  }
}

document.getElementById("transaction-list")
  .addEventListener("click", event => {
    const button = event.target.closest("button[data-tx-action]");
    if (!button) return;
    if (button.dataset.txAction === "edit") {
      editTransaction(button.dataset.id);
    } else if (button.dataset.txAction === "delete") {
      deleteTransaction(button.dataset.id);
    }
  });

document.getElementById("tx-type")
  .addEventListener("change", updateTransactionMode);
document.getElementById("tx-pricing-mode")
  .addEventListener("change", updateTransactionMode);
document.getElementById("cancel-transaction-edit")
  .addEventListener("click", resetTransactionForm);
document.getElementById("refresh-transactions")
  .addEventListener("click", loadTransactions);

document.getElementById("preview-settings")
  .addEventListener("click", previewChanges);

form.addEventListener("submit", event => {
  event.preventDefault();
  if (!pendingSettingsPayload) return;
  confirmSummary.innerHTML = previewBox.innerHTML;
  dialog.showModal();
});

document.getElementById("cancel-confirm")
  .addEventListener("click", () => dialog.close());

document.getElementById("confirm-save")
  .addEventListener("click", async () => {
    dialog.close();
    await saveSettings();
  });

document.getElementById("preview-transaction")
  .addEventListener("click", previewTransaction);

resetTransactionForm();
loadTransactions();

fetch("/api/settings")
  .then(response => response.json())
  .then(fillSettings)
  .catch(error => {
    previewBox.hidden = false;
    previewBox.className = "result-box error";
    previewBox.textContent = `读取配置失败：${error}`;
  });
