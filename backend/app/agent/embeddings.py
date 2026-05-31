import httpx

from app.core.config import settings
from app.core.timing import elapsed_ms, emit_timing, now_counter


DASHSCOPE_EMBEDDING_MAX_BATCH_SIZE = 10


def embed_texts(texts: list[str]) -> list[list[float]]:
    total_started_at = now_counter()
    if not texts:
        return []
    if not settings.dashscope_api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is required to initialize embeddings.")

    # DashScope 提供 OpenAI-compatible embeddings 接口；这里直接调用 HTTP API，
    # 避免检索层绑定到某个 SDK 的响应结构，后续切换 provider 也更直接。
    embeddings: list[list[float]] = []
    request_ms = 0
    parse_ms = 0
    status_code = 0
    batches = list(_embedding_batches(texts))
    for batch in batches:
        request_started_at = now_counter()
        response = _post_embedding_batch(batch)
        request_ms += elapsed_ms(request_started_at)
        status_code = response.status_code

        parse_started_at = now_counter()
        _raise_for_embedding_status(response)
        payload = response.json()
        embeddings.extend(_embeddings_from_payload(payload, expected_count=len(batch)))
        parse_ms += elapsed_ms(parse_started_at)

    emit_timing(
        "rag.embedding_timing",
        total_ms=elapsed_ms(total_started_at),
        request_ms=request_ms,
        parse_ms=parse_ms,
        model=settings.dashscope_embedding_model,
        text_count=len(texts),
        text_chars=sum(len(text) for text in texts),
        dimensions=settings.embedding_dimensions,
        batch_count=len(batches),
        batch_size=DASHSCOPE_EMBEDDING_MAX_BATCH_SIZE,
        status_code=status_code,
    )
    return embeddings


def _embedding_batches(texts: list[str]) -> list[list[str]]:
    return [
        texts[index:index + DASHSCOPE_EMBEDDING_MAX_BATCH_SIZE]
        for index in range(0, len(texts), DASHSCOPE_EMBEDDING_MAX_BATCH_SIZE)
    ]


def _post_embedding_batch(batch: list[str]) -> httpx.Response:
    return httpx.post(
        f"{settings.dashscope_base_url.rstrip('/')}/embeddings",
        headers={
            "Authorization": f"Bearer {settings.dashscope_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": settings.dashscope_embedding_model,
            "input": batch,
            "dimensions": settings.embedding_dimensions,
            "encoding_format": "float",
        },
        timeout=60,
    )


def _raise_for_embedding_status(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = (response.text or "").strip()
        if len(detail) > 2000:
            detail = detail[:2000] + "..."
        raise RuntimeError(
            f"DashScope embeddings request failed with HTTP {response.status_code}: {detail or exc}"
        ) from exc


def _embeddings_from_payload(payload: dict, *, expected_count: int) -> list[list[float]]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("DashScope embeddings response missing data list.")
    indexed_items = sorted(
        (item for item in data if isinstance(item, dict)),
        key=lambda item: int(item.get("index", 0)),
    )
    embeddings = [item.get("embedding") for item in indexed_items]
    if len(embeddings) != expected_count:
        raise RuntimeError(
            f"DashScope embeddings response count mismatch: expected {expected_count}, got {len(embeddings)}."
        )
    if not all(isinstance(embedding, list) for embedding in embeddings):
        raise RuntimeError("DashScope embeddings response contains invalid embedding payload.")
    return embeddings  # type: ignore[return-value]
