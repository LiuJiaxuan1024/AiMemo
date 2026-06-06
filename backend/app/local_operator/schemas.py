from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.config import settings


class ToolError(BaseModel):
    """统一工具错误结构，便于 graph 和前端按 error_code 处理。"""

    ok: bool = False
    error_code: str
    message: str
    blocked: bool = False


class DirectoryEntry(BaseModel):
    """目录列表中的单个条目。"""

    name: str
    relative_path: str
    kind: Literal["file", "directory"]
    size: int | None = None
    modified_at: str


class ToolResult(BaseModel):
    """工具返回的通用 envelope。

    data 中放具体工具的结构化结果；失败时 data 为空，error_code/message 给出原因。
    """

    ok: bool
    tool_name: str
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str = ""
    message: str = ""
    blocked: bool = False


class ListDirInput(BaseModel):
    path: str = Field(description="要列出的目录路径，必须位于授权 workspace 内。必须是绝对路径，例如 E:\\demo 或 /home/user/demo。")
    max_entries: int = Field(default=100, description="最大返回条目数，上限 500。")
    include_hidden: bool = Field(default=False, description="是否包含隐藏文件。")


class ReadFileInput(BaseModel):
    path: str = Field(description="要读取的文本文件路径，必须位于授权 workspace 内。必须是绝对路径，例如 E:\\demo\\config.json 或 /home/user/demo/config.json。")
    start_line: int | None = Field(default=None, description="起始行号，1-based。")
    end_line: int | None = Field(default=None, description="结束行号，包含该行。")
    max_bytes: int = Field(default=65536, description="最大返回字节数，上限由系统策略限制。")


class ReadDocumentInput(BaseModel):
    path: str = Field(description="要解析的 PDF 或 DOCX 文档路径，必须位于授权 workspace 内。必须是绝对路径，例如 /home/user/report.pdf。")
    max_chars: int = Field(default=80000, description="最多返回的解析文本字符数，上限 200000。")


class WriteFileInput(BaseModel):
    path: str = Field(description="要写入的文件路径，必须位于授权 workspace 内。必须是绝对路径，例如 E:\\demo\\main.py 或 /home/user/demo/main.py。")
    content: str = Field(description="要写入文件的完整内容。该工具会整文件写入。")
    overwrite: bool = Field(default=False, description="是否允许覆盖已存在文件。覆盖前必须先用 read_file 完整读取该文件。")
    confirmed_overwrite_without_read: bool = Field(
        default=False,
        description="仅当用户已通过结构化确认允许在未完整读取旧文件内容时整文件覆盖，才可设置为 true。",
    )


class ExecCommandInput(BaseModel):
    """终端命令执行工具输入。

    exec 是高风险能力，所以参数故意保持少而明确：只允许前台短时命令，
    不支持后台任务、不支持交互输入，也不把它作为读写文件的替代品。
    """

    command: str = Field(description="要前台执行的终端命令。用于本轮需要拿到 stdout/stderr/exit_code 的任务；不要用它读写文件，读写文件应使用专用工具。")
    cwd: str = Field(default=".", description="命令工作目录，必须位于授权 workspace 内。必须是绝对路径，例如 E:\\demo 或 /home/user/demo。")
    timeout_ms: int = Field(
        default=settings.local_operator_exec_default_timeout_ms,
        description="超时时间，单位毫秒，上限由系统策略限制。pip install、构建等耗时命令可以使用更长超时。",
    )
    max_output_bytes: int = Field(
        default=settings.local_operator_exec_default_max_output_bytes,
        description="stdout/stderr 合计最多返回字节数。",
    )


class ExecCommandBackgroundInput(BaseModel):
    """后台启动命令的输入。

    仅用于启动长期运行的服务（如 flask run/uvicorn/npm run dev），不会阻塞 agent 循环。
    构建、安装、测试、一次性脚本等需要本轮结果的命令应使用 exec_command，而不是后台化。
    返回 task_id 后，用 read_background_output 轮询输出，用 kill_background_task 停止。
    """

    command: str = Field(description="要在后台启动的服务型命令，例如 'uvicorn app:app'、'flask run' 或 'npm run dev'。不要用于 pip install、构建、测试或一次性脚本。不要用它读写文件，也不要写带 & 的 shell 后台符。")
    cwd: str = Field(default=".", description="命令工作目录，必须位于授权 workspace 内绝对路径。")


class ReadBackgroundOutputInput(BaseModel):
    """读取后台任务输出与状态。"""

    task_id: str = Field(description="exec_command_background 返回的 task_id，例如 'bg-1234abcd'.")
    since_line: int = Field(default=0, description="从该行号之后开始返回（0 表示从最早一行）。轮询时应记录上次返回的 last_line 并把它当作 since_line。")
    max_lines: int = Field(default=50, description="最多返回多少行，上限 200。")


class KillBackgroundTaskInput(BaseModel):
    """停止指定后台任务。"""

    task_id: str = Field(description="要停止的后台任务 ID。会整树 kill 子进程及孙子进程。")


class ListBackgroundTasksInput(BaseModel):
    """列出当前会话的所有后台任务（含历史/orphaned）。"""

    include_finished: bool = Field(
        default=True,
        description="是否包含已结束（exited/failed/killed/orphaned）的任务；默认 True。",
    )


class SearchFilesInput(BaseModel):
    root: str = Field(default=".", description="搜索根目录，必须位于授权 workspace 内。必须是绝对路径，例如 E:\\demo 或 /home/user/demo。")
    pattern: str = Field(description="文件名关键词或 glob，例如 memory 或 *.py。")
    max_results: int = Field(default=50, description="最大返回结果数，上限 200。")
    include_hidden: bool = Field(default=False, description="是否包含隐藏文件。")


class SearchTextInput(BaseModel):
    root: str = Field(default=".", description="搜索根目录，必须位于授权 workspace 内。必须是绝对路径，例如 E:\\demo 或 /home/user/demo。")
    query: str = Field(description="要搜索的文本。第一阶段按普通字符串匹配。")
    include_glob: str | None = Field(default=None, description="限制文件 glob，例如 *.py、*.tsx。")
    max_results: int = Field(default=50, description="最大匹配条数，上限 200。")
    context_lines: int = Field(default=2, description="每个命中的上下文行数，上限 5。")


class GetFileInfoInput(BaseModel):
    path: str = Field(description="要查看信息的文件或目录路径，必须位于授权 workspace 内。必须是绝对路径，例如 E:\\demo 或 /home/user/demo。")


class RemoteSshBaseInput(BaseModel):
    host: str = Field(description="远程服务器主机名或 IP，不要包含用户名或协议。")
    username: str = Field(description="SSH 用户名。")
    port: int = Field(default=22, ge=1, le=65535, description="SSH 端口，默认 22。")
    identity_file: str | None = Field(
        default=None,
        description="可选 SSH 私钥路径。必须位于授权 workspace roots 内；不要传入私钥内容。",
    )
    connect_timeout_seconds: int = Field(default=10, ge=3, le=60, description="SSH 连接超时秒数。")


class RemoteConnectivityCheckInput(RemoteSshBaseInput):
    """检查远程 SSH 是否能以非交互方式连接。"""


class RemoteUploadFileInput(RemoteSshBaseInput):
    local_path: str = Field(description="要上传的本地文件路径，必须位于授权 workspace 内。")
    remote_path: str = Field(description="远程目标文件路径，例如 /usr/share/nginx/html/index.html。")


class RemoteExecInput(RemoteSshBaseInput):
    command: str = Field(description="要在远程服务器执行的短时非交互命令。不要传入需要密码、TTY 或交互确认的命令。")
    timeout_ms: int = Field(default=settings.local_operator_exec_default_timeout_ms, description="远程命令超时时间，单位毫秒。")


class RemoteVerifyHttpInput(BaseModel):
    url: str = Field(description="要验证的 HTTP/HTTPS URL。")
    expected_text: str | None = Field(default=None, description="可选：响应中应该包含的文本片段。")
    timeout_seconds: int = Field(default=10, ge=3, le=60, description="HTTP 请求超时秒数。")
