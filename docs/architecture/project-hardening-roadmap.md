# AiMemo 项目加固路线图

本文根据外部静态评价、现有文档和当前代码状态整理。目标不是继续扩功能，而是把真正会影响长期维护、可信度和产品收敛的痛点记录下来，作为下一阶段加固依据。

## 结论摘要

外部评价的总体判断基本成立：AiMemo 已经不是普通 AI 笔记应用，而是一个本地优先的个人记忆 Agent Runtime。Memory Chat Graph、上下文金字塔、长期记忆、Local Operator、任务队列、云同步和导出能力已经形成了较强骨架。

但评价里有几处需要校正：

```text
已实现但文档滞后：
- Cloud Sync 已经有 API、前端同步页、OSS provider、domain push/pull/sync、冲突和备份入口；但 architecture overview 仍写“云端同步暂未实现”。
- 长期记忆已经不止“提取 + 管理”，代码里已有 memory_key、reinforcement_count、evidence_count、evidence_source_ids 和 consolidation judge；但 memories.md 仍主要描述第一版 active/archived。

确实存在的痛点：
- 产品边界过宽，README 能力列表越来越像“全家桶”，一句话定位还需要收敛。
- memory_chat/nodes.py 和 chat_service.py 已经过大，核心 Agent 逻辑存在 God File 风险。
- 长期记忆还没有完整生命周期：候选确认、版本历史、superseded/conflicted 状态和证据表仍缺。
- Retrieval 缺少稳定 eval set，当前更多依赖启发式、单测和人工观察。
- Local Operator 的 approval_required 已有审计字段和策略判断，但高风险写入/命令还没有 graph interrupt 审批闭环。
- 文档状态矩阵缺失，外部读者难分清 implemented / beta / experimental / planned。
- 在线聊天展示与导出 HTML 仍存在样式/交互分叉风险，近期 KaTeX 片段追问样式问题就是一个信号。
```

## 核对表

| 评价点 | 当前判断 | 依据 | 处理方式 |
| --- | --- | --- | --- |
| 项目定位应是 Personal Memory Agent Runtime | 成立 | README 和 architecture overview 已经描述“桌面精灵 + 记忆系统 + Agent Skills” | 收敛产品表述，避免继续横向扩功能 |
| Memory Chat Graph 是核心 | 成立 | `docs/agent/memory-chat-graph.md` 与 `backend/app/agent/graphs/memory_chat/graph.py` 对齐 | 加固核心图，而不是继续堆外围能力 |
| Cloud Sync 文档状态不一致 | 成立 | `frontend/src/features/cloud_sync/`、`backend/app/api/cloud_sync.py` 已存在；`docs/architecture/overview.md` 仍写暂未实现 | 建状态矩阵，并更新架构概览 |
| 长期记忆只停留在提取 + 管理 | 部分过时 | `LongTermMemory` 已有 evidence/reinforcement；`memory_consolidation_service.py` 已做 merge/replace/reinforce | 更新文档，同时补完整 lifecycle |
| Local Operator 安全意识不错但审批闭环不足 | 成立 | 策略、审计、read-before-write 已有；docs 仍写 high-risk approval 后续接 interrupt | 优先做 graph interrupt 审批 |
| Retrieval 缺评测闭环 | 成立 | 现有大量单测覆盖行为，但没有固定 memory/retrieval eval corpus | 建小型 eval set 和 runner |
| God File 风险 | 成立 | `memory_chat/nodes.py` 约 5332 行，`chat_service.py` 约 1452 行 | 先做无行为变化拆分 |

## P0：先停止横向扩张

下一阶段默认不新增大模块。Web Search、Cloud Sync、Voice Studio、Local Operator、Export、Knowledge、Memory Chat 已经足够多。优先目标改为：

```text
让现有能力更可信、更一致、更容易维护。
```

产品一句话建议收敛为：

```text
AiMemo 是本地优先的个人记忆 Agent，帮助用户沉淀笔记、对话、项目上下文和长期偏好，并在可控工具权限下辅助本地工作。
```

Memo Elf、语音、桌面壳、Local Operator 都应服务这句话，而不是各自成为新主线。

## P0：补文档状态矩阵

新增或更新一个状态文档，建议路径：

```text
docs/status.md
```

状态矩阵字段：

```text
模块
状态：implemented / beta / experimental / partial / planned
入口
已覆盖能力
已知限制
关键文档
关键测试
```

首批应覆盖：

| 模块 | 建议状态 | 主要限制 |
| --- | --- | --- |
| Memory Chat Graph | beta | refresh 后自动接管 running turn 仍未完整实现 |
| Long-term Memory | beta | 无版本历史、用户确认队列、superseded/conflicted 状态 |
| Local Operator | experimental | 高风险 write/exec 无 interrupt 审批和 rollback |
| Cloud Sync | experimental | 冲突 resolution 仍弱，真实 OSS 场景需更多错误分类和恢复测试 |
| Knowledge Base | beta | 检索 eval、rerank、慢启动策略仍需固化 |
| Web Search | experimental | Provider 配置、隐私确认和引用质量仍需产品化 |
| Conversation Export | beta | 与在线聊天组件仍有展示分叉风险 |
| Voice Studio | beta | 依赖 DashScope，错误恢复和离线能力有限 |

同时更新 `docs/architecture/overview.md`，不要再把 Cloud Sync 写为“暂未实现”。

## P0：拆分 Memory Chat God File

当前风险：

```text
backend/app/agent/graphs/memory_chat/nodes.py 约 5332 行
backend/app/services/chat_service.py 约 1452 行
```

`nodes.py` 同时承载上下文 worker、检索策略、知识库挂载、附件、Web context、Agent prompt、工具循环、Local Operator policy、persist 和 debug payload。继续堆功能会导致每次改动都影响核心图。

建议先做无行为变化拆分，保持 graph API 和测试不变：

```text
backend/app/agent/graphs/memory_chat/
  graph.py
  state.py
  nodes/
    __init__.py
    load_turn.py
    dispatch.py
    context_l4_memory.py
    context_l3_notes.py
    context_l35_knowledge.py
    context_l2_summary.py
    context_l1_recent.py
    context_l05_adjacent.py
    context_l0_input.py
    context_attachments.py
    context_web.py
    merge.py
    planner.py
    agent_node.py
    tools_node.py
    persist.py
  policies/
    retrieval_policy.py
    tool_policy.py
    prompt_policy.py
  debug/
    payloads.py
```

拆分原则：

```text
- 不改 state schema。
- 不改节点名称。
- 不改 graph 边。
- 先移动纯函数和局部节点，再拆复杂工具链。
- 每一小步跑 memory_chat_graph 相关测试。
```

验收：

```text
pytest backend/tests/test_memory_chat_graph.py
pytest backend/tests/test_chat_stream_service.py
pytest backend/tests/test_context_pyramid.py
```

## P1：长期记忆生命周期

当前已经具备：

```text
memory_key
reinforcement_count
evidence_count
evidence_source_ids
metadata_json
merge / replace / reinforce consolidation decision
active / archived 管理
```

缺口：

```text
候选记忆没有独立状态。
replace 会改写现有记忆，但没有 revision 表保存旧版本。
没有 superseded / conflicted 状态。
用户无法在写入前确认敏感长期记忆。
证据仍用 JSON 字符串保存 source ids，不利于查询、解释和迁移。
```

建议路线：

```text
1. 新增 memory_revisions 或 memory_events 表，记录每次 create/merge/replace/reinforce。
2. 新增 memory_evidence 表，替代或补强 evidence_source_ids 字符串。
3. 扩展 status：candidate / active / superseded / archived / conflicted。
4. conversation_memory_graph 默认写 candidate 或低风险直接 active，高敏感类别进入确认队列。
5. Memory 工坊增加“候选记忆”与“被替代记忆”视图。
```

第一阶段可以只做 revision + evidence，不急着改 UI。

## P1：Memory / Retrieval Eval

当前检索已有 cheap recall、vector upgrade、knowledge mount、recall_cache 和不少单测，但缺少长期维护的评测集。没有 eval，就很难判断一次检索策略调整是进步还是退步。

建议新增：

```text
backend/tests/evals/memory_retrieval_cases.jsonl
backend/tests/test_memory_retrieval_eval.py
```

每条 case：

```json
{
  "id": "preference-food-001",
  "question": "我之前说过想吃什么？",
  "seed": {
    "notes": [],
    "memories": [],
    "conversations": []
  },
  "expected": {
    "must_hit": ["memory:..."],
    "may_answer_unknown": false,
    "requires_followup": false
  }
}
```

首批 50 条：

```text
事实回忆
偏好回忆
身份画像
项目上下文
时间线问题
“继续/这个/刚才那个”指代
冲突更新
知识库挂载边界
无答案时应承认不知道
```

验收指标先保持简单：

```text
命中 expected source
不命中 forbidden source
能区分 should_answer / should_ask / should_unknown
```

## P1：Local Operator 审批闭环

已有基础：

```text
workspace roots
sensitive path block
read-before-write
mtime guard
destructive command block
agent_operations audit
approval_required 字段
request_user_input 工具
```

缺口：

```text
approval_required 目前更多是审计标记，不是强制执行暂停点。
write_file 没有 diff preview。
exec medium/high 没有 interrupt 确认。
没有 rollback / undo 记录。
远程 upload / exec 标记 high approval_required，但还没有统一审批恢复流。
```

建议路线：

```text
1. 新增 approval node，使用 LangGraph interrupt 暂停。
2. write_file 改为 propose -> diff -> approve -> apply。
3. exec_command 对 medium/high 输出 command、cwd、risk_reason，用户确认后执行。
4. 写文件前自动保存 backup snapshot 或 reverse patch。
5. 前端和桌面精灵统一渲染审批卡片，而不是普通文本追问。
```

高风险行为继续直接拒绝，不进入审批：

```text
递归删除
格式化磁盘
权限提升
泄露密钥
绕过 workspace
```

## P1：导出与在线聊天展示收敛

近期导出片段追问里的 KaTeX 公式被 CSS 后代选择器污染，说明导出 HTML 仍然有分叉风险。

已有改善：

```text
导出已迁到 frontend/src/features/chat_view/exportHtmlRenderer.tsx 生成静态 HTML。
导出 snapshot schema 与在线展示更接近。
```

缺口：

```text
导出 HTML 仍内联大段 CSS/JS。
在线聊天和导出阅读不是完全共享同一组组件和样式边界。
缺少针对导出 HTML 的视觉/DOM 回归测试。
```

建议：

```text
1. 为导出渲染增加 fixture snapshot。
2. 用 Playwright 打开导出 HTML，检查 Markdown、KaTeX、Mermaid、片段追问弹窗。
3. 把导出专用 CSS 中容易污染 markdown/katex 的选择器列为 lint/测试规则。
4. 长期把只读消息展示层抽成在线/导出共用组件。
```

## P2：Cloud Sync 稳定化

当前已有：

```text
status / pull / push / sync API
domains API
conflicts API
backups API
LocalMockStorageProvider
Aliyun OSS provider
notes / conversations / memories / config / knowledge domain
```

缺口：

```text
错误分类仍容易漏到 500，前端只看到 Failed to fetch。
冲突 resolution 目前主要是 keep_both，体验弱。
真实 OSS 集成测试默认不跑，边界问题容易到手动联调才暴露。
config domain 已出现过远端 id 与本地 id 不一致导致唯一约束冲突。
```

建议：

```text
1. pull/push/sync 顶层捕获 StorageAuthError、StorageUnavailableError、IntegrityError、invalid payload，写入 state.last_error 并返回结构化错误。
2. 每个 domain 增加“空库从云恢复”测试。
3. config 同步明确按 scope + path 作为业务键，不信任远端自增 id。
4. 真实 OSS 测试做 opt-in，并限制 prefix 到测试 user_id。
5. 冲突 UI 增加本地/远端 diff。
```

## P2：产品边界和导航

当前 README 能力列表已经很长。短期不要继续增加顶层模块，先优化信息架构：

```text
主导航：
  Ai 记
  对话
  知库
  工坊

工坊内：
  任务
  记忆
  语音
  同步
  调试
```

对外文档优先讲：

```text
1. 个人记忆 Agent
2. 本地优先和可控工具
3. 笔记 / 对话 / 知库如何进入记忆
4. 用户如何治理长期记忆
```

弱化“什么都能做”的表达。

## 下一步建议执行顺序

```text
1. docs/status.md + 更新 architecture overview。
2. memory_chat/nodes.py 无行为变化拆分第一批：context workers。
3. 建 memory/retrieval eval fixture，先 20 条，跑通 runner。
4. Local Operator approval 设计落成一份实现文档，随后接 interrupt。
5. 长期记忆 revision/evidence 表设计。
6. 导出 HTML Playwright 回归测试。
```

这 6 步完成前，不建议新增新的大功能模块。
