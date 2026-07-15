const environmentGrid = document.getElementById("environment-grid");
const runResult = document.getElementById("run-result");

function statusCard(title, entry) {
  const extra = entry.models?.length
    ? `<p>模型：${entry.models.join(", ")}</p>`
    : "";
  return `
    <article class="status-item ${entry.ok ? "ok" : "bad"}">
      <strong>${entry.ok ? "●" : "●"} ${title}</strong>
      <p>${entry.message}</p>
      ${extra}
    </article>
  `;
}

async function loadEnvironment() {
  environmentGrid.innerHTML = "<p>正在检测……</p>";
  try {
    const response = await fetch("/api/environment");
    const data = await response.json();
    environmentGrid.innerHTML = [
      statusCard("Python", data.python),
      statusCard("依赖包", data.packages),
      statusCard("网络", data.network),
      statusCard("Ollama", data.ollama),
      statusCard("Microsoft Token", data.microsoft_token),
      statusCard("最新报告", data.report),
    ].join("");
  } catch (error) {
    environmentGrid.innerHTML = `<div class="result-box error">环境检测失败：${error}</div>`;
  }
}

async function runReport(mode) {
  runResult.hidden = false;
  runResult.className = "result-box";
  runResult.textContent = "正在运行，请不要关闭页面。首次加载本地模型可能需要较长时间……";

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
  }
}

document.querySelectorAll("[data-run-mode]").forEach(button => {
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
