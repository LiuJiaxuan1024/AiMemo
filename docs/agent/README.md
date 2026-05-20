# Agent 设计

Agent 相关代码预留在 `backend/app/agent/`。

## 当前目录

```text
backend/app/agent/
  checkpoints.py  checkpoint 配置
  model.py        Agent 模型工厂、缓存和启动预热
  graphs/         具体 graph 实现
  streaming/      LangGraph stream 事件映射
```

## 默认模型

当前 Agent 默认使用阿里云百炼的 OpenAI 兼容接口：

```text
model: qwen3.5-plus
base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
api_key: DASHSCOPE_API_KEY
thinking: disabled by default
```

回答模型名暂时在 `backend/app/agent/model.py` 中固定为 `qwen3.5-plus`。后续需要支持多 Provider 时，再引入可配置的模型注册表。

轻量规划模型：

```text
model: qwen-turbo
use: retrieval planner / query rewrite
thinking: disabled by default
```

Qwen3.5 系列默认可能开启 thinking mode。Ai 记当前的 planner、摘要、长期记忆抽取和普通
RAG 回答都属于低延迟交互路径，所以默认通过 `extra_body={"enable_thinking": false}`
关闭 thinking。后续如果需要深度推理，应单独增加 reasoning model，而不是改变默认模型。
planner 当前使用 `qwen-turbo` 降低 L3 worker 延迟；回答质量主要由 `qwen3.5-plus`
回答模型和回答提示词控制。两类模型都通过启动预热和关闭 thinking 控制冷启动与推理延迟。

模型实例会在服务启动时预热并缓存：

```text
warmup_agent_models()
  -> 创建并缓存 qwen-turbo planner model
  -> 创建并缓存 qwen3.5-plus answer model
```

warmup 只创建本地 `ChatOpenAI` / OpenAI client，不发送真实 LLM 请求。这样可以把
首次 client 构造的冷启动成本挪到服务启动阶段，同时不因为网络/API 临时异常阻断启动。
业务代码仍通过 `get_planner_chat_model()` 和 `get_agent_chat_model()` 获取模型；同一进程内会复用缓存实例。

## 设计原则

LangGraph 不直接替代业务数据库。

- LangGraph checkpoint 用于保存会话状态、执行过程和可恢复工作流。
- 用户长期笔记、结构化字段和后续向量索引由业务存储负责。
- jobs 表负责应用级任务排队、重试、锁和恢复。
- Agent 通过工具调用业务服务，例如 `search_notes`、`get_note`、`create_note`。

## 当前 Graph

- [Note Metadata Graph](./note-metadata-graph.md)
- [Note Embedding Graph](./note-embedding-graph.md)
- [Memory Chat Graph](./memory-chat-graph.md)
- [Conversation Summary Graph](./conversation-summary-graph.md)
- [Conversation Memory Graph](./conversation-memory-graph.md)
- [Context Pyramid](./context-pyramid.md)
- [Local Operator Agent](./local-operator-agent.md)
- [Memory Chat Graph 设计草案](./memory-chat-graph-design.md)

## Memory Chat Graph 当前结构

```text
load_turn_state
  -> dispatch_context_workers
  -> merge_prompt_context
  -> route_answer_mode
  -> generate_answer / generate_elf_bubble_answer
  -> persist_messages
  -> enqueue_conversation_memory_job
```

上下文金字塔通过 worker 并行构建：

```text
L0 当前输入
L1 近期消息
L2 对话滚动摘要
L3 检索到的笔记记忆
L4 长期核心记忆
Local Operator 本地 read-only 上下文
```

其中 L3 context worker 内部负责 plan / rewrite / retrieve / grade。
Local Operator worker 负责 read-only 本地工具上下文：明确本地读取请求走规则快路径，
模糊本地操作候选交给 qwen-turbo planner 判断，普通聊天会快速跳过。
