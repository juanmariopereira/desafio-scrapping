from __future__ import annotations

import json
import uuid
from typing import Annotated

import aio_pika
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.schemas import ScrapeAccepted, ScrapeRequest, TaskResultResponse
from app.core.config import Settings, get_settings
from app.services.task_store import TaskStore

router = APIRouter()


def get_task_store(request: Request) -> TaskStore:
    return request.app.state.task_store


def get_app_settings() -> Settings:
    return get_settings()


@router.post("/scrape", response_model=ScrapeAccepted, status_code=202)
async def enqueue_scrape(
    body: ScrapeRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    store: Annotated[TaskStore, Depends(get_task_store)],
) -> ScrapeAccepted:
    task_id = str(uuid.uuid4())
    await store.create_pending(task_id, body.cnpj)

    channel: aio_pika.abc.AbstractChannel = request.app.state.rabbit_channel
    payload = json.dumps({"task_id": task_id, "cnpj": body.cnpj}).encode("utf-8")
    message = aio_pika.Message(
        body=payload,
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )
    try:
        await channel.default_exchange.publish(
            message,
            routing_key=settings.queue_name,
        )
    except Exception:
        await store.delete(task_id)
        raise HTTPException(
            status_code=503,
            detail="Fila de mensagens indisponível; tente novamente.",
        ) from None
    return ScrapeAccepted(task_id=task_id)


@router.get("/results/{task_id}", response_model=TaskResultResponse)
async def get_result(
    task_id: str,
    store: Annotated[TaskStore, Depends(get_task_store)],
) -> TaskResultResponse:
    data = await store.get(task_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    return TaskResultResponse.model_validate(data)
