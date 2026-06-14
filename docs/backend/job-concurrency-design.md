# 后台 Job 并发调度设计

本文描述 AiMemo 后台 `jobs` 队列从“全局串行 worker”升级为“受控并发调度”的设计方案。

实现状态：当前版本已经引入 `lane`、`lock_key`、`concurrency_policy` 和 `resource_weight`，`JobWorker` 会用一个调度线程管理多个 runner 线程。全局并发由 `jobs.worker_concurrency` 控制，默认值为 3。

原先的全局串行模型简单可靠，但一个耗时任务会阻塞所有后续任务，例如大文档 OCR / embedding 会让笔记 metadata、对话摘要、图片定向重试都排队等待。

目标不是让所有任务无脑并行，而是让可以并行的任务并行，同时保护会互相覆盖、抢占资源或写同一业务对象的任务仍然串行。

## 设计目标

- 同一时间允许多个后台任务执行，避免轻任务被大任务长期阻塞。
- 同一个业务对象的冲突任务必须串行，例如同一文档的导入和图片重试。
- 不同业务对象的任务可以按资源预算并行，例如不同笔记的 metadata 生成。
- 调度规则要能在工坊中解释，用户能看懂任务为什么等待。
- 先保持本地 SQLite 架构，不引入 Redis / Celery / 外部队列。

## 非目标

- 第一版不做跨进程、跨设备的分布式任务调度。
- 第一版不做复杂的 DAG 依赖调度，只处理队列领取和资源互斥。
- 第一版不保证严格公平调度，只在 priority / run_after / created_at 基础上跳过当前不可运行任务。
- 第一版不自动并发执行同一个 LangGraph 内部节点；Graph 内部并行仍由 LangGraph 自身控制。

## 核心概念

### lane

`lane` 表示任务所属的资源通道。它决定同类任务最多允许多少个同时运行。

示例：

```text
note_light
  笔记标题、摘要、标签等轻任务。

embedding
  笔记或知识库向量化，通常受模型调用、sqlite-vec 写入影响。

knowledge_ingest
  文档解析、chunk、图片抽取、embedding、索引写入。

knowledge_retry
  知识库图片失败定向重试。

conversation_maintenance
  对话摘要、长期记忆抽取、标题生成等后台维护任务。

cloud_sync
  云端上传、拉取、冲突合并。
```

### lane 最大并发

每个 lane 有独立并发上限：

```text
note_light: 2
embedding: 1
knowledge_ingest: 1
knowledge_retry: 2
conversation_maintenance: 1
cloud_sync: 1
```

这些数字不是最终值，只是第一版建议。后续可以放入 `config.json5`：

```json5
{
  "jobs": {
    "worker_concurrency": 4,
    "lanes": {
      "note_light": { "max_concurrency": 2 },
      "embedding": { "max_concurrency": 1 },
      "knowledge_ingest": { "max_concurrency": 1 },
      "knowledge_retry": { "max_concurrency": 2 },
      "conversation_maintenance": { "max_concurrency": 1 },
      "cloud_sync": { "max_concurrency": 1 }
    }
  }
}
```

### lock_key

`lock_key` 表示任务实际会读写的互斥资源。只要存在运行中的任务持有相同 `lock_key`，新任务就不能被领取。

示例：

```text
note:123
document:6
conversation:20
embedding_index:notes
embedding_index:knowledge
cloud_sync:user:local
```

`lane` 解决“这类资源最多跑几个”，`lock_key` 解决“这个具体对象不能同时被改”。两者不能合并成一个字段，因为冲突常常不是同类任务才发生。

例如：

```text
knowledge_ingest(document:6)
knowledge_image_retry(document:6)
```

二者类型不同、lane 也可能不同，但都会写同一份文档的 image assets、chunks 和统计字段，所以必须通过 `lock_key=document:6` 串行。

### concurrency_policy

`concurrency_policy` 描述任务默认是否可以与同 lane 任务共享并发。

建议第一版只保留两个值：

```text
shared
  受 lane max_concurrency 控制，可以和同 lane 的其他不同 lock_key 任务并行。

exclusive
  同 lane 同一时间只能运行一个，即使 lock_key 不同也要排队。
```

如果某个 lane 的 `max_concurrency=1`，它天然等价于 exclusive。保留 `concurrency_policy` 是为了表达任务自己的意图，方便工坊展示和未来扩展。

## 表结构建议

在 `jobs` 表增加字段：

```text
lane: str
lock_key: str | null
concurrency_policy: str
resource_weight: int
```

字段含义：

```text
lane
  调度通道。默认由 job type 推导。

lock_key
  互斥资源。为空表示没有具体对象锁，只受 lane 限制。

concurrency_policy
  shared 或 exclusive。

resource_weight
  预留字段。第一版可默认 1，未来用于 OCR / embedding 等重任务消耗更多并发预算。
```

迁移时历史 job 可以按 `type` 和 `payload` 补默认值。无法解析 payload 的历史任务使用保守策略：

```text
lane = "default"
lock_key = null
concurrency_policy = "exclusive"
resource_weight = 1
```

## 默认映射建议

```text
note_metadata
  lane: note_light
  lock_key: note:{note_id}
  concurrency_policy: shared

note_embedding
  lane: embedding
  lock_key: note:{note_id}
  concurrency_policy: shared

knowledge_ingest
  lane: knowledge_ingest
  lock_key: document:{document_id}
  concurrency_policy: shared

knowledge_image_retry
  lane: knowledge_retry
  lock_key: document:{document_id}
  concurrency_policy: shared

conversation_summary
  lane: conversation_maintenance
  lock_key: conversation:{conversation_id}
  concurrency_policy: shared

conversation_memory
  lane: conversation_maintenance
  lock_key: conversation:{conversation_id}
  concurrency_policy: shared

conversation_title
  lane: conversation_maintenance
  lock_key: conversation:{conversation_id}
  concurrency_policy: shared

cloud_sync_upload / cloud_sync_pull
  lane: cloud_sync
  lock_key: cloud_sync:user:{user_id}
  concurrency_policy: exclusive
```

注意：`embedding` lane 第一版建议最大并发为 1。即便不同 note 的 `note_embedding` 可以理论并行，也要先避免 sqlite-vec 写入和模型额度压力变复杂。后续如果向量写入隔离更好，再提高该 lane 并发。

## 领取规则

`claim_next_job` 需要从“取第一个 pending job”改成“取第一个可运行 pending job”。

候选任务排序仍然使用：

```text
priority desc
run_after asc
created_at asc
```

对每个候选任务检查：

```text
1. job.status == pending
2. job.run_after <= now
3. 全局 running 数 < JOB_WORKER_CONCURRENCY
4. 当前 lane running 数 < lane.max_concurrency
5. 不存在 running job 与候选任务 lock_key 相同
6. 如果候选任务 concurrency_policy == exclusive：
     不存在 running job 与候选任务 lane 相同
7. 如果已有 running job concurrency_policy == exclusive 且 lane 相同：
     候选任务不能领取
```

伪代码：

```text
for job in pending_jobs_ordered:
  if global_running_count >= max_workers:
    return None
  if running_count(job.lane) >= lane_limit(job.lane):
    continue
  if job.lock_key and exists_running(lock_key=job.lock_key):
    continue
  if job.concurrency_policy == "exclusive" and exists_running(lane=job.lane):
    continue
  if exists_running(lane=job.lane, concurrency_policy="exclusive"):
    continue
  return claim(job)
return None
```

## Worker 模型

第一版可以继续使用一个 `JobWorker` 管理线程，但内部维护固定数量的执行线程：

```text
JobWorker
  scheduler loop
    recover stale running jobs
    如果 active thread 数 < JOB_WORKER_CONCURRENCY:
      claim_next_runnable_job
      spawn job runner thread

  runner thread
    handler(job)
    complete_job / fail_job
```

这样可以复用现有 handler、重试、事件通知逻辑。后续再考虑线程池或 asyncio。

需要注意：

- 每个 runner 必须使用独立 SQLModel session。
- 不要把 session 对象跨线程传给 handler。
- `claim_next_runnable_job` 必须在一次数据库事务里完成“检查 + 标记 running”。
- SQLite 写锁有限，领取逻辑要短，长耗时工作必须在事务外执行。

## SQLite 并发注意事项

SQLite 适合本地轻并发，但不适合大量写入同时发生。第一版要保守：

- `JOB_WORKER_CONCURRENCY` 默认 2 或 3，不建议一开始开太大。
- `embedding` 和 `knowledge_ingest` 默认并发 1。
- `knowledge_retry` 可以为 2，但同一 document 仍通过 `lock_key` 串行。
- handler 中长时间模型调用不要持有数据库事务。
- 如果出现 `database is locked`，应优先缩小 lane 并发，或在写入点加短重试。

## 工坊展示

后台工坊应展示调度字段，帮助用户理解等待原因：

```text
lane
lock_key
concurrency_policy
worker_id
等待原因
```

等待原因可以由前端或后端根据当前 running jobs 推导：

```text
等待同一文档任务完成：document:6
等待 embedding 通道空闲
等待 conversation_maintenance 独占任务完成
等待 run_after 到达
```

Graph 视图可以继续展示 job 内部进度；队列并发状态属于 job 层，不建议塞进 LangGraph checkpoint。

## 与 dedupe_key 的关系

`dedupe_key` 负责防止重复入队，`lock_key` 负责防止运行时冲突。

二者职责不同：

```text
dedupe_key
  同一个业务动作是否已经有活跃任务。

lock_key
  当前任务运行时会占用哪个业务资源。
```

例如同一文档可能同时存在：

```text
knowledge_ingest:document:6
knowledge_image_retry:document:6
```

它们 dedupe_key 不同，因为是不同动作；但 lock_key 相同，因此不能并行运行。

## 与 LangGraph checkpoint 的关系

并发调度不改变 checkpoint 设计：

```text
job.thread_id = job:{job_id}
```

每个 job 仍然有独立 checkpoint。并发只影响 job 何时开始执行，不改变 graph 内部恢复方式。

需要避免的是：两个 job 同时写同一业务对象，导致各自 checkpoint 都认为自己成功，但业务状态互相覆盖。这个问题由 `lock_key` 解决。

## 实施步骤

### Phase 1：文档和字段

- 增加 `jobs.lane`、`jobs.lock_key`、`jobs.concurrency_policy`、`jobs.resource_weight`。
- 在 `enqueue_job` 增加可选参数，并提供按 `job_type + payload` 推导默认调度字段的 helper。
- 为历史数据库 migration 补默认值。
- API / 工坊展示新增字段。

### Phase 2：可运行任务领取

- 将 `claim_next_job` 改为 `claim_next_runnable_job`。
- 查询 pending 候选任务并跳过当前被 lane / lock_key 阻塞的任务。
- 增加测试覆盖：
  - 不同 note metadata 可以并行领取。
  - 同一 note metadata / embedding 不能同时领取。
  - 同一 document ingest / retry 不能同时领取。
  - lane 达到 max_concurrency 时跳过该 lane，领取其他 lane。

### Phase 3：Worker 并发执行

- `JobWorker` 内部增加 runner 线程池或受控线程集合。
- 配置 `JOB_WORKER_CONCURRENCY`。
- 保持 handler 外围的 complete/fail/retry 逻辑一致。
- 确保 shutdown 时等待 runner 短时间收尾。

### Phase 4：工坊可解释性

- 工坊列表展示 lane / lock_key。
- pending 任务展示等待原因。
- running 任务展示 worker_id 和运行时长。
- Graph 继续展示单任务内部流程，不承担队列调度解释。

## 测试计划

后端：

- `test_job_queue.py`
  - 调度字段默认值。
  - lane 并发上限。
  - lock_key 互斥。
  - exclusive lane。
  - priority / run_after / created_at 排序仍然生效。

- `test_job_worker.py`
  - 多 runner 能并发完成不同 lock_key 任务。
  - handler 失败仍按 max_attempts 重试。
  - worker stop 不会丢失 running job，超时后可恢复。

- 业务回归：
  - note processing。
  - knowledge ingest。
  - image retry。
  - conversation summary / memory。

前端：

- 工坊能展示 lane / lock_key。
- pending 队列能显示等待原因。
- active job 数和失败数统计不受并发影响。

## 风险与缓解

```text
风险：SQLite database is locked
缓解：默认并发小；重写点短事务；embedding / ingest lane 默认 1。

风险：同一业务对象被两个任务同时写
缓解：所有会写业务对象的任务必须有 lock_key；缺失 lock_key 时使用保守 exclusive。

风险：调度规则难以理解
缓解：工坊展示 lane、lock_key、等待原因。

风险：任务长期被高优先级任务饿死
缓解：第一版接受；后续可引入 aging priority。

风险：旧任务缺少新字段
缓解：migration 按 type/payload 回填；无法判断时走 default exclusive。
```

## 第一版建议

最小可落地版本：

```text
JOB_WORKER_CONCURRENCY = 3

note_light max_concurrency = 2
embedding max_concurrency = 1
knowledge_ingest max_concurrency = 1
knowledge_retry max_concurrency = 2
conversation_maintenance max_concurrency = 1
cloud_sync max_concurrency = 1
default max_concurrency = 1
```

这个配置的效果：

- 轻量笔记任务不会被单个大文档完全阻塞。
- 同一文档的导入和图片重试不会互相覆盖。
- embedding / ingest 这类重写入任务仍然保守串行。
- 后续可以根据实际 `database is locked` 和模型限流情况逐步调大。
