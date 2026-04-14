from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = Field(default="redis://localhost:6379/0")
    rabbitmq_url: str = Field(default="amqp://guest:guest@localhost:5672/")

    sintegra_base_url: str = Field(
        default="https://appasp.sefaz.go.gov.br",
        description="Host base da consulta pública Sintegra GO",
    )
    sintegra_entry_path: str = Field(
        default="/Sintegra/Consulta/default.html",
        description="Caminho da página inicial do formulário (.html ou .asp)",
    )
    sintegra_verify_ssl: bool = Field(
        default=True,
        description="Validação TLS ao consultar o site legado da SEFAZ-GO",
    )
    sintegra_timeout_seconds: float = Field(default=45.0)

    cors_allowed_origins: str = Field(
        default="*",
        description='Origens CORS (lista separada por vírgula) ou "*" para desenvolvimento local',
    )

    queue_name: str = Field(default="scrape_tasks")
    task_result_ttl_seconds: int = Field(
        default=604_800,
        description="TTL em segundos para chaves de tarefa no Redis após conclusão",
    )


# @lru_cache
def get_settings() -> Settings:
    return Settings()
