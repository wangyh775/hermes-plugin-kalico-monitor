# 命令参考

简体中文 | [English](COMMANDS.md) · [首页](../README.zh-CN.md)

> 依据 main 提交 0c47b84（PR #21 已合并）。Monitor 只读，打印机—Hermes—飞书实机验收尚未完成。

## CLI

在仓库目录使用 `.venv/bin/kalico-monitor`。除配置/应用与模拟通知投递外，命令仅查询。

| 命令 | 用途 |
|---|---|
| `config` | 交互配置 |
| `config --show` | 脱敏配置 |
| `config --check` | 校验 |
| `config --test` | 连通性 |
| `config --apply` | 确认应用 |
| `events` | 已注册路由 |
| `events --watch` | 基础 Moonraker 通知观察，不包含所有可选对象 |
| `discover` | 列状态对象 |
| `test filament_jam_detected` | 按策略发送模拟通知，可能调用模型 |
| `/kalico status` | 打印状态与可选字段 |
| `/kalico temp` | 喷嘴及热床 |
| `/kalico force` | 旧受力模块状态 |
| `/kalico alerts` | 近期告警及待投递 |
| `/kalico health` | 心跳与队列 |
| `/kalico help` | 帮助 |

快捷命令由 Hermes 授权，可追加群白名单，不调用 LLM。没有 /kalico 运动命令。test 不产生真实打印故障或生产队列记录。
