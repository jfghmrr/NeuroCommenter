from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(256))
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    discussion_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    extra_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    posts: Mapped[list["Post"]] = relationship(back_populates="channel")


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (UniqueConstraint("channel_id", "tg_message_id", name="uq_post"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    tg_message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default="new"
    )  # new | scheduled | commented | skipped | failed
    skip_reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    channel: Mapped[Channel] = relationship(back_populates="posts")
    comment: Mapped[Optional["Comment"]] = relationship(
        back_populates="post", uselist=False
    )


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), unique=True)
    text: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    tg_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    post: Mapped[Post] = relationship(back_populates="comment")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    key: Mapped[str] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Proxy(Base):
    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(512), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_check_ok: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class LogEntry(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    level: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)


DEFAULT_PERSONA_PROMPT = """\
Ты — владелец онлайн-школы программирования для детей (возраст 7–17 лет).
Школа учит детей Python, Scratch, веб-разработке, Minecraft-моддингу, играм, ИИ.

Когда комментируешь чужие посты:
- Пиши КОРОТКО (1–3 предложения, максимум 350 символов).
- Звучи как живой человек, а не как реклама.
- Если пост по теме (образование детей, IT, программирование, родительство, развитие) —
  ответь по сути, добавь полезную мысль или мягко поделись опытом школы.
  ОДИН раз из 5 коммов можно естественно упомянуть, что ведёшь школу.
- Если пост вне темы — отвечай как обычный читатель: согласие, эмоция, наблюдение.
- Никогда не вставляй ссылки, эмодзи можно 0–1 шт.
- НЕ начинай со слов "Согласен", "Интересно", "Спасибо за пост" — это палится.
- Пиши на том же языке, что и пост.
- НИКАКИХ хештегов, призывов "пишите в личку", "переходите по ссылке".
"""


DEFAULT_SETTINGS = {
    "persona_prompt": DEFAULT_PERSONA_PROMPT,
    "running": "0",
    "preferred_provider": "groq",
    "preferred_model": "llama-3.3-70b-versatile",
    "manual_approval": "0",  # если 1 — комменты ждут ручного аппрува
}
