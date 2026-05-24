# Background Tasks API

Background Tasks API 用于查询和管理 Local Operator Agent 启动的后台进程（如本地 dev server、长跑脚本）。

这些任务和 `Jobs API`（LangGraph job 元数据）是不同的概念：

- **Jobs**：后端内部的 LangGraph 流程实例，反映 `note_metadata_graph` 等图的运行状态。
- **Background Tasks**：通过 `run_command_background` 工具拉起的真实操作系统进程，进程在后端关闭后仍会继续运行（详见 [Background Tasks 后端](../backend/background-tasks.md)）。

所有路由挂在 `/api/background_tasks` 前缀下。

## 任务列表

```text
GET /api/background_tasks
```

查询参数：

```text
conversation_id=<int>   # 可选，按会话过滤
include_finished=true   # 是否返回已结束/已终止/已孤立任务，默认 true
limit=100               # 1..500，默认 100
```

返回数组，每一项 `BackgroundTaskRead`：

```json
{
  "task_id": "bg-abc12345",
  "conversation_id": 31,
  "command": "python app.py",
  "cwd": "E:/demo",
  "pid": 18204,
  "status": "running",
  "exit_code": null,
  "kill_reason": "",
  "started_at": "2026-05-24T10:30:00Z",
  "finished_at": null
}
```

### 状态枚举

| status | 含义 |
| --- | --- |
| `running` | 进程仍在运行（pool 持有 Popen 句柄） |
| `exited` | 进程自然退出，`exit_code == 0` |
| `failed` | 进程退出，`exit_code != 0` |
| `killed` | 通过 `kill` 接口或工具显式终止 |
| `orphaned` | 后端启动时发现 DB 中记录的 PID 已不存在（之前可能被 OS 或用户外部杀掉） |
| `unknown` | adopt 期间无法判定状态（极少见） |

## 任务详情

```text
GET /api/background_tasks/{task_id}
```

返回单个 `BackgroundTaskRead`。`task_id` 不存在时返回 `404`。

## 任务输出（增量日志）

```text
GET /api/background_tasks/{task_id}/output
```

查询参数：

```text
since_line=0     # 从第几行开始返回（含），默认 0
max_lines=50     # 1..200，默认 50
```

后端会把 `stdout` 和 `stderr` 日志文件按时间归并、按全局行号编号后返回：

```json
{
  "task_id": "bg-abc12345",
  "status": "running",
  "pid": 18204,
  "exit_code": null,
  "lines": [
    { "line": 1, "stream": "stdout", "text": " * Serving Flask app 'app'" },
    { "line": 2, "stream": "stdout", "text": " * Running on http://127.0.0.1:5000" },
    { "line": 3, "stream": "stderr", "text": "Press CTRL+C to quit" }
  ],
  "last_line": 3,
  "dropped_lines": 0,
  "more": false
}
```

- `last_line`：本次返回的最大行号，前端下次轮询时把它传回 `since_line` 即可拿到增量。
- `dropped_lines`：日志文件 tail 扫描时丢弃的过老行数（默认只扫最后 4 MiB）。
- `more`：日志文件中还有未读完的行（受 `max_lines` 限制）。

## 终止任务

```text
POST /api/background_tasks/{task_id}/kill
```

无请求体。返回更新后的 `BackgroundTaskRead`（status 通常变为 `killed`，`kill_reason` 写入触发来源）。

后端处理：

- 若 pool 中还持有 Popen 句柄：调用对应平台的进程树终止（Windows `taskkill /F /T /PID`，POSIX `os.killpg`）。
- 若是 `orphaned` 或 adopt 进来的进程：通过 PID 走相同的进程树终止逻辑。
- 已结束任务调用此接口会原样返回当前状态（不报错）。

## 移除记录

```text
DELETE /api/background_tasks/{task_id}
```

返回 `204 No Content`。

- 仅允许删除非 `running` 的任务（仍在跑的任务会返回 `409`，需要先 `kill`）。
- 同时从内存 pool 和数据库中移除该记录。日志文件保留在 `data/background_logs/` 不自动清理。

## 相关

- [Background Tasks 后端实现](../backend/background-tasks.md)
- [Background Tasks 抽屉（前端）](../frontend/background-tasks-drawer.md)
- [Local Operator Agent](../agent/local-operator-agent.md#后台命令工具)
