# 后端说明

后端位于 `backend/`，使用 FastAPI 提供 HTTP API，使用 SQLModel 访问 SQLite。

## 目录说明

```text
backend/app/
  api/       HTTP 路由
  agent/     LangGraph 相关代码
  core/      配置、数据库等基础设施
  models/    数据库模型
  rag/       chunk、hash、向量存储等 RAG 基础能力
  schemas/   API 入参和出参模型
  services/  业务逻辑
  main.py    FastAPI 应用入口
```

## 关键文件

- `app/main.py`: 创建 FastAPI 应用、注册 CORS 和路由、启动时创建数据库表。
- `app/core/config.py`: 读取 `config.json5`、`.env` 和默认配置。
- `app/core/database.py`: 创建 SQLModel engine，提供 session 依赖。
- `app/models/note.py`: 定义 `Note` 数据模型。
- `app/models/note_chunk.py`: 定义笔记 chunk 数据模型。
- `app/rag/chunking/`: 定义笔记分片策略。
- `app/rag/vector_store.py`: 管理 `sqlite-vec` 向量表。
- `app/schemas/note.py`: 定义笔记 API 的请求和响应结构。
- `app/services/note_service.py`: 封装笔记创建、列表和详情读取逻辑。
- `app/api/notes.py`: 暴露笔记相关 API。
- `app/api/health.py`: 暴露健康检查 API。
- `app/api/app_config.py`: 暴露前端 / 桌面运行时配置。
- `app/api/elf_voice.py`: 暴露桌面精灵语音模式、ASR 和 TTS 接口。
- `app/api/voice_profiles.py`: 暴露语音工坊声线 CRUD、试听和声音设计接口。
- `app/services/knowledge_image_text_service.py`: 知识库图片转文本服务，默认调用 DashScope `qwen-vl-ocr` 并做结构化解析和质量过滤。
- `app/services/knowledge_ocr_service.py`: 知识库图片转文本状态检测。默认 qwen 模式只检查 DashScope Key；`local_ocr` 模式下才检测 / 安装 Tesseract。

## 数据库

默认数据库地址：

```text
backend/data/ai_note.db
```

该文件属于本地运行时数据，不进入版本管理。

## 配置

仓库根目录的 `config.json5` 保存适合提交的项目级默认配置，例如 Local Operator
命令超时、输出截断上限和 job worker 轮询间隔。`.env` 和系统环境变量仍然拥有更高优先级，
适合放 API Key、本机路径和临时覆盖项。

当前 `exec_command` 默认超时已从 30 秒提高到 180 秒，上限 600 秒。`pip install`、
首次构建依赖等前台短命令可以直接使用这个默认值；长期运行的服务仍应走
`exec_command_background`，避免阻塞 agent 主循环。

`GET /api/config/runtime` 会返回精灵语音模式等轻量运行时状态。精灵本体是前端 / 桌面渲染组件，
不再把隐藏组件作为“关闭精灵”的运行时配置语义。

语音模块默认走阿里百炼 / DashScope 远程能力，复用 `DASHSCOPE_API_KEY`，不再要求下载本地
ASR / TTS 模型。

知识库图片转文本默认也走 DashScope `qwen-vl-ocr`，配置位于
`knowledge.image_text_extraction`：

```json5
{
  "knowledge": {
    "image_text_extraction": {
      "mode": "qwen_vl_ocr",
      "provider": "dashscope",
      "model": "qwen-vl-ocr",
      "max_image_bytes": 5242880,
      "max_images_per_document": 80,
      "min_confidence": 0.45,
      "timeout_seconds": 60,
      "max_attempts": 3,
      "retry_backoff_seconds": 0.5
    }
  }
}
```

`mode=off` 时跳过图片转文本；`mode=local_ocr` 时才启用本地 Tesseract 检测和一键安装流程。本地 OCR 不再作为 qwen-vl-ocr 缺 Key 或失败时的自动兜底，避免把噪声 OCR 结果写入知识库索引。

qwen-vl-ocr 单张图片默认最多尝试 `max_attempts=3` 次，只对超时、网络错误、限流、服务端错误和模型非 JSON 输出这类临时失败重试；图片为空、过大、格式不支持、低价值或低置信度会直接跳过/失败，不重复调用模型。

## 当前 Note 模型

```text
notes
  id
  title
  content
  summary
  tags
  processing_status
  embedding_status
  embedding_error
  embedded_at
  created_at
  updated_at
```

当前 `tags` 暂以逗号分隔字符串保存。后续如果需要更强的标签查询能力，应拆分为 `tags` 和 `note_tags` 两张表。

## 相关文档

- [笔记智能处理](./note-processing.md)
- [本地任务系统](./jobs.md)
- [对话持久化](./conversations.md)
- [长期记忆管理](./memories.md)
- [向量存储](./vector-storage.md)
- [向量检索](./vector-search.md)
- [阿里云 OSS 云存储使用规划](./aliyun-oss-storage-plan.md)
- [云存储模块设计](./cloud-storage-module-design.md)
- [从本地 OCR 切换到 qwen-vl-ocr](./qwen-vl-ocr-migration.md)
- [知识库图片明细与定向重试设计](./knowledge-image-asset-retry-design.md)
- [阿里云远程语音能力接入设计](../desktop/aliyun-voice-provider.md)
