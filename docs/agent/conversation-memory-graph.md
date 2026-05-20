# Conversation Memory Graph

`conversation_memory_graph` 负责从一轮对话中抽取 L4 核心长期记忆。它通过 job 后台执行，不阻塞主聊天回答。

用户可通过 Memories API 管理这些长期记忆，详见：

```text
docs/backend/memories.md
docs/api/memories.md
docs/agent/memory-consolidation.md
```

## 流程

```mermaid
flowchart TD
    Start([START]) --> Load[load_memory_source]
    Load --> Extract[extract_memories]
    Extract --> Consolidate[consolidate_memories]
    Consolidate --> Write[write_memories]
    Write --> End([END])

    Load -. checkpoint .-> CP1[(checkpoint)]
    Extract -. checkpoint .-> CP2[(checkpoint)]
    Consolidate -. checkpoint .-> CP3[(checkpoint)]
    Write -. checkpoint .-> CP4[(checkpoint)]
```

## 节点职责

```text
load_memory_source
  根据 payload 读取 user_message_id 和 assistant_message_id。
  第一版只处理一问一答，边界清晰，方便排查。

extract_memories
  调用 qwen3.5-plus 判断是否有值得长期记住的信息。
  调用时绑定 response_format={"type":"json_object"}，优先让模型进入 JSON mode。
  输出 extraction_result，并写入 LangGraph checkpoint。
  模型输出会先经过 parse_memory_extraction_response 归一化。
  如果模型偶发返回非严格 JSON，则降级为 {"memories": []}，不让后台 job 失败。

consolidate_memories
  将抽取出的候选记忆和已有 active L4 记忆做归并判断。
  memory_key 相同的记忆会优先召回，即使 category 不同也进入同一轮判断。
  称呼、昵称、身份偏好等容易跨 category 的记忆会做跨类候选召回。
  content_hash 完全重复时直接 skip。
  明显高相似记忆由本地规则直接 skip。
  其他候选交给 planner LLM judge，判断 skip/create/update。
  输出 consolidation_result，并写入 LangGraph checkpoint。

write_memories
  执行 consolidation_result.decisions。
  skip 不写入。
  create 创建新 LongTermMemory。
  update 更新已有 active LongTermMemory。
  create/update 前再次做 content_hash 或 id 检查，确保恢复时幂等。
```

## Job 约定

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

## 写入规则

第一版采用保守阈值：

```text
should_write = true
importance >= 0.7
confidence >= 0.6
content 非空
content_hash 不重复
```

`memory_key` 是长期记忆的稳定槽位键，用来表达“这条记忆属于哪个可更新槽位”。
例如：

```text
user.preferred_name
```

如果用户先说“叫我小刘”，后面又说“以后叫我家炫，不要叫我小刘”，
两条候选都应归入 `user.preferred_name`。归并节点会优先更新旧记忆，而不是创建两条互相冲突的 L4 记忆。

支持的 `category`：

```text
preference
identity
goal
instruction
event
fact
```

所有写入的记忆第一版都使用：

```text
level = 4
status = active
source_type = chat_message
source_id = assistant_message_id
```

## 恢复语义

```text
如果 graph 在 extract_memories 后中断：
  extraction_result 已进入 checkpoint。
  恢复时继续执行 consolidate_memories。
  不重复调用 LLM。

如果 graph 在 consolidate_memories 后中断：
  consolidation_result 已进入 checkpoint。
  恢复时直接执行 write_memories。
  不重复调用归并 judge。

如果 write_memories 被重复执行：
  create 会再次检查 content_hash。
  update 会按 existing_memory_id 更新已有 active 记忆。
  update 会保留或写入 memory_key，避免恢复后丢失槽位信息。
```

## JSON 解析容错

长期记忆抽取属于后台增强任务，不阻塞用户当前回答。这里的失败策略偏保守：

```text
模型返回合法 JSON
  -> 正常进入 write_memories。

模型支持 JSON mode
  -> extract_memories 通过 response_format={"type":"json_object"} 请求严格 JSON。

模型返回 markdown / 夹杂解释文本
  -> parse_json_object 会尝试截取 JSON object。

模型返回缺逗号、字段类型异常等非严格 JSON
  -> 记录 warning 日志。
  -> extraction_result 降级为 {"memories": []}。
  -> job 继续完成，不写入长期记忆。
```

这样做的取舍是：宁可少写一条长期记忆，也不要因为模型格式波动污染 job 队列。

## 当前限制

第一版暂不实现：

```text
长期记忆向量化
用户确认机制
记忆编辑 UI
多 worker 分类抽取
```

后续可以把 `extract_memories` 拆成 preference / identity / goal / instruction 等多个 worker 并行抽取，再由 synthesizer 合并结果。

语义级去重和更新策略见：

```text
docs/agent/memory-consolidation.md
```
