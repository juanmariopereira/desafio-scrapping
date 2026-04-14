"""
Integração ponta a ponta com o site público Sintegra GO (rede real).

Rodar explicitamente (pode falhar por captcha, indisponibilidade ou certificado):

  RUN_E2E=1 pytest tests/test_e2e_live_sintegra.py -v

No Windows (cmd):

  set RUN_E2E=1 && pytest tests/test_e2e_live_sintegra.py -v
"""

from __future__ import annotations

import os

import pytest

from app.api.schemas import ScrapeRequest
from app.core.config import Settings
from scraper.sintegra_go import ScrapeError, run_sintegra_go_query

# CNPJ de exemplo sugerido no enunciado/README do projeto
CNPJ_DIGITS = "00006486000175"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_e2e_live_sintegra_contribuinte_data() -> None:
    if os.environ.get("RUN_E2E") != "1":
        pytest.skip("Defina RUN_E2E=1 para executar este teste contra o Sintegra GO ao vivo.")

    body = ScrapeRequest(cnpj=CNPJ_DIGITS)
    assert body.cnpj == CNPJ_DIGITS

    settings = Settings(
        sintegra_verify_ssl=False,
        sintegra_timeout_seconds=90.0,
    )

    try:
        result = await run_sintegra_go_query(body.cnpj, settings)
    except ScrapeError as exc:
        msg = str(exc).lower()
        if any(
            w in msg
            for w in (
                "captcha",
                "segurança",
                "seguranca",
                "código",
                "codigo",
            )
        ):
            pytest.skip(f"Sintegra exige interação humana ou bloqueou: {exc}")
        raise

    assert isinstance(result, dict)
    assert len(result) >= 1

    if "resumo_pagina" in result:
        blob = (result.get("resumo_pagina") or "").lower()
        if any(w in blob for w in ("captcha", "segurança", "seguranca", "código de segurança")):
            pytest.skip("Resposta parece exigir captcha; não há como concluir o E2E de forma automática.")

    assert (
        (result.get("contribuinte") or {}).get("CNPJ")
        or "CNPJ" in result
        or "Razão Social" in result
        or "Razao Social" in result
        or "resumo_pagina" in result
        or result.get("situacao_consulta") == "nao_encontrado"
    ), f"Resposta inesperada (chaves): {list(result.keys())}"
