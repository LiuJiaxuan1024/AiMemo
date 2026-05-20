# 流程图

本文档用 Mermaid 描述 Ai 记当前核心流程。阅读顺序建议：

1. 先看系统总览。
2. 再看创建笔记流程。
3. 最后看具体 job 和 graph 的状态流转。

## 系统总览

```mermaid
flowchart LR
    User[用户] --> Frontend[React Web 前端]
    Frontend --> API[FastAPI API 层]
    API --> NoteService[NoteService]
    NoteService --> Notes[(SQLite notes)]
    NoteService --> Jobs[(SQLite jobs)]

    Reconciler[JobReconciler] -->|扫描业务状态| Notes
    Reconciler -->|补建缺失 job| Jobs
    Worker[JobWorker] --> Jobs
    Worker --> Handler[Job Handler]
    Handler --> MetadataGraph[note_metadata_graph]
    Handler --> EmbeddingGraph[note_embedding_graph]
    MetadataGraph --> Checkpoints[(SQLite LangGraph checkpoints)]
    EmbeddingGraph --> Checkpoints
    MetadataGraph --> LLM[DashScope qwen3.5-plus]
    EmbeddingGraph --> Embedding[DashScope text-embedding-v4]
    MetadataGraph --> Notes
    EmbeddingGraph --> Chunks[(SQLite notechunk)]
    EmbeddingGraph --> Vectors[(sqlite-vec vec_note_chunks)]

    Frontend -->|轮询 processing_status| API
    Workshop[Workshop Jobs] -->|GET /api/jobs| API
    Workshop -->|GET /api/jobs/:id/graph| API
```

## 创建笔记与 AI 整理

```mermaid
sequenceDiagram
    participant U as 用户
    participant FE as 前端
    participant API as FastAPI
    participant NS as NoteService
    participant DB as SQLite notes/jobs
    participant W as JobWorker
    participant G as note_metadata_graph
    participant CP as LangGraph checkpoint
    participant LLM as qwen3.5-plus

    U->>FE: 输入笔记并保存
    FE->>API: POST /api/notes
    API->>NS: create_note(payload)
    NS->>DB: 写入 note(processing_status=pending)
    NS->>DB: 写入 job(type=note_metadata)
    NS->>DB: 写入 job(type=note_embedding)
    NS-->>API: 返回 note
    API-->>FE: 201 Created
    FE-->>U: 立即显示保存成功 / AI 整理中

    W->>DB: claim pending job
    W->>G: run graph(thread_id=job:{id})
    G->>CP: 读取或创建 checkpoint
    G->>DB: load_note，标记 processing
    G->>CP: checkpoint after load_note
    G->>LLM: generate_metadata
    LLM-->>G: title / summary / tags
    G->>CP: checkpoint after generate_metadata
    G->>DB: write_metadata，标记 completed
    G->>CP: checkpoint after write_metadata
    W->>DB: job.status=completed
    FE->>API: 轮询 note
    API-->>FE: 返回 completed + AI 元数据
```

## Job 与 Checkpoint 分层恢复

```mermaid
flowchart TD
    A[服务启动] --> R[reconcile 业务状态与 jobs 表]
    R --> B[扫描 jobs 表]
    B --> C{是否存在 pending job}
    C -->|是| D[worker 领取 job]
    C -->|否| E{是否存在超时 running job}
    E -->|是| F[恢复为 pending]
    F --> D
    E -->|否| Z[等待下一轮轮询]

    D --> G[根据 job.graph_name 找 graph]
    G --> H[使用 job.thread_id 读取 checkpoint]
    H --> I{checkpoint 是否有 next 节点}
    I -->|有| J[从 checkpoint 继续执行]
    I -->|没有| K[从 graph 初始输入开始]
    J --> L{graph 是否成功}
    K --> L
    L -->|成功| M[job.status=completed]
    L -->|失败且可重试| N[job.status=pending run_after=backoff]
    L -->|失败且超过次数| O[job.status=failed]
```

## Job Reconciler

```mermaid
flowchart TD
    A[启动或周期 tick] --> B[扫描 note 状态]
    B --> C{processing_status 是否 pending/processing}
    C -->|是| D{是否已有活跃 note_metadata job}
    D -->|否| E[补建 note_metadata job]
    D -->|是| F[跳过]
    C -->|否| F

    B --> G{embedding_status 是否 pending/processing}
    G -->|是| H{是否已有活跃 note_embedding job}
    H -->|否| I[补建 note_embedding job]
    H -->|是| J[跳过]
    G -->|否| J
```

## Note Metadata Graph

```mermaid
flowchart LR
    Start([START]) --> Load[load_note]
    Load --> Gen[generate_metadata]
    Gen --> Write[write_metadata]
    Write --> End([END])

    Load -. checkpoint .-> CP1[(checkpoint)]
    Gen -. checkpoint .-> CP2[(checkpoint)]
    Write -. checkpoint .-> CP3[(checkpoint)]

    Load -->|读取 note 并标记 processing| Notes[(notes)]
    Gen -->|调用 qwen3.5-plus| LLM[LLM]
    Write -->|覆盖写入 title summary tags| Notes
```

## Note Embedding Graph

```mermaid
flowchart LR
    Start([START]) --> Load[load_note]
    Load --> Split[split_note]
    Split --> WriteChunks[write_chunks]
    WriteChunks --> Embed[generate_embeddings]
    Embed --> WriteVec[write_vector_index]
    WriteVec --> Done[mark_embedding_completed]
    Done --> End([END])

    Load -->|读取 note 并标记 embedding processing| Notes[(notes)]
    Split -->|段落优先 + token fallback| Chunking[chunking]
    WriteChunks -->|写入 chunk 元数据| Chunks[(notechunk)]
    Embed -->|调用 text-embedding-v4| Embedding[Embedding API]
    WriteVec -->|rowid = notechunk.id| Vectors[(sqlite-vec)]
    Done -->|标记 embedding completed| Notes
```

## Note 处理状态

```mermaid
stateDiagram-v2
    [*] --> pending: 创建 note
    pending --> processing: load_note
    processing --> completed: write_metadata 成功
    processing --> failed: graph 执行失败
    failed --> pending: 未来手动重试
    completed --> pending: 未来重新处理
```

## Job 状态

```mermaid
stateDiagram-v2
    [*] --> pending: enqueue_job
    pending --> running: claim_next_job
    running --> completed: handler 成功
    running --> pending: 失败但未超过 max_attempts
    running --> failed: 失败且超过 max_attempts
    running --> pending: running 超时恢复
    pending --> canceled: 未来取消
    running --> canceled: 未来取消
```
