from __future__ import annotations

import pytest
from fakeredis import FakeAsyncRedis

from app.services.task_store import TaskStatus, TaskStore


@pytest.mark.asyncio
async def test_create_pending_and_get() -> None:
    r = FakeAsyncRedis(decode_responses=True)
    store = TaskStore(r, ttl_seconds=3600)
    await store.create_pending("tid", "00006486000175")
    data = await store.get("tid")
    assert data is not None
    assert data["task_id"] == "tid"
    assert data["cnpj"] == "00006486000175"
    assert data["status"] == "pending"
    assert data["result"] is None
    assert data["error"] is None


@pytest.mark.asyncio
async def test_update_status_completed_sets_result() -> None:
    r = FakeAsyncRedis(decode_responses=True)
    store = TaskStore(r, ttl_seconds=3600)
    await store.create_pending("tid", "00006486000175")
    await store.update_status("tid", TaskStatus.PROCESSING)
    await store.update_status(
        "tid",
        TaskStatus.COMPLETED,
        result={"Razão Social": "ACME"},
    )
    data = await store.get("tid")
    assert data is not None
    assert data["status"] == "completed"
    assert data["result"] == {"Razão Social": "ACME"}


@pytest.mark.asyncio
async def test_update_status_failed_sets_error() -> None:
    r = FakeAsyncRedis(decode_responses=True)
    store = TaskStore(r, ttl_seconds=3600)
    await store.create_pending("tid", "00006486000175")
    await store.update_status("tid", TaskStatus.FAILED, error="timeout")
    data = await store.get("tid")
    assert data is not None
    assert data["status"] == "failed"
    assert data["error"] == "timeout"


@pytest.mark.asyncio
async def test_update_status_missing_task_no_op() -> None:
    r = FakeAsyncRedis(decode_responses=True)
    store = TaskStore(r, ttl_seconds=3600)
    await store.update_status("missing", TaskStatus.COMPLETED, result={})
    assert await store.get("missing") is None


@pytest.mark.asyncio
async def test_delete_removes_key() -> None:
    r = FakeAsyncRedis(decode_responses=True)
    store = TaskStore(r, ttl_seconds=3600)
    await store.create_pending("tid", "00006486000175")
    await store.delete("tid")
    assert await store.get("tid") is None
