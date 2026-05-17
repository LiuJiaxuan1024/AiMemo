import httpx

from app.core.config import settings
from app.core.timing import elapsed_ms, emit_timing, now_counter


def embed_texts(texts: list[str]) -> list[list[float]]:
    total_started_at = now_counter()
    if not texts:
        return []
    if not settings.dashscope_api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is required to initialize embeddings.")

    # DashScope 提供 OpenAI-compatible embeddings 接口；这里直接调用 HTTP API，
    # 避免检索层绑定到某个 SDK 的响应结构，后续切换 provider 也更直接。
    request_started_at = now_counter()
    response = httpx.post(
        f"{settings.dashscope_base_url.rstrip('/')}/embeddings",
        headers={
            "Authorization": f"Bearer {settings.dashscope_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": settings.dashscope_embedding_model,
            "input": texts,
            "dimensions": settings.embedding_dimensions,
        },
        timeout=60,
    )
    request_ms = elapsed_ms(request_started_at)
    parse_started_at = now_counter()
    response.raise_for_status()
    payload = response.json()
    embeddings = [item["embedding"] for item in payload["data"]]
    parse_ms = elapsed_ms(parse_started_at)
    emit_timing(
        "rag.embedding_timing",
        total_ms=elapsed_ms(total_started_at),
        request_ms=request_ms,
        parse_ms=parse_ms,
        model=settings.dashscope_embedding_model,
        text_count=len(texts),
        text_chars=sum(len(text) for text in texts),
        dimensions=settings.embedding_dimensions,
        status_code=response.status_code,
    )
    return embeddings
