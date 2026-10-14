"""Gateway-owned Telegram ingress state and authenticated identity resolution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from logging import getLogger
from typing import Any, Protocol, cast, get_args
from uuid import UUID, uuid4

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Update
from pydantic import BaseModel, TypeAdapter
from sqlalchemy import BigInteger, CheckConstraint, String, UniqueConstraint, Uuid, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from pin.contexts.billing.api import RestoreReturningPaidAccess, SqlAlchemyAccessUsageStore
from pin.persistence.models import Base
from pin.platform.jobs import InboxResult, JobLane, receive_update

logger = getLogger("pin.telegram.ingress")
CALLBACK_ACK_TIMEOUT_SECONDS = 2


async def acknowledge_callback(bot: Bot, callback_query_id: str) -> bool:
    """Bound spinner acknowledgement; durable update delivery does not depend on it."""
    try:
        return await bot.answer_callback_query(
            callback_query_id=callback_query_id,
            request_timeout=CALLBACK_ACK_TIMEOUT_SECONDS,
        )
    except TelegramBadRequest:
        logger.info("telegram_callback_ack_terminal_rejection")
    except Exception as error:
        logger.warning("telegram_callback_ack_failed error=%s", type(error).__name__)
    return False


class TelegramIngressBotRecord(Base):
    __tablename__ = "ingress_bots"
    __table_args__ = (
        CheckConstraint("bot_id > 0", name="ck_telegram_ingress_bot_id"),
        CheckConstraint("mode IN ('webhook', 'polling')", name="ck_telegram_ingress_mode"),
        CheckConstraint(
            "(mode = 'webhook' AND next_update_offset IS NULL) OR "
            "(mode = 'polling' AND next_update_offset IS NOT NULL AND next_update_offset >= 0)",
            name="ck_telegram_ingress_offset_mode",
        ),
        {"schema": "pin_telegram"},
    )

    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    next_update_offset: Mapped[int | None] = mapped_column(BigInteger)


class TelegramOwnerRecord(Base):
    __tablename__ = "owners"
    __table_args__ = (
        CheckConstraint("bot_id > 0", name="ck_telegram_owner_bot_id"),
        CheckConstraint("telegram_user_id > 0", name="ck_telegram_owner_user_id"),
        UniqueConstraint("owner_id", name="uq_telegram_owner_id"),
        {"schema": "pin_telegram"},
    )

    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class TelegramOwnerResolver(Protocol):
    """Resolve an authenticated Telegram actor to its opaque application owner."""

    async def resolve(
        self, session: AsyncSession, *, bot_id: int, telegram_user_id: int
    ) -> UUID: ...


class PostgresTelegramOwnerResolver:
    async def resolve(self, session: AsyncSession, *, bot_id: int, telegram_user_id: int) -> UUID:
        """Create a server-generated opaque owner ID once for this bot/user pair."""
        if bot_id < 1 or telegram_user_id < 1:
            raise ValueError("Telegram identity must use positive IDs")
        existing = await session.scalar(
            select(TelegramOwnerRecord.owner_id).where(
                TelegramOwnerRecord.bot_id == bot_id,
                TelegramOwnerRecord.telegram_user_id == telegram_user_id,
            )
        )
        if existing is not None:
            billing = SqlAlchemyAccessUsageStore(session)
            retained = await billing.resolve_returning_owner(telegram_user_id)
            if retained is not None and retained != existing:
                raise RuntimeError(
                    "Telegram identity mapping conflicts with retained billing owner"
                )
            await billing.restore_returning_paid_access(
                RestoreReturningPaidAccess(existing, telegram_user_id, datetime.now(UTC))
            )
            return existing
        billing = SqlAlchemyAccessUsageStore(session)
        retained = await billing.resolve_returning_owner(telegram_user_id)
        owner_id = retained or uuid4()
        inserted = await session.execute(
            insert(TelegramOwnerRecord)
            .values(bot_id=bot_id, telegram_user_id=telegram_user_id, owner_id=owner_id)
            .on_conflict_do_nothing(
                index_elements=[TelegramOwnerRecord.bot_id, TelegramOwnerRecord.telegram_user_id]
            )
            .returning(TelegramOwnerRecord.owner_id)
        )
        resolved = inserted.scalar_one_or_none()
        if resolved is not None:
            await billing.restore_returning_paid_access(
                RestoreReturningPaidAccess(resolved, telegram_user_id, datetime.now(UTC))
            )
            return resolved
        saved = await session.scalar(
            select(TelegramOwnerRecord.owner_id).where(
                TelegramOwnerRecord.bot_id == bot_id,
                TelegramOwnerRecord.telegram_user_id == telegram_user_id,
            )
        )
        if saved is None:
            raise RuntimeError("Telegram identity mapping was not visible after insert conflict")
        if retained is not None and retained != saved:
            raise RuntimeError(
                "Telegram identity mapping conflicts with retained billing owner"
            )
        await billing.restore_returning_paid_access(
            RestoreReturningPaidAccess(saved, telegram_user_id, datetime.now(UTC))
        )
        return saved


async def select_ingress_mode(
    session: AsyncSession, *, bot_id: int, mode: str
) -> TelegramIngressBotRecord:
    if mode not in {"webhook", "polling"}:
        raise ValueError("Telegram ingress mode must be webhook or polling")
    statement = (
        insert(TelegramIngressBotRecord)
        .values(
            bot_id=bot_id,
            mode=mode,
            next_update_offset=0 if mode == "polling" else None,
        )
        .on_conflict_do_nothing(index_elements=[TelegramIngressBotRecord.bot_id])
    )
    await session.execute(statement)
    record = await session.scalar(
        select(TelegramIngressBotRecord)
        .where(TelegramIngressBotRecord.bot_id == bot_id)
        .with_for_update()
    )
    if record is None or record.mode != mode:
        raise IngressModeConflict("Telegram bot is already assigned to another ingress mode")
    return record


class IngressModeConflict(RuntimeError):
    """A bot already has a persistent webhook or polling owner."""


@dataclass(frozen=True, slots=True)
class IngressReceipt:
    accepted: bool
    update_id: int
    callback_query_id: str | None = None


def validate_update(payload: object, bot: Bot) -> dict[str, Any]:
    if isinstance(payload, Update):
        return cast(dict[str, Any], _trusted_model_dump(payload))
    if not isinstance(payload, Mapping):
        raise ValueError("Telegram update must be an object")
    try:
        update = Update.model_validate(payload, context={"bot": bot})
    except Exception as error:
        raise ValueError("Telegram update is malformed") from error
    return cast(dict[str, Any], _trusted_model_dump(update))


def _trusted_model_dump(value: object, annotation: Any | None = None) -> Any:
    """Serialize Telegram's declared fields only; aiogram retains unknown extras."""
    if isinstance(value, BaseModel):
        result: dict[str, Any] = {}
        for name, field in type(value).model_fields.items():
            if field.exclude:
                continue
            item = getattr(value, name)
            if item is None:
                continue
            key = field.serialization_alias or field.alias or name
            result[key] = _trusted_model_dump(item, field.annotation)
        return result
    if isinstance(value, (list, tuple)):
        arguments = get_args(annotation) if annotation is not None else ()
        item_annotation = arguments[0] if arguments else None
        return [_trusted_model_dump(item, item_annotation) for item in value]
    if isinstance(value, dict):
        arguments = get_args(annotation) if annotation is not None else ()
        value_annotation = arguments[1] if len(arguments) > 1 else None
        return {
            str(key): _trusted_model_dump(item, value_annotation) for key, item in value.items()
        }
    if isinstance(value, datetime):
        return int(value.timestamp())
    if value is None:
        return None
    return TypeAdapter(annotation or type(value)).dump_python(value, mode="json")


async def persist_update(
    session: AsyncSession,
    *,
    bot_id: int,
    payload: Mapping[str, Any],
    owner_resolver: TelegramOwnerResolver,
) -> IngressReceipt | None:
    update_id = _positive_or_zero_id(payload.get("update_id"))
    if update_id is None:
        raise ValueError("Telegram update has no valid update_id")
    actor = private_actor(payload)
    if actor is None:
        callback_id = _callback_query_id(payload)
        return IngressReceipt(False, update_id, callback_id) if callback_id else None
    telegram_user_id, update_type, callback_query_id = actor
    owner_id = await owner_resolver.resolve(
        session, bot_id=bot_id, telegram_user_id=telegram_user_id
    )
    speech = update_type == "speech_message"
    fast_path = update_type in {"pre_checkout_query", "successful_payment"}
    result: InboxResult = await receive_update(
        session,
        bot_id=bot_id,
        update_id=update_id,
        owner_id=owner_id,
        update_type=update_type,
        payload=dict(payload),
        lane=JobLane.SPEECH if speech else JobLane.APPLICATION,
        conversation_ordered=not fast_path,
        priority=100 if fast_path else 0,
    )
    return IngressReceipt(result.accepted, update_id, callback_query_id)


def private_actor(update: Mapping[str, Any]) -> tuple[int, str, str | None] | None:
    """Return a private-chat actor and typed update, ignoring unsupported groups."""
    query = update.get("pre_checkout_query")
    if isinstance(query, Mapping):
        sender = query.get("from")
        actor_id = _positive_id(sender.get("id")) if isinstance(sender, Mapping) else None
        if actor_id is None:
            raise ValueError("payment query has no valid sender")
        return actor_id, "pre_checkout_query", None

    callback = update.get("callback_query")
    if isinstance(callback, Mapping):
        sender = callback.get("from")
        actor_id = _positive_id(sender.get("id")) if isinstance(sender, Mapping) else None
        message = callback.get("message")
        if actor_id is None or not isinstance(message, Mapping):
            return None
        chat = message.get("chat")
        if not isinstance(chat, Mapping) or chat.get("type") != "private":
            return None
        if _positive_id(chat.get("id")) != actor_id:
            return None
        callback_id = _callback_query_id(update)
        if callback_id is None:
            raise ValueError("callback query has no valid identifier")
        return actor_id, "callback_query", callback_id

    message = update.get("message")
    if isinstance(message, Mapping):
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, Mapping) or chat.get("type") != "private":
            return None
        if not isinstance(sender, Mapping):
            return None
        actor_id = _positive_id(sender.get("id"))
        if actor_id is None or _positive_id(chat.get("id")) != actor_id:
            return None
        content = message.get("voice") or message.get("audio")
        kind = "speech_message" if isinstance(content, Mapping) else "message"
        if isinstance(message.get("successful_payment"), Mapping):
            kind = "successful_payment"
        return actor_id, kind, None
    return None


def _positive_id(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _callback_query_id(update: Mapping[str, Any]) -> str | None:
    callback = update.get("callback_query")
    if not isinstance(callback, Mapping):
        return None
    callback_id = callback.get("id")
    if not isinstance(callback_id, str) or not callback_id or len(callback_id) > 256:
        return None
    return callback_id


def _positive_or_zero_id(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
