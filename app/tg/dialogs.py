from __future__ import annotations

from typing import List

from loguru import logger
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel as TgChannel

from .client import tg


async def list_subscribed_channels() -> List[dict]:
    """Возвращает каналы (broadcast) на которые подписан юзер."""
    c = await tg.ensure_client()
    out: List[dict] = []
    async for d in c.iter_dialogs():
        ent = d.entity
        # broadcast канал — то что нам надо (не группа, не личка)
        if isinstance(ent, TgChannel) and getattr(ent, "broadcast", False):
            out.append(
                {
                    "tg_id": ent.id,
                    "title": ent.title,
                    "username": ent.username,
                    "participants": getattr(ent, "participants_count", None),
                }
            )
    return out


async def resolve_linked_discussion(channel_tg_id: int) -> int | None:
    """Найти linked discussion group у канала. Возвращает chat_id или None."""
    c = await tg.ensure_client()
    try:
        ent = await c.get_entity(channel_tg_id)
        full = await c(GetFullChannelRequest(channel=ent))
        linked_id = getattr(full.full_chat, "linked_chat_id", None)
        return int(linked_id) if linked_id else None
    except Exception as e:
        logger.warning(f"resolve_linked_discussion({channel_tg_id}) failed: {e}")
        return None
