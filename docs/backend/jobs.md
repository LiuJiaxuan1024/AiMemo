# 本地任务系统

Ai 记使用 SQLite `jobs` 表作为本地持久化任务队列。

## 职责边界

```text
Job
  负责任务生命周期：排队、领取、重试、失败、完成、恢复。

LangGraph checkpoint
  负责 graph 内部执行进度：节点状态、下一步节点、中断后恢复。
```

二者协作方式：

```text
reconciler 发现业务状态需要任务但缺少活跃 job
  -> 补建 job
jobs 表存在可执行任务
  -> worker 领取任务
  -> 根据 job.graph_name 执行对应 LangGraph
  -> LangGraph 用 job.thread_id 恢复 checkpoint
  -> graph 成功后 job 标记 completed
```

## 状态流转图

```mermaid
stateDiagram-v2
    [*] --> pending: enqueue_job
    pending --> running: claim_next_job
    running --> completed: handler 成功
    running --> pending: 失败但可重试
    running --> failed: 超过 max_attempts
    running --> pending: locked_at 超时恢复
    pending --> canceled: 未来取消
    running --> canceled: 未来取消
```

## jobs 表

```text
job
  id
  type
  graph_name
  thread_id
  dedupe_key
  status
  payload
  priority
  attempts
  max_attempts
  error
  locked_at
  locked_by
  run_after
  created_at
  updated_at
  completed_at
```

## 状态机

```text
pending -> running -> completed
pending -> running -> pending
pending -> running -> failed
running -> pending
```

- `pending`: 等待 worker 执行。
- `running`: 已被某个 worker 领取。
- `completed`: 执行完成。
- `failed`: 超过最大重试次数或不可恢复。
- `canceled`: 保留状态，后续用于取消任务。

## 恢复策略

服务启动后会先执行一次 job reconcile：

```text
note.processing_status in pending/processing
  且不存在活跃 note_metadata job
  -> 补建 note_metadata job

note.embedding_status in pending/processing
  且不存在活跃 note_embedding job
  -> 补建 note_embedding job

conversation 未摘要消息 token 超过阈值
  且不存在活跃 conversation_summary job
  -> 补建 conversation_summary job

assistant 消息缺少任何 conversation_memory job
  -> 补建 conversation_memory job
```

随后 `JobReconciler` 会按固定间隔继续扫描，默认间隔：

```text
JOB_RECONCILER_INTERVAL_SECONDS=30
```

这解决两类问题：

- 历史数据在新功能上线前没有对应 job。
- 未来某些业务状态和 jobs 表因为异常中断变得不一致。

worker 在领取任务前会扫描过期 `running` 任务：

```text
running + locked_at < now - JOB_RUNNING_TIMEOUT_SECONDS
  -> pending
```

重新领取任务后，worker 使用同一个 `thread_id` 恢复 LangGraph checkpoint。

## 当前任务类型

```text
type: note_metadata
graph_name: note_metadata_graph
payload: {"note_id": 1}
thread_id: job:{job_id}
dedupe_key: note_metadata:note:{note_id}
```

该任务负责给笔记生成标题、摘要和标签。

```text
type: note_embedding
graph_name: note_embedding_graph
payload: {"note_id": 1}
thread_id: job:{job_id}
dedupe_key: note_embedding:note:{note_id}
```

该任务负责把笔记拆分为 chunk，调用 embedding 模型，并写入 `sqlite-vec` 向量索引。

```text
type: conversation_summary
graph_name: conversation_summary_graph
payload: {"conversation_id": 1}
thread_id: job:{job_id}
dedupe_key: conversation_summary:conversation:{conversation_id}
```

该任务负责把 `conversation.summary_message_id` 之后的消息滚动合并到 `conversation.summary`。

```text
type: conversation_memory
graph_name: conversation_memory_graph
payload:
  {
    "conversation_id": 1,
    "user_message_id": 10,
    "assistant_message_id": 11
  }
thread_id: job:{job_id}
dedupe_key: conversation_memory:assistant_message:{assistant_message_id}
```

该任务负责从一轮对话中抽取 L4 核心长期记忆，并写入 `longtermmemory`。

## 与 LangGraph 的协作

```mermaid
flowchart LR
    BusinessState[(notes / conversations status)] --> Reconciler[JobReconciler]
    Reconciler --> Jobs[(jobs)]
    Jobs[(jobs)] --> Worker[JobWorker]
    Worker --> Handler[Job Handler]
    Handler --> Graph[LangGraph graph]
    Graph --> Checkpoint[(checkpoint)]
    Graph --> BusinessDB[(notes / conversations / longtermmemory / vector store)]
    Worker --> Jobs
```
