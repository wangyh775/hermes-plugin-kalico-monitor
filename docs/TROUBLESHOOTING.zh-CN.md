# 故障排查

简体中文 | [English](TROUBLESHOOTING.md) · [首页](../README.zh-CN.md)

> 依据 main 提交 0c47b84（PR #21 已合并）。Monitor 只读，打印机—Hermes—飞书实机验收尚未完成。

| 现象 | 检查 |
|---|---|
| 开机未启动 | systemctl --user status kalico-monitor；Linger；install.sh --check-autostart |
| 没有 /kalico | Hermes 插件启用、安装副本同步、重载 Gateway、群权限 |
| 状态缺失 | Moonraker 根地址、HTTP/WS、API Key、discover |
| 无受力事件 | watch_nozzle_force；filament_force.events；可选订阅回退；16 条内存保留 |
| 重复通知 | 区分 paused 与 filament_jam_detected；both 路由；对比 event_id / 请求 ID |
| HTTP 502 / no chat_id | Hermes Gateway 日志、目标群 ID、默认继承；模拟测试路由 |
| Agent accepted 无群消息 | 接收不等于投递；检查 Agent 运行与权限 |
| 群中无负载数据 | 默认文本 detail，分析模板须读取 event |
| 待投递队列积压 | 服务日志、路由、端点、签名/时钟；备份前不要删除 DB |

运行 bash install.sh --check、journalctl --user -u kalico-monitor -n 100 及 Hermes Gateway 日志；脱敏密钥。
