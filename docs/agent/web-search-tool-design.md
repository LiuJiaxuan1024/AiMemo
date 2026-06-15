# Web Search 工具设计草案

本文档定义 AiMemo / Memo Elf 给 Memory Chat Agent 增加联网搜索能力的第一版设计。目标是让 agent 在需要最新公共信息、官方资料或网页来源时可以受控联网，同时保持 AiMemo 的本地优先和隐私边界。

## 背景

当前 Memory Chat Agent 已经具备三类检索能力：

- 个人笔记检索：L3 cheap recall / 向量检索。
- 挂载知识库检索：L3.5 mounted knowledge search / `knowledge_search`。
- 本地文件检索：Local Operator 的 `search_files` / `search_text`。

这些能力都面向本地数据或用户主动导入的数据。它们无法回答以下问题：

- 今天或最近发生了什么。
- 某个产品、模型、库、API 的当前版本、价格、限制或官方说明。
- 用户要求“查一下网上”“找官方文档”“帮我搜资料”。
- 本地知识库明显过期，需要联网核验。

因此需要增加 Web Search 工具，但它必须是可控、可审计、可关闭的联网能力。

## 目标

1. Agent 可以在明确需要公共互联网信息时调用搜索工具。
2. 搜索结果必须带来源 URL，最终回答不能伪造“已搜索”。
3. 默认保护本地隐私：个人笔记、本地文件、对话细节不应无提示外发。
4. 搜索 provider 可替换，第一版实现不绑定到单一厂商。
5. 搜索调用可配置开关、调用上限、缓存和超时。
6. 前端可以用工具卡片展示搜索过程和引用来源。

## 非目标

- 第一版不做浏览器自动化。
- 第一版不读取需要登录的网页。
- 第一版不把网页内容自动写入知识库。
- 第一版不做大规模爬虫、站点镜像或批量采集。
- 第一版不做图片、视频、社交媒体专项搜索。
- 第一版不让 agent 通过 `exec_command` 自行 curl / 爬网页绕过 Web Search 策略。

## 产品原则

### 本地优先

当问题可以由个人笔记、挂载知识库或本地文件回答时，优先使用本地上下文，不主动联网。

典型例子：

```text
用户：我之前记过的阿里云 OSS 方案是什么？
行为：优先查个人笔记/对话记忆/知识库，不调用 web_search。
```

### 最新性优先

当问题包含当前性或变化性时，可以主动联网：

```text
最新 / 当前 / 今天 / 今年 / 现在
价格 / 计费 / 限额 / API 版本
新闻 / 发布 / changelog / release notes
官方文档 / 官网说明
```

### 隐私确认

如果搜索 query 可能包含用户隐私、项目路径、未公开代码、个人笔记原文、聊天私密内容，应先调用 `request_user_input` 确认。

```text
用户：把我这段笔记里的病历信息拿去网上查一下。
行为：必须先确认是否允许外发该查询。
```

### 来源可见

只要回答使用了联网信息，最终回答必须包含来源 URL。没有成功工具 observation 时，不能说“我查了网上”。

## 工具分层

第一版设计两个工具。

### `web_search`

用途：调用搜索 provider，返回结构化搜索结果。

输入：

```json
{
  "query": "string",
  "max_results": 5,
  "freshness": "any | day | week | month | year",
  "locale": "zh-CN",
  "site": "optional domain filter"
}
```

输出：

```json
{
  "ok": true,
  "tool_name": "web_search",
  "data": {
    "provider": "tavily",
    "query": "阿里云 OSS 计费 官方",
    "results": [
      {
        "title": "string",
        "url": "https://example.com/page",
        "snippet": "string",
        "source_domain": "example.com",
        "published_at": "2026-06-01T00:00:00Z",
        "rank": 1
      }
    ],
    "cached": false
  }
}
```

### `web_fetch`

用途：读取指定 URL 的正文，做网页正文抽取和截断。`web_fetch` 不负责发现网页，只负责核验具体来源。

输入：

```json
{
  "url": "https://example.com/page",
  "max_chars": 12000
}
```

输出：

```json
{
  "ok": true,
  "tool_name": "web_fetch",
  "data": {
    "url": "https://example.com/page",
    "title": "string",
    "text": "extracted readable text",
    "content_type": "text/html",
    "fetched_at": "2026-06-15T12:00:00Z",
    "truncated": false
  }
}
```

## Provider 选型

当前推荐 provider 抽象 + Tavily Search 实现。原因是 Tavily 返回结构化
`answer/results/usage`，比 DashScope 搜索增强更接近 AiMemo 需要的“可审计来源列表”。
DashScope / 百炼保留为可选 provider，方便没有 Tavily key 时回退到已有阿里账号体系。

### 阿里体系验证结果

2026-06-15 使用当前项目 `.env` 中的 `DASHSCOPE_API_KEY` 做过最小探测：

- OpenAI-compatible `/chat/completions` 接受 `enable_search=true`。
- DashScope 原生 generation endpoint 也接受 `parameters.enable_search=true`。
- 原生 endpoint 的 `usage.plugins.search.count=1` 可以证明本轮触发了搜索插件。
- 本次最小探测没有稳定拿到结构化搜索结果列表；官方文档说明 DashScope 执行搜索时可返回 `search_info`，实现时应优先解析该字段。
- 通过提示词要求“只输出 JSON sources”时，模型可以返回可解析 JSON；当响应没有 `search_info` 时，可用这一路作为兜底，但来源仍需后续 `web_fetch` 核验。

DashScope provider 不应把阿里能力建模成传统搜索引擎 API，而应建模为：

```text
aliyun_dashscope_search
  输入 query
  调用 qwen-plus / qwen-turbo + enable_search=true
  优先读取 search_info.search_results
  若无 search_info，则要求模型输出结构化 conclusion + sources
  对最终 sources.url 再调用 web_fetch 做来源核验
```

它的限制是：阿里搜索增强第一跳返回的是“模型整理后的搜索结果”，不是原始 SERP。因此当前主 provider 切换为 Tavily，DashScope 只作为回退。

候选：

| Provider | 优点 | 风险 |
| --- | --- | --- |
| Tavily | 面向 LLM/RAG 搜索，返回 `answer`、结构化 `results` 和 `usage.credits`，更适合 agent 消费 | 需要新增 `TAVILY_API_KEY`，要评估国内可达性和配额 |
| Brave Search API | 面向 agent/chatbot 场景，Web/News/Images 等端点清晰，结果结构化 | 需要新增 `BRAVE_SEARCH_API_KEY`，配置面变复杂 |
| Aliyun DashScope Search-Augmented Generation | 复用 `DASHSCOPE_API_KEY`，不增加新账号；与当前模型 provider 一致；可解析 `search_info` | 不是传统 SERP API，来源仍建议二次 fetch 核验 |
| SerpAPI | 搜索类型丰富，兼容多搜索引擎结果 | 成本和配额需要控制 |
| Google Programmable Search | 文档成熟 | 新客户可用性和长期迁移风险较高 |

阿里云 OpenSearch 不作为第一版公网搜索 provider。它更适合给业务数据、站内内容、商品或知识库建检索服务，不等同于通用互联网搜索 API。

### 阿里联网搜索计费快照

以下是 2026-06-15 核验官方文档时的设计参考，真实账单以阿里云控制台和官方价格页为准。

- 模型调用费用：联网搜索的网页内容会拼接到提示词中，因此会增加输入 Token，并按所选模型的标准 token 价格计费。
- 搜索策略费用：中国内地地域下，`turbo` / `max` 策略从 2026-02-27 00:00 起正式计费，官方文档当前写明每 1000 次分别为 3 元 / 4 元。
- `agent` 策略：官方文档当前写明中国内地和全球部署范围每 1000 次 4 元，国际部署范围每 1000 次 73.392381 元。
- `agent_max` 策略：包含联网搜索与网页抓取费用，官方文档当前写明中国内地部署范围搜索每 1000 次 4 元，国际部署范围每 1000 次 73.392381 元；网页抓取工具处于限时免费状态。
- 限流：官方文档当前写明联网搜索按阿里云主账号维度限流 15 RPS；超出时 API 不一定报错，但可能跳过搜索链路。

DashScope 回退模式应默认使用 `turbo`，配合 `daily_limit`、缓存和前端可见的调用提示。实现前仍必须重新核验官方页面，不把价格写死到业务逻辑中。

## 配置

`config.json5` 保存可提交的默认开关，不保存密钥。

```json5
{
  "web_search": {
    "enabled": false,
    "provider": "tavily",
    "model": "",
    "strategy": "basic",
    "max_results": 5,
    "timeout_seconds": 12,
    "fetch_timeout_seconds": 15,
    "daily_limit": 100,
    "cache_ttl_seconds": 86400,
    "require_confirmation_for_private_queries": true,
    "allowed_domains": [],
    "blocked_domains": []
  }
}
```

密钥放环境变量：

```text
TAVILY_API_KEY
# Optional fallback providers:
# DASHSCOPE_API_KEY
# BRAVE_SEARCH_API_KEY
# SERPAPI_API_KEY
```

默认 provider 需要 `TAVILY_API_KEY`。其他 key 只在用户显式切换 fallback provider 时需要。

运行时配置接口应返回：

```json
{
  "web_search": {
    "enabled": true,
    "provider": "tavily",
    "configured": true,
    "daily_limit": 100,
    "remaining_today": 93
  }
}
```

## 后端落点

建议目录：

```text
backend/app/providers/web_search/
  __init__.py
  provider.py
  tavily.py
  aliyun_dashscope.py
  brave.py        # fallback，可后置
  factory.py

backend/app/services/web_search_service.py
backend/app/schemas/web_search.py
backend/app/api/web_search.py
```

`provider.py` 定义抽象接口：

```python
class WebSearchProvider(Protocol):
    def search(self, request: WebSearchRequest) -> WebSearchResponse:
        ...
```

`web_search_service.py` 负责：

- 读取配置和密钥状态。
- 做每日上限检查。
- 做 query 风险分类。
- 做缓存读写。
- 调用 provider。
- 归一化 provider 结果。
- 记录审计和错误。

`api/web_search.py` 可选，仅用于配置测试和调试：

```text
GET  /api/web-search/status
POST /api/web-search/test
```

Memory Chat tool 不应直接调用 provider，而应调用 service。

## 数据模型

第一版建议增加 SQLite 缓存表：

```text
web_search_cache
  id
  provider
  query_hash
  query_text
  locale
  freshness
  site
  results_json
  created_at
  expires_at
```

调用计数可以先使用轻量表：

```text
web_search_usage
  id
  provider
  date
  request_count
  created_at
  updated_at
```

后续如果要做多用户，再加 `user_id`。

## Agent 接入

接入点：

```text
backend/app/agent/graphs/memory_chat/nodes.py
```

第一版建议把联网搜索包装成新的 `Lx.web` 上下文层，而不是只作为 ReAct 阶段的普通工具。

原因：

- 是否需要联网是规划问题，应由 planner 基于 L0 当前输入、L0.5 邻接上下文、L1/L2 摘要和 L3/L3.5 本地检索信号决定。
- 联网结果是外部临时证据，不属于 L4 长期记忆、L3 个人笔记或 L3.5 挂载知识库。
- Web 信息需要缓存、限额、隐私确认、来源核验和审计，适合由独立 worker/service 统一收口。
- 回答模型应看到“本轮是否联网、搜了什么、哪些来源已 fetch 核验”，而不是只看到一段无出处文本。

建议新增：

```text
context_lx_web_layer
```

`Lx.web` 与现有 `Lx.attachments` 同属 Lx 派生上下文族：

```text
Lx.attachments
  当前轮附件、历史附件引用、OCR/caption/key facts。

Lx.web
  planner 判定需要公网信息时产生的搜索结果、fetch 摘要、来源 URL 和搜索审计信息。
```

### Planner 决策

`merge_prompt_context` 前新增一个轻量规划节点，或扩展现有 context worker 分发前的 planning step：

```text
plan_lx_web_context
  输入：L0 当前输入、L0.5 邻接上下文、L1+L0 调试窗口摘要、本地检索意图信号、web_search 配置状态
  输出：是否联网、query、freshness、site/domain hint、是否需要用户确认、原因
```

结构化输出示例：

```json
{
  "action": "search | skip | confirm",
  "reason": "用户询问当前阿里云 OSS 计费，需要最新官方信息。",
  "query": "阿里云 OSS 标准存储 计费 官方",
  "freshness": "month",
  "site": "aliyun.com",
  "privacy_risk": "low"
}
```

Planner 只做决策，不直接联网。实际执行交给 `build_lx_web_context`：

```text
plan_lx_web_context
  -> skip: context_lx_web_layer 写入 skipped reason
  -> confirm: request_user_input 确认是否允许外发 query
  -> search: web_search_service.search()
       -> provider search / cache / daily limit
       -> web_fetch 核验官方或高可信来源
       -> build context_lx_web_layer
```

`context_lx_web_layer` 内容示例：

```json
{
  "level": "Lx.web",
  "name": "联网搜索上下文",
  "items": [
    {
      "query": "阿里云 OSS 标准存储 计费 官方",
      "provider": "tavily",
      "sources": [
        {
          "title": "string",
          "url": "https://help.aliyun.com/...",
          "domain": "help.aliyun.com",
          "fetched": true,
          "snippet": "string"
        }
      ]
    }
  ],
  "note": "本层来自公网搜索，只能作为带来源的外部证据；涉及价格和政策时以 fetch 后的官方来源为准。"
}
```

合并顺序建议：

```text
L4 core memory
L3.5 mounted knowledge
L3 personal notes
L2 summary
L1 history
Lx.attachments
Lx.web
L0.5 adjacent
L0 current
```

这样可以保证本地个人上下文仍然优先，当前输入仍然是最终任务锚点；联网信息作为本轮外部证据靠近 L0 注入，方便回答模型引用来源。

ReAct 阶段仍可以保留工具：

```text
web_search
web_fetch
```

这些工具用于首轮 `Lx.web` 不足、需要追问后补搜、或回答过程中发现需要补充来源的场景。工具执行仍由 `tools` 节点统一处理，观察结果进入 `tool_observations` 和 `world_state`，最终由 `agent` 节点决定是否继续 fetch、补搜或回答。

## Prompt 规则

Memory Chat system prompt 需要增加以下规则：

```text
- 当问题需要最新公共信息、官方网页、价格、版本、新闻、法规或实时状态时，可以调用 web_search。
- 当问题可以由个人笔记、挂载知识库或本地文件回答时，优先使用本地上下文，不主动联网。
- 不要把用户的私人笔记、未公开代码、文件路径、账号、密钥、聊天隐私直接放入 web_search query。
- 查询可能包含隐私或商业敏感信息时，必须先调用 request_user_input 确认。
- web_search 结果只是候选来源；涉及精确事实、价格、政策、API 参数时，应优先 web_fetch 官方来源核验。
- 使用联网信息回答时，必须列出来源 URL。
- 没有成功 web_search/web_fetch observation 时，不得声称已经联网搜索。
- 搜索失败、配额耗尽、provider 未配置时，要明确说明原因，并尝试使用本地知识或询问用户是否配置 provider。
```

## 何时调用

推荐主动调用：

- 用户明确说“联网查 / 搜一下 / 查官网 / 最新”。
- 问题包含强时效信息。
- 问题要求当前价格、计费、版本、政策、标准、法规。
- 用户要求引用来源。
- 本地知识与问题明显不匹配或可能过期。

不推荐主动调用：

- 用户问“我之前记过什么”。
- 用户问本地文件、项目代码、知识库内容。
- 用户只是普通聊天、写作、总结。
- query 需要包含隐私内容但尚未确认。

## 隐私与安全

### Query 最小化

搜索 query 应尽量抽象，不直接外发原文：

```text
不推荐：把整段用户笔记复制到 query。
推荐：提取公共关键词，例如“阿里云 OSS 标准存储 计费 官方”。
```

### 域名限制

配置支持 `allowed_domains` 和 `blocked_domains`：

- `allowed_domains` 非空时，只允许搜索/抓取指定域名。
- `blocked_domains` 用于阻止不可信、低质量或高风险域名。

### SSRF 防护

`web_fetch` 必须阻止：

- localhost / 127.0.0.1 / 0.0.0.0
- 内网 IP 段
- file:// / ftp:// 等非 http(s) scheme
- 重定向到内网地址

### 内容限制

`web_fetch` 限制：

- 最大响应字节数。
- 最大正文字符数。
- 超时时间。
- 只处理 text/html、text/plain、application/json 等安全类型。
- 不执行 JavaScript。

## 前端展示

Chat 工具链展示新增卡片：

```text
Web Search
provider: tavily
query: ...
results: 5
cached: false
```

`web_fetch` 展示：

```text
Web Fetch
url: ...
title: ...
chars: 8462
truncated: false
```

最终回答的来源建议结构化展示：

```text
来源
1. 标题 - https://...
2. 标题 - https://...
```

后续可以在消息 schema 里加入 `citations` 字段，但第一版可以先让 agent 在文本回答末尾输出来源列表。

## 错误处理

常见错误：

| error_code | 含义 | Agent 行为 |
| --- | --- | --- |
| `WEB_SEARCH_DISABLED` | 配置关闭 | 告知用户可开启，并用本地知识回答 |
| `WEB_SEARCH_KEY_MISSING` | 缺少 provider key | 引导配置密钥 |
| `WEB_SEARCH_DAILY_LIMIT_EXCEEDED` | 当日额度耗尽 | 不再重试，说明额度问题 |
| `WEB_SEARCH_PRIVATE_QUERY_CONFIRMATION_REQUIRED` | query 可能含隐私 | 调用 `request_user_input` |
| `WEB_FETCH_BLOCKED_URL` | URL 被安全策略阻止 | 换来源或说明不可抓取 |
| `WEB_FETCH_TIMEOUT` | 抓取超时 | 可换来源或只基于搜索摘要谨慎回答 |

连续失败应进入现有 `consecutive_failed_tools` 机制，避免 provider 异常时反复搜索。

## 审计

建议记录：

```text
web_search_events
  id
  conversation_id
  provider
  tool_name
  query_hash
  query_preview
  result_count
  cached
  error_code
  created_at
```

`query_preview` 只保留截断后的短文本；完整 query 是否保存需要配置控制。默认可只保存 hash，减少隐私风险。

## 分阶段落地

### Phase 1: 最小可用

- `web_search` + `web_fetch` service。
- Tavily provider，读取 `TAVILY_API_KEY`。
- Aliyun DashScope provider 作为回退，复用 `DASHSCOPE_API_KEY`。
- config 开关和环境变量密钥。
- SQLite 搜索缓存。
- Memory Chat tool 接入。
- Prompt 规则。
- 前端工具卡片沿用现有 ToolCallCard。
- 后端单元测试覆盖 provider mock、缓存、关闭状态、缺 key、隐私确认。

### Phase 2: 产品化

- `/config web_search.enabled true` 指令。
- `/agent status` 展示 web search 配置状态。
- 前端设置页或 Workshop 面板。
- 结构化 citations 字段。
- 域名 allow/block 配置。
- usage 统计和每日额度 UI。

### Phase 3: 知识库联动

- 用户确认后把网页保存为知识库文档。
- 对同一 URL 建立快照和更新时间。
- 对抓取网页做 chunk / embedding。
- 支持“搜索并保存到某知识空间”。

## 测试计划

后端：

- provider factory 根据配置选择 provider。
- disabled 时 tool 返回 `WEB_SEARCH_DISABLED`。
- 缺 key 时返回 `WEB_SEARCH_KEY_MISSING`。
- daily limit 生效。
- cache hit 不增加 provider 调用。
- `web_fetch` 阻止 localhost / 内网 IP。
- `web_fetch` 截断大正文。
- Memory Chat 工具注册包含 `web_search` / `web_fetch`。

Agent 行为：

- 明确“最新/官网/联网查”时调用 `web_search`。
- 本地笔记问题不调用 `web_search`。
- 隐私 query 先调用 `request_user_input`。
- 没有 observation 不声称已搜索。

前端：

- 工具卡片显示 query、provider、result count。
- 搜索失败时展示错误信息。
- 最终回答来源可点击。

## 开放问题

1. Tavily `search_depth` 是否长期默认 `basic`，还是给用户暴露 `fast` / `advanced` 切换。
2. 是否把 `web_search.enabled` 默认设为 false。
3. 隐私确认规则第一版用关键词/启发式，还是接入轻量 planner。
4. 搜索结果是否默认进入对话记忆摘要。
5. 是否允许桌面精灵入口主动触发联网搜索，还是只允许主聊天窗口。
6. 是否需要专门的“只搜官方来源”模式。

## 建议结论

当前按以下边界实现：

```text
默认由配置控制开关
Tavily provider 为主
Aliyun DashScope provider 保留为回退
web_search + web_fetch 两个工具
SQLite cache + daily limit
隐私 query 先 request_user_input
回答必须列出来源 URL
不做浏览器自动化
不自动入知识库
```

这样可以补齐“最新公共信息”能力，同时不破坏 AiMemo 的本地优先、隐私保护和工具可审计原则。
