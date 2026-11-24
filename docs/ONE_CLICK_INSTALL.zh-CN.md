# Linux 一键安装与 Hermes 自动集成

本功能适用于 **Hermes CLI 已安装、飞书机器人与模型已在 Hermes 中配置**的 Linux 主机。安装器只管理 Kalico Monitor、名为 `kalico-alert` 和 `kalico-analysis` 的 Hermes Webhook 路由，以及当前用户的 systemd 单元。不会覆盖其他飞书、模型或插件配置。

## 快速安装

```bash
git clone https://github.com/wangyh775/hermes-plugin-kalico-monitor.git
cd hermes-plugin-kalico-monitor
bash install.sh
```

向导提示输入 Moonraker URL（默认 `http://192.168.1.100:7125`）和确认安装。默认名称 `PEEK-01`、关键事件策略 `both`。命令行可覆盖：

```bash
bash install.sh --moonraker-url http://192.168.1.100:7125 --printer PEEK-01 --mode both
```

`--mode direct|agent|both` 只应用到 `klippy_shutdown` 和 `nozzle_force_anomaly`，其余事件保持 `direct`。如需其他细粒度规则，后续仍可运行 `.venv/bin/kalico-monitor config`。

**必要前提：** Hermes 由当前 Linux 用户运行（默认 `~/.hermes/config.yaml`）；已设置可用的飞书平台和消息目标（Hermes Home Channel，例如在飞书内使用 `/set-home`）。安装器优先读取现有 `FEISHU_HOME_CHANNEL`，但不要求你重复填写群 ID；没有单独指定目标时，Webhook 使用 Hermes 现有的 Home Channel。若飞书未配置可用目标，自动投递测试会失败。非默认 Hermes 用户或目录可通过 `--hermes-home` 调整，但需确认 CLI 运行的是同一实例。

## 安装器执行的操作

1. 验证 Python、Hermes CLI 和 systemd；使用 `.venv` 安装依赖。
2. 加载 Hermes `config.yaml`，通过可保留注释的 YAML 解析器**仅合并 Webhook 路由**；保留已有模型、飞书及其他路由。若同名路由并非 Kalico Monitor 创建，拒绝覆盖。
3. 生成或复用 HMAC Secret，将其同时写入 Monitor `monitor.env` 和两条 Hermes 路由；文件写入采用原子替换，旧文件备份为带时间戳的 `.bak.*` 文件并设置权限 `0600`。
4. 通过当前终端执行 Hermes 原生权限确认，再运行 `hermes plugins install "file://<项目路径>" --enable`，重启 Gateway 使配置生效。安装器可能中断当前 Gateway 会话，请选择空闲时段安装。
5. 创建并启用 `~/.config/systemd/user/kalico-monitor.service`，自动启动 Monitor。成功安装后，安装器检查当前 Linux 用户的 Linger；未开启时明确提示并申请 `sudo`/logind 授权，完成后再次校验 `enabled`、`active` 和 `Linger=yes`，使 Monitor 无需用户登录即可随系统启动。
6. 检查 Moonraker `/server/info`、Hermes Webhook `/health`，并向飞书发送一条安全的 `installation_test` 通知。只有 Hermes 确认直接投递成功，才认为通知链路验收通过。

不依赖任何打印机运动、加热或故障模拟。**Agent 的 LLM 推理结果、飞书消息实际被人阅读、打印机真实堵头判定**不在自动验收范围。

## 备份、回滚与限制

安装前会备份已有 Hermes 配置、Monitor env 和 systemd 单元。文件变更失败时尝试回滚已改写文件；**不能保证自动撤销 Hermes 插件安装、已发送的测试消息或已经执行的 Gateway 重启**。出现异常时可从 `.bak.*` 恢复并检查服务：

```bash
systemctl --user status kalico-monitor
journalctl --user -u kalico-monitor -n 100 --no-pager
hermes gateway status
.venv/bin/kalico-monitor config --show
```

`--skip-checks` 仅用于离线安装/测试，跳过远程验证，此时**不得视为已经开箱即用**；`--no-service` 不创建 systemd 单元。安装器不会向外网暴露 Hermes Webhook；Monitor 与 Hermes 建议同主机运行。如需从局域网访问 Moonraker，在 Moonraker 防火墙中只向可信主机开放。

自动安装目前限定 Linux + systemd + Python 3.10+；不提供 Windows 系统服务。更详细的事件配置见 [EVENT_ROUTING.zh-CN.md](EVENT_ROUTING.zh-CN.md)。


## 再次运行交互向导

已安装后使用 `bash install.sh --reconfigure`；也可以使用 `.venv/bin/python -m kalico_monitor.bootstrap --reconfigure`，无需重新注册 Hermes 插件。向导默认读取原有设置，修改时按回车保留，只有明确选择确认才应用；选择取消不会写入磁盘。支持逐项修改打印机 IP / 名称 / API Key、喷嘴力开关、进度报告、停滞与断连阈值、可选会话限制及事件策略。

菜单 **10** 可直接调整两种关键故障的 `direct / agent / both`，不会覆盖打印完成、暂停等其他事件的独立配置。保存前展示脱敏变更摘要；保存时自动备份原配置，只更新必要字段；**仅在 Hermes 路由或插件确实变化时重启 Gateway**，普通 Moonraker 地址等本地配置修改不会重启 Hermes。Monitor 用户服务重新加载后会复测连接和测试通知。若校验或部署出错，尝试恢复原文件及服务；实际已发出的通知和插件外部副作用无法自动撤销。

`--reconfigure --skip-checks` 可跳过连通性验证，仅用于离线排查，不代表已成功投递。配置 Hermes 的飞书账户或大模型仍使用 Hermes 自身的管理工具。


## Review 修复后的行为说明

- **故障优先通知**：`both` 模式会独立持久化 direct 与 agent 支路。即使未配置 Agent 地址，也先投递普通告警；分析任务在 SQLite 中保留并按退避策略重试。
- **重新配置一致性**：Hermes 插件优先读取同用户的 Monitor 共享 `monitor.env`；只有文件未定义字段时才回退到 Hermes Secret / 进程环境，防止旧地址覆盖新 Moonraker IP。配置路径不一致时应设置 `KALICO_MONITOR_ENV_FILE`。
- **飞书目标**：已有 `kalico-alert` / `kalico-analysis` 的 `deliver_extra.chat_id` 不会因为 Hermes Home Channel 改变而被覆盖。要主动更改通知目标，可在安装命令中使用 `--chat-id oc_新目标`，其他 `deliver_extra` 参数保留。
- **插件安装**：Hermes 原生权限确认在终端显示，不再把交互输入隐藏到 subprocess PIPE。网关如需优雅退出，不再受通用 90 秒命令超时中断；应在空闲时执行首次安装。
- **Agent 只读工具**：仅给 Agent Webhook 开启 `kalico_monitor` 工具集，允许使用插件提供的只读状态/力/健康查询，不授予终端、文件写入或打印机控制工具。
- **失败恢复**：除了旧配置快照，还会尽量恢复原有 systemd 启用/停用状态，重新加载服务与 Gateway。已经发送的飞书通知和新安装的第三方插件无法保证回滚；建议初次安装前人工备份整个 `~/.hermes`。

## 无人登录开机自启

**首次安装成功会自动配置 Kalico Monitor 的 Linger**：这是 Linux systemd-logind 的用户级设置，允许该用户启用的 `systemd --user` 服务随系统启动，不必先登录 SSH/桌面。此操作需要相应权限，可能弹出 sudo 授权提示；不修改 Hermes 配置，也不控制 Hermes 服务。**注意：Linger 作用于该用户，其他已经启用的用户服务也可能在无人登录时启动。**

无需重装即可检查或补配：

```bash
bash install.sh --check-autostart   # 只检查，不修改
bash install.sh --enable-autostart  # 如尚未开启，按提示申请授权
loginctl show-user "$(whoami)" -p Linger
systemctl --user is-enabled kalico-monitor
systemctl --user is-active kalico-monitor
```

预期输出为 `Linger=yes`、`enabled`、`active`。最后一个 `active` 只表示当前正在运行；真正的无人登录场景需要在条件允许时重启机器，并在登录前检查 Monitor 是否运行。启动时网络或 Moonraker 尚不可用时，Monitor 会按现有重连策略等待，不要求重启登录会话。

安装被取消、服务未安装或没有启用时，不会触发 Linger 权限操作。使用 `--no-service` 则跳过全部 Monitor systemd 与 Linger 设置。若没有 sudo 权限或 logind 不可用，安装器会明确说明 **Monitor 服务本身已安装，但无人登录开机自启未配置成功**，不会误报成功；管理员可以执行 `sudo loginctl enable-linger "$(whoami)"` 后使用 `--check-autostart` 复查。

本项目只保证 Kalico Monitor 的开机自启，不负责 Hermes Gateway 的后台运行、开机启动或权限配置。


## Webhook 502 的诊断与恢复

安装器将 Moonraker `GET /server/info` 作为**修改文件前的必需预检**。如果这一步失败，立即报告明确的 Moonraker 地址和 HTTP 状态，不改写本地配置。

Hermes Gateway 的 `GET /health` 和飞书通知 `POST /webhooks/kalico-alert` 属于**安装后的外部链路验证**。Gateway 刚重启时可能短暂返回 502，因此健康检查会最多重试 5 次；**测试通知不会重复 POST**，避免通知可能已投递时重复发送。若健康检查或飞书测试仍失败，输出具体的失败阶段，并保留已经验证处于运行状态的 Kalico Monitor systemd 服务及新配置，不因外部服务故障禁用 Monitor。命令以非零状态表示“外部链路待验收”，并非“已回滚”。

在主机上执行以下只读命令定位故障：

```bash
systemctl --user is-enabled kalico-monitor
systemctl --user is-active kalico-monitor
systemctl --user status kalico-monitor --no-pager
curl --noproxy '*' -i http://192.168.31.10:7125/server/info
curl --noproxy '*' -i http://127.0.0.1:8644/health
```

如果 `/server/info` 正常而 `/health` 返回 502，先检查 Hermes Gateway/Webhook 及日志，不必清理或重装 Monitor。恢复后重新运行安装验证，或等待 Monitor 的消息投递重试逻辑恢复。**不要使用 `--skip-checks` 将未验收状态视为成功。**
