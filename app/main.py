from contextlib import asynccontextmanager

import aio_pika
import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings
from app.services.task_store import TaskStore



@asynccontextmanager
async def lifespan(app: FastAPI):

    settings = get_settings()
    app.state.redis = redis.from_url(settings.redis_url, decode_responses=True)
    app.state.task_store = TaskStore(app.state.redis, settings.task_result_ttl_seconds)

    app.state.rabbit_connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    app.state.rabbit_channel = await app.state.rabbit_connection.channel()
    await app.state.rabbit_channel.declare_queue(settings.queue_name, durable=True)

    yield

    await app.state.rabbit_channel.close()
    await app.state.rabbit_connection.close()
    await app.state.redis.aclose()


def _cors_allow_origins(raw: str) -> list[str]:
    raw = raw.strip()
    if raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="API de scraping Sintegra GO",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_allow_origins(settings.cors_allowed_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
