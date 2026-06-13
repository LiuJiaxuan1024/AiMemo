# Chat View / Conversation Export Refactor Plan

## 背景

当前聊天页和对话导出页已经具备相近的用户体验目标：

- 聊天页：React 组件直接连接后端 API，支持发送消息、片段追问、删除、Graph 调试、Checkpoint history 等在线能力。
- 导出页：后端生成一个自包含 HTML，嵌入消息、片段追问和 Graph 数据，让接收者离线阅读。

现状的问题是两者的展示实现并不共用。聊天页使用 `frontend/src/features/chat` 下的 React 组件，导出页主要由 `backend/app/services/conversation_export_service.py` 拼接 HTML/CSS/JS 字符串。短期可以快速交付，但长期会带来分叉：

- 样式需要维护两套。
- Graph 面板、片段追问弹窗、消息气泡的交互容易不一致。
- 聊天页增加新消息类型后，导出页需要重复实现。
- 导出页内联 JS 缺少 TypeScript、组件测试和前端构建链保护。

目标是把两者收敛为“同一套展示前端 + 不同能力适配器”。

## 目标

1. 抽出可复用的只读/可交互对话展示层，让在线聊天和导出阅读共享组件。
2. 后端导出接口不再拼接完整交互页面，而是产出标准化 `ConversationExportSnapshot` 数据。
3. 导出 HTML 通过一个轻量 viewer bundle 渲染 snapshot，保留当前聊天页的视觉风格和主要查看交互。
4. 在线模式保留后端能力；离线模式明确降级为只读，但展示结构一致。
5. 重构过程中保持现有聊天功能和现有导出接口可用，分阶段替换。

## 非目标

- 不在本轮重构里改变聊天消息、Graph、片段追问的数据库模型。
- 不重新设计 Memory Chat Graph 或 checkpoint 存储。
- 不要求第一阶段导出的 HTML 100% 嵌入 Mermaid runtime 和全部 checkpoint history；这两项可以分阶段补齐。
- 不把导出页做成依赖本地 AiMemo 服务的页面。分享文件仍应可以离线打开。

## 目标架构

建议新增一个展示层模块：

```text
frontend/src/features/chat_view/
  types.ts
  ChatTranscript.tsx
  ChatMessageItem.tsx
  ChatMessageBody.tsx
  SegmentFollowupPanel.tsx
  SegmentFollowupModal.tsx
  GraphDebugDrawer.tsx
  adapters/
    liveChatAdapter.ts
    exportSnapshotAdapter.ts
  export_viewer/
    ExportViewerApp.tsx
    bootstrap.tsx
```

原有在线聊天页：

```text
ChatWindow
  -> liveChatAdapter
  -> ChatTranscript / SegmentFollowupPanel / GraphDebugDrawer
```

导出 HTML：

```text
ConversationExportSnapshot JSON
  -> export viewer bundle
  -> exportSnapshotAdapter
  -> ChatTranscript / SegmentFollowupPanel / GraphDebugDrawer
```

核心原则是：组件只关心“展示数据”和“能力开关”，不直接知道自己运行在在线聊天页还是导出 HTML 里。

## 数据协议

新增前后端共享语义的导出快照：

```ts
interface ConversationExportSnapshot {
  schema_version: 1;
  conversation: {
    id: number;
    title: string;
    summary: string;
    langgraph_thread_id: string;
    exported_at: string;
  };
  messages: ExportMessage[];
  graphs: Record<string, ExportGraphSnapshot>;
}

interface ExportMessage {
  id: number;
  role: "user" | "assistant" | "system";
  content: string;
  content_html?: string;
  created_at: string;
  status: string;
  attachments: ExportAttachment[];
  turn_id?: number | null;
  graph_id?: string | null;
  followup_threads: ExportSegmentFollowupThread[];
}

interface ExportSegmentFollowupThread {
  segment_id: string;
  original_text: string;
  position: { start: number; end: number } | null;
  status: "pending" | "answered" | "failed";
  turns: ExportSegmentFollowupTurn[];
}

interface ExportSegmentFollowupTurn {
  question: string;
  answer: string;
  answer_html?: string;
  status: "pending" | "answered" | "failed";
  timestamp: string;
  graph_id?: string | null;
}

interface ExportGraphSnapshot {
  turn_id: number;
  status: string;
  node_statuses: Record<string, string>;
  mermaid: string;
  subgraphs: Record<string, string>;
  context_layers: unknown[];
  retrieved_chunks: unknown[];
  debug_payload: unknown;
  state_history?: unknown;
}
```

后端继续负责：

- 选择导出消息范围。
- 排除隐藏的片段追问原始消息。
- 将片段追问线程挂回源 assistant 消息。
- 收集 Graph、上下文层、检索证据和调试 payload。
- 嵌入图片附件或提供附件元数据。

前端 viewer 负责：

- Markdown/HTML 渲染。
- 消息气泡、片段标记、片段面板、片段弹窗。
- Graph 调试抽屉、标签页、节点状态、上下文和证据展示。
- 根据 mode 隐藏在线操作，例如继续追问、删除消息、重新拉取 graph。

## 能力适配器

展示组件通过 adapter 获取能力：

```ts
interface ChatViewAdapter {
  mode: "live" | "export";
  canMutate: boolean;
  loadGraph(messageId: number): Promise<ChatTurnGraph | ExportGraphSnapshot | null>;
  loadStateHistory?(turnId: number): Promise<ChatTurnStateHistory | null>;
  submitSegmentFollowup?(request: SegmentFollowupRequest): Promise<void>;
  deleteMessage?(messageId: number): Promise<void>;
}
```

在线模式：

- `mode = "live"`
- `canMutate = true`
- `loadGraph` 调用现有 `/turns/{turn_id}/graph`
- `loadStateHistory` 调用现有 `/state-history`
- `submitSegmentFollowup` 和 `deleteMessage` 保持现有行为

导出模式：

- `mode = "export"`
- `canMutate = false`
- `loadGraph` 从 snapshot 的 `graphs` 字典读取
- `loadStateHistory` 从 snapshot 读取，缺失时显示“导出文件未包含 checkpoint history”
- 不提供继续追问和删除能力

## 组件拆分建议

### ChatTranscript

负责消息列表布局、空状态、滚动容器和消息选择。

在线聊天页继续支持：

- streaming 自动滚动
- 导出多选
- context menu 删除

导出 viewer 支持：

- 只读滚动
- 点击消息侧边按钮打开片段追问和 Graph

### ChatMessageBody

复用当前 `AssistantMessageBody` 的核心逻辑：

- 普通 Markdown
- segments 时间线
- tool cards
- thought recap
- command result card
- attachments

需要把“可执行命令建议”等在线行为通过 props 传入，导出模式默认禁用。

### SegmentFollowupPanel / SegmentFollowupModal

从 `MessageList.tsx` 中拆出，改成不依赖 `messages` 全局数组，而是接收标准化 thread 数据：

```ts
sourceMessage: ChatViewMessage | null;
threads: ChatViewFollowupThread[];
mode: "live" | "export";
```

在线模式显示继续追问、删除等按钮；导出模式只显示查看、展开和打开 Graph。

### GraphDebugDrawer

从 `ChatGraphPanel.tsx` 提炼：

- `graph` 由外部传入
- `stateHistory` 由外部传入或通过 loader 懒加载
- `onLoadStateHistory` 可选
- `mode="export"` 时不发后端请求

这样导出 viewer 可以复用图结构、上下文、性能、证据等 UI。

## 构建与导出方案

推荐增加一个专门的导出 viewer bundle：

```text
frontend/src/features/chat_view/export_viewer/bootstrap.tsx
```

构建产物可以是：

- `dist/export-viewer.js`
- `dist/export-viewer.css`

后端导出 HTML 时：

1. 读取构建后的 viewer JS/CSS。
2. 将 `ConversationExportSnapshot` 作为 `<script type="application/json">` 嵌入。
3. 输出单文件 HTML。

开发环境下可以先允许后端继续生成旧版 HTML；等 viewer bundle 稳定后再替换。

## 分阶段计划

### Phase 0：当前快照

已在 `main` 上提交当前可回退版本：

```text
d6988fd feat: add cloud sync and conversation export baseline
```

### Phase 1：定义共享类型和 snapshot

- 新增 `frontend/src/features/chat_view/types.ts`
- 后端 `conversation_export_service` 输出 `ConversationExportSnapshot`
- 保留当前 HTML 字符串渲染作为兼容层
- 测试覆盖 selected export、include_all、followups、graphs

### Phase 2：抽离只读展示组件

- 从 `MessageList.tsx` 提取 `ChatMessageBody`
- 从 `MessageList.tsx` 提取 `SegmentFollowupPanel` 和 `SegmentFollowupModal`
- 保持 `ChatWindow` 行为不变
- 为导出模式补 story-like fixture 或组件测试

### Phase 3：抽离 GraphDebugDrawer

- 改造 `ChatGraphPanel`，让 state history 由外部 loader 注入
- 将 Graph tabs 作为纯展示组件
- 在线聊天页仍使用现有 API
- 导出 snapshot 直接提供 graph 数据

### Phase 4：导出 viewer bundle

- 增加 export viewer 入口
- 将 snapshot 嵌入 HTML
- viewer 使用共享组件渲染
- 后端删除大部分内联 JS/CSS 字符串，只保留 HTML shell

### Phase 5：完善离线 Graph 能力

- 评估是否嵌入 Mermaid runtime
- 评估是否导出 checkpoint history
- 控制文件体积：提供导出选项，例如“包含 checkpoint history / 包含完整 graph runtime”

## 风险与对策

| 风险 | 对策 |
| --- | --- |
| 抽组件时破坏在线聊天 streaming | 先只抽纯展示层，保留 `ChatWindow` 的状态管理和 SSE 逻辑 |
| 导出 HTML 文件过大 | 默认不嵌入 Mermaid runtime 和 checkpoint history，作为高级选项 |
| Markdown 渲染差异 | 让导出 viewer 使用同一套 `MarkdownMessage` |
| 片段追问和原文匹配不稳定 | 继续保留 `segment_id` 和 `position`，文本匹配只做 fallback |
| Graph 面板依赖后端请求 | 把 loader 改成可选；导出模式读 snapshot |
| 一次重构范围过大 | 按 Phase 提交，每个 Phase 保持可运行和可回退 |

## 验收标准

第一阶段完成后：

- 后端能返回结构化 snapshot。
- 原导出 HTML 行为不回退。
- 现有导出测试通过。

组件抽离完成后：

- 在线聊天页视觉和交互不变。
- 导出 viewer 使用共享 `ChatMessageBody`、片段追问组件和 Graph drawer。
- 导出 HTML 中的消息气泡、片段追问弹窗、Graph 抽屉和聊天页保持同源样式。

最终完成后：

- 对话展示相关 UI 不再在后端字符串模板中重复实现。
- 修改聊天消息样式时，导出 viewer 自动同步。
- 离线导出明确只读，但查看体验和在线聊天高度一致。

