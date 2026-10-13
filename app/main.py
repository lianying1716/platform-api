import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    accounts,
    ai_access,
    analytics,
    assets,
    auth,
    crawl_tasks,
    followed_creators,
    imports,
    model_access,
    pipeline,
    skills,
    subscriptions,
    sync_switches,
    system,
)
from app.config import settings
from app.core.events import subscribe
from app.core.scheduler import start_scheduler, stop_scheduler
from app.core.version import load_build_info
from app.core.version_guard import check as check_version_guard
from app.db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    # 契约 I3：版本门禁在对外服务前执行；不通过则拒绝启动（fail fast）。
    guard = check_version_guard(
        db_url=settings.resolved_database_url,
        auto_migrate=settings.auto_migrate,
        allow_unknown=settings.allow_unknown_db,
        # 生产由 Alembic 负责 schema，严格门禁；开发用 create_all，宽松只告警。
        enforce=not settings.auto_create_tables,
    )
    if not guard.ok:
        raise RuntimeError(f"版本门禁未通过（{guard.action}）：{guard.message}")
    if guard.action != "ok":
        logging.getLogger("platform.startup").info("版本门禁：%s", guard.message)
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="platform-api", version=load_build_info().version, lifespan=lifespan)

# 开发期允许 Vite dev server 跨域；生产同源部署不受影响
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)

app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(subscriptions.router)
app.include_router(followed_creators.router)
app.include_router(sync_switches.router)
app.include_router(assets.router)
app.include_router(crawl_tasks.router)
app.include_router(imports.router)
app.include_router(pipeline.router)
app.include_router(analytics.router)
app.include_router(ai_access.router)
app.include_router(skills.router)
app.include_router(model_access.router)
app.include_router(system.router)
app.include_router(system.public_router)


@app.get("/api/events/stream", include_in_schema=False)
async def events_stream():
    from fastapi.responses import StreamingResponse

    return StreamingResponse(subscribe(), media_type="text/event-stream")


def mount_frontend(target_app: FastAPI, static_dir: Path) -> None:
    """挂载正式前端产物（仅 PLATFORM_SERVE_FRONTEND=true 时调用）并设置缓存策略。

    - index.html / SPA 回退：**no-cache**，每次回源校验。否则浏览器会用启发式缓存把
      index.html 缓存住，升级后页面仍引用旧的 hashed bundle，用户看到旧界面——本平台
      实测踩过：升级到新版后前端仍显示"手填版本号"的旧升级交互。
    - /assets 下带 hash 的资源：immutable 长缓存（文件名含 hash，内容一变名字就变）。
    """
    if not static_dir.is_dir():
        raise RuntimeError(
            "PLATFORM_SERVE_FRONTEND=true 但未找到前端构建产物 app/static/；"
            "请先构建正式包（./dev.sh web 或 cd web && npm run build:release）或关闭该开关。"
        )

    class _HashedStatic(StaticFiles):
        """带内容哈希的 /assets 资源：可长缓存（immutable）。"""

        def file_response(self, *args, **kwargs):  # type: ignore[override]
            resp = super().file_response(*args, **kwargs)
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp

    target_app.mount("/assets", _HashedStatic(directory=static_dir / "assets"), name="assets")

    def _index_response() -> FileResponse:
        resp = FileResponse(static_dir / "index.html")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @target_app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        candidate = static_dir / full_path
        if full_path and candidate.is_file():
            resp = FileResponse(candidate)
            resp.headers["Cache-Control"] = "no-cache"
            return resp
        return _index_response()


# 前端托管（仅正式包）：由 PLATFORM_SERVE_FRONTEND 控制，开发默认关闭。
# 这样开发时后端只提供 /api，前端走 Vite dev server，不会误托管 app/static 里的旧构建产物。
if settings.serve_frontend:
    mount_frontend(app, _STATIC_DIR)
else:
    @app.get("/", include_in_schema=False)
    def dev_index():
        # 开发模式：后端不托管前端，给出明确指引（否则访问根路径会 404 到令人困惑）
        return {
            "service": "platform-api (dev)",
            "frontend": "开发模式不由后端托管前端，请访问 Vite dev server：http://<host>:5173",
            "api_docs": "/docs",
        }


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
