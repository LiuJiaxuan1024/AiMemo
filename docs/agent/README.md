# Agent 设计

Agent 相关代码预留在 `backend/app/agent/`。

## 当前目录

```text
backend/app/agent/
  graph.py        LangGraph 图入口
  model.py        Agent 默认模型定义
  graphs/         具体 graph 实现
  state.py        Agent 状态定义
  nodes.py        图节点实现
  tools.py        Agent 可调用工具
  checkpoints.py  checkpoint 配置
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
- [Memory Chat Graph 设计草案](./memory-chat-graph-design.md)

## 对话 Graph 方向

```text
classify_intent
  -> retrieve_memory
  -> generate_answer
```

对话系统后续会按 RAG + 记忆分层设计展开：

```text
load_thread_context
  -> dispatch_context_workers
  -> merge_prompt_context
  -> generate_answer
  -> checkpoint

其中 L3 context worker 内部负责 plan / rewrite / retrieve / grade。
```
