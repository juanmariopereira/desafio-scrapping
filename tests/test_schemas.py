import pytest
from pydantic import ValidationError

from app.api.schemas import ScrapeRequest


def test_cnpj_digits_only() -> None:
    body = ScrapeRequest(cnpj="00.006.486/0001-75")
    assert body.cnpj == "00006486000175"


def test_cnpj_invalid_length() -> None:
    with pytest.raises(ValidationError):
        ScrapeRequest(cnpj="123")
