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

---

## 既有部署细节与历史说明（保留）

以下为原有详细说明；若旧版本描述与前面当前源码基准冲突，以前面为准。

# 故障排查与验收

## 分层排查

| 现象 | 排查重点 |
| --- | --- |
| Monitor 启动失败 | `monitor.env` 中 Secret、URL、数据库路径、依赖；非 localhost 的 HTTP Hermes Webhook 会被拒绝 |
| `/kalico status` 失败 | Hermes 插件的 Moonraker 地址、API Key、网络访问 |
| `/kalico health` 数据库不存在 | Monitor 是否启动、Hermes 与 Monitor 是否共享 SQLite 路径及访问权限 |
| 心跳正常但无通知 | WebSocket 订阅、事件是否发生、SQLite 队列、Webhook 签名及飞书权限 |
| 飞书命令无权限 | `KALICO_ALLOWED_CHAT_IDS`、Gateway 可信 `source.chat_id` |
| `/kalico force` 无对象 | Kalico PR #13 是否部署、`[nozzle_force_monitor]` 是否加载 |
| 首层 Z 修正没有独立告警 | 当前桥接仅根据 `triggered` 与 `trigger_count` 生成告警，成功修正不触发 |
| 网络恢复后收到旧报告 | SQLite outbox 补发历史消息，使用 `/kalico status` 查询当前状态 |
| 重复告警 | 是否运行多个 Monitor、系统级与用户级服务同时启动、旧 Cron 未停用 |

## 诊断命令

```bash
systemctl --user status kalico-monitor
journalctl --user -u kalico-monitor -n 100 --no-pager
curl -fsS http://192.168.1.100:7125/server/info
curl -fsS -X POST http://192.168.1.100:7125/printer/objects/query \
  -H 'Content-Type: application/json' \
  -d '{"objects":{"nozzle_force_monitor":null}}'
hermes plugins list
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

把 `192.168.1.100` 换成真实 Moonraker 地址；启用 API Key 时添加相应认证头。系统级服务使用 `sudo systemctl` 和 `sudo journalctl -u kalico-monitor`，不要与用户级服务并行。

## 运行验收

1. AI 主机能通过 HTTP 与 WebSocket 访问 Moonraker。
2. Monitor 心跳更新；Hermes 已授权聊天能查询 `/kalico status`。
3. 正常打印完成后可投递完成通知。
4. 开启定时报告时，打印中按间隔推送，暂停时不继续生成新报告。
5. 自定义力检测先使用 Kalico 的 `log` 模式标定，再考虑 `pause` / `z_adjust`。
6. 检查 SQLite 待发送数量、Webhook 重试及断线恢复。

**安全**：不要通过人为堵头、撞床、超温、MCU Shutdown 或禁用保护装置测试通知。SQLite 保留历史消息和错误上下文，当前无自动清理机制；应保护数据库权限并注意磁盘容量。Hermes 不替代硬件过温保护、急停和 Kalico 本地控制。
