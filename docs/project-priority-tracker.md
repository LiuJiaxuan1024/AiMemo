# 项目优先级追踪表

本文用于追踪 AiMemo / Memo Elf 当前执行项、完成进度和待完成工作。它关注“接下来应该做什么”，和 [项目状态矩阵](./status.md) 互补：状态矩阵描述模块成熟度，本表描述近期维护优先级。

更新时间：2026-06-26

## 状态口径

```text
done        本轮目标已完成，后续只保留常规维护。
in_progress 已经有代码或测试落地，但还没形成完整闭环。
next        建议下一阶段优先启动。
backlog     重要但暂不抢占主线。
blocked     需要外部条件、产品决策或密钥/环境后才能推进。
```

进度是粗粒度工程判断，不代表精确工时燃尽。

## 当前执行项

| ID | 优先级 | 模块 | 目标 | 状态 | 进度 | 已完成 | 待完成 | 验证入口 |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- |
| P0-01 | P0 | Memory / Retrieval Eval | 建立可重复的检索质量基线，避免后续调参靠感觉 | in_progress | 45% | 已新增 memory retrieval eval baseline 和 source-based assertions | 固定 eval 数据集；覆盖 notes / knowledge / memories；输出可读评估报告 | `backend/tests/test_memory_retrieval_eval.py` |
| P0-02 | P0 | Conversation Export | 降低导出 HTML 回归风险，尤其是移动端、片段追问、Mermaid、KaTeX、代码块 | next | 35% | 已修复导出布局、片段追问、Mermaid 缩放、KaTeX 公式和代码横向滚动 | 增加浏览器视觉回归测试；抽离导出 CSS/JS；构造固定 snapshot fixture | `backend/tests/test_conversation_export.py`, `frontend npm run build` |
| P0-03 | P0 | Memory Chat Graph | 继续拆分主对话 graph 的复杂节点，让 planner/context/tool/answer 边界更清楚 | in_progress | 65% | `memory_chat` 已拆出 `context_workers`、`react_agent`、`tools_runtime`、`web_context` 等模块 | 继续瘦身 `tools_runtime.py`、`react_agent.py`；补模块级测试；更新 graph 文档 | `backend/tests/test_memory_chat_graph.py` |
| P0-04 | P0 | Local Operator Approval | 为高风险 write / exec 落地 interrupt 审批、diff preview 和可恢复执行 | next | 15% | 已有 Local Operator 策略、审计、后台任务和结构化 `request_user_input` 规则 | 设计 approval node；实现 diff preview；接入前端确认 UI；补 rollback / cancel 路径 | `backend/tests/test_local_operator_graph.py`, `backend/tests/test_command_background_and_policy.py` |
| P1-01 | P1 | Cloud Sync | 加固 OSS 云同步的错误分类、冲突处理和真实环境可诊断性 | next | 45% | 已有 OSS provider、分域 push/pull/sync、冲突列表和加密备份入口 | 完善错误分类；补冲突 resolution；整理真实 OSS smoke 流程；增强 UI 归因 | `backend/tests/test_cloud_sync_service.py`, `backend/tests/test_full_cloud_sync_domains.py` |
| P1-02 | P1 | Long-term Memory | 增加长期记忆 revision / event / evidence 结构，降低误归并和不可追踪风险 | next | 30% | 已有记忆抽取、归并、reinforce、编辑、停用、恢复和硬删除 | 增加 revision/event 表；候选确认队列；superseded/conflicted 生命周期 | `backend/tests/test_memory_api.py`, `backend/tests/test_conversation_memory_graph.py` |
| P1-03 | P1 | Knowledge Base | 固化知识库检索质量和图片 OCR 稳定性 | backlog | 55% | 已有知识空间、文档导入、chunk 预览、图片 OCR 和知库检索 | 建 knowledge eval set；补 rerank / 慢启动策略；完善失败重试可视化 | `backend/tests/test_knowledge_api.py`, `backend/tests/test_knowledge_search_service.py` |
| P1-04 | P1 | Chat Runtime Recovery | 浏览器刷新后自动接管 running turn，减少流式对话中断体验问题 | backlog | 25% | 已有 checkpoint、草稿消息、job 和 background task 机制 | 前端恢复订阅；后端 running turn 查询；异常状态修复策略 | `backend/tests/test_chat_stream_service.py` |
| P2-01 | P2 | Web Search | 产品化联网搜索配置、引用质量和隐私确认策略 | backlog | 50% | 已接入 Tavily / DashScope provider、缓存、限额、fetch 核验 | Provider 设置 UI；引用展示；私密查询确认体验；失败降级策略 | `backend/tests/test_tavily_web_search_provider.py` |
| P2-02 | P2 | Voice / Desktop Elf | 加固桌面精灵语音和桌面体验，避免远程语音依赖失败时体验断裂 | backlog | 50% | 已有 DashScope ASR/TTS/Voice Design、声线管理、桌面气泡 | 错误恢复；离线/禁用模式；桌面自动化能力边界设计 | `backend/tests/test_voice_api.py`, `backend/tests/test_elf_chat_service.py` |

## 已完成里程碑

| 时间 | 模块 | 结果 | 代表提交 / 验证 |
| --- | --- | --- | --- |
| 2026-06 | Web Search | 从设计讨论落地到 Tavily / DashScope provider，并接入 Memory Chat Lx.web | `3d39911` |
| 2026-06 | Dev Startup | `aimemo start/restart` 等待服务 ready 后打印实际端口，Linux Vite watcher 默认 polling | `5106792` |
| 2026-06 | Conversation Export | 导出 HTML 支持片段追问、移动端侧栏、Mermaid 交互、代码横向滚动和 KaTeX 公式 | `5106792`, `215297b`, `7803d74` |
| 2026-06 | Cloud Sync | 修复 cloud sync pull 关键问题，并补全全域同步测试覆盖 | `7803d74` |
| 2026-06 | Memory Chat Split | 初步拆分 `memory_chat` 节点代码，降低单文件复杂度 | `4a75812` |
| 2026-06 | Retrieval Eval | 增加 memory retrieval eval baseline 和 source-based assertions | `ffd329d`, `29207df` |

## 待排期候选池

| 模块 | 候选项 | 为什么重要 | 建议进入条件 |
| --- | --- | --- | --- |
| Auth / Profile | 用户登录、本地 profile、多用户数据隔离 | 云同步、长期记忆和桌面技能最终都需要身份边界 | Cloud Sync 稳定后启动 |
| Memory UX | 记忆候选确认、冲突解释、证据溯源 UI | 长期记忆会直接影响回答质量和信任感 | revision/event 表落地后启动 |
| Local Operator UX | diff preview、文件树、命令风险说明 | 本地操作能力越强，越需要用户可理解的控制面 | approval interrupt 后启动 |
| Export Architecture | 把导出 HTML 的 CSS/JS/renderer 分文件，建立 snapshot fixture | `exportHtmlRenderer.tsx` 已成为维护热点 | 视觉回归测试前后都可推进 |
| Cloud Sync UX | 冲突三方对比、按 domain 恢复、同步历史 | 云同步是高风险数据能力，必须可诊断 | 错误分类完善后启动 |
| Desktop Skills | 文件、浏览器、自动化技能入口 | Memo Elf 的长期方向，但会显著扩大风险面 | Local Operator approval 闭环后启动 |

## 维护规则

1. 每完成一个执行项，更新状态、进度、已完成和验证入口。
2. 新增大功能前先检查本表，避免同时开启过多 P0/P1。
3. P0 只放“影响稳定维护或核心体验”的事项，最多同时推进 3 个。
4. 如果设计文档和本表冲突，以本表作为近期执行优先级，但仍应回头修正文档。
