# Memo Elf / AiMemo 文档

本文档目录按项目结构和主题组织。Memo Elf 是桌面精灵本体，AiMemo 是它的第一个记忆能力。建议先读“当前实现”，再按需要查看设计草案和历史报告。

## 推荐阅读路径

1. [安装与启动](./setup.md)
2. [架构概览](./architecture/overview.md)
3. [流程图](./architecture/flows.md)
4. [Memo 知库模块设计](./architecture/knowledge-base-module.md)
5. [Memo 知库第一版工程设计](./architecture/knowledge-base-implementation-design.md)
6. [从本地 OCR 切换到 qwen-vl-ocr](./backend/qwen-vl-ocr-migration.md)
7. [本地任务系统](./backend/jobs.md)
8. [Memory Chat Graph](./agent/memory-chat-graph.md)
9. [长期记忆管理](./backend/memories.md)
10. [Local Operator Agent](./agent/local-operator-agent.md)
11. [前后台任务边界](./agent/background-vs-foreground.md)
12. [前端说明](./frontend/README.md)
13. [Memo Elf 桌面化架构](./desktop/memo-elf-desktop-architecture.md)
14. [语音工坊第一版设计](./desktop/voice-workshop-design.md)

## 当前实现

### 架构与启动

- [架构概览](./architecture/overview.md)
- [流程图](./architecture/flows.md)
- [Memo 知库模块设计](./architecture/knowledge-base-module.md)
- [Memo 知库第一版工程设计](./architecture/knowledge-base-implementation-design.md)
- [安装与启动](./setup.md)
- [本地开发](./development.md)

### 后端

- [后端说明](./backend/README.md)
- [笔记智能处理](./backend/note-processing.md)
- [本地任务系统](./backend/jobs.md)
- [后台进程任务](./backend/background-tasks.md)
- [对话持久化](./backend/conversations.md)
- [长期记忆管理](./backend/memories.md)
- [精灵事件中心](./backend/elf-events.md)
- [向量存储](./backend/vector-storage.md)
- [向量检索](./backend/vector-search.md)
- [从本地 OCR 切换到 qwen-vl-ocr](./backend/qwen-vl-ocr-migration.md)
- [阿里云远程语音能力接入设计](./desktop/aliyun-voice-provider.md)

### Agent / Graph

- [Agent 设计](./agent/README.md)
- [Note Metadata Graph](./agent/note-metadata-graph.md)
- [Note Embedding Graph](./agent/note-embedding-graph.md)
- [Memory Chat Graph](./agent/memory-chat-graph.md)
- [Context Pyramid](./agent/context-pyramid.md)
- [聊天模型 Provider 适配设计](./agent/model-provider-adapter.md)
- [远程服务器操作失败复盘与改进说明](./agent/remote-server-operation-failure-analysis.md)
- [Conversation Summary Graph](./agent/conversation-summary-graph.md)
- [Conversation Memory Graph](./agent/conversation-memory-graph.md)
- [Conversation Title Graph](./agent/conversation-title-graph.md)
- [Memory Consolidation](./agent/memory-consolidation.md)
- [Local Operator Agent](./agent/local-operator-agent.md)
- [前后台任务边界](./agent/background-vs-foreground.md)
- [Claude-Code Agent 设计借鉴](./agent/claude-code-agent-lessons.md)

### 前端

- [前端说明](./frontend/README.md)
- [前端模块路由](./frontend/module-decoupling.md)
- [Chat Window](./frontend/chat-window.md)
- [Workshop / Job Graph](./frontend/job-drawer.md)
- [后台任务抽屉](./frontend/background-tasks-drawer.md)
- [精灵助手](./frontend/elf-assistant.md)
- [精灵事件总线](./frontend/elf-event-bus.md)
- [原创精灵设计](./frontend/elf-character-design.md)
- [精灵图片生成提示词模板](./frontend/elf-image-prompts.md)

### 桌面精灵

- [Memo Elf 桌面化架构](./desktop/memo-elf-desktop-architecture.md)
- [外置精灵聊天](./desktop/elf-external-chat.md)
- [阿里云远程语音能力接入设计](./desktop/aliyun-voice-provider.md)
- [语音工坊第一版设计](./desktop/voice-workshop-design.md)

### API

- [Notes API](./api/notes.md)
- [Jobs API](./api/jobs.md)
- [Background Tasks API](./api/background-tasks.md)
- [Search API](./api/search.md)
- [Conversations API](./api/conversations.md)
- [Chat API](./api/chat.md)
- [Knowledge API](./api/knowledge.md)
- [Memories API](./api/memories.md)
- [Runtime Config API](./api/runtime-config.md)
- [Voice API](./api/voice.md)

## 设计草案与历史报告

这些文档记录了讨论过程和阶段性判断，不一定完全代表当前代码状态。阅读时请优先以“当前实现”部分的文档为准。

- [Memory Chat Graph 设计草案](./agent/memory-chat-graph-design.md)
- [前端体验优化报告](./frontend/ui-optimization-report.md)
