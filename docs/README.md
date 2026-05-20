# Ai 记文档

本文档目录按项目结构和主题组织。建议先读“当前实现”，再按需要查看设计草案和历史报告。

## 推荐阅读路径

1. [安装与启动](./setup.md)
2. [架构概览](./architecture/overview.md)
3. [流程图](./architecture/flows.md)
4. [本地任务系统](./backend/jobs.md)
5. [Memory Chat Graph](./agent/memory-chat-graph.md)
6. [长期记忆管理](./backend/memories.md)
7. [Local Operator Agent](./agent/local-operator-agent.md)
8. [前端说明](./frontend/README.md)
9. [Memo Elf 桌面化架构](./desktop/memo-elf-desktop-architecture.md)

## 当前实现

### 架构与启动

- [架构概览](./architecture/overview.md)
- [流程图](./architecture/flows.md)
- [安装与启动](./setup.md)
- [本地开发](./development.md)

### 后端

- [后端说明](./backend/README.md)
- [笔记智能处理](./backend/note-processing.md)
- [本地任务系统](./backend/jobs.md)
- [对话持久化](./backend/conversations.md)
- [长期记忆管理](./backend/memories.md)
- [精灵事件中心](./backend/elf-events.md)
- [向量存储](./backend/vector-storage.md)
- [向量检索](./backend/vector-search.md)

### Agent / Graph

- [Agent 设计](./agent/README.md)
- [Note Metadata Graph](./agent/note-metadata-graph.md)
- [Note Embedding Graph](./agent/note-embedding-graph.md)
- [Memory Chat Graph](./agent/memory-chat-graph.md)
- [Context Pyramid](./agent/context-pyramid.md)
- [Conversation Summary Graph](./agent/conversation-summary-graph.md)
- [Conversation Memory Graph](./agent/conversation-memory-graph.md)
- [Memory Consolidation](./agent/memory-consolidation.md)
- [Local Operator Agent](./agent/local-operator-agent.md)

### 前端

- [前端说明](./frontend/README.md)
- [前端模块路由](./frontend/module-decoupling.md)
- [Chat Window](./frontend/chat-window.md)
- [Workshop / Job Graph](./frontend/job-drawer.md)
- [精灵助手](./frontend/elf-assistant.md)
- [精灵事件总线](./frontend/elf-event-bus.md)
- [原创精灵设计](./frontend/elf-character-design.md)
- [精灵图片生成提示词模板](./frontend/elf-image-prompts.md)

### 桌面精灵

- [Memo Elf 桌面化架构](./desktop/memo-elf-desktop-architecture.md)
- [外置精灵聊天](./desktop/elf-external-chat.md)

### API

- [Notes API](./api/notes.md)
- [Jobs API](./api/jobs.md)
- [Search API](./api/search.md)
- [Conversations API](./api/conversations.md)
- [Chat API](./api/chat.md)
- [Memories API](./api/memories.md)

## 设计草案与历史报告

这些文档记录了讨论过程和阶段性判断，不一定完全代表当前代码状态。阅读时请优先以“当前实现”部分的文档为准。

- [Memory Chat Graph 设计草案](./agent/memory-chat-graph-design.md)
- [前端体验优化报告](./frontend/ui-optimization-report.md)
