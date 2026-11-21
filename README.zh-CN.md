# Hermes Kalico 实时监控插件

**版本：v0.1.0 原型；已完成离线模拟测试，尚未在 RK3588 实机验证。** 本项目面向高温 PEEK 打印机：发生打印完成、错误、Kalico Shutdown、Klippy 断线或 Moonraker 无法连接时，通过 Hermes 将告警推送到飞书群等已连接的消息通道。无需 Obico 和摄像头，普通告警不调用大模型。

## 1. 架构与分工

```text
Kalico -> Moonraker WebSocket -> Kalico Monitor (systemd + SQLite)
                                   -> Hermes Webhook (deliver_only)
                                   -> 飞书群 / 可用的微信通道
```

- **Kalico**：维持原有运动控制、温度控制与硬件保护；不修改固件。
- **Moonraker**：通过 `printer.objects.subscribe` 和事件通知提供打印状态。
- **Monitor**：维护状态机、读取原始错误、判定事件；使用 SQLite 记录待发送告警、重试与去重。
- **Hermes**：已连接的消息渠道转发；需要时再用 AI 查询运行状态、分析故障日志。
- **Mainsail**：保留现有温度图、状态面板与操作界面，不重复建设 Obico Server。

当前只包含 Hermes 只读工具 `kalico_status` 和 `kalico_monitor_health`。监控常驻进程独立启动，并不在插件的 `register()` 中启动无限循环。状态机实现参考 MIT 授权的 [TakeshiTGAL/klipper-print-watch](https://github.com/TakeshiTGAL/hermes-plugin-klipper-print-watch)，见 [NOTICE.md](NOTICE.md)。这不是原仓库的完整克隆，不包含控制、摄像头和旧 Cron 模块。

## 2. RK3588 安装

先进入项目目录并确认 Moonraker：

```bash
git clone https://github.com/wangyh775/hermes-plugin-kalico-monitor.git
cd hermes-plugin-kalico-monitor
curl http://127.0.0.1:7125/printer/info
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

在 Hermes 中配置飞书机器人（或其他消息通道），把 `docs/hermes-webhook.example.yaml` **合并**到 `~/.hermes/config.yaml`，不要覆盖原配置。要求 `deliver_only: true`，普通事件无需模型推理。Webhook 模板的具体字段应结合安装的 Hermes 版本实际验证。生成随机 Secret：

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

复制 `deploy/kalico-monitor.env.example` 到 `/home/YOUR_USER/.config/kalico-monitor/monitor.env`，替换用户路径、Secret 和设备名。先临时运行验证：

```bash
set -a
source /home/YOUR_USER/.config/kalico-monitor/monitor.env
set +a
python -m kalico_monitor.monitor
```

若测试正常，将 `deploy/kalico-monitor.service.example` 中的 `YOUR_USER` 替换为实际用户名，放到 `/etc/systemd/system/kalico-monitor.service` 并执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kalico-monitor
sudo journalctl -u kalico-monitor -f
```

可选安装 Hermes 只读查询工具：

```bash
hermes plugins install file:///home/YOUR_USER/hermes-plugin-kalico-monitor --enable
```

注意：**安装插件不自动启动常驻服务**。若已配置上游 `klipper-print-watch` 的 Cron 轮询，建议先停用以免重复告警。

## 3. 触发逻辑

| 输入 | 处理 |
| --- | --- |
| printing → complete | 普通完成通知；同一任务只发一次 |
| printing → paused | 暂停提醒 |
| printing → error | 严重故障；记录 print_stats.message 和 G-code 上下文 |
| webhooks.state=shutdown / notify_klippy_shutdown | 严重故障；优先附带 state_message |
| notify_klippy_disconnected | 经确认窗口后发 Kalico 连接异常 |
| Moonraker WebSocket 持续不可达 | 发 Moonraker 不可用；恢复后发恢复消息 |
| G-code 响应含 Bad probe / Probing failed | 暂存错误上下文，不单独断定任务已终止 |

Moonraker 订阅返回初始完整快照，随后为增量更新；监控模块先合并缓存再比较，避免误判未更新的字段。打印停滞检测默认关闭，防止正常等待期间误报。

## 4. 测试与限制

```bash
pip install -e '.[test]'
pytest -q
```

测试仅使用模拟 JSON-RPC 数据和本地 SQLite，不通过制造真实超温、失控或 MCU 故障来测试。仍需在 RK3588 上验证 Moonraker、Hermes Gateway 与飞书投递的版本匹配及稳定性。整机断电或 Linux 死机时，本机无法发信，需要外部心跳监测；硬件超温保护、急停、危险能量切断必须独立于 Hermes。

开发与提交约定见 [仓库开发指南](docs/REPOSITORY_SETUP.zh-CN.md)。
