from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import and_, select
from telethon import events

from .ai.prompts import build_user_prompt, get_persona
from .ai.providers import AIRequest
from .ai.router import generate
from .config import get_settings
from .db import session_scope
from .models import Channel, Comment, Post, Setting
from .tg.client import tg
from .tg.commenter import post_comment


def _settings_get(s, key: str, default: str) -> str:
    row = s
    return row.value if row else default


async def _running() -> bool:
    async with session_scope() as s:
        row = (await s.execute(select(Setting).where(Setting.key == "running"))).scalar_one_or_none()
        return bool(row and row.value == "1")


async def _manual_approval() -> bool:
    async with session_scope() as s:
        row = (
            await s.execute(select(Setting).where(Setting.key == "manual_approval"))
        ).scalar_one_or_none()
        return bool(row and row.value == "1")


async def _hourly_cap_reached() -> bool:
    s = get_settings()
    cutoff = datetime.utcnow() - timedelta(hours=1)
    async with session_scope() as sess:
        rows = (
            await sess.execute(
                select(Comment).where(Comment.sent_at.is_not(None), Comment.sent_at >= cutoff)
            )
        ).scalars().all()
        return len(rows) >= s.max_comments_per_hour


async def _schedule_post(channel_db_id: int, msg_id: int, text: str) -> None:
    s = get_settings()
    delay = random.randint(s.min_delay_sec, max(s.min_delay_sec + 1, s.max_delay_sec))
    when = datetime.utcnow() + timedelta(seconds=delay)
    async with session_scope() as sess:
        exists = (
            await sess.execute(
                select(Post).where(
                    and_(Post.channel_id == channel_db_id, Post.tg_message_id == msg_id)
                )
            )
        ).scalar_one_or_none()
        if exists:
            return
        # вероятность вообще отвечать
        if random.random() > s.comment_probability:
            sess.add(
                Post(
                    channel_id=channel_db_id,
                    tg_message_id=msg_id,
                    text=text[:8000],
                    status="skipped",
                    skip_reason="probability",
                )
            )
            return
        sess.add(
            Post(
                channel_id=channel_db_id,
                tg_message_id=msg_id,
                text=text[:8000],
                status="scheduled",
                scheduled_at=when,
            )
        )
        logger.info(
            f"Scheduled comment for channel#{channel_db_id} msg#{msg_id} в {when.isoformat()} (через {delay}s)"
        )


# ---------- слушатель новых постов ----------

_active_handlers: list = []


async def attach_handlers() -> None:
    c = await tg.ensure_client()
    if not await c.is_user_authorized():
        return

    # снимаем старые
    for h in list(_active_handlers):
        try:
            c.remove_event_handler(h)
        except Exception:
            pass
    _active_handlers.clear()

    async with session_scope() as s:
        channels = (
            await s.execute(select(Channel).where(Channel.is_active.is_(True)))
        ).scalars().all()
        active_ids = [ch.tg_id for ch in channels]
        id_map = {ch.tg_id: ch.id for ch in channels}

    if not active_ids:
        logger.info("Активных каналов нет — слушатель не навешан")
        return

    async def on_new(event):
        try:
            chan_tg_id = event.chat_id
            db_id = id_map.get(chan_tg_id)
            if db_id is None:
                # negative -100... format
                for tgid, did in id_map.items():
                    if str(chan_tg_id).endswith(str(abs(tgid))[-9:]):
                        db_id = did
                        break
            if db_id is None:
                return
            txt = event.message.message or ""
            await _schedule_post(db_id, event.message.id, txt)
        except Exception as e:
            logger.exception(f"on_new handler error: {e}")

    c.add_event_handler(on_new, events.NewMessage(chats=active_ids))
    _active_handlers.append(on_new)
    logger.info(f"Слушатель навешан на {len(active_ids)} каналов")


# ---------- цикл отправки запланированных комментов ----------

async def _process_due_posts() -> None:
    if not await _running():
        return
    if await _hourly_cap_reached():
        return
    if await _manual_approval():
        return  # ждём ручного аппрува, не шлём авто

    now = datetime.utcnow()
    async with session_scope() as sess:
        due = (
            await sess.execute(
                select(Post).where(
                    and_(Post.status == "scheduled", Post.scheduled_at <= now)
                ).limit(3)
            )
        ).scalars().all()
        # отметим как в работе
        for p in due:
            p.status = "processing"
        due_ids = [p.id for p in due]

    for pid in due_ids:
        await _process_one(pid)


async def _process_one(post_id: int) -> None:
    async with session_scope() as sess:
        p = await sess.get(Post, post_id)
        if not p:
            return
        ch = await sess.get(Channel, p.channel_id)
        if not ch:
            p.status = "failed"
            p.skip_reason = "channel deleted"
            return
        channel_title = ch.title
        channel_tg_id = ch.tg_id
        channel_extra = ch.extra_prompt
        msg_id = p.tg_message_id
        post_text = p.text

    # генерация
    persona = await get_persona()
    user = build_user_prompt(channel_title, post_text, channel_extra)
    try:
        ai = await generate(AIRequest(system=persona, user=user))
    except Exception as e:
        async with session_scope() as sess:
            p = await sess.get(Post, post_id)
            if p:
                p.status = "failed"
                p.skip_reason = f"ai: {e}"[:250]
        logger.error(f"AI fail для post#{post_id}: {e}")
        return

    comment_text = _postprocess(ai.text)

    # сохраняем коммент даже если не отправили (чтобы аппрувить)
    async with session_scope() as sess:
        p = await sess.get(Post, post_id)
        if p:
            cm = Comment(
                post_id=p.id, text=comment_text, provider=ai.provider, model=ai.model
            )
            sess.add(cm)

    # отправляем
    res = await post_comment(channel_tg_id, msg_id, comment_text)
    async with session_scope() as sess:
        p = await sess.get(Post, post_id)
        cm = (
            await sess.execute(select(Comment).where(Comment.post_id == post_id))
        ).scalar_one_or_none()
        if res.ok:
            if cm:
                cm.sent_at = datetime.utcnow()
                cm.tg_message_id = res.message_id
            if p:
                p.status = "commented"
        else:
            if cm:
                cm.error = res.error
            if p:
                p.status = "failed"
                p.skip_reason = res.error
    if res.ok:
        logger.info(f"✓ post#{post_id} коммент отправлен (msg {res.message_id})")
    else:
        logger.warning(f"✗ post#{post_id} не отправлен: {res.error}")


def _postprocess(text: str) -> str:
    t = (text or "").strip()
    # убираем кавычки если модель завернула в них
    if (t.startswith('"') and t.endswith('"')) or (t.startswith("«") and t.endswith("»")):
        t = t[1:-1].strip()
    # обрезаем
    if len(t) > 380:
        t = t[:380].rsplit(" ", 1)[0] + "…"
    return t


async def loop_dispatcher() -> None:
    logger.info("Worker dispatcher запущен")
    while True:
        try:
            await _process_due_posts()
        except Exception as e:
            logger.exception(f"dispatcher tick error: {e}")
        await asyncio.sleep(15)


async def approve_and_send(post_id: int, override_text: str | None = None) -> dict:
    """Ручной аппрув: если override_text — заменим, и отправим."""
    async with session_scope() as sess:
        p = await sess.get(Post, post_id)
        if not p:
            return {"ok": False, "error": "Пост не найден"}
        ch = await sess.get(Channel, p.channel_id)
        cm = (
            await sess.execute(select(Comment).where(Comment.post_id == post_id))
        ).scalar_one_or_none()
        if not cm:
            return {"ok": False, "error": "Коммент ещё не сгенерирован"}
        if override_text:
            cm.text = override_text
        text = cm.text
        chid = ch.tg_id
        mid = p.tg_message_id

    res = await post_comment(chid, mid, text)
    async with session_scope() as sess:
        p = await sess.get(Post, post_id)
        cm = (
            await sess.execute(select(Comment).where(Comment.post_id == post_id))
        ).scalar_one_or_none()
        if res.ok:
            cm.sent_at = datetime.utcnow()
            cm.tg_message_id = res.message_id
            p.status = "commented"
        else:
            cm.error = res.error
            p.status = "failed"
            p.skip_reason = res.error
    return {"ok": res.ok, "error": res.error, "message_id": res.message_id}
