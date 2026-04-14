from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import redis.asyncio as redis

from app.core.constants import TASK_KEY_PREFIX


class TaskStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


class TaskStore:
    def __init__(self, client: redis.Redis, ttl_seconds: int) -> None:
        self._r = client
        self._ttl = ttl_seconds

    def _key(self, task_id: str) -> str:
        return f"{TASK_KEY_PREFIX}{task_id}"

    async def create_pending(self, task_id: str, cnpj: str) -> None:
        payload = {
            "task_id": task_id,
            "cnpj": cnpj,
            "status": TaskStatus.PENDING.value,
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "result": None,
            "error": None,
        }
        await self._r.set(
            self._key(task_id),
            json.dumps(payload, ensure_ascii=False),
            ex=self._ttl,
        )

    async def get(self, task_id: str) -> dict[str, Any] | None:
        raw = await self._r.get(self._key(task_id))
        if raw is None:
            return None
        return json.loads(raw)

    async def delete(self, task_id: str) -> None:
        await self._r.delete(self._key(task_id))

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        current = await self.get(task_id)
        if current is None:
            return
        current["status"] = status.value
        current["updated_at"] = _utc_now_iso()
        if result is not None:
            current["result"] = result
        if error is not None:
            current["error"] = error
        key = self._key(task_id)
        await self._r.set(
            key,
            json.dumps(current, ensure_ascii=False),
            ex=self._ttl,
        )
