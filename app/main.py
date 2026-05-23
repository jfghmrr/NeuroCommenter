from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from .ai.providers import seed_keys_from_env, seed_proxies_from_env
from .api.routes import public, router
from .config import get_settings
from .db import init_db, session_scope
from .models import DEFAULT_SETTINGS, Setting
from .tg.client import tg
from .worker import attach_handlers, loop_dispatcher

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"


async def _seed_settings():
    from sqlalchemy import select

    async with session_scope() as s:
        for k, v in DEFAULT_SETTINGS.items():
            row = (
                await s.execute(select(Setting).where(Setting.key == k))
            ).scalar_one_or_none()
            if not row:
                s.add(Setting(key=k, value=v))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _seed_settings()
    await seed_keys_from_env()
    await seed_proxies_from_env()
    # пытаемся подключить TG (без логина — просто connect)
    try:
        await tg.ensure_client()
        if await tg.is_authorized():
            await attach_handlers()
    except Exception as e:
        logger.warning(f"TG не подключился на старте: {e}")
    worker_task = asyncio.create_task(loop_dispatcher())
    logger.info("NeuroCommenter запущен")
    try:
        yield
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except Exception:
            pass
        if tg.client:
            try:
                await tg.client.disconnect()
            except Exception:
                pass


app = FastAPI(title="NeuroCommenter", version="1.0", lifespan=lifespan)
app.include_router(public)
app.include_router(router)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
async def index():
    from fastapi import Request  # noqa
    return FileResponse(str(STATIC / "index.html"))


@app.get("/login")
async def login_page():
    return FileResponse(str(STATIC / "login.html"))


def run():
    import uvicorn

    s = get_settings()
    uvicorn.run("app.main:app", host=s.web_host, port=s.web_port, reload=False)


if __name__ == "__main__":
    run()
