const environmentGrid = document.getElementById("environment-grid");
const runResult = document.getElementById("run-result");
const runButtons = [...document.querySelectorAll("[data-run-mode]")];
const modeGuidance = document.getElementById("mode-guidance");

function statusCard(title, entry) {
  const extra = entry.models?.length
    ? `<p>模型：${entry.models.join(", ")}</p>`
    : "";
  return `
    <article class="status-item ${entry.ok ? "ok" : "bad"}">
      <strong>● ${title}</strong>
      <p>${entry.message}</p>
      ${extra}
    </article>
  `;
}

function updateRunButtons(market) {
  const actions = market?.actions || {};
  const notes = [];
  runButtons.forEach(button => {
    const mode = button.dataset.runMode;
    const action = actions[mode];
    if (!action) return;
    button.disabled = !action.enabled;
    button.title = action.reason || "";
    button.setAttribute("aria-disabled", String(!action.enabled));
    if (!action.enabled && mode !== "auto") {
      notes.push(action.reason);
    }
  });
  modeGuidance.textContent = notes.length
    ? [...new Set(notes)].join(" ")
    : "当前可按市场阶段生成报告。";
}

async function loadEnvironment() {
  environmentGrid.innerHTML = "<p>正在检测……</p>";
  try {
    const response = await fetch("/api/environment");
    const data = await response.json();
    environmentGrid.innerHTML = [
      statusCard("当前市场阶段", data.market),
      statusCard("Python", data.python),
      statusCard("依赖包", data.packages),
      statusCard("基础网络", data.network),
      statusCard("东方财富行情接口", data.eastmoney),
      statusCard("日线缓存", data.cache),
      statusCard("latest.json", data.latest),
      statusCard("Ollama", data.ollama),
      statusCard("Microsoft Token", data.microsoft_token),
      statusCard("最新报告", data.report),
    ].join("");
    updateRunButtons(data.market);
  } catch (error) {
    environmentGrid.innerHTML = `<div class="result-box error">环境检测失败：${error}</div>`;
  }
}

async function runReport(mode) {
  runResult.hidden = false;
  runResult.className = "result-box";
  runResult.textContent = "正在运行，请不要关闭页面。首次加载本地模型可能需要较长时间……";
  runButtons.forEach(button => button.disabled = true);

  try {
    const response = await fetch("/api/report/run", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({mode, slot: ""}),
    });
    const data = await response.json();

    runResult.classList.add(data.success ? "success" : "error");
    runResult.textContent = data.message;

    if (data.success && data.report_url) {
      setTimeout(() => {
        window.location.href = `${data.report_url}?t=${Date.now()}`;
      }, 700);
      return;
    }
  } catch (error) {
    runResult.classList.add("error");
    runResult.textContent = `运行请求失败：${error}`;
  }

  await loadEnvironment();
}

runButtons.forEach(button => {
  button.addEventListener("click", () => runReport(button.dataset.runMode));
});

document.getElementById("refresh-environment")
  .addEventListener("click", loadEnvironment);

document.getElementById("start-ollama")
  .addEventListener("click", async () => {
    environmentGrid.innerHTML = "<p>正在启动 Ollama……</p>";
    const response = await fetch("/api/ollama/start", {method: "POST"});
    const data = await response.json();
    await loadEnvironment();
    alert(data.message);
  });

loadEnvironment();
