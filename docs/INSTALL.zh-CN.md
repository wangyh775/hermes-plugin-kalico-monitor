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

编辑配置文件：

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
KALICO_ALLOWED_CHAT_IDS=oc_你的飞书群ID
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
4. 配置 `deliver_only: true`，使常规告警不经过 LLM。Hermes Webhook 具体配置格式和签名兼容性**仍需以部署版本实测为准**，本仓库没有真实飞书端到端验收记录。

当前 Monitor 使用 HMAC-SHA256 签名，发送 `X-Webhook-Timestamp`、`X-Webhook-Signature-V2`、`X-Request-ID`。只有 Hermes 返回 JSON `status=delivered` 或 `duplicate` 才标记为成功；失败会在 SQLite 中排队重试。历史报告在恢复网络后可能补发。

## 4. 安装 Hermes 只读插件

在仓库目录执行：

~~~bash
hermes plugins install "file://$PWD" --enable
hermes plugins list
~~~

插件注册 `/kalico` 命令和 `kalico_status`、`kalico_force`、`kalico_monitor_health` 三个只读工具。命令详情见 [事件与查询](EVENTS.zh-CN.md)。

**权限与配置共享**：`/kalico` 需要 Hermes Gateway 提供可信的 `platform` 和 `source.chat_id` 上下文；远程聊天 ID 不在 `KALICO_ALLOWED_CHAT_IDS` 中时一律拒绝。插件优先读取 Hermes Secret / 进程环境，再读取 `KALICO_MONITOR_ENV_FILE` 指定的文件或默认 `~/.config/kalico-monitor/monitor.env`。因此推荐 Hermes 与 Monitor 同用户运行；若 Hermes 在容器或另一用户下运行，必须显式配置它能读取的路径、数据库访问权限和 Moonraker 地址。**仅在 Monitor 的 systemd 环境里设置变量，不代表 Hermes 进程自动获得这些变量。**

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

## 6. 可选：Kalico PR #13 自定义喷嘴力检测

先在 Kalico 打印机上安装、配置并验证 [Kalico PR #13](https://github.com/wangyh775/kalico/pull/13) 的 `[nozzle_force_monitor]`；该 PR 仍处于 Draft，参数必须经实机标定。可用以下只读查询验证对象存在：

~~~bash
curl -fsS -X POST http://192.168.1.100:7125/printer/objects/query \
  -H 'Content-Type: application/json' \
  -d '{"objects":{"nozzle_force_monitor":null}}'
~~~

然后在 Monitor 配置中启用 `KALICO_WATCH_NOZZLE_FORCE=1` 并重启服务。对象不存在时 Monitor 会尝试回退基础订阅；`/kalico force` 只读查询不依赖 Monitor 的该开关，但要求 Kalico 端存在该对象。

**重要的实际限制**：最新 PR #13 支持 `first_layer_action: z_adjust`、`normal_layer_action: pause` 等分层动作；目前 Hermes 桥接仅在 `triggered=true` 且 `trigger_count` 增加时产生 `nozzle_force_anomaly`。在 PR #13 当前实现中，这主要对应本地请求暂停，**普通 `log` 回弹和成功执行的首层 Z 偏移不会各自触发该告警**。现有 `/kalico force` 也尚未完整展示 `adjust_count`、`auto_z_total` 等首层控制状态。不能将它误认为已覆盖全部自定义事件。详见 [事件与查询](EVENTS.zh-CN.md)。

## 7. 验收与安全

建议依次验证：Moonraker HTTP 与 WebSocket 可访问、Hermes Gateway 运行、Monitor 服务持续心跳、授权聊天能执行只读查询、正常打印完成能投递消息、30 分钟状态报告（若启用）能收到、SQLite 无持续积压。自定义力监控应先用 `log` 模式标定；**不要为了测试通知而制造堵头、超温、撞床或 MCU Shutdown**。

本项目不是安全系统；硬件过温保护、急停、电源切断和本地 Kalico 控制必须独立于 Hermes。更多排障方法见 [故障排查](TROUBLESHOOTING.zh-CN.md)。
