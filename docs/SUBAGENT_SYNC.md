# SUBAGENT_SYNC.md

cli-claw 子 agent 协同规则（强制）

## 目标
所有子 agent 必须通过文档同步进度、边界和阻塞，避免重复分析、重复实现、架构跑偏。

## 工作模式
- 主线程负责：架构边界、接口裁决、代码合并、最终 review
- 子 agent 负责：单条施工线的分析/实现/测试/文档
- 所有子 agent 完成或卡住时，都必须先更新本文档对应区块，再汇报

## 当前优先级
1. Channel contract（最高优先级）
2. Feishu 首条真实链路迁移
3. Telegram 迁移
4. 其他渠道批量迁移

## 输出要求
每个子 agent 必须写清：
- 负责范围
- 触达文件
- 已完成
- 未完成
- 阻塞点
- 下一步
- 是否需要主线程裁决

## 严禁
- 不经主线程确认擅自扩接口
- 只口头汇报，不更新文档
- 重复实现已有抽象
- 跳过测试

## 统一接口原则
- 出入口一致
- channel 不得直接耦合 provider 实现细节
- provider 不得反向侵入 channel 层
- 所有事件先过统一 schema

---

## [子agent: cli-claw-feishu-migration | 2026-03-09 22:54 GMT+8] Feishu 迁移分析状态（仅方案，不改代码）

### 负责范围
- 对比 iflow-bot 现有 Feishu 实现与 cli-claw 现状
- 在 cli-claw 新架构下给出 Feishu 首条真实链路迁移落点（实现级别清单）
- 明确阻塞点与需要主线程裁决项

### 触达文件（本次仅分析，未改代码）
- 已阅读：
  - `cli-claw/docs/ARCHITECTURE.md`
  - `cli-claw/src/cli_claw/schemas/events.py`
  - `cli-claw/src/cli_claw/runtime/orchestrator.py`
  - `cli-claw/src/cli_claw/kernel/session/service.py`
  - `cli-claw/src/cli_claw/kernel/transcript/service.py`
  - `cli-claw/src/cli_claw/providers/base/provider.py`
  - `cli-claw/src/cli_claw/providers/iflow/provider.py`
  - `iflow-bot/iflow_bot/channels/base.py`
  - `iflow-bot/iflow_bot/channels/manager.py`
  - `iflow-bot/iflow_bot/channels/feishu.py`
  - `iflow-bot/iflow_bot/bus/events.py`
  - `iflow-bot/iflow_bot/engine/loop.py`

### 已完成
- 结论：`cli-claw` 目前仅有 provider scaffold + runtime demo，**无 channel 层实现**，也无 Feishu 接入。
- 结论：`iflow-bot` 的 Feishu 能力已覆盖真实链路关键点：
  - 入站：WebSocket 收消息、去重、媒体下载、消息规范化
  - 出站：text/interactive、图片/文件上传发送
  - 流式：create+patch+fallback、streaming end 清理
  - 会话相关：按 `channel:chat_id` 维度会话与回复关联（`reply_to_id`）
- 差距定位：cli-claw 已有 `MessageInput/Output` 与 `RuntimeEvent`，但尚不足以支撑渠道真实链路（缺 receive_id_type/reply_to/message_id/media 统一约束与出站回执语义）。

### 未完成（待施工）
- 未落地 channel contract（入站/出站/流式/附件/回复）
- 未创建 channel runtime（manager + dispatch）
- 未接入 FeishuChannel 到 runtime 主链路
- 未补齐 Feishu e2e/回归测试

### 建议创建/修改文件（implementation-ready 清单）
> 目标：先打通 Feishu 首条真实链路，再复用到 Telegram/Discord 等。

1) 统一契约（先定）
- **修改** `src/cli_claw/schemas/events.py`
  - 扩展/新增：
    - `InboundEnvelope`（channel, chat_id, sender_id, content, media, message_id, reply_to_id, metadata）
    - `OutboundEnvelope`（channel, chat_id, content, media, reply_to_id, stream flags, metadata）
    - 明确 `message_id`/`reply_to_id` 字段语义
- **修改** `src/cli_claw/schemas/transcript.py`
  - 对齐 `delta/final/error` + media + reply_to + message_id 存档

2) channel 层骨架（新增）
- **新建** `src/cli_claw/channels/__init__.py`
- **新建** `src/cli_claw/channels/base.py`（抽象：start/stop/send/is_allowed）
- **新建** `src/cli_claw/channels/manager.py`（注册、生命周期、outbound 路由）
- **新建** `src/cli_claw/channels/feishu.py`（首条真实链路实现）

3) runtime 对接（修改）
- **修改** `src/cli_claw/runtime/orchestrator.py`
  - 新增从 `InboundEnvelope -> provider -> OutboundEnvelope` 的处理入口
  - 新增 stream 事件透传（delta/final/end）到 outbound dispatcher
- **修改** `src/cli_claw/kernel/session/service.py`
  - 增加 `channel:chat_id -> logical_session_id` 映射辅助（或单独 map service）
- **修改** `src/cli_claw/kernel/transcript/service.py`
  - 支持写入 streaming delta 与最终聚合记录

4) provider 接口衔接（最小改动）
- **修改** `src/cli_claw/providers/base/provider.py`
  - 保持现有 `chat/chat_stream`，但补充 stream event 约束（至少 MESSAGE_DELTA/MESSAGE_FINAL）
- **修改** `src/cli_claw/providers/iflow/provider.py`
  - 由 stub 过渡为 bridge 驱动（后续施工阶段）

5) 测试（新增）
- **新建** `tests/test_channel_contract.py`
- **新建** `tests/test_feishu_channel_unit.py`
- **新建** `tests/test_runtime_feishu_e2e.py`

### 阻塞点
1. **最高阻塞：统一 channel contract 未裁决**
   - 入站/出站字段最小集合
   - streaming 事件与平台 patch/edit 语义统一
   - 附件字段是本地路径、URL、还是 typed object
2. **会话键策略未定**
   - 仅 `channel:chat_id` 还是支持 thread 维度 override
3. **流式回执策略未定**
   - 是否要求 outbound 返回 message_id（用于 Feishu patch）
4. **错误恢复边界未定**
   - 渠道失败重试是在 channel 层还是 runtime 层统一处理

### 下一步
1. 主线程先裁决 `channel contract v1`（字段+语义+错误边界）
2. 子线按清单先落 `channels/base + manager + schemas`（不做平台细节）
3. 再落 `channels/feishu` MVP：文本入站/出站 + reply_to + 基础流式
4. 最后补附件与回归测试，形成可复制模板迁移 Telegram

### 是否需要主线程裁决
- **需要（必须）**：
  1) channel contract v1
  2) session key 规范（是否支持 thread override）
  3) streaming patch message_id 回传规范

---

## [子agent: cli-claw-channel-impl | 2026-03-09 23:34 GMT+8] Channel Contract v1 实施结果

### 负责范围
- 在 `cli-claw` 落地 channel contract v1 的最小可运行版本
- 新增 channel 抽象与 manager
- 将 runtime/orchestrator 从旧 `MessageInput/MessageOutput` 主流程切换到 `InboundEnvelope/OutboundEnvelope`
- 补充契约与 manager 行为测试
- 更新文档与 changelog

### 触达文件
- 新增：
  - `src/cli_claw/schemas/channel.py`
  - `src/cli_claw/channels/__init__.py`
  - `src/cli_claw/channels/base.py`
  - `src/cli_claw/channels/manager.py`
  - `tests/test_channel_contract.py`
  - `tests/test_channel_manager.py`
- 修改：
  - `src/cli_claw/runtime/orchestrator.py`
  - `src/cli_claw/schemas/transcript.py`
  - `src/cli_claw/cli/main.py`
  - `tests/test_smoke.py`
  - `CHANGELOG.md`

### 已完成
1. **Channel Contract v1（provider-agnostic）**
   - 定义 `InboundEnvelope` / `OutboundEnvelope` / `ChannelAttachment`
   - 统一字段：`channel/chat_id/message_id/reply_to_id/text/attachments/metadata`
   - 出站增加最小流式字段：`stream_id/stream_seq/stream_final`

2. **Channel 抽象层**
   - `BaseChannel`：`start/stop/send/is_allowed/is_running`
   - `ChannelManager`：注册、按配置启动、队列分发、直接发送、统一停止

3. **Runtime 最小接线**
   - `RuntimeOrchestrator.handle_inbound(...)` 使用 `InboundEnvelope` 入站
   - provider 返回内容后组装 `OutboundEnvelope` 出站
   - transcript 按 envelope 记录 `message_id/reply_to_id/attachments`

4. **测试补齐**
   - 契约模型默认值与流式字段测试
   - manager 启停、队列分发、策略拒绝测试
   - smoke 测试切换到新 envelope 接口

5. **验证**
   - `uv run pytest -q` 通过（8 passed）

### 未完成
- 未实现具体平台 channel（Feishu/Telegram/Discord）
- 未接入 provider streaming -> channel patch/edit 的完整链路
- 未实现跨 channel 的会话 key 策略扩展（thread override 等）

### 阻塞点
- 无新增硬阻塞；但进入平台迁移前，建议主线程确认：
  - `reply_to_id` 在各平台的映射优先级
  - 流式消息 patch 失败时的统一降级策略

### 下一步
1. 以 `ChannelAttachment` 为核心抽象扩展平台媒体映射
2. 优先接入 Feishu MVP（text/reply_to/stream patch）
3. 在 manager 上补观测事件（发送成功率、失败类型、重试指标）

### 是否需要主线程裁决
- **建议裁决（非阻塞）**：
  1) stream patch 失败降级规范
  2) channel 会话键是否引入 thread 维度

