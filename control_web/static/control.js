const environmentGrid = document.getElementById("environment-grid");
const runResult = document.getElementById("run-result");
const runButtons = [...document.querySelectorAll("[data-run-mode]")];
const modeGuidance = document.getElementById("mode-guidance");

function statusCard(title, entry) {
  const safeEntry = entry && typeof entry === "object"
    ? entry
    : {
        ok: false,
        message: "后台没有返回这一项检测结果。可能仍在运行旧版控制服务，请关闭旧服务后重新启动当前版本。",
        models: [],
      };

  const models = Array.isArray(safeEntry.models)
    ? safeEntry.models
    : [];
  const extra = models.length
    ? `<p>模型：${models.join(", ")}</p>`
    : "";

  return `
    <article class="status-item ${safeEntry.ok ? "ok" : "bad"}">
      <strong>● ${title}</strong>
      <p>${safeEntry.message || "未返回说明"}</p>
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
    const response = await fetch(`/api/environment?t=${Date.now()}`, {
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`后台返回 HTTP ${response.status}`);
    }
    const data = await response.json();

    if (data.control_version && data.control_version !== "11.1") {
      environmentGrid.innerHTML = `
        <div class="result-box error">
          当前浏览器连接的是旧版控制服务（${data.control_version}）。
          请关闭旧的 ETF Monitor 命令窗口，再重新运行 Start ETF Monitor.bat。
        </div>
      `;
      return;
    }

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


function renderDataSourceDiagnostic(diagnostic) {
  const checks = Array.isArray(diagnostic?.checks) ? diagnostic.checks : [];
  const chain = diagnostic?.call_chain || {};
  const rows = checks.map(item => `
    <tr>
      <td>${item.name || "未知"}</td>
      <td>${item.provider || "未知"}</td>
      <td>${item.ok ? "成功" : "失败"}</td>
      <td>${item.category || "-"}</td>
      <td>${item.elapsed_seconds ?? "-"} 秒</td>
      <td>${item.rows ?? "-"}</td>
      <td>${item.message || "-"}</td>
    </tr>
  `).join("");

  return `
    <strong>数据源诊断结果</strong>
    <p>标的：${diagnostic?.symbol || "未知"} · AKShare版本：${diagnostic?.akshare_version || "未知"}</p>
    <p>历史主调用链：${chain.historical_primary || "未知"}</p>
    <p>历史备用调用链：${chain.historical_secondary || "未知"}</p>
    <p>实时调用链：${chain.realtime_primary || "未知"}</p>
    <div class="diagnostic-table-wrap">
      <table class="diagnostic-table">
        <thead>
          <tr>
            <th>检测项</th><th>数据路径</th><th>状态</th><th>错误分类</th>
            <th>耗时</th><th>记录数</th><th>说明</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <p class="muted">详细URL、请求参数、响应状态和异常堆栈已写入 logs/monitor.log；最近诊断也保存到 data/diagnostics/latest_data_source.json。</p>
  `;
}

document.getElementById("diagnose-data-sources")
  .addEventListener("click", async () => {
    const panel = document.getElementById("data-source-diagnostic");
    panel.hidden = false;
    panel.className = "result-box";
    panel.textContent = "正在分别检测 AKShare 历史接口、东方财富直接历史接口和 AKShare 实时接口……";
    try {
      const response = await fetch("/api/data-sources/diagnose", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.message || `HTTP ${response.status}`);
      }
      panel.classList.add(data.diagnostic?.ok ? "success" : "error");
      panel.innerHTML = renderDataSourceDiagnostic(data.diagnostic);
    } catch (error) {
      panel.classList.add("error");
      panel.textContent = `数据源诊断失败：${error}`;
    }
  });
