# Voice API

Voice API 支撑桌面精灵语音对话和语音工坊。当前实现统一使用阿里百炼 / DashScope 远程 ASR、TTS 和 Voice Design，不要求本地部署语音模型。

## 精灵语音模式

```http
GET /api/elf/voice/mode
```

```http
PUT /api/elf/voice/mode
Content-Type: application/json

{
  "enabled": true
}
```

语音模式开启后，桌面精灵显示“按住说话”。该开关当前保存在后端进程内，重启后默认关闭。

## 语音转文本

```http
POST /api/elf/voice/transcribe?language=auto
Content-Type: multipart/form-data

file: audio/webm | audio/wav | audio/ogg
```

响应：

```json
{
  "text": "帮我总结一下刚才的内容",
  "language": "zh",
  "duration_ms": 1800,
  "provider": "aliyun_dashscope",
  "model": "qwen3-asr-flash"
}
```

## 精灵气泡转语音

```http
POST /api/elf/voice/speak
Content-Type: application/json

{
  "text": "我查一下。",
  "emoji": "thinking",
  "profile_id": 1
}
```

响应为音频二进制，`Content-Type` 由供应商返回结果决定。`profile_id` 可选，未传时使用当前默认声线。

## 声线列表

```http
GET /api/voice/profiles
```

## 创建 / 更新 / 删除声线

```http
POST /api/voice/profiles
PATCH /api/voice/profiles/{profile_id}
DELETE /api/voice/profiles/{profile_id}
POST /api/voice/profiles/{profile_id}/activate
```

只有 `ready` 状态的声线适合作为默认声线。

## 文字声音设计

```http
POST /api/voice/profiles/design
Content-Type: application/json

{
  "description": "温柔、带一点活泼感的二次元少女声线",
  "style_prompt": "自然聊天，不要播音腔",
  "preview_text": "晚上好呀，今天也慢慢来。"
}
```

响应会创建或返回一个 Voice Profile，并保存远端 `voice_id` 或失败原因。语音工坊会用这个结果支持试听和设为默认。

## 试听

```http
POST /api/voice/profiles/{profile_id}/preview
Content-Type: application/json

{
  "text": "今天也一起把事情慢慢做好吧。",
  "emoji": "soft"
}
```

响应为音频二进制。

## 配置

`config.json5`：

```json5
"voice": {
  "enabled": true,
  "asr_provider": "aliyun_dashscope",
  "tts_provider": "aliyun_dashscope",
  "voice_design_provider": "aliyun_dashscope",
  "max_audio_mb": 20,
  "language": "auto",
  "aliyun": {
    "base_url": "https://dashscope.aliyuncs.com",
    "asr_model": "qwen3-asr-flash",
    "tts_model": "qwen3-tts-instruct-flash",
    "voice_design_model": "qwen-voice-design",
    "voice_design_target_model": "qwen3-tts-vd-2026-01-26",
    "sample_rate": 48000,
    "timeout_seconds": 120
  }
}
```

密钥复用：

```text
DASHSCOPE_API_KEY
```
