# Second reliability repair / 第二轮可靠性整改

ACK status must be a string with route-specific accepted value. Malformed payload/ACK and permanent HTTP 4xx (except 408/409/425/429) or missing endpoint move only that route to dead letter; transient transport/server failures retain retry. SQLite failed_at is separate from suppressed/delivered; aggregate alert fails if any route permanently fails after all routes terminate. Operator recovery via Outbox.retry_dead_letter retains the original ID; CLI is available to inspect and explicitly requeue. Same incident and route block later source sequences until predecessors terminate; critical successors may bypass after 60 seconds. This preserves urgency with bounded causal waiting, not strict global ordering. Single-printer DB scope remains unchanged.

ACK 必须为字符串且符合路由语义。非法负载/ACK、永久 HTTP 4xx（408/409/425/429 除外）、缺少端点进入该路由死信；临时网络和服务器故障继续重试。failed_at 与投递及过滤独立；所有路由终止后任一路永久失败则告警整体失败。Outbox.retry_dead_letter 可保留原 ID 重新入队，CLI 支持查询和显式重新入队。同故障同路由按源序号阻塞后继，关键后继等待 60 秒后可放行，不保证全局严格顺序。单打印机 DB 范围保持不变。

Production daemon serializes SQLite operations in one dedicated thread (no shared transaction across threads). Other event-loop tasks remain responsive during DB locks; persistence itself still waits for the database, and a failed write fails the supervisor. Independent read-only HTTP status query every 15 s (5 s timeout) with two failed observations before stale alert; valid query emits recovered. This probes Kalico status, not proof of every WebSocket notification; event gaps still require cursor monitoring. Progress reports are suppressed during degraded/stale state. No printer commands are issued.

生产服务将 SQLite 操作串行放入专用线程；数据库锁不再冻结整个异步事件循环，但入库仍需等待，写入失败由监督器处理。独立只读 HTTP 状态查询每 15 s（5 s 超时），连续两次失败产生状态失效告警，查询恢复发恢复事件。只证明状态查询有效，不证明 WebSocket 每条消息均收到；序号缺口仍由游标检测。降级/失效期间暂停进度报告，不执行打印机指令。

The WebSocket separately refreshes subscribed objects every 30 s; a 10 s reply timeout reconnects. Re-subscription has its own outstanding-reply state even if an old snapshot exists. This is independent of the HTTP health probe. Use `kalico-monitor dead-letters` to inspect and `kalico-monitor dead-letters --retry ALERT_ID --route direct` (or agent) after fixing the endpoint; current routing policy must permit the route. Replay uses the same request ID and does not execute printer commands. Verify delivery separately.

WebSocket 每 30 s 独立刷新已订阅对象，10 s 未回复则重连；重新订阅使用独立等待状态，不被旧快照掩盖。与 HTTP 健康探测独立。使用 `kalico-monitor dead-letters` 查询；修复端点后 `kalico-monitor dead-letters --retry ALERT_ID --route direct`（或 agent）重新入队，须符合当前路由策略。复用原请求 ID，不执行打印机指令；投递仍需确认。
