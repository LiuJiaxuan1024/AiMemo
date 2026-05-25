# 安装与启动

这份文档面向第一次 clone AiMemo 的用户，目标是尽量用最少步骤把本地 Web 应用跑起来。

## 环境要求

推荐环境：

```text
Python 3.12
Node.js 20 或更高版本
npm
Git
```

说明：

```text
后端依赖声明为 Python >=3.11,<3.14。
当前推荐 Python 3.12，因为 LangGraph / LangChain / sqlite-vec 生态在 3.12 上更稳。
不建议用 Python 3.14 启动后端。
```

## 1. 克隆项目

```powershell
git clone https://github.com/LiuJiaxuan1024/AiMemo.git
cd AiMemo
```

## 2. 配置 API Key

复制环境变量模板：

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

Linux / macOS：

```bash
cp .env.example .env
```

编辑 `.env`，填入阿里百炼 DashScope API Key：

```text
DASHSCOPE_API_KEY=你的百炼 API Key
```

当前默认模型配置：

```text
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSIONS=1024
```

Local Operator 默认授权仓库根目录和当前用户 Home 目录，用于受控文件读取、写入和命令工作目录。需要追加更多目录时，
可以在 `.env` 中配置：

```text
LOCAL_OPERATOR_WORKSPACE_ROOTS=E:\Ai记;D:\资料;~/Documents
```

多个路径可用分号或逗号分隔。敏感文件、数据库文件和密钥文件仍会被默认拦截。

`.env` 放在仓库根目录即可。后端配置会同时兼容从仓库根目录或 `backend/` 目录启动。

## 3. 一键开发启动

开发时推荐用一键脚本同时启动后端、Vite 前端和桌面外置精灵。

Windows PowerShell：

```powershell
.\scripts\start-dev.ps1
```

Linux / macOS：

```bash
chmod +x scripts/start-dev.sh scripts/start-backend.sh scripts/start-frontend.sh
./scripts/start-dev.sh
```

启动后访问：

```text
后端 API:     http://127.0.0.1:8000
前端开发页:   http://127.0.0.1:5173/app/
API 文档:     http://127.0.0.1:8000/docs
桌面精灵:     Memo Elf Tauri 透明窗口
```

一键脚本会在启动前刷新后端托管入口使用的前端产物：
`http://127.0.0.1:8000/app` 读取的是 `frontend/dist`，不是 Vite 的
`5173` 开发产物。脚本会检查 `dist/index.html` 是否缺失或比前端源码旧，
必要时自动执行 `npm run build`。Windows 后端开发启动默认启用 uvicorn
reload，便于 Python 代码改动后自动生效。

依赖已经安装好时，可以跳过安装：

Windows PowerShell：

```powershell
.\scripts\start-dev.ps1 -SkipInstall
```

Linux / macOS：

```bash
./scripts/start-dev.sh --skip-install
```

如果只想调试 Web，不启动桌面精灵：

Windows PowerShell：

```powershell
.\scripts\start-dev.ps1 -NoDesktop
```

Linux / macOS：

```bash
./scripts/start-dev.sh --no-desktop
```

停止所有开发进程：

Windows PowerShell：

```powershell
.\scripts\stop-dev.ps1
```

Linux / macOS：

```bash
./scripts/stop-dev.sh
```

`stop-dev` 会停止后端、Vite 前端、Tauri desktop webview 和残留的 Memo Elf 桌面进程，避免重复启动后出现多个精灵。

### Python 3.12 虚拟环境策略

后端必须使用 Python 3.12。启动脚本会检查 `backend/.venv`：

```text
backend/.venv 不存在
  -> 用 Python 3.12 创建。

backend/.venv 存在但不是 Python 3.12
  -> 删除旧虚拟环境并用 Python 3.12 重建。
```

Windows 下如果没有 Python 3.12，脚本会尝试用 `winget install Python.Python.3.12` 安装。

Linux / macOS 下脚本不会静默安装系统 Python，而是提示用户安装。示例：

```bash
# Ubuntu / Debian
sudo apt install python3.12 python3.12-venv

# Fedora
sudo dnf install python3.12

# macOS
brew install python@3.12
```

## 4. 单独启动后端

推荐使用脚本：

Windows PowerShell：

```powershell
.\scripts\start-backend.ps1
```

Linux / macOS：

```bash
chmod +x scripts/start-backend.sh scripts/start-frontend.sh
./scripts/start-backend.sh
```

脚本会做这些事：

```text
创建 backend/.venv
安装后端依赖
启动 FastAPI 服务
```

后端地址：

```text
http://127.0.0.1:8000
```

API 文档：

```text
http://127.0.0.1:8000/docs
```

后续依赖已经装好时，可以跳过安装：

Windows PowerShell：

```powershell
.\scripts\start-backend.ps1 -SkipInstall
```

Linux / macOS：

```bash
./scripts/start-backend.sh --skip-install
```

## 5. 构建并访问前端

后端网关会托管 `frontend/dist`，正常使用不需要单独启动 Vite。先构建前端：

Windows PowerShell / Linux / macOS：

```powershell
cd frontend
npm install
npm run build
cd ..
```

构建完成后，访问后端统一入口：

```text
http://127.0.0.1:8000/app
```

如果你正在开发前端，需要热更新，再另开一个终端窗口启动 Vite。

Windows PowerShell：

```powershell
.\scripts\start-frontend.ps1
```

Linux / macOS：

```bash
./scripts/start-frontend.sh
```

脚本会安装前端依赖并启动 Vite dev server。

```text
http://127.0.0.1:5173/app/
```

主要前端路由：

```text
/app/memo
/app/chat
/app/workshop/jobs
/app/workshop/memories
```

后续依赖已经装好时，可以跳过安装：

Windows PowerShell：

```powershell
.\scripts\start-frontend.ps1 -SkipInstall
```

Linux / macOS：

```bash
./scripts/start-frontend.sh --skip-install
```

## 6. 手动启动

后端：

Windows PowerShell：

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Linux / macOS：

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端生产构建：

```powershell
cd frontend
npm install
npm run build
```

Linux / macOS 同样使用：

```bash
cd frontend
npm install
npm run build
```

如果你正在开发前端，需要热更新，可以使用 Vite dev server：

```powershell
cd frontend
npm run dev -- --host 127.0.0.1
```

开发服务地址：

```text
http://127.0.0.1:5173/app/
```

## 6. 验证

后端健康检查：

Windows PowerShell：

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing
```

Linux / macOS：

```bash
curl http://127.0.0.1:8000/api/health
```

预期返回：

```json
{"status":"ok"}
```

前端验证：

```text
打开 http://127.0.0.1:8000/app
创建一条笔记
进入 /app/workshop/jobs 查看后台任务
进入 /app/chat 询问与笔记相关的问题
```

## 常见问题

### PowerShell 不允许执行脚本

如果遇到执行策略限制，可以只对当前用户放开：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

也可以直接手动启动，不使用脚本。

### Linux 脚本没有执行权限

执行：

```bash
chmod +x scripts/start-backend.sh scripts/start-frontend.sh
```

然后重新运行脚本。

### 后端提示缺少 DASHSCOPE_API_KEY

检查仓库根目录 `.env` 是否存在，并确认：

```text
DASHSCOPE_API_KEY=你的百炼 API Key
```

如果你在系统环境变量里配置了同名变量，系统环境变量会优先生效。

### 8000 端口被占用

查看占用进程：

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
```

停止旧后端进程后重新启动。

### 前端请求后端失败

确认后端健康检查正常：

```text
http://127.0.0.1:8000/api/health
```

正常入口是 `http://127.0.0.1:8000/app`，前端会使用同源 `/api/*`。
如果你使用 Vite dev server，请确认 `VITE_API_BASE_URL` 指向后端，或后端 CORS 配置包含开发服务 origin。

### 精灵没有显示或连接失败

当前推荐使用桌面外置精灵。确认后端健康检查正常，并确认桌面端监听：

```text
http://127.0.0.1:1420
```

如果只启动后端和 Vite 前端，不会显示外置精灵。外置精灵由 `desktop/`
里的 Tauri 进程提供。推荐直接使用：

```powershell
.\scripts\start-dev.ps1
```

浏览器内精灵默认关闭，因为主精灵已经迁移到桌面外置窗口。需要调试旧 Web 精灵时，可以临时设置：

```powershell
$env:VITE_ENABLE_WEB_ELF="true"
.\scripts\start-frontend.ps1
```
