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

## v0.2.0 快捷查询及统一安装

新增 Hermes Gateway `/kalico status`、`/kalico temp`、`/kalico alerts` 和 `/kalico health`：只读、无需模型推理。监控本体仍为独立 systemd 用户服务。详细安装及权限配置参见 [v0.2 部署说明](docs/V0.2_INSTALL.zh-CN.md)。本版本仍未经过真实飞书网关及 RK3588 实机验收。

### 打印过程定时状态推送（可选）

在 `~/.config/kalico-monitor/monitor.env` 添加或修改：

```ini
# 0=关闭；5~1440=每隔 N 分钟推送一次，建议 30 或 60
KALICO_PROGRESS_REPORT_MINUTES=30
```

修改后执行 `systemctl --user restart kalico-monitor`（系统级服务使用 `sudo systemctl restart kalico-monitor`）。仅打印状态为 `printing` 且 Kalico 为 `ready`、Moonraker 状态最近更新时发送；暂停、Shutdown、断连期间不定时发送，恢复打印后重新计时。不把打印完成、暂停、故障告警当作定时消息。信息来自 Moonraker，包括文件、文件位置估算进度、已打印时间、喷嘴/热床温度、可用时的当前层/总层。进度并非质量判定；没有字段则显示“未知”。默认关闭，不调用 LLM。

每次报告按打印任务代次与序号生成唯一 ID，和下次发送时间一起持久化到 SQLite；Hermes 暂时不可用时沿用现有 Webhook 重试队列。**当系统恢复网络后，队列中未发送的旧报告也可能被补发**；不要将其当作实时状态，实际状态请用 `/kalico status` 查询。

## 自定义喷嘴力回弹事件（Kalico PR #13）

本功能是 **可选、只读** 的监控桥接。Kalico PR #13 的
\`[nozzle_force_monitor]\` 负责分析称重传感器回弹事件、判定疑似堵头，并按自身配置决定是否运行 PAUSE。
Hermes 和 Kalico Monitor **不会发送 G-code 或控制打印机**。

### 开启

1. 在 Kalico 中安装并验证 [PR #13](https://github.com/wangyh775/kalico/pull/13)，
   在 \`printer.cfg\` 配置 \`[nozzle_force_monitor]\`，先使用 \`action: log\` 完成标定。
   自动暂停仅在经过实机验证后才考虑设置 \`action: pause\`。
2. 通过 Moonraker 查询 \`/printer/objects/query\`，确认 \`nozzle_force_monitor\` 状态对象存在。
3. 在 Monitor 的 \`monitor.env\` 增加：
   \`\`\`ini
   KALICO_WATCH_NOZZLE_FORCE=1
   \`\`\`
   默认值为 \`0\`，即关闭。重启 Monitor systemd 服务以应用配置。
4. 在已授权的飞书聊天发送 \`/kalico force\`，不调用 LLM，查询当前喷嘴力、
   基线、偏差、回弹匹配数、阈值、窗口、锁存状态和动作模式。

### 告警触发和去重

Monitor 通过 Moonraker WebSocket 订阅 \`nozzle_force_monitor\`，观察
\`triggered\` 和 \`trigger_count\`，识别新触发并生成
\`nozzle_force_anomaly\` 告警。告警附带打印文件、层数、匹配事件数、
\`window_s\`、力阈值和触发时偏差。该异常只能表明**疑似堵头或其他喷嘴力异常**，
不是堵头确诊；也不会在缺少证据时声称 PAUSE 成功。

每个新事件的通知序号和检查点通过 SQLite 原子持久化；Monitor 进程重启、
Moonraker WebSocket 重连不会为相同的锁存事件重复入队。
若同一时刻或紧接着因 \`action: pause\` 出现普通打印暂停通知，
在同一打印任务的短窗口内优先显示专门的力异常告警，避免刷屏。
独立手动暂停仍正常通知。

如果当前 Kalico 没有加载此可选对象，Monitor 会自动退回原有状态订阅，
不影响正常打印、温度和故障告警；\`/kalico force\` 会提示对象不可用。
该功能只适配 PR #13 的 **时间窗口相似回弹判定**，不是旧版持续超阈值算法。

### 重要限制

- **不能直接把 Klippy 内部的 \`printer.send_event\` 当成 Moonraker 外部事件。**
  当前桥接依赖 \`get_status()\` 状态订阅。
- 通知消息进入已有 Hermes 签名 Webhook 和 SQLite 重试队列；
  Hermes \`deliver_only\` 可零 LLM 投递飞书。
- Klippy 重启、配置重载、监控器重启时，检查点依赖持久化的计数与锁存状态；
  后续建议在 Kalico 侧增加独立 \`last_trigger_id\` / 时间戳，
  使跨 Klippy 重启和重放场景具备更严格的事件身份。
- PR #13 目前仍是 Draft；此集成必须在真实 Kalico、Moonraker、
  Hermes Gateway 和飞书环境下进行端到端验证。
- 既有硬件急停、独立过温保护不受本功能替代。
