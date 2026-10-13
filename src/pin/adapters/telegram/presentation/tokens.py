"""SQLAlchemy persistence adapter for Telegram's one-time callback bindings."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Uuid, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from pin.adapters.telegram.presentation.callbacks import CallbackBinding, CallbackTokenStore
from pin.persistence.models import Base


class TelegramCallbackTokenRecord(Base):
    __tablename__ = "callback_tokens"
    __table_args__ = (
        CheckConstraint("length(token_hash) = 64", name="ck_callback_tokens_hash_length"),
        CheckConstraint("target_version >= 0", name="ck_callback_tokens_target_version"),
        CheckConstraint("length(action) BETWEEN 1 AND 64", name="ck_callback_tokens_action"),
        {"schema": "pin_telegram"},
    )

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SqlAlchemyCallbackTokenStore(CallbackTokenStore):
    """Use the caller's UoW session so consume and action can commit atomically."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def issue(self, binding: CallbackBinding) -> None:
        self._session.add(
            TelegramCallbackTokenRecord(
                token_hash=binding.token_hash,
                owner_id=binding.owner_id,
                action=binding.action,
                target_id=binding.target_id,
                target_version=binding.target_version,
                expires_at=binding.expires_at,
            )
        )
        await self._session.flush()

    async def consume(
        self, token_hash: str, owner_id: UUID, now: datetime
    ) -> CallbackBinding | None:
        statement = (
            update(TelegramCallbackTokenRecord)
            .where(
                TelegramCallbackTokenRecord.token_hash == token_hash,
                TelegramCallbackTokenRecord.owner_id == owner_id,
                TelegramCallbackTokenRecord.expires_at > now,
                TelegramCallbackTokenRecord.consumed_at.is_(None),
            )
            .values(consumed_at=now)
            .returning(
                TelegramCallbackTokenRecord.token_hash,
                TelegramCallbackTokenRecord.owner_id,
                TelegramCallbackTokenRecord.action,
                TelegramCallbackTokenRecord.target_id,
                TelegramCallbackTokenRecord.target_version,
                TelegramCallbackTokenRecord.expires_at,
            )
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        return CallbackBinding(
            token_hash=row.token_hash,
            owner_id=row.owner_id,
            action=row.action,
            target_id=row.target_id,
            target_version=row.target_version,
            expires_at=row.expires_at,
        )
