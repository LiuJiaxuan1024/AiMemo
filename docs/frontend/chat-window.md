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
```

## 子图查看

`ChatGraphPanel` 会读取 `ChatTurnGraphRead.subgraphs`。这个字段是一个
`node_id -> mermaid` 的映射，表示主图里的某个节点可以继续展开为子图。

当前第一版只接入：

```text
build_local_operator_context -> Local Operator Graph
```

用户点击主图里的 `build_local_operator_context` 节点后，右侧面板会在主图下方展开
Local Operator 子图，并展示该节点在 `debug_payload.nodes` 中记录的调用详情。

主图中的 `build_local_operator_context` 使用独立的子图节点颜色，和普通节点区分。
Local Operator 子图内部也会根据本轮执行结果染色：

```text
succeeded：本轮实际走过的节点
skipped：本轮未走到的分支
failed：后续异常路径
```

当前子图状态来自 Local Operator 最终 state 推导，不是独立的嵌套实时 stream。
也就是说，主图会实时看到 `build_local_operator_context` 这个 worker 是否完成；
展开子图后看到的是该 worker 完成后记录下来的内部执行路径。

这套设计刻意让子图注册发生在后端：

```text
backend/app/services/chat_turn_service.py
```

前端只关心“哪个节点有子图”和“如何渲染子图”，不需要知道具体 graph 的构建逻辑。
后续如果 `memory_chat_graph` 新增 RAG 子图、写入工具子图或多 worker 汇总子图，只需要在
`subgraphs` 中追加对应节点即可。

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
