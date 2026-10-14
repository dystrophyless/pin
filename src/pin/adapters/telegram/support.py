"""Build the bounded, owner-filtered plan context used by planning support."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID

from pin.contexts.conversation.application.capture import CaptureInterpretation
from pin.contexts.conversation.application.dto import PlanSnapshot, TargetSnapshot
from pin.contexts.conversation.application.service import bounded_target_snapshot
from pin.contexts.planning.mutation_read_api import MutationTargetSummary


def with_owned_plans(
    interpretation: CaptureInterpretation,
    targets: Sequence[MutationTargetSummary],
) -> CaptureInterpretation:
    """Attach only the caller's bounded Planning read result to Conversation context."""
    owner_id = interpretation.draft.owner_id
    if any(item.owner_id != owner_id for item in targets):
        raise ValueError("support context contains a foreign planning target")

    plan_snapshots = tuple(
        PlanSnapshot(
            id=item.occurrence_id,
            version=item.occurrence_version,
            title=item.title[:240],
            due_at=item.schedule.anchor_at,
            timezone=item.schedule.event_zone,
            ends_at=item.schedule.ends_at,
            status="open",
        )
        for item in targets
    )
    target_snapshots: list[TargetSnapshot] = [
        TargetSnapshot(
            id=item.occurrence_id,
            version=item.occurrence_version,
            kind="occurrence",
            title=item.title[:240],
        )
        for item in targets
    ]
    series_seen: set[UUID] = set()
    for item in targets:
        if item.series_id is not None and item.series_id not in series_seen:
            assert item.series_version is not None
            target_snapshots.append(
                TargetSnapshot(
                    id=item.series_id,
                    version=item.series_version,
                    kind="series",
                    title=item.title[:240],
                )
            )
            series_seen.add(item.series_id)

    context = interpretation.context.model_copy(
        update={"targets": tuple(target_snapshots), "plans": plan_snapshots}
    )
    return replace(
        interpretation,
        context=bounded_target_snapshot(context),
    )

