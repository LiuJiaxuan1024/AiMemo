from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.note import utc_now


class BackgroundTask(SQLModel, table=True):
    """持久化的后台命令任务记录。

    与内存 `BackgroundShellPool` 一一对应，但额外承担：
    - 后端重启后仍可被 UI / 工具发现；
    - 通过 pid 探活判断进程是否还在跑；
    - stdout/stderr 落到磁盘日志文件而非父进程管道，
      子进程在后端关闭后仍能继续写日志、用户可重启后继续查看。

    status 枚举：
      - running   : 进程在运行（最近一次探活成功）
      - exited    : 进程正常退出，exit_code = 0
      - failed    : 进程非 0 退出
      - killed    : 被工具/用户主动终止
      - orphaned  : 后端重启后探活失败，进程已不在 OS 里（任务记录留作历史）
      - unknown   : 探活失败但不确定（保留位）
    """

    # 详见 Note.__table_args__ 的注释。
    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    task_id: str = Field(index=True, max_length=40)
    conversation_id: int | None = Field(default=None, index=True)
    command: str
    cwd: str
    pid: int | None = Field(default=None, index=True)
    status: str = Field(default="running", index=True, max_length=24)
    exit_code: int | None = None
    kill_reason: str = ""
    stdout_path: str = ""
    stderr_path: str = ""
    started_at: datetime = Field(default_factory=utc_now, index=True)
    finished_at: datetime | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
