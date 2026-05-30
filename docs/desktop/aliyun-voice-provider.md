# 阿里云远程语音能力接入设计

本文记录 Memo Elf 语音模块的新方向：不再本地部署 SenseVoice、VoxCPM2 或其他大模型，统一改为调用阿里云 / DashScope 远程 ASR 与 TTS 能力。

## 背景

本地 VoxCPM2 路线已经验证过可行性边界：

```text
模型体积约 5GB
Windows CPU-only 加载会触发 native access violation
8GB 显存机器在 Windows 桌面环境下显存余量不足
本地依赖链包含 torch / torchcodec / safetensors / CUDA，维护成本高
```

AiMemo 的目标是桌面精灵产品，不应该要求普通用户维护大型语音模型、CUDA 环境或本地推理服务。

## 新目标

第一版远程语音模块负责：

```text
语音转文本：用户长按说话 -> 上传音频 -> 阿里 ASR -> 文本 -> 精灵对话
文本转语音：精灵气泡文本 -> 当前声线配置 -> 阿里 TTS -> 音频 -> 桌面播放
语音工坊：通过文字设计音色、保存声线描述、风格、情绪映射，并调用远程 TTS 试听
```

所有调用复用当前项目已经配置的阿里百炼 / DashScope API Key：

```text
DASHSCOPE_API_KEY
DASHSCOPE_BASE_URL
```

不新增本地模型下载，不新增本地 GPU/CPU 推理服务。

## 删除范围

以下能力不再保留：

```text
SenseVoice / FunASR 本地 STT
VoxCPM2 本地 TTS wrapper
scripts/install-voice.*
scripts/install-voxcpm2.*
scripts/start-voxcpm2.*
backend/app/voice/voxcpm2_wrapper.py
本地 Hugging Face / ModelScope 语音模型缓存
data/models/
data/venvs/
```

如果后续需要本地模式，应作为独立高级插件重新设计，而不是默认产品路径。

## 模块边界

后端保留语音服务抽象，但 provider 改成远程阿里实现：

```text
backend/app/services/voice_asr_service.py
  transcribe_audio(...)

backend/app/services/voice_tts_service.py
  synthesize_bubble_voice(...)

backend/app/services/voice_profile_service.py
  声线 profile CRUD
```

API 层建议保留：

```text
POST /api/elf/voice/transcribe
POST /api/elf/voice/speak
GET  /api/voice/profiles
POST /api/voice/profiles
PATCH /api/voice/profiles/{id}
POST /api/voice/profiles/{id}/activate
DELETE /api/voice/profiles/{id}
```

这样桌面端和语音工坊无需关心底层是本地模型还是远程 API。

## 配置设计

`config.json5` 建议改为：

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
      "voice_clone_model": "qwen-voice-enrollment",
      "voice_clone_target_model": "qwen3-tts-vc-realtime",
      "sample_rate": 48000,
      "timeout_seconds": 120,
    },
  },
}
```

具体模型名以实现时官方文档为准；本文先固定产品语义和默认路线。

## 模型路线

远程语音能力不要混成一个“大 TTS 模型”概念，应拆成三类：

```text
实时默认说话
  精灵把普通回复变成语音，要求低延迟、可流式、稳定。

文字设计音色
  用户在语音工坊里描述角色声线，由模型生成可复用 voice_id。

声音复刻
  用户上传参考音频，生成接近参考音色的 voice_id。
```

第一阶段主线采用 Qwen3-TTS 系列：

```text
ASR
  qwen3-asr-flash-realtime

默认实时 TTS
  qwen3-tts-instruct-flash-realtime

文字设计音色
  qwen-voice-design
  target_model: qwen3-tts-vd-realtime-2026-01-15

声音复刻
  qwen-voice-enrollment
  target_model: qwen3-tts-vc-realtime
```

CosyVoice v3.5 Plus 暂时作为备选高级通道，而不是第一阶段默认主线。

原因：

```text
Qwen3-TTS 的声音设计描述空间更大，voice_prompt 可到 2048 字符，更适合由 agent 和用户一起打磨复杂角色声线。
CosyVoice 声音设计更适合快速原型，voice_prompt 通常限制在 500 字符以内。
Qwen3-TTS 系列可以覆盖默认实时说话、文字设计音色和声音复刻，产品路线更统一。
CosyVoice v3.5 Plus 主要面向声音设计 / 复刻，不适合作为普通精灵说话的默认模型。
```

因此第一版语音工坊不以“上传一个模型”或“训练一个模型”为中心，而以“生成并管理远端 voice_id”为中心。
用户每创建一个新声线，通常不是生成一个新的本地模型，而是在阿里侧创建一个可复用的音色资源。

## 声线 Profile

继续保留 Voice Profile 概念，但它不再代表本地模型或 LoRA 权重，而是远程 TTS 参数集合。

字段建议：

```text
id
name
description
voice_prompt
style_prompt
language
speed
energy
emotion_bias
remote_voice_id
remote_provider
remote_model
remote_target_model
source_type
status
preview_text
is_active
created_at
updated_at
```

`remote_voice_id` 用于保存阿里 TTS 的远端音色 id；如果目标模型只支持 prompt-style 控制，则允许为空。

字段语义：

```text
source_type
  builtin       使用阿里模型内置音色或默认音色。
  designed      通过文字声音设计生成。
  cloned        通过参考音频复刻生成。

status
  draft         只保存了本地描述，还没有创建远端 voice_id。
  generating    正在创建远端音色。
  ready         可用于试听和精灵播放。
  failed        创建失败，需要展示错误并允许重试。
```

语音工坊第一版优先支持 `builtin` 和 `designed`；`cloned` 作为第二阶段。

## 语音工坊交互原则

语音工坊不是传统表单，而是“用户和 agent 一起设计角色声线”的工作台。

第一阶段应支持：

```text
1. 用户用自然语言描述想要的声音。
2. agent 将描述整理成结构化 voice_prompt。
3. 用户可以继续说“更活泼一点 / 少一点机械感 / 像深夜电台”。
4. 工坊保存多版草稿，允许试听、命名、设为默认。
5. 创建成功后保存 remote_voice_id，后续精灵说话直接复用。
```

不要把 voice_prompt 暴露成唯一入口。高级用户可以编辑 prompt，但普通用户应该通过对话式调音完成。

## 情绪映射

桌面精灵气泡已有 `emoji` 字段。TTS provider 需要把它转成声音风格：

```text
soft      -> gentle, warm, close
happy     -> cheerful, bright, smiling tone
shy       -> shy, soft, slightly nervous
angry     -> cute tsundere, mildly annoyed, not aggressive
thinking  -> thoughtful, slower, quiet
worried   -> concerned, careful, soft
sleepy    -> sleepy, low energy
curious   -> curious, lively
success   -> pleased, warm, quietly excited
error     -> apologetic, careful
```

最终 TTS 请求由三部分组成：

```text
active_voice_profile.voice_prompt
active_voice_profile.style_prompt
emoji style modifier
bubble.text
```

## 桌面播放策略

继续使用气泡级播放队列：

```text
每个 bubble 生成一段音频
TTS 请求可并发
播放必须按 bubble 顺序
用户开始新一轮输入时停止旧音频并清空队列
TTS 失败不阻塞文字气泡显示
```

## 第一阶段实现计划

```text
1. 清理本地语音模型、wrapper、安装脚本和依赖。
2. 保留 API 形态，新增 aliyun_dashscope ASR provider，默认 qwen3-asr-flash-realtime。
3. 新增 aliyun_dashscope TTS provider，默认 qwen3-tts-instruct-flash-realtime，返回浏览器可播放音频。
4. 桌面端继续使用现有长按录音与播放队列，TTS 失败不影响文字回复。
5. 语音工坊支持 Voice Profile CRUD、试听、设为默认。
6. 语音工坊接入 qwen-voice-design，创建 designed voice profile。
7. 文档补齐模型、计费、超时、错误处理和隐私说明。
```

第二阶段：

```text
1. 接入 qwen-voice-enrollment，支持参考音频声音复刻。
2. 增加多版本 voice_prompt 历史和回滚。
3. 支持流式 TTS 播放，降低精灵回复开始说话的延迟。
4. 评估是否加入 CosyVoice v3.5 Plus 作为高级备选 provider。
```

## 错误处理

```text
API Key 缺失
  -> 返回 503，提示配置 DASHSCOPE_API_KEY。

远程 ASR/TTS 超时
  -> 返回 503，保留文本气泡，不打断对话。

音频格式不支持
  -> 后端返回 400，并说明支持的上传格式。

余额不足 / 限流
  -> 返回 503，detail 中保留阿里错误码和简短解释。
```

## 不再做

```text
不再下载 VoxCPM2 / SenseVoice 模型
不再创建本地语音专用 venv
不再要求 CUDA / GPU / WSL
不再维护本地 TTS wrapper 端口 8765
```
