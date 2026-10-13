"""Telegram adapter for confirmed Planning mutation previews."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pin.adapters.telegram.presentation.callbacks import CallbackTokens, InvalidCallbackToken
from pin.adapters.telegram.presentation.mutations import mutation_keyboard, render_mutation_preview
from pin.adapters.telegram.presentation.tokens import SqlAlchemyCallbackTokenStore
from pin.contexts.planning.mutations_api import (
    MutationGrant,
    MutationPreview,
    MutationProof,
)

MUTATION_CALLBACK_PREFIX = "pm:"


class SqlAlchemyTelegramMutationVerifier:
    """Consume an owner-bound one-time preview token in the caller's UoW."""

    def __init__(self, session: AsyncSession) -> None:
        self._tokens = CallbackTokens(SqlAlchemyCallbackTokenStore(session))

    async def verify_and_consume(
        self,
        proof: MutationProof,
        *,
        owner_id: UUID,
        projection_sha256: str,
        at: datetime,
    ) -> MutationGrant | None:
        try:
            binding = await self._tokens.consume(
                proof.token,
                actor_id=owner_id,
                current_target_versions={"mutation.confirm": proof.preview_version},
                now=at,
            )
        except InvalidCallbackToken:
            return None
        if (
            binding.action != "mutation.confirm"
            or binding.target_id != str(proof.preview_id)
            or binding.target_version != proof.preview_version
        ):
            return None
        return MutationGrant(
            owner_id=owner_id,
            preview_id=proof.preview_id,
            preview_version=proof.preview_version,
            projection_sha256=projection_sha256,
            expires_at=binding.expires_at,
        )


async def render_confirmable_mutation(
    session: AsyncSession,
    preview: MutationPreview,
    *,
    language: str,
    now: datetime,
) -> tuple[str, object]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    at = now.astimezone(UTC)
    # Durable previews live for 24 hours, but Telegram callback tokens have a
    # shorter bounded lifetime. Re-rendering may issue a fresh token for the
    # same unchanged preview; confirmation still enforces preview expiry.
    expires_at = min(preview.expires_at, at + timedelta(minutes=15))
    if expires_at <= at:
        raise ValueError("mutation preview has expired")
    tokens = CallbackTokens(SqlAlchemyCallbackTokenStore(session))
    confirm = await tokens.issue(
        owner_id=preview.owner_id,
        action="mutation.confirm",
        target_id=str(preview.preview_id),
        target_version=preview.preview_version,
        expires_at=expires_at,
        now=at,
    )
    cancel = await tokens.issue(
        owner_id=preview.owner_id,
        action="mutation.dismiss",
        target_id=str(preview.preview_id),
        target_version=preview.preview_version,
        expires_at=expires_at,
        now=at,
    )
    heading, detail, confirm_label, cancel_label = render_mutation_preview(preview, language)
    return (
        f"{heading}\n\n{detail}",
        mutation_keyboard(confirm, cancel, confirm_label, cancel_label),
    )


def mutation_proof_from_callback(data: str | None, preview_id: UUID, version: int) -> MutationProof:
    if not data or not data.startswith(MUTATION_CALLBACK_PREFIX):
        raise InvalidCallbackToken("callback is not a mutation confirmation")
    token = data.removeprefix(MUTATION_CALLBACK_PREFIX)
    if not token or len(data.encode("utf-8")) > 64:
        raise InvalidCallbackToken("callback is not a valid Telegram token")
    return MutationProof(preview_id=preview_id, preview_version=version, token=token)


__all__ = [
    "MUTATION_CALLBACK_PREFIX",
    "SqlAlchemyTelegramMutationVerifier",
    "mutation_proof_from_callback",
    "render_confirmable_mutation",
]
