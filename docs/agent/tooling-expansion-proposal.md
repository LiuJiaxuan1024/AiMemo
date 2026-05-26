# Agent 工具扩展提案

本文记录当前 AiMemo / Memo Elf 的工具分层，以及为了让 agent 更稳定完成任务，建议补充的工具与节点语义。

## 现状

当前已存在的本地操作工具，核心是三类原子能力：

- `read_*`：读取信息
- `write_*`：写入或修改信息
- `exec_*`：执行命令

这套能力足以完成很多任务，但对 agent 来说还不够“可规划”。原因是：

- `write` 不知道自己为什么写，只知道写什么。
- `exec` 不知道自己是在验证任务、启动服务，还是做一次性命令。
- `final_answer` 容易抢走“继续问用户”或“继续执行”的角色。

所以接下来需要把“工具”分成两层：

1. 原子执行工具
2. 任务控制与交互工具

## 已有原子工具

```text
list_dir
read_file
search_files
search_text
get_file_info
write_file
exec_command
exec_command_background
read_background_output
kill_background_task
list_background_tasks
```

这些工具解决的是“怎么做”。

## 建议补充的任务控制工具

### 1. `request_user_input`

用途：

- 路径不明确时询问目录
- 多方案决策时给用户选项卡
- 风险操作前请求确认
- 外置精灵对话里的分支选择

期望效果：

- 不是普通文本问答
- 由运行时强制中断
- 前端或桌面精灵渲染成结构化选项

### 2. `plan_task`

用途：

- 把用户自然语言转成显式执行计划
- 生成 `Task` / `Step` 列表
- 标记每步依赖、目标、工具类型

建议输出字段：

- `task_id`
- `goal`
- `steps`
- `assumptions`
- `acceptance_criteria`

### 3. `observe_tool_result`

用途：

- 把工具返回值吸收进 task/world state
- 让 agent 明确知道上一步是否真的成功
- 记录 stdout/stderr、错误码、截断标记、阻塞原因

它解决的是“我执行了什么，世界现在变成什么样”。

### 4. `verify_goal`

用途：

- 判断当前任务是否真正完成
- 区分“工具成功”与“目标成功”
- 防止 `exit_code=0` 被误判为任务结束

适合检查的内容：

- 文件是否真的存在
- 写入内容是否符合要求
- 服务是否真的启动
- 命令输出是否满足目标

### 5. `replan_step`

用途：

- 当前步骤失败后重新生成后续步骤
- 根据 tool observation 修正计划
- 避免原样重试同一个失败动作

建议触发条件：

- `exec_command` 非 0
- `read_file`/`write_file` 被策略拒绝
- `read_background_output` 显示环境缺失
- `request_user_input` 返回新的用户选择

## 建议的任务语义层

如果要继续增强 agent 的稳定性，建议在图里显式保留这些语义对象：

```text
Task
  任务目标与完成条件

Step
  单个可执行步骤

WorldState
  文件、命令输出、服务状态、已知事实

WorldStatus
  当前任务阶段与待执行/已完成/失败状态

ExecutionHistory
  每次工具调用的时间线
```

这些不是“工具”本身，但它们决定工具怎么用。

## 推荐优先级

第一优先级：

- `request_user_input`
- `plan_task`
- `observe_tool_result`

第二优先级：

- `verify_goal`
- `replan_step`

第三优先级：

- 更细粒度的只读工具，如 `glob_files`、`read_lines`
- 更细粒度的编辑工具，如 `apply_patch`、`replace_text`

## 结论

当前工具集不算少，但“决策层工具”还不够。
真正需要补的不是更多低层 API，而是：

- 让 agent 会问
- 让 agent 会规划
- 让 agent 会验收
- 让 agent 会重规划

这几项补齐后，`read / write / exec` 才能真正变成可控的 agent 能力。

## 第一版落地状态

当前已在 Memory Chat 主图中补入第一版任务控制节点：

```text
merge_prompt_context
  -> plan_task
  -> agent
  -> tools
  -> observe_tool_result
  -> verify_goal
  -> agent
```

第一版实现目标：

- `plan_task`：为本轮建立轻量 `task`，包含 goal、steps、acceptance_criteria。
- `observe_tool_result`：把 `tool_observations` 归纳进 `world_state`。
- `verify_goal`：记录当前进展是否需要重规划，并把 `verification` 注入下一轮 agent 输入。

仍待增强：

- 用 LLM 或规则混合 verifier 判断目标是否真的完成。
- 让 `replan_step` 成为独立节点，而不是先由下一轮 agent 根据 `verification` 自行调整。
- 增加更细粒度的 edit/patch 工具。
