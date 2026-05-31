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

## 重试任务

```text
POST /api/jobs/{job_id}/retry
```

将失败任务重置为 `pending`，清空错误和锁信息，并等待 worker 重新领取。
当前用于知库导入、笔记处理、对话摘要/记忆等后台 graph 的失败恢复。

## 删除任务

```text
DELETE /api/jobs/{job_id}
```

返回 `204 No Content`。用于清理失败或不再需要展示的任务记录。
删除任务只移除 `jobs` 表记录，不直接删除业务数据；例如知库文档本身仍应通过 Knowledge API 的文档删除接口处理。
