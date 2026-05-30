# 本地开发

## 环境要求

- Python 3.11-3.13，推荐 Python 3.12
- Node.js
- npm

当前开发环境验证版本：

```text
Python 3.12.10
Node.js v23.9.0
npm 10.9.2
```

说明：

```text
不建议使用 Python 3.14 运行后端。
当前 LangGraph/LangChain/sqlite-vec 生态对 Python 3.12 支持更稳定。
项目可以在系统 Python 3.14 不变的情况下，只让 backend/.venv 使用 Python 3.12。
```

## 后端启动

如需启用 Agent 模型能力，先设置百炼 API Key：

```powershell
$env:DASHSCOPE_API_KEY="你的百炼 API Key"
```

Ai 记默认关闭 LangSmith tracing。原因：

```text
本地开源应用默认不应上传 graph trace。
用户笔记、对话和 graph trace 都可能包含私人信息。
```

如果系统环境变量里已经显式设置 `LANGSMITH_TRACING=true`，应用会尊重该配置。
也可以用 Ai 记自己的开关显式打开：

```powershell
$env:AIJI_ENABLE_LANGSMITH_TRACING="true"
```

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

后端默认地址：

```text
http://127.0.0.1:8000
```

API 文档：

```text
http://127.0.0.1:8000/docs
```

## 前端启动

常规开发建议先构建前端，由后端网关托管 `/app`：

```powershell
cd frontend
npm install
npm run build
```

访问：

```text
http://127.0.0.1:8000/app
```

如果你正在调试 React 组件，需要热更新，再启动 Vite：

```powershell
cd frontend
npm install
npm run dev
```

Vite 开发服务地址：

```text
http://127.0.0.1:5173/app/
```

## 验证命令

后端 smoke test 可通过 FastAPI TestClient 或直接访问：

```text
GET  /api/health
POST /api/notes
GET  /api/notes
GET  /api/notes/{id}
```

前端构建验证：

```powershell
cd frontend
npm run build
```

后端测试：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
```

## 运行时配置

`config.json5` 用于保存可提交的项目级默认值，`.env` 和系统环境变量仍拥有更高优先级。

当前前端和桌面精灵会通过运行时接口读取开关：

```text
GET /api/config/runtime
```

重点字段：

```json5
{
  "elf": {
    "enabled": true,
  },
  "voice": {
    "enabled": true,
    "asr_provider": "aliyun_dashscope",
    "tts_provider": "aliyun_dashscope",
    "voice_design_provider": "aliyun_dashscope",
  },
}
```

`elf.enabled=false` 时不加载 Web/桌面精灵，但不影响 `/app/workshop/*` 页面。
桌面精灵启动时如果后端配置接口还没准备好，会等待并重试；只有明确读到 `false` 才保持隐藏。

## 常用验证

```powershell
cd frontend
npm run build
```

```powershell
cd desktop
npm run web:build
```

```powershell
python -m pytest backend/tests/test_app_config_api.py backend/tests/test_elf_voice_api.py backend/tests/test_voice_profile_service.py -q
```
