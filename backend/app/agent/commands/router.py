from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from app.agent.commands.registry import get_command_by_input
from app.agent.commands.result_codec import serialize_command_result
from app.agent.commands.schemas import CommandExecuteResponse, CommandResult
from app.agent.model import AGENT_CHAT_MODEL
from app.core.config import settings
from app.models.knowledge import KnowledgeSpace
from app.models.voice_profile import VoiceProfile
from app.schemas.conversation import ChatMessageCreate
from app.services.conversation_service import append_message
from app.services.elf_voice_mode_service import get_elf_voice_mode_enabled, set_elf_voice_mode_enabled_persistent
from app.services.knowledge_mount_service import (
    add_conversation_knowledge_mount,
    delete_conversation_knowledge_mount,
    list_conversation_knowledge_mounts,
)
from app.services.model_config_service import (
    agent_chat_provider_specs,
    current_agent_chat_config,
    current_agent_chat_model,
    current_agent_chat_provider,
    current_planner_model,
    normalize_model_name,
    planner_model_options,
    set_agent_chat_model,
    set_agent_chat_provider,
    set_planner_model,
)
from app.services.runtime_config_service import set_persistent_runtime_config
from app.services.voice_profile_service import activate_voice_profile, ensure_default_voice_profile


def execute_slash_command(
    session: Session,
    *,
    conversation_id: int,
    raw_command: str,
    parent_message_id: int | None = None,
) -> CommandExecuteResponse:
    user_message = append_message(
        session,
        conversation_id,
        ChatMessageCreate(
            role="user",
            content=raw_command.strip(),
            parent_id=parent_message_id,
            status="completed",
        ),
    )
    result = _execute(session, conversation_id=conversation_id, raw_command=raw_command)
    assistant_message = append_message(
        session,
        conversation_id,
        ChatMessageCreate(
            role="assistant",
            content=serialize_command_result(result),
            parent_id=user_message.id,
            status="completed" if result.status in {"success", "noop"} else "failed",
        ),
    )
    return CommandExecuteResponse(
        result=result,
        user_message=user_message.model_dump(mode="json"),
        assistant_message=assistant_message.model_dump(mode="json"),
    )


def _execute(session: Session, *, conversation_id: int, raw_command: str) -> CommandResult:
    command = get_command_by_input(session, raw_command)
    if command is None:
        return CommandResult(
            command=raw_command.strip(),
            status="failed",
            message=f"未知指令：{raw_command.strip()}",
        )
    if command.visibility.state == "disabled":
        return CommandResult(
            command=raw_command.strip(),
            command_id=command.id,
            status="failed",
            scope=command.scope,
            target=command.id,
            message=command.visibility.reason or "该指令当前不可用。",
        )
    if command.executor == "show_config":
        return _show_config(session, conversation_id, raw_command, command.id)
    if command.executor == "agent_status":
        return _agent_status(raw_command, command.id)
    if command.executor == "elf_status":
        return _elf_status(session, raw_command, command.id)
    if command.executor == "knowledge_mounts":
        return _knowledge_mounts(session, conversation_id, raw_command, command.id)
    if command.executor == "mount_knowledge":
        return _mount_knowledge(session, conversation_id, raw_command, command.id)
    if command.executor == "unmount_knowledge":
        return _unmount_knowledge(session, conversation_id, raw_command, command.id)
    if command.executor == "set_elf_voice_mode":
        return _set_elf_voice_mode(session, raw_command, command.id)
    if command.executor == "set_elf_default_voice":
        return _set_elf_default_voice(session, raw_command, command.id)
    if command.executor == "set_agent_chat_provider":
        return _set_agent_chat_provider(session, raw_command, command.id)
    if command.executor == "set_agent_chat_model":
        return _set_agent_chat_model(session, raw_command, command.id)
    if command.executor == "set_planner_model":
        return _set_planner_model(session, raw_command, command.id)
    return CommandResult(
        command=raw_command.strip(),
        command_id=command.id,
        status="failed",
        scope=command.scope,
        target=command.id,
        message="该指令已注册，但执行器尚未接入。",
    )


def _show_config(session: Session, conversation_id: int, raw_command: str, command_id: str) -> CommandResult:
    mounts = list_conversation_knowledge_mounts(session, conversation_id)
    agent_chat = _agent_chat_config()
    details = [
        {
            "label": "主聊天模型",
            "value": f"{agent_chat.get('provider', 'dashscope')} / {agent_chat.get('model', AGENT_CHAT_MODEL)}",
        },
        {"label": "Planner", "value": f"dashscope / {current_planner_model()}"},
        {
            "label": "语音",
            "value": f"ASR {settings.voice_aliyun_asr_model} / TTS {settings.voice_aliyun_tts_model}",
        },
        {
            "label": "当前对话知识空间",
            "value": "、".join(mount.space_name for mount in mounts) if mounts else "未挂载",
        },
    ]
    return CommandResult(
        command=raw_command.strip(),
        command_id=command_id,
        status="success",
        scope="turn",
        target="runtime_config",
        message="已读取当前运行时配置。",
        details=details,
    )


def _agent_status(raw_command: str, command_id: str) -> CommandResult:
    agent_chat = _agent_chat_config()
    details = [
        {"label": "主聊天模型", "value": str(agent_chat.get("model", AGENT_CHAT_MODEL))},
        {"label": "Provider", "value": str(agent_chat.get("provider", "dashscope"))},
        {"label": "工具调用", "value": "enabled"},
        {"label": "流式输出", "value": "enabled" if bool(agent_chat.get("streaming", True)) else "disabled"},
        {"label": "Planner", "value": current_planner_model()},
        {"label": "Thinking", "value": "disabled by default"},
    ]
    return CommandResult(
        command=raw_command.strip(),
        command_id=command_id,
        status="success",
        scope="turn",
        target="agent",
        message="Agent 运行状态正常。",
        details=details,
    )


def _elf_status(session: Session, raw_command: str, command_id: str) -> CommandResult:
    voice_module_enabled = bool(settings.voice_enabled)
    voice_mode_enabled = get_elf_voice_mode_enabled(session)
    active_voice = ensure_default_voice_profile(session)
    details = [
        {"label": "持续语音对话", "value": "enabled" if voice_mode_enabled else "disabled"},
        {"label": "语音服务能力", "value": "enabled" if voice_module_enabled else "disabled"},
        {"label": "默认声线", "value": f"{active_voice.name} (ID {active_voice.id})"},
        {"label": "ASR Provider", "value": settings.voice_asr_provider},
        {"label": "TTS Provider", "value": settings.voice_tts_provider},
        {"label": "TTS 模型", "value": settings.voice_aliyun_tts_model},
    ]
    return CommandResult(
        command=raw_command.strip(),
        command_id=command_id,
        status="success",
        scope="turn",
        target="elf",
        message="已读取精灵状态。",
        details=details,
    )


def _knowledge_mounts(session: Session, conversation_id: int, raw_command: str, command_id: str) -> CommandResult:
    mounts = list_conversation_knowledge_mounts(session, conversation_id)
    details = [
        {
            "label": mount.space_name,
            "value": f"ready {mount.ready_document_count}/{mount.document_count}",
        }
        for mount in mounts
    ]
    return CommandResult(
        command=raw_command.strip(),
        command_id=command_id,
        status="success" if mounts else "noop",
        scope="conversation",
        target="knowledge.mounts",
        message="当前对话已挂载的知识空间如下。" if mounts else "当前对话还没有挂载知识空间。",
        details=details,
        suggestions=[] if mounts else ["可以稍后使用 /mount knowledge <space> 挂载知识空间。"],
    )


def _mount_knowledge(session: Session, conversation_id: int, raw_command: str, command_id: str) -> CommandResult:
    space_arg = _extract_space_arg(raw_command, prefixes=("/mount knowledge",))
    if not space_arg:
        candidates = _unmounted_active_spaces(session, conversation_id)
        if not candidates:
            mounts = list_conversation_knowledge_mounts(session, conversation_id)
            return CommandResult(
                command=raw_command.strip(),
                command_id=command_id,
                status="noop",
                scope="conversation",
                target="knowledge.mounts",
                message="当前所有可用知识空间都已经挂载到这个对话。",
                details=_mount_details(mounts),
                old_value=[mount.space_name for mount in mounts],
                new_value=[mount.space_name for mount in mounts],
            )
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="needs_input",
            scope="conversation",
            target="knowledge.mounts",
            message="请选择要挂载到当前对话的知识空间。",
            details=_space_details(candidates),
            suggestions=[f"/mount knowledge {space.id}" for space in candidates if space.id is not None],
        )

    spaces, resolution = _resolve_active_knowledge_spaces(session, space_arg, command_prefix="/mount knowledge")
    if resolution is not None:
        return resolution.model_copy(update={"command": raw_command.strip(), "command_id": command_id})

    assert spaces is not None
    before = list_conversation_knowledge_mounts(session, conversation_id)
    old_value = [mount.space_name for mount in before]
    before_ids = {mount.space_id for mount in before}
    spaces_to_mount = [space for space in spaces if space.id is not None and space.id not in before_ids]
    if not spaces_to_mount:
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="noop",
            scope="conversation",
            target="knowledge.mounts",
            message="选择的知识空间已经挂载到当前对话。",
            details=_mount_details(before),
            old_value=old_value,
            new_value=old_value,
            suggestions=["可以使用 /knowledge mounts 查看当前挂载。"],
        )

    for space in spaces_to_mount:
        assert space.id is not None
        add_conversation_knowledge_mount(session, conversation_id, space.id)
    after = list_conversation_knowledge_mounts(session, conversation_id)
    mounted_names = "、".join(space.name for space in spaces_to_mount)
    rollback_ids = ",".join(str(space.id) for space in spaces_to_mount if space.id is not None)
    return CommandResult(
        command=raw_command.strip(),
        command_id=command_id,
        status="success",
        scope="conversation",
        changed=True,
        target="knowledge.mounts",
        old_value=old_value,
        new_value=[mount.space_name for mount in after],
        message=f"已挂载 {len(spaces_to_mount)} 个知识空间：{mounted_names}。",
        details=_mount_details(after),
        rollback_command=f"/unmount knowledge {rollback_ids}" if rollback_ids else None,
    )


def _unmount_knowledge(session: Session, conversation_id: int, raw_command: str, command_id: str) -> CommandResult:
    space_arg = _extract_space_arg(raw_command, prefixes=("/unmount knowledge", "/umount knowledge"))
    if not space_arg:
        mounts = list_conversation_knowledge_mounts(session, conversation_id)
        if not mounts:
            return CommandResult(
                command=raw_command.strip(),
                command_id=command_id,
                status="noop",
                scope="conversation",
                target="knowledge.mounts",
                message="当前对话还没有挂载知识空间。",
                details=[],
                suggestions=["可以使用 /mount knowledge 挂载知识空间。"],
            )
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="needs_input",
            scope="conversation",
            target="knowledge.mounts",
            message="请选择要从当前对话卸载的知识空间。",
            details=_mount_details(mounts),
            suggestions=[f"/unmount knowledge {mount.space_id}" for mount in mounts],
        )

    spaces, resolution = _resolve_active_knowledge_spaces(session, space_arg, command_prefix="/unmount knowledge")
    if resolution is not None:
        return resolution.model_copy(update={"command": raw_command.strip(), "command_id": command_id})

    assert spaces is not None
    before = list_conversation_knowledge_mounts(session, conversation_id)
    old_value = [mount.space_name for mount in before]
    before_ids = {mount.space_id for mount in before}
    spaces_to_unmount = [space for space in spaces if space.id is not None and space.id in before_ids]
    if not spaces_to_unmount:
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="noop",
            scope="conversation",
            target="knowledge.mounts",
            message="选择的知识空间当前没有挂载到这个对话。",
            details=_mount_details(before),
            old_value=old_value,
            new_value=old_value,
            suggestions=["可以使用 /knowledge mounts 查看当前挂载。"],
        )

    for space in spaces_to_unmount:
        assert space.id is not None
        delete_conversation_knowledge_mount(session, conversation_id, space.id)
    after = list_conversation_knowledge_mounts(session, conversation_id)
    unmounted_names = "、".join(space.name for space in spaces_to_unmount)
    rollback_ids = ",".join(str(space.id) for space in spaces_to_unmount if space.id is not None)
    return CommandResult(
        command=raw_command.strip(),
        command_id=command_id,
        status="success",
        scope="conversation",
        changed=True,
        target="knowledge.mounts",
        old_value=old_value,
        new_value=[mount.space_name for mount in after],
        message=f"已卸载 {len(spaces_to_unmount)} 个知识空间：{unmounted_names}。",
        details=_mount_details(after),
        rollback_command=f"/mount knowledge {rollback_ids}" if rollback_ids else None,
    )


def _set_elf_voice_mode(session: Session, raw_command: str, command_id: str) -> CommandResult:
    raw_value = _extract_space_arg(raw_command, prefixes=("/config elf.voice.mode",))
    if not raw_value:
        return _boolean_input_result(
            raw_command,
            command_id,
            target="elf.voice.mode",
            message="请选择是否开启精灵语音对话模式。",
            true_label="开启语音对话",
            false_label="关闭语音对话",
            true_command="/config elf.voice.mode true",
            false_command="/config elf.voice.mode false",
        )
    parsed = _parse_bool_arg(raw_value)
    if parsed is None:
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="failed",
            scope="user",
            target="elf.voice.mode",
            message="elf.voice.mode 只接受 true 或 false。",
        )
    if parsed and not settings.voice_enabled:
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="failed",
            scope="user",
            target="elf.voice.mode",
            message="当前语音服务能力未启用，无法开启精灵语音对话。",
        )

    old_value = get_elf_voice_mode_enabled(session)
    if old_value == parsed:
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="noop",
            scope="user",
            target="elf.voice.mode",
            old_value=old_value,
            new_value=old_value,
            message=f"精灵持续语音对话模式当前已经是{'开启' if parsed else '关闭'}状态。",
            details=[
                {"label": "持续语音对话", "value": "enabled" if old_value else "disabled"},
                {"label": "语音服务能力", "value": "enabled" if settings.voice_enabled else "disabled"},
            ],
        )

    set_elf_voice_mode_enabled_persistent(parsed, session)
    return CommandResult(
        command=raw_command.strip(),
        command_id=command_id,
        status="success",
        scope="user",
        changed=True,
        target="elf.voice.mode",
        old_value=old_value,
        new_value=parsed,
        message=f"精灵持续语音对话模式已{'开启' if parsed else '关闭'}。",
        details=[
            {"label": "配置文件", "value": "config.json5 已更新"},
            {"label": "运行时配置", "value": "已更新"},
            {"label": "语音服务能力", "value": "enabled" if settings.voice_enabled else "disabled"},
            {"label": "Reload", "value": "runtime_config / elf_voice_state"},
        ],
        rollback_command=f"/config elf.voice.mode {'true' if old_value else 'false'}",
    )


def _set_elf_default_voice(session: Session, raw_command: str, command_id: str) -> CommandResult:
    raw_voice = _extract_space_arg(raw_command, prefixes=("/config elf.voice.default",))
    if not raw_voice:
        candidates = _ready_voice_profiles(session)
        if not candidates:
            return CommandResult(
                command=raw_command.strip(),
                command_id=command_id,
                status="failed",
                scope="user",
                target="elf.voice.default",
                message="暂无可用声线。请先在语音工坊创建或修复 ready 状态的声线。",
            )
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="needs_input",
            scope="user",
            target="elf.voice.default",
            message="请选择精灵默认声线。",
            details=_voice_profile_details(candidates),
            suggestions=[f"/config elf.voice.default {profile.id}" for profile in candidates if profile.id is not None],
        )

    profile, resolution = _resolve_ready_voice_profile(session, raw_voice)
    if resolution is not None:
        return resolution.model_copy(update={"command": raw_command.strip(), "command_id": command_id})

    assert profile is not None
    old_profile = ensure_default_voice_profile(session)
    if old_profile.id == profile.id:
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="noop",
            scope="user",
            target="elf.voice.default",
            old_value=old_profile.id,
            new_value=old_profile.id,
            message=f"精灵默认声线当前已经是「{profile.name}」。",
            details=_voice_profile_details([profile]),
        )

    activated = activate_voice_profile(session, profile.id)
    set_persistent_runtime_config(session, "elf.voice.default_profile_id", activated.id)
    return CommandResult(
        command=raw_command.strip(),
        command_id=command_id,
        status="success",
        scope="user",
        changed=True,
        target="elf.voice.default",
        old_value=old_profile.id,
        new_value=activated.id,
        message=f"精灵默认声线已切换为「{activated.name}」。",
        details=[
            {"label": "旧声线", "value": f"{old_profile.name} (ID {old_profile.id})"},
            {"label": "新声线", "value": f"{activated.name} (ID {activated.id})"},
            {"label": "配置文件", "value": "config.json5 已更新"},
        ],
        rollback_command=f"/config elf.voice.default {old_profile.id}",
    )


def _set_agent_chat_provider(session: Session, raw_command: str, command_id: str) -> CommandResult:
    raw_provider = _extract_space_arg(raw_command, prefixes=("/config agent.chat.provider",))
    specs = agent_chat_provider_specs()
    if not raw_provider:
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="needs_input",
            scope="user",
            target="models.slots.agent_chat.provider",
            message="请选择主 Agent 使用的 provider。",
            details=[
                {
                    "label": spec.provider,
                    "value": spec.provider,
                    "model": spec.default_model,
                    "api_key_env": spec.api_key_env,
                    "command": f"/config agent.chat.provider {spec.provider}",
                }
                for spec in specs.values()
            ],
            suggestions=[f"/config agent.chat.provider {spec.provider}" for spec in specs.values()],
        )

    provider = raw_provider.strip().lower()
    old_provider = current_agent_chat_provider()
    if provider == old_provider:
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="noop",
            scope="user",
            target="models.slots.agent_chat.provider",
            old_value=old_provider,
            new_value=old_provider,
            message=f"主 Agent provider 当前已经是 {old_provider}。",
            details=_agent_model_details(current_agent_chat_config(), current_planner_model()),
        )

    old_config = current_agent_chat_config()
    next_config, error = set_agent_chat_provider(session, provider)
    if error:
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="failed",
            scope="user",
            target="models.slots.agent_chat.provider",
            message=error,
        )
    assert next_config is not None
    return CommandResult(
        command=raw_command.strip(),
        command_id=command_id,
        status="success",
        scope="user",
        changed=True,
        target="models.slots.agent_chat.provider",
        old_value=old_provider,
        new_value=next_config.get("provider"),
        message=f"主 Agent provider 已切换为 {next_config.get('provider')}。",
        details=[
            {"label": "旧模型", "value": f"{old_config.get('provider', 'dashscope')} / {old_config.get('model', AGENT_CHAT_MODEL)}"},
            {"label": "新模型", "value": f"{next_config.get('provider')} / {next_config.get('model')}"},
            {"label": "配置文件", "value": "config.json5 已更新"},
            {"label": "Reload", "value": "runtime_config / agent_models"},
        ],
        rollback_command=f"/config agent.chat.provider {old_provider}",
    )


def _set_agent_chat_model(session: Session, raw_command: str, command_id: str) -> CommandResult:
    raw_model = _extract_space_arg(raw_command, prefixes=("/config agent.chat.model",))
    provider = current_agent_chat_provider()
    spec = agent_chat_provider_specs().get(provider)
    if not raw_model:
        models = list(spec.models) if spec else []
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="needs_input",
            scope="user",
            target="models.slots.agent_chat.model",
            message=f"请选择 {provider} 的主 Agent 模型。",
            details=[
                {
                    "label": model,
                    "value": model,
                    "provider": provider,
                    "command": f"/config agent.chat.model {model}",
                }
                for model in models
            ],
            suggestions=[f"/config agent.chat.model {model}" for model in models],
        )

    model = normalize_model_name(raw_model)
    if model is None:
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="failed",
            scope="user",
            target="models.slots.agent_chat.model",
            message="agent.chat.model 不能为空，只能包含模型名称字符。",
        )

    old_model = current_agent_chat_model()
    if model == old_model:
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="noop",
            scope="user",
            target="models.slots.agent_chat.model",
            old_value=old_model,
            new_value=old_model,
            message=f"主 Agent 模型当前已经是 {old_model}。",
            details=_agent_model_details(current_agent_chat_config(), current_planner_model()),
        )

    next_config, error = set_agent_chat_model(session, model)
    if error:
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="failed",
            scope="user",
            target="models.slots.agent_chat.model",
            message=error,
        )
    assert next_config is not None
    return CommandResult(
        command=raw_command.strip(),
        command_id=command_id,
        status="success",
        scope="user",
        changed=True,
        target="models.slots.agent_chat.model",
        old_value=old_model,
        new_value=next_config.get("model"),
        message=f"主 Agent 模型已切换为 {next_config.get('model')}。",
        details=[
            {"label": "Provider", "value": str(next_config.get("provider", provider))},
            {"label": "旧模型", "value": old_model},
            {"label": "新模型", "value": str(next_config.get("model"))},
            {"label": "配置文件", "value": "config.json5 已更新"},
            {"label": "Reload", "value": "runtime_config / agent_models"},
        ],
        rollback_command=f"/config agent.chat.model {old_model}",
    )


def _set_planner_model(session: Session, raw_command: str, command_id: str) -> CommandResult:
    raw_model = _extract_space_arg(raw_command, prefixes=("/config planner.model",))
    if not raw_model:
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="needs_input",
            scope="user",
            target="models.planner.model",
            message="请选择 planner 使用的 DashScope 模型。",
            details=[
                {
                    "label": model,
                    "value": model,
                    "provider": "dashscope",
                    "command": f"/config planner.model {model}",
                }
                for model in planner_model_options()
            ],
            suggestions=[f"/config planner.model {model}" for model in planner_model_options()],
        )

    model = normalize_model_name(raw_model)
    if model is None:
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="failed",
            scope="user",
            target="models.planner.model",
            message="planner.model 不能为空，只能包含模型名称字符。",
        )

    old_model = current_planner_model()
    if model == old_model:
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="noop",
            scope="user",
            target="models.planner.model",
            old_value=old_model,
            new_value=old_model,
            message=f"Planner 模型当前已经是 {old_model}。",
            details=_agent_model_details(current_agent_chat_config(), old_model),
        )

    next_model, error = set_planner_model(session, model)
    if error:
        return CommandResult(
            command=raw_command.strip(),
            command_id=command_id,
            status="failed",
            scope="user",
            target="models.planner.model",
            message=error,
        )
    assert next_model is not None
    return CommandResult(
        command=raw_command.strip(),
        command_id=command_id,
        status="success",
        scope="user",
        changed=True,
        target="models.planner.model",
        old_value=old_model,
        new_value=next_model,
        message=f"Planner 模型已切换为 {next_model}。",
        details=[
            {"label": "Provider", "value": "dashscope"},
            {"label": "旧模型", "value": old_model},
            {"label": "新模型", "value": next_model},
            {"label": "配置文件", "value": "config.json5 已更新"},
            {"label": "Reload", "value": "runtime_config / agent_models"},
        ],
        rollback_command=f"/config planner.model {old_model}",
    )


def _agent_chat_config() -> dict[str, Any]:
    return current_agent_chat_config()


def _agent_model_details(agent_chat: dict[str, Any], planner_model: str) -> list[dict[str, Any]]:
    return [
        {"label": "主聊天模型", "value": f"{agent_chat.get('provider', 'dashscope')} / {agent_chat.get('model', AGENT_CHAT_MODEL)}"},
        {"label": "Planner", "value": f"dashscope / {planner_model}"},
    ]


def _extract_space_arg(raw_command: str, *, prefixes: tuple[str, ...]) -> str:
    stripped = raw_command.strip()
    lowered = stripped.lower()
    for prefix in prefixes:
        normalized_prefix = prefix.lower()
        if lowered == normalized_prefix:
            return ""
        if lowered.startswith(f"{normalized_prefix} "):
            return _strip_wrapping_quotes(stripped[len(prefix) :].strip())
    return ""


def _strip_wrapping_quotes(value: str) -> str:
    stripped = value.strip()
    quote_pairs = [('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"), ("`", "`")]
    for left, right in quote_pairs:
        if stripped.startswith(left) and stripped.endswith(right) and len(stripped) >= 2:
            return stripped[1:-1].strip()
    return stripped


def _parse_bool_arg(value: str) -> bool | None:
    normalized = value.strip().lower()
    true_values = {"true", "1", "yes", "on", "enable", "enabled", "开启", "打开", "启用"}
    false_values = {"false", "0", "no", "off", "disable", "disabled", "关闭", "关掉", "停用"}
    if normalized in true_values:
        return True
    if normalized in false_values:
        return False
    return None


def _boolean_input_result(
    raw_command: str,
    command_id: str,
    *,
    target: str,
    message: str,
    true_label: str,
    false_label: str,
    true_command: str,
    false_command: str,
) -> CommandResult:
    return CommandResult(
        command=raw_command.strip(),
        command_id=command_id,
        status="needs_input",
        scope="user",
        target=target,
        message=message,
        details=[
            {"label": true_label, "value": "true", "command": true_command},
            {"label": false_label, "value": "false", "command": false_command},
        ],
        suggestions=[true_command, false_command],
    )


def _resolve_active_knowledge_spaces(
    session: Session,
    raw_space: str,
    *,
    command_prefix: str,
) -> tuple[list[KnowledgeSpace] | None, CommandResult | None]:
    space_name = raw_space.strip()
    spaces = session.exec(
        select(KnowledgeSpace)
        .where(KnowledgeSpace.status == "active")
        .order_by(KnowledgeSpace.name, KnowledgeSpace.id)
    ).all()
    requested_ids = _parse_space_id_list(space_name)
    if requested_ids:
        matched = [space for space in spaces if space.id in requested_ids]
        matched_ids = {space.id for space in matched}
        missing_ids = [space_id for space_id in requested_ids if space_id not in matched_ids]
        if missing_ids:
            return None, CommandResult(
                command="",
                status="failed",
                scope="conversation",
                target="knowledge.mounts",
                message=f"没有找到可用知识空间 ID：{', '.join(str(item) for item in missing_ids)}。",
            )
        return matched, None

    if space_name.isdigit():
        space_id = int(space_name)
        matched = [space for space in spaces if space.id == space_id]
        if len(matched) == 1:
            return matched, None

    lowered = space_name.lower()
    exact_matches = [space for space in spaces if space.name.lower() == lowered]
    if len(exact_matches) == 1:
        return exact_matches, None
    if len(exact_matches) > 1:
        return None, CommandResult(
            command="",
            status="needs_input",
            scope="conversation",
            target="knowledge.mounts",
            message=f"存在多个同名知识空间「{space_name}」，请改用知识空间 ID。",
            details=_space_details(exact_matches),
            suggestions=[f"{command_prefix} {space.id}" for space in exact_matches if space.id is not None],
        )

    fuzzy_matches = [space for space in spaces if lowered in space.name.lower()]
    if len(fuzzy_matches) == 1:
        return fuzzy_matches, None
    if len(fuzzy_matches) > 1:
        return None, CommandResult(
            command="",
            status="needs_input",
            scope="conversation",
            target="knowledge.mounts",
            message=f"「{space_name}」匹配到多个知识空间，请选择更精确的名称或 ID。",
            details=_space_details(fuzzy_matches),
            suggestions=[f'{command_prefix} "{space.name}"' for space in fuzzy_matches[:5]],
        )

    return None, CommandResult(
        command="",
        status="failed",
        scope="conversation",
        target="knowledge.mounts",
        message=f"没有找到知识空间「{space_name}」。",
    )


def _ready_voice_profiles(session: Session) -> list[VoiceProfile]:
    return session.exec(
        select(VoiceProfile)
        .where(VoiceProfile.status == "ready")
        .order_by(VoiceProfile.name, VoiceProfile.id)
    ).all()


def _resolve_ready_voice_profile(
    session: Session,
    raw_voice: str,
) -> tuple[VoiceProfile | None, CommandResult | None]:
    voice_name = raw_voice.strip()
    ready_profiles = _ready_voice_profiles(session)
    if voice_name.isdigit():
        profile_id = int(voice_name)
        profile = session.get(VoiceProfile, profile_id)
        if profile is None:
            return None, CommandResult(
                command="",
                status="failed",
                scope="user",
                target="elf.voice.default",
                message=f"没有找到声线 ID：{profile_id}。",
            )
        if profile.status != "ready":
            return None, CommandResult(
                command="",
                status="failed",
                scope="user",
                target="elf.voice.default",
                message=f"声线「{profile.name}」当前状态为 {profile.status}，不能设为默认声线。",
            )
        return profile, None

    lowered = voice_name.lower()
    exact_matches = [profile for profile in ready_profiles if profile.name.lower() == lowered]
    if len(exact_matches) == 1:
        return exact_matches[0], None
    if len(exact_matches) > 1:
        return None, CommandResult(
            command="",
            status="needs_input",
            scope="user",
            target="elf.voice.default",
            message=f"存在多个同名声线「{voice_name}」，请改用声线 ID。",
            details=_voice_profile_details(exact_matches),
            suggestions=[f"/config elf.voice.default {profile.id}" for profile in exact_matches if profile.id is not None],
        )

    fuzzy_matches = [profile for profile in ready_profiles if lowered in profile.name.lower()]
    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0], None
    if len(fuzzy_matches) > 1:
        return None, CommandResult(
            command="",
            status="needs_input",
            scope="user",
            target="elf.voice.default",
            message=f"「{voice_name}」匹配到多个声线，请选择具体声线。",
            details=_voice_profile_details(fuzzy_matches),
            suggestions=[f"/config elf.voice.default {profile.id}" for profile in fuzzy_matches if profile.id is not None],
        )

    return None, CommandResult(
        command="",
        status="failed",
        scope="user",
        target="elf.voice.default",
        message=f"没有找到可用声线「{voice_name}」。",
    )


def _voice_profile_details(profiles: list[VoiceProfile]) -> list[dict[str, Any]]:
    return [
        {
            "label": profile.name,
            "value": f"ID {profile.id}",
            "voice_profile_id": profile.id,
            "status": profile.status,
            "remote_voice_id": profile.remote_voice_id,
        }
        for profile in profiles[:10]
    ]


def _mount_details(mounts: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "label": mount.space_name,
            "value": f"ready {mount.ready_document_count}/{mount.document_count}",
            "space_id": mount.space_id,
        }
        for mount in mounts
    ]


def _space_details(spaces: list[KnowledgeSpace]) -> list[dict[str, Any]]:
    return [
        {
            "label": space.name,
            "value": f"ID {space.id}",
            "space_id": space.id,
        }
        for space in spaces[:10]
    ]


def _parse_space_id_list(value: str) -> list[int]:
    normalized = value.replace(",", " ")
    parts = [part for part in normalized.split() if part]
    if len(parts) <= 1 or not all(part.isdigit() for part in parts):
        return []
    return list(dict.fromkeys(int(part) for part in parts))


def _unmounted_active_spaces(session: Session, conversation_id: int) -> list[KnowledgeSpace]:
    mounted_ids = {mount.space_id for mount in list_conversation_knowledge_mounts(session, conversation_id)}
    spaces = session.exec(
        select(KnowledgeSpace)
        .where(KnowledgeSpace.status == "active")
        .order_by(KnowledgeSpace.name, KnowledgeSpace.id)
    ).all()
    return [space for space in spaces if space.id is not None and space.id not in mounted_ids]
