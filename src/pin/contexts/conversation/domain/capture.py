"""Framework-independent durable text-capture draft states."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class CaptureState(StrEnum):
    COLLECTING = "collecting"
    PROCESSING = "processing"
    READY = "ready"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


class CaptureConflict(ValueError):
    """The owner draft is busy, stale, or no longer eligible for this action."""


@dataclass(frozen=True, slots=True)
class CaptureMessage:
    role: str
    text: str

    def __post_init__(self) -> None:
        if self.role not in {"user", "assistant"}:
            raise ValueError("capture message role must be user or assistant")
        if not self.text.strip() or len(self.text) > 4_000:
            raise ValueError("capture message must contain 1 to 4000 characters")


@dataclass(frozen=True, slots=True)
class CaptureDraft:
    id: UUID
    owner_id: UUID
    version: int
    state: CaptureState
    messages: tuple[CaptureMessage, ...]
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    sequence: int
    processing_grant_id: UUID | None = None
    processing_policy_version: str | None = None
    processing_key: str | None = None
    normalized_actions: tuple[dict[str, object], ...] = ()
    preview: dict[str, object] | None = None
    projection_sha256: str | None = None
    confirmed_result: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if self.id.int == 0 or self.owner_id.int == 0:
            raise ValueError("capture draft and owner IDs must be non-zero")
        if self.version < 1 or self.sequence < 1:
            raise ValueError("capture version and sequence must be positive")
        if (self.processing_grant_id is None) != (self.processing_policy_version is None):
            raise ValueError("processing grant and policy provenance must be paired")
        for instant in (self.created_at, self.updated_at, self.expires_at):
            if instant.tzinfo is None or instant.utcoffset() is None:
                raise ValueError("capture timestamps must be timezone-aware")
        if len(self.messages) > 20:
            raise ValueError("capture history is limited to 20 messages")
        if self.state is CaptureState.READY and (
            not self.normalized_actions or self.preview is None or self.projection_sha256 is None
        ):
            raise ValueError("ready capture requires actions and a bound preview")
        if self.state is CaptureState.PROCESSING and not self.processing_key:
            raise ValueError("processing capture requires a durable processing key")


def start_capture(
    *,
    draft_id: UUID,
    owner_id: UUID,
    text: str,
    request_key: str,
    at: datetime,
    expires_at: datetime,
    sequence: int,
    processing_grant_id: UUID | None = None,
    processing_policy_version: str | None = None,
) -> CaptureDraft:
    _aware(at)
    _aware(expires_at)
    if expires_at <= at or not request_key:
        raise ValueError("capture expiry and request key are invalid")
    return CaptureDraft(
        id=draft_id,
        owner_id=owner_id,
        version=1,
        state=CaptureState.PROCESSING,
        messages=(CaptureMessage("user", text),),
        created_at=at,
        updated_at=at,
        expires_at=expires_at,
        sequence=sequence,
        processing_grant_id=processing_grant_id,
        processing_policy_version=processing_policy_version,
        processing_key=request_key,
    )


def append_user_input(
    draft: CaptureDraft,
    *,
    text: str,
    request_key: str,
    at: datetime,
    processing_grant_id: UUID | None = None,
    processing_policy_version: str | None = None,
) -> CaptureDraft:
    _active(draft, at)
    if draft.state is CaptureState.PROCESSING:
        raise CaptureConflict("another capture turn is still processing")
    if not request_key:
        raise ValueError("request key is required")
    messages = (*draft.messages, CaptureMessage("user", text))[-20:]
    return replace(
        draft,
        version=draft.version + 1,
        state=CaptureState.PROCESSING,
        messages=messages,
        processing_grant_id=processing_grant_id,
        processing_policy_version=processing_policy_version,
        updated_at=at,
        processing_key=request_key,
        # Keep pending, still-unsaved proposals visible to the model context.
        # Only the approved preview/projection are invalidated by a correction.
        preview=None,
        projection_sha256=None,
    )


def finish_collecting(
    draft: CaptureDraft,
    *,
    request_key: str,
    reply: str,
    at: datetime,
    actions: tuple[dict[str, object], ...] | None = None,
) -> CaptureDraft:
    _processing_fence(draft, request_key, at)
    return replace(
        draft,
        version=draft.version + 1,
        state=CaptureState.COLLECTING,
        messages=(*draft.messages, CaptureMessage("assistant", reply))[-20:],
        updated_at=at,
        processing_key=None,
        normalized_actions=draft.normalized_actions if actions is None else actions,
        preview=None,
        projection_sha256=None,
    )


def begin_clarification_choice(
    draft: CaptureDraft,
    *,
    expected_version: int,
    request_key: str,
    at: datetime,
) -> CaptureDraft:
    """Fence one deterministic button choice without reserving an AI turn."""
    _version_fence(draft, expected_version)
    _active(draft, at)
    if draft.state is not CaptureState.COLLECTING or not draft.normalized_actions:
        raise CaptureConflict("capture has no pending clarification")
    if not request_key:
        raise ValueError("clarification request key is required")
    return replace(
        draft,
        version=draft.version + 1,
        state=CaptureState.PROCESSING,
        updated_at=at,
        processing_key=request_key,
        preview=None,
        projection_sha256=None,
    )


def finish_ready(
    draft: CaptureDraft,
    *,
    request_key: str,
    actions: tuple[dict[str, object], ...],
    preview: dict[str, object],
    projection_sha256: str,
    reply: str,
    at: datetime,
) -> CaptureDraft:
    _processing_fence(draft, request_key, at)
    if not actions or len(projection_sha256) != 64:
        raise ValueError("ready capture proof must bind a non-empty batch projection")
    return replace(
        draft,
        version=draft.version + 1,
        state=CaptureState.READY,
        messages=(*draft.messages, CaptureMessage("assistant", reply))[-20:],
        updated_at=at,
        processing_key=None,
        normalized_actions=actions,
        preview=preview,
        projection_sha256=projection_sha256,
    )


def discard_processing_result(
    draft: CaptureDraft, *, request_key: str, at: datetime
) -> CaptureDraft:
    """Terminalize a provider result that lost its authorization fence."""
    _aware(at)
    if draft.state is not CaptureState.PROCESSING or draft.processing_key != request_key:
        raise CaptureConflict("capture processing result is stale")
    return replace(
        draft,
        version=draft.version + 1,
        state=CaptureState.FAILED,
        updated_at=at,
        processing_key=None,
        normalized_actions=(),
        preview=None,
        projection_sha256=None,
    )


def finish_failed(
    draft: CaptureDraft, *, request_key: str, reply: str, at: datetime
) -> CaptureDraft:
    _processing_fence(draft, request_key, at)
    return replace(
        draft,
        version=draft.version + 1,
        state=CaptureState.COLLECTING,
        messages=(*draft.messages, CaptureMessage("assistant", reply))[-20:],
        updated_at=at,
        processing_key=None,
    )


def cancel_capture(draft: CaptureDraft, *, expected_version: int, at: datetime) -> CaptureDraft:
    _version_fence(draft, expected_version)
    _active(draft, at)
    if draft.state is CaptureState.PROCESSING:
        raise CaptureConflict("processing capture cannot be cancelled until the turn settles")
    return replace(
        draft,
        version=draft.version + 1,
        state=CaptureState.CANCELLED,
        updated_at=at,
        normalized_actions=(),
        preview=None,
        projection_sha256=None,
    )


def confirm_capture(
    draft: CaptureDraft,
    *,
    expected_version: int,
    result: dict[str, object],
    at: datetime,
) -> CaptureDraft:
    _version_fence(draft, expected_version)
    _active(draft, at)
    if draft.state is not CaptureState.READY:
        raise CaptureConflict("only a ready capture can be confirmed")
    return replace(
        draft,
        version=draft.version + 1,
        state=CaptureState.CONFIRMED,
        updated_at=at,
        confirmed_result=result,
    )


def expire_capture(draft: CaptureDraft, *, at: datetime) -> CaptureDraft:
    _aware(at)
    if at < draft.expires_at or draft.state not in {
        CaptureState.COLLECTING,
        CaptureState.READY,
    }:
        raise CaptureConflict("capture is not eligible for expiry")
    return replace(
        draft,
        version=draft.version + 1,
        state=CaptureState.EXPIRED,
        updated_at=at,
        normalized_actions=(),
        preview=None,
        projection_sha256=None,
    )


def _processing_fence(draft: CaptureDraft, request_key: str, at: datetime) -> None:
    _active(draft, at)
    if draft.state is not CaptureState.PROCESSING or draft.processing_key != request_key:
        raise CaptureConflict("capture processing result is stale")


def _active(draft: CaptureDraft, at: datetime) -> None:
    _aware(at)
    if at >= draft.expires_at:
        raise CaptureConflict("capture draft has expired")
    if draft.state in {
        CaptureState.CONFIRMED,
        CaptureState.CANCELLED,
        CaptureState.EXPIRED,
        CaptureState.FAILED,
    }:
        raise CaptureConflict("capture draft is no longer active")


def _version_fence(draft: CaptureDraft, expected_version: int) -> None:
    if expected_version != draft.version:
        raise CaptureConflict("capture version is stale")


def _aware(at: datetime) -> None:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("capture time must be timezone-aware")
