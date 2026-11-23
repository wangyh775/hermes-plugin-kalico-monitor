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
4. 自动运行 `hermes plugins install "file://<项目路径>" --enable`，重启 Gateway 使配置生效。安装器可能中断当前 Gateway 会话，请选择空闲时段安装。
5. 创建并启用 `~/.config/systemd/user/kalico-monitor.service`，自动启动 Monitor。
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

菜单 **10** 可直接调整两种关键故障的 `direct / agent / both`，不会覆盖打印完成、暂停等其他事件的独立配置。保存前展示脱敏变更摘要；保存时自动备份原配置，安全更新 Monitor 和 Hermes 路由、重新加载 Gateway 与 systemd 用户服务，并复测连接和测试通知。若校验或部署出错，尝试恢复原文件及服务；实际已发出的通知和插件外部副作用无法自动撤销。

`--reconfigure --skip-checks` 可跳过连通性验证，仅用于离线排查，不代表已成功投递。配置 Hermes 的飞书账户或大模型仍使用 Hermes 自身的管理工具。
