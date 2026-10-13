"""Pure batch projection shared by preview producers and command application."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pin.contexts.planning.domain.plans import (
    OneOffPlanDraft,
    ProposedPlan,
    ReminderOffset,
    approval_projection_digest,
    payload_digest,
    propose_plan,
)


@dataclass(frozen=True, slots=True)
class PlanBatchPreview:
    plans: tuple[ProposedPlan, ...]
    payload_sha256: str
    approval_projection_sha256: str


def preview_plan_batch(
    owner_id: UUID,
    drafts: tuple[OneOffPlanDraft, ...],
    *,
    expected_preferences_version: int,
    default_zone: str,
    default_reminders: tuple[ReminderOffset, ...],
    at: datetime,
) -> PlanBatchPreview:
    """Resolve a typed batch exactly as it would be applied at a preview instant.

    A trusted preview producer must render `plans`, then bind its proof to
    `approval_projection_sha256`. That fingerprint includes the typed payload,
    preference version, local/UTC schedule meaning, effective reminders, and any
    omitted default reminders. It excludes generated IDs and processing time.
    """
    if not drafts:
        raise ValueError("a confirmed plan batch cannot be empty")
    if expected_preferences_version < 1:
        raise ValueError("expected_preferences_version must be positive")
    payload_sha256 = payload_digest(
        drafts, expected_preferences_version=expected_preferences_version
    )
    plans = tuple(
        propose_plan(
            owner_id,
            draft,
            default_zone=default_zone,
            default_reminders=default_reminders,
            now=at,
        )
        for draft in drafts
    )
    return PlanBatchPreview(
        plans=plans,
        payload_sha256=payload_sha256,
        approval_projection_sha256=approval_projection_digest(
            drafts,
            expected_preferences_version=expected_preferences_version,
            plans=plans,
        ),
    )
