# Search API

Search API 负责查询本地向量库。当前只支持笔记 chunk 检索。

## 搜索笔记

```http
GET /api/search/notes?q=我之前说过想吃什么&limit=5
```

也支持 POST：

```http
POST /api/search/notes
Content-Type: application/json

{
  "query": "我之前说过想吃什么",
  "limit": 5
}
```

## 响应

```json
{
  "query": "我之前说过想吃什么",
  "limit": 5,
  "results": [
    {
      "note_id": 4,
      "note_title": "今天中午想吃炸鸡",
      "chunk_id": 4,
      "chunk_index": 0,
      "content": "今天中午想吃炸鸡",
      "content_hash": "...",
      "token_count": 8,
      "distance": 0.12,
      "score": 0.89
    }
  ]
}
```

字段说明：

- `distance`: sqlite-vec 返回的距离，越小越相似。
- `score`: 后端基于 distance 转换出的调试分数，越大越相似。
- `chunk_id`: 对应 `notechunk.id`，同时也是 `vec_note_chunks.rowid`。

## 当前限制

- 只查询 `notechunk`。
- 不做 query rewrite。
- 不做多源检索。
- 不做 LLM rerank。

这些能力会在 `memory_chat_graph` 和后续 RAG 子图中逐步加入。

