# Portfolio Monitor V13.2

本地运行的多资产投资组合监控工具。它只提供数据整理与人工复核，不会自动交易，也不构成投资建议。

## 使用

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python control_server.py
```

也可以运行 `Start ETF Monitor.bat`。控制台只监听本机 `127.0.0.1:8765`。

## 日常流程

1. 在“添加资产”中填写资产名称、持有代码、跟踪名称和跟踪代码。
2. 在资产页维护交易记录与投资策略；策略不属于资产身份信息。
3. 在首页选择资产并生成其报告。报告、历史、缓存和交易记录均隔离在该资产目录。
4. 使用“系统工具”运行环境检查、浏览器检查或数据源诊断。

浏览器检查会验证 Playwright 和市场页面，成功后尝试刷新所选资产的历史缓存。数据源诊断只检查行情提供方，二者互不替代。数据获取会按“主提供方 → 备用提供方 → 浏览器 → 缓存”降级，并在报告/日志中保留状态。

## 保持系统唤醒

“保持系统唤醒”固定为北京时间工作日 09:00–15:30，并自动显示本机时区的等效时间。它阻止 Windows 在窗口内因空闲自动睡眠，但允许显示器关闭；它无法解锁 Windows，也不会唤醒已经进入睡眠的电脑。

## 目录

```text
config/global/       全局应用、邮件、LLM、调度和电源配置
config/assets/       每个资产的基本信息与独立 strategy 文件
config/private/      Token 与其他私密配置（不进入 Git）
data/global/         全局运行状态
data/assets/<id>/    交易、缓存、运行数据
output/assets/<id>/  资产最新与历史报告
logs/global/         全局日志
logs/assets/<id>/    资产日志
web/control/         控制台模板与静态资源
web/report/          资产报告模板与静态资源
docs/                使用限制、路线图与版本记录
scripts/windows/     Windows 辅助脚本
```

运行时会自动创建缺失目录。Microsoft Graph token 位于 `config/private/msal_token_cache.json`；不得提交、共享或打包。

## 排障

- Playwright/浏览器不可用：在系统工具中运行环境检查和浏览器检查，并安装 Playwright Chromium。
- 无法发邮件：报告标题旁会显示“邮件发送失败，请验证”链接；打开后检查 Microsoft 应用注册配置与本地私密配置。
- 报告为空：这是该资产尚未生成报告的正常状态；从首页选择该资产后生成第一份报告。

更多限制见 [已知限制](docs/KNOWN_ISSUES.md)。
