from __future__ import annotations

from datetime import UTC, date, datetime, time
from uuid import uuid4

import pytest

from pin.adapters.telegram.support import with_owned_plans
from pin.contexts.conversation.application.capture import CaptureInterpretation
from pin.contexts.conversation.application.dto import ConversationContext
from pin.contexts.conversation.domain.capture import start_capture
from pin.contexts.conversation.domain.proposals import InputKind, SourceLanguage
from pin.contexts.planning.domain.plans import OccurrenceState, PlanKind, ResolvedSchedule
from pin.contexts.planning.mutation_read_api import MutationTargetSummary


def _interpretation(owner_id):
    now = datetime(2026, 10, 14, 8, tzinfo=UTC)
    draft = start_capture(
        draft_id=uuid4(),
        owner_id=owner_id,
        text="What should I do next?",
        request_key="support-turn",
        at=now,
        expires_at=now.replace(day=15),
        sequence=1,
    )
    return CaptureInterpretation(
        draft=draft,
        request_key="support-turn",
        context=ConversationContext(
            owner_id=owner_id,
            request_id="support-turn",
            language=SourceLanguage.EN,
            input_kind=InputKind.TEXT,
            text="Current user turn: What should I do next?",
            reference_date=now.date(),
            reference_instant=now,
            timezone="Asia/Almaty",
        ),
    )


def _target(owner_id):
    return MutationTargetSummary(
        owner_id=owner_id,
        occurrence_id=uuid4(),
        occurrence_version=2,
        plan_item_id=uuid4(),
        plan_version=1,
        series_id=None,
        series_version=None,
        state=OccurrenceState.OPEN,
        title="Prepare agenda",
        schedule=ResolvedSchedule(
            kind=PlanKind.APPOINTMENT,
            event_zone="Asia/Almaty",
            local_date=date(2026, 10, 14),
            local_time=time(15),
            fold=None,
            end_local_date=date(2026, 10, 14),
            end_local_time=time(16),
            end_fold=None,
            starts_at=datetime(2026, 10, 14, 9, tzinfo=UTC),
            ends_at=datetime(2026, 10, 14, 10, tzinfo=UTC),
            deadline_at=None,
            duration_seconds=3600,
        ),
    )


def test_support_context_preserves_owned_plan_schedule_without_claiming_free_gaps() -> None:
    owner_id = uuid4()
    interpretation = _interpretation(owner_id)

    enriched = with_owned_plans(interpretation, (_target(owner_id),))

    assert enriched.context.plans[0].title == "Prepare agenda"
    assert enriched.context.plans[0].due_at == datetime(2026, 10, 14, 9, tzinfo=UTC)
    assert enriched.context.plans[0].ends_at == datetime(2026, 10, 14, 10, tzinfo=UTC)
    assert enriched.context.plans[0].timezone == "Asia/Almaty"
    assert enriched.context.targets[0].kind == "occurrence"
    assert enriched.context.targets[0].version == 2
    assert enriched.context.text == interpretation.context.text


def test_support_context_rejects_foreign_plan_records() -> None:
    interpretation = _interpretation(uuid4())

    with pytest.raises(ValueError, match="foreign planning target"):
        with_owned_plans(interpretation, (_target(uuid4()),))
