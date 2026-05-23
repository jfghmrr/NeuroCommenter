from __future__ import annotations

from sqlalchemy import select

from ..db import session_scope
from ..models import DEFAULT_PERSONA_PROMPT, Setting


async def get_persona() -> str:
    async with session_scope() as s:
        row = (
            await s.execute(select(Setting).where(Setting.key == "persona_prompt"))
        ).scalar_one_or_none()
        if row and row.value.strip():
            return row.value
        return DEFAULT_PERSONA_PROMPT


def build_user_prompt(channel_title: str, post_text: str, extra: str | None = None) -> str:
    cleaned = (post_text or "").strip()
    if len(cleaned) > 2200:
        cleaned = cleaned[:2200] + " […]"
    parts = [
        f"Канал: «{channel_title}»",
        "Пост:",
        '"""',
        cleaned if cleaned else "(пост без текста — медиа)",
        '"""',
    ]
    if extra:
        parts += ["", f"Дополнительный контекст для этого канала: {extra}"]
    parts += [
        "",
        "Напиши ОДИН короткий комментарий в обсуждение под этим постом. "
        "Без префиксов, без подписей, без объяснений — только сам текст комментария.",
    ]
    return "\n".join(parts)
