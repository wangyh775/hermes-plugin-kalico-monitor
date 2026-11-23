# Hermes 权限与事件投递策略

本功能以 Hermes Gateway 作为入站命令认证和权限判断入口。Monitor 不管理飞书 App Secret、用户或群组身份；可选的 `KALICO_ALLOWED_CHAT_IDS` 仅用于在 Hermes 认证后再限制 `/kalico` 的 chat_id。留空则继承 Hermes 权限。**请勿把插件函数当作可公开调用的独立鉴权接口**，所有用户命令必须通过 Hermes Gateway。

Webhook 是另一条机器到机器的信任边界；`HERMES_WEBHOOK_SECRET` 仍必须与 Hermes 两条路由共享并执行 HMAC v2 签名验证。远程 Gateway 必须使用 HTTPS。

## 配置

将 `docs/hermes-webhook.example.yaml` 的两条路由**合并**进 Hermes 配置，不要覆盖现有飞书权限或其他路由。

```dotenv
HERMES_WEBHOOK_URL=http://127.0.0.1:8644/webhooks/kalico-alert
HERMES_AGENT_WEBHOOK_URL=http://127.0.0.1:8644/webhooks/kalico-analysis
HERMES_WEBHOOK_SECRET=<同步到两条 Hermes 路由的同一随机密钥>
KALICO_ALLOWED_CHAT_IDS=
KALICO_EVENT_MODES=klippy_shutdown=both,nozzle_force_anomaly=both
```

也可运行 `kalico-monitor config`，在菜单 8 设置分析端点与事件模式。事件配置写在本地 `monitor.env` 中，避免新增 YAML 解析依赖。语法为逗号分隔的 `kind=mode`，支持 `default=direct|agent|both`；未知事件默认直接发送。建议 `complete=direct,paused=direct,print_progress=direct,klippy_shutdown=both,nozzle_force_anomaly=both`。

- **direct**：使用 `kalico-alert`，Hermes `deliver_only: true`，不触发 LLM。
- **agent**：使用 `kalico-analysis`，Hermes `deliver_only: false`，由 Agent 分析后发送。
- **both**：分别建立 direct 和 agent 的 SQLite 投递记录。直接告警成功后即使 Agent 调用失败，也只会重试分析支路。

Agent 路由成功依据为 JSON `status=accepted` 或 `duplicate`，表示 Hermes **已受理**而非模型已运行完成或消息已送达；direct 则要求 `delivered` 或 `duplicate`。HTTP 失败、无效响应均重试。每条支路使用独立 `X-Request-ID`（原事件 ID 加 `:direct` 或 `:agent`），防止两路被 Hermes 误判为同一事件。

旧版 SQLite `alerts` 表保留，新增 `alert_routes` 表，无需手工迁移。第一次分发时按当时规则生成支路；已生成的任务不会因为后续策略调整而静默改变。已有已送达告警不会再投递。

**安全约束**：Monitor 只读 Moonraker/本地状态，不发 G-code；Agent 提示词仅授予只读诊断能力。LLM 结果仅供辅助判断，不可直接参与加热、运动、堵头暂停等安全控制。若指定了 agent/both，先配置并验证 Hermes Agent 路由；如果 Agent 地址缺失，**普通 direct 告警仍会正常投递**，只有 agent 支路记录配置失败并退避重试；只有纯 agent 模式需要等待路由恢复。

## 验证

1. 使用 `kalico-monitor config --show` 检查脱敏配置摘要。
2. 运行 `pytest -q`，确认策略、双路重试和幂等 ID 测试通过。
3. 用 Hermes Gateway 真实路由分别测试 direct、agent 与 both（观察模型调用、飞书消息与 SQLite 分路状态）。
4. 在 Agent 暂时不可用时确认 direct 仍能投递，恢复后 agent 单独重试。
5. 验证 Hermes 飞书准入与斜杠命令限制；不要把 `KALICO_ALLOWED_CHAT_IDS` 误当作 webhook 投递目标。

**限制**：Hermes 的实际配置字段与版本兼容性应以部署实例为准。一键安装与二次配置会安全合并 Hermes 的两条 Kalico 路由；仍未实现 Agent 任务完成回执跟踪。Agent 路由配置 `toolsets: [kalico_monitor]`，仅使用此插件的只读诊断工具。
