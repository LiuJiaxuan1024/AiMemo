# Jobs API

Jobs API 用于观察本地后台任务和对应 LangGraph 流程图。

## 获取任务列表

```text
GET /api/jobs
```

可选参数：

```text
limit=50
```

## 获取任务详情

```text
GET /api/jobs/{job_id}
```

## 获取任务流程图

```text
GET /api/jobs/{job_id}/graph
```

响应包含：

```json
{
  "job_id": 1,
  "graph_name": "note_metadata_graph",
  "thread_id": "job:1",
  "status": "running",
  "next_nodes": ["write_metadata"],
  "mermaid": "graph TD; ..."
}
```

`mermaid` 由 LangGraph 原生 `draw_mermaid()` 生成，后端会根据 checkpoint 的 `next_nodes` 附加高亮样式。
