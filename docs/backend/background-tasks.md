# Background Tasks 后端

Background Tasks 子系统负责让 Local Operator Agent 拉起的后台进程（dev server、长跑脚本等）**独立于后端进程的生命周期**存活，并提供持久化、startup 回收、运行时管理。

## 设计原则

1. **关掉后端 ≠ 杀掉子进程**。Agent 帮用户起的服务必须独立存活，否则用户体验非常反直觉。
2. **重启后能"接管"老进程**。后端再次启动时通过 PID 探活，把之前的记录恢复到内存 pool；探活失败的标记为 `orphaned`，但记录依旧保留。
3. **输出可追溯**。stdout/stderr 都重定向到磁盘文件，前端/工具按行号增量读取。
4. **per-conversation 配额**。同一会话最多 5 个并发后台任务，避免误用造成进程爆炸。

## 涉及的代码

```text
backend/app/models/background_task.py            # SQLModel 表
backend/app/schemas/background_task.py           # API 契约
backend/app/local_operator/background_command.py # 核心：BackgroundShellPool / 进程生命周期
backend/app/services/background_task_service.py  # API 用的 service 层
backend/app/api/background_tasks.py              # REST 路由
backend/app/local_operator/tools.py              # list_background_tasks 工具
```

## 数据模型

`BackgroundTask`（SQLite 表，启动时自动建表）：

| 字段 | 说明 |
| --- | --- |
| `task_id` | `bg-<8 hex>` 短 ID，工具 / API 唯一引用 |
| `conversation_id` | 启动时所在会话；用于配额和列表过滤 |
| `command` / `cwd` | 原始命令字符串和工作目录 |
| `pid` | OS 进程号；探活用 |
| `status` | `running` / `exited` / `failed` / `killed` / `orphaned` / `unknown` |
| `exit_code` | 进程退出码 |
| `kill_reason` | 谁触发的 kill（`tool` / `api` / `pool_shutdown` 等） |
| `stdout_path` / `stderr_path` | `data/background_logs/<task_id>.{stdout,stderr}.log` |
| `started_at` / `finished_at` | UTC 时间戳 |

## 启动流程：`BackgroundShellPool.spawn`

1. 校验会话内活跃任务数 < `MAX_BACKGROUND_TASKS_PER_CONVERSATION (=5)`。
2. 在 `data/background_logs/` 下创建 stdout / stderr 两个日志文件，以**追加写文件描述符**形式打开。
3. `subprocess.Popen(command, stdout=fd, stderr=fd, …)` 启动进程。
4. **平台 detached 标志**：
   - Windows：`CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`，并显式 `close_fds=False`。
     **关键**：**不要**使用 `DETACHED_PROCESS` 标志。Windows 上一旦带 `DETACHED_PROCESS`，传给子进程的 stdio HANDLE 会被切断，日志文件永远是 0 字节。Windows 子进程在父进程死亡后默认就能继续存活，不需要额外的 Job Object 操作。
   - POSIX：`start_new_session=True`，让子进程成为新的会话首进程。
5. 写入 `BackgroundTask` 记录（`status = running`）。
6. 启动一个 daemon watcher 线程，阻塞在 `proc.wait()`，子进程退出后负责：
   - 更新内存中的 `BackgroundShellTask`；
   - 关闭日志文件 FD；
   - 把状态写回数据库（`exited` / `failed`）。

DB 写入全部包在 `try/except` 里，确保测试中没准备好 isolated DB 时不会炸。

## 启动时回收：`adopt_persisted_tasks`

`app.main` 的 startup hook 调用此方法：

1. 从 DB 查所有 `status == "running"` 的记录。
2. 对每个 PID 做跨平台探活：
   - Windows：`ctypes.windll.kernel32.OpenProcess + GetExitCodeProcess`，`STILL_ACTIVE == 259`。
   - POSIX：`os.kill(pid, 0)`。
3. 探活成功 → 在内存 pool 中重建一个 `BackgroundShellTask`（`proc = None`，无 Popen 句柄），状态保持 `running`。这种"被收养"的任务依然能通过 PID 进行 kill。
4. 探活失败 → 把记录置为 `orphaned`、写 `finished_at`，留在数据库供 UI 显示。

启动日志会打印 `[background_shell] adopted N, orphaned M`。

## 启动时清理：`cleanup_finished_tasks`

紧跟在 `adopt_persisted_tasks` 之后，`app.main` 的 startup hook 会再调用
`cleanup_finished_tasks`：

1. 扫描 DB 中所有 `status` 属于 `exited / failed / killed / orphaned / unknown` 的记录。
2. 逐条删除其 `stdout_path` / `stderr_path` 指向的日志文件（best-effort，文件被占用或
   已被外部删除不会抛错）。
3. 删除 DB 行，并把残留在内存池中的同名任务一并移除。
4. 仍在运行的任务（status=running）保持不动。

启动日志会打印 `[background_shell] cleaned N finished tasks, deleted M log files`。

这一步避免了 UI 列表里长期堆着上次留下的 `exited / killed / orphaned` 历史记录，
也避免 `./data/background_logs/` 无限增长。如需保留所有历史记录用于审计，可通过
环境变量 `BACKGROUND_TASK_CLEANUP_ON_STARTUP=false` 关闭。

## 关闭时不杀进程：`shutdown_all`

`on_shutdown` 调用 `shutdown_all`，但此方法**只关闭日志文件 FD**，**不**调用 `proc.kill()`、不发任何信号。
子进程因为是 detached 起的，会继续在 OS 里运行。下次启动时由 `adopt_persisted_tasks` 接管。

## 终止：`kill`

- 有 Popen 句柄（本次会话起的，或上一轮 watcher 还活着）：调 `_kill_pid_tree(pid)`。
- 仅有 PID（收养来的）：同样走 `_kill_pid_tree`。
- 跨平台实现：
  - Windows：`taskkill /F /T /PID <pid>`。
  - POSIX：`os.killpg(os.getpgid(pid), SIGTERM)`，3 秒后兜底 `SIGKILL`。

## 日志读取：`get_output`

- 单次最多扫描 `MAX_LOG_BYTES_TO_SCAN = 4 MiB` 的 tail。
- `stdout` 和 `stderr` 两个文件按行交叉合并、按时间近似排序，输出全局行号。
- 包含 ANSI 转义码会被剥离。
- 单次返回 `max_lines` 行（默认 50，上限 200）。

## 配额和常量

```python
MAX_BACKGROUND_TASKS_PER_CONVERSATION = 5
MAX_LOG_BYTES_TO_SCAN = 4 * 1024 * 1024
DEFAULT_BACKGROUND_LINES_RETURNED = 50
MAX_BACKGROUND_LINES_RETURNED = 200
```

## 测试

```text
backend/tests/test_background_task_persistence.py   # 8 个用例
backend/tests/test_command_background_and_policy.py # 含"关闭不杀子进程"用例
backend/tests/test_app_routes.py                    # /api/background_tasks 路由挂载断言
```

`isolated_db` fixture 通过 monkeypatch 替换 `core_db.engine` 和 `session_scope`，把数据库切到 `tmp_path`，再 chdir 到 tmp_path，让 `data/background_logs/` 也落在临时目录。

## 相关

- [Background Tasks API](../api/background-tasks.md)
- [Background Tasks 抽屉（前端）](../frontend/background-tasks-drawer.md)
- [Local Operator Agent](../agent/local-operator-agent.md#后台命令工具)
