# ETF Monitor

本项目是一个本地运行、无服务器的 ETF 监控工具骨架。

## 功能
- Python 获取 ETF 行情（默认 mock 数据，可切换 AKShare）
- Pandas/NumPy 计算近 5/10/20 日涨跌、连续下跌、近期高点回撤、创新低、稳定天数
- Python 确定性规则输出 `WAIT / WATCH / REVIEW_BUY`
- 结果保存到 Excel 和 JSON
- Jinja2 生成静态 HTML 仪表盘，无需 Flask
- 可选调用 DeepSeek 生成文字摘要
- 可选通过 Outlook SMTP 发送邮件
- 可通过 Windows 任务计划程序定时运行

> 只用于数据整理与人工复核，不会自动交易，也不构成投资建议。

## 快速开始
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python run.py
```
运行后打开 `output/index.html`。

默认 `config.json` 中 `mock_mode=true`，所以无需 API Key 即可先运行。切换真实行情时改为 `false`。

## 配置
- 应用与数据：`config/app.json`
- ETF、仓位上限与规则：`config/assets.json`
- 邮件策略：`config/email.json`
- 大模型：`config/llm.json`
- 存储路径：`config/storage.json`
- 买卖记录：`data/transactions.json`
- API Key、邮箱密码：`.env`
- 真实行情适配器：`src/data_fetcher.py`
- 判断规则：`src/strategy.py`
- 页面模板：`templates/dashboard.html`
- 样式：`static/style.css`

## Outlook 说明
SMTP 登录是否可用取决于 Microsoft 账户设置；有些账户需要应用密码、启用 SMTP AUTH，或改用 Microsoft Graph。邮件模块独立封装，后续只需替换 `src/email_sender.py`。

## Windows 定时任务
程序填写 Python 可执行文件，参数填写项目内 `run.py`，起始于填写项目目录。可设为交易日 14:35 或 15:10 运行。


## V2 更新

- 买入规则增加“止跌横盘”和“趋势反转确认”两个入口。
- 连续下跌并创新低时保持 WAIT。
- 下跌后上涨1—2日只 WATCH；连续上涨达到配置天数且累计反弹达到阈值后，按正常额度进入 REVIEW_BUY。
- 回撤超过8%且横盘稳定后，按增强额度进入 REVIEW_BUY。
- 页面将规则判断和仓位置顶，删除最新价格卡片。
- 涨跌与1月/3月/6月高点回撤分栏显示，并标出高点日期。
- 增加近6个月可交互走势图，鼠标悬停显示日期、价格和区间涨跌幅。

## V3 页面与持仓配置

走势图现在支持：一周、一个月、三个月、六个月、最近一年和成立以来。`成立以来` 实际展示数据源当前能够返回的最早历史；如需更长数据，请提高 `config.json` 中的 `data.history_days`，并确保数据接口能够返回对应区间。

持仓收益依赖以下配置：

```json
"position": {
  "invested_amount": 10000,
  "average_cost": 1.0000,
  "last_buy_price": 1.0000
}
```

- `invested_amount`：当前仍持有仓位的成本总额；
- `average_cost`：当前整体持仓的平均单位成本；
- `last_buy_price`：最近一笔买入价格。

每次加仓或卖出后应同步更新这些数值，否则网页中的总收益金额和收益率只是旧成本数据下的估算。


## 当前配置架构
程序现在优先读取 `config/` 目录。根目录 `config.json` 仅保留迁移提示。

## 邮件启用
1. 在 `config/email.json` 把 `enabled` 改成 `true`；
2. 修改 `recipient`；
3. 在 `.env` 填写 `OUTLOOK_EMAIL` 与 `OUTLOOK_PASSWORD`；
4. 在 `triggers` 中决定哪些状态发信。未列出的状态默认不发。

`send_mode` 可为：
- `trigger`：只按 `triggers` 发；
- `always`：每次运行都发。

邮件失败只写入日志，不会阻止 Excel、JSON 和网页生成。只有确实需要发送邮件、且 `include_llm_summary=true`、同时 `llm.enabled=true` 时，才会调用大模型。

## 持仓与操作记录
编辑 `data/transactions.json` 记录真实买卖：
```json
{
  "561380": [
    {"date":"2026-07-10","type":"BUY","amount":10000,"price":1.0,"note":"首次建仓"}
  ]
}
```
程序会自动计算份额、平均成本、当前成本、总收益和最近买入价格，并在网页显示操作时间线。卖出记录建议同时填写 `shares`，以保证份额计算准确。


## 邮件调试与强制测试

邮件配置位于 `config/email.json`。正常启用邮件：

```json
{
  "email": {
    "enabled": true,
    "send_mode": "trigger"
  }
}
```

### 强制发送一封测试邮件

为了测试 Outlook SMTP，而不必等待策略进入 `WATCH` 或 `REVIEW_BUY`，临时修改：

```json
"debug": {
  "enabled": true,
  "force_send_email": true,
  "verbose_logging": true
}
```

然后运行：

```bash
python run.py
```

这会绕过状态触发条件，但仍要求：

```json
"email": {
  "enabled": true
}
```

测试完成后，应恢复为：

```json
"debug": {
  "enabled": false,
  "force_send_email": false,
  "verbose_logging": true
}
```

### 日志位置

- `logs/monitor.log`：行情、指标、策略、存储与网页生成；
- `logs/email.log`：邮件是否触发、跳过原因、SMTP 连接、TLS、登录与发送结果。

例如状态没有开启触发时，`email.log` 会写明：

```text
Email skipped | Reason=status HOLD is disabled or missing in email.triggers
```

SMTP 登录失败时，完整错误会记录在 `logs/email.log`，而 Excel、JSON 和网页仍会正常生成。

### Outlook 常见问题

如果日志显示 `SMTPAuthenticationError`，可能原因包括：

- 密码或应用密码不正确；
- Microsoft 账户未启用 SMTP AUTH；
- 账户开启双重验证后需要应用密码；
- 组织账户管理员禁止了 SMTP AUTH。

如果日志显示连接超时，应检查当前网络能否访问：

```text
smtp.office365.com:587
```
