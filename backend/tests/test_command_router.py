import json

from app.agent.commands.registry import get_command_by_input, list_command_schemas
from app.agent.commands.router import execute_slash_command
from app.models.chat_message import ChatMessage
from app.models.knowledge import ConversationKnowledgeMount, KnowledgeSpace
from app.models.voice_profile import VoiceProfile
from app.schemas.conversation import ConversationCreate
from app.services.conversation_service import create_conversation
from app.services.elf_voice_mode_service import set_elf_voice_mode_enabled
from app.services.runtime_config_service import get_runtime_config
from sqlmodel import select


def test_command_registry_exposes_readonly_commands(session):
    commands = list_command_schemas(session)

    assert get_command_by_input(session, "/config show") is not None
    assert [command.command for command in commands[:4]] == [
        "/config show",
        "/agent status",
        "/elf status",
        "/knowledge mounts",
    ]


def test_execute_slash_command_persists_command_result_messages(session):
    conversation = create_conversation(session, ConversationCreate(title="指令测试"))

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command="/config show",
    )

    assert response.result.status == "success"
    assert response.result.source == "command_router"
    messages = session.exec(select(ChatMessage).order_by(ChatMessage.id)).all()
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].content == "/config show"
    assert "```aimemo-command-result" in messages[1].content
    payload = messages[1].content.split("```aimemo-command-result", 1)[1].split("```", 1)[0]
    parsed = json.loads(payload)
    assert parsed["type"] == "command_result"
    assert parsed["command_id"] == "config.show"


def test_unknown_slash_command_is_deterministic_failure(session):
    conversation = create_conversation(session, ConversationCreate(title="未知指令"))

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command="/configg",
    )

    assert response.result.status == "failed"
    assert response.result.message == "未知指令：/configg"
    assert response.result.suggestions == []


def test_elf_status_reports_workshop_voice_mode_separately(session):
    set_elf_voice_mode_enabled(False, session)
    conversation = create_conversation(session, ConversationCreate(title="精灵状态"))

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command="/elf status",
    )

    details = {item["label"]: item["value"] for item in response.result.details}
    assert details["语音服务能力"] in {"enabled", "disabled"}
    assert details["精灵语音对话"] == "disabled"

    set_elf_voice_mode_enabled(True, session)
    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command="/elf status",
    )
    details = {item["label"]: item["value"] for item in response.result.details}
    assert details["精灵语音对话"] == "enabled"
    set_elf_voice_mode_enabled(False, session)


def test_mount_knowledge_command_mounts_space_by_exact_name(session):
    conversation = create_conversation(session, ConversationCreate(title="挂载知识空间"))
    _create_space(session, "AiMemo 文档")

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command='/mount knowledge "AiMemo 文档"',
    )

    assert response.result.status == "success"
    assert response.result.changed is True
    assert response.result.command_id == "mount.knowledge"
    assert response.result.rollback_command == "/unmount knowledge 1"
    mounts = session.exec(select(ConversationKnowledgeMount)).all()
    assert len(mounts) == 1
    assert mounts[0].conversation_id == conversation.id


def test_mount_knowledge_command_is_noop_when_already_mounted(session):
    conversation = create_conversation(session, ConversationCreate(title="重复挂载"))
    space = _create_space(session, "技术资料")
    execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command=f"/mount knowledge {space.id}",
    )

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command=f"/mount knowledge {space.id}",
    )

    assert response.result.status == "noop"
    assert response.result.changed is False
    mounts = session.exec(select(ConversationKnowledgeMount)).all()
    assert len(mounts) == 1


def test_unmount_knowledge_command_removes_existing_mount(session):
    conversation = create_conversation(session, ConversationCreate(title="卸载知识空间"))
    space = _create_space(session, "项目资料")
    execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command=f"/mount knowledge {space.id}",
    )

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command='/unmount knowledge "项目资料"',
    )

    assert response.result.status == "success"
    assert response.result.changed is True
    assert response.result.rollback_command == "/mount knowledge 1"
    mounts = session.exec(select(ConversationKnowledgeMount)).all()
    assert mounts == []


def test_mount_knowledge_command_needs_input_when_space_missing(session):
    conversation = create_conversation(session, ConversationCreate(title="缺少参数"))
    _create_space(session, "个人知识")

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command="/mount knowledge",
    )

    assert response.result.status == "needs_input"
    assert response.result.message == "请选择要挂载到当前对话的知识空间。"
    assert response.result.details[0]["label"] == "个人知识"


def test_mount_knowledge_command_needs_input_for_ambiguous_partial_name(session):
    conversation = create_conversation(session, ConversationCreate(title="模糊匹配"))
    _create_space(session, "项目 A")
    _create_space(session, "项目 B")

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command="/mount knowledge 项目",
    )

    assert response.result.status == "needs_input"
    assert "匹配到多个知识空间" in response.result.message
    assert [item["label"] for item in response.result.details] == ["项目 A", "项目 B"]


def test_mount_knowledge_missing_arg_only_lists_unmounted_spaces(session):
    conversation = create_conversation(session, ConversationCreate(title="只显示未挂载"))
    mounted = _create_space(session, "已挂载")
    unmounted = _create_space(session, "未挂载")
    execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command=f"/mount knowledge {mounted.id}",
    )

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command="/mount knowledge",
    )

    assert response.result.status == "needs_input"
    assert [item["space_id"] for item in response.result.details] == [unmounted.id]
    assert response.result.suggestions == [f"/mount knowledge {unmounted.id}"]


def test_unmount_knowledge_missing_arg_only_lists_mounted_spaces(session):
    conversation = create_conversation(session, ConversationCreate(title="只显示已挂载"))
    mounted = _create_space(session, "已挂载")
    _create_space(session, "未挂载")
    execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command=f"/mount knowledge {mounted.id}",
    )

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command="/unmount knowledge",
    )

    assert response.result.status == "needs_input"
    assert [item["space_id"] for item in response.result.details] == [mounted.id]
    assert response.result.suggestions == [f"/unmount knowledge {mounted.id}"]


def test_mount_and_unmount_knowledge_commands_accept_multiple_space_ids(session):
    conversation = create_conversation(session, ConversationCreate(title="批量挂载"))
    first = _create_space(session, "第一空间")
    second = _create_space(session, "第二空间")

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command=f"/mount knowledge {first.id},{second.id}",
    )

    assert response.result.status == "success"
    assert response.result.changed is True
    assert response.result.rollback_command == f"/unmount knowledge {first.id},{second.id}"
    mounts = session.exec(select(ConversationKnowledgeMount).order_by(ConversationKnowledgeMount.space_id)).all()
    assert [mount.space_id for mount in mounts] == [first.id, second.id]

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command=f"/unmount knowledge {first.id},{second.id}",
    )

    assert response.result.status == "success"
    assert response.result.rollback_command == f"/mount knowledge {first.id},{second.id}"
    assert session.exec(select(ConversationKnowledgeMount)).all() == []


def test_mount_knowledge_invalid_space_fails_without_candidates(session):
    conversation = create_conversation(session, ConversationCreate(title="错误知识空间"))
    _create_space(session, "有效空间")

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command="/mount knowledge 不存在的空间",
    )

    assert response.result.status == "failed"
    assert response.result.message == "没有找到知识空间「不存在的空间」。"
    assert response.result.details == []
    assert response.result.suggestions == []


def test_config_elf_enabled_updates_runtime_config(session):
    conversation = create_conversation(session, ConversationCreate(title="精灵开关"))

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command="/config elf.enabled false",
    )

    assert response.result.status == "success"
    assert response.result.changed is True
    assert response.result.target == "elf.enabled"
    assert response.result.rollback_command == "/config elf.enabled true"
    assert get_runtime_config(session, "elf.enabled") is False

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command="/config elf.enabled false",
    )

    assert response.result.status == "noop"


def test_config_elf_enabled_missing_arg_needs_input(session):
    conversation = create_conversation(session, ConversationCreate(title="精灵开关缺参"))

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command="/config elf.enabled",
    )

    assert response.result.status == "needs_input"
    assert [item["label"] for item in response.result.details] == ["开启", "关闭"]
    assert response.result.suggestions == ["/config elf.enabled true", "/config elf.enabled false"]


def test_config_elf_enabled_invalid_arg_fails_without_candidates(session):
    conversation = create_conversation(session, ConversationCreate(title="精灵开关错误"))

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command="/config elf.enabled maybe",
    )

    assert response.result.status == "failed"
    assert response.result.details == []
    assert response.result.suggestions == []


def test_config_elf_voice_mode_updates_runtime_config(session):
    conversation = create_conversation(session, ConversationCreate(title="语音对话"))
    set_elf_voice_mode_enabled(False, session)

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command="/config elf.voice.mode true",
    )

    assert response.result.status == "success"
    assert response.result.target == "elf.voice.mode"
    assert get_runtime_config(session, "elf.voice.mode") is True


def test_config_elf_default_voice_switches_active_ready_profile(session):
    conversation = create_conversation(session, ConversationCreate(title="默认声线"))
    old_profile = _create_voice_profile(session, "旧声线", is_active=True)
    new_profile = _create_voice_profile(session, "星野声线")

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command=f"/config elf.voice.default {new_profile.id}",
    )

    assert response.result.status == "success"
    assert response.result.target == "elf.voice.default"
    assert response.result.rollback_command == f"/config elf.voice.default {old_profile.id}"
    profiles = session.exec(select(VoiceProfile).order_by(VoiceProfile.id)).all()
    assert [(profile.id, profile.is_active) for profile in profiles] == [
        (old_profile.id, False),
        (new_profile.id, True),
    ]


def test_config_elf_default_voice_missing_arg_lists_ready_profiles(session):
    conversation = create_conversation(session, ConversationCreate(title="默认声线缺参"))
    ready = _create_voice_profile(session, "可用声线")
    _create_voice_profile(session, "失败声线", status_value="failed")

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command="/config elf.voice.default",
    )

    assert response.result.status == "needs_input"
    assert [item["voice_profile_id"] for item in response.result.details] == [ready.id]
    assert response.result.suggestions == [f"/config elf.voice.default {ready.id}"]


def test_config_elf_default_voice_not_ready_fails_without_candidates(session):
    conversation = create_conversation(session, ConversationCreate(title="默认声线不可用"))
    failed_profile = _create_voice_profile(session, "失败声线", status_value="failed")

    response = execute_slash_command(
        session,
        conversation_id=conversation.id,
        raw_command=f"/config elf.voice.default {failed_profile.id}",
    )

    assert response.result.status == "failed"
    assert response.result.details == []
    assert response.result.suggestions == []


def _create_space(session, name: str) -> KnowledgeSpace:
    space = KnowledgeSpace(name=name, status="active")
    session.add(space)
    session.commit()
    session.refresh(space)
    return space


def _create_voice_profile(
    session,
    name: str,
    *,
    is_active: bool = False,
    status_value: str = "ready",
) -> VoiceProfile:
    profile = VoiceProfile(
        name=name,
        remote_model="qwen3-tts",
        remote_voice_id=f"voice-{name}",
        status=status_value,
        is_active=is_active,
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile
