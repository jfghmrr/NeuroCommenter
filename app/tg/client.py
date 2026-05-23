from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from loguru import logger
from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import SQLiteSession

from ..config import get_settings


class TGManager:
    """Singleton-like хранилище Telethon клиента + state логина."""

    def __init__(self) -> None:
        self._client: Optional[TelegramClient] = None
        self._login_phone: Optional[str] = None
        self._login_hash: Optional[str] = None
        self._lock = asyncio.Lock()

    @property
    def client(self) -> Optional[TelegramClient]:
        return self._client

    def _session_path(self) -> Path:
        s = get_settings()
        return s.sessions_dir / f"{s.tg_session_name}.session"

    def _build_proxy(self) -> Optional[tuple]:
        s = get_settings()
        if not s.tg_proxy:
            return None
        # socks5://user:pass@host:port  or  http://...
        import urllib.parse as up

        u = up.urlparse(s.tg_proxy)
        if not u.hostname or not u.port:
            return None
        scheme_map = {"socks5": "socks5", "socks4": "socks4", "http": "http"}
        scheme = scheme_map.get((u.scheme or "socks5").lower(), "socks5")
        return (scheme, u.hostname, u.port, True, u.username or None, u.password or None)

    async def ensure_client(self) -> TelegramClient:
        async with self._lock:
            if self._client is not None:
                return self._client
            s = get_settings()
            if not s.tg_api_id or not s.tg_api_hash:
                raise RuntimeError(
                    "TG_API_ID / TG_API_HASH не заданы в .env. "
                    "Получи их на https://my.telegram.org/apps"
                )
            session = SQLiteSession(str(self._session_path()))
            kwargs = dict(api_id=s.tg_api_id, api_hash=s.tg_api_hash)
            proxy = self._build_proxy()
            if proxy:
                kwargs["proxy"] = proxy
            self._client = TelegramClient(session, **kwargs)
            await self._client.connect()
            return self._client

    async def is_authorized(self) -> bool:
        c = await self.ensure_client()
        return await c.is_user_authorized()

    async def send_code(self, phone: str) -> dict:
        c = await self.ensure_client()
        try:
            res = await c.send_code_request(phone)
        except PhoneNumberInvalidError as e:
            return {"ok": False, "error": "Неверный номер телефона", "detail": str(e)}
        self._login_phone = phone
        self._login_hash = res.phone_code_hash
        return {"ok": True, "needs_code": True}

    async def sign_in(self, code: str, password: Optional[str] = None) -> dict:
        c = await self.ensure_client()
        if not self._login_phone:
            return {"ok": False, "error": "Сначала запроси код"}
        try:
            await c.sign_in(
                phone=self._login_phone,
                code=code,
                phone_code_hash=self._login_hash,
            )
        except PhoneCodeInvalidError:
            return {"ok": False, "error": "Неверный код"}
        except SessionPasswordNeededError:
            if not password:
                return {"ok": False, "needs_password": True}
            try:
                await c.sign_in(password=password)
            except Exception as e:
                return {"ok": False, "error": f"Неверный пароль 2FA: {e}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        me = await c.get_me()
        logger.info(f"TG авторизован как {me.first_name} (@{me.username}, id={me.id})")
        return {"ok": True, "user": {"id": me.id, "name": me.first_name, "username": me.username}}

    async def logout(self) -> None:
        if self._client:
            try:
                await self._client.log_out()
            except Exception:
                pass
            try:
                await self._client.disconnect()
            except Exception:
                pass
        # удаляем файл сессии
        try:
            p = self._session_path()
            if p.exists():
                p.unlink()
        except Exception:
            pass
        self._client = None
        self._login_phone = None
        self._login_hash = None

    async def me(self) -> Optional[dict]:
        if not self._client:
            await self.ensure_client()
        if not await self._client.is_user_authorized():
            return None
        u = await self._client.get_me()
        return {"id": u.id, "name": u.first_name, "username": u.username, "phone": u.phone}


tg = TGManager()
