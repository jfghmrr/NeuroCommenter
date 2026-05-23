from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import httpx
from loguru import logger
from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..models import ApiKey
from ..proxies.pool import pool as proxy_pool


@dataclass
class AIRequest:
    system: str
    user: str
    temperature: float = 0.85
    max_tokens: int = 220


@dataclass
class AIResponse:
    text: str
    provider: str
    model: str


class ProviderError(Exception):
    pass


class BaseProvider:
    name: str = "base"
    default_model: str = ""
    # OpenAI-compat endpoint
    base_url: str = ""

    def __init__(self, model: Optional[str] = None) -> None:
        self.model = model or self.default_model

    async def _get_keys(self) -> List[str]:
        async with session_scope() as s:
            rows = (
                await s.execute(
                    select(ApiKey).where(
                        ApiKey.provider == self.name, ApiKey.is_active.is_(True)
                    )
                )
            ).scalars().all()
            return [r.key for r in rows]

    async def _mark_key(self, key: str, err: Optional[str]) -> None:
        async with session_scope() as s:
            row = (
                await s.execute(
                    select(ApiKey).where(ApiKey.provider == self.name, ApiKey.key == key)
                )
            ).scalar_one_or_none()
            if not row:
                return
            row.last_used_at = datetime.utcnow()
            if err:
                row.last_error = err[:500]
                row.fail_count += 1
                if row.fail_count >= 8:
                    row.is_active = False
                    logger.warning(f"{self.name} key отключён после 8 фейлов")
            else:
                row.last_error = None
                row.fail_count = 0

    async def generate(self, req: AIRequest) -> AIResponse:
        keys = await self._get_keys()
        if not keys:
            raise ProviderError(f"Нет активных ключей для {self.name}")
        random.shuffle(keys)
        last_err: Optional[Exception] = None
        for key in keys:
            try:
                text = await self._call(key, req)
                await self._mark_key(key, None)
                return AIResponse(text=text.strip(), provider=self.name, model=self.model)
            except Exception as e:
                last_err = e
                await self._mark_key(key, str(e))
                logger.debug(f"{self.name} key fail: {e}")
                continue
        raise ProviderError(f"{self.name}: все ключи упали ({last_err})")

    async def _http_client(self) -> httpx.AsyncClient:
        proxy = await proxy_pool.get_random()
        timeout = httpx.Timeout(45.0, connect=15.0)
        kw = dict(timeout=timeout)
        if proxy:
            kw["proxy"] = proxy
        return httpx.AsyncClient(**kw)

    async def _openai_compat_call(
        self, key: str, req: AIRequest, *, extra_headers: dict | None = None
    ) -> str:
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": req.system},
                {"role": "user", "content": req.user},
            ],
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        async with await self._http_client() as cli:
            r = await cli.post(self.base_url, json=payload, headers=headers)
            if r.status_code >= 400:
                raise ProviderError(f"HTTP {r.status_code}: {r.text[:300]}")
            data = r.json()
            return data["choices"][0]["message"]["content"]

    async def _call(self, key: str, req: AIRequest) -> str:  # pragma: no cover - переопр.
        raise NotImplementedError


class GroqProvider(BaseProvider):
    name = "groq"
    default_model = "llama-3.3-70b-versatile"
    base_url = "https://api.groq.com/openai/v1/chat/completions"

    async def _call(self, key: str, req: AIRequest) -> str:
        return await self._openai_compat_call(key, req)


class OpenRouterProvider(BaseProvider):
    name = "openrouter"
    default_model = "meta-llama/llama-3.3-70b-instruct:free"
    base_url = "https://openrouter.ai/api/v1/chat/completions"

    async def _call(self, key: str, req: AIRequest) -> str:
        return await self._openai_compat_call(
            key,
            req,
            extra_headers={
                "HTTP-Referer": "https://localhost",
                "X-Title": "NeuroCommenter",
            },
        )


class CerebrasProvider(BaseProvider):
    name = "cerebras"
    default_model = "llama-3.3-70b"
    base_url = "https://api.cerebras.ai/v1/chat/completions"

    async def _call(self, key: str, req: AIRequest) -> str:
        return await self._openai_compat_call(key, req)


class SambaNovaProvider(BaseProvider):
    name = "sambanova"
    default_model = "Meta-Llama-3.3-70B-Instruct"
    base_url = "https://api.sambanova.ai/v1/chat/completions"

    async def _call(self, key: str, req: AIRequest) -> str:
        return await self._openai_compat_call(key, req)


class MistralProvider(BaseProvider):
    name = "mistral"
    default_model = "mistral-large-latest"
    base_url = "https://api.mistral.ai/v1/chat/completions"

    async def _call(self, key: str, req: AIRequest) -> str:
        return await self._openai_compat_call(key, req)


PROVIDERS: dict[str, type[BaseProvider]] = {
    "groq": GroqProvider,
    "openrouter": OpenRouterProvider,
    "cerebras": CerebrasProvider,
    "sambanova": SambaNovaProvider,
    "mistral": MistralProvider,
}


async def seed_keys_from_env() -> None:
    """Раз при старте: подтянуть ключи из .env в БД (если их там ещё нет)."""
    s = get_settings()
    mapping = {
        "groq": s.groq_api_keys_list,
        "openrouter": s.openrouter_api_keys_list,
        "cerebras": s.cerebras_api_keys_list,
        "sambanova": s.sambanova_api_keys_list,
        "mistral": s.mistral_api_keys_list,
    }
    async with session_scope() as sess:
        for prov, keys in mapping.items():
            for k in keys:
                exists = (
                    await sess.execute(
                        select(ApiKey).where(ApiKey.provider == prov, ApiKey.key == k)
                    )
                ).scalar_one_or_none()
                if not exists:
                    sess.add(ApiKey(provider=prov, key=k, is_active=True))


async def seed_proxies_from_env() -> None:
    s = get_settings()
    for url in s.ai_proxies_list:
        await proxy_pool.add(url)
