"""Consumer-owned ports for confirmed one-off planning commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pin.contexts.planning.domain.plans import ProposedPlan


@dataclass(frozen=True, slots=True)
class ConfirmationProof:
    """Opaque, server-issued proof credential; this value is never self-authenticating."""

    preview_id: UUID
    preview_version: int
    credential: str


@dataclass(frozen=True, slots=True)
class ConfirmationGrant:
    owner_id: UUID
    preview_id: UUID
    preview_version: int
    producer_id: str
    approved_projection_sha256: str
    issued_at: datetime
    expires_at: datetime


class ConfirmationVerifier(Protocol):
    """Trusted producer adapter; verify and consume within the command's UoW.

    Implementations must authenticate an unguessable producer-issued credential,
    require an approved producer, bind it to the authenticated owner and exact
    approved schedule/reminder projection fingerprint, reject
    cancelled/expired/already-consumed grants,
    and write the consume marker in the same database transaction as the command.
    The application has no permissive implementation or boolean confirmation flag.
    """

    async def verify_and_consume(
        self,
        proof: ConfirmationProof,
        *,
        owner_id: UUID,
        approved_projection_sha256: str,
        at: datetime,
    ) -> ConfirmationGrant | None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class PlanCommandStore(Protocol):
    async def lock_action(self, owner_id: UUID, action_key: str) -> None: ...

    async def get_receipt(self, owner_id: UUID, action_key: str) -> dict[str, object] | None: ...

    async def persist_confirmed_batch(
        self,
        *,
        owner_id: UUID,
        action_key: str,
        payload_sha256: str,
        approved_preferences_version: int,
        plans: tuple[ProposedPlan, ...],
        at: datetime,
    ) -> dict[str, object]: ...
