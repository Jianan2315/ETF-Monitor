# ETF Monitor

这是一个本地运行、无服务器的 ETF 监控工具。

## 主要功能

- Python 获取 ETF 行情；默认可使用模拟数据，也可切换 AKShare；
- Pandas / NumPy 计算阶段涨跌、连续涨跌、近期高点回撤、创新低和稳定天数；
- 确定性规则输出 `WAIT / WATCH / HOLD / REVIEW_BUY / PARTIAL_SELL`；
- 结果保存为 Excel 和 JSON；
- Jinja2 生成静态 HTML 仪表盘，不需要 Flask；
- 默认调用本机 Ollama + Qwen3 生成邮件摘要，也保留手动切回 DeepSeek 的能力；
- 使用 Microsoft Graph + OAuth2 从 Outlook.com 个人邮箱发送邮件；
- 支持 Windows 任务计划程序定时运行。

> 该工具只用于数据整理和人工复核，不会自动交易，也不构成投资建议。

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python run.py
```

运行后打开：

```text
output/index.html
```


> `data/`、`logs/` 和 `output/` 可以不随代码打包，程序会在运行时创建需要的目录。但是 `data/private/msal_token_cache.json` 是已经完成 Microsoft 登录后的本地 Token 缓存；如果你整体替换项目目录，请从旧项目复制回去，否则下一次发送邮件时需要重新完成设备登录授权。不要把该 Token 上传到 GitHub。

## 项目配置

- 应用与数据：`config/app.json`
- ETF、仓位上限与规则：`config/assets.json`
- 邮件与 OAuth：`config/email.json`
- 大模型：`config/llm.json`
- 存储路径：`config/storage.json`
- 买卖记录：`data/transactions.json`
- Microsoft Client ID：`.env`；Ollama 本地模式不需要 AI API Key
- 行情适配器：`src/data_fetcher.py`
- 策略规则：`src/strategy.py`
- 邮件内容和 Graph 请求：`src/email_sender.py`
- Microsoft OAuth：`src/microsoft_graph_auth.py`


## 本地 Ollama 模型设置

当前默认配置位于 `config/llm.json`：

```json
{
  "llm": {
    "enabled": true,
    "provider": "ollama",
    "model": "qwen3:8b",
    "fallback_models": ["qwen3:4b"],
    "base_url": "http://127.0.0.1:11434",
    "think": false,
    "keep_alive": "0"
  }
}
```

运行逻辑：

1. 优先使用 `qwen3:8b`；
2. 如果 8B 未安装、加载失败或超时，会尝试 `qwen3:4b`；
3. 如果 Ollama 没启动或两个模型都失败，程序只跳过模型摘要，邮件仍以确定性策略结果发送；
4. 本地模型只负责把 Python 已确定的结果整理成中文，不参与 `WAIT / WATCH / REVIEW_BUY` 等正式决策。

### 启动前检查

在 PowerShell 中执行：

```powershell
ollama list
```

应能看到：

```text
qwen3:8b
qwen3:4b
```

然后确认 Ollama 正在后台运行。通常 Windows 版 Ollama 启动后会自动提供本地服务；无法连接时可尝试：

```powershell
ollama serve
```

也可以先手动测试：

```powershell
ollama run qwen3:8b
```

### 测试完整邮件流程

模型摘要只有在邮件确实准备发送时才会调用。为了立即测试，在 `config/email.json` 中临时保留：

```json
"debug": {
  "enabled": true,
  "force_send_email": true,
  "verbose_logging": true
}
```

运行：

```powershell
python run.py
```

在 `logs/monitor.log` 中可看到：

```text
Calling local Ollama | Model=qwen3:8b
Local summary generated
```

邮件正文中会出现“模型摘要”。测试完成后，将 `force_send_email` 改回 `false`。

### 切回 DeepSeek（可选）

本地模型是默认方案。仅在需要时，才把 `provider` 改为 `deepseek`，将 `model` 改为 `deepseek-chat`，并在 `.env` 中配置 `DEEPSEEK_API_KEY`。

## Microsoft Graph 邮件设置

本版本不再使用 Outlook SMTP、普通密码或 App Password。邮件通过 Microsoft Graph 的 `sendMail` 接口发送，并使用 OAuth2 Device Code Flow 登录。

### 1. 注册 Microsoft 应用

进入 Microsoft Entra 管理中心或 Azure Portal 的 **App registrations（应用注册）**：

```text
https://entra.microsoft.com/
```

新建应用时：

1. 填写任意名称，例如 `ETF Monitor`；
2. 支持的账户类型选择：
   - `Personal Microsoft accounts only`；或
   - 同时支持组织账户和个人 Microsoft 账户；
3. Redirect URI 可以先留空；
4. 注册完成后，复制 **Application (client) ID**。

把 Client ID 填入 `.env`：

```env
MICROSOFT_CLIENT_ID=你的ApplicationClientID
```

### 2. 开启 Public Client Flow

在应用注册页面进入：

```text
Authentication
→ Advanced settings
→ Allow public client flows
→ Yes
```

Device Code Flow 需要应用被配置为 Public Client。

### 3. 添加 Microsoft Graph 权限

进入：

```text
API permissions
→ Add a permission
→ Microsoft Graph
→ Delegated permissions
→ Mail.Send
```

只需要委托权限 `Mail.Send`。本地脚本不需要 Client Secret。

### 4. 配置发件账户和收件人

编辑 `config/email.json`：

```json
{
  "email": {
    "enabled": true,
    "provider": "microsoft_graph",
    "expected_sender": "你的Outlook邮箱@outlook.com",
    "recipient": "收件邮箱@example.com"
  }
}
```

- `expected_sender`：用于检查你登录的 Microsoft 账户是否正确；不需要时可以留空；
- `recipient`：支持单个邮箱，也支持用逗号分隔多个邮箱；
- 实际发件人由 OAuth 登录账户决定，代码不会保存邮箱密码。

## 第一次授权与测试

为了立即测试邮件，保留或临时设置：

```json
"debug": {
  "enabled": true,
  "force_send_email": true,
  "verbose_logging": true
}
```

然后在可见的终端中运行：

```bash
python run.py
```

第一次运行时，终端会显示一个网址和验证码。通常会提示打开：

```text
https://microsoft.com/devicelogin
```

输入验证码，使用 `expected_sender` 对应的 Microsoft 个人账户登录，并同意 `Mail.Send` 权限。

成功后，Token 会缓存到：

```text
data/private/msal_token_cache.json
```

后续运行通常会静默刷新 Token，不需要每次重新登录。

> Token 缓存包含敏感的刷新凭据，已经加入 `.gitignore`，不要上传到 GitHub，也不要分享给他人。

测试完成后建议改回：

```json
"debug": {
  "enabled": false,
  "force_send_email": false,
  "verbose_logging": true
}
```

## 邮件触发规则

`config/email.json` 中：

```json
"send_mode": "trigger",
"triggers": {
  "WAIT": false,
  "WATCH": true,
  "HOLD": false,
  "REVIEW_BUY": true,
  "PARTIAL_SELL": true
}
```

- `trigger`：只在对应状态为 `true` 时发送；
- `always`：每次程序运行都发送；
- `debug.force_send_email=true`：绕过状态触发，用于测试，但仍要求 `email.enabled=true`。

## 日志

- `logs/monitor.log`：行情、指标、策略、存储和网页生成；
- `logs/email.log`：邮件是否触发、OAuth Token、设备登录提示和 Graph `sendMail` 结果。

Graph 成功发送时通常返回 HTTP `202`。如果失败，`email.log` 会记录 Graph 错误码和请求 ID；邮件失败不会阻止 Excel、JSON 和网页生成。

## 持仓与操作记录

编辑 `data/transactions.json` 记录真实买卖：

```json
{
  "561380": [
    {
      "date": "2026-07-10",
      "type": "BUY",
      "amount": 10000,
      "price": 1.0,
      "note": "首次建仓"
    }
  ]
}
```

程序会自动计算份额、平均成本、当前成本、总收益和最近买入价格，并在网页显示操作时间线。卖出记录建议填写 `shares`，以保证份额计算准确。

## Windows 定时任务

首次 OAuth 授权必须手动在终端完成。确认 Token 缓存已经生成并且邮件测试成功后，再创建 Windows 任务计划：

- 程序：项目虚拟环境中的 `python.exe`；
- 参数：项目内的 `run.py`；
- 起始于：项目根目录；
- 时间：例如交易日 14:35 或 15:10。

如果 Token 缓存失效并且再次需要人工授权，程序会在 `logs/email.log` 中明确记录。

## 官方参考

- MSAL Python：`https://learn.microsoft.com/en-us/entra/msal/python/`
- Device Code Flow：`https://learn.microsoft.com/en-us/entra/identity-platform/msal-authentication-flows`
- Microsoft Graph sendMail：`https://learn.microsoft.com/en-us/graph/api/user-sendmail`


## V9 真实盘中数据、失败告警与历史报告

推荐在 Windows 任务计划程序中建立三个独立任务：

```powershell
python run.py --mode intraday --slot 14:45
python run.py --mode intraday --slot 14:55
python run.py --mode close --slot 15:10
```

盘中任务会获取 ETF 实时快照和当日一分钟分时数据；收盘任务使用正式日线。
数据接口失败时会按照 `config/data.json` 重试。全部失败后：

- 不覆盖 `output/index.html`；
- 不生成新的策略结果；
- 发送 `DATA_ERROR` 邮件；
- 在 `logs/monitor.log` 中记录每次失败。

每次成功运行会同时更新：

```text
output/index.html
output/history.html
output/history/YYYY-MM-DD_HHMM_mode.html
```

`WAIT` 表示下跌尚未停止，需要主动等待；`HOLD` 表示没有触发操作条件，维持现有仓位。


## V9.1 非交易时段回退逻辑

即使手动运行：

```powershell
python run.py --mode intraday --slot 14:45
```

程序也会先根据当前实际时间判断市场状态：

- 09:30 前：`PRE_MARKET`，跳过实时接口，使用最近一个交易日的收盘数据；
- 09:30—15:00：`INTRADAY`，获取实时快照；
- 15:00 后：`CLOSED`，跳过实时接口，使用最新日线；
- 周末：`NON_TRADING_DAY`，使用最近一个交易日的收盘数据。

只有在真实交易时段内，实时快照连续三次获取失败，才会发送 `DATA_ERROR` 邮件。
分钟走势图接口失败不会阻止策略、邮件和网页生成；网页中的“今日走势”会显示为空。


## V10 本地控制台

本版本新增本地网页控制台。正常使用时无需手动输入 Python 命令。

### 启动

双击：

```text
Start ETF Monitor.bat
```

双击后会打开一个可见的命令窗口。该窗口会逐步显示环境检查、依赖安装和服务器启动状态。

程序会自动：

1. 检查 `.venv`；
2. 缺少虚拟环境时自动创建；
3. 缺少依赖时自动安装 `requirements.txt`；
4. 在后台启动本地控制服务；
5. 打开 `http://127.0.0.1:8765/`。

控制服务仅监听 `127.0.0.1`，不会向局域网开放。

也可以双击：

```text
create_desktop_shortcut.bat
```

一次性在桌面创建 `ETF Monitor` 快捷方式。

### 页面

- `/`：控制台首页；
- `/report`：最新报告；
- `/history`：历史报告；
- `/settings`：参数管理与交易录入。

最新报告文件由原来的：

```text
output/index.html
```

调整为：

```text
output/report.html
```

如果旧的 `output/index.html` 存在，而 `report.html` 尚未生成，控制台会自动复制旧报告作为初始 `report.html`。

### 参数保存

参数页面只允许修改白名单字段，包括：

- 目标仓位与策略参数；
- Ollama模型参数；
- 邮件开关、收件人和触发状态；
- 数据重试与历史长度；
- 报告保留规则；
- 调度时间；
- 买入、卖出记录。

保存前会先显示修改摘要。确认后，原配置会备份到：

```text
data/backups/
```

然后通过临时文件验证并原子替换原 JSON。

### Ollama

控制台会检测：

```text
http://127.0.0.1:11434/api/tags
```

如果 Ollama 没有运行，可以点击“启动 Ollama”。不需要提前执行：

```text
ollama run qwen3:8b
```

Ollama API启动后，生成报告时会自动加载所配置的模型。

## V10.1 稳定性修复

### 市场阶段

程序现在按照 `Asia/Shanghai` 时间区分：

```text
PRE_OPEN       开盘前
AM_SESSION     上午交易时段
MIDDAY_BREAK   午间休市
PM_SESSION     下午交易时段
AFTER_CLOSE    收盘后
WEEKEND        周末休市
```

“按当前时间生成”会自动选择正确的数据流程。

“生成盘中报告”只允许在 `09:30—15:00` 之间使用，午间休市仍可生成，行情使用上午最后有效价格。开盘前、收盘后和周末会直接提示，不运行数据接口，也不发送 `DATA_ERROR` 邮件。

“生成收盘报告”最早可在 `15:05` 后使用。15:05 前点击会直接提示市场收盘数据尚未就绪，不运行监控程序，也不发送失败邮件。

### 实时字段容错

实时行情只把“最新价”作为必需字段。以下字段缺失时不会再导致整个报告失败：

```text
涨跌幅
今开
最高
最低
昨收
成交量
成交额
```

程序会按以下顺序补齐：

1. 使用当天一分钟分时数据；
2. 使用最近历史收盘价；
3. 使用最新价构造合理的开盘、最高、最低；
4. 成交量和成交额缺失时按 `0` 处理，并在报告中显示“数据容错说明”。

如果实时总表不可用，但当天分时接口可用，程序会使用分时最后一笔价格生成盘中报告。

### 数据错误邮件去重

相同标的、相同数据接口类别、相同最后可用日期的 `DATA_ERROR`，默认在 15 分钟内只发送一次。配置位于：

```json
{
  "email": {
    "data_error_dedup_minutes": 15,
    "data_error_state_file": "data/notifications/data_error_state.json"
  }
}
```

该时间也可以在本地控制台的参数管理页面修改。

### 控制台环境检测

控制台现在额外显示：

- 当前市场阶段和实际市场时间；
- 东方财富历史行情接口是否可访问；
- 本地日线缓存是否符合当前日期要求；
- `data/latest.json` 的最近生成时间和行情时间；
- 各报告按钮当前是否可用。

### 运行结果判定

控制台不再因为旧的 `output/report.html` 已经存在，就把失败运行误判为成功。每次运行会写入：

```text
data/run_status/latest_run.json
```

只有状态为 `SUCCESS` 且本次程序返回成功时，控制台才会跳转到新报告。


## V10.2 定时运行计划管理

控制台新增：

```text
http://127.0.0.1:8765/schedules
```

也可以在控制台首页点击“定时运行计划”。

页面支持：

- 新增定时运行计划；
- 修改计划名称、模式、时间和运行日期；
- 启用或停用计划；
- 删除计划；
- 立即运行某个计划；
- 重新同步全部计划到 Windows 任务计划程序。

计划配置保存在：

```text
config/schedule_plans.json
```

每个计划会生成独立的执行脚本：

```text
data/scheduled_tasks/<计划ID>.cmd
```

并同步为名称以 `ETFMonitor_` 开头的 Windows 系统任务。系统任务直接调用：

```text
.venv\Scripts\python.exe run.py --mode <模式> --slot <时间>
```

因此控制台和浏览器关闭后，只要 Windows 正常运行，定时计划仍可以触发。

定时运行日志写入：

```text
logs/scheduled_tasks.log
```

模式时间限制：

- `intraday`：只能设在 09:30–15:00；
- `close`：最早 15:05；
- `auto`：全天可设置，由程序在运行时判断市场阶段。

`config/schedule.json` 中的盘中和收盘时点仍作为默认配置使用，但不会自动建立 Windows 系统任务。实际计划统一在“定时运行计划”页面管理。


## V10.3 可见启动器

VBS 启动方式已经移除。请直接双击：

```text
Start ETF Monitor.bat
```

启动窗口会依次显示：

```text
[1/5] 检查 Python
[2/5] 检查或创建 .venv
[3/5] 检查或安装依赖
[4/5] 检查 control_server.py
[5/5] 启动控制台
```

服务器运行期间，请保持这个命令窗口打开。关闭窗口或按 `Ctrl+C` 会停止本地控制台。

发生错误时，窗口不会立即消失，而会停留并显示错误信息。启动过程同时记录到：

```text
logs/launcher.log
```

原来的 `launch_monitor.bat` 仍然保留，用于兼容旧快捷方式，但它只是调用新的：

```text
Start ETF Monitor.bat
```

桌面快捷方式工具现在也会指向 BAT 文件，不再依赖 Windows Script Host 或 VBS。


## V10.4 启动与环境检测修复

### 环境检测 `reading 'models'` 错误

控制台环境卡片现在会对缺失字段进行容错。即使后台少返回某一项，也只会把该项显示为异常，不会让整个环境检测页面崩溃。

该错误通常不是模型不存在，而是浏览器连接到了仍占用 `127.0.0.1:8765` 的旧版控制服务。旧服务返回的数据结构与新版网页不一致，于是新版 JavaScript 读取不到 `models` 字段。

环境接口现在返回控制服务版本号：

```text
10.4
```

网页也会识别版本不一致并显示明确提示。

### 启动窗口不会再一闪而过

双击：

```text
Start ETF Monitor.bat
```

启动器首先检测端口 `8765`。如果发现旧服务仍在运行，会显示对应 PID，并询问：

```text
Stop the existing server and start this version? [Y/N]
```

选择 `Y` 后，会关闭旧服务并启动当前版本。

服务器退出后，不论退出码是否为 0，命令窗口都会停留，直到手动按键关闭。

启动信息写入：

```text
logs/launcher.log
```

控制服务器的完整控制台输出会同时显示在窗口中，并追加写入：

```text
logs/control_server_console.log
```


## V11 可靠启动器

V11 不再使用 PowerShell 管道或 `Tee-Object` 启动控制服务，也不再依赖 VBS。

正常使用只需要双击：

```text
Start ETF Monitor.bat
```

BAT 文件使用纯 ASCII、无 BOM 编码，避免 Windows CMD 把首行识别成：

```text
ï»¿@echo off
```

BAT 只负责：

1. 查找 Python；
2. 创建或复用 `.venv`；
3. 调用 `launcher.py`。

`launcher.py` 使用 Python 标准库完成：

- 检查 8765 端口；
- 查找并关闭旧控制服务；
- 检查和安装依赖；
- 编译检查 `control_server.py`；
- 启动控制服务；
- 同时把控制台输出显示在窗口并写入日志。

不再使用容易出错的 PowerShell 重定向：

```text
2>&1 | Tee-Object
```

服务器停止或启动失败后，BAT 窗口会停留，直到手动按键关闭。

日志文件：

```text
logs/launcher.log
logs/control_server_console.log
```

如果固定日志文件正被其他程序占用，启动器会自动改用带时间戳的备用日志文件，不会因为日志锁定而终止。


## V11.1 数据源稳定性与诊断

控制台环境区域新增：

```text
诊断数据源
```

该诊断会分别测试：

1. `AKShare fund_etf_hist_em`：AKShare封装层，底层数据源为东方财富；
2. 东方财富历史K线接口：程序通过 `requests` 直接请求；
3. `AKShare fund_etf_spot_em`：AKShare实时ETF列表，底层数据源为东方财富。

这可以区分：

```text
AKShare封装调用失败
```

和：

```text
东方财富底层接口本身失败
```

最近一次诊断保存到：

```text
data/diagnostics/latest_data_source.json
```

详细日志写入：

```text
logs/monitor.log
```

日志现在包含：

- AKShare、Requests和Pandas版本；
- 明确的调用链；
- 东方财富直接请求URL；
- 请求参数和非敏感Headers；
- HTTP状态码；
- 最终URL；
- 响应时间和响应字节数；
- 关键响应Headers；
- 响应内容前300字符；
- 错误分类；
- 完整异常堆栈。

历史数据获取顺序调整为：

```text
AKShare fund_etf_hist_em（底层东方财富）
→ 东方财富历史K线接口直接请求
→ 日期符合要求的本地缓存
```

因此，AKShare封装层临时异常时，程序会尝试绕过AKShare，直接请求同一底层数据接口。需要注意，这两个网络路径最终都依赖东方财富；如果东方财富服务器本身拒绝连接，程序仍会回退到本地缓存，而不会把二者误称为两个完全独立的数据提供商。

错误会被分类为：

```text
REMOTE_DISCONNECTED
CONNECTION_ERROR
TIMEOUT
SSL_ERROR
HTTP_403
HTTP_404
JSON_PARSE_ERROR
EMPTY_RESPONSE
SCHEMA_CHANGED
```

控制服务版本更新为：

```text
11.1
```


## V11.2 东方财富接口探测器

控制台环境区域新增：

```text
接口探测器
```

该工具不会生成策略报告，而是针对当前ETF代码测试多种东方财富历史K线请求组合：

- `push2his.eastmoney.com/api/qt/stock/kline/get`
- `push2his.eastmoney.com/api/qt/fund/kline/get`
- `push2his.eastmoney.com/api/qt/quote/kline/get`
- `push2.eastmoney.com/api/qt/stock/kline/get`
- `secid=1.<代码>` 与 `secid=0.<代码>`
- 默认长连接与 `Connection: close`

总计测试16种组合。页面会显示：

- 接口路径；
- `secid`；
- 连接模式；
- 是否成功；
- HTTP状态码；
- 错误分类；
- 请求耗时；
- 返回K线数量；
- 错误或解析说明。

完整结果保存到：

```text
data/diagnostics/latest_api_explorer.json
```

详细URL、参数、Headers、响应摘要和异常堆栈继续写入：

```text
logs/monitor.log
```

该探测器只用于确认哪些候选接口当前可用，不会自动把未经验证的候选接口切换为正式生产数据源。


## V11.3 浏览器接口检查

控制台环境区域新增：

```text
浏览器接口检查
```

该功能会使用 Playwright 启动本机已经安装的 Google Chrome 或 Microsoft Edge，并使用一个临时、独立的浏览器上下文：

1. 打开当前ETF的东方财富行情页面；
2. 监听东方财富相关的 XHR、Fetch 和K线响应；
3. 在页面上下文中直接请求历史K线URL；
4. 记录HTTP状态、资源类型、请求与响应Headers、响应摘要；
5. 对比浏览器上下文与普通 `requests` 的结果。

浏览器窗口默认可见约12秒，然后自动关闭。请在检查结束前不要手动关闭该窗口。

检查结果保存在：

```text
data/diagnostics/latest_browser_inspector.json
```

原始网络记录保存在：

```text
data/diagnostics/browser_network.har
```

HAR文件可能包含Cookie或其他本机会话数据，只应保存在本机，不要公开上传。控制台页面和JSON摘要会对Cookie、Authorization等敏感Header进行隐藏。

解释原则：

- 浏览器获得HTTP 200和K线，而普通 `requests` 被断开：更可能是请求指纹、TLS、Cookie或反自动化策略；
- 浏览器也无法获得K线：更可能是接口本身、网络线路、区域访问策略或服务端限制；
- 页面能打开但页面内跨域fetch失败：还需要结合监听到的页面自身XHR请求判断，不能仅凭CORS错误认定接口不可用。

Playwright Python包已加入 `requirements.txt`。该功能优先使用本机Chrome，其次使用Edge，不要求下载Playwright自带浏览器。


## V11.4 缓存优先与东方财富风控熔断

本版本不再要求每次生成报告都重新下载完整历史日线。

### 盘中流程

在上午盘中、午间休市和下午盘中，只要本地缓存已经覆盖上一交易日：

```text
本地历史日线
→ 获取当前实时行情
→ 拼接今日临时日线
→ 计算指标并生成报告
```

不会再请求东方财富历史K线接口，因此历史接口触发滑动验证时，盘中报告仍可正常生成。

### 收盘流程

15:05以后优先尝试正式历史日线。若历史接口受风控限制，但本地缓存覆盖上一交易日：

```text
上一交易日历史缓存
→ 获取实时收盘快照
→ 补入今日待验证日线
→ 正常生成收盘报告
```

待验证日线会写入缓存，并包含：

```json
{
  "provisional": true,
  "source": "realtime_close_snapshot"
}
```

以后正式历史接口恢复并成功下载时，会用正式日线重新覆盖缓存。

### 历史接口熔断

一旦检测到：

```text
REMOTE_DISCONNECTED
ERR_EMPTY_RESPONSE
ANTI_BOT_CHALLENGE
```

程序会记录：

```text
data/provider_state.json
```

并默认在未来6小时内停止请求东方财富历史接口，直接使用缓存。这样可避免每次运行都进行三次无意义重试并继续触发风控。

配置项：

```json
{
  "history_circuit_breaker_hours": 6,
  "provider_state_file": "data/provider_state.json"
}
```

### 手动验证后刷新

控制台新增：

```text
验证后立即刷新历史缓存
```

完成东方财富滑动验证后，应尽快点击该按钮。程序只尝试一次AKShare历史接口和一次东方财富直接接口：

- 成功：更新本地缓存并解除熔断；
- 失败：保留熔断，不影响缓存优先报告。

### 浏览器检查修正

浏览器检查现在只把严格匹配：

```text
/api/qt/stock/kline/get
```

的HTTP 200响应认定为历史K线数据成功，不再把 `klinepic` 图片误判为成功。

页面出现以下内容时会标记：

```text
ANTI_BOT_CHALLENGE
```

包括：

```text
拖动下方滑块完成拼图
滑动验证
安全验证
完成拼图
```

### DATA_ERROR行为

历史接口失败不再自动等于报告失败。只要：

```text
历史缓存可用
+
实时行情可用
```

报告会正常生成，只在页面和邮件中加入数据来源说明。

只有缓存不可用或严重过期，并且无法取得必要实时数据时，才会发送真正的DATA_ERROR。


## V11.5 交易时段防休眠

控制台首页新增“交易时段防休眠”设置。默认时段为：

```text
工作日 09:00–15:30
```

点击“启用或更新防休眠”后，程序会创建 Windows 系统任务：

```text
ETFMonitor_KeepAwake
```

任务具有以下行为：

- 工作日开始时间自动运行；
- 设置 `WakeToRun=true`，电脑处于普通睡眠状态时会请求唤醒；
- 运行期间调用 Windows `SetThreadExecutionState`，阻止因空闲自动进入系统睡眠；
- 不强制保持屏幕点亮，显示器仍可按照原有电源设置关闭；
- 到结束时间自动退出，并恢复原有休眠策略；
- 控制台和浏览器关闭后，系统任务仍然有效。

状态文件：

```text
data/keep_awake_status.json
```

运行日志：

```text
logs/keep_awake.log
```

配置文件：

```text
config/power.json
```

点击“停用防休眠”会删除系统任务，并结束当前正在运行的防休眠进程。

限制：

- 完全关机状态无法由该功能自动开机；
- 从睡眠中唤醒依赖 Windows 电源计划允许唤醒定时器；
- 笔记本合盖动作属于显式休眠，是否能够保持运行取决于系统的合盖策略；
- 任务使用当前登录用户创建，用户完全注销后不会在无用户会话下运行。
