# 测试与验收

简体中文 | [English](TESTING.md) · [首页](../README.zh-CN.md)

> 依据 main 提交 0c47b84（PR #21 已合并）。Monitor 只读，打印机—Hermes—飞书实机验收尚未完成。

## 自动化

```bash
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
ruff check .
```

CI 使用 Python 3.10/3.12 与 Ruff。测试模拟生命周期、可选订阅回退、路由、Outbox 迁移、跨重启重放去重、负载转发、配置与安装，不证明真实消息投递。

## 验收清单

- 检查只读 HTTP/WS、服务心跳及无人登录自启。
- 真实授权群验证 status/temp/alerts/health；分开测试直推及 Agent。确认直推零 LLM；accepted 不是最终投递。
- 监督安全模拟生产方事件；核对源 ID、样本、SQLite 与 Webhook event。普通 paused 独立。
- 在保留期内重连，不重复源事件；长断线单独记录可能丢失。
- off 抑制不允许的待投递；both 双路独立重试。保护密钥并备份 DB。
- Monitor 不发送 G-code；网络/Monitor/Hermes 故障时打印机仍本地保护。

记录版本、脱敏端点及路由、时间、ID、实际结果。目前未记录真实打印机/Hermes/飞书完整验收。
