# 对话持久化

对话持久化是 `memory_chat_graph` 的业务数据底座。LangGraph checkpoint 保存执行现场，但用户可见的会话、消息、分支关系必须落在业务表。

## 表结构

### conversation

```text
conversation
  id
  title
  status
  summary
  summary_message_id
  langgraph_thread_id
  created_at
  updated_at
```

字段说明：

- `title`: 用户可见标题。MVP 默认“新对话”。
- `status`: 当前默认 `active`。
- `summary`: 对话滚动摘要，后续由 summary graph 写入。
- `summary_message_id`: 表示摘要覆盖到哪条消息。
- `langgraph_thread_id`: 固定为 `conversation:{id}`，用于绑定聊天 graph checkpoint。

### chatmessage

```text
chatmessage
  id
  conversation_id
  role
  content
  parent_id
  checkpoint_id
  status
  token_count
  created_at
  updated_at
```

字段说明：

- `conversation_id`: 所属对话。
- `role`: `user / assistant / system`。
- `parent_id`: 业务消息树父节点。MVP 中用于线性串联消息，后续可支撑对话分支。
- `checkpoint_id`: 对应 LangGraph checkpoint。MVP 为空，接入 graph 后写入。
- `token_count`: 本地 tokenizer 估算的 token 数，后续用于上下文预算。

### chatturn

```text
chatturn
  id
  conversation_id
  user_message_id
  assistant_message_id
  thread_id
  checkpoint_id
  status
  node_statuses
  context_layers
  retrieved_chunks
  error
  created_at
  updated_at
```

字段说明：

- `user_message_id`: 本轮用户消息 ID。
- `assistant_message_id`: 本轮 AI 回复消息 ID，用于前端从消息反查 graph。
- `thread_id`: 本轮使用的 LangGraph thread，当前为 `conversation:{id}`。
- `checkpoint_id`: 本轮最终 checkpoint。
- `node_statuses`: JSON 字符串，记录节点 `pending / running / succeeded / failed / skipped`。
- `context_layers`: JSON 字符串，保存 L0-L4 金字塔上下文快照。
- `retrieved_chunks`: JSON 字符串，保存 L3 检索证据。
- `error`: graph 失败时的错误信息。

## 文件职责

```text
backend/app/models/conversation.py
  Conversation 数据模型。

backend/app/models/chat_message.py
  ChatMessage 数据模型。

backend/app/models/chat_turn.py
  ChatTurn 数据模型，保存单轮 graph 执行调试信息。

backend/app/schemas/conversation.py
  Conversations API 入参和出参。

backend/app/services/conversation_service.py
  创建对话、追加消息、读取对话和消息列表。
  delete_conversation 负责级联释放：BackgroundTask（含 OS 进程）、
  LongTermMemory、AgentOperation、Job、ChatTurn、ChatMessage、
  LangGraph checkpoint 和 Conversation 本体。

backend/app/services/chat_turn_service.py
  创建、更新、完成 ChatTurn，并生成消息 graph 调试视图。

backend/app/api/conversations.py
  暴露 /api/conversations 路由。
```

## 与 LangGraph 的关系

```text
Conversation.langgraph_thread_id
  conversation:{conversation_id}

ChatMessage.checkpoint_id
  某条消息生成或保存后对应的 checkpoint。
```

第一版只记录业务数据。后续 `memory_chat_graph` 执行时会：

```text
读取 conversation.langgraph_thread_id
  -> 使用同一个 thread_id 调用 graph
  -> graph 执行完成后保存 user/assistant 消息
  -> 把 checkpoint_id 写回 chatmessage
```

当前 `memory_chat_graph` 已通过 `POST /api/conversations/{id}/chat` 接入。直接追加消息接口仍保留，
主要用于调试、手工构造历史或后续编辑功能。

## 线性消息与未来状态树

MVP 中，如果追加消息时不传 `parent_id`，后端会自动把它接在当前会话最后一条消息后：

```text
A -> B -> C
```

未来做编辑和状态树时，`parent_id` 可以表达分支：

```text
A -> B  -> C
A -> B' -> C'
```

届时可能需要补充：

```text
edited_from_id
branch_id
```

当前先保留 `parent_id` 和 `checkpoint_id` 两个最关键的钩子。
