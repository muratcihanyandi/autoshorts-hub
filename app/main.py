"""
Uygulama giris noktasi.

- FastAPI uygulamasini olusturur, API yonlendiricilerini baglar.
- Acilista veritabani semasini olusturur, veri klasorlerini hazirlar ve arka
  plan zamanlayicisini baslatir (SCHEDULER_ENABLED=0 ile devre disi birakilabilir;
  testlerde boyle calistirilir).
- Web arayuzunu (web/ klasoru) kok adresten sunar.

Calistirma:
    uvicorn app.main:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import settings
from app.db import init_db
from app.routers import auth as auth_router
from app.routers import channels as channels_router
from app.routers import contents as contents_router
from app.routers import dashboard as dashboard_router
from app.routers import oauth as oauth_router
from app.routers import publications as publications_router
from app.routers import settings as settings_router
from app.services.scheduler import start_scheduler, stop_scheduler

_WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    os.makedirs(settings.uploads_dir, exist_ok=True)

    scheduler = None
    if os.environ.get("SCHEDULER_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    ):
        scheduler = start_scheduler()
    try:
        yield
    finally:
        if scheduler is not None:
            stop_scheduler()


def create_app() -> FastAPI:
    app = FastAPI(
        title="AutoShorts Hub",
        description=(
            "YouTube kanal istatistiklerini 7/24 izleyen ve cok kanalli yayinlari "
            "kural motoruyla yoneten pasif gelir asistani."
        ),
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(GZipMiddleware, minimum_size=1024)

    app.include_router(auth_router.router)
    app.include_router(dashboard_router.router)
    app.include_router(channels_router.router)
    app.include_router(contents_router.router)
    app.include_router(publications_router.router)
    app.include_router(settings_router.router)
    app.include_router(oauth_router.router)

    if os.path.isdir(_WEB_DIR):
        app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")

    return app


app = create_app()
