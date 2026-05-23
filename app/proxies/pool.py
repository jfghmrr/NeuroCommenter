from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import Optional

import httpx
from loguru import logger
from sqlalchemy import select

from ..db import session_scope
from ..models import Proxy


class ProxyPool:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def get_random(self) -> Optional[str]:
        async with session_scope() as s:
            rows = (
                await s.execute(select(Proxy).where(Proxy.is_active.is_(True)))
            ).scalars().all()
            if not rows:
                return None
            chosen = random.choice(rows)
            chosen.last_used_at = datetime.utcnow()
            return chosen.url

    async def add(self, url: str) -> bool:
        async with session_scope() as s:
            exists = (
                await s.execute(select(Proxy).where(Proxy.url == url))
            ).scalar_one_or_none()
            if exists:
                exists.is_active = True
                return False
            s.add(Proxy(url=url, is_active=True))
            return True

    async def delete(self, proxy_id: int) -> None:
        async with session_scope() as s:
            row = await s.get(Proxy, proxy_id)
            if row:
                await s.delete(row)

    async def list_all(self) -> list[dict]:
        async with session_scope() as s:
            rows = (await s.execute(select(Proxy).order_by(Proxy.id))).scalars().all()
            return [
                {
                    "id": r.id,
                    "url": r.url,
                    "is_active": r.is_active,
                    "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
                    "last_check_ok": r.last_check_ok,
                    "fail_count": r.fail_count,
                }
                for r in rows
            ]

    async def report_fail(self, url: str, err: str) -> None:
        async with session_scope() as s:
            row = (
                await s.execute(select(Proxy).where(Proxy.url == url))
            ).scalar_one_or_none()
            if not row:
                return
            row.fail_count += 1
            row.last_check_ok = False
            if row.fail_count >= 5:
                row.is_active = False
                logger.warning(f"Прокси {url} отключён после 5 фейлов: {err}")

    async def check(self, url: str, timeout: float = 8.0) -> bool:
        try:
            async with httpx.AsyncClient(proxy=url, timeout=timeout) as cli:
                r = await cli.get("https://api.ipify.org?format=json")
                ok = r.status_code == 200 and "ip" in r.text
        except Exception as e:
            logger.debug(f"proxy check fail {url}: {e}")
            ok = False
        async with session_scope() as s:
            row = (
                await s.execute(select(Proxy).where(Proxy.url == url))
            ).scalar_one_or_none()
            if row:
                row.last_check_ok = ok
                if not ok:
                    row.fail_count += 1
        return ok


pool = ProxyPool()
