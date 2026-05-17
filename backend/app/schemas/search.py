from pydantic import BaseModel, Field


class NoteSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)


class NoteSearchResult(BaseModel):
    note_id: int
    note_title: str
    chunk_id: int
    chunk_index: int
    content: str
    content_hash: str
    token_count: int
    distance: float
    score: float


class NoteSearchResponse(BaseModel):
    query: str
    limit: int
    results: list[NoteSearchResult]

