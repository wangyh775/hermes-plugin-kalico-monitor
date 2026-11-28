# 安装与部署

本文对应仓库当前 `main`，适用于 Linux + Python 3.10+、Moonraker、Hermes Gateway（插件清单要求 >=0.21.6）。监控服务与 Hermes 插件是**两个独立组件**：安装插件不会启动监控服务；监控服务也不要求和打印机位于同一主机。所有打印控制与硬件保护继续由 Kalico 执行。

## 1. 部署拓扑与网络

推荐把 **Kalico + Moonraker** 放在打印机控制主机，把 **Hermes Gateway + Kalico Monitor + SQLite** 放在另一台 AI 主机（RK3588、Jetson 或普通 Linux PC）。

~~~text
打印机主机 192.168.1.100                  AI 主机 192.168.1.101
Kalico -> Moonraker :7125  --LAN HTTP/WS-->  Kalico Monitor + SQLite
                                            |  localhost HTTP :8644
                                            v
                                         Hermes Gateway --Internet--> 飞书
                                         Hermes 插件 --LAN HTTP--> Moonraker
~~~

- **同机部署**：`MOONRAKER_URL=http://127.0.0.1:7125`。
- **分机部署**：`MOONRAKER_URL=http://192.168.1.100:7125`（IP 仅为示例），Moonraker 必须允许 AI 主机访问；若使用 VLAN，须放通路由/防火墙，**不要求同一网段**。
- Monitor 与 Hermes Gateway 同机时，`HERMES_WEBHOOK_URL=http://127.0.0.1:8644/webhooks/kalico-alert`。当前代码**拒绝非 localhost 的 HTTP Hermes Webhook URL**；若两者也分机部署，必须使用有效的 HTTPS 地址及适当的反向代理/鉴权。为简化运维，推荐 Monitor 与 Hermes 同机。
- Moonraker 的 7125 端口只向可信局域网主机开放，不向公网直接暴露。局域网监控不依赖云端 LLM；飞书推送和云端 LLM 需要相应外网连通性。
- 每个 Monitor 进程目前仅对应一台打印机。**多打印机统一配置/选择命令尚未实现**，不要让多个实例共用同一个 SQLite 数据库。

在**AI 主机**上测试 Moonraker（按实际 IP 修改）：

~~~bash
curl -fsS http://192.168.1.100:7125/server/info
curl -fsS -X POST http://192.168.1.100:7125/printer/objects/query \
  -H 'Content-Type: application/json' \
  -d '{"objects":{"print_stats":null,"webhooks":null}}'
~~~

如启用了 Moonraker API Key，添加 `-H 'X-Api-Key: 你的密钥'`。HTTP 成功不代表 WebSocket 一定正常，仍须检查 Monitor 日志。

## 2. 安装独立监控服务（推荐：systemd 用户服务）

在运行 **Hermes 的同一 Linux 用户**下执行：

~~~bash
git clone https://github.com/wangyh775/hermes-plugin-kalico-monitor.git
cd hermes-plugin-kalico-monitor
bash scripts/setup-user-service.sh
~~~

脚本创建 `.venv`、`~/.config/kalico-monitor/monitor.env`、`~/.config/systemd/user/kalico-monitor.service`，执行 `systemctl --user daemon-reload`，**不会自动启动服务、不会覆盖现有 monitor.env，也不会修改 Hermes 配置**。

**推荐使用交互配置向导**（像 `hermes model` 一样逐项选择，隐藏密钥、校验输入）：

~~~bash
.venv/bin/kalico-monitor config
# 也可以使用：.venv/bin/python -m kalico_monitor.monitor config
# 脱敏查看当前配置：.venv/bin/kalico-monitor config --show
# 只读测试 Moonraker HTTP：.venv/bin/kalico-monitor config --test
~~~

向导提供 Moonraker、Hermes Webhook、数据库/聊天授权、监控策略以及 **[8] LLM 分析与事件策略** 五个配置分组；支持保留现有值、自动生成随机 Webhook Secret、原子写入并设置文件权限 0600。**不会修改 `~/.hermes/config.yaml`，也不会自动启动或重启服务**。如果生成了新 Secret，必须同步到 Hermes Webhook 路由的 `secret`；飞书目标 `chat_id` 仍在 Hermes 配置中单独设置。向导不保证飞书推送已成功，仍需按下文实测。

如需手动编辑，仍可使用：

~~~bash
nano ~/.config/kalico-monitor/monitor.env
~~~

至少检查以下配置（示例；替换真实地址和密钥）：

~~~ini
KALICO_PRINTER_NAME=PEEK-01
MOONRAKER_URL=http://192.168.1.100:7125
MOONRAKER_API_KEY=
HERMES_WEBHOOK_URL=http://127.0.0.1:8644/webhooks/kalico-alert
HERMES_WEBHOOK_SECRET=请替换为随机密钥
KALICO_MONITOR_DB=/home/YOUR_USER/.local/state/kalico-monitor/alerts.sqlite3
KALICO_ALLOWED_CHAT_IDS=
HERMES_AGENT_WEBHOOK_URL=http://127.0.0.1:8644/webhooks/kalico-analysis
KALICO_EVENT_MODES=klippy_shutdown=both,filament_jam_confirmed=both
KALICO_DISCONNECT_SECONDS=25
KALICO_STALL_MINUTES=0
KALICO_PROGRESS_REPORT_MINUTES=30
KALICO_WATCH_NOZZLE_FORCE=0
~~~

密钥生成：

~~~bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
chmod 600 ~/.config/kalico-monitor/monitor.env
~~~

`KALICO_MONITOR_DB` 中的 `YOUR_USER` 必须替换成实际用户名；若脚本首次生成配置，路径已自动替换。所有参数、默认值和约束见 [配置参考](CONFIGURATION.zh-CN.md)。

## 3. 配置 Hermes Webhook 与飞书

1. 确保 Hermes Gateway 已安装并连接飞书机器人，机器人具备向目标群聊发消息的权限。
2. 将 [Webhook 示例](hermes-webhook.example.yaml) **合并**到 `~/.hermes/config.yaml`，不要覆盖已有 `platforms`、飞书配置或其他路由。
3. Webhook 路由名 `kalico-alert` 应与 `HERMES_WEBHOOK_URL` 的路径一致；路由 `secret` 与 `HERMES_WEBHOOK_SECRET` 完全一致；`deliver_extra.chat_id` 使用实际飞书群 ID。
4. 常规 `kalico-alert` 路由配置 `deliver_only: true`，使普通告警不经过 LLM。如启用 `agent`/`both`，还需要 `kalico-analysis` 路由（`deliver_only: false`），并使 `HERMES_AGENT_WEBHOOK_URL` 指向它。Hermes Webhook 具体配置格式和签名兼容性**仍需以部署版本实测为准**，本仓库没有真实飞书端到端验收记录。

当前 Monitor 使用 HMAC-SHA256 签名，发送 `X-Webhook-Timestamp`、`X-Webhook-Signature-V2`、`X-Request-ID`。普通 `direct` 路由只有收到 JSON `status=delivered` 或 `duplicate` 才标记为成功；`agent` 路由收到 `accepted` 或 `duplicate` 时仅标记为已受理；失败会在 SQLite 中排队重试。历史报告在恢复网络后可能补发。

## 4. 安装 Hermes 只读插件

在仓库目录执行：

~~~bash
hermes plugins install "file://$PWD" --enable
hermes plugins list
~~~

插件注册 `/kalico` 命令和 `kalico_status`、`kalico_force`、`kalico_monitor_health` 三个只读工具。命令详情见 [事件与查询](EVENTS.zh-CN.md)。

**权限与配置共享**：`/kalico` 需要 Hermes Gateway 提供可信的 `platform` 和 `source.chat_id` 上下文；入站权限由 Hermes 自身的用户、群组及命令权限管理；只有显式配置了非空 `KALICO_ALLOWED_CHAT_IDS` 时，插件才进一步按 chat_id 拒绝不在列表中的会话。插件优先读取 Hermes Secret / 进程环境，再读取 `KALICO_MONITOR_ENV_FILE` 指定的文件或默认 `~/.config/kalico-monitor/monitor.env`。因此推荐 Hermes 与 Monitor 同用户运行；若 Hermes 在容器或另一用户下运行，必须显式配置它能读取的路径、数据库访问权限和 Moonraker 地址。**仅在 Monitor 的 systemd 环境里设置变量，不代表 Hermes 进程自动获得这些变量。**

## 5. 前台验证与启动

先在仓库目录前台试运行，确认没有配置错误（Ctrl+C 停止）：

~~~bash
set -a
source ~/.config/kalico-monitor/monitor.env
set +a
.venv/bin/python -m kalico_monitor.monitor
~~~

前台停止后再启动后台服务，避免两个实例重复监听：

~~~bash
systemctl --user enable --now kalico-monitor
systemctl --user status kalico-monitor
journalctl --user -u kalico-monitor -f
~~~

希望用户退出登录后服务仍持续运行时：

~~~bash
sudo loginctl enable-linger "$USER"
~~~

在已授权的飞书聊天中尝试 `/kalico status`、`/kalico health`。`/kalico health` 的心跳只能证明 Monitor 进程曾写入心跳，**不能证明 Moonraker 在线或飞书消息实际送达**。

如果采用 `deploy/kalico-monitor.service.example` 安装**系统级服务**，则使用 `sudo systemctl ...` 和 `sudo journalctl -u ...`；不要同时运行系统级与用户级服务。修改 `monitor.env` 后需重启相应服务，当前没有动态热重载。

## 6. 可选：klipper-filament-force 事件接入

在打印机上安装并确认 `filament_force` Moonraker 状态对象存在后，将 `monitor.cfg` 的 `watch_nozzle_force` 设置为 `1`。Monitor 只读订阅 `filament_force.events`；旧的 `nozzle_force_monitor` 不再用于生成告警。详见 [事件配置](EVENT_ROUTER.zh-CN.md)。

## 7. 验收与安全

建议依次验证：Moonraker HTTP 与 WebSocket 可访问、Hermes Gateway 运行、Monitor 服务持续心跳、授权聊天能执行只读查询、正常打印完成能投递消息、30 分钟状态报告（若启用）能收到、SQLite 无持续积压。自定义力监控应先用 `log` 模式标定；**不要为了测试通知而制造堵头、超温、撞床或 MCU Shutdown**。

本项目不是安全系统；硬件过温保护、急停、电源切断和本地 Kalico 控制必须独立于 Hermes。更多排障方法见 [故障排查](TROUBLESHOOTING.zh-CN.md)。

## 8. 既有部署升级与双路通知验收

PR #7、#8 已合并到 `main`。既有安装先备份配置和数据库，更新仓库并重新安装 Python 包：

~~~bash
cp ~/.config/kalico-monitor/monitor.env ~/.config/kalico-monitor/monitor.env.bak
# 在克隆的仓库目录执行
git pull --ff-only
.venv/bin/python -m pip install -e .
.venv/bin/kalico-monitor config
.venv/bin/kalico-monitor config --show
systemctl --user restart kalico-monitor
~~~

交互向导只写 Monitor 本地环境文件，不会自动修改 `~/.hermes/config.yaml`、Secret 或飞书投递目标。需要手工合并 [Hermes Webhook 双路示例](hermes-webhook.example.yaml)，确认两个路由共享同一个 Secret，之后按部署方式重启 Hermes Gateway。开启 `both` 前先确认普通告警能发送，再用安全的模拟事件或独立测试环境验证 Agent 能受理请求；不要主动制造硬件故障。

`agent` 返回 `accepted` 表示排队受理，不代表推理已成功或通知已送达。SQLite 使用 `alerts` + `alert_routes` 分别记录告警与投递分路；任何一路失败只重试该路。升级后的 CLI、Hub、Webhook 配置和投递语义详见 [事件路由](EVENT_ROUTING.zh-CN.md)。GitHub CI 已通过 Python 3.10、3.12 与 Ruff，但实机 Hermes/飞书链路仍未确认。
