from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pin.contexts.identity.application.contracts import (
    IdentityStore,
    ProcessingAuthorization,
    ProcessingConsentSnapshot,
)

PROCESSING_CONSENT_POLICY_VERSION = "processing-consent-v1"
PROCESSING_CONSENT_LOCK_ACTION = "identity.processing_consent"


class ProcessingConsentConflict(RuntimeError):
    """The caller acted on a consent revision or grant that is no longer current."""


@dataclass(frozen=True, slots=True)
class ProcessingConsentAcceptance:
    expected_revision: int
    policy_version: str
    language: str
    accepted_at: datetime
    telegram_user_id: int
    inbox_update_id: UUID
    draft_id: UUID
    draft_version: int


async def get_processing_consent(
    store: IdentityStore, *, owner_id: UUID
) -> ProcessingConsentSnapshot:
    return await store.get_processing_consent(owner_id)


async def current_processing_authorization(
    store: IdentityStore, *, owner_id: UUID
) -> ProcessingAuthorization | None:
    """Read a grant while holding the owner fence for the caller's transaction."""
    await store.lock_processing_consent(owner_id)
    consent = await store.get_processing_consent(owner_id)
    if (
        consent.state != "granted"
        or consent.grant_id is None
        or consent.policy_version != PROCESSING_CONSENT_POLICY_VERSION
    ):
        return None
    return ProcessingAuthorization(owner_id, consent.grant_id, consent.policy_version)


async def accept_processing_consent(
    store: IdentityStore,
    *,
    owner_id: UUID,
    expected_revision: int,
    policy_version: str,
    language: str,
    accepted_at: datetime,
    telegram_user_id: int,
    inbox_update_id: UUID,
) -> ProcessingConsentSnapshot:
    if policy_version != PROCESSING_CONSENT_POLICY_VERSION:
        raise ProcessingConsentConflict("processing consent policy is stale")
    if language not in {"ru", "en"} or telegram_user_id <= 0:
        raise ValueError("processing consent actor or language is invalid")
    if accepted_at.tzinfo is None or accepted_at.utcoffset() is None:
        raise ValueError("processing consent time must be timezone-aware")
    return await store.grant_processing_consent(
        owner_id=owner_id,
        expected_revision=expected_revision,
        policy_version=policy_version,
        accepted_at=accepted_at,
        language=language,
        telegram_user_id=telegram_user_id,
        inbox_update_id=inbox_update_id,
    )


async def revoke_processing_consent(
    store: IdentityStore,
    *,
    owner_id: UUID,
    expected_revision: int,
    expected_grant_id: UUID,
    expected_policy_version: str,
    revoked_at: datetime,
) -> ProcessingConsentSnapshot:
    if revoked_at.tzinfo is None or revoked_at.utcoffset() is None:
        raise ValueError("processing consent time must be timezone-aware")
    return await store.revoke_processing_consent(
        owner_id=owner_id,
        expected_revision=expected_revision,
        expected_grant_id=expected_grant_id,
        expected_policy_version=expected_policy_version,
        revoked_at=revoked_at,
    )


async def authorize_processing_consent(
    store: IdentityStore, *, owner_id: UUID, authorization: ProcessingAuthorization
) -> bool:
    if authorization.owner_id != owner_id:
        return False
    if authorization.policy_version != PROCESSING_CONSENT_POLICY_VERSION:
        return False
    return await store.authorize_processing_consent(owner_id, authorization)


async def invalidate_restored_processing_consent(
    store: IdentityStore, *, owner_id: UUID
) -> bool:
    return await store.invalidate_restored_processing_consent(owner_id)


async def list_granted_processing_consent_owners(store: IdentityStore) -> tuple[UUID, ...]:
    """Enumerate restored grants through Identity's published store contract."""
    return await store.list_granted_processing_consent_owners()
