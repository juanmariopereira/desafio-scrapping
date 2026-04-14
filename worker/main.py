from __future__ import annotations

import asyncio
import json
import logging
import traceback

import aio_pika
import httpx
import redis.asyncio as redis

from app.core.config import get_settings
from app.services.task_store import TaskStatus, TaskStore
from scraper.sintegra_go import ScrapeError, run_sintegra_go_query

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")


async def handle_payload(store: TaskStore, body: bytes, settings) -> None:
    msg = json.loads(body.decode("utf-8"))
    task_id = msg["task_id"]
    cnpj = msg["cnpj"]

    await store.update_status(task_id, TaskStatus.PROCESSING)
    try:
        result = await run_sintegra_go_query(cnpj, settings)
        await store.update_status(task_id, TaskStatus.COMPLETED, result=result)
        logger.info("Tarefa concluída task_id=%s", task_id)
    except (ScrapeError, httpx.HTTPError, ValueError, KeyError) as exc:
        err = f"{exc}"
        logger.exception("Falha na tarefa task_id=%s: %s", task_id, err)
        await store.update_status(task_id, TaskStatus.FAILED, error=err)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        logger.exception("Erro inesperado task_id=%s", task_id)
        await store.update_status(task_id, TaskStatus.FAILED, error=err[:8000])


async def run_worker() -> None:
    settings = get_settings()
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    store = TaskStore(redis_client, settings.task_result_ttl_seconds)

    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=5)
    queue = await channel.declare_queue(settings.queue_name, durable=True)

    logger.info("Worker aguardando mensagens na fila '%s'...", settings.queue_name)

    async def on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            await handle_payload(store, message.body, settings)

    await queue.consume(on_message)
    await asyncio.Future()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
