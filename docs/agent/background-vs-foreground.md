# 前后台任务边界

本文定义 Local Operator 的前台执行与后台任务边界，避免 agent 把本应等待结果的命令误丢进后台池。

## 前台任务

前台任务是“这一轮对话内要拿到结果”的命令。

适用场景：
- `git status`
- `python --version`
- `pytest`
- `npm run build`
- 需要立刻返回 stdout / stderr / 退出码的短时命令

规则：
- 默认优先前台。
- 执行失败、超时、非 0 退出码都要直接回到 agent 重新规划。
- 如果用户要的是本轮结果，不能擅自改成后台。

## 后台任务

后台任务是“会持续存活、后续还要回来读”的命令。

适用场景：
- 本地服务：`uvicorn`、`flask run`、`npm run dev`、`python -m http.server`
- 长驻进程：需要持续监听端口、不断打印日志、用户后续会回来检查状态的任务

规则：
- 只有“持续运行”这一类任务才允许后台化。
- 不是“慢”就能后台化。
- 不是“还在进行”就能后台化。
- `exec_command_background` 必须返回 `task_id`，后续通过 `read_background_output` / `kill_background_task` 跟进。

## 判定原则

1. 目标是拿结果 -> 前台。
2. 目标是起服务 -> 后台。
3. 不确定 -> 默认前台。
4. 前台命令若命中长跑服务模式，策略层应拦截并提示改用后台。

## 反例

- `pip install` 通常应前台执行，除非你明确只想先起一个后台安装守望流程。
- `python app.py` 如果它是一次性脚本，前台；如果它是常驻服务入口，才考虑后台。
- 用户说“帮我运行这个任务并告诉我结果”，不能自动后台化。

## 相关位置

- `backend/app/local_operator/command.py`
- `backend/app/local_operator/background_command.py`
- `backend/app/local_operator/tools.py`
- `backend/app/agent/graphs/memory_chat/nodes.py`

