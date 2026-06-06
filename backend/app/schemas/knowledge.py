from datetime import datetime

from pydantic import BaseModel, Field


class KnowledgeSpaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    icon: str | None = Field(default=None, max_length=80)


class KnowledgeSpaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=80)
    status: str | None = Field(default=None, max_length=24)


class KnowledgeSpaceRead(BaseModel):
    id: int
    name: str
    description: str
    icon: str | None
    status: str
    document_count: int = 0
    ready_document_count: int = 0
    created_at: datetime
    updated_at: datetime


class KnowledgeDocumentRead(BaseModel):
    id: int
    space_id: int
    title: str
    source_type: str
    source_uri: str | None
    storage_path: str | None
    original_filename: str | None
    mime_type: str | None
    content_hash: str
    parser: str | None
    chunk_strategy: str
    status: str
    chunk_count: int
    text_chunk_count: int
    image_asset_count: int
    image_asset_processed_count: int
    image_text_chunk_count: int
    image_asset_failed_count: int
    token_count: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None


class KnowledgeDocumentUploadResponse(BaseModel):
    document: KnowledgeDocumentRead
    job: dict | None = None


class KnowledgeChunkDraftRead(BaseModel):
    chunk_index: int
    text: str
    heading_path: list[str]
    page_number: int | None
    source_offset: int | None
    token_count: int
    content_hash: str
    metadata_json: str | None


class KnowledgeChunkRead(BaseModel):
    id: int
    space_id: int
    document_id: int
    chunk_index: int
    text: str
    summary: str | None
    heading_path: str | None
    page_number: int | None
    source_offset: int | None
    token_count: int
    content_hash: str
    embedding_status: str
    embedding_error: str | None
    metadata_json: str | None
    created_at: datetime
    updated_at: datetime


class KnowledgeOcrStatusRead(BaseModel):
    mode: str
    ready: bool
    status: str
    tesseract_available: bool
    tesseract_path: str | None
    tesseract_version: str | None
    tessdata_path: str | None = None
    available_languages: list[str]
    required_languages: list[str]
    missing_languages: list[str]
    install_running: bool = False
    install_processes: list[str] = Field(default_factory=list)
    install_task_ids: list[str] = Field(default_factory=list)
    python_packages: dict[str, bool]
    message: str


class KnowledgeOcrInstallRequest(BaseModel):
    confirm_install: bool = False


class KnowledgeOcrInstallCommandResult(BaseModel):
    task_id: str | None = None
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    message: str


class KnowledgeOcrInstallResponse(BaseModel):
    supported: bool
    installed: bool
    command_results: list[KnowledgeOcrInstallCommandResult]
    install_task_id: str | None = None
    before_status: KnowledgeOcrStatusRead
    after_status: KnowledgeOcrStatusRead
    message: str


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    space_id: int | None = None
    top_k: int = Field(default=8, ge=1, le=20)
    mode: str = Field(default="hybrid", max_length=24)


class KnowledgeSearchResultItem(BaseModel):
    chunk_id: int
    space_id: int
    space_name: str
    document_id: int
    document_title: str
    text: str
    score: float
    score_source: str
    heading_path: list[str]
    page_number: int | None
    source_uri: str | None
    original_filename: str | None
    retrieval_phase: str
    distance: float | None = None


class KnowledgeSearchResponse(BaseModel):
    query: str
    top_k: int
    mode: str
    status: str
    results: list[KnowledgeSearchResultItem]


class ConversationKnowledgeMountRead(BaseModel):
    id: int
    conversation_id: int
    space_id: int
    space_name: str
    space_icon: str | None
    ready_document_count: int = 0
    document_count: int = 0
    created_by: str
    scope_note: str | None
    created_at: datetime


class ConversationKnowledgeMountReplace(BaseModel):
    space_ids: list[int] = Field(default_factory=list)
