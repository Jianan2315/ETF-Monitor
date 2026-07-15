const form = document.getElementById("schedule-form");
const plansList = document.getElementById("plans-list");
const resultBox = document.getElementById("schedule-result");
const dialog = document.getElementById("schedule-confirm-dialog");
const summary = document.getElementById("schedule-confirm-summary");
const cancelEditButton = document.getElementById("cancel-edit");
let plans = [];
let pendingPayload = null;

const modeLabels = {
  auto: "按当前时间自动判断",
  intraday: "盘中报告",
  close: "收盘报告",
};

function selectedWeekdays() {
  return [...document.querySelectorAll('input[name="weekday"]:checked')]
    .map(input => input.value);
}

function weekdayText(plan) {
  return (plan.weekday_labels || []).join("、");
}

function showResult(message, success = true) {
  resultBox.hidden = false;
  resultBox.className = `result-box ${success ? "success" : "error"}`;
  resultBox.textContent = message;
}

function resetForm() {
  form.reset();
  document.getElementById("plan-id").value = "";
  document.getElementById("plan-time").value = "14:45";
  document.getElementById("plan-enabled").checked = true;
  document.querySelectorAll('input[name="weekday"]').forEach(input => {
    input.checked = ["MON", "TUE", "WED", "THU", "FRI"].includes(input.value);
  });
  document.getElementById("form-title").textContent = "新增计划";
  cancelEditButton.hidden = true;
  pendingPayload = null;
}

function buildPayload() {
  return {
    id: document.getElementById("plan-id").value || undefined,
    name: document.getElementById("plan-name").value.trim(),
    mode: document.getElementById("plan-mode").value,
    time: document.getElementById("plan-time").value,
    weekdays: selectedWeekdays(),
    enabled: document.getElementById("plan-enabled").checked,
  };
}

function validatePayload(payload) {
  if (!payload.name) return "计划名称不能为空。";
  if (!payload.time) return "运行时间不能为空。";
  if (!payload.weekdays.length) return "至少选择一个运行日期。";

  const [hour, minute] = payload.time.split(":").map(Number);
  const total = hour * 60 + minute;
  if (payload.mode === "intraday" && (total < 570 || total > 900)) {
    return "盘中报告计划只能设置在09:30至15:00之间。";
  }
  if (payload.mode === "close" && total < 905) {
    return "收盘报告计划最早只能设置为15:05。";
  }
  return null;
}

function planCard(plan) {
  const statusClass = plan.enabled ? "active" : "inactive";
  const taskClass = plan.system_task_exists ? "ok-text" : "bad-text";
  return `
    <article class="schedule-item ${statusClass}">
      <div class="schedule-main">
        <div>
          <div class="schedule-title-row">
            <h3>${plan.name}</h3>
            <span class="schedule-badge">${plan.enabled ? "已启用" : "已停用"}</span>
          </div>
          <p><strong>${plan.time}</strong> · ${modeLabels[plan.mode] || plan.mode}</p>
          <p>${weekdayText(plan)}</p>
          <p class="${taskClass}">
            系统任务：${plan.system_task_exists ? "已同步" : "未找到"}
          </p>
        </div>
        <div class="button-row schedule-actions">
          <button data-action="run" data-id="${plan.id}" class="secondary-button">立即运行</button>
          <button data-action="edit" data-id="${plan.id}" class="secondary-button">修改</button>
          <button data-action="delete" data-id="${plan.id}" class="danger-button">删除</button>
        </div>
      </div>
    </article>
  `;
}

async function loadPlans() {
  plansList.innerHTML = "<p>正在读取计划……</p>";
  try {
    const response = await fetch("/api/schedules");
    const data = await response.json();
    plans = data.plans || [];

    const warning = document.getElementById("platform-warning");
    warning.hidden = data.platform_supported;
    if (!data.platform_supported) {
      warning.textContent = "当前环境不是 Windows。页面可以保存配置，但无法创建 Windows 系统定时任务。";
    }

    plansList.innerHTML = plans.length
      ? plans.map(planCard).join("")
      : '<div class="empty-state">尚未创建定时运行计划。</div>';
  } catch (error) {
    plansList.innerHTML = `<div class="result-box error">读取计划失败：${error}</div>`;
  }
}

function editPlan(id) {
  const plan = plans.find(item => item.id === id);
  if (!plan) return;

  document.getElementById("plan-id").value = plan.id;
  document.getElementById("plan-name").value = plan.name;
  document.getElementById("plan-mode").value = plan.mode;
  document.getElementById("plan-time").value = plan.time;
  document.getElementById("plan-enabled").checked = plan.enabled;
  document.querySelectorAll('input[name="weekday"]').forEach(input => {
    input.checked = plan.weekdays.includes(input.value);
  });
  document.getElementById("form-title").textContent = "修改计划";
  cancelEditButton.hidden = false;
  window.scrollTo({top: 0, behavior: "smooth"});
}

async function deletePlan(id) {
  const plan = plans.find(item => item.id === id);
  if (!plan) return;
  if (!confirm(`确定删除“${plan.name}”吗？对应的 Windows 定时任务也会一起删除。`)) {
    return;
  }

  const response = await fetch("/api/schedules/delete", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({id}),
  });
  const data = await response.json();
  showResult(data.message, data.success);
  if (data.success) {
    resetForm();
    await loadPlans();
  }
}

async function runPlan(id) {
  const plan = plans.find(item => item.id === id);
  if (!plan) return;
  showResult(`正在启动“${plan.name}”……`, true);

  const response = await fetch("/api/schedules/run", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({id}),
  });
  const data = await response.json();
  showResult(data.message, data.success);
}

async function savePlan() {
  const response = await fetch("/api/schedules/save", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(pendingPayload),
  });
  const data = await response.json();
  dialog.close();
  showResult(
    data.system_message
      ? `${data.message}\n${data.system_message}`
      : data.message,
    data.success
  );
  if (data.success) {
    resetForm();
    await loadPlans();
  }
}

form.addEventListener("submit", event => {
  event.preventDefault();
  const payload = buildPayload();
  const error = validatePayload(payload);
  if (error) {
    showResult(error, false);
    return;
  }

  pendingPayload = payload;
  summary.innerHTML = `
    <ol class="change-list">
      <li>计划名称：${payload.name}</li>
      <li>运行模式：${modeLabels[payload.mode]}</li>
      <li>运行时间：${payload.time}</li>
      <li>运行日期：${payload.weekdays.join(", ")}</li>
      <li>状态：${payload.enabled ? "启用" : "停用"}</li>
    </ol>
  `;
  dialog.showModal();
});

plansList.addEventListener("click", event => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const {action, id} = button.dataset;
  if (action === "edit") editPlan(id);
  if (action === "delete") deletePlan(id);
  if (action === "run") runPlan(id);
});

document.getElementById("confirm-schedule-save")
  .addEventListener("click", savePlan);
document.getElementById("cancel-schedule-confirm")
  .addEventListener("click", () => dialog.close());
document.getElementById("refresh-plans")
  .addEventListener("click", loadPlans);
cancelEditButton.addEventListener("click", resetForm);

document.getElementById("sync-all")
  .addEventListener("click", async () => {
    showResult("正在同步全部计划……", true);
    const response = await fetch("/api/schedules/sync", {method: "POST"});
    const data = await response.json();
    showResult(data.message, data.success);
    await loadPlans();
  });

loadPlans();
