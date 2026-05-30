# 语音工坊第一版设计

本文是 `aliyun-voice-provider.md` 的落地设计，目标是把远程 ASR、实时 TTS、文字声音设计和声线管理接进现有 AiMemo / Memo Elf 架构。

第一版只做阿里云 / DashScope 远程能力，不做本地模型，不下载权重，不启动本地 wrapper。

## 目标

语音系统拆成两条用户路径：

```text
桌面精灵语音对话
  长按说话 -> ASR -> 文本发送给 Memory Chat Graph -> 精灵文字气泡 -> TTS -> 播放

语音工坊
  描述想要的声线 -> agent 整理 voice_prompt -> 创建远端 voice_id -> 试听 -> 设为默认
```

第一版必须做到：

```text
1. 用户可以在桌面精灵里长按说话，并自动发送识别文本。
2. 精灵回复后可以用当前默认声线播放。
3. 用户可以在 /app/workshop/voice 管理声线。
4. 用户可以通过文字设计一个新声线，试听后设为默认。
5. 所有声线都是远端资源或远端参数集合，不对应本地模型文件。
```

## 非目标

第一版不做：

```text
声音复刻上传参考音频
语音边生成边播放的细粒度流式播放
本地 ASR / TTS fallback
多角色 Live2D 绑定
复杂音频剪辑和声纹评测
```

这些放到第二阶段。

## 总体架构

```mermaid
flowchart TD
    Desktop[桌面精灵] -->|audio blob| TranscribeAPI[POST /api/elf/voice/transcribe]
    TranscribeAPI --> ASR[DashScope ASR Provider]
    ASR --> Desktop

    Desktop -->|text| ElfChat[POST /api/elf/chat/stream]
    ElfChat --> MemoryGraph[Memory Chat Graph]
    MemoryGraph --> Desktop

    Desktop -->|bubble text + emoji| SpeakAPI[POST /api/elf/voice/speak]
    SpeakAPI --> ProfileSvc[Voice Profile Service]
    ProfileSvc --> TTS[DashScope TTS Provider]
    TTS --> Desktop

    Workshop[语音工坊] --> ProfileAPI[/api/voice/profiles]
    Workshop --> DesignAPI[POST /api/voice/profiles/design]
    DesignAPI --> PromptAgent[Voice Prompt Agent]
    PromptAgent --> DesignProvider[DashScope Voice Design]
    DesignProvider --> ProfileSvc
```

## 后端模块

新增或恢复以下模块：

```text
backend/app/api/elf_voice.py
  桌面精灵专用语音入口：转写和播放。

backend/app/api/voice_profiles.py
  语音工坊入口：声线 CRUD、试听、创建远端声线、设为默认。

backend/app/services/voice_asr_service.py
  ASR provider 编排。

backend/app/services/voice_tts_service.py
  TTS provider 编排，负责把 profile + emoji + text 合成请求。

backend/app/services/voice_profile_service.py
  VoiceProfile 持久化、默认声线管理、状态流转。

backend/app/services/voice_design_service.py
  voice_prompt 整理、远端 voice_id 创建、错误归一化。

backend/app/providers/dashscope_voice.py
  DashScope ASR / TTS / Voice Design 的 HTTP 或 WebSocket 适配层。
```

`providers` 是外部供应商适配层，不放业务状态。业务状态统一在 service 层。

## 数据模型

新增表 `voice_profiles`。

字段：

```text
id                  int / uuid
name                str
description         str
voice_prompt        str
style_prompt        str
preview_text        str
language            str
speed               float
energy              float
emotion_bias        json
remote_provider     str
remote_model        str
remote_target_model str
remote_voice_id     str | null
source_type         builtin | designed | cloned
status              draft | generating | ready | failed
last_error          str | null
is_active           bool
created_at          datetime
updated_at          datetime
```

约束：

```text
同一时间最多一个 is_active=true。
ready profile 才能设为默认。
designed profile 必须有 voice_prompt。
remote_voice_id 创建失败时 status=failed，并保留 last_error。
```

初始迁移应创建一个默认 builtin profile：

```text
name: 默认精灵声线
source_type: builtin
status: ready
remote_provider: aliyun_dashscope
remote_model: qwen3-tts-instruct-flash-realtime
is_active: true
```

## 配置

`config.json5` 增加：

```json5
{
  "voice": {
    "enabled": true,
    "asr_provider": "aliyun_dashscope",
    "tts_provider": "aliyun_dashscope",
    "voice_design_provider": "aliyun_dashscope",
    "max_audio_mb": 20,
    "language": "auto",
    "aliyun": {
      "asr_model": "qwen3-asr-flash-realtime",
      "tts_model": "qwen3-tts-instruct-flash-realtime",
      "voice_design_model": "qwen-voice-design",
      "voice_design_target_model": "qwen3-tts-vd-realtime-2026-01-15",
      "sample_rate": 48000,
      "timeout_seconds": 120
    }
  }
}
```

`Settings` 中暴露对应字段，环境变量优先：

```text
VOICE_ENABLED
VOICE_ASR_PROVIDER
VOICE_TTS_PROVIDER
VOICE_DESIGN_PROVIDER
VOICE_ALIYUN_ASR_MODEL
VOICE_ALIYUN_TTS_MODEL
VOICE_ALIYUN_VOICE_DESIGN_MODEL
VOICE_ALIYUN_VOICE_DESIGN_TARGET_MODEL
```

密钥继续复用：

```text
DASHSCOPE_API_KEY
DASHSCOPE_BASE_URL
```

## API 设计

### 桌面精灵语音

```text
POST /api/elf/voice/transcribe
Content-Type: multipart/form-data

file: audio/webm | audio/wav | audio/ogg
language?: auto | zh | en | ja | ...
```

返回：

```json
{
  "text": "帮我打开浏览器",
  "language": "zh",
  "duration_ms": 1840,
  "provider": "aliyun_dashscope",
  "model": "qwen3-asr-flash-realtime"
}
```

```text
POST /api/elf/voice/speak
Content-Type: application/json

{
  "text": "我查一下。",
  "emoji": "thinking",
  "profile_id": null
}
```

返回 `audio/mpeg` 或 `audio/wav`。第一版可以一次性返回完整音频；第二阶段再改 SSE / chunked streaming。

### 语音工坊

```text
GET /api/voice/profiles
POST /api/voice/profiles
GET /api/voice/profiles/{id}
PATCH /api/voice/profiles/{id}
DELETE /api/voice/profiles/{id}
POST /api/voice/profiles/{id}/activate
POST /api/voice/profiles/{id}/preview
POST /api/voice/profiles/design
```

`POST /api/voice/profiles/design`：

```json
{
  "description": "温柔、轻快、像陪伴型二次元助手，语速中等，尾音有一点元气。",
  "name_hint": "暖糖",
  "preview_text": "今天也一起把事情慢慢做好吧。"
}
```

返回：

```json
{
  "profile": {
    "id": 12,
    "name": "暖糖",
    "status": "ready",
    "source_type": "designed",
    "remote_voice_id": "voice_xxx"
  },
  "voice_prompt": "一个年轻女性声音，温柔、轻快、亲近...",
  "warnings": []
}
```

如果远端创建很慢，接口可以先返回 `status=generating`，由工坊轮询 profile 状态。第一版优先同步实现，超时再切异步任务。

## Voice Prompt Agent

声音设计不能直接把用户一句话原样塞给 voice design。需要一个轻量 agent，把自然语言整理成稳定 prompt。

输入：

```text
用户描述
当前精灵设定
已有声线 profile
目标语言
试听文本
```

输出结构：

```json
{
  "name": "暖糖",
  "voice_prompt": "年轻女性声音，音色温暖清亮，语速中等偏慢...",
  "style_prompt": "日常陪伴场景，表达自然，轻微微笑感...",
  "preview_text": "今天也一起把事情慢慢做好吧。",
  "rationale": "保留给调试，不直接展示为主要内容"
}
```

约束：

```text
voice_prompt 不超过 2048 字符。
不要包含真实名人、现役声优、未授权角色的仿声要求。
不要承诺生成和某个人完全相同的声音。
如果用户只说“二次元一点”，agent 应主动补足年龄感、语速、情绪底色、使用场景。
```

## 前端设计

新增路由：

```text
/app/workshop/voice
```

`WorkshopPage` 子导航增加“语音”。

新增目录：

```text
frontend/src/features/voice/
  VoiceWorkshop.tsx
  VoiceProfileList.tsx
  VoiceProfileDetail.tsx
  VoiceDesignPanel.tsx
  VoicePreviewPlayer.tsx
  voiceApi.ts
  types.ts

frontend/src/pages/workshop/WorkshopVoicePage.tsx
```

页面布局：

```text
左侧：声线列表
  默认声线
  自定义声线
  状态标识：草稿 / 生成中 / 可用 / 失败

中间：当前声线详情
  名称
  描述
  voice_prompt 摘要
  style_prompt 摘要
  试听文本
  试听按钮
  设为默认按钮

右侧：对话式声音设计
  用户描述输入
  agent 整理后的 prompt 预览
  创建声线
  生成失败时展示错误和重试
```

视觉原则：

```text
不做营销页。
信息密度接近现有 Workshop。
声线卡片要小而清楚，突出状态和默认标记。
prompt 默认折叠，避免一屏全是长文本。
试听按钮必须有 loading、playing、error 三种状态。
```

## 桌面端播放链路

桌面精灵现有长按录音入口应保持：

```text
pointerdown 开始录音
pointerup 停止录音
上传 /api/elf/voice/transcribe
拿到 text 后自动调用 /api/elf/chat/stream
收到精灵气泡后调用 /api/elf/voice/speak
播放音频
```

播放规则：

```text
用户开始新一轮说话时停止旧播放。
TTS 失败只提示“语音播放失败”，文字气泡照常显示。
同一轮多个气泡按顺序播放。
如果 voice.enabled=false，隐藏语音播放按钮，但保留文字对话。
```

## 错误处理

后端错误统一转换成前端可读 detail：

```text
VOICE_DISABLED
DASHSCOPE_API_KEY_MISSING
VOICE_AUDIO_TOO_LARGE
VOICE_UNSUPPORTED_AUDIO_FORMAT
VOICE_ASR_FAILED
VOICE_TTS_FAILED
VOICE_DESIGN_FAILED
VOICE_PROFILE_NOT_READY
VOICE_PROFILE_NOT_FOUND
```

不要把完整供应商 traceback 返回给前端；日志里保留 request id、provider code 和简短错误。

## 测试计划

后端：

```text
配置读取测试
profile CRUD 测试
只能有一个 active profile
未配置 API Key 时返回 503
TTS 使用 active profile 和 emoji modifier
voice design 成功后保存 remote_voice_id
voice design 失败后 status=failed 且 last_error 可见
```

前端：

```text
Workshop 子导航出现语音入口
profile 列表状态渲染
试听按钮 loading / error 状态
设计面板提交后刷新列表
active profile 切换状态更新
```

端到端手测：

```text
1. 长按说话，确认识别文本自动发送。
2. 精灵回复后自动播放默认声线。
3. 进入 /app/workshop/voice 创建文字设计声线。
4. 试听新声线。
5. 设为默认后回到桌面精灵，确认使用新声线播放。
```

## 实现顺序

```text
1. 配置和 Settings：加 voice 配置，不接外部 API。
2. 数据模型和 profile CRUD：先让工坊能管理本地 profile。
3. TTS provider：实现 /api/elf/voice/speak 和 profile preview。
4. ASR provider：实现 /api/elf/voice/transcribe。
5. 前端 /app/workshop/voice：列表、详情、试听、设为默认。
6. Voice Design：接 qwen-voice-design，生成 designed profile。
7. 桌面精灵串联：长按语音输入 + 回复播放。
```

这个顺序保证每一步都能独立验证，避免一次把 ASR、TTS、工坊和精灵 UI 全绑在一起导致排障困难。
