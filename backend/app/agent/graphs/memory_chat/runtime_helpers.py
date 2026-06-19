import json
from typing import Literal

from app.agent.graphs.memory_chat.state import (
    AgentThoughtPayload,
    AgentToolObservationPayload,
    MemoryChatGraphState,
    TurnMessagePayload,
)

REQUEST_USER_INPUT_TOOL_NAME = "request_user_input"
INSPECT_IMAGE_ATTACHMENT_TOOL_NAME = "inspect_image_attachment"


def _turn_message(
    role: Literal["user", "assistant", "tool", "system"],
    content: str,
    *,
    name: str = "",
    tool_call_id: str | None = None,
) -> TurnMessagePayload:
    """创建本轮 graph 内部消息。

    该消息流只在本轮内追加，不承担跨轮历史职责；跨轮历史仍由金字塔上下文负责。
    """

    return {
        "role": role,
        "content": content,
        "name": name,
        "tool_call_id": tool_call_id,
    }


def _tool_observation_message(observation: AgentToolObservationPayload) -> str:
    """把工具 observation 压成一条本轮 tool message。"""

    if observation.get("ok"):
        return json_dumps_compact(
            {
                "ok": True,
                "tool_name": observation.get("tool_name"),
                "data": observation.get("data") or {},
            }
        )
    return json_dumps_compact(
        {
            "ok": False,
            "tool_name": observation.get("tool_name"),
            "error_code": observation.get("error_code"),
            "message": observation.get("message"),
            "blocked": observation.get("blocked", False),
            "data": observation.get("data") or {},
        }
    )


def json_dumps_compact(payload: dict) -> str:
    """把工具消息压成稳定 JSON，避免大段 Python repr 进入模型上下文。"""

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _thought(
    thought_id: str,
    title: str,
    summary: str,
    *,
    related_node: str,
    related_tool_call_id: str | None = None,
    status: Literal["running", "completed", "failed", "interrupted"] = "completed",
    step_index: int | None = None,
) -> AgentThoughtPayload:
    """创建一个可展示的过程摘要。

    step_index 标记该 thought 属于 ReAct 循环里的哪一步，前端可以据此把
    同一步的工具调用、回答片段聚合到一段时间线上,实现 通用 coding agent 那样
    "思考-工具-回答" 顺序串行的展示。
    """

    payload: AgentThoughtPayload = {
        "id": thought_id,
        "title": title,
        "summary": summary,
        "status": status,
        "related_node": related_node,
        "related_tool_call_id": related_tool_call_id,
    }
    if step_index is not None:
        payload["step_index"] = int(step_index)
    return payload


def _complete_running_thoughts(state: MemoryChatGraphState) -> list[AgentThoughtPayload]:
    """把已有 running thought 收敛为 completed，便于前端自动折叠。"""

    thoughts: list[AgentThoughtPayload] = []
    for thought in state.get("thought_events", []):
        item = dict(thought)
        if item.get("status") == "running":
            item["status"] = "completed"
        thoughts.append(item)  # type: ignore[arg-type]
    return thoughts


def _summarize_tool_observation(observation: AgentToolObservationPayload) -> str:
    """生成面向用户的工具过程摘要。"""

    tool_name = observation.get("tool_name", "")
    if not observation.get("ok"):
        return f"{tool_name} 没有成功：{observation.get('error_code', '')} {observation.get('message', '')}".strip()
    data = dict(observation.get("data") or {})
    if tool_name == REQUEST_USER_INPUT_TOOL_NAME:
        return f"用户已选择：{data.get('answer') or observation.get('message') or ''}".strip()
    if tool_name == "write_file":
        return f"写入完成：{data.get('relative_path') or data.get('path')}"
    if tool_name == "read_file":
        return f"读取完成：{data.get('relative_path') or data.get('path')}"
    if tool_name == "search_files":
        return f"文件搜索完成，找到 {len(data.get('matches') or [])} 个候选。"
    if tool_name == "search_text":
        return f"文本搜索完成，找到 {len(data.get('matches') or [])} 条匹配。"
    if tool_name == "knowledge_search":
        return f"挂载知库检索完成，找到 {len(data.get('results') or [])} 条片段。"
    if tool_name == INSPECT_IMAGE_ATTACHMENT_TOOL_NAME:
        return f"图片解析完成：attachment_id={data.get('attachment_id')}"
    if tool_name == "remote_connectivity_check":
        return f"远程连接可用：{data.get('username')}@{data.get('host')}:{data.get('port')}"
    if tool_name == "remote_upload_file":
        return f"远程上传完成：{data.get('remote_path')}"
    if tool_name == "remote_exec":
        return f"远程命令执行完成：exit_code={data.get('exit_code')}"
    if tool_name == "remote_verify_http":
        return f"HTTP 验证完成：{data.get('url')} status={data.get('status_code')}"
    if tool_name == "list_dir":
        return f"目录读取完成：{data.get('relative_path') or data.get('path')}"
    return f"{tool_name} 执行完成。"




def _resolve_conversation_id(state: MemoryChatGraphState) -> int:
    conversation_id = state.get("conversation_id")
    if conversation_id is None:
        raise ValueError("conversation_id is required.")
    return int(conversation_id)


def _resolve_user_message(state: MemoryChatGraphState) -> str:
    user_message = state.get("user_message", "").strip()
    if not user_message:
        raise ValueError("user_message is required.")
    return user_message
