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
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
