from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fakeredis import FakeAsyncRedis

from app.core.config import Settings
from app.services.task_store import TaskStatus, TaskStore
from scraper.sintegra_go import ScrapeError
from worker.main import handle_payload


@pytest.mark.asyncio
async def test_handle_payload_marks_completed() -> None:
    r = FakeAsyncRedis(decode_responses=True)
    store = TaskStore(r, ttl_seconds=3600)
    await store.create_pending("t1", "00006486000175")
    settings = Settings()
    fake_result = {"contribuinte": {"CNPJ": "00.006.486/0001-75"}}
    with patch(
        "worker.main.run_sintegra_go_query",
        new_callable=AsyncMock,
        return_value=fake_result,
    ):
        await handle_payload(
            store,
            json.dumps({"task_id": "t1", "cnpj": "00006486000175"}).encode("utf-8"),
            settings,
        )
    row = await store.get("t1")
    assert row is not None
    assert row["status"] == TaskStatus.COMPLETED.value
    assert row["result"] == fake_result


@pytest.mark.asyncio
async def test_handle_payload_marks_failed_on_scrape_error() -> None:
    r = FakeAsyncRedis(decode_responses=True)
    store = TaskStore(r, ttl_seconds=3600)
    await store.create_pending("t2", "00006486000175")
    settings = Settings()
    with patch(
        "worker.main.run_sintegra_go_query",
        new_callable=AsyncMock,
        side_effect=ScrapeError("página indisponível"),
    ):
        await handle_payload(
            store,
            json.dumps({"task_id": "t2", "cnpj": "00006486000175"}).encode("utf-8"),
            settings,
        )
    row = await store.get("t2")
    assert row is not None
    assert row["status"] == TaskStatus.FAILED.value
    assert row["error"] == "página indisponível"
