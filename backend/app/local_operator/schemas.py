from typing import Any, Literal

from pydantic import BaseModel, Field


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
    path: str = Field(description="要列出的目录路径，必须位于授权 workspace 内。")
    max_entries: int = Field(default=100, description="最大返回条目数，上限 500。")
    include_hidden: bool = Field(default=False, description="是否包含隐藏文件。")


class ReadFileInput(BaseModel):
    path: str = Field(description="要读取的文本文件路径，必须位于授权 workspace 内。")
    start_line: int | None = Field(default=None, description="起始行号，1-based。")
    end_line: int | None = Field(default=None, description="结束行号，包含该行。")
    max_bytes: int = Field(default=65536, description="最大返回字节数，上限由系统策略限制。")


class SearchFilesInput(BaseModel):
    root: str = Field(default=".", description="搜索根目录，必须位于授权 workspace 内。")
    pattern: str = Field(description="文件名关键词或 glob，例如 memory 或 *.py。")
    max_results: int = Field(default=50, description="最大返回结果数，上限 200。")
    include_hidden: bool = Field(default=False, description="是否包含隐藏文件。")


class SearchTextInput(BaseModel):
    root: str = Field(default=".", description="搜索根目录，必须位于授权 workspace 内。")
    query: str = Field(description="要搜索的文本。第一阶段按普通字符串匹配。")
    include_glob: str | None = Field(default=None, description="限制文件 glob，例如 *.py、*.tsx。")
    max_results: int = Field(default=50, description="最大匹配条数，上限 200。")
    context_lines: int = Field(default=2, description="每个命中的上下文行数，上限 5。")


class GetFileInfoInput(BaseModel):
    path: str = Field(description="要查看信息的文件或目录路径，必须位于授权 workspace 内。")
