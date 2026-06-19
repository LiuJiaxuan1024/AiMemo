# 项目状态矩阵

本文记录 AiMemo / Memo Elf 当前各模块的可用程度、入口、已知限制和验证入口。状态优先级高于较早的设计草案；如果设计文档与本文冲突，以本文为准，再回头修正对应设计文档。

状态含义：

```text
implemented   已实现基础闭环，作为常规能力使用。
beta          可用但仍有体验、恢复或边界场景需要加固。
experimental 具备端到端链路，但仍偏开发/联调阶段，真实数据使用需谨慎。
partial       只完成一部分能力，不能按完整产品能力理解。
planned       仅规划或设计中。
```

| 模块 | 状态 | 入口 | 已覆盖能力 | 已知限制 | 关键文档 | 关键测试 |
| --- | --- | --- | --- | --- | --- | --- |
| Memo / Notes | beta | `/app/memo`, `/api/notes` | 笔记创建、编辑、软删除、恢复、标题/摘要/标签、chunk 和 embedding | BlockNote/Markdown 双存仍可能有格式损耗；标签仍是轻量字符串模型 | [笔记智能处理](./backend/note-processing.md) | `backend/tests/test_note_service_jobs.py` |
| Knowledge Base | beta | `/app/knowledge`, `/api/knowledge/*` | 知识空间、文档导入、chunk 预览、挂载到对话、图片 OCR 文本抽取 | 缺固定检索 eval；rerank 和慢启动自适应策略仍需固化 | [Memo 知库模块设计](./architecture/knowledge-base-module.md) | `backend/tests/test_knowledge_api.py`, `backend/tests/test_knowledge_search_service.py` |
| Memory Chat Graph | beta | `/app/chat`, `/api/conversations/{id}/chat/stream` | 可恢复草稿消息、L0-L4 上下文金字塔、ReAct 工具循环、Graph 调试、checkpoint history | 浏览器刷新后自动接管 running turn 仍未完整实现；核心节点文件过大 | [Memory Chat Graph](./agent/memory-chat-graph.md) | `backend/tests/test_memory_chat_graph.py`, `backend/tests/test_chat_stream_service.py` |
| Long-term Memory | beta | `/app/workshop/memories`, `/api/memories` | 自动抽取、归并、reinforce、evidence 计数、编辑、停用、恢复、硬删除 | 无 revision/event 表；无 candidate 确认队列；无 superseded/conflicted 生命周期 | [长期记忆管理](./backend/memories.md), [Memory Consolidation](./agent/memory-consolidation.md) | `backend/tests/test_memory_api.py`, `backend/tests/test_conversation_memory_graph.py` |
| Local Operator | experimental | Memory Chat Graph 工具链 | 受控读文件、读文档、整文件写入、短时命令、后台服务任务、远程连通性/上传/命令、审计 | 高风险 write/exec 还没有 LangGraph interrupt 审批、diff preview 和 rollback | [Local Operator Agent](./agent/local-operator-agent.md) | `backend/tests/test_local_operator_graph.py`, `backend/tests/test_command_background_and_policy.py` |
| Background Jobs | beta | `/app/workshop/jobs`, `/api/jobs`, `/api/background_tasks` | job worker、启动补偿、并发 lane、后台命令持久化、输出轮询 | 任务失败恢复和 UI 归因仍可加强 | [本地任务系统](./backend/jobs.md), [后台进程任务](./backend/background-tasks.md) | `backend/tests/test_background_task_persistence.py`, `backend/tests/test_job_concurrency.py` |
| Cloud Sync | experimental | `/app/workshop/sync`, `/api/cloud-sync/*` | OSS provider、domain push/pull/sync、notes/conversations/memories/config/knowledge、冲突列表、加密备份入口 | 错误分类仍需兜底；冲突 resolution 弱；真实 OSS 集成测试默认跳过 | [云存储模块设计](./backend/cloud-storage-module-design.md), [全域云存储规划](./backend/aimemo-full-cloud-storage-plan.md) | `backend/tests/test_cloud_sync_service.py`, `backend/tests/test_full_cloud_sync_domains.py`, `backend/tests/test_cloud_sync_api.py` |
| Web Search | experimental | Memory Chat Graph Lx.web worker / tool | Tavily / DashScope provider、缓存、限额、审计、fetch 核验、隐私确认策略 | Provider 配置和引用质量仍需产品化；联网能力默认应可关闭 | [Web Search 工具设计草案](./agent/web-search-tool-design.md) | `backend/tests/test_memory_chat_graph.py`, `backend/tests/test_tavily_web_search_provider.py` |
| Conversation Export | beta | `/app/chat` 导出按钮 | 单/多对话 HTML 导出、snapshot、消息树、片段追问、Markdown、KaTeX、Mermaid、代码高亮 | 导出 HTML 与在线聊天展示仍有样式/交互分叉风险；缺浏览器视觉回归测试 | [对话导出重构](./frontend/chat-view-export-refactor.md) | `backend/tests/test_conversation_export.py`, 前端 `npm run build` |
| Voice Studio | beta | `/app/workshop/voice`, `/api/voice/*` | DashScope ASR/TTS/Voice Design、声线列表、试听、默认声线、精灵语音模式 | 依赖远程 DashScope；错误恢复和离线能力有限 | [语音工坊第一版设计](./desktop/voice-workshop-design.md) | `backend/tests/test_voice_api.py` |
| Memo Elf Desktop | beta | Tauri 桌面窗口 | 外置精灵、气泡、表情、桌面聊天入口、事件中心 | 系统级自动化能力仍未实现；模型资源版权需谨慎 | [Memo Elf 桌面化架构](./desktop/memo-elf-desktop-architecture.md) | `backend/tests/test_elf_chat_service.py` |
| Retrieval Eval | planned | 无 | 尚未建立固定 memory/retrieval eval set | 无法稳定衡量检索策略调整质量 | [项目加固路线图](./architecture/project-hardening-roadmap.md) | 待新增 |
| Local Operator Approval | planned | 未来 approval node | 设计中：diff preview、interrupt 审批、rollback | 当前仅有策略拦截和审计字段，未形成审批恢复闭环 | [Local Operator Agent](./agent/local-operator-agent.md) | 待新增 |

## 当前优先级

短期不再横向新增大模块。下一阶段优先：

```text
1. 拆分 memory_chat/nodes.py。
2. 建 memory/retrieval eval set。
3. 落地 Local Operator approval interrupt。
4. 补长期记忆 revision/evidence 表。
5. 给导出 HTML 增加浏览器回归测试。
6. 加固 Cloud Sync 错误分类和冲突体验。
```
