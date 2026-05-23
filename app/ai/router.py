from __future__ import annotations

from typing import List, Optional

from loguru import logger
from sqlalchemy import select

from ..db import session_scope
from ..models import ApiKey, Setting
from .providers import PROVIDERS, AIRequest, AIResponse, BaseProvider, ProviderError


async def _preferred() -> tuple[str, str | None]:
    async with session_scope() as s:
        prov = (
            await s.execute(select(Setting).where(Setting.key == "preferred_provider"))
        ).scalar_one_or_none()
        mdl = (
            await s.execute(select(Setting).where(Setting.key == "preferred_model"))
        ).scalar_one_or_none()
        return (
            prov.value if prov else "groq",
            mdl.value if mdl else None,
        )


async def _providers_with_keys() -> List[str]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(ApiKey.provider).where(ApiKey.is_active.is_(True)).distinct()
            )
        ).scalars().all()
        return [r for r in rows]


async def generate(req: AIRequest) -> AIResponse:
    """Пробует preferred провайдера, потом по очереди остальных у кого есть ключи."""
    preferred, model = await _preferred()
    available = await _providers_with_keys()
    if not available:
        raise ProviderError("Нет ни одного активного API-ключа. Добавь в панели или .env")
    order = []
    if preferred in available:
        order.append(preferred)
    for p in available:
        if p not in order:
            order.append(p)

    last_err: Optional[Exception] = None
    for prov_name in order:
        cls = PROVIDERS.get(prov_name)
        if not cls:
            continue
        prov = cls(model=model if prov_name == preferred else None)
        try:
            return await prov.generate(req)
        except Exception as e:
            last_err = e
            logger.warning(f"Провайдер {prov_name} упал: {e}")
            continue
    raise ProviderError(f"Все провайдеры упали ({last_err})")
