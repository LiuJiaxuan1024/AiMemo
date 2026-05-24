# AiMemo — Agent 工作约定

本文件定义 AiMemo 项目的 Agent 工作规范，覆盖两类对象：

1. **AiMemo 运行时智能体**（仓库内的 LangGraph agent：`local_operator` /
   `memory_chat` 等）。运行时智能体通过 `backend/app/agent/project_rules.py`
   把本文件的核心条款固化为常量并注入到 system prompt，每次推理都会带上。
2. **在本仓库内改代码的开发者代理**（Claude Code / Cursor / 子智能体等）。
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
2. **绝对不要**默认地选择"在我现在被启动的目录里直接搭一套全新系统"。AiMemo 是一个
   完整的产品项目，污染它的目录结构对用户的成本非常高。
3. 对已有功能做扩展 / 修 bug / 改文档时，**继续使用现有目录结构**，不要新建平行
   的 `*_service/` `*_v2/` `rag_service/` 这类目录。
4. 临时验证脚本（`scratch_*.py`、一次性测试 demo 等）应当落到仓库**之外**的临时
   目录（`tmp_path` / 用户 home / 系统临时目录），不要塞进仓库。
5. 如果不确定一个文件该放哪个子目录，**问用户**，不要凭直觉新建路径。

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
