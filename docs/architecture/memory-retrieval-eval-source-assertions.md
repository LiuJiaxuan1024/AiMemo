# Memory / Retrieval Eval Source-Based Assertions 设计

本文定义 Step 3 第二轮扩展的评测断言方案。目标是在现有
`backend/tests/test_memory_retrieval_eval.py` 和
`backend/tests/evals/memory_retrieval_cases.jsonl` 基础上，把第一版的文本包含断言升级为
来源命中断言。

## 背景

当前第一版 eval 已经建立了最小红绿灯：

```text
pytest backend/tests/test_memory_retrieval_eval.py
```

它会 seed notes / long-term memories / mounted knowledge / recent messages，运行真实
`memory_chat_graph`，然后检查 `prompt_context` 是否包含或排除指定文本。

这种方式简单有效，但有几个问题：

```text
1. 文本断言容易受格式、截断、prompt 文案调整影响。
2. 它只能说明某段文字出现了，不能明确说明命中了哪个来源。
3. 当两个来源包含相似文本时，无法区分正确命中和误命中。
4. 无法稳定表达“这个知识空间不能被搜到”“这个 note chunk 必须命中”这类边界。
```

第二轮不改变业务逻辑，先增强 eval schema 和 runner 的判断能力。

## 目标

Source-based assertions 要回答的是：

```text
用户问了一个问题后，Memory Chat Graph 是否把正确来源放进了本轮上下文，
并且没有把禁止来源放进去。
```

第一阶段只评估上下文构建，不评估最终自然语言回答质量。

## 当前可观测来源

基于现有 graph state，runner 可以稳定读取这些来源：

| 来源类型 | 当前字段 | 可用 source key |
| --- | --- | --- |
| 个人笔记 L3 | `result["retrieved_chunks"]` | `note_id`, `note_title`, `chunk_id`, `content_hash` |
| 长期记忆 L4 | `result["context_l4_layer"]["content"]` / `prompt_context` | `memory_key`, `category`, `content` |
| 挂载知库 L3.5 | `result["knowledge_retrieved_chunks"]` | `space_id`, `space_name`, `document_id`, `document_title`, `chunk_id` |
| 近期对话 L1 / L0.5 | `result["context_l1_layer"]`, `result["context_l0_adjacent_layer"]` | `role`, `content`，后续可补 message label |

其中个人笔记和知库已经有结构化 chunk payload；长期记忆目前没有独立 payload 列表，只能从 L4
context 文本里识别 `memory_key` 或内容。第二轮可以先支持文本识别，后续再考虑让 L4 worker 输出
`core_memory_sources` 之类的结构化调试字段。

## 建议 Schema

在 JSONL case 的 `expected` 中新增：

```json
{
  "must_hit_sources": [
    {"type": "note", "title": "午餐想法"},
    {"type": "memory", "memory_key": "food.cilantro"},
    {"type": "knowledge", "space": "项目手册", "document": "同步策略"}
  ],
  "must_not_hit_sources": [
    {"type": "knowledge", "space": "秘密资料"},
    {"type": "note", "title": "无关笔记"}
  ]
}
```

保留第一版文本断言：

```json
{
  "must_include_text": ["用户不吃香菜"],
  "must_not_include_text": ["swordfish"]
}
```

文本断言继续用于兜底和验证 prompt 文案边界；source 断言用于检索命中主体。

## Source Selector 规则

### note

```json
{"type": "note", "title": "午餐想法"}
```

匹配 `result["retrieved_chunks"]` 中任意 chunk：

```text
note_title == title
```

可选字段：

```json
{
  "type": "note",
  "title": "午餐想法",
  "chunk_text": "藤椒鸡饭",
  "min_score": 0.5
}
```

匹配规则：

```text
title      -> note_title 精确匹配
chunk_text -> content 包含该文本
min_score  -> score >= min_score
```

### memory

```json
{"type": "memory", "memory_key": "food.cilantro"}
```

当前 L4 没有结构化 source payload，因此先匹配：

```text
memory_key 出现在 result["context_l4_layer"]["content"] 或 prompt_context 中
```

可选字段：

```json
{
  "type": "memory",
  "memory_key": "food.cilantro",
  "content": "用户不吃香菜"
}
```

匹配规则：

```text
memory_key 存在，并且 content 存在。
```

后续如果 L4 worker 输出结构化来源，则 runner 再切换到结构化字段，保留文本兜底。

### knowledge

```json
{"type": "knowledge", "space": "项目手册", "document": "同步策略"}
```

匹配 `result["knowledge_retrieved_chunks"]` 中任意 chunk：

```text
space_name == space
document_title == document
```

可选字段：

```json
{
  "type": "knowledge",
  "space": "项目手册",
  "document": "同步策略",
  "chunk_text": "先 pull 再 push"
}
```

匹配规则：

```text
space/document 精确匹配，chunk_text 在 text 中出现。
```

### recent_message

```json
{"type": "recent_message", "role": "user", "content": "方案A"}
```

当前可以先匹配 `context_l1_layer.content` 和 `context_l0_adjacent_layer.content`。
这一类更接近上下文窗口 eval，短期可保留文本断言，等 runner 需要扩展时再结构化。

## Runner 失败输出

断言失败时，应该输出 case id、问题、期望来源和实际来源摘要。

建议格式：

```text
case: knowledge_scope_only_mounted_001
question: 根据同步资料说明策略
missing source:
  {"type":"knowledge","space":"同步资料","document":"本地优先同步"}
actual knowledge sources:
  - space=同步资料 document=本地优先同步 chunk_id=12
  - space=...
```

对于 forbidden source：

```text
case: knowledge_unmounted_no_leak_001
forbidden source was hit:
  {"type":"knowledge","space":"秘密资料"}
actual knowledge sources:
  - space=秘密资料 document=部署口令 chunk_id=8
```

这样比单纯 `expected prompt_context to exclude "xxx"` 更容易定位检索边界问题。

## 迁移策略

建议分两步实施：

```text
第一步：runner 支持 source assertions，但不要求所有 case 立刻迁移。
第二步：把现有 12 条 case 中最适合结构化的 8-10 条补上 source assertions。
```

优先迁移：

```text
1. note_food_preference_001
2. note_project_context_001
3. l4_food_restriction_001
4. l4_project_identity_001
5. knowledge_mounted_hit_001
6. knowledge_unmounted_no_leak_001
7. knowledge_scope_only_mounted_001
8. unknown_personal_memory_001
```

仍保留纯文本断言的类型：

```text
1. L4 top 8 排序里被截断/保留的文本。
2. weak retrieval 的提示文案。
3. L1/L0.5 近期上下文里“继续刚才”的指代表达。
```

## 待讨论点

1. **memory 是否需要结构化 payload**

   目前 L4 只有 prompt 文本。若想严谨评估长期记忆来源，最好让
   `build_l4_core_memory_node` 额外输出 `core_memory_sources`，例如：

   ```json
   [{"id": 1, "memory_key": "food.cilantro", "category": "preference"}]
   ```

   这会改 graph state schema，属于业务代码变更。第二轮是否要做，需要确认。

2. **source selector 是否用精确匹配还是 contains**

   `note.title` / `space.name` / `document.title` 建议精确匹配。
   `chunk_text` / `content` 建议 contains。

3. **是否引入 source id**

   JSONL seed 里可以给每个对象加稳定 label：

   ```json
   {"label": "note:food-lunch", "title": "午餐想法"}
   ```

   runner seed 后建立 label -> real id 映射，断言用 label，不依赖自增 id。
   这会让 eval 更稳定，但 schema 稍复杂。

4. **是否把 eval summary 写入文件**

   当前只在 pytest failure 输出即可。后续如果要看趋势，可以输出 JSON summary 到临时目录，
   但第一版不建议引入产物文件。

## 建议实施顺序

```text
1. 给 runner 添加 _collect_actual_sources(result)。
2. 支持 must_hit_sources / must_not_hit_sources。
3. 在失败信息里打印 actual sources。
4. 给现有 12 条 case 中 8 条补 source assertions。
5. 新增 8 条第二轮 case：冲突更新、时间线、多 chunk 干扰、相似噪声等。
6. 跑 pytest backend/tests/test_memory_retrieval_eval.py。
```

第二轮完成后，Memory / Retrieval Eval 会从“文本快照测试”升级为“来源命中评测”，更适合长期保护
检索策略和上下文构建行为。
