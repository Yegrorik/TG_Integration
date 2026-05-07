from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    amocrm_chat_channel_id: str | None = Field(default=None)
    amocrm_chat_channel_secret: str | None = Field(default=None)
    amocrm_chat_scope_id: str | None = Field(default=None)
    amocrm_chat_base_url: str = Field(default="https://amojo.amocrm.ru")
    amocrm_chat_default_title: str = Field(default="TG Integration")
    telegram_bot_token: str | None = Field(default=None)
    telegram_webhook_secret: str | None = Field(default=None)
    telegram_api_base_url: str = Field(default="https://api.telegram.org")
    public_base_url: str | None = Field(default=None)
    bridge_db_path: str = Field(default="data/bridge.sqlite3")
    telegram_expose_file_urls: bool = Field(default=False)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_settings() -> Settings:
    return Settings()
