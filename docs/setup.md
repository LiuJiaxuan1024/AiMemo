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

`.env` 放在仓库根目录即可。后端配置会同时兼容从仓库根目录或 `backend/` 目录启动。

## 3. 启动后端

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

## 4. 启动前端

另开一个终端窗口。

Windows PowerShell：

```powershell
.\scripts\start-frontend.ps1
```

Linux / macOS：

```bash
./scripts/start-frontend.sh
```

脚本会安装前端依赖并启动 Vite。

前端地址：

```text
http://127.0.0.1:5173
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

## 5. 手动启动

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

前端：

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1
```

Linux / macOS 同样使用：

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1
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
打开 http://127.0.0.1:5173
创建一条笔记
等待右下角精灵提示后台任务
进入对话窗口，询问与笔记相关的问题
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

如果前端不是 `http://127.0.0.1:5173`，需要在后端 CORS 配置中加入对应 origin。

### Live2D 精灵加载较慢

当前第一版精灵使用远程示例模型。网络较慢时会显示“精灵加载中”。后续计划支持本地模型资源，减少首次加载等待。
