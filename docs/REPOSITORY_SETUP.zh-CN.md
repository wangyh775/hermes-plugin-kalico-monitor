# GitHub 仓库开发与发布

仓库：[wangyh775/hermes-plugin-kalico-monitor](https://github.com/wangyh775/hermes-plugin-kalico-monitor)。默认分支为 `main`。新功能或文档修改请从当前 `main` 创建独立分支并通过 PR 审查；历史功能 PR #1–#5 已合并。

## 本地克隆与测试

```bash
git clone https://github.com/wangyh775/hermes-plugin-kalico-monitor.git
cd hermes-plugin-kalico-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
pytest -q
```

## 开发约定

- 核心打印控制、温控及安全阈值均不通过本插件修改；Hermes 工具默认只读。
- 与 Moonraker 的连接采用 WebSocket；关键状态必须先与缓存合并再判断。
- 事件与 SQLite 状态修改应在同一事务内；消息成功送达前不得删除 outbox。
- 不将真实 `MOONRAKER_API_KEY`、`HERMES_WEBHOOK_SECRET`、飞书群 ID 等敏感凭据提交到 Git。
- GitHub Actions 在 Python 3.10 和 3.12 上运行模拟测试。提交 PR 后应等待 CI 完成。
- 真机验收只能在满足实验室安全条件后进行，避免通过制造高温或硬件故障测试通知。

## 里程碑

| 版本 | 内容 |
| --- | --- |
| v0.1.0 | WebSocket 事件监听、Hermes 直接投递、SQLite 告警队列、模拟测试 |
| v0.1.1 | RK3588 + Moonraker + Hermes Gateway + 飞书实机联调及问题修复 |
| v0.2.0 | 故障确认和升级、错误日志关联、健康指标 |
| v0.3.x（当前实验性功能） | 自定义喷嘴力事件桥接、定时报告、只读查询；尚未完成实机验收 |
| 后续规划 | 通用事件规则引擎、多打印机与外部心跳监测 |
