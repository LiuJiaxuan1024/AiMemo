# 前端体验优化建议报告

本文记录 Ai 记当前前端界面的体验问题、React 生态可复用方案，以及建议的分阶段优化路线。

## 当前结论

Ai 记现在的前端已经能支撑核心功能，但仍处于“工程原型”形态：

```text
优点
  结构简单，依赖少，调试成本低。
  笔记、对话、任务、记忆管理已经跑通。
  布局基本完成了固定高度和内部滚动，不再明显被内容撑爆。

主要问题
  UI 视觉层次偏硬，卡片、边框、间距、状态颜色缺少统一设计 token。
  聊天消息不渲染 Markdown，AI 回复里的标题、列表、代码块会以原始文本出现。
  API 请求、轮询、缓存、乐观更新都靠组件手写，后期会越来越难维护。
  对话消息列表没有虚拟化，长对话后性能和滚动体验会下降。
  表单、弹层、Tabs、Tooltip、Drawer 等交互都偏手写，可访问性和键盘体验不足。
  图标体系缺失，按钮大多依赖文字，工具型界面不够轻。
```

我的建议是：**不要一次性重写前端**。先做“体验基础设施”升级，再逐步调整视觉。

## 参考资料

- React Markdown：`react-markdown` 支持把 Markdown 转成 React 元素，并可配合 `remark-gfm` 支持 GitHub Flavored Markdown；安全场景建议配合 `rehype-sanitize`。  
  https://github.com/remarkjs/react-markdown

- Radix Primitives：低层 UI primitives，强调 accessibility、customization、developer experience，适合做 Dialog、Tabs、Tooltip、Select、ScrollArea 等基础交互。  
  https://www.radix-ui.com/primitives/docs/overview/introduction

- shadcn/ui：提供 Button、Tabs、Drawer、Sidebar、Textarea、Tooltip、Typography 等组件目录，适合在 React 项目里建立可控的组件系统。  
  https://ui.shadcn.com/docs/components

- TanStack Query：用于服务端状态 fetching、caching、synchronizing、updating，适合替代当前组件里手写的 list / refresh / polling。  
  https://tanstack.com/query/

- React Virtuoso：提供 React 列表虚拟化能力；官方也有面向聊天场景的 Message List 说明，但专用 Message List 是商业包，MIT 的 `react-virtuoso` 核心列表仍可用于长列表优化。  
  https://virtuoso.dev/

- react-textarea-autosize：轻量 textarea 替代组件，可根据输入内容自动调整高度，包体很小，适合聊天输入框。  
  https://www.npmjs.com/package/react-textarea-autosize

- lucide-react：React 图标库，适合工具按钮、状态、导航和调试面板。  
  https://www.npmjs.com/package/lucide-react

## 建议依赖

### 第一优先级

```text
react-markdown
remark-gfm
rehype-sanitize
```

用途：

```text
AI 回复 Markdown 渲染
代码块 / 列表 / 表格 / 引用 / 粗体等展示
避免直接把模型输出当 HTML 注入
```

当前最明显的体验缺口就是聊天 Markdown 没有渲染。这个改动小、收益大，建议最先做。

### 第二优先级

```text
@tanstack/react-query
```

用途：

```text
统一管理 notes / conversations / messages / jobs / memories 的请求状态
自动缓存和刷新
减少 useEffect + useState + 手写 refresh 的重复逻辑
更自然地处理轮询、失败重试、乐观更新
```

当前 `App.tsx`、`ChatWindow.tsx`、`JobDrawer.tsx`、`MemoryPanel.tsx` 都在重复处理请求状态。后期 graph、summary、memory 调试面板越来越多时，手写模式会很快变乱。

当前进展：

```text
已引入 @tanstack/react-query。
已接管 jobs / job graph / memories 的读取、轮询、刷新和 mutation 后失效刷新。
已接管 notes 的列表读取、详情读取、创建笔记和处理中轮询。
conversations 暂时仍保留现有 useState/useEffect。
chat stream 仍保留自定义 SSE 逻辑，不直接交给 Query。
```

### 第三优先级

```text
lucide-react
react-textarea-autosize
```

用途：

```text
lucide-react
  给发送、刷新、新建、图示、停用、启用、关闭、固定等按钮加图标。

react-textarea-autosize
  聊天输入框自动增高，设置 minRows/maxRows，避免手写 resize 和固定高度。
```

这两个依赖轻，能快速提升“工具感”和输入体验。

### 第四优先级

```text
Radix Primitives 或 shadcn/ui
```

建议取舍：

```text
短期
  直接用 Radix 的 Tabs / Tooltip / Dialog / ScrollArea / Select / Separator。
  继续保留当前 CSS，降低迁移成本。

中期
  如果愿意引入 Tailwind，可以采用 shadcn/ui 作为组件系统底座。
  shadcn 的优势是组件源码归项目所有，方便开源项目长期维护和定制。
```

不建议现在立刻全量引入 shadcn/ui 并重写页面。Ai 记当前还在产品结构探索阶段，过早迁移会拖慢 agent 功能迭代。

### 第五优先级

```text
react-virtuoso
```

用途：

```text
长对话消息列表虚拟化
长 job 列表虚拟化
长 memory 列表虚拟化
```

当前数据量不大，可以先不做。但对话历史增长后，这会变成明显问题。

## 页面级优化建议

## 1. 总体布局

当前是：

```text
左侧笔记列表
右侧 workspace
右侧悬浮 Job Drawer
```

建议改为更稳定的三层信息架构：

```text
App Shell
  Sidebar: 笔记 / 对话 / 记忆入口
  Main: 当前核心工作区
  Inspector: 调试信息，以 Sheet/Drawer 方式出现
```

当前“精灵工坊”既承载 jobs，又承载 memories。这个想法有趣，但需要更清晰：

```text
任务
  当前后台工作、失败、重试、graph。

记忆
  用户可控的长期记忆。

调试
  当前对话 graph、上下文金字塔、检索证据、性能指标。
```

建议后续把“任务”和“记忆”保留在精灵工坊，但“当前对话 graph 调试”更适合做成对话消息右侧的 Inspector，而不是和全局 job drawer 混在一起。

## 2. 视觉系统

当前 CSS 颜色、边框、间距已经可用，但不够体系化。建议建立 design tokens：

```css
:root {
  --bg-app: #f6f7f9;
  --bg-panel: #ffffff;
  --bg-subtle: #f8fafc;
  --border: #d9dde5;
  --border-subtle: #e6e9ef;
  --text: #1d2433;
  --text-muted: #667085;
  --accent: #1f6feb;
  --success: #027a48;
  --warning: #c2410c;
  --danger: #b42318;
  --radius-sm: 6px;
  --radius-md: 8px;
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
}
```

收益：

```text
后续改主题更简单
避免颜色越写越散
减少同类组件视觉不一致
```

## 3. 聊天窗口

当前问题：

```text
Markdown 未渲染
代码块不可读
AI 消息和用户消息视觉区分较硬
Graph 按钮是文字“图”，不够直观
streaming 状态只有“正在思考...”，缺少阶段提示
```

建议：

```text
消息内容
  新建 MarkdownMessage 组件。
  assistant 消息使用 react-markdown + remark-gfm + rehype-sanitize。
  user 消息保持纯文本 pre-wrap，避免用户输入被误渲染。

消息操作
  assistant 消息右上角放 icon button：
    graph
    copy
    retry
    inspect context

streaming
  正在 plan/retrieve/build context 时显示轻量状态条：
    正在检索记忆...
    正在组织上下文...
    正在回答...

布局
  assistant 消息不必像传统气泡那么窄，可以用更接近 ChatGPT 的正文块。
  用户消息保持右侧小气泡。
```

建议组件拆分：

```text
frontend/src/features/chat/components/
  ChatWindow.tsx
  ConversationList.tsx
  MessageList.tsx
  MessageItem.tsx
  MarkdownMessage.tsx
  ChatComposer.tsx
  GraphInspectButton.tsx
```

## 4. 笔记页

当前问题：

```text
新建笔记 composer 永远占据顶部，占空间。
笔记详情和新建表单视觉权重接近，主次不清。
列表缺少搜索、过滤、按状态筛选。
AI 整理状态和 embedding 状态比较分散。
```

建议：

```text
第一版
  将“新建笔记”改成按钮 + 展开式 composer。
  笔记详情区域优先显示选中笔记。
  增加搜索框。

第二版
  左侧列表加入状态过滤：
    全部 / 整理中 / 建立记忆中 / 失败
  note detail 右侧显示结构化元信息：
    tags
    summary
    embedding status
    chunk count
```

## 5. 精灵工坊

当前问题：

```text
信息密度高，任务列表、任务详情、graph、记忆管理挤在同一个窄 drawer 中。
Graph SVG 区域容易出现内部滚动套滚动。
Job row 的 type、状态、时间视觉层次还可以更清晰。
```

建议：

```text
Drawer 宽度
  固定 420px 对 graph 不友好。
  可以提供 compact / wide 两档：
    compact: 420px
    wide: min(760px, 70vw)

任务列表
  用状态图标 + badge。
  failed job 固定在上方或提供状态过滤。

Graph
  小 drawer 中只显示简化状态。
  点击“展开”后用 Dialog / Sheet 大图查看。

记忆
  生效 / 停用命名已经调整正确。
  后续增加“为什么写入”字段展示 reason/source。
```

## 6. 记忆管理

当前功能已经具备：

```text
查看
编辑
停用
启用
筛选
```

建议补充：

```text
搜索记忆
按 importance/confidence 排序
显示“会进入 L4 / 不会进入 L4”的明确说明
编辑时用 Slider 替代 number input
category 用 Select / Badge
source 点击后跳转到对应 chat message
```

## 7. 请求状态管理

当前模式：

```text
组件内部 useState
组件内部 useEffect
手写 refresh
手写 polling
```

建议迁移到 TanStack Query：

```text
notes
  useNotesQuery
  useNoteQuery
  useCreateNoteMutation

chat
  useConversationsQuery
  useMessagesQuery
  streamChat 仍保留自定义逻辑

jobs
  useJobsQuery(refetchInterval)
  useJobGraphQuery

memories
  useMemoriesQuery
  useUpdateMemoryMutation
```

收益：

```text
减少重复状态代码
自动缓存
切换 tab 不必重复加载
失败重试和 loading/error 状态统一
```

## 推荐实施顺序

### Phase 1：低风险体验补齐

```text
1. 引入 react-markdown / remark-gfm / rehype-sanitize。
2. 新建 MarkdownMessage，assistant 消息渲染 Markdown。
3. 引入 lucide-react，替换“图”“刷新”“新建”等文字按钮的一部分。
4. 引入 react-textarea-autosize，优化聊天输入框。
```

这一步改动小、收益最大。

### Phase 2：组件拆分和状态治理

```text
1. 拆分 ChatWindow。
2. 拆分 MemoryPanel。
3. 引入 TanStack Query 管理 notes/jobs/memories/conversations。
4. 保留 streamChat 自定义 SSE 逻辑，但把完成后的 cache invalidation 交给 Query。
```

这一步提升可维护性。

### Phase 3：设计系统

```text
1. 建立 CSS design tokens。
2. 抽 Button / Badge / Tabs / Textarea / Select / EmptyState / StatusPill。
3. 评估 Radix Primitives。
4. 如果确定要走 Tailwind，再引入 shadcn/ui。
```

这一步让界面真正稳定下来。

### Phase 4：高级体验

```text
1. 长列表虚拟化。
2. Graph 大图 Inspector。
3. 上下文金字塔可视化。
4. 记忆来源跳转。
5. 深色模式。
```

## 我建议下一步实际做什么

下一步最推荐做：

```text
实现 MarkdownMessage
引入 react-markdown + remark-gfm + rehype-sanitize
只对 assistant 消息启用 Markdown 渲染
补充代码块、列表、表格、引用的 CSS
```

理由：

```text
用户马上能感受到变化。
不会影响后端 agent。
不会改变 graph/job/memory 结构。
风险低，收益高。
```

这一步完成后，AI 回复会从“原始 markdown 文本”变成真正可读的富文本回答。

## 已完成记录

### 2026-05-17：Assistant Markdown 渲染

已实现：

```text
新增 frontend/src/features/chat/MarkdownMessage.tsx
assistant 消息使用 react-markdown 渲染
启用 remark-gfm 支持表格、任务列表等 GFM 语法
启用 rehype-sanitize 避免直接渲染不可信 HTML
用户消息仍保持纯文本展示
补充段落、标题、列表、引用、代码块、表格、链接样式
```

验证：

```text
npm run build 通过
```

### 2026-05-17：输入框和图标基础体验

已实现：

```text
引入 lucide-react
引入 react-textarea-autosize
聊天输入框支持 1-6 行自动增高
聊天发送按钮增加 SendHorizontal 图标
assistant 消息 graph 按钮改为 GitBranch 图标
对话新建按钮增加 Plus 图标
精灵工坊入口、固定按钮、任务/记忆 tab 增加图标
记忆管理的刷新、编辑、停用、启用、保存、取消按钮增加图标
```

验证：

```text
npm run build 通过
```

### 2026-05-17：Shared UI 基础组件

已实现：

```text
新增 frontend/src/shared/ui/Button.tsx
新增 frontend/src/shared/ui/Badge.tsx
新增 frontend/src/shared/ui/EmptyState.tsx
新增 frontend/src/shared/ui/index.ts
```

已迁移：

```text
笔记页状态标签 -> Badge
笔记页保存按钮 -> Button
笔记页空状态 -> EmptyState
聊天页新建 / 发送 / graph 按钮 -> Button
聊天页空状态 -> EmptyState
Job 列表状态 -> Badge
Job / Graph 空状态 -> EmptyState
记忆状态 -> Badge
记忆操作按钮 -> Button
记忆空状态 -> EmptyState
```

保留策略：

```text
列表项 button、tab button 暂时保留业务样式。
它们属于导航/选择控件，后续应单独抽 ListItemButton / SegmentedTabs，而不是强塞进通用 Button。
```

验证：

```text
npm run build 通过
```

### 2026-05-17：Shared UI Tabs / Header

已实现：

```text
新增 frontend/src/shared/ui/SegmentedTabs.tsx
新增 frontend/src/shared/ui/PanelHeader.tsx
```

已迁移：

```text
工作区“笔记 / 对话”切换 -> SegmentedTabs
精灵工坊“任务 / 记忆”切换 -> SegmentedTabs
记忆面板“生效 / 停用”切换 -> SegmentedTabs
精灵工坊标题区 -> PanelHeader
Graph 调试面板标题区 -> PanelHeader
Graph 调试空状态 -> EmptyState
```

清理：

```text
移除 workspace-tabs / drawer-tabs / segmented-control / job-drawer-header 旧样式。
```

验证：

```text
npm run build 通过
```

### 2026-05-17：ChatWindow 组件拆分

已实现：

```text
新增 frontend/src/features/chat/ConversationList.tsx
新增 frontend/src/features/chat/MessageList.tsx
新增 frontend/src/features/chat/ChatComposer.tsx
在 frontend/src/features/chat/types.ts 中导出 DraftAssistantMessage
```

职责调整：

```text
ChatWindow
  保留会话加载、SSE 流式事件、graph 状态和消息状态管理。

ConversationList
  只负责会话列表、新建会话和切换会话。

MessageList
  只负责消息列表、Markdown/纯文本展示和每条 assistant 消息的 graph 入口。

ChatComposer
  只负责聊天输入框、发送按钮和提交表单。
```

验证：

```text
npm run build 通过
```

### 2026-05-17：笔记区组件拆分

已实现：

```text
新增 frontend/src/features/notes/NoteSidebar.tsx
新增 frontend/src/features/notes/NoteComposer.tsx
新增 frontend/src/features/notes/NoteDetail.tsx
新增 frontend/src/features/notes/NotesWorkspace.tsx
新增 frontend/src/features/notes/noteUtils.ts
```

职责调整：

```text
App
  保留页面级状态、笔记接口调用、工作区 tab 和整体布局。

NoteSidebar
  只负责品牌区、笔记列表、状态标签和选择笔记入口。

NoteComposer
  只负责新建笔记表单展示和提交入口。

NoteDetail
  只负责选中笔记详情、摘要、标签和处理状态展示。

NotesWorkspace
  组合新建笔记、笔记详情和精灵提示区。
```

后续收益：

```text
笔记搜索、状态过滤、笔记编辑、chunk/embedding 调试信息都可以在 features/notes 内部继续扩展。
App.tsx 不再承载具体笔记 UI 细节，后续更适合迁移到 TanStack Query 或自定义 hook。
```

验证：

```text
npm run build 通过
```

### 2026-05-17：MemoryPanel 组件拆分

已实现：

```text
新增 frontend/src/features/memories/MemoryToolbar.tsx
新增 frontend/src/features/memories/MemoryCard.tsx
新增 frontend/src/features/memories/memoryUtils.ts
```

职责调整：

```text
MemoryPanel
  保留记忆列表加载、编辑状态、保存、停用、启用等数据调度。

MemoryToolbar
  只负责生效/停用 tab、类型筛选和刷新按钮。

MemoryCard
  只负责单条记忆的展示、编辑表单和操作按钮。

memoryUtils
  统一维护记忆类型文案、状态文案、时间和分数展示格式。
```

后续收益：

```text
记忆搜索、排序、来源跳转、Slider 编辑 importance/confidence 可以分别落在 toolbar/card 内。
MemoryPanel 之后迁移到 TanStack Query 时，不需要改动记忆卡片展示逻辑。
```

验证：

```text
npm run build 通过
```

### 2026-05-17：TanStack Query 第一阶段接入

已实现：

```text
安装 @tanstack/react-query
新增 frontend/src/shared/query/queryClient.ts
在 frontend/src/main.tsx 接入 QueryClientProvider
JobDrawer 使用 useQuery 接管 job 列表轮询
JobDrawer 使用 useQuery 接管 job graph 读取和运行中刷新
MemoryPanel 使用 useQuery 接管 L4 记忆列表读取
MemoryPanel 使用 useMutation 接管记忆保存、停用、启用
mutation 成功后统一 invalidate memories 查询
```

保留策略：

```text
ChatWindow 的 streamChat 暂时不迁移到 Query。
原因是 SSE 是连续事件流，不是普通 request/response；当前组件需要实时处理 turn/node/answer_delta/done 事件。

App.tsx 的 notes 请求暂时保留原实现。
原因是本轮先验证 Query 在 jobs/memories 上的收益，下一步再迁移 notes，风险更低。
```

收益：

```text
减少 JobDrawer 和 MemoryPanel 内部手写 loading / polling / refresh 状态。
轮询间隔、缓存、失败重试由 Query 统一管理。
记忆 mutation 后的刷新入口收敛到 invalidateQueries，后续更容易加乐观更新。
```

验证：

```text
npm run build 通过
```

### 2026-05-17：Notes 请求状态迁移

已实现：

```text
App.tsx 使用 useQuery 接管 notes 列表读取。
App.tsx 使用 useQuery 接管选中 note 详情读取。
App.tsx 使用 useMutation 接管 createNote。
新增 isNoteProcessing 工具函数，统一判断 AI 整理 / embedding 是否仍在进行。
notes 列表存在后台处理中笔记时，每 3 秒轮询刷新。
选中 note 本身仍在处理时，每 3 秒轮询刷新详情。
创建笔记成功后自动选中新笔记，并 invalidate notes 缓存。
```

职责变化：

```text
App
  仍然负责页面级状态，例如当前 tab、选中 note id、笔记输入框草稿。
  不再手写 listNotes/getNote/createNote 的 loading、saving、refresh 状态。

NoteSidebar / NotesWorkspace / NoteDetail
  仍然只负责展示和用户交互，不直接接触 Query 或 API。
```

保留策略：

```text
笔记输入框 title/content 仍保留本地 useState。
这是典型的客户端临时表单状态，不属于服务端状态缓存。

ChatWindow 暂不迁移。
对话流式输出需要处理 SSE 的 answer_delta/node/done 等事件，后续应单独设计 useStreamChat，而不是直接套普通 query。
```

验证：

```text
npm run build 通过
```
