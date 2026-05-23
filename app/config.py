from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(v: str | None) -> List[str]:
    if not v:
        return []
    return [p.strip() for p in str(v).split(",") if p.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    tg_api_id: int = 0
    tg_api_hash: str = ""
    tg_session_name: str = "user_session"
    tg_proxy: str = ""

    web_host: str = "127.0.0.1"
    web_port: int = 8800
    web_password: str = "changeme"

    # храним как строку — список парсим в @property
    groq_api_keys: str = ""
    openrouter_api_keys: str = ""
    cerebras_api_keys: str = ""
    sambanova_api_keys: str = ""
    mistral_api_keys: str = ""
    ai_proxies: str = ""

    min_delay_sec: int = 180
    max_delay_sec: int = 1800
    max_comments_per_hour: int = 8
    comment_probability: float = 0.7

    db_url: str = "sqlite+aiosqlite:///./app/data/neuro.db"

    @property
    def groq_api_keys_list(self) -> List[str]:
        return _split_csv(self.groq_api_keys)

    @property
    def openrouter_api_keys_list(self) -> List[str]:
        return _split_csv(self.openrouter_api_keys)

    @property
    def cerebras_api_keys_list(self) -> List[str]:
        return _split_csv(self.cerebras_api_keys)

    @property
    def sambanova_api_keys_list(self) -> List[str]:
        return _split_csv(self.sambanova_api_keys)

    @property
    def mistral_api_keys_list(self) -> List[str]:
        return _split_csv(self.mistral_api_keys)

    @property
    def ai_proxies_list(self) -> List[str]:
        return _split_csv(self.ai_proxies)

    @property
    def base_dir(self) -> Path:
        return Path(__file__).resolve().parent

    @property
    def data_dir(self) -> Path:
        p = self.base_dir / "data"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def sessions_dir(self) -> Path:
        p = self.data_dir / "sessions"
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()
