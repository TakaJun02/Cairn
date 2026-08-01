"""FastAPI アプリケーションの組み立て。"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routers.health import router as health_router
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.logging import RequestIdMiddleware, configure_logging


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """将来の MQTT・ジョブワーカーを接続するプロセス寿命の境界。"""

    settings = get_settings()
    configure_logging(settings.log_level)
    logging.getLogger("app.lifecycle").info("application_started")
    try:
        # Phase 3 で MQTT、Phase 2 でジョブランナーをこの境界へ接続する。
        yield
    finally:
        await dispose_engine()
        logging.getLogger("app.lifecycle").info("application_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="鳥海山観光ガイダンス API",
        version=__version__,
        lifespan=lifespan,
    )
    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(health_router)
    return application


app = create_app()
