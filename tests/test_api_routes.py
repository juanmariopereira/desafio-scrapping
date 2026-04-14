from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fakeredis import FakeAsyncRedis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import router
from app.core.constants import TASK_KEY_PREFIX
from app.services.task_store import TaskStore


def _test_app() -> tuple[FastAPI, TaskStore, AsyncMock, FakeAsyncRedis]:
    app = FastAPI()
    app.include_router(router)
    r = FakeAsyncRedis(decode_responses=True)
    store = TaskStore(r, ttl_seconds=3600)
    app.state.task_store = store
    ch = AsyncMock()
    ch.default_exchange.publish = AsyncMock()
    app.state.rabbit_channel = ch
    return app, store, ch, r


@pytest.mark.asyncio
async def test_post_scrape_accepted_and_enqueues() -> None:
    app, store, ch, _r = _test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/scrape",
            json={"cnpj": "00.006.486/0001-75"},
        )
    assert resp.status_code == 202
    body = resp.json()
    assert "task_id" in body
    tid = body["task_id"]
    row = await store.get(tid)
    assert row is not None
    assert row["status"] == "pending"
    assert row["cnpj"] == "00006486000175"
    ch.default_exchange.publish.assert_awaited_once()
    call = ch.default_exchange.publish.await_args
    assert call is not None
    msg = call.args[0]
    assert b"00006486000175" in msg.body


@pytest.mark.asyncio
async def test_post_scrape_publish_fails_returns_503_and_no_stale_task() -> None:
    app, _, ch, r = _test_app()
    ch.default_exchange.publish = AsyncMock(side_effect=RuntimeError("broker down"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/scrape", json={"cnpj": "00006486000175"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Fila de mensagens indisponível; tente novamente."
    keys = [k async for k in r.scan_iter(match=f"{TASK_KEY_PREFIX}*")]
    assert keys == []


@pytest.mark.asyncio
async def test_get_results_404_when_missing() -> None:
    app, _, _, _ = _test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/results/00000000-0000-0000-0000-000000000001")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_results_returns_redis_payload() -> None:
    app, store, _, _ = _test_app()
    await store.create_pending("tid", "00006486000175")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/results/tid")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "tid"
    assert data["status"] == "pending"
