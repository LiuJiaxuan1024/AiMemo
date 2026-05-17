# 精灵助手设计草案

本文记录 Ai 记前端精灵助手的产品定位、第一版能力边界、状态设计和接入计划。精灵不是单纯装饰物，而是 Ai 记后台 agent 系统的可视化媒介。

## 设计目标

Ai 记的核心是“用户记录笔记，AI 基于个人知识库帮助用户做事”。随着 job、graph、memory、chat stream 逐渐复杂，用户需要一个更直观的入口理解系统正在做什么。

精灵助手的目标是：

```text
把后台任务人格化
把 agent 状态可视化
把调试入口轻量化
把产品体验变得更有陪伴感
```

它应该回答用户一个简单问题：

```text
现在 Ai 记的 AI 正在帮我做什么？
```

## 第一版定位

第一版精灵定位为：

```text
后台任务状态媒介 + 精灵工坊入口
```

它优先服务现有系统，而不是独立做一个复杂虚拟角色。

第一版要做：

```text
右下角显示 Live2D 精灵。
精灵旁边显示状态气泡。
根据 jobs 状态切换精灵提示文案。
点击精灵打开 / 收起现有精灵工坊。
任务失败时给出更明显提醒。
任务运行时提示正在整理、嵌入、处理。
没有任务时保持 idle 状态。
```

第一版暂不做：

```text
语音对话
主动聊天
复杂角色人格
大量随机台词
复杂动作编排
多模型换装系统
用户自定义角色
直接接入 Memory Chat Graph stream
```

原因：

```text
当前项目核心仍然是笔记、记忆、RAG 和 agent graph。
精灵第一版应该先和真实系统状态闭环，而不是抢产品重心。
对话 stream 状态目前仍在 ChatWindow 内部，后续需要单独抽共享状态或事件总线。
```

## 技术选择

当前第一版实际使用：

```text
透明 PNG 表情切换
```

选择原因：

```text
资源完全在本地，首屏不依赖远程模型。
不会触发 Live2D runtime 的模型加载和边界计算异常。
实现足够轻，适合先验证“精灵状态反馈”这个产品闭环。
后续升级 Live2D 时，可以保留 ElfState，只替换渲染层。
```

已暂缓直接使用：

```text
OhMyLive2D
```

选择原因：

```text
接入成本低。
适合快速把 Live2D 精灵显示出来。
支持 Web 场景，不需要一开始深入 PixiJS / Cubism runtime 细节。
后续如果控制力不足，可以迁移到 pixi-live2d-display 或 easy-live2d。
```

备选方案：

```text
pixi-live2d-display
  更底层，控制力更强，适合后期做动作、命中区域、表情、状态机深度控制。

easy-live2d
  介于 OhMyLive2D 和 pixi-live2d-display 之间，API 更集中，但需要进一步验证生态稳定性。
```

## 产品形态

第一版建议替换现在右下角的精灵工坊入口表现，但不删除精灵工坊。

目标形态：

```text
右下角
  Live2D 精灵
  状态气泡
  任务数量 / 失败提醒徽标

点击精灵
  展开现有精灵工坊 Drawer

精灵工坊
  任务
  记忆
  后续可加入设置、换装、调试入口
```

也就是说：

```text
精灵是入口。
精灵工坊是详情面板。
```

## 状态模型

建议第一版定义前端状态：

```ts
type ElfMood =
  | "idle"
  | "thinking"
  | "working"
  | "success"
  | "warning"
  | "error"
  | "talking";

interface ElfState {
  mood: ElfMood;
  message: string;
  source: "jobs" | "chat" | "memory" | "system";
  priority: number;
  jobId?: number;
  turnId?: number;
}
```

字段说明：

```text
mood
  精灵当前情绪 / 动作倾向。

message
  气泡展示文案。

source
  状态来源，第一版主要是 jobs。

priority
  状态优先级，用于多个状态同时出现时决定展示谁。

jobId
  可选，关联到具体任务。

turnId
  可选，后续关联到具体对话轮次。
```

优先级建议：

```text
error > warning > thinking / working > success > idle
```

示例：

```ts
{
  mood: "working",
  message: "我正在帮你整理刚保存的笔记。",
  source: "jobs",
  priority: 60,
  jobId: 12
}
```

## Jobs 状态映射

第一版从 jobs 入手，因为 jobs 已经接入 TanStack Query，状态稳定且全局可访问。

建议映射：

```text
存在 failed job
  mood: error
  message: 有任务失败了，点我看看哪里卡住了。
  priority: 100

存在 running job
  mood: working
  message: 我正在处理后台任务。
  priority: 70

存在 pending job
  mood: thinking
  message: 我排好队了，马上开始处理。
  priority: 60

最近有 completed job
  mood: success
  message: 刚刚有任务完成了。
  priority: 40

无任务或任务都安静
  mood: idle
  message: 我在这里，需要时可以点我。
  priority: 10
```

后续可以继续细分 job 类型：

```text
note_metadata
  我正在整理标题、摘要和标签。

note_embedding
  我正在把这条笔记放进记忆库。

conversation_memory
  我正在判断这轮对话里有没有值得长期记住的内容。
```

## 与现有 JobDrawer 的关系

当前 `JobDrawer` 同时承担：

```text
右下角入口
展开面板
任务列表
任务详情
Graph 展示
记忆管理
```

精灵接入后建议拆成：

```text
ElfAssistant
  右下角精灵、气泡、徽标、点击入口。

JobDrawer
  仍负责展开后的精灵工坊内容。
```

第一版可以保守改造：

```text
保留 JobDrawer 的面板内容。
把原来的 handle 按钮逐步替换为 ElfAssistant。
JobDrawer 暴露 isOpen / onToggle 或由父组件统一控制 open 状态。
```

这样不会破坏现有任务和记忆面板。

## 建议目录结构

```text
frontend/src/features/elf/
  ElfAssistant.tsx
  elfState.ts
  elfMessages.ts
  memoExpressionRenderer.tsx
  live2dAdapter.ts
  types.ts
```

职责说明：

```text
ElfAssistant.tsx
  React 组件入口，渲染精灵容器、状态气泡、徽标。

elfState.ts
  根据 jobs / chat / memory 等输入推导 ElfState。

elfMessages.ts
  管理不同状态下的中文提示文案。

memoExpressionRenderer.tsx
  当前 Memo 精灵的 PNG 表情渲染层，负责 mood -> image 的映射和图片预加载。

live2dAdapter.ts
  预留的 OhMyLive2D 包装层。当前 PNG 版本不主动初始化它，后续进入真正 Live2D 时再启用。

types.ts
  定义 ElfMood、ElfState、ElfAssistantProps 等类型。
```

## 当前 PNG 表情接入

资源位置：

```text
frontend/public/elf/memo/
  01_idle_soft.png
  02_thinking.png
  03_working_focus.png
  04_success_smile.png
  05_error_worried.png
  06_sleepy.png
  07_curious.png
  08_memory_glow.png
```

当前映射：

| ElfMood | PNG |
| --- | --- |
| idle | `01_idle_soft.png` |
| thinking | `02_thinking.png` |
| working | `03_working_focus.png` |
| success | `04_success_smile.png` |
| warning | `05_error_worried.png` |
| error | `05_error_worried.png` |
| talking | `07_curious.png` |

实现说明：

```text
ElfAssistant.tsx
  继续负责 jobs -> ElfState、拖拽、点击打开精灵工坊、任务徽标。

memoExpressionRenderer.tsx
  只负责表情图选择和预加载。

styles.css
  负责气泡、透明 PNG 容器、阴影和不同 mood 下的轻微姿态变化。
```

这个拆分是为了后续可以把 `memoExpressionRenderer.tsx` 替换为真正的 Live2D 渲染器，而不影响 jobs 状态推导和精灵工坊入口。

## 第一版实现计划

### Step 1：状态层

```text
新增 features/elf/types.ts
新增 features/elf/elfState.ts
根据 jobs 推导 ElfState
先不接 Live2D，只用普通占位容器测试状态
```

### Step 2：UI 层

```text
新增 ElfAssistant.tsx
渲染状态气泡
渲染任务数量 / 失败徽标
点击后触发 onToggleWorkshop
```

### Step 3：接入 JobDrawer

```text
把 JobDrawer 的 open 状态保留在 JobDrawer 内，或上提到 App。
用 ElfAssistant 替代原 job-drawer-handle。
JobDrawer 面板内容保持不变。
```

### Step 4：接入 OhMyLive2D

```text
安装并初始化 OhMyLive2D。
在 ElfAssistant 中挂载 Live2D 容器。
先使用默认模型或项目内配置的模型 URL。
保证卸载时清理实例，避免重复初始化。
```

### Step 5：体验细化

```text
根据 mood 调整气泡颜色和文案。
失败任务优先显示。
running/pending 任务显示数量。
idle 时降低气泡存在感，避免打扰用户。
```

## 后续扩展方向

### 接入 Chat Stream

后续可以让 ChatWindow 在 SSE 事件中更新共享状态：

```text
node: plan_retrieval
  我在判断要不要翻记忆。

node: build_l3_retrieved_memory
  我在翻你的笔记。

node: build_context
  我在组织上下文。

answer_delta
  我正在回答你。

done
  回答完成。
```

这一步建议先抽：

```text
useElfRuntime
ElfEventBus
或全局轻量 store
```

不要直接让 ChatWindow 和 ElfAssistant 强耦合。

### 接入 Memory 管理

后续 memory mutation 成功后可以提示：

```text
我帮你记住了一点。
这条记忆已经停用了。
这条记忆又回来了。
```

### 接入个性化设置

后续可以加入：

```text
开关精灵
切换模型
气泡显示频率
减少动效
静默模式
```

### 接入更底层 Live2D 控制

如果 OhMyLive2D 后续无法满足动作和表情需求，可以迁移：

```text
pixi-live2d-display
```

届时可以实现：

```text
不同 mood 播放不同 motion
点击头部 / 身体触发不同反馈
根据 job 类型切换表情
根据对话状态做口型或动作反馈
```

## 第一版成功标准

第一版完成后应满足：

```text
页面右下角能看到二次元精灵。
jobs 运行中时，精灵能提示正在处理。
jobs 失败时，精灵能提示有任务失败。
点击精灵能打开现有精灵工坊。
精灵状态来源于真实系统状态，而不是随机装饰文案。
不影响现有笔记、对话、任务、记忆功能。
```

## 风险与注意事项

```text
Live2D 资源体积可能较大，需要避免阻塞首屏。
第三方模型资源可能有版权限制，开源项目必须谨慎选择可分发模型。
精灵动效不能遮挡核心操作区域。
气泡文案不能过于频繁，否则会打扰用户。
如果用户关闭精灵，后台任务入口仍然要保留。
```

## 当前建议

我建议第一版先做：

```text
基于 jobs 的 ElfState 推导
ElfAssistant UI 占位版
点击打开现有 JobDrawer
再接 OhMyLive2D
```

这样可以先把架构关系理顺，再处理 Live2D 资源和第三方库接入细节。

## 已完成记录

### 2026-05-17：第一版精灵助手

已实现：

```text
安装 oh-my-live2d。
新增 frontend/src/features/elf/types.ts。
新增 frontend/src/features/elf/elfMessages.ts。
新增 frontend/src/features/elf/elfState.ts。
新增 frontend/src/features/elf/live2dAdapter.ts。
新增 frontend/src/features/elf/ElfAssistant.tsx。
JobDrawer 接入 ElfAssistant，并由精灵负责打开 / 收起精灵工坊。
精灵状态根据 jobs 推导 idle / thinking / working / success / error。
精灵气泡显示当前后台任务状态。
运行中任务显示数量徽标。
失败任务显示红色徽标并优先提示。
OhMyLive2D 通过动态 import 加载，避免进入首屏主包。
```

第一版状态来源：

```text
jobs
  pending
  running
  completed
  failed
```

暂未接入：

```text
ChatWindow stream 节点状态。
Memory mutation 成功提示。
精灵设置面板。
模型切换。
本地模型资源。
```

技术说明：

```text
live2dAdapter.ts 负责包装 OhMyLive2D。
ElfAssistant 不直接依赖第三方库细节，只调用 createLive2DElf。
OhMyLive2D 模型暂时使用项目作者备用远程模型 URL。
模型初始化前会先请求 model.json 做可用性预检查。
该远程模型仅适合作为开发阶段验证，开源发布前需要替换成版权明确、允许分发的模型资源。
```

验证：

```text
npm run build 通过。
构建后主包约 446KB，OhMyLive2D 被拆到异步 chunk，约 1000KB。
```

### 2026-05-17：Live2D 模型加载修复

问题：

```text
浏览器控制台出现 net::ERR_CONNECTION_CLOSED。
OhMyLive2D 一直显示精灵加载中。
Pixi 内部报 Cannot read properties of undefined (reading 'width')。
Pixi 内部报 Cannot read properties of null (reading 'transform')。
```

原因：

```text
原模型地址 https://model.oml2d.com/HK416-1-normal/model.json 在当前网络环境下 SSL 握手失败。
模型资源未完整加载时，OhMyLive2D / Pixi 仍继续进入尺寸计算和渲染流程，导致内部空对象异常。
```

修复：

```text
模型地址切换为 https://model.hacxy.cn/HK416-1-normal/model.json。
该地址已验证返回 200，并带有 Access-Control-Allow-Origin: *。
createLive2DElf 在导入 OhMyLive2D 前先 fetch model.json 做预检查。
预检查失败时直接进入精灵加载失败占位，不启动 Live2D runtime。
ElfAssistant 增加 loading / ready / failed 三态，避免永久显示加载中。
```

验证：

```text
npm run build 通过。
```
