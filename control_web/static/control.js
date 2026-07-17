let selectedAssetId = localStorage.getItem("portfolio.selectedAssetId") || "";

async function loadAssets() {
  const selector = document.getElementById("asset-selector");
  const summary = document.getElementById("asset-summary");
  if (!selector || !summary) return;
  try {
    const data = await fetch("/api/assets").then(r => r.json());
    const assets = data.assets || [];
    selector.innerHTML = '<option value="">全部已启用资产</option>' + assets.map(asset =>
      `<option value="${asset.id}">${asset.name}（${asset.asset_code}）→ ${asset.tracking_type} ${asset.tracking_code}</option>`
    ).join("");
    if (assets.some(asset => asset.id === selectedAssetId)) selector.value = selectedAssetId;
    else selectedAssetId = "";
    summary.innerHTML = assets.map(asset => `
      <article class="status-item ${asset.enabled ? 'ok' : 'bad'}">
        <strong>${asset.name}</strong>
        <span>持有：${asset.asset_code}</span>
        <span>盘中跟踪：${asset.tracking_type} ${asset.tracking_code}</span>
      </article>`).join("");
    selector.addEventListener("change", () => {
      selectedAssetId = selector.value;
      localStorage.setItem("portfolio.selectedAssetId", selectedAssetId);
    });
  } catch (error) {
    summary.textContent = `读取资产列表失败：${error}`;
  }
}

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

    if (data.control_version && data.control_version !== "12.0.0") {
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
      statusCard("交易时段防休眠", data.keep_awake),
      statusCard("Ollama", data.ollama),
      statusCard("Microsoft Token", data.microsoft_token),
      statusCard("最新报告", data.report),
    ].join("");
    updateRunButtons(data.market);
  } catch (error) {
    environmentGrid.innerHTML = `<div class="result-box error">环境检测失败：${error}</div>`;
  }
}


function runStepStatusLabel(status) {
  return {
    SUCCESS: "成功",
    WARNING: "警告",
    FAILED: "失败",
    SKIPPED: "跳过",
  }[status] || status || "未知";
}

function renderRunResult(data) {
  const steps = Array.isArray(data.steps) ? data.steps : [];
  const stepHtml = steps.length
    ? `
      <div class="run-step-list">
        ${steps.map(step => `
          <article class="run-step ${String(step.status || "").toLowerCase()}">
            <div class="run-step-title">
              <strong>${step.label || step.key || "步骤"}</strong>
              <span>${runStepStatusLabel(step.status)}</span>
            </div>
            <p>${step.message || "-"}</p>
          </article>
        `).join("")}
      </div>
    `
    : "<p>后台没有返回步骤明细。</p>";

  const errorHtml = Array.isArray(data.errors) && data.errors.length
    ? `
      <details class="run-error-details">
        <summary>查看错误明细</summary>
        <pre>${data.errors.join("\n")}</pre>
      </details>
    `
    : "";

  return `
    <strong>${data.message || (data.success ? "运行成功" : "运行失败")}</strong>
    <p>
      返回码：${data.return_code ?? "-"} ·
      阶段：${data.phase || "-"} ·
      邮件：${data.email_status || "UNKNOWN"}
    </p>
    ${stepHtml}
    ${errorHtml}
  `;
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
      body: JSON.stringify({mode, slot: "", asset_id: selectedAssetId}),
    });
    const data = await response.json();

    runResult.classList.add(data.success ? "success" : "error");
    runResult.innerHTML = renderRunResult(data);

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


function renderApiExplorer(explorer) {
  const attempts = Array.isArray(explorer?.attempts) ? explorer.attempts : [];
  const rows = attempts.map(item => `
    <tr>
      <td>${item.endpoint_name || "-"}</td>
      <td>${item.secid || "-"}</td>
      <td>${item.connection_mode || "-"}</td>
      <td>${item.ok ? "成功" : "失败"}</td>
      <td>${item.http_status ?? "-"}</td>
      <td>${item.error_category || "-"}</td>
      <td>${item.elapsed_seconds ?? "-"} 秒</td>
      <td>${item.kline_count ?? "-"}</td>
      <td>${item.message || "-"}</td>
    </tr>
  `).join("");

  const successful = Array.isArray(explorer?.successful_candidates)
    ? explorer.successful_candidates
    : [];
  const successSummary = successful.length
    ? `<ol class="change-list">${
        successful.map(item => `
          <li>
            ${item.endpoint_name} · secid=${item.secid} ·
            ${item.connection_mode} · HTTP ${item.http_status} ·
            ${item.kline_count}条K线
          </li>
        `).join("")
      }</ol>`
    : "<p>没有探测到可正常返回K线的候选接口。</p>";

  return `
    <strong>东方财富历史接口探测结果</strong>
    <p>
      标的：${explorer?.symbol || "未知"} ·
      AKShare版本：${explorer?.akshare_version || "未知"} ·
      共测试${explorer?.attempt_count ?? attempts.length}种组合 ·
      成功${explorer?.success_count ?? successful.length}种
    </p>
    <h3>成功候选</h3>
    ${successSummary}
    <div class="diagnostic-table-wrap">
      <table class="diagnostic-table">
        <thead>
          <tr>
            <th>接口</th><th>secid</th><th>连接模式</th><th>状态</th>
            <th>HTTP</th><th>错误分类</th><th>耗时</th><th>K线数</th><th>说明</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <p class="muted">
      每次请求的完整URL、参数、Headers、HTTP状态、响应摘要和异常堆栈已写入
      logs/monitor.log；完整探测结果保存在
      data/diagnostics/latest_api_explorer.json。
    </p>
  `;
}

document.getElementById("explore-api")
  .addEventListener("click", async () => {
    const panel = document.getElementById("api-explorer-result");
    panel.hidden = false;
    panel.className = "result-box";
    panel.textContent = "正在测试不同接口路径、市场前缀和连接模式。该过程可能需要几十秒……";

    try {
      const response = await fetch("/api/data-sources/explore", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
      });
      const data = await response.json();

      if (!response.ok || !data.success) {
        throw new Error(data.message || `HTTP ${response.status}`);
      }

      panel.classList.add(data.explorer?.ok ? "success" : "error");
      panel.innerHTML = renderApiExplorer(data.explorer);
    } catch (error) {
      panel.classList.add("error");
      panel.textContent = `接口探测失败：${error}`;
    }
  });


function renderBrowserInspector(result) {
  const fetchResult = result?.browser_fetch_result || {};
  const events = Array.isArray(result?.network_events)
    ? result.network_events
    : [];
  const rows = events.map(item => `
    <tr>
      <td>${item.status ?? "-"}</td>
      <td>${item.resource_type || "-"}</td>
      <td class="url-cell">${item.url || "-"}</td>
      <td>${item.method || "-"}</td>
    </tr>
  `).join("");

  const cookieNames = Array.isArray(result?.eastmoney_cookie_summary)
    ? result.eastmoney_cookie_summary.map(item => item.name).filter(Boolean)
    : [];

  return `
    <strong>浏览器接口检查结果</strong>
    <p>
      浏览器：${result?.browser || "未知"} ·
      页面：${result?.page_title || "未知"} ·
      滑动验证：${result?.anti_bot_challenge ? "检测到" : "未检测到"} ·
      历史K线数据响应：${result?.successful_kline_response_count ?? 0}/
      ${result?.kline_response_count ?? 0}
    </p>
    <p><strong>结论：</strong>${result?.interpretation || "未生成结论"}</p>
    <p>
      页面内直接fetch：
      ${fetchResult.status ? `HTTP ${fetchResult.status}` : (fetchResult.errorMessage || "无结果")} ·
      耗时 ${fetchResult.elapsedMs ?? "-"} ms ·
      响应长度 ${fetchResult.bodyLength ?? "-"}
    </p>
    <p>
      东方财富Cookie名称：
      ${cookieNames.length ? cookieNames.join(", ") : "未检测到"}
    </p>
    <div class="diagnostic-table-wrap">
      <table class="diagnostic-table">
        <thead>
          <tr><th>HTTP</th><th>资源类型</th><th>URL</th><th>方法</th></tr>
        </thead>
        <tbody>${rows || '<tr><td colspan="4">未捕获到相关网络响应</td></tr>'}</tbody>
      </table>
    </div>
    <p class="muted">
      完整摘要保存在 data/diagnostics/latest_browser_inspector.json；
      原始网络记录保存在 data/diagnostics/browser_network.har。
      HAR可能包含本机浏览器会话信息，请只在本机保存，不要公开上传。
    </p>
  `;
}

document.getElementById("browser-inspector")
  .addEventListener("click", async () => {
    const panel = document.getElementById("browser-inspector-result");
    panel.hidden = false;
    panel.className = "result-box";
    panel.textContent =
      "正在启动可见的Chrome或Edge并监听网络请求。浏览器窗口会自动关闭，请暂时不要手动关闭……";

    try {
      const response = await fetch("/api/data-sources/browser-inspect", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({visible_seconds: 12}),
      });
      const data = await response.json();

      if (!response.ok || !data.success) {
        throw new Error(data.message || `HTTP ${response.status}`);
      }

      panel.classList.add(data.inspection?.ok ? "success" : "error");
      panel.innerHTML = renderBrowserInspector(data.inspection);
    } catch (error) {
      panel.classList.add("error");
      panel.textContent = `浏览器接口检查失败：${error}`;
    }
  });


document.getElementById("refresh-history-cache")
  .addEventListener("click", async () => {
    const panel = document.getElementById("browser-inspector-result");
    panel.hidden = false;
    panel.className = "result-box";
    panel.textContent =
      "正在立即刷新历史缓存。请在完成滑动验证后尽快点击此按钮……";

    try {
      const response = await fetch("/api/data-sources/refresh-history", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
      });
      const data = await response.json();
      panel.classList.add(data.success ? "success" : "error");
      panel.textContent = data.message || "刷新结束。";
      await loadEnvironment();
    } catch (error) {
      panel.classList.add("error");
      panel.textContent = `历史缓存刷新请求失败：${error}`;
    }
  });


function updateKeepAwakeControls(status) {
  if (!status || typeof status !== "object") return;
  document.getElementById("keep-awake-start").value =
    status.start || "09:00";
  document.getElementById("keep-awake-end").value =
    status.end || "15:30";

  const result = document.getElementById("keep-awake-result");
  result.hidden = false;
  result.className = `result-box ${status.enabled ? "success" : ""}`;
  result.textContent = status.message || "尚未读取防休眠状态。";
}

async function loadKeepAwakeStatus() {
  try {
    const response = await fetch(`/api/power/keep-awake?t=${Date.now()}`, {
      cache: "no-store",
    });
    const data = await response.json();
    if (response.ok && data.success) {
      updateKeepAwakeControls(data.status);
    }
  } catch (error) {
    const result = document.getElementById("keep-awake-result");
    result.hidden = false;
    result.className = "result-box error";
    result.textContent = `读取防休眠状态失败：${error}`;
  }
}

document.getElementById("enable-keep-awake")
  .addEventListener("click", async () => {
    const result = document.getElementById("keep-awake-result");
    const start = document.getElementById("keep-awake-start").value;
    const end = document.getElementById("keep-awake-end").value;
    result.hidden = false;
    result.className = "result-box";
    result.textContent = "正在创建或更新 Windows 防休眠计划……";

    try {
      const response = await fetch("/api/power/keep-awake/enable", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({start, end}),
      });
      const data = await response.json();
      result.className = `result-box ${data.success ? "success" : "error"}`;
      result.textContent = data.message || "操作结束。";
      if (data.status) updateKeepAwakeControls(data.status);
      await loadEnvironment();
    } catch (error) {
      result.className = "result-box error";
      result.textContent = `启用防休眠失败：${error}`;
    }
  });

document.getElementById("disable-keep-awake")
  .addEventListener("click", async () => {
    const result = document.getElementById("keep-awake-result");
    result.hidden = false;
    result.className = "result-box";
    result.textContent = "正在停用防休眠计划……";

    try {
      const response = await fetch("/api/power/keep-awake/disable", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
      });
      const data = await response.json();
      result.className = `result-box ${data.success ? "success" : "error"}`;
      result.textContent = data.message || "操作结束。";
      if (data.status) updateKeepAwakeControls(data.status);
      await loadEnvironment();
    } catch (error) {
      result.className = "result-box error";
      result.textContent = `停用防休眠失败：${error}`;
    }
  });

loadKeepAwakeStatus();

loadAssets();
