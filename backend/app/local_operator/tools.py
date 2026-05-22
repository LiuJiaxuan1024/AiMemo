from collections.abc import Callable
from contextlib import AbstractContextManager
import json
from typing import Any

from langchain_core.tools import BaseTool, tool
from sqlmodel import Session

from app.local_operator.audit import AgentOperationAudit
from app.local_operator.command import LocalCommandExecutor, evaluate_command_policy
from app.local_operator.filesystem import LocalFilesystemError, LocalFilesystemService, tool_result_to_json
from app.local_operator.policy import LocalOperatorPolicy
from app.local_operator.schemas import (
    ExecCommandInput,
    GetFileInfoInput,
    ListDirInput,
    ReadFileInput,
    SearchFilesInput,
    SearchTextInput,
    ToolResult,
    WriteFileInput,
)


SessionFactory = Callable[[], AbstractContextManager[Session]]


def create_read_tools(
    *,
    session_factory: SessionFactory,
    policy: LocalOperatorPolicy,
    conversation_id: int | None,
    turn_id: int | None,
    known_existing_paths: set[str] | None = None,
) -> dict[str, BaseTool]:
    """创建 Local Operator LangChain 工具集合。

    工具本身是标准 `@tool`，但内部显式调用 filesystem service 和 audit。
    这样第一版可以通过 `tool.invoke()` 受控执行，后续也能直接交给 ToolNode。
    """

    filesystem = LocalFilesystemService(policy)
    command_executor = LocalCommandExecutor(policy)
    known_existing_paths = known_existing_paths or set()

    def run_with_audit(
        tool_name: str,
        arguments: dict[str, Any],
        action,
        *,
        operation_type: str = "read",
        risk_level: str = "low",
        approval_required: bool = False,
    ) -> str:
        with session_factory() as session:
            audit = AgentOperationAudit(
                session,
                conversation_id=conversation_id,
                turn_id=turn_id,
            )
            operation = audit.start(
                tool_name=tool_name,
                arguments=arguments,
                operation_type=operation_type,
                risk_level=risk_level,
                approval_required=approval_required,
            )
            try:
                result: ToolResult = action()
            except LocalFilesystemError as exc:
                result = ToolResult(
                    ok=False,
                    tool_name=tool_name,
                    error_code=exc.error_code,
                    message=exc.message,
                    blocked=exc.error_code in {"PATH_OUTSIDE_WORKSPACE", "SENSITIVE_FILE_BLOCKED"},
                )
            except Exception as exc:
                result = ToolResult(
                    ok=False,
                    tool_name=tool_name,
                    error_code=f"{operation_type.upper()}_FAILED",
                    message=str(exc),
                    blocked=False,
                )

            output = result.model_dump()
            if result.ok:
                audit.complete(operation, output=output)
            elif result.blocked:
                audit.block(operation, output=output)
            else:
                audit.fail(operation, output=output)
            return json.dumps(output, ensure_ascii=False)

    @tool(args_schema=ListDirInput)
    def list_dir(path: str, max_entries: int = 100, include_hidden: bool = False) -> str:
        """列出授权 workspace 内的目录内容。"""

        args = {"path": path, "max_entries": max_entries, "include_hidden": include_hidden}
        return run_with_audit(
            "list_dir",
            args,
            lambda: filesystem.list_dir(path, max_entries=max_entries, include_hidden=include_hidden),
        )

    @tool(args_schema=ReadFileInput)
    def read_file(
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        max_bytes: int = 65536,
    ) -> str:
        """读取授权 workspace 内的文本文件，可指定行号范围。"""

        args = {"path": path, "start_line": start_line, "end_line": end_line, "max_bytes": max_bytes}
        return run_with_audit(
            "read_file",
            args,
            lambda: filesystem.read_file(
                path,
                start_line=start_line,
                end_line=end_line,
                max_bytes=max_bytes,
            ),
        )

    @tool(args_schema=SearchFilesInput)
    def search_files(
        root: str = ".",
        pattern: str = "",
        max_results: int = 50,
        include_hidden: bool = False,
    ) -> str:
        """在授权 workspace 内按文件名搜索文件。"""

        args = {
            "root": root,
            "pattern": pattern,
            "max_results": max_results,
            "include_hidden": include_hidden,
        }
        return run_with_audit(
            "search_files",
            args,
            lambda: filesystem.search_files(
                root,
                pattern=pattern,
                max_results=max_results,
                include_hidden=include_hidden,
            ),
        )

    @tool(args_schema=SearchTextInput)
    def search_text(
        root: str = ".",
        query: str = "",
        include_glob: str | None = None,
        max_results: int = 50,
        context_lines: int = 2,
    ) -> str:
        """在授权 workspace 内搜索文本内容。"""

        args = {
            "root": root,
            "query": query,
            "include_glob": include_glob,
            "max_results": max_results,
            "context_lines": context_lines,
        }
        return run_with_audit(
            "search_text",
            args,
            lambda: filesystem.search_text(
                root,
                query=query,
                include_glob=include_glob,
                max_results=max_results,
                context_lines=context_lines,
            ),
        )

    @tool(args_schema=GetFileInfoInput)
    def get_file_info(path: str) -> str:
        """查看授权 workspace 内文件或目录的元信息。"""

        args = {"path": path}
        return run_with_audit("get_file_info", args, lambda: filesystem.get_file_info(path))

    @tool(args_schema=WriteFileInput)
    def write_file(path: str, content: str, overwrite: bool = False) -> str:
        """创建或整文件覆盖授权 workspace 内的文本文件。"""

        args = {"path": path, "content": content, "overwrite": overwrite}
        return run_with_audit(
            "write_file",
            args,
            lambda: filesystem.write_file(
                path,
                content=content,
                overwrite=overwrite,
                known_existing_paths=known_existing_paths,
            ),
            operation_type="write",
            risk_level="medium",
            approval_required=False,
        )

    @tool(args_schema=ExecCommandInput)
    def exec_command(
        command: str,
        cwd: str = ".",
        timeout_ms: int = 30000,
        max_output_bytes: int = 65536,
    ) -> str:
        """执行短时、非交互的本地终端命令。

        该工具只用于终端级任务，例如查看版本、运行测试、git 状态等。
        文件读写仍应走 read_file/write_file，避免 shell 绕过专用工具策略。
        """

        args = {
            "command": command,
            "cwd": cwd,
            "timeout_ms": timeout_ms,
            "max_output_bytes": max_output_bytes,
        }
        decision = evaluate_command_policy(command)
        return run_with_audit(
            "exec_command",
            args,
            lambda: command_executor.exec_command(
                command=command,
                cwd=cwd,
                timeout_ms=timeout_ms,
                max_output_bytes=max_output_bytes,
            ),
            operation_type="exec",
            risk_level=decision.risk_level,
            approval_required=decision.risk_level != "low",
        )

    return {
        "list_dir": list_dir,
        "read_file": read_file,
        "search_files": search_files,
        "search_text": search_text,
        "get_file_info": get_file_info,
        "write_file": write_file,
        "exec_command": exec_command,
    }
