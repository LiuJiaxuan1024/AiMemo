import json

import httpx
import pytest

from app.agent.embeddings import embed_texts
from app.core.config import settings


def test_embed_texts_batches_dashscope_embedding_requests(monkeypatch):
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(settings, "dashscope_base_url", "https://dashscope.test/compatible-mode/v1")
    monkeypatch.setattr(settings, "dashscope_embedding_model", "text-embedding-v4")
    monkeypatch.setattr(settings, "embedding_dimensions", 4)
    requests: list[dict] = []

    def fake_post(url, *, headers, json, timeout):  # noqa: A002, ARG001
        requests.append(json)
        data = [
            {"index": index, "embedding": [float(index), 0.0, 0.0, 0.0]}
            for index, _ in enumerate(json["input"])
        ]
        return httpx.Response(200, json={"data": data}, request=httpx.Request("POST", url))

    monkeypatch.setattr("app.agent.embeddings.httpx.post", fake_post)

    embeddings = embed_texts([f"text-{index}" for index in range(23)])

    assert [len(request["input"]) for request in requests] == [10, 10, 3]
    assert all(request["model"] == "text-embedding-v4" for request in requests)
    assert all(request["dimensions"] == 4 for request in requests)
    assert all(request["encoding_format"] == "float" for request in requests)
    assert len(embeddings) == 23


def test_embed_texts_includes_dashscope_error_body(monkeypatch):
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")

    def fake_post(url, *, headers, json, timeout):  # noqa: A002, ARG001
        return httpx.Response(
            400,
            content=json_dumps({"code": "InvalidParameter", "message": "input size should be less than 10"}),
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.agent.embeddings.httpx.post", fake_post)

    with pytest.raises(RuntimeError, match="input size should be less than 10"):
        embed_texts(["hello"])


def json_dumps(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
