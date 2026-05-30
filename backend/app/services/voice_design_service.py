from __future__ import annotations

import json

from fastapi import HTTPException, status
from langchain_openai import ChatOpenAI
from sqlmodel import Session

from app.core.config import settings
from app.providers.dashscope_voice import DashScopeVoiceError, DashScopeVoiceProvider
from app.schemas.voice import VoiceDesignRequest, VoiceDesignResponse
from app.services.voice_profile_service import save_designed_profile, _to_profile_read


DEFAULT_PREVIEW_TEXT = "今天也一起把事情慢慢做好吧。"


def design_voice_profile(session: Session, payload: VoiceDesignRequest) -> VoiceDesignResponse:
    if not settings.voice_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "VOICE_DISABLED", "message": "Voice module is disabled."},
        )

    prompt_plan = _build_voice_prompt(payload)
    remote_voice_id = ""
    status_value = "ready"
    last_error = ""
    warnings: list[str] = []
    try:
        remote_voice_id = DashScopeVoiceProvider().design_voice(
            voice_prompt=prompt_plan["voice_prompt"],
            target_model=settings.voice_aliyun_voice_design_target_model,
            name=prompt_plan["name"],
            preview_text=prompt_plan["preview_text"],
            language=payload.language,
        )
    except DashScopeVoiceError as exc:
        status_value = "failed"
        last_error = exc.message
        warnings.append(f"{exc.code}: {exc.message}")

    profile = save_designed_profile(
        session,
        name=prompt_plan["name"],
        description=payload.description,
        voice_prompt=prompt_plan["voice_prompt"],
        style_prompt=prompt_plan["style_prompt"],
        preview_text=prompt_plan["preview_text"],
        remote_voice_id=remote_voice_id,
        status_value=status_value,
        last_error=last_error,
    )
    return VoiceDesignResponse(profile=_to_profile_read(profile), voice_prompt=profile.voice_prompt, warnings=warnings)


def _build_voice_prompt(payload: VoiceDesignRequest) -> dict[str, str]:
    fallback = _fallback_prompt_plan(payload)
    if not settings.dashscope_api_key:
        return fallback

    llm = ChatOpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model=settings.chat_model or "qwen-plus",
        temperature=0.4,
    )
    messages = [
        (
            "system",
            "你是 Memo Elf 的语音工坊设计师。把用户的自然语言声线需求整理成 JSON，"
            "字段为 name、voice_prompt、style_prompt、preview_text。"
            "voice_prompt 不超过 2048 字符，不要要求模仿名人、声优或未授权角色。",
        ),
        (
            "user",
            json.dumps(
                {
                    "description": payload.description,
                    "name_hint": payload.name_hint or "",
                    "preview_text": payload.preview_text or DEFAULT_PREVIEW_TEXT,
                    "language": payload.language,
                },
                ensure_ascii=False,
            ),
        ),
    ]
    try:
        response = llm.invoke(messages)
        content = str(response.content)
        parsed = _parse_json_object(content)
    except Exception:
        return fallback
    if not parsed:
        return fallback
    return {
        "name": _safe_text(str(parsed.get("name") or fallback["name"]), 120),
        "voice_prompt": _safe_text(str(parsed.get("voice_prompt") or fallback["voice_prompt"]), 2048),
        "style_prompt": _safe_text(str(parsed.get("style_prompt") or fallback["style_prompt"]), 1000),
        "preview_text": _safe_text(str(parsed.get("preview_text") or fallback["preview_text"]), 500),
    }


def _fallback_prompt_plan(payload: VoiceDesignRequest) -> dict[str, str]:
    name = payload.name_hint.strip() if payload.name_hint else "自定义声线"
    preview_text = payload.preview_text.strip() if payload.preview_text else DEFAULT_PREVIEW_TEXT
    voice_prompt = (
        f"中文角色声音。用户希望的声音特征：{payload.description.strip()}。"
        "表达自然，咬字清楚，避免机械感，适合桌面陪伴助手日常对话。"
    )
    return {
        "name": _safe_text(name, 120),
        "voice_prompt": _safe_text(voice_prompt, 2048),
        "style_prompt": "温暖、自然、亲近，语速中等，情绪表达克制但有陪伴感。",
        "preview_text": _safe_text(preview_text, 500),
    }


def _parse_json_object(content: str) -> dict | None:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        stripped = stripped[start : end + 1]
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _safe_text(value: str, limit: int) -> str:
    return value.strip()[:limit]
