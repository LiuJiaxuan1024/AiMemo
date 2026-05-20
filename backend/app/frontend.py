from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


def mount_frontend_app(app: FastAPI) -> None:
    """把 AiMemo 前端构建产物挂载到后端统一入口。

    产品入口统一为 http://127.0.0.1:8000/app。开发期仍可单独启动 Vite，
    但桌面精灵和普通用户不再依赖 5173 端口是否存在。
    """

    dist_dir = _frontend_dist_dir()
    assets_dir = dist_dir / "assets"
    index_file = dist_dir / "index.html"

    @app.get("/", include_in_schema=False)
    def redirect_to_app() -> RedirectResponse:
        return RedirectResponse(url="/app")

    if assets_dir.exists():
        app.mount("/app/assets", StaticFiles(directory=assets_dir), name="aimemo-assets")

    @app.get("/app", include_in_schema=False)
    @app.get("/app/{path:path}", include_in_schema=False)
    def serve_app(path: str = "") -> FileResponse:
        """返回 SPA 入口文件。

        React Router 或浏览器刷新 /app/... 时都应回退到 index.html。
        如果用户还没构建前端，返回清晰错误，避免误以为后端没起来。
        """

        if not index_file.exists():
            raise HTTPException(
                status_code=503,
                detail="AiMemo frontend is not built. Run `npm run build` in frontend/ first.",
            )
        return FileResponse(index_file)


def _frontend_dist_dir() -> Path:
    """定位仓库内 frontend/dist。

    当前文件位于 backend/app/frontend.py，因此 parents[2] 是仓库根目录。
    """

    return Path(__file__).resolve().parents[2] / "frontend" / "dist"
