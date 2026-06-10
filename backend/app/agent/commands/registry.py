from __future__ import annotations

from sqlmodel import Session, select

from app.agent.commands.schemas import CommandArg, CommandOption, CommandSchema, CommandVisibility
from app.models.knowledge import KnowledgeSpace
from app.models.voice_profile import VoiceProfile


_COMMANDS: list[CommandSchema] = [
    CommandSchema(
        id="config.show",
        command="/config show",
        title="查看当前配置",
        description="查看模型、精灵、语音和当前知识空间挂载状态。",
        aliases=["/config", "/settings"],
        category="配置",
        scope="turn",
        risk="low",
        executor="show_config",
        result_view="config_status",
    ),
    CommandSchema(
        id="agent.status",
        command="/agent status",
        title="查看 Agent 状态",
        description="查看主聊天模型、planner、工具和运行时能力。",
        aliases=["/status"],
        category="Agent",
        scope="turn",
        risk="low",
        executor="agent_status",
        result_view="agent_status",
    ),
    CommandSchema(
        id="elf.status",
        command="/elf status",
        title="查看精灵状态",
        description="查看精灵语音对话、语音能力和默认声线状态。",
        category="精灵",
        scope="turn",
        risk="low",
        executor="elf_status",
        result_view="elf_status",
    ),
    CommandSchema(
        id="knowledge.mounts",
        command="/knowledge mounts",
        title="查看知识空间挂载",
        description="查看当前对话已经挂载的知识空间。",
        aliases=["/mounts"],
        category="知识空间",
        scope="conversation",
        risk="low",
        executor="knowledge_mounts",
        result_view="knowledge_mounts",
    ),
    CommandSchema(
        id="mount.knowledge",
        command="/mount knowledge <space>",
        title="挂载知识空间",
        description="将知识空间挂载到当前对话。",
        category="知识空间",
        args=[
            CommandArg(
                name="space",
                type="knowledge_space",
                required=True,
                placeholder="知识空间名称或 ID",
            )
        ],
        scope="conversation",
        risk="low",
        executor="mount_knowledge",
        result_view="config_change",
    ),
    CommandSchema(
        id="unmount.knowledge",
        command="/unmount knowledge <space>",
        title="卸载知识空间",
        description="从当前对话移除已挂载的知识空间。",
        aliases=["/umount knowledge <space>"],
        category="知识空间",
        args=[
            CommandArg(
                name="space",
                type="knowledge_space",
                required=True,
                placeholder="知识空间名称或 ID",
            )
        ],
        scope="conversation",
        risk="low",
        executor="unmount_knowledge",
        result_view="config_change",
    ),
    CommandSchema(
        id="config.elf.voice_mode",
        command="/config elf.voice.mode <true|false>",
        title="开启或关闭精灵语音对话",
        description="控制精灵是否进入持续语音对话模式，不代表 ASR/TTS 服务能力。",
        category="精灵",
        args=[
            CommandArg(
                name="value",
                type="boolean",
                required=True,
                placeholder="true 或 false",
                options=[
                    CommandOption(id="true", label="开启语音对话", value=True, description="允许持续语音交流。"),
                    CommandOption(id="false", label="关闭语音对话", value=False, description="只保留文本交互。"),
                ],
            )
        ],
        scope="user",
        risk="low",
        executor="set_elf_voice_mode",
        reload=["runtime_config", "elf_voice_state"],
        result_view="config_change",
    ),
    CommandSchema(
        id="config.elf.voice_default",
        command="/config elf.voice.default <voice>",
        title="设置精灵默认声线",
        description="切换精灵文本转语音时使用的默认声线。",
        category="精灵",
        args=[
            CommandArg(
                name="voice",
                type="voice_profile",
                required=True,
                placeholder="声线名称或 ID",
            )
        ],
        scope="user",
        risk="low",
        executor="set_elf_default_voice",
        reload=["elf_voice_state"],
        result_view="config_change",
    ),
]


def list_command_schemas(session: Session) -> list[CommandSchema]:
    """Return command schemas enriched with current visibility policy."""

    spaces = session.exec(
        select(KnowledgeSpace)
        .where(KnowledgeSpace.status == "active")
        .order_by(KnowledgeSpace.name, KnowledgeSpace.id)
    ).all()
    has_spaces = any(space.id is not None for space in spaces)
    space_options = [
        CommandOption(
            id=f"knowledge-space-{space.id}",
            label=space.name,
            value=space.id,
            description=f"知识空间 ID: {space.id}",
        )
        for space in spaces
        if space.id is not None
    ]
    ready_voice_profiles = session.exec(
        select(VoiceProfile)
        .where(VoiceProfile.status == "ready")
        .order_by(VoiceProfile.name, VoiceProfile.id)
    ).all()
    voice_options = [
        CommandOption(
            id=f"voice-profile-{profile.id}",
            label=profile.name,
            value=profile.id,
            description=f"{profile.remote_model or 'voice'} / {profile.remote_voice_id or '未绑定远端声线'}",
        )
        for profile in ready_voice_profiles
        if profile.id is not None
    ]
    items: list[CommandSchema] = []
    for command in _COMMANDS:
        item = command.model_copy(deep=True)
        if item.id in {"mount.knowledge", "unmount.knowledge"} and item.args:
            item.args[0].options = space_options
        if item.id == "config.elf.voice_default" and item.args:
            item.args[0].options = voice_options
        if item.id in {"mount.knowledge", "unmount.knowledge"} and not has_spaces:
            item.visibility = CommandVisibility(
                state="disabled",
                reason="暂无可挂载知识空间。请先在知识库中创建知识空间。",
            )
        if item.id == "config.elf.voice_default" and not voice_options:
            item.visibility = CommandVisibility(
                state="disabled",
                reason="暂无可用声线。请先在语音工坊创建或修复 ready 状态的声线。",
            )
        items.append(item)
    return items


def get_command_by_input(session: Session, raw_input: str) -> CommandSchema | None:
    normalized = normalize_command(raw_input)
    for command in list_command_schemas(session):
        candidates = [command.command, *command.aliases]
        for candidate in candidates:
            normalized_candidate = normalize_command(candidate)
            if "<" in normalized_candidate:
                prefix = normalized_candidate.split("<", 1)[0].strip()
                if normalized == prefix or normalized.startswith(f"{prefix} "):
                    return command
                continue
            if normalized_candidate == normalized:
                return command
    return None


def suggest_commands(session: Session, raw_input: str, *, limit: int = 4) -> list[str]:
    normalized = normalize_command(raw_input)
    tokens = [part for part in normalized.split(" ") if part]
    suggestions: list[str] = []
    for command in list_command_schemas(session):
        haystacks = [command.command, command.title, command.description, *command.aliases]
        searchable = " ".join(haystacks).lower()
        if not tokens or any(token in searchable for token in tokens):
            suggestions.append(command.command)
        if len(suggestions) >= limit:
            break
    if suggestions:
        return suggestions
    return [command.command for command in list_command_schemas(session)[:limit]]


def normalize_command(value: str) -> str:
    return " ".join(value.strip().split()).lower()
