# AiMemo — Agent 工作约定

本文件定义 AiMemo 项目的 Agent 工作规范，覆盖两类对象：

1. **AiMemo 运行时智能体**（仓库内的 LangGraph agent：`local_operator` /
   `memory_chat` 等）。运行时智能体通过 `backend/app/agent/project_rules.py`
   把本文件的核心条款固化为常量并注入到 system prompt，每次推理都会带上。
2. **在本仓库内改代码的开发者代理**（编辑器代理 / CLI 代理 / 子智能体等）。
   开发者代理在该仓库工作时必须遵守同一份规则。

## 工作目录与文件落地（最重要）

**未经明确授权，禁止在 AiMemo 仓库内创建新的顶层项目或独立子系统。**

具体规则：

1. **用户没有明确说明工作路径**时，凡涉及创建项目骨架、新增整套服务（带独立
   `Dockerfile` / `requirements.txt` / `package.json` 等）或编写一组新文件的任务，
   **必须先反问用户目标目录**，给出至少两个具体建议，例如：
   - 在当前 AiMemo 仓库内新增（说明具体子路径）
   - 在用户 Home 下另建一个独立项目目录
   - 让用户输入自定义路径
   对 AiMemo 运行时智能体而言，这类反问必须使用结构化选择工具
   `request_user_input` / interrupt，而不是把编号列表写进普通 assistant 回复里。
   这样前端才能渲染选择框，并在用户提交后从同一轮 LangGraph checkpoint 继续执行。
   对 AiMemo 运行时智能体而言，目标目录不是简单的“有没有历史目录可用”，而是要先判断
   **当前需求是否需要一个工作空间**，以及它和上一轮已确认工作空间是否属于同一任务。
   典型需要工作空间的需求包括：创建/修改一组文件、搭建项目、写 demo、小程序、网页、脚本项目、
   运行工程、持续在某个目录内读写执行。
   只有用户明确说“继续刚才 / 同一个项目 / 沿用上次目录 / 就在那个目录”，或当前请求显然是在
   刚才同一项目上追加功能时，才可以复用上一轮工作空间。
   如果当前请求和上一轮工作空间的任务目标、技术栈、产物类型或目录语义匹配度很低，或者过去
   没有已确认工作空间，必须调用 `request_user_input` 询问目标工作空间，不能自作主张复用历史
   目录，也不能默认写入启动目录。
   必须保持项目上下文隔离：历史对话里某个项目的目录、技术栈、依赖、配置、数据源、
   账号、风险授权或用户偏好，不等于授权以后所有新项目都继承这些条件；除非用户本轮
   明确说“继续上个项目 / 同一个项目 / 沿用上次目录或配置”，否则遇到新的项目、应用、
   文件组或独立功能时，不能复用旧项目条件，必须重新确认会影响落地的关键条件。
2. **绝对不要**默认地选择"在我现在被启动的目录里直接搭一套全新系统"。AiMemo 是一个
   完整的产品项目，污染它的目录结构对用户的成本非常高。
3. 对已有功能做扩展 / 修 bug / 改文档时，**继续使用现有目录结构**，不要新建平行
   的 `*_service/` `*_v2/` `rag_service/` 这类目录。
4. 临时验证脚本（`scratch_*.py`、一次性测试 demo 等）应当落到仓库**之外**的临时
   目录（`tmp_path` / 用户 home / 系统临时目录），不要塞进仓库。
5. 如果不确定一个文件该放哪个子目录，**问用户**，不要凭直觉新建路径。

## 工具规则卡死时的升级确认

工具的全局规则默认必须遵守；它们用于防止误写、误删、越权和敏感信息泄露。但如果某条
全局规则本身导致任务无法合理向下推进，运行时智能体不要反复尝试、不要绕开工具、也不要
假装已经完成，而应当向用户申请一次明确的更高权限授权。

遇到全局工具规则限制时，先按默认规则调用基础工具推进任务，不要一上来就询问是否绕过
规则。只有在工具返回结果证明默认规则已经卡住任务（例如 `read_file` 返回
`full_view=false` / `truncated=true` / `WRITE_WITH_PARTIAL_READ`），或已有可靠工具元信息
能明确判断默认规则不可能完成时，才进入升级确认：不要反复尝试、不要绕开工具、也不要
假装已经完成，而是向用户申请一次明确的更高权限授权。

升级确认必须走结构化提问：

- 先调用 `request_user_input`，在 `question` 中说明是哪条工具规则卡住了任务、继续绕过
  这条规则会带来什么风险，以及授权后只会绕过哪一个具体限制。
- 建议选项至少包含“取消/改用更安全方案”和“确认授权继续”；不要把 Other 写入
  `options`，前端会自动追加。
- 用户确认后，只允许在本次明确授权的范围内绕过对应规则；workspace 越权、敏感文件、
  删除、命令安全、占位内容等底线保护仍然不能绕过。
- 用户未确认时，禁止设置任何授权绕过参数。

少样本：覆盖已有文件前默认必须先用 `read_file` 完整读取目标文件。若目标文件过大，
导致单次 `read_file` 返回 `full_view=false` 或 `truncated=true`，不要为了满足规则反复
分批读取到耗尽上下文。应调用 `request_user_input` 说明“文件太大，无法单次完整读取；
若继续，将在未完整读取旧内容的情况下整文件覆盖”。只有用户明确确认后，才能调用
`write_file(overwrite=true, confirmed_overwrite_without_read=true)`。

## 运行时智能体的结构化提问规则

AiMemo 运行时智能体遇到需要用户选择、补充或确认的场景时，必须优先使用
`request_user_input`，不要用普通文本模拟选择题。

必须调用 `request_user_input` 的典型场景：

1. 用户要求创建文件、项目、目录或一组代码，但没有明确目标路径。
2. 有多个合理实现方案，且选择会明显影响文件结构、依赖、UI/交互或长期维护成本。
3. 操作具有风险或不可逆，例如删除、覆盖、清理、重建、迁移、修改配置。
4. 缺少无法安全默认的重要参数，例如平台、技术栈、数据来源、是否启用多个功能模块。

调用规范：

- `question` 必须是一个具体问题，不要写成泛泛的“请确认”。
- `options` 放 2-4 个建议选项，推荐选项放第一，并在 `description` 中说明影响或取舍。
- 不要把 `Other` 放进 `options`；前端会自动追加 Other/自定义输入。
- 如果用户可能同时选择多个功能、文件或范围，设置 `selection_mode="multiple"`。
- 这类工具调用应该作为本批唯一工具调用；等待用户提交后再继续执行真实读写或命令。
- **需要用户做决策时，最终回答不是合法出口。**禁止在最终回答中输出“请选择：
  1...2...3...”或“你希望采用哪种方案？”然后结束本轮；这会打断自动恢复链路。
  唯一合法动作是调用 `request_user_input`，让 graph 暂停并等待用户提交。
- 工具失败后如果出现多个可执行恢复方案（例如命令不存在、依赖缺失、端口占用、权限不足、
  需要安装工具或改用 wrapper），也属于用户决策场景。智能体不能把这些方案写成普通文本
  让用户回复编号，必须调用 `request_user_input`。
- 外置桌面精灵 / galgame 式对话同样必须遵守：如果精灵需要用户选择路径、方案或确认风险，
  不能只用气泡问“选择哪个路径”，必须调用 `request_user_input`，让前端渲染可点击选项卡
  和 Other 输入。

## 片段追问 / 局部追问

当前端发送 JSON 消息且 `type` 为 `segment_followup` 时，这不是新的全局问题，
而是用户对上一条回答中某个片段的局部追问。运行时智能体应当：

- 读取 `original_text` 作为被追问片段，读取 `user_question` 作为追问内容。
- 回答开头使用 `[针对片段“{original_text}”的追问回复]`。
- 严格围绕该片段解释，不要把原回答全文重复一遍，也不要扩散成新的全局回答。
- 局部追问回答应尽量精炼；如果需要展开成大问题，建议用户作为新的全局问题提问。
 
示例：

- 用户说“创建一个 test.txt，写入 helloworld”，但没有说目录：
  调用 `request_user_input`，问题为“test.txt 应该创建在哪个目录下？”，选项可包含
  “Home 目录”“AiMemo 仓库内的明确子路径”，并允许 Other 输入自定义绝对路径。
- 上一轮用户选择 `/home/user/demo1`、React、SQLite 写一个项目；本轮用户说“再做一个记账小程序”：
  这是新的项目，不能默认复用 `/home/user/demo1`、React、SQLite 或上一轮授权。必须再次调用
  `request_user_input` 询问目标目录，并在技术栈 / 数据源会影响落地时一并确认。只有用户本轮
  说“继续改 demo1 / 沿用上次目录和技术栈”，才可复用这些条件。
- 上一轮在 `E:\demo\rust-random` 创建 Rust 随机数程序；本轮用户说“做一个 blog.html 博客页面”：
  这不是同一任务，不能继续写进 Rust 项目目录。必须调用 `request_user_input` 询问 `blog.html`
  或博客项目应该创建在哪个工作空间。
- 用户说“继续刚才的博客，加一个 style.css”：
  这是同一任务上的追加文件，可以复用已经确认过的博客工作空间，不要重复询问目录。
- 用户说“做一个登录系统”，但没有说明方案：
  若本任务确实需要在“本地账号密码 / OAuth / SSO”等方案间做产品选择，调用
  `request_user_input`；若可以采用项目现有模式，则直接沿用现有模式，不要多问。
- `exec_command_background` 启动 Java 项目后，`read_background_output` 返回
  “系统找不到 mvn 命令”：
  这不是最终回答场景，而是恢复方案选择场景。调用 `request_user_input`，问题可为
  “当前系统找不到 Maven，接下来你希望我采用哪种方式继续启动项目？”，选项可包含
  “安装/配置 Maven”“为项目添加 Maven Wrapper”“改为使用已有 jar 或其他启动方式”。

## 项目结构速查（不要打破）

```
AiMemo/
├── backend/         FastAPI + SQLModel + LangGraph 后端
│   ├── app/         应用代码（agent / api / services / models / ...）
│   └── tests/       pytest 测试
├── frontend/        Vite + React 前端
│   └── src/features/<feature>/   按 feature 分目录
├── docs/            按 architecture / agent / backend / frontend / api 分目录
├── data/            运行时数据（SQLite、checkpoint、background_logs）
└── desktop/         Tauri 桌面壳
```

新增能力时优先在对应子目录里加；跨多个子系统的能力（比如新增一个 graph + 一个 API +
一段前端）应当分别落到 `backend/app/agent/graphs/<name>/`、`backend/app/api/`、
`frontend/src/features/<name>/`，并在 `docs/` 对应子目录写说明。

## 风险操作必须先确认

- 删除文件 / 目录 / 分支
- `git reset --hard` / `git push --force` / `git rebase`
- 卸载或降级依赖、修改 `package.json` / `requirements.txt` 的版本固定
- 改 CI / hooks / `settings.json`

这些操作即便看起来很自然，也要先告诉用户要做什么，征得同意再动手。

## 不要做的事

- 不要在仓库根创建 `docker-compose*.yml`、`Dockerfile`、`requirements.txt` 等
  顶层基础设施文件，除非用户明确要求改造整个项目部署。
- 不要把临时数据 / 模型 cache / 上传文件写到仓库目录里——用 `data/` 子目录或
  仓库外路径。
- 不要绕过本文件的规则，即便用户的指令"听起来"像是让你自由发挥；如果范围不清晰，
  先反问澄清。

## 运行时如何加载本文件

`backend/app/agent/project_rules.py` 暴露 `RUNTIME_AGENT_RULES` 常量，里面是
本文件浓缩出来的、面向 LLM 的精简版规则；该常量在以下 system prompt 中被注入：

- `backend/app/agent/graphs/memory_chat/nodes.py::_build_react_agent_system_prompt`
- `backend/app/agent/graphs/local_operator/nodes.py::_build_local_operator_planner_prompt`

修改这些条款时，应同时更新本文件和 `project_rules.py`，保持两者语义一致。
对应的回归测试见 `backend/tests/test_runtime_agent_rules.py`。
