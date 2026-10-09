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
    pipeline,
    skills,
    subscriptions,
    sync_switches,
    system,
)
from app.core.events import subscribe
from app.core.scheduler import start_scheduler, stop_scheduler
from app.db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="platform-api", version="0.1.0", lifespan=lifespan)

# 开发期允许 Vite dev server 跨域；生产同源部署不受影响
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
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
app.include_router(system.router)


@app.get("/api/events/stream", include_in_schema=False)
async def events_stream():
    from fastapi.responses import StreamingResponse

    return StreamingResponse(subscribe(), media_type="text/event-stream")


# 生产模式：托管前端构建产物（web/dist 构建后复制/软链到 app/static），SPA 回退
if _STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        candidate = _STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")


def run() -> None:
    import uvicorn

    from app.config import settings

    uvicorn.run("app.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
