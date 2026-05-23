from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from ..ai.providers import PROVIDERS
from ..config import get_settings
from ..db import session_scope
from ..models import ApiKey, Channel, Comment, LogEntry, Post, Proxy, Setting
from ..proxies.pool import pool
from ..tg.client import tg
from ..tg.dialogs import list_subscribed_channels, resolve_linked_discussion
from ..worker import approve_and_send, attach_handlers
from .auth import issue_token, require_auth, revoke_token

router = APIRouter(prefix="/api")
public = APIRouter(prefix="/api")


# ---------------- AUTH ----------------

class LoginBody(BaseModel):
    password: str


@public.post("/login")
async def login(body: LoginBody, response: Response):
    s = get_settings()
    if body.password != s.web_password:
        raise HTTPException(401, "Неверный пароль")
    tok = issue_token()
    response.set_cookie("nc_token", tok, httponly=True, samesite="lax")
    return {"ok": True}


@router.post("/logout", dependencies=[Depends(require_auth)])
async def logout(response: Response, nc_token: str | None = None):
    if nc_token:
        revoke_token(nc_token)
    response.delete_cookie("nc_token")
    return {"ok": True}


# ---------------- TG LOGIN ----------------

@router.get("/tg/status", dependencies=[Depends(require_auth)])
async def tg_status():
    try:
        ok = await tg.is_authorized()
    except Exception as e:
        return {"authorized": False, "error": str(e)}
    me = await tg.me() if ok else None
    return {"authorized": ok, "me": me}


class TgPhoneBody(BaseModel):
    phone: str


@router.post("/tg/send_code", dependencies=[Depends(require_auth)])
async def tg_send_code(body: TgPhoneBody):
    return await tg.send_code(body.phone)


class TgSignInBody(BaseModel):
    code: str
    password: Optional[str] = None


@router.post("/tg/sign_in", dependencies=[Depends(require_auth)])
async def tg_sign_in(body: TgSignInBody):
    res = await tg.sign_in(body.code, body.password)
    if res.get("ok"):
        await attach_handlers()
    return res


@router.post("/tg/logout", dependencies=[Depends(require_auth)])
async def tg_logout():
    await tg.logout()
    return {"ok": True}


# ---------------- CHANNELS ----------------

@router.get("/channels", dependencies=[Depends(require_auth)])
async def channels_list():
    async with session_scope() as s:
        rows = (await s.execute(select(Channel).order_by(Channel.title))).scalars().all()
        return [
            {
                "id": r.id,
                "tg_id": r.tg_id,
                "title": r.title,
                "username": r.username,
                "is_active": r.is_active,
                "discussion_chat_id": r.discussion_chat_id,
                "extra_prompt": r.extra_prompt,
            }
            for r in rows
        ]


@router.post("/channels/sync", dependencies=[Depends(require_auth)])
async def channels_sync():
    """Подтянуть список каналов из TG (на которые подписан user). Не помечает активными — это делается отдельно."""
    items = await list_subscribed_channels()
    inserted = 0
    async with session_scope() as s:
        for it in items:
            exists = (
                await s.execute(select(Channel).where(Channel.tg_id == it["tg_id"]))
            ).scalar_one_or_none()
            if exists:
                exists.title = it["title"]
                exists.username = it["username"]
                continue
            s.add(
                Channel(
                    tg_id=it["tg_id"],
                    title=it["title"],
                    username=it["username"],
                    is_active=False,
                )
            )
            inserted += 1
    return {"ok": True, "total": len(items), "new": inserted}


class ChannelToggleBody(BaseModel):
    is_active: bool
    extra_prompt: Optional[str] = None


@router.patch("/channels/{cid}", dependencies=[Depends(require_auth)])
async def channel_update(cid: int, body: ChannelToggleBody):
    async with session_scope() as s:
        row = await s.get(Channel, cid)
        if not row:
            raise HTTPException(404)
        row.is_active = body.is_active
        if body.extra_prompt is not None:
            row.extra_prompt = body.extra_prompt
        if body.is_active and not row.discussion_chat_id:
            row.discussion_chat_id = await resolve_linked_discussion(row.tg_id)
    await attach_handlers()
    return {"ok": True}


@router.delete("/channels/{cid}", dependencies=[Depends(require_auth)])
async def channel_delete(cid: int):
    async with session_scope() as s:
        row = await s.get(Channel, cid)
        if row:
            await s.delete(row)
    await attach_handlers()
    return {"ok": True}


# ---------------- API KEYS ----------------

@router.get("/keys", dependencies=[Depends(require_auth)])
async def keys_list():
    async with session_scope() as s:
        rows = (await s.execute(select(ApiKey).order_by(ApiKey.provider, ApiKey.id))).scalars().all()
        return [
            {
                "id": r.id,
                "provider": r.provider,
                "key_masked": (r.key[:6] + "…" + r.key[-4:]) if len(r.key) > 12 else "***",
                "is_active": r.is_active,
                "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
                "last_error": r.last_error,
                "fail_count": r.fail_count,
            }
            for r in rows
        ]


class KeyBody(BaseModel):
    provider: str
    key: str


@router.post("/keys", dependencies=[Depends(require_auth)])
async def keys_add(body: KeyBody):
    if body.provider not in PROVIDERS:
        raise HTTPException(400, f"Неизвестный провайдер. Доступно: {list(PROVIDERS)}")
    async with session_scope() as s:
        exists = (
            await s.execute(
                select(ApiKey).where(ApiKey.provider == body.provider, ApiKey.key == body.key)
            )
        ).scalar_one_or_none()
        if exists:
            exists.is_active = True
            exists.fail_count = 0
            exists.last_error = None
            return {"ok": True, "updated": True}
        s.add(ApiKey(provider=body.provider, key=body.key, is_active=True))
    return {"ok": True}


@router.delete("/keys/{kid}", dependencies=[Depends(require_auth)])
async def keys_delete(kid: int):
    async with session_scope() as s:
        row = await s.get(ApiKey, kid)
        if row:
            await s.delete(row)
    return {"ok": True}


@router.post("/keys/{kid}/toggle", dependencies=[Depends(require_auth)])
async def keys_toggle(kid: int):
    async with session_scope() as s:
        row = await s.get(ApiKey, kid)
        if not row:
            raise HTTPException(404)
        row.is_active = not row.is_active
        if row.is_active:
            row.fail_count = 0
            row.last_error = None
    return {"ok": True}


@router.get("/providers", dependencies=[Depends(require_auth)])
async def providers_list():
    return [
        {"name": name, "default_model": cls.default_model}
        for name, cls in PROVIDERS.items()
    ]


# ---------------- PROXIES ----------------

@router.get("/proxies", dependencies=[Depends(require_auth)])
async def proxies_list():
    return await pool.list_all()


class ProxyBody(BaseModel):
    url: str


@router.post("/proxies", dependencies=[Depends(require_auth)])
async def proxies_add(body: ProxyBody):
    ok = await pool.add(body.url.strip())
    return {"ok": True, "new": ok}


@router.delete("/proxies/{pid}", dependencies=[Depends(require_auth)])
async def proxies_delete(pid: int):
    await pool.delete(pid)
    return {"ok": True}


@router.post("/proxies/{pid}/check", dependencies=[Depends(require_auth)])
async def proxies_check(pid: int):
    async with session_scope() as s:
        row = await s.get(Proxy, pid)
        if not row:
            raise HTTPException(404)
        url = row.url
    ok = await pool.check(url)
    return {"ok": ok}


# ---------------- SETTINGS ----------------

@router.get("/settings", dependencies=[Depends(require_auth)])
async def settings_get():
    async with session_scope() as s:
        rows = (await s.execute(select(Setting))).scalars().all()
        return {r.key: r.value for r in rows}


@router.post("/settings", dependencies=[Depends(require_auth)])
async def settings_set(body: dict = Body(...)):
    async with session_scope() as s:
        for k, v in body.items():
            row = await s.get(Setting, k)
            if row:
                row.value = str(v)
            else:
                s.add(Setting(key=k, value=str(v)))
    if "running" in body:
        await attach_handlers()
    return {"ok": True}


# ---------------- POSTS / COMMENTS HISTORY ----------------

@router.get("/posts", dependencies=[Depends(require_auth)])
async def posts_list(status: str | None = None, limit: int = 50):
    async with session_scope() as s:
        q = select(Post).order_by(desc(Post.id))
        if status:
            q = q.where(Post.status == status)
        rows = (await s.execute(q.limit(limit))).scalars().all()
        out = []
        for p in rows:
            ch = await s.get(Channel, p.channel_id)
            cm = (
                await s.execute(select(Comment).where(Comment.post_id == p.id))
            ).scalar_one_or_none()
            out.append(
                {
                    "id": p.id,
                    "channel_title": ch.title if ch else "?",
                    "channel_username": ch.username if ch else None,
                    "tg_message_id": p.tg_message_id,
                    "text": p.text[:600],
                    "status": p.status,
                    "skip_reason": p.skip_reason,
                    "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None,
                    "seen_at": p.seen_at.isoformat() if p.seen_at else None,
                    "comment": {
                        "id": cm.id,
                        "text": cm.text,
                        "provider": cm.provider,
                        "model": cm.model,
                        "sent_at": cm.sent_at.isoformat() if cm.sent_at else None,
                        "error": cm.error,
                    } if cm else None,
                }
            )
        return out


class ApproveBody(BaseModel):
    text: Optional[str] = None


@router.post("/posts/{pid}/approve", dependencies=[Depends(require_auth)])
async def posts_approve(pid: int, body: ApproveBody):
    return await approve_and_send(pid, body.text)


@router.post("/posts/{pid}/skip", dependencies=[Depends(require_auth)])
async def posts_skip(pid: int):
    async with session_scope() as s:
        p = await s.get(Post, pid)
        if p:
            p.status = "skipped"
            p.skip_reason = "manual"
    return {"ok": True}


# ---------------- STATS ----------------

@router.get("/stats", dependencies=[Depends(require_auth)])
async def stats():
    async with session_scope() as s:
        total_posts = (await s.execute(select(func.count(Post.id)))).scalar_one()
        commented = (
            await s.execute(select(func.count(Post.id)).where(Post.status == "commented"))
        ).scalar_one()
        failed = (
            await s.execute(select(func.count(Post.id)).where(Post.status == "failed"))
        ).scalar_one()
        scheduled = (
            await s.execute(select(func.count(Post.id)).where(Post.status == "scheduled"))
        ).scalar_one()
        active_channels = (
            await s.execute(select(func.count(Channel.id)).where(Channel.is_active.is_(True)))
        ).scalar_one()
        return {
            "posts_seen": total_posts,
            "commented": commented,
            "scheduled": scheduled,
            "failed": failed,
            "active_channels": active_channels,
        }
