# 后台任务抽屉（Background Tasks Drawer）

后台任务抽屉是右侧的全局抽屉组件，让用户能查看和管理 Local Operator Agent 启动的所有后台进程。

## 设计目标

来自用户的明确需求：

> 后端关闭不能把 agent 帮我起的服务也关掉。
> 我们需要一个展示栏，**默认不展示**，用户主动查阅；同时用户能管理这些任务。

因此抽屉：

- 默认收起，挂在屏幕右侧，竖排"后台任务"把手始终可见。
- 把手上有运行中任务数量的徽章，方便一眼看到有没有任务在跑。
- 完全独立于 LangGraph Job Drawer（左侧是对话列表，右侧空闲，复用 `.job-drawer` 那套右侧滑入模板）。

## 文件结构

```text
frontend/src/features/background_tasks/
  types.ts                     # 状态枚举 / API DTO
  backgroundTasksApi.ts        # fetch 封装
  BackgroundTasksDrawer.tsx    # 抽屉 + 列表 + 详情
```

挂载点：`frontend/src/app/AppShell.tsx`，渲染在 `<Outlet />` 之后，作为全局 fixed 元素。

## 交互

- 把手位于屏幕右缘，竖排文字"后台任务" + Server 图标 + 运行中数字徽章。
- 点击把手 → 抽屉滑入；再点 / 点 PanelHeader 的"收起"按钮 → 滑出。
- 列表：每行一个任务，左侧主按钮（状态徽章 + 命令文本 + PID / 启动时间 / exit_code 元信息），右侧"终止/移除"按钮。
- 选中某行后，下半区显示任务详情。
- 点击"查看命令行"展开实时日志；再次点击可收起。运行中的任务会持续轮询输出，避免只显示静态快照。

## 轮询节奏

- 列表 `useQuery(["background_tasks"])`：
  - 抽屉打开且**有任务运行中** → 3 秒一次；
  - 抽屉打开但没运行中任务 → 10 秒一次；
  - 抽屉收起 → 仍以 10 秒一次轮询，目的是让把手上的徽章保持新鲜。
- 详情输出 `useQuery(["background_task_output", id])`：
  - 任务运行中 → 2 秒一次；
  - 任务已结束 → 关闭轮询。
- `killBackgroundTask` / `pruneBackgroundTask` 调用后 `queryClient.invalidateQueries`，立即拉取最新状态。

## 状态徽章配色

`.bg-task-status--*` 一一对应 `BackgroundTaskStatus`：

| status | 颜色 |
| --- | --- |
| `running` | 绿色（#d1fae5 / #047857） |
| `exited` | 灰色 |
| `failed` | 红色 |
| `killed` | 琥珀 |
| `orphaned` | 紫色 |
| `unknown` | 石板灰 |

抽屉自身用青色（teal `#14b8a6`）作为强调色，跟之前的橙色 `.job-drawer` 区分。

## CSS 注意事项

- `.bg-task-drawer` 是 `position: fixed; right: 0; top: 20px; bottom: 20px;` 加 `transform: translateX(440px)`，开关靠 `.open` 切换。
- 把手用 `position: absolute; right: 420px;`（贴在面板左缘），**不要**让 handle 进 flex 流，否则会把面板挤出可视区。
- 面板内部用 `display: flex; flex-direction: column;`，因为 `error` 块是条件渲染——用 grid 会让网格行错位。

## 相关

- [Background Tasks API](../api/background-tasks.md)
- [Background Tasks 后端](../backend/background-tasks.md)
- [Local Operator Agent](../agent/local-operator-agent.md#后台命令工具)
