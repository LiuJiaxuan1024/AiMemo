# 外置精灵聊天

本文记录 Memo Elf 外置聊天的第一版设计与实现。它的目标不是给 AiMemo 页面再加一个聊天框，而是让桌面精灵本身拥有自然语言交流能力。

## 产品方向

当前阶段：

```text
AiMemo 内置聊天
  用于验证 Memory Chat Graph、上下文金字塔、检索和流式输出。

Memo Elf 外置聊天
  复用同一套后端 graph 能力。
  用户可以直接在桌面精灵上输入问题。
  精灵通过气泡回复。
```

后续方向：

```text
AiMemo
  回归“记录笔记、管理记忆”的主功能。

Memo Elf
  成为用户和记忆系统交流的主入口。
  AiMemo Chat 模块可以逐步抽离到精灵能力中。
  右侧调试栏、任务工坊、记忆面板也可以成为精灵菜单/功能面板的一部分。
```

## 与 AiMemo Chat 的关系

外置精灵聊天复用现有 Memory Chat Graph：

```text
load_turn_state
dispatch_context_workers
build_l0_current_input
build_l1_recent_messages
build_l2_conversation_summary
build_l3_retrieved_memory
build_l4_long_term_memory
merge_context_pyramid
plan_retrieval
generate_answer
persist_messages
```

这样可以保证：

```text
上下文连续
可使用用户笔记和长期记忆
checkpoint/thread 机制一致
后续对话状态树能力可复用
```

区别是外置聊天隐藏 graph 细节：

```text
用户只看到精灵回复。
不会看到“我开始检索了”“我开始写入记忆了”等工作状态气泡。
任务、记忆归档、摘要仍可在后台发生。
```

## 后端入口

新增接口：

```text
POST /api/elf/chat/stream
```

请求体复用：

```json
{
  "message": "你还记得我之前说过什么吗？"
}
```

响应是 SSE，内部仍可包含 graph 进度事件：

```text
turn
node
answer_delta
bubble_delta
done
error
```

桌面端当前消费：

```text
bubble_delta
  generate_elf_bubble_answer 的 JSON token。第一版不逐 token 展示。

done.bubbles
  后端 graph 产出的最终气泡数组。

error
  展示错误气泡。
```

后端实现位置：

```text
backend/app/api/elf.py
backend/app/services/elf_chat_service.py
backend/app/services/chat_service.py
```

`stream_conversation_chat_events` 支持：

```text
emit_status_events
answer_mode
```

外置精灵聊天传入：

```text
emit_status_events = False
answer_mode = elf_bubble
```

这样它会走 `generate_elf_bubble_answer` 分支，并避免直接对话时再收到精灵工作状态播报。

## 专用 Conversation

外置精灵第一版会创建或复用一条标题为：

```text
Memo Elf
```

的业务对话。

这条 conversation 的意义：

```text
保存桌面精灵聊天历史
复用 conversation:{id} 作为 LangGraph thread
让 checkpoint 和对话上下文连续
后续可以迁移为专门的 elf_profile / elf_thread
```

## 气泡分段策略

用户与精灵直接聊天时，回复不适合一次塞进一个超长气泡。

当前桌面端策略：

```text
1. 收到 bubble_delta 时不直接改气泡，避免 JSON token 级闪烁。
2. 等 done.bubbles 到达后等待约 650ms。
3. 按后端 graph 产出的 bubble 顺序展示。
4. 每个气泡根据文本长度展示一段时间，字数越多停留越久。
5. 气泡之间间隔约 420ms。
```

这样可以避免：

```text
气泡在 token 级别频繁闪烁
长回答挤在一个小气泡里
用户还没读完就跳走
```

## emoji 字段

每个气泡包含：

```text
text
emoji
```

`emoji` 用于决定精灵表情或动作，例如：

```text
idle_soft
thinking
working_focus
success_smile
error_worried
sleepy
curious
memory_glow
shy_blush
angry_pout
surprised
sad_teary
wronged_pout
confused
proud
playful_wink
serious
relaxed
encouraging
speechless
```

这些值与 `frontend/public/elf/memo/*.png` 一一对应。后端仍兼容旧值：

```text
soft -> idle_soft
happy -> success_smile
worried -> error_worried
memory -> memory_glow
thinking
```

当前 graph 已实现专用分支：

```text
merge_prompt_context
  -> route_answer_mode
      -> generate_answer
      -> generate_elf_bubble_answer
  -> persist_messages
```

`generate_elf_bubble_answer` 要求模型输出：

```json
{
  "bubbles": [
    {
      "text": "一段语义完整、适合放进气泡的话",
      "emoji": "idle_soft"
    }
  ]
}
```

气泡切分规则：

```text
一个 bubble 只表达一种主要情绪。
如果同一段里先开心后担心、先回忆后提问、先解释后鼓励，应拆成多个 bubble。
遇到“但是/不过/然而/突然/同时/如果/所以”等明显语气转折时，优先拆分。
后端会对模型输出再做一次轻量规整，防止一个 emoji 对应多种情绪。
```

桌面端仍保留规则拆分作为 fallback：如果后端没有返回 bubbles，就用旧规则从最终文本推断。

## 桌面端实现

当前实现位置：

```text
desktop/index.html
desktop/src/main.ts
desktop/src/styles.css
```

交互：

```text
点击精灵
  打开菜单。

点击“和我聊聊”
  打开输入面板。

发送消息
  调用 /api/elf/chat/stream。

收到回答
  等流结束后分气泡展示。
```

## 后续计划

建议下一步：

```text
1. 后端返回结构化 bubble parts，而不是桌面端规则拆分。
2. 为外置精灵聊天增加独立 graph 调试入口。
3. 把 AiMemo 内置聊天右侧 graph/debug 面板抽成可复用功能面板。
4. 桌面精灵支持“打开聊天历史”“打开相关记忆”。
5. 对话输入支持快捷键唤起。
```
