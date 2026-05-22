# Chat Window

Chat Window 是 Memory Chat Graph 的用户交互入口，也是第一版对话调试入口。

## 位置

```text
frontend/src/features/chat/
  ChatWindow.tsx
  ChatGraphPanel.tsx
  chatApi.ts
  types.ts
```

主界面在工作区顶部增加：

```text
/app/chat
```

聊天模块已经从笔记页面中拆出，由 React Router 挂载到 `/app/chat`。

## 当前能力

```text
创建对话
切换对话
读取历史消息
发送用户消息
通过 SSE 接收 graph 节点事件
通过 answer_delta 增量展示 AI 回复
AI 回复落库后替换本地临时消息
点击 AI 回复右侧“图”按钮查看本轮 graph
```

## 流式交互

前端调用：

```text
POST /api/conversations/{conversation_id}/chat/stream
```

事件处理：

```text
turn
  初始化本轮 turn_id 和节点状态。

node
  更新 header 中的 graph 节点进度。

answer_delta
  追加到正在生成的 assistant 临时消息。该事件现在来自 LangGraph
  stream_mode="messages" 的 generate_answer 节点 token。

done
  使用后端返回的 user_message / assistant_message 替换临时消息。

error
  显示错误信息。
```

注意：后端会过滤内部 LLM token。比如 L3 planner 产生的 JSON token 不会进入
`answer_delta`，避免用户看到内部规划内容。

## Graph 调试面板

每条已落库的 assistant 消息右侧有 `图` 按钮。

点击后调用：

```text
GET /api/conversations/{conversation_id}/messages/{message_id}/graph
```

面板显示：

```text
LangGraph Mermaid 图
节点状态染色
Graph 点击 / 滚轮缩放
Graph 拖动画布
可点击子图节点
可点击普通节点查看该节点 state 快照
按需读取 LangGraph checkpoint history
L0-L4 上下文金字塔
L3 检索证据
```

Mermaid 图结构来自后端 LangGraph `draw_mermaid()`，前端只负责渲染。
状态染色由后端根据 ChatTurn 的 `node_statuses` 注入 Mermaid class。

Graph 调试面板使用 `React.lazy` 按需加载，Mermaid 渲染器也只在打开 graph 时动态加载。

Graph 交互规则：

```text
鼠标滚轮：围绕鼠标位置缩放
点击空白区域：放大
Ctrl + 点击空白区域：缩小
按住拖动：移动画布
双击空白区域：重置缩放和位置
点击有子图的节点：展开对应子图
点击普通节点：展示该节点完成后的 state 快照
```

## 节点 State 查看

普通节点点击后，`ChatGraphPanel` 会读取：

```text
graph.debug_payload.nodes.{node_id}.state
```

这个 state 是后端在 LangGraph `updates` 事件到达时保存的“节点完成后的累计 state
快照”。它不是模型原始思维链，而是 graph 可恢复执行状态，包括上下文层、工具计划、
工具 observation、回答分支等调试字段。

第一版为了控制体积会裁剪长字符串和长列表。未来如果要做完整 checkpoint 回溯或
对话状态树，可以新增后端接口按 `checkpoint_id` 读取 LangGraph state history。

## Checkpoint History

Graph 调试面板内有 `Checkpoint History` 区域。点击“读取”后调用：

```text
GET /api/conversations/{conversation_id}/turns/{turn_id}/state-history
```

该接口返回 LangGraph 原生 `get_state_history()` 的压缩结果。左侧是 checkpoint
时间线，右侧展示选中 checkpoint 的：

```text
checkpoint_id
parent_checkpoint_id
created_at
next
values
tasks
metadata
```

这个功能和“点击普通节点查看 state”互补：

```text
节点 state
  看某个 Mermaid 节点完成后的累计 state，响应快，适合日常排查。

Checkpoint History
  看 LangGraph 原生 checkpoint 时间线，适合分析恢复、回溯、update_state 和后续状态树。
```

## 子图查看

`ChatGraphPanel` 会读取 `ChatTurnGraphRead.subgraphs`。这个字段是一个
`node_id -> mermaid` 的映射，表示主图里的某个节点可以继续展开为子图。

当前版本暂时没有注册子图。原因是本地 read/write 工具循环已经迁入
`memory_chat_graph` 主图，`agent_think`、`select_tool`、`check_tool_policy`、
`run_read_tool`、`run_write_tool`、`observe_tool_result` 都会直接在主图中显示状态。

```text
subgraphs = {}
```

这套设计刻意让子图注册发生在后端：

```text
backend/app/services/chat_turn_service.py
```

前端只关心“哪个节点有子图”和“如何渲染子图”，不需要知道具体 graph 的构建逻辑。
后续如果 `memory_chat_graph` 新增 RAG 子图、写入审批子图、exec 子图或多 worker 汇总子图，
只需要在 `subgraphs` 中追加对应节点即可。

## UI 设计

聊天窗口保持工具型布局：

```text
左侧：conversation 列表
中间：消息流和输入框
右侧浮层：Graph 调试面板
```

右侧 Graph 面板使用固定定位，避免挤压主聊天区域。

## 后续目标

```text
节点 start/running 事件
更完整的点击 graph 节点查看输入输出
消息编辑与 checkpoint 分支
对话状态树
```
