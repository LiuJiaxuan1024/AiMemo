from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "sqlite:///./data/ai_note.db"
    langgraph_checkpoint_path: str = "./data/langgraph_checkpoints.db"
    job_worker_enabled: bool = True
    job_worker_poll_interval_seconds: float = 2.0
    job_running_timeout_seconds: int = 600
    job_reconciler_enabled: bool = True
    job_reconciler_interval_seconds: float = 30.0
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = 1024
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    chat_model: str = ""
    embedding_model: str = ""
    # Local Operator read-only 默认允许仓库根目录和当前用户 Home。
    # 这里可以追加更多根目录，使用分号或逗号分隔，例如：
    # LOCAL_OPERATOR_WORKSPACE_ROOTS=E:\Ai记;D:\资料;~/Documents
    local_operator_workspace_roots: str = ""
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "tauri://localhost",
    ]

    model_config = SettingsConfigDict(
        # 支持两种常见启动方式：
        # 1. 在仓库根目录执行脚本，此时读取根目录 `.env`；
        # 2. 进入 backend/ 后启动 uvicorn，此时额外读取 `../.env`。
        # 后面的文件优先级更高，方便用户在 backend/.env 做本地临时覆盖。
        env_file=(".env", "../.env", "backend/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
