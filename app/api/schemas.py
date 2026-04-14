from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def only_digits(v: str) -> str:
    d = "".join(ch for ch in v if ch.isdigit())
    if len(d) != 14:
        raise ValueError("CNPJ deve conter 14 dígitos")
    return d


class ScrapeRequest(BaseModel):
    cnpj: str = Field(..., description="CNPJ com ou sem máscara")

    @field_validator("cnpj")
    @classmethod
    def validate_cnpj(cls, v: str) -> str:
        return only_digits(v)


class ScrapeAccepted(BaseModel):
    task_id: str


class TaskResultResponse(BaseModel):
    task_id: str
    cnpj: str
    status: Literal["pending", "processing", "completed", "failed"]
    created_at: str
    updated_at: str
    result: dict[str, Any] | None = None
    error: str | None = None
