from copy import deepcopy

from app.agent.graphs.memory_chat.state import (
    AgentTaskPayload,
    AgentToolObservationPayload,
    AgentWorldStatePayload,
    MemoryChatGraphState,
    RemoteTaskSessionPayload,
)


def _nodes_facade():
    from app.agent.graphs.memory_chat import nodes as nodes_facade

    return nodes_facade


def _thought(*args, **kwargs):
    return _nodes_facade()._thought(*args, **kwargs)


def _summarize_tool_observation(observation: AgentToolObservationPayload) -> str:
    return _nodes_facade()._summarize_tool_observation(observation)


def _resolve_user_message(state: MemoryChatGraphState) -> str:
    user_message = state.get("user_message", "").strip()
    if not user_message:
        raise ValueError("user_message is required.")
    return user_message


def build_plan_task_node():
    """把本轮用户输入显式化成 task，供后续工具循环持续引用。

    这里先做确定性轻量计划，不额外调用 LLM。真正的工具选择仍由 ReAct agent 完成；
    这个节点的价值是给 checkpoint/debug 和后续 verify/replan 一个稳定任务对象。
    """

    def plan_task(state: MemoryChatGraphState) -> MemoryChatGraphState:
        user_message = _resolve_user_message(state).strip()
        task_id = f"turn-{state.get('user_message_id') or state.get('conversation_id') or 'current'}"
        task: AgentTaskPayload = {
            "id": task_id,
            "goal": user_message,
            "status": "running",
            "current_step_id": "step-1",
            "steps": [
                {
                    "id": "step-1",
                    "description": _classify_initial_step_description(user_message),
                    "status": "pending",
                    "tool_name": "",
                    "arguments": {},
                    "result_summary": "",
                    "retry_count": 0,
                }
            ],
            "acceptance_criteria": _infer_acceptance_criteria(user_message),
            "assumptions": [],
        }
        update: MemoryChatGraphState = {
            "task": task,
            "world_state": _empty_world_state(),
            "verification": {"status": "pending", "reason": "task planned"},
            "replan_required": False,
            "thought_events": [
                *state.get("thought_events", []),
                _thought(
                    "plan-task",
                    "规划任务",
                    f"目标：{user_message[:120]}",
                    related_node="plan_task",
                    step_index=0,
                ),
            ],
        }
        if _looks_like_remote_task(user_message):
            update["remote_task_session"] = _empty_remote_task_session(f"{task_id}-remote", goal=user_message)
        return update

    return plan_task


def build_observe_tool_result_node():
    """把工具结果吸收进 task/world state。

    tools 节点负责执行；observe 节点负责把结果变成 agent 可持续利用的世界状态。
    """

    def observe_tool_result(state: MemoryChatGraphState) -> MemoryChatGraphState:
        observations = list(state.get("tool_observations") or [])
        world_state = _world_state_from_observations(observations)
        task = _task_with_latest_observation(state.get("task") or {}, observations)
        latest = observations[-1] if observations else {}
        latest_summary = _summarize_tool_observation(latest) if latest else "本轮还没有工具结果。"
        update: MemoryChatGraphState = {
            "world_state": world_state,
            "task": task,
            "thought_events": [
                *state.get("thought_events", []),
                _thought(
                    "observe-tool-result",
                    "吸收工具结果",
                    latest_summary,
                    related_node="observe_tool_result",
                    related_tool_call_id=str(latest.get("tool_call_id") or "") or None,
                    step_index=int(state.get("agent_step_index") or 0),
                ),
            ],
        }
        if state.get("remote_task_session") or _observations_include_remote_tool(observations):
            update["remote_task_session"] = _remote_task_session_from_observations(
                state.get("remote_task_session") or _empty_remote_task_session(
                    f"{(state.get('task') or {}).get('id') or 'turn-current'}-remote",
                    goal=str((state.get("task") or {}).get("goal") or state.get("user_message") or ""),
                ),
                observations,
            )
        return update

    return observe_tool_result


def build_verify_goal_node():
    """基于工具事实做一层轻量验收，防止“工具成功 == 任务成功”。

    第一版只做确定性检查：失败工具会要求 agent 重新规划；没有失败时把状态标为
    ready_for_agent，让下一次 agent 调用基于 ToolMessage 决定继续还是最终回答。
    """

    def verify_goal(state: MemoryChatGraphState) -> MemoryChatGraphState:
        observations = list(state.get("tool_observations") or [])
        latest = observations[-1] if observations else {}
        failed = [obs for obs in observations if not bool(obs.get("ok"))]
        verification = {
            "status": "needs_replan" if latest and not bool(latest.get("ok")) else "ready_for_agent",
            "reason": _verification_reason(state, latest),
            "observation_count": len(observations),
            "failure_count": len(failed),
        }
        remote_session = dict(state.get("remote_task_session") or {})
        if remote_session:
            verification["remote_task_session"] = remote_session
            if remote_session.get("status") == "blocked":
                verification["status"] = "needs_user_input"
                verification["reason"] = (
                    "远程任务已阻塞："
                    f"{remote_session.get('blocked_reason') or '缺少远程目标、认证或路径信息'}。"
                    "下一轮 agent 必须调用 request_user_input 收集恢复方案，不要继续原样重试。"
                )
            elif remote_session.get("status") == "completed":
                verification["status"] = "ready_for_final"
                verification["reason"] = "远程任务上传/执行/验证链路已完成，可以基于真实工具结果总结。"
        task = dict(state.get("task") or {})
        if latest and not bool(latest.get("ok")):
            task["status"] = "running"
        elif observations:
            task["status"] = "running"
        return {
            "verification": verification,
            "task": task,
            "replan_required": verification["status"] == "needs_replan",
            "thought_events": [
                *state.get("thought_events", []),
                _thought(
                    "verify-goal",
                    "验收当前进展",
                    str(verification["reason"]),
                    related_node="verify_goal",
                    step_index=int(state.get("agent_step_index") or 0),
                ),
            ],
        }

    return verify_goal


def _empty_world_state() -> AgentWorldStatePayload:
    return {
        "known_paths": {},
        "command_results": [],
        "background_tasks": [],
        "observations": [],
        "failures": [],
    }


REMOTE_TOOL_NAMES = {
    "remote_connectivity_check",
    "remote_upload_file",
    "remote_exec",
    "remote_verify_http",
}
REMOTE_PHASES: list[tuple[str, str]] = [
    ("collect_target", "收集远程目标"),
    ("collect_auth", "确认认证能力"),
    ("prepare_artifact", "准备本地/远程产物"),
    ("transfer", "传输文件"),
    ("remote_apply", "远程应用变更"),
    ("verify", "验证远程结果"),
]


def _looks_like_remote_task(text: str) -> bool:
    lowered = text.lower()
    remote_tokens = ["远程", "服务器", "ssh", "scp", "sftp", "nginx", "部署", "上传", "传到", "登录服务器"]
    return any(token in lowered for token in remote_tokens)


def _empty_remote_task_session(session_id: str, *, goal: str = "") -> RemoteTaskSessionPayload:
    return {
        "id": session_id,
        "status": "collecting_target",
        "current_phase": "collect_target",
        "target": {"goal": goal},
        "auth": {"method": "ssh_key_or_agent", "status": "unknown"},
        "artifacts": {},
        "phases": [
            {
                "id": phase_id,
                "label": label,
                "status": "pending",
                "tool_name": "",
                "summary": "",
                "error_code": "",
            }
            for phase_id, label in REMOTE_PHASES
        ],
        "blocked_reason": "",
        "next_actions": [],
    }


def _classify_initial_step_description(user_message: str) -> str:
    text = user_message.lower()
    if _looks_like_remote_task(user_message):
        return "确认远程目标、认证方式并按上传/执行/验证闭环推进"
    if any(token in text for token in ["创建", "新建", "写入", "保存", "write"]):
        return "确认目标与路径后写入文件"
    if any(token in text for token in ["运行", "执行", "编译", "测试", "run", "test", "build"]):
        return "执行命令并检查结果"
    if any(token in text for token in ["读取", "查看", "搜索", "列出", "read", "list", "search"]):
        return "读取本地信息并回答"
    return "理解用户目标并选择下一步行动"


def _infer_acceptance_criteria(user_message: str) -> list[str]:
    criteria = ["最终回答必须基于真实上下文或工具结果。"]
    text = user_message.lower()
    remote_tokens = ["远程", "服务器", "ssh", "scp", "sftp", "nginx", "部署", "上传", "传到", "登录服务器"]
    if any(token in text for token in ["创建", "新建", "写入", "保存", "write"]):
        criteria.append("如果需要落地文件，必须存在成功 write_file observation。")
    if any(token in text for token in ["运行", "执行", "编译", "测试", "run", "test", "build"]):
        criteria.append("如果需要运行结果，必须引用成功或失败的 exec/background observation。")
    if any(token in text for token in ["读取", "查看", "搜索", "列出", "read", "list", "search"]):
        criteria.append("如果回答本地文件内容，必须存在成功 read/list/search observation。")
    if any(token in text for token in remote_tokens):
        criteria.append("远程服务器操作必须使用 remote_* 工具，不能用 exec_command 直接执行 ssh/scp。")
        criteria.append("远程修改或部署完成前，必须存在成功 remote_upload_file/remote_exec，并通过 remote_exec 或 remote_verify_http 验证。")
        criteria.append("缺少远程 host、username、路径或认证方式时，必须调用 request_user_input。")
    if any(token in text for token in ["目录", "路径", "放在哪", "创建一个", "新建一个"]):
        criteria.append("缺少目标路径或存在多个合理选择时，必须调用 request_user_input。")
    return criteria


def _world_state_from_observations(observations: list[AgentToolObservationPayload]) -> AgentWorldStatePayload:
    world = _empty_world_state()
    for observation in observations:
        data = dict(observation.get("data") or {})
        tool_name = str(observation.get("tool_name") or "")
        compact = {
            "tool_call_id": observation.get("tool_call_id", ""),
            "tool_name": tool_name,
            "ok": bool(observation.get("ok")),
            "error_code": observation.get("error_code", ""),
            "message": observation.get("message", ""),
            "data": _compact_observation_data_for_world(data),
        }
        world["observations"].append(compact)
        if not observation.get("ok"):
            world["failures"].append(compact)
        path = str(data.get("path") or data.get("relative_path") or "")
        if path:
            world["known_paths"][path] = {
                "tool_name": tool_name,
                "ok": bool(observation.get("ok")),
                "exists": data.get("exists", True),
                "size": data.get("size"),
                "modified_at": data.get("modified_at", ""),
            }
        if tool_name == "exec_command":
            world["command_results"].append(
                {
                    "command": data.get("command", ""),
                    "cwd": data.get("cwd", ""),
                    "exit_code": data.get("exit_code"),
                    "ok": bool(observation.get("ok")),
                    "timed_out": data.get("timed_out", False),
                    "stdout_preview": str(data.get("stdout") or "")[:500],
                    "stderr_preview": str(data.get("stderr") or "")[:500],
                }
            )
        if tool_name.startswith("remote_"):
            world["command_results"].append(
                {
                    "tool_name": tool_name,
                    "host": data.get("host", ""),
                    "username": data.get("username", ""),
                    "remote_path": data.get("remote_path", ""),
                    "remote_command": data.get("remote_command", ""),
                    "url": data.get("url", ""),
                    "exit_code": data.get("exit_code"),
                    "ok": bool(observation.get("ok")),
                    "timed_out": data.get("timed_out", False),
                    "stdout_preview": str(data.get("stdout") or data.get("response_preview") or "")[:500],
                    "stderr_preview": str(data.get("stderr") or "")[:500],
                }
            )
        if tool_name in {"exec_command_background", "read_background_output", "kill_background_task", "list_background_tasks"}:
            task_id = str(data.get("task_id") or "")
            if task_id:
                world["background_tasks"].append(
                    {
                        "task_id": task_id,
                        "status": data.get("status", ""),
                        "command": data.get("command", ""),
                        "ok": bool(observation.get("ok")),
                    }
                )
    return world


def _observations_include_remote_tool(observations: list[AgentToolObservationPayload]) -> bool:
    return any(str(obs.get("tool_name") or "") in REMOTE_TOOL_NAMES for obs in observations)


def _remote_task_session_from_observations(
    session: RemoteTaskSessionPayload,
    observations: list[AgentToolObservationPayload],
) -> RemoteTaskSessionPayload:
    updated: RemoteTaskSessionPayload = deepcopy(dict(session))
    phases = _remote_phase_map(updated.get("phases") or [])
    target = dict(updated.get("target") or {})
    auth = dict(updated.get("auth") or {"method": "ssh_key_or_agent", "status": "unknown"})
    artifacts = dict(updated.get("artifacts") or {})
    blocked_reason = ""
    next_actions: list[str] = []

    for observation in observations:
        tool_name = str(observation.get("tool_name") or "")
        if tool_name not in REMOTE_TOOL_NAMES:
            if tool_name == "write_file" and observation.get("ok"):
                data = dict(observation.get("data") or {})
                artifacts["local_path"] = data.get("path") or data.get("relative_path") or artifacts.get("local_path", "")
                _mark_remote_phase(
                    phases,
                    "prepare_artifact",
                    "completed",
                    tool_name=tool_name,
                    summary=f"本地产物已准备：{artifacts.get('local_path') or 'unknown'}",
                )
            continue

        data = dict(observation.get("data") or {})
        args = dict(observation.get("arguments") or {})
        _merge_remote_target(target, data)
        _merge_remote_target(target, args)
        if data.get("local_path") or args.get("local_path"):
            artifacts["local_path"] = data.get("local_path") or args.get("local_path")
        if data.get("remote_path") or args.get("remote_path"):
            target["remote_path"] = data.get("remote_path") or args.get("remote_path")
        if data.get("url") or args.get("url"):
            target["url"] = data.get("url") or args.get("url")

        ok = bool(observation.get("ok"))
        error_code = str(observation.get("error_code") or "")
        message = str(observation.get("message") or "")
        phase_id = _remote_phase_for_tool(tool_name)
        phase_status = "completed" if ok else ("blocked" if bool(observation.get("blocked")) else "failed")
        _mark_remote_phase(
            phases,
            phase_id,
            phase_status,
            tool_name=tool_name,
            summary=_summarize_tool_observation(observation),
            error_code=error_code,
        )

        if tool_name == "remote_connectivity_check":
            auth["status"] = "ready" if ok else "blocked" if observation.get("blocked") else "failed"
            auth["error_code"] = error_code
        elif tool_name == "remote_upload_file" and ok:
            _mark_remote_phase(
                phases,
                "prepare_artifact",
                "completed",
                tool_name=tool_name,
                summary="上传已使用本地产物，产物准备完成。",
            )
        elif tool_name == "remote_verify_http" and ok:
            target["verified_url"] = data.get("url") or args.get("url") or ""

        if not ok and (observation.get("blocked") or error_code in _remote_blocking_error_codes()):
            blocked_reason = f"{error_code} {message}".strip()
            next_actions = _remote_next_actions_for_error(error_code)

    if target.get("host") and target.get("username"):
        _mark_remote_phase(
            phases,
            "collect_target",
            "completed",
            tool_name="remote_task_session",
            summary=f"远程目标已确认：{target.get('username')}@{target.get('host')}:{target.get('port') or 22}",
        )
    if phases["transfer"].get("status") == "completed" and phases["prepare_artifact"].get("status") == "pending":
        _mark_remote_phase(
            phases,
            "prepare_artifact",
            "completed",
            tool_name="remote_upload_file",
            summary="上传已使用本地产物，产物准备完成。",
        )
    if phases["verify"].get("status") == "completed" and phases["remote_apply"].get("status") == "pending":
        _mark_remote_phase(
            phases,
            "remote_apply",
            "skipped",
            tool_name="remote_task_session",
            summary="本次任务通过上传后 HTTP 验证闭环，无需额外远程执行。",
        )

    ordered_phases = [phases[phase_id] for phase_id, _label in REMOTE_PHASES]
    current_phase = _first_unfinished_remote_phase(ordered_phases)
    status = _remote_session_status(ordered_phases, blocked_reason=blocked_reason, target=target)
    updated.update(
        {
            "target": target,
            "auth": auth,
            "artifacts": artifacts,
            "phases": ordered_phases,
            "current_phase": current_phase,
            "status": status,
            "blocked_reason": blocked_reason,
            "next_actions": next_actions,
        }
    )
    return updated


def _remote_phase_map(phases: list[dict]) -> dict[str, dict]:
    by_id = {str(phase.get("id") or ""): dict(phase) for phase in phases if phase.get("id")}
    for phase_id, label in REMOTE_PHASES:
        by_id.setdefault(
            phase_id,
            {
                "id": phase_id,
                "label": label,
                "status": "pending",
                "tool_name": "",
                "summary": "",
                "error_code": "",
            },
        )
    return by_id


def _merge_remote_target(target: dict, source: dict) -> None:
    for key in ["host", "username", "port", "remote_path", "url"]:
        value = source.get(key)
        if value not in [None, ""]:
            target[key] = value


def _remote_phase_for_tool(tool_name: str) -> str:
    mapping = {
        "remote_connectivity_check": "collect_auth",
        "remote_upload_file": "transfer",
        "remote_exec": "remote_apply",
        "remote_verify_http": "verify",
    }
    return mapping.get(tool_name, "collect_target")


def _mark_remote_phase(
    phases: dict[str, dict],
    phase_id: str,
    status: str,
    *,
    tool_name: str,
    summary: str,
    error_code: str = "",
) -> None:
    phase = dict(phases.get(phase_id) or {})
    phase.update(
        {
            "id": phase_id,
            "label": phase.get("label") or dict(REMOTE_PHASES).get(phase_id, phase_id),
            "status": status,
            "tool_name": tool_name,
            "summary": summary,
            "error_code": error_code,
        }
    )
    phases[phase_id] = phase


def _remote_blocking_error_codes() -> set[str]:
    return {
        "INTERACTIVE_AUTH_REQUIRED",
        "LOCAL_SSH_NOT_FOUND",
        "LOCAL_SCP_NOT_FOUND",
        "INVALID_REMOTE_HOST",
        "INVALID_REMOTE_USER",
        "REMOTE_PATH_NOT_ABSOLUTE",
        "REMOTE_COMMAND_BLOCKED",
        "IDENTITY_FILE_OUTSIDE_WORKSPACE",
        "IDENTITY_FILE_NOT_FOUND",
    }


def _remote_next_actions_for_error(error_code: str) -> list[str]:
    mapping = {
        "INTERACTIVE_AUTH_REQUIRED": ["configure_ssh_key", "use_existing_ssh_agent", "manual_remote_command"],
        "LOCAL_SSH_NOT_FOUND": ["install_openssh_client", "configure_ssh_path", "manual_remote_command"],
        "LOCAL_SCP_NOT_FOUND": ["install_openssh_client", "configure_scp_path", "manual_remote_command"],
        "INVALID_REMOTE_HOST": ["request_remote_host"],
        "INVALID_REMOTE_USER": ["request_remote_username"],
        "REMOTE_PATH_NOT_ABSOLUTE": ["request_absolute_remote_path"],
        "REMOTE_COMMAND_BLOCKED": ["request_safe_remote_command_or_manual_step"],
        "IDENTITY_FILE_OUTSIDE_WORKSPACE": ["request_authorized_identity_file", "use_existing_ssh_agent"],
        "IDENTITY_FILE_NOT_FOUND": ["request_existing_identity_file", "use_existing_ssh_agent"],
    }
    return mapping.get(error_code, ["inspect_remote_error", "request_user_decision"])


def _first_unfinished_remote_phase(phases: list[dict]) -> str:
    for phase in phases:
        if phase.get("status") in {"blocked", "failed", "pending", "running"}:
            return str(phase.get("id") or "collect_target")
    return "done"


def _remote_session_status(phases: list[dict], *, blocked_reason: str, target: dict) -> str:
    if blocked_reason:
        return "blocked"
    if any(phase.get("status") == "failed" for phase in phases):
        return "failed"
    if any(phase.get("id") == "verify" and phase.get("status") == "completed" for phase in phases):
        return "completed"
    if any(phase.get("id") == "transfer" and phase.get("status") == "completed" for phase in phases) or any(
        phase.get("id") == "remote_apply" and phase.get("status") == "completed" for phase in phases
    ):
        return "verifying"
    if not target.get("host") or not target.get("username"):
        return "collecting_target"
    return "running"


def _compact_observation_data_for_world(data: dict) -> dict:
    compact: dict = {}
    for key in [
        "path",
        "relative_path",
        "command",
        "cwd",
        "exit_code",
        "status",
        "task_id",
        "count",
        "truncated",
        "full_view",
        "host",
        "username",
        "port",
        "local_path",
        "remote_path",
        "remote_command",
        "url",
        "status_code",
        "contains_expected_text",
    ]:
        if key in data:
            compact[key] = data[key]
    return compact


def _task_with_latest_observation(task: dict, observations: list[AgentToolObservationPayload]) -> AgentTaskPayload:
    updated: AgentTaskPayload = dict(task)
    steps = [dict(step) for step in updated.get("steps", [])]
    if not steps:
        steps = [{"id": "step-1", "description": "执行本轮任务", "status": "pending"}]
    latest = observations[-1] if observations else None
    if latest:
        step = steps[0]
        step["status"] = "completed" if latest.get("ok") else "failed"
        step["tool_name"] = str(latest.get("tool_name") or "")
        step["arguments"] = dict(latest.get("arguments") or {})
        step["result_summary"] = _summarize_tool_observation(latest)
        if not latest.get("ok"):
            step["retry_count"] = int(step.get("retry_count") or 0) + 1
        steps[0] = step
        updated["current_step_id"] = str(step.get("id") or "step-1")
    updated["steps"] = steps  # type: ignore[typeddict-item]
    return updated


def _verification_reason(state: MemoryChatGraphState, latest: dict) -> str:
    if not latest:
        return "尚未调用工具，交由 agent 决定下一步。"
    if not latest.get("ok"):
        return (
            f"{latest.get('tool_name', 'tool')} 失败："
            f"{latest.get('error_code', '')} {latest.get('message', '')}".strip()
        )
    criteria = (state.get("task") or {}).get("acceptance_criteria") or []
    return "最近工具调用成功；下一轮 agent 必须对照验收条件决定继续执行或最终回答。验收条件：" + "；".join(criteria)
