import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount, Route

from app.api import (
    accounts,
    ai_access,
    analytics,
    assets,
    auth,
    crawl_tasks,
    followed_creators,
    imports,
    media,
    model_access,
    pipeline,
    skills,
    subscriptions,
    sync_switches,
    system,
)
from app.config import settings
from app.core import security
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
    from app.services.legacy_llm_migration import bootstrap_legacy_llm

    claim = bootstrap_legacy_llm()
    if claim is not None:
        logging.getLogger("platform.startup").info("旧模型配置：%s", claim.message)
    start_scheduler()
    _prewarm_storage_index()
    yield
    stop_scheduler()


def _prewarm_storage_index() -> None:
    """后台预热存储根文件索引。

    总览/素材列表要按 storage_root + relative_path 统计体积，而首次整树遍历在
    NAS 大库（71k+ 文件、245G）上冷启动可达一分钟以上，用户会看到总览长时间转圈。
    启动时用一个后台线程先把索引建好，接口只读内存缓存。
    """
    import threading

    def _run() -> None:
        from sqlalchemy import select

        from app.core import storage
        from app.db import SessionLocal
        from app.models import StorageRoot

        db = SessionLocal()
        try:
            try:
                roots = db.scalars(select(StorageRoot)).all()
            except Exception as exc:  # noqa: BLE001 表尚未就绪/数据库瞬断不是预热错误
                logging.getLogger("platform.startup").info(
                    "跳过存储索引预热：数据库暂不可用（%s）", type(exc).__name__
                )
                return
            for root in roots:
                try:
                    storage.cached_root_file_sizes(root.path)
                    storage.cached_dir_size(root.path)
                except Exception:  # noqa: BLE001 预热失败不影响启动
                    logging.getLogger("platform.startup").warning(
                        "存储索引预热失败：%s", root.path, exc_info=True
                    )
        finally:
            db.close()

    threading.Thread(target=_run, name="storage-index-prewarm", daemon=True).start()


app = FastAPI(title="platform-api", version=load_build_info().version, lifespan=lifespan)

# 开发期允许 Vite dev server 跨域；生产同源部署不受影响
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Apply browser boundaries to every response, including errors and media."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "media-src 'self' blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none'",
    )
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    if request.url.scheme == "https" or forwarded == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(subscriptions.router)
app.include_router(followed_creators.router)
app.include_router(sync_switches.router)
app.include_router(assets.router)
app.include_router(crawl_tasks.router)
app.include_router(imports.router)
app.include_router(media.router)
app.include_router(pipeline.router)
app.include_router(analytics.router)
app.include_router(ai_access.router)
app.include_router(skills.router)
app.include_router(model_access.router)
app.include_router(system.router)
app.include_router(system.public_router)


@app.get("/api/events/stream", include_in_schema=False, dependencies=[Depends(security.require_admin)])
async def events_stream(request: Request):
    from fastapi.responses import StreamingResponse

    raw = request.headers.get("Last-Event-ID", "").strip()
    if not raw:
        raw = request.query_params.get("last_event_id", "").strip()
    try:
        after_id = max(0, int(raw)) if raw else 0
    except ValueError:
        after_id = 0
    return StreamingResponse(
        subscribe(after_id=after_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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

    # Remove the development-only ``GET /`` route (if present).  Otherwise it
    # wins over the SPA fallback and production tests silently serve the JSON
    # dev banner instead of index.html.
    target_app.router.routes[:] = [
        route
        for route in target_app.router.routes
        if not (isinstance(route, Route) and route.path == "/" and "GET" in (route.methods or set()))
    ]
    # A router built after startup cannot use ``app.mount`` because Starlette's
    # router has already frozen its startup hook.  Mount objects also work for
    # a not-yet-started app and let the isolated acceptance harness add the
    # production frontend to an already imported app.
    target_app.router.routes.append(
        Mount("/assets", app=_HashedStatic(directory=static_dir / "assets"), name="assets")
    )

    def _index_response() -> FileResponse:
        resp = FileResponse(static_dir / "index.html")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    def _spa_fallback(request: Request, full_path: str = ""):
        # ``full_path`` is URL-decoded by Starlette.  A request like
        # ``/%2e%2e/secret`` therefore arrives as ``../secret`` and a plain
        # ``static_dir / full_path`` would serve files outside the build.
        # Resolve and require containment in the static directory.
        base = static_dir.resolve()
        try:
            candidate = (base / full_path).resolve()
        except (OSError, RuntimeError, ValueError):
            return _index_response()
        if full_path and (candidate == base or candidate.is_relative_to(base)) and candidate.is_file():
            resp = FileResponse(candidate)
            resp.headers["Cache-Control"] = "no-cache"
            return resp
        return _index_response()

    target_app.router.routes.append(
        Route("/{full_path:path}", endpoint=_spa_fallback, methods=["GET"], name="spa-fallback")
    )


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
