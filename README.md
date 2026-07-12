# ETF Monitor 第一版

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
- ETF、仓位与规则：`config.json`
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
