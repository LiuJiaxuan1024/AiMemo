from typing import Literal

from langchain_core.tools import StructuredTool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from app.agent.graphs.memory_chat.state import (
    AgentToolActionPayload,
    AgentToolObservationPayload,
    MemoryChatGraphState,
)


REQUEST_USER_INPUT_TOOL_NAME = "request_user_input"


def _nodes_facade():
    from app.agent.graphs.memory_chat import nodes as nodes_facade

    return nodes_facade


def json_dumps_compact(payload: dict) -> str:
    return _nodes_facade().json_dumps_compact(payload)


def _turn_message(*args, **kwargs):
    return _nodes_facade()._turn_message(*args, **kwargs)


def _tool_observation_message(observation: AgentToolObservationPayload) -> str:
    return _nodes_facade()._tool_observation_message(observation)


def _thought(*args, **kwargs):
    return _nodes_facade()._thought(*args, **kwargs)


class UserInputOption(BaseModel):
    label: str = Field(description="展示给用户看的选项标题。")
    value: str = Field(description="选中后交给 agent 继续执行的具体答案。")
    description: str = Field(default="", description="一行以内的选项说明，解释影响或取舍。")


class RequestUserInputToolInput(BaseModel):
    questions: list[dict] = Field(
        default_factory=list,
        description="连续展示给用户的结构化问题列表。每项包含 question/options/selection_mode 等字段。",
    )
    question: str = Field(
        default="",
        min_length=0,
        description="兼容旧版单问题提问；当 questions 为空时才使用。",
    )
    options: list[UserInputOption] = Field(
        default_factory=list,
        description="兼容旧版单问题的推荐选项。",
    )
    selection_mode: Literal["single", "multiple"] = Field(
        default="single",
        description="兼容旧版单问题的选择模式。",
    )
    allow_other: bool = Field(default=True, description="兼容旧版单问题：是否允许用户输入自定义答案。")
    other_placeholder: str = Field(
        default="请输入其他答案",
        description="兼容旧版单问题：用户选择 Other 时的输入框占位文本。",
    )


def _create_request_user_input_tool() -> StructuredTool:
    def request_user_input(
        question: str = "",
        options: list[dict] | None = None,
        allow_other: bool = True,
        selection_mode: str = "single",
        other_placeholder: str = "请输入其他答案",
        questions: list[dict] | None = None,
    ) -> str:
        """Ask the user to choose when required information is missing."""

        return json_dumps_compact(
            {
                "ok": False,
                "tool_name": REQUEST_USER_INPUT_TOOL_NAME,
                "message": "request_user_input is handled by the graph interrupt runtime.",
                "data": {
                    "questions": questions or [],
                    "question": question,
                    "options": options or [],
                    "selection_mode": selection_mode,
                    "allow_other": allow_other,
                    "other_placeholder": other_placeholder,
                },
            }
        )

    return StructuredTool.from_function(
        func=request_user_input,
        name=REQUEST_USER_INPUT_TOOL_NAME,
        description=(
            "当必须让用户补充选择、确认路径、选择方案、提供缺失参数或确认风险操作时调用。"
            "调用后 graph 会暂停并向用户展示选择框；用户回答后会从同一轮继续执行。"
            "不要把“请选择：1...2...3...”写成普通最终回答；需要用户选择时必须调用此工具。"
            "如果需要一次收集多个信息，优先使用 questions 数组，每个 question 都包含自己的 options；"
            "例如同时询问项目目录和项目类型时，传 questions=[{question:'项目放在哪里？', options:[...]}, {question:'项目类型是什么？', options:[...]}]。"
            "单个问题时可以继续使用 question/options。question 必须说明为什么需要用户选择；不能空泛。"
            "每个 options 只放 2-4 个建议选项，推荐项放第一；不要包含 Other，界面会自动追加自定义输入。"
            "如果用户可同时选择多个项目/功能/范围，selection_mode 必须设为 multiple。"
        ),
        args_schema=RequestUserInputToolInput,
    )


_OTHER_LIKE_LABELS = {
    "其他",
    "other",
    "others",
    "其他答案",
    "其他选项",
    "其他路径",
    "其它",
    "请输入其他答案",
    "请输入其他选项",
    "请输入其他路径",
    "自定义",
    "自定义答案",
    "自定义路径",
    "自定义选项",
    "custom",
}


def _is_other_like_option(label: str, value: str) -> bool:
    """判断 LLM 加的某个选项是否在重复前端会自动追加的 Other 输入项。

    前端永远会在末尾挂一项带输入框的“其他”，LLM 若再加“其他/Other/自定义路径”等就会出现
    一项无输入框的伪“其他”按钮，看起来像 disabled 还可能被默认选中。
    """

    haystack = {label.strip().lower(), value.strip().lower()}
    if not any(haystack):
        return False
    return bool(haystack & _OTHER_LIKE_LABELS)


def _normalize_request_user_input_arguments(arguments: dict) -> dict:
    """保留并规整结构化提问参数。

    Local Operator 的通用 _normalize_tool_arguments 会把未知工具参数清成空 dict。
    request_user_input 是 memory_chat 自己的交互工具，必须单独保留 question/options。
    """

    questions = _normalize_user_input_questions(arguments)
    raw_options = arguments.get("options")
    options: list[dict] = []
    if isinstance(raw_options, list):
        for raw_option in raw_options[:4]:
            if not isinstance(raw_option, dict):
                continue
            label = str(raw_option.get("label") or raw_option.get("value") or "").strip()
            value = str(raw_option.get("value") or label).strip()
            if not label and not value:
                continue
            if _is_other_like_option(label, value):
                # 兜底过滤：即便 prompt 已经禁止，LLM 仍会偶尔塞“其他/自定义路径”等
                # 重复项；这里直接丢弃，前端会在末尾自动追加唯一一份带输入框的 Other。
                continue
            options.append(
                {
                    "id": str(raw_option.get("id") or ""),
                    "label": label or value,
                    "value": value or label,
                    "description": str(raw_option.get("description") or "").strip(),
                }
            )
    raw_selection_mode = arguments.get("selection_mode")
    if raw_selection_mode is None and isinstance(arguments.get("multiSelect"), bool):
        raw_selection_mode = "multiple" if bool(arguments.get("multiSelect")) else "single"
    elif raw_selection_mode is None:
        raw_selection_mode = arguments.get("multiSelect")
    selection_mode = str(raw_selection_mode or "single").strip().lower()
    if selection_mode not in {"single", "multiple"}:
        selection_mode = "multiple" if bool(arguments.get("allow_multiple", False)) else "single"
    return {
        "questions": questions,
        "question": str(arguments.get("question") or "").strip(),
        "options": options,
        "selection_mode": selection_mode,
        "allow_other": bool(arguments.get("allow_other", True)),
        "other_placeholder": str(arguments.get("other_placeholder") or "请输入其他答案").strip(),
    }


def _run_request_user_input_action(
    state: MemoryChatGraphState,
    *,
    action: AgentToolActionPayload,
    step_index: int | None = None,
) -> MemoryChatGraphState:
    arguments = dict(action.get("arguments") or {})
    invalid_reason = _invalid_user_input_request_reason(arguments)
    if invalid_reason:
        observation: AgentToolObservationPayload = {
            "tool_call_id": str(action.get("tool_call_id") or ""),
            "tool_name": REQUEST_USER_INPUT_TOOL_NAME,
            "arguments": arguments,
            "ok": False,
            "data": {},
            "error_code": "INVALID_ARGUMENT",
            "message": (
                f"{invalid_reason}。请重新调用 request_user_input：question 必须是用户能直接理解的具体问题，"
                "options 必须包含 2-4 个具体建议选项；不要用普通文本列选项。"
            ),
            "blocked": False,
        }
        return {
            "tool_observations": [*state.get("tool_observations", []), observation],
            "tool_budget": int(state.get("tool_budget") or 0),
            "turn_messages": [
                *state.get("turn_messages", []),
                _turn_message(
                    "tool",
                    _tool_observation_message(observation),
                    name=REQUEST_USER_INPUT_TOOL_NAME,
                    tool_call_id=str(action.get("tool_call_id") or "") or None,
                ),
            ],
            "thought_events": [
                *state.get("thought_events", []),
                _thought(
                    f"request-user-input-invalid-{action.get('tool_call_id') or step_index or 'choice'}",
                    "提问参数不完整",
                    "request_user_input 缺少具体问题或建议选项，要求 agent 重新发起结构化提问。",
                    related_node="tools",
                    related_tool_call_id=str(action.get("tool_call_id") or "") or None,
                    status="failed",
                    step_index=step_index,
                ),
            ],
        }
    request = _build_user_input_interrupt_payload(arguments, action=action, step_index=step_index)
    resume_value = interrupt(request)
    answer_payload = _normalize_user_input_resume(resume_value, request)
    observation: AgentToolObservationPayload = {
        "tool_call_id": str(action.get("tool_call_id") or ""),
        "tool_name": REQUEST_USER_INPUT_TOOL_NAME,
        "arguments": arguments,
        "ok": True,
        "data": {
            "request": request,
            "answer": answer_payload["answer"],
            "question_answers": answer_payload["question_answers"],
            "selected_option_id": answer_payload["selected_option_id"],
            "selected_option_ids": answer_payload["selected_option_ids"],
            "selected_option_label": answer_payload["selected_option_label"],
            "selected_option_labels": answer_payload["selected_option_labels"],
            "is_other": answer_payload["is_other"],
        },
        "error_code": "",
        "message": f"用户选择：{answer_payload['answer']}",
        "blocked": False,
    }
    return {
        "tool_observations": [*state.get("tool_observations", []), observation],
        "tool_budget": int(state.get("tool_budget") or 0),
        "turn_messages": [
            *state.get("turn_messages", []),
            _turn_message(
                "tool",
                _tool_observation_message(observation),
                name=REQUEST_USER_INPUT_TOOL_NAME,
                tool_call_id=str(action.get("tool_call_id") or "") or None,
            ),
        ],
        "thought_events": [
            *state.get("thought_events", []),
            _thought(
                f"request-user-input-{action.get('tool_call_id') or step_index or 'choice'}",
                "等待用户选择",
                # 多问题路径里 request 只有 `questions=[...]`、没有顶层 `question` 字段；
                # 用 .get + fallback 拼一段摘要，避免 KeyError 把整轮终止。
                "已向用户询问：" + _summarize_user_input_request(request),
                related_node="tools",
                related_tool_call_id=str(action.get("tool_call_id") or "") or None,
                status="interrupted",
                step_index=step_index,
            ),
        ],
    }


def _summarize_user_input_request(request: dict) -> str:
    """从 interrupt 请求里提一段适合 thought 展示的简短摘要。"""

    single_question = str(request.get("question") or "").strip()
    if single_question:
        return single_question
    questions = request.get("questions") if isinstance(request.get("questions"), list) else []
    titles: list[str] = []
    for item in questions:
        if not isinstance(item, dict):
            continue
        text = str(item.get("question") or "").strip()
        if text:
            titles.append(text)
    if not titles:
        return "需要你补充一个选择。"
    if len(titles) == 1:
        return titles[0]
    return "；".join(titles)


def _build_user_input_interrupt_payload(
    arguments: dict,
    *,
    action: AgentToolActionPayload,
    step_index: int | None,
) -> dict:
    questions = _normalize_user_input_questions(arguments)
    if questions:
        return {
            "kind": "user_input",
            "request_id": str(action.get("tool_call_id") or f"user-input-{step_index or 0}"),
            "questions": questions,
            "allow_other": bool(arguments.get("allow_other", True)),
            "other_option": {
                "id": "other",
                "label": "其他",
                "value": "",
                "description": "自己输入一个答案。",
                "placeholder": str(arguments.get("other_placeholder") or "请输入其他答案").strip(),
            },
            "step_index": int(step_index or 0),
        }
    question = str(arguments.get("question") or "").strip()
    raw_options = arguments.get("options")
    options: list[dict] = []
    if isinstance(raw_options, list):
        for index, raw_option in enumerate(raw_options[:4]):
            if not isinstance(raw_option, dict):
                continue
            label = str(raw_option.get("label") or raw_option.get("value") or "").strip()
            value = str(raw_option.get("value") or label).strip()
            if not label and not value:
                continue
            option_id = str(raw_option.get("id") or f"option-{index + 1}")
            options.append(
                {
                    "id": option_id,
                    "label": label or value,
                    "value": value or label,
                    "description": str(raw_option.get("description") or "").strip(),
                    "recommended": index == 0,
                }
            )
    selection_mode = str(arguments.get("selection_mode") or "").strip().lower()
    if selection_mode not in {"single", "multiple"}:
        selection_mode = "multiple" if bool(arguments.get("allow_multiple", False)) else "single"
    return {
        "kind": "user_input",
        "request_id": str(action.get("tool_call_id") or f"user-input-{step_index or 0}"),
        "question": question,
        "options": options,
        "selection_mode": selection_mode,
        "allow_other": bool(arguments.get("allow_other", True)),
        "other_option": {
            "id": "other",
            "label": "其他",
            "value": "",
            "description": "自己输入一个答案。",
            "placeholder": str(arguments.get("other_placeholder") or "请输入其他答案").strip(),
        },
        "step_index": int(step_index or 0),
    }


def _invalid_user_input_request_reason(arguments: dict) -> str:
    if _normalize_user_input_questions(arguments):
        return ""
    question = str(arguments.get("question") or "").strip()
    if len(question) < 6 or question in {"需要你补充一个选择。", "需要你补充一个选择", "请选择"}:
        return "request_user_input 缺少具体问题"
    raw_options = arguments.get("options")
    if not isinstance(raw_options, list):
        return "request_user_input 缺少 options"
    valid_options = []
    for raw_option in raw_options:
        if not isinstance(raw_option, dict):
            continue
        label = str(raw_option.get("label") or raw_option.get("value") or "").strip()
        value = str(raw_option.get("value") or label).strip()
        if label or value:
            valid_options.append(raw_option)
    if len(valid_options) < 2:
        return "request_user_input 至少需要 2 个具体建议选项"
    return ""


def _normalize_user_input_questions(arguments: dict) -> list[dict]:
    raw_questions = arguments.get("questions")
    if not isinstance(raw_questions, list):
        return []
    questions: list[dict] = []
    for index, raw_question in enumerate(raw_questions[:6]):
        if not isinstance(raw_question, dict):
            continue
        question_text = str(raw_question.get("question") or "").strip()
        raw_options = raw_question.get("options")
        options: list[dict] = []
        if isinstance(raw_options, list):
            for option_index, raw_option in enumerate(raw_options[:4]):
                if not isinstance(raw_option, dict):
                    continue
                label = str(raw_option.get("label") or raw_option.get("value") or "").strip()
                value = str(raw_option.get("value") or label).strip()
                if not label and not value:
                    continue
                if _is_other_like_option(label, value):
                    continue
                options.append(
                    {
                        "id": str(raw_option.get("id") or f"question-{index + 1}-option-{option_index + 1}"),
                        "label": label or value,
                        "value": value or label,
                        "description": str(raw_option.get("description") or "").strip(),
                        "recommended": bool(raw_option.get("recommended", option_index == 0)),
                    }
                )
        if len(question_text) < 6 or len(options) < 2:
            continue
        selection_mode = str(raw_question.get("selection_mode") or "single").strip().lower()
        if selection_mode not in {"single", "multiple"}:
            selection_mode = "single"
        questions.append(
            {
                "id": str(raw_question.get("id") or f"question-{index + 1}"),
                "question": question_text,
                "options": options,
                "selection_mode": selection_mode,
                "allow_other": bool(raw_question.get("allow_other", True)),
                "other_placeholder": str(raw_question.get("other_placeholder") or "请输入其他答案").strip(),
            }
        )
    return questions


def _normalize_user_input_resume(resume_value, request: dict) -> dict:
    payload = resume_value if isinstance(resume_value, dict) else {"answer": str(resume_value or "")}
    questions = request.get("questions") if isinstance(request.get("questions"), list) else []
    if questions:
        return _normalize_multi_user_input_resume(payload, request, questions)
    raw_ids = payload.get("selected_option_ids")
    if isinstance(raw_ids, list):
        selected_option_ids = [str(item) for item in raw_ids if str(item)]
    else:
        selected_option_ids = []
    legacy_id = str(payload.get("selected_option_id") or payload.get("option_id") or "")
    if legacy_id and legacy_id not in selected_option_ids:
        selected_option_ids.append(legacy_id)
    answer = str(payload.get("answer") or "").strip()
    selected_option_labels: list[str] = []
    selected_option_values: list[str] = []
    is_other = "other" in selected_option_ids
    options = request.get("options") if isinstance(request.get("options"), list) else []
    options_by_id = {
        str(option.get("id") or ""): option
        for option in options
        if isinstance(option, dict)
    }
    if not selected_option_ids and request.get("selection_mode") != "multiple" and options:
        first_id = str(options[0].get("id") or "")
        if first_id:
            selected_option_ids.append(first_id)
    for option_id in selected_option_ids:
        if option_id == "other":
            continue
        option = options_by_id.get(option_id)
        if not isinstance(option, dict):
            continue
        label = str(option.get("label") or "")
        value = str(option.get("value") or label)
        if label:
            selected_option_labels.append(label)
        if value:
            selected_option_values.append(value)
    other_text = str(payload.get("other_text") or "").strip()
    if not answer:
        answer_parts = [*selected_option_values]
        if is_other and other_text:
            answer_parts.append(other_text)
        answer = "\n".join(answer_parts).strip()
    if not answer:
        answer = "继续"
    selected_option_id = selected_option_ids[0] if selected_option_ids else "other"
    return {
        "answer": answer,
        "question_answers": [],
        "selected_option_id": selected_option_id,
        "selected_option_ids": selected_option_ids or [selected_option_id],
        "selected_option_label": selected_option_labels[0] if selected_option_labels else ("其他" if is_other else answer),
        "selected_option_labels": selected_option_labels or (["其他"] if is_other else [answer]),
        "is_other": is_other,
    }


def _normalize_multi_user_input_resume(payload: dict, request: dict, questions: list[dict]) -> dict:
    """把多问题选择结果还原成 agent 可读的逐题答案。

    前端/桌面端会一次性提交多个问题的选择。这里保留每题的 question_id、问题文本和答案，
    避免工具 observation 只剩几行值，导致 agent 分不清“哪个答案对应哪个问题”。
    """

    raw_question_answers = payload.get("question_answers")
    answer_items: list[dict] = []
    if isinstance(raw_question_answers, list):
        answer_items = [item for item in raw_question_answers if isinstance(item, dict)]
    raw_answers = payload.get("answers")
    fallback_answers = [str(item).strip() for item in raw_answers] if isinstance(raw_answers, list) else []
    answers_by_id = {
        str(item.get("question_id") or item.get("id") or ""): item
        for item in answer_items
        if str(item.get("question_id") or item.get("id") or "")
    }
    normalized_items: list[dict] = []
    answer_lines: list[str] = []
    all_selected_ids: list[str] = []
    all_selected_labels: list[str] = []
    any_other = False

    for index, question in enumerate(questions):
        question_id = str(question.get("id") or f"question-{index + 1}")
        item = answers_by_id.get(question_id)
        if item is None and index < len(answer_items):
            item = answer_items[index]
        if item is None:
            item = {}
        selected_ids = _string_list(item.get("selected_option_ids"))
        legacy_id = str(item.get("selected_option_id") or "").strip()
        if legacy_id and legacy_id not in selected_ids:
            selected_ids.append(legacy_id)
        options = question.get("options") if isinstance(question.get("options"), list) else []
        options_by_id = {
            str(option.get("id") or ""): option
            for option in options
            if isinstance(option, dict)
        }
        if not selected_ids and question.get("selection_mode") != "multiple" and options:
            first_id = str(options[0].get("id") or "")
            if first_id:
                selected_ids.append(first_id)
        selected_labels: list[str] = []
        selected_values: list[str] = []
        for option_id in selected_ids:
            if option_id == "other":
                continue
            option = options_by_id.get(option_id)
            if not isinstance(option, dict):
                continue
            label = str(option.get("label") or "").strip()
            value = str(option.get("value") or label).strip()
            if label:
                selected_labels.append(label)
            if value:
                selected_values.append(value)
        other_text = str(item.get("other_text") or "").strip()
        is_other = "other" in selected_ids or bool(other_text)
        any_other = any_other or is_other
        answer = str(item.get("answer") or "").strip()
        if not answer and index < len(fallback_answers):
            answer = fallback_answers[index]
        if not answer:
            parts = [*selected_values]
            if other_text:
                parts.append(other_text)
            answer = "\n".join(parts).strip()
        if not answer:
            answer = "继续"
        question_text = str(question.get("question") or question_id)
        normalized_item = {
            "question_id": question_id,
            "question": question_text,
            "answer": answer,
            "selected_option_id": selected_ids[0] if selected_ids else "other",
            "selected_option_ids": selected_ids or ["other"],
            "selected_option_labels": selected_labels or (["其他"] if is_other else [answer]),
            "other_text": other_text,
            "is_other": is_other,
        }
        normalized_items.append(normalized_item)
        answer_lines.append(f"{index + 1}. {question_text}\n答：{answer}")
        all_selected_ids.extend(normalized_item["selected_option_ids"])
        all_selected_labels.extend(normalized_item["selected_option_labels"])

    compact_answer = str(payload.get("answer") or "").strip() or "\n".join(answer_lines).strip() or "继续"
    return {
        "answer": compact_answer,
        "question_answers": normalized_items,
        "selected_option_id": all_selected_ids[0] if all_selected_ids else "other",
        "selected_option_ids": all_selected_ids or ["other"],
        "selected_option_label": all_selected_labels[0] if all_selected_labels else ("其他" if any_other else compact_answer),
        "selected_option_labels": all_selected_labels or (["其他"] if any_other else [compact_answer]),
        "is_other": any_other,
    }


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


