from __future__ import annotations

from loguru import logger
from telethon.errors import (
    ChatWriteForbiddenError,
    FloodWaitError,
    MsgIdInvalidError,
    SlowModeWaitError,
    UserBannedInChannelError,
)

from .client import tg


class CommentResult:
    def __init__(self, ok: bool, message_id: int | None = None, error: str | None = None):
        self.ok = ok
        self.message_id = message_id
        self.error = error


async def post_comment(channel_tg_id: int, channel_msg_id: int, text: str) -> CommentResult:
    """
    Постит коммент под пост канала. Telethon умеет ходить в linked discussion
    автоматически через `reply_to=<post_id>` если передать peer = сам канал.
    """
    c = await tg.ensure_client()
    if not await c.is_user_authorized():
        return CommentResult(False, error="TG не авторизован")

    try:
        entity = await c.get_entity(channel_tg_id)
        msg = await c.send_message(
            entity=entity,
            message=text,
            comment_to=channel_msg_id,
        )
        return CommentResult(True, message_id=msg.id)
    except FloodWaitError as e:
        return CommentResult(False, error=f"FloodWait {e.seconds}s")
    except SlowModeWaitError as e:
        return CommentResult(False, error=f"SlowMode {e.seconds}s")
    except (ChatWriteForbiddenError, UserBannedInChannelError) as e:
        return CommentResult(False, error=f"Нет прав писать: {e.__class__.__name__}")
    except MsgIdInvalidError:
        return CommentResult(False, error="Пост уже не доступен (удалён/нет коммов)")
    except Exception as e:
        logger.exception("post_comment failed")
        return CommentResult(False, error=f"{e.__class__.__name__}: {e}")
