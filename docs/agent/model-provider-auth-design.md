# 模型 Provider 与凭据解耦设计草案

本文是 [聊天模型 Provider 适配设计](./model-provider-adapter.md) 的下一阶段补充，重点解决当前
`models.agent_chat` 过于单一的问题：模型槽位、provider 定义和 API Key 来源被绑在同一个对象里，
导致用户切换模型时必须匹配固定环境变量，也无法优雅支持本地模型、代理网关、自定义 key 名称和后续多模型槽位。

## 当前问题

当前 Step 4 的模型配置已经能工作，但它是一个过渡实现：

```json5
{
  "models": {
    "agent_chat": {
      "provider": "dashscope",
      "model": "qwen3.5-plus",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key_env": "DASHSCOPE_API_KEY",
      "capabilities": {
        "tool_calling": true,
        "json_mode": true,
        "vision": false
      }
    }
  }
}
```

这个结构的问题：

1. `agent_chat` 同时承载模型选择、provider 定义、鉴权配置和能力声明，职责太重。
2. provider 的默认值写死在 `backend/app/services/model_config_service.py`，用户无法通过配置新增 provider。
3. API Key 只支持固定环境变量名，虽然可以手动改 `api_key_env`，但没有完整的指令和校验体系。
4. 本地 OpenAI-compatible 服务也会被要求配置 `LOCAL_LLM_API_KEY`，但很多本地服务本来不需要密钥。
5. planner、vision、embedding、ASR/TTS 后续都需要模型配置，继续堆字段会让 `config.json5` 变成一团。
6. 斜杠指令当前切 provider 时会写入一整份 provider 默认配置，容易覆盖用户手动调过的 `base_url`、`extra_body` 或能力声明。

## OpenClaw 可借鉴点

OpenClaw 的核心经验不是“支持很多 provider”，而是把模型系统拆成稳定的三层：

1. **模型引用**
   - 运行时选择的是 `provider/model` 或 slot 指向。
   - 会话和 UI 可以只关心当前 slot 选择了哪个 provider 和 model。

2. **Provider 定义**
   - provider 单独定义 `baseUrl`、`api`、模型目录、能力、请求 header、transport override 等。
   - 内置 provider 和用户自定义 provider 走同一种读取路径。

3. **凭据解析**
   - API Key 不直接绑死在模型 slot 上。
   - 支持 env、文件、exec、auth profile、provider entry apiKey、本地 no-auth marker 等来源。
   - 本地 provider 如果指向 localhost/private network，可以合成一个 no-auth 标记，不强制用户填 key。

AiMemo 不需要一上来复制 OpenClaw 的完整复杂度，但应该吸收这条边界：**slot 只负责“用谁”，provider 负责“怎么连”，auth 负责“凭据从哪里来”。**

## 配置文件结构借鉴

OpenClaw 的配置结构也值得参考。它不是简单把所有内容拆成很多文件，而是把不同生命周期、敏感级别和归属边界的内容分开：

```text
~/.openclaw/openclaw.json
  主配置：agents、models、tools、channels 等用户可读配置。

~/.openclaw/credentials/
  channel/provider 账号状态和敏感凭据状态。

~/.openclaw/agents/<agentId>/agent/auth-profiles.json
  每个 agent 的模型认证 profile。

state/openclaw.sqlite
agents/<agentId>/agent/openclaw-agent.sqlite
  全局运行态、插件状态、per-agent 状态和缓存。
```

这说明“拆配置”的核心不是物理文件数量，而是职责边界：

1. 主配置文件只放可读、可解释、可迁移的产品配置。
2. 凭据和账号状态不要混进普通主配置。
3. 运行中状态不要写配置文件。
4. per-agent / per-slot / per-provider 的配置要有明确归属。
5. 配置修改要有 schema、校验、doctor 修复和 last-known-good 兜底。

AiMemo 当前把大量配置集中在仓库根目录 `config.json5`，短期开发效率高，但随着模型、知识库、精灵、语音、工坊、命令系统扩展，会逐渐出现几个问题：

1. 用户配置、本地运行态、开发模板混在同一个文件。
2. `/config` 指令写入根配置时容易误伤手工注释或示例配置。
3. 真实部署时 `config.json5` 容易被误提交，尤其当未来支持更多 provider 凭据时风险更高。
4. 前端设置页、斜杠指令和手工编辑会争用同一个大文件，冲突恢复会越来越复杂。
5. 配置没有按“产品配置 / 凭据引用 / 运行态状态 / 缓存”拆边界，后续很难做热更新和迁移。

### AiMemo 建议分层

短期不要立刻大迁移，可以先采用兼容式分层：

```text
AiMemo/
  config.example.json5
    提交到仓库的配置模板，不包含本机密钥和用户私有路径。

  config.json5
    当前本机主配置。短期继续兼容，长期建议作为开发/本机覆盖配置。

  data/config/runtime.json5
    可选：UI 和 /config 指令写入的本机持久配置。

  data/config/model-auth.json5
    可选：只放模型凭据引用和 auth profile 元信息，不放明文 key。

  data/ai_note.db
    业务数据、任务、运行态状态、会话状态和缓存。
```

长期可以考虑用户级目录：

```text
~/.aimemo/
  config.json5
    用户主配置。

  credentials/
    provider/channel 外部账号状态。

  agents/
    main/
      auth-profiles.json
      runtime.sqlite
```

这个长期结构更适合多人 clone 项目、桌面端安装包和跨仓库使用，但迁移成本更高，不能一步到位。

### 配置归属原则

建议按以下边界决定放哪里：

| 类型 | 建议落点 | 示例 |
| --- | --- | --- |
| 产品默认值 | `config.example.json5` / 代码默认值 | 默认端口、默认模型、默认上下文预算 |
| 用户可读配置 | `config.json5` 或 `~/.aimemo/config.json5` | models、voice、knowledge、local_operator |
| 指令/UI 持久配置 | `data/config/runtime.json5` 或主配置受控写入 | 默认声线、当前模型 slot、挂载偏好 |
| 凭据引用 | `models.providers.*.api_key` / `model-auth.json5` | `{source:"env", id:"DASHSCOPE_API_KEY"}` |
| 明文凭据 | 不建议；后续如支持必须高风险确认 | API Key、OAuth token |
| 运行中状态 | SQLite | 精灵是否思考、后台任务、job 状态 |
| 缓存和索引 | SQLite / data 子目录 | 模型列表缓存、向量索引、OCR 缓存 |

这也意味着：精灵“当前是否正在说话”、后台任务状态、某轮 graph 是否进行中，都不应该写进 `config.json5`。它们属于 runtime state，应进入 SQLite 或已有状态表。

### 渐进迁移策略

第一阶段仍保留 `config.json5` 作为事实来源，但新增配置读取抽象：

```text
ProjectConfig
  读取根目录 config.json5

RuntimeConfig
  读取 data/config/runtime.json5 或数据库中的用户覆盖项

EffectiveConfig
  合并 defaults + ProjectConfig + RuntimeConfig + env
```

写入策略：

1. 用户手工改的稳定配置继续允许在 `config.json5`。
2. `/config` 和设置页优先写入受控 runtime config。
3. 高风险配置，例如 provider auth、base_url、Local Operator 权限，必须结构化确认。
4. API Key 只写引用，不写明文。
5. `aimemo doctor` 负责检测旧字段、冲突字段、无效配置和可迁移配置。

这样可以把“用户手工配置”和“应用运行中修改”分开，减少命令写入对主配置文件的破坏性。

## 目标

下一阶段的目标：

1. 让用户可以自定义 provider，而不是只能选后端硬编码 provider。
2. 让用户可以自定义 API Key 来源，例如环境变量名，而不是必须叫 `DASHSCOPE_API_KEY` / `OPENAI_API_KEY`。
3. 允许本地 OpenAI-compatible provider 配置为无 API Key。
4. 让 `agent_chat`、`planner`、`vision`、`embedding` 等模型槽位共用一套 provider/auth 解析逻辑。
5. `/config` 指令、设置页和后端模型工厂复用同一套验证和写入服务。
6. 不把真实 API Key 写入普通对话、长期记忆、日志和普通 command result。
7. 为配置文件分层预留边界，逐步区分主配置、运行态覆盖、凭据引用和 SQLite 状态。

非目标：

1. 第一版不实现 OAuth、文件 secret、exec secret 的完整能力。
2. 第一版不做 provider 在线模型目录拉取。
3. 第一版不自动测试远端模型可用性；模型调用失败仍由下一次真实调用暴露。
4. 第一版不把 embedding、ASR/TTS 全部迁移到新结构，只预留结构。
5. 第一版不强制迁移所有配置文件到 `~/.aimemo`，避免影响现有开发和部署。

## 建议配置结构

建议把 `models.agent_chat` 迁移为 `models.slots.agent_chat`，并新增 `models.providers`：

```json5
{
  "models": {
    "slots": {
      "agent_chat": {
        "provider": "dashscope",
        "model": "qwen3.5-plus",
        "temperature": 0.2,
        "streaming": true,
        "required_capabilities": ["tool_calling", "streaming"]
      },
      "planner": {
        "provider": "dashscope",
        "model": "qwen-turbo",
        "temperature": 0.2,
        "streaming": false
      }
    },
    "providers": {
      "dashscope": {
        "label": "DashScope",
        "api": "openai_chat_completions",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": {
          "source": "env",
          "id": "DASHSCOPE_API_KEY"
        },
        "capabilities": {
          "tool_calling": true,
          "json_mode": true,
          "vision": false
        },
        "extra_body": {
          "enable_thinking": false
        },
        "models": ["qwen3.5-plus", "qwen-plus", "qwen-max", "qwen-turbo"]
      },
      "local": {
        "label": "Local OpenAI Compatible",
        "api": "openai_chat_completions",
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": {
          "source": "none"
        },
        "capabilities": {
          "tool_calling": true,
          "json_mode": true,
          "vision": false
        },
        "models": ["qwen3", "qwen2.5", "llama3.1"]
      }
    }
  }
}
```

### Slot 字段

`models.slots.<slot>` 表达“这个业务槽位使用哪个模型”：

```text
provider              provider id
model                 模型 id
temperature           可选，默认 0.2
streaming             可选，默认按 slot 决定
required_capabilities 该 slot 必须具备的能力
extra_body            可选，slot 层覆盖或追加 provider extra_body
```

第一批 slot：

```text
agent_chat  主 ReAct 对话模型，必须支持 tool_calling
planner     轻量规划和 JSON 判断模型
vision      图片/附件理解模型，后续迁移
embedding   向量化模型，后续迁移
```

### Provider 字段

`models.providers.<provider>` 表达“这个 provider 怎么连接”：

```text
label        UI 展示名
api          适配器类型，例如 openai_chat_completions
base_url     上游 API 地址
api_key      凭据引用，不建议明文
capabilities provider 默认能力
extra_body   provider 默认请求扩展参数
models       推荐模型列表，不作为唯一合法模型来源
```

第一版只实现 `api=openai_chat_completions`，也就是继续使用 `ChatOpenAI`。

### API Key 引用

第一版建议支持两类：

```json5
{ "source": "env", "id": "DASHSCOPE_API_KEY" }
{ "source": "none" }
```

含义：

- `env`：从指定环境变量或 `.env` / settings 中读取。
- `none`：不需要 API Key，仅允许本地 provider 或用户显式确认过的 provider 使用。

后续可扩展：

```json5
{ "source": "file", "path": "...", "key": "/providers/dashscope/apiKey" }
{ "source": "exec", "command": "..." }
```

真实 API Key 明文写入 `config.json5` 暂不推荐。即使后续支持，也必须作为高风险配置，需要结构化确认、日志脱敏、结果脱敏。

## 解析优先级

模型工厂应走统一解析流程：

```text
1. 读取 slot：models.slots.agent_chat
2. 读取 provider：models.providers[slot.provider]
3. 校验 provider api 是否支持当前模型工厂
4. 合并 provider 默认参数和 slot 覆盖参数
5. 解析 api_key
6. 校验 required_capabilities
7. 构造 ChatOpenAI / 其他模型 client
8. 以 slot + provider + model + base_url + auth ref + 参数生成缓存 key
```

兼容旧配置时：

```text
如果存在 models.slots.agent_chat：
  使用新结构。

否则如果存在 models.agent_chat：
  通过兼容转换读取为临时 slot + provider。

否则：
  使用 DashScope qwen3.5-plus 默认值。
```

运行时只应该消费 canonical 结构；旧结构可以在读取边界转换，也可以通过 `aimemo doctor --fix` 或迁移函数写回新结构。短期为了不打断用户，可以保留只读兼容；后续再做迁移提示。

## 指令设计

### Slot 切换

```text
/config agent.chat.provider <provider>
/config agent.chat.model <model>
/config planner.provider <provider>
/config planner.model <model>
```

行为：

- provider 缺失时，`needs_input` 只展示已配置且满足能力要求的 provider。
- model 缺失时，`needs_input` 展示当前 provider 的推荐模型列表。
- 用户输入不存在的 provider：直接 `failed`，不弹候选。
- 用户输入未列入推荐列表但格式合法的 model：允许写入，因为很多 provider 模型列表会变化。
- 切换 slot 只修改 `models.slots.<slot>`，不覆盖 `models.providers.<provider>`。

### Provider 管理

```text
/config provider.add
/config provider.base_url <provider> <url>
/config provider.auth <provider>
/config provider.models <provider>
/config provider.remove <provider>
```

第一版优先实现：

```text
/config provider.auth <provider>
/config provider.base_url <provider> <url>
```

`/config provider.auth <provider>` 参数缺失时走 `needs_input`：

```text
1. 使用环境变量
2. 本地服务无密钥
```

选择“使用环境变量”后继续要求用户输入环境变量名，例如 `DASHSCOPE_API_KEY`。这一步不经过 Agent 自由发挥，仍由 command router 管理。

`source=none` 的限制：

- 默认只允许 `localhost`、`127.0.0.1`、`::1`、局域网私有地址。
- 如果用户要给公网 base_url 设置 no-auth，必须二次确认。

## 设置页设计

设置页和 `/config` 必须共用后端 service：

```text
model_provider_config_service
  list_slots()
  list_providers()
  resolve_slot(slot)
  set_slot_provider(slot, provider)
  set_slot_model(slot, model)
  set_provider_auth(provider, auth_ref)
  set_provider_base_url(provider, base_url)
  validate_provider_for_slot(provider, slot)
```

前端只渲染后端返回的 schema、options、status，不自己维护 provider 白名单。

展示信息：

```text
Slot:
  当前 provider / model / capability 状态 / reload 状态

Provider:
  base_url
  api
  api_key 状态：ready / missing / none / invalid_ref
  capabilities
  推荐模型
```

不要展示真实 key，只展示来源：

```text
env:DASHSCOPE_API_KEY ready
env:DEEPSEEK_API_KEY missing
none local only
```

## 落地技术设计

下一步实现不应直接在现有 `model_config_service.py` 上继续堆字段，而应该新增一层解析器，让旧结构和新结构都汇入同一个 runtime-ready model config。

### 后端模块边界

建议模块拆分：

```text
backend/app/services/model_provider_config_service.py
  负责读取 models.slots / models.providers，合并内置默认 provider，校验 provider、slot 和 auth。

backend/app/agent/model.py
  只负责把 resolved slot config 构造成 LangChain ChatModel，并做缓存。

backend/app/agent/commands/router.py
  只负责解析 /config 指令、构造 command result、调用 service。

backend/app/core/config.py
  只提供受控 JSON5 读写能力，不承载 provider 业务规则。

backend/app/services/runtime_config_service.py
  继续负责 runtime config / project config 写入；后续再迁移到 data/config/runtime.json5。
```

现有 `model_config_service.py` 可以作为兼容层逐步退场：

```text
Phase 1:
  model_config_service.py 调用新的 resolver，但保留旧函数名，降低 command router 改动。

Phase 2:
  command router 直接调用 model_provider_config_service.py。

Phase 3:
  删除或缩小旧 service，只保留测试辅助函数。
```

### 核心数据结构

建议先用 dataclass，不急着引入复杂 schema 框架：

```python
@dataclass(frozen=True)
class ModelAuthRef:
    source: Literal["env", "none"]
    id: str | None = None


@dataclass(frozen=True)
class ModelProviderConfig:
    provider: str
    label: str
    api: str
    base_url: str
    api_key: ModelAuthRef
    capabilities: dict[str, bool]
    models: tuple[str, ...]
    extra_body: dict[str, Any]


@dataclass(frozen=True)
class ModelSlotConfig:
    slot: str
    provider: str
    model: str
    temperature: float
    streaming: bool
    required_capabilities: tuple[str, ...]
    extra_body: dict[str, Any]


@dataclass(frozen=True)
class ResolvedModelSlot:
    slot: str
    provider: str
    model: str
    api: str
    base_url: str
    api_key_source: str
    api_key: str
    temperature: float
    streaming: bool
    capabilities: dict[str, bool]
    extra_body: dict[str, Any]
```

其中 `ResolvedModelSlot.api_key` 是运行时内部字段，不能进入 command result、日志和前端 API。

### 内置 provider registry

代码里仍保留内置 provider 默认值，但它们应该作为 fallback，而不是唯一事实源：

```text
built_in_providers()
  dashscope
  openai
  deepseek
  openrouter
  siliconflow
  local_openai_compatible
```

合并规则：

```text
1. 先加载内置 provider 默认值。
2. 再加载 config.json5 / runtime config 中的 models.providers。
3. 用户配置按 provider id 覆盖内置字段。
4. capabilities 和 extra_body 做浅合并。
5. models 列表如果用户显式配置，则以用户配置为准；否则使用内置推荐列表。
```

这样用户只想改 base_url 或 api_key 时，不需要复制一整份 provider 配置。

示例：

```json5
{
  "models": {
    "providers": {
      "dashscope": {
        "api_key": { "source": "env", "id": "MY_DASHSCOPE_KEY" }
      }
    }
  }
}
```

### Slot 解析规则

`resolve_model_slot(slot)` 的优先级：

```text
1. 读取 models.slots.<slot>
2. 如果 slot=agent_chat 且不存在新结构，读取旧 models.agent_chat 并转换
3. 如果 slot=planner 且不存在新结构，读取旧 models.planner.model 并使用 dashscope provider
4. 如果仍不存在，使用代码默认值
5. 读取并合并 provider 配置
6. 解析 auth ref
7. 校验 slot required_capabilities
8. 返回 ResolvedModelSlot
```

旧结构转换规则：

```text
models.agent_chat.provider  -> slot.provider
models.agent_chat.model     -> slot.model
models.agent_chat.base_url  -> provider.base_url
models.agent_chat.api_key_env -> provider.api_key = {source:"env", id: api_key_env}
models.agent_chat.capabilities -> provider.capabilities
models.agent_chat.extra_body -> provider.extra_body
```

注意：旧结构只做读取兼容，新的 `/config` 写入应逐步写入 `models.slots` 和 `models.providers`。

### Auth 解析规则

第一版只支持：

```text
env
none
```

解析行为：

```text
source=env:
  1. 校验 id 是合法环境变量名。
  2. 优先读 os.environ[id]。
  3. 再尝试 settings.<lowercase id>，兼容当前 pydantic-settings 字段。
  4. 缺失时返回 missing，不抛出真实 key。

source=none:
  1. 校验 provider base_url 是否是 local/private 允许范围。
  2. 通过时返回一个内部 synthetic key，例如 "aimemo-local-no-key"。
  3. 该 synthetic key 只用于满足 ChatOpenAI 构造参数，不展示给用户。
```

`source=none` 的本地地址判定建议第一版只允许：

```text
localhost
127.0.0.1
0.0.0.0
::1
host.docker.internal
*.local
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16
```

公网 no-key 暂不开放；如果以后开放，必须单独高风险确认。

### 模型工厂改造

`backend/app/agent/model.py` 的目标形态：

```text
get_agent_chat_model()
  -> get_chat_model("agent_chat")

get_planner_chat_model()
  -> get_chat_model("planner")

get_chat_model(slot)
  -> resolve_model_slot(slot)
  -> validate_resolved_slot(slot)
  -> build ChatOpenAI / future adapter
```

第一版 adapter 只支持：

```text
api = openai_chat_completions
```

也就是继续使用 `ChatOpenAI`，不同 provider 只影响：

```text
base_url
api_key
model
temperature
streaming
extra_body
```

缓存 key 必须包含：

```text
slot
provider
model
api
base_url
api_key_source
temperature
streaming
extra_body
```

不把真实 API Key 放进 cache key，避免在调试信息中泄漏。

### 配置写入路径

短期写入仍可复用 `set_persistent_runtime_config()`，但写入目标要从旧路径切到新路径：

```text
/config agent.chat.provider dashscope
  写 models.slots.agent_chat.provider = "dashscope"

/config agent.chat.model qwen-plus
  写 models.slots.agent_chat.model = "qwen-plus"

/config provider.auth dashscope env MY_DASHSCOPE_KEY
  写 models.providers.dashscope.api_key = {source:"env", id:"MY_DASHSCOPE_KEY"}

/config provider.base_url local http://127.0.0.1:11434/v1
  写 models.providers.local.base_url = "http://127.0.0.1:11434/v1"
```

`config.py` 的受控写入白名单需要扩展：

```text
models.slots
models.slots.agent_chat
models.slots.agent_chat.provider
models.slots.agent_chat.model
models.slots.planner
models.slots.planner.provider
models.slots.planner.model
models.providers
models.providers.<provider>.api_key
models.providers.<provider>.base_url
```

白名单不能无限开放任意路径。对 `<provider>` 这种动态路径，需要在 service 层先校验 provider id，再调用受控写入函数。

### Command Router 行为

现有经验要继续保持：

```text
needs_input:
  只有参数缺失或需要用户补全时才返回选项。

failed:
  用户输入错误、provider 不存在、auth 缺失、base_url 不合法时直接失败，不弹候选。

success:
  展示旧值、新值、reload 状态、rollback_command。

noop:
  当前已经是目标值时不写配置。
```

新增命令建议分两步：

```text
Step A:
  /config provider.auth <provider>
  /config provider.base_url <provider> <url>

Step B:
  /config planner.provider <provider>
  /config provider.add
  /config provider.models <provider>
```

第一版可以不做 `/config provider.add`，因为用户只要在 `models.providers.<id>` 写一个新 provider，resolver 就能识别。后续 UI 再提供图形化创建。

### 设置页 API 设计

后端可以先暴露只读 API，设置页后续复用：

```text
GET /api/config/models
```

返回：

```json
{
  "slots": [
    {
      "slot": "agent_chat",
      "provider": "dashscope",
      "model": "qwen3.5-plus",
      "status": "ready",
      "required_capabilities": ["tool_calling", "streaming"]
    }
  ],
  "providers": [
    {
      "provider": "dashscope",
      "label": "DashScope",
      "api": "openai_chat_completions",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "auth": {
        "source": "env",
        "id": "DASHSCOPE_API_KEY",
        "status": "ready"
      },
      "capabilities": {
        "tool_calling": true,
        "json_mode": true,
        "vision": false
      },
      "models": ["qwen3.5-plus", "qwen-plus"]
    }
  ]
}
```

如果暂时不做设置页，也应该先让 command result 使用同一套 `describe_model_config()` 输出，避免 UI 和 command 两套状态判断。

### 测试设计

后端测试至少覆盖：

```text
resolver:
  默认配置 -> dashscope agent_chat
  旧 models.agent_chat -> 兼容读取
  新 models.slots + models.providers -> 优先读取新结构
  用户 provider 覆盖内置 api_key env
  source=none + localhost -> ready
  source=none + public url -> invalid
  agent_chat provider 缺 tool_calling -> invalid

model factory:
  agent_chat 使用新 resolver
  planner 使用 slot 解析
  cache key 不包含真实 api key
  reset_agent_models 后重新解析

command router:
  缺 provider 参数 -> needs_input
  非法 provider -> failed
  缺 env key -> failed
  provider.auth env 写入 -> success
  provider.auth none + local base_url -> success
  provider.auth none + public base_url -> failed
  slot provider/model 写入新路径

config writer:
  写 models.slots 时保留注释
  写 models.providers 时跳过注释里的示例 key
  不允许写未授权路径
```

## 安全边界

1. API Key 不进入普通聊天消息、长期记忆、graph 可见文本和日志。
2. command result 只展示 key 来源和状态，不展示 key 内容。
3. 修改 provider auth、base_url、删除 provider 属于 medium/high 风险，需要结构化确认。
4. `source=none` 默认只允许本地地址。
5. 对公网 `base_url` 做基本 URL 校验，拒绝空 host、非 http/https、包含用户名密码的 URL。
6. provider id、slot id、env var id 都需要严格字符集校验。
7. 切换 `agent_chat` 时必须校验 `tool_calling`，否则 ReAct agent 会失效。

## 迁移计划

### Phase 1：Resolver 和兼容读取

- 保留当前 `models.agent_chat`。
- 新增 `model_provider_config_service.py`。
- 增加 `resolve_model_slot(slot)` 内部 API，没有新结构时继续走旧结构。
- 增加内置 provider registry 和 `env` / `none` auth ref 解析。
- 测试旧配置、新配置、默认配置三种路径。

### Phase 2：模型工厂接入

- `get_agent_chat_model()` 改为通过 `resolve_model_slot("agent_chat")` 构造。
- `get_planner_chat_model()` 改为通过 `resolve_model_slot("planner")` 构造。
- 保持第一版 adapter 只支持 `openai_chat_completions`。
- 更新 warmup、cache key、timing 字段，避免泄漏真实 API Key。

### Phase 3：指令和设置页

- `/config agent.chat.provider` 改为只写 slot。
- 新增 `/config provider.auth`、`/config provider.base_url`。
- 增加只读 `GET /api/config/models`，设置页展示 slot/provider/auth 状态。
- 所有写入走统一 service，并调用 `reset_agent_models()`。

### Phase 4：配置写入分层

- 保留 `config.json5` 兼容读取。
- `/config` 和设置页优先写入新路径：`models.slots.*` / `models.providers.*`。
- 评估是否把 UI/指令写入迁移到 `data/config/runtime.json5` 或继续写主配置。
- command result 对旧配置给出温和提示，不阻断运行。

### Phase 5：Doctor 迁移工具

- `aimemo doctor` 检测旧 `models.agent_chat`。
- `aimemo doctor --fix` 可迁移为 `models.slots.agent_chat` + `models.providers.<provider>`。
- 检测无效 auth ref、无效 base_url、source=none 指向公网、缺少 required capabilities。

### Phase 6：扩展到更多能力

- vision slot 迁移。
- embedding provider 迁移。
- ASR/TTS provider 迁移。
- 文件/exec secret ref 作为可选高级能力。

## 需要继续讨论的问题

1. `planner` 是否应该允许非 DashScope provider，还是继续固定 DashScope 以控制成本和延迟？
2. 是否允许用户把真实 API Key 明文写入 `config.json5`？如果允许，是否必须高风险确认？
3. 本地无 key provider 的判断范围是只允许 loopback，还是允许私有网段？
4. `models.providers.<provider>.models` 是严格 allowlist，还是只是 UI 推荐列表？
5. provider 的 `capabilities` 是完全由用户声明，还是内置 provider 强制覆盖关键能力？
6. 旧 `models.agent_chat` 兼容保留多久，什么时候迁移为 doctor-only？

## 建议结论

AiMemo 应该采用轻量版 OpenClaw 结构：

```text
slot 负责业务用途
provider 负责连接方式
auth ref 负责凭据来源
```

短期先做 `env` 和 `none` 两种凭据来源，就能解决当前最痛的两个问题：自定义 API Key 名称，以及本地模型不该强制 API Key。等这条主线稳定后，再考虑文件 secret、exec secret、auth profile 和 provider 动态模型目录。
