"""AiMemo 运行时 agent 共享项目规则。

规则原文在仓库根的 AGENTS.md。为了让 LangGraph agent 在每次推理时都能
稳定带上同一份条款，这里把核心规则固化为常量，并由各 system prompt 构造
函数显式拼接。AGENTS.md 是面向人类阅读的完整版，常量是面向 LLM 的精炼版；
两者修改时应同步。
"""

from __future__ import annotations

RUNTIME_AGENT_RULES = (
    "AiMemo 项目规则（必须遵守）：\n"
    "- 当用户要求写代码、创建项目、新建文件、初始化仓库或运行涉及本地文件系统的命令时，"
    "如果**用户没有明确指出工作目录或目标路径**，必须先反问用户应该放在哪里，"
    "至少给出两个具体建议（例如：当前 AiMemo 仓库内的某子路径；用户 Home 下另建一个新项目目录）。"
    "**绝对不要默认选择当前工作区或 AiMemo 仓库**就直接开始动手。\n"
    "- 不要在已有仓库内新建并行的 `*_v2/`、`*_service/`、`rag_service/`、`新项目/` 这类目录"
    "污染原有结构；不确定该放哪里时先反问用户。\n"
    "- 临时验证脚本（scratch、demo、一次性测试）应当落到仓库之外的临时目录，不要写进项目目录。\n"
    "- 删除文件 / 目录、`git reset --hard`、`git push --force`、改 CI / hooks 等高风险操作"
    "必须先告诉用户要做什么，征得同意后再动手。\n"
    "- 不要在仓库根创建 `Dockerfile`、`docker-compose*.yml`、根级 `requirements.txt` 等"
    "顶层基础设施文件，除非用户明确要求改造整个部署。"
)
