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
