# 前端说明

前端位于 `frontend/`，使用 Vite + React + TypeScript。

## 目录说明

```text
frontend/src/
  features/  按功能拆分的前端模块
  services/  API 请求封装
  types/     前端类型定义
  App.tsx    当前主界面
  main.tsx   React 入口
  styles.css 全局样式
```

## 当前界面

当前实现一个基础工作台：

- 左侧笔记列表
- 右侧笔记创建表单
- 笔记详情展示区
- AI 摘要和标签展示
- 精灵入口占位区
- 右侧 Job Drawer，用于观察后台任务和 graph 状态
- 对话窗口，用于发送 Memory Chat Graph 消息、流式显示回答
- 每条 AI 回复可打开 Graph 调试面板，查看 Mermaid 图、上下文金字塔和检索证据

## 相关文档

- [Job Drawer](./job-drawer.md)
- [Chat Window](./chat-window.md)
- [精灵助手](./elf-assistant.md)
- [精灵事件总线](./elf-event-bus.md)
- [原创精灵设计](./elf-character-design.md)

## API 地址

前端默认请求：

```text
http://127.0.0.1:8000
```

如需调整，可以使用环境变量：

```text
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## 构建

```powershell
cd frontend
npm install
npm run build
```

构建产物生成在 `frontend/dist/`，不进入版本管理。
