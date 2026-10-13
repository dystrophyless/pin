from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest

from pin.adapters.telegram.presentation.capture import (
    build_capture_keyboard,
    capture_choice_callback_data,
    capture_confirmation_callback_data,
    render_capture_response,
)
from pin.contexts.conversation.application.capture import (
    CaptureClarification,
    CaptureInterpretation,
    _candidates,
    _effective_reminders,
    _preview,
    finish_interpretation,
)
from pin.contexts.conversation.application.contracts import CaptureProofPart, CaptureStore
from pin.contexts.conversation.application.dto import (
    ActionProposal,
    ConversationContext,
    ModelSuccess,
    ProposalBatch,
    RelativeReminder,
    Reply,
)
from pin.contexts.conversation.domain.capture import (
    CaptureConflict,
    CaptureDraft,
    CaptureState,
    append_user_input,
    cancel_capture,
    confirm_capture,
    finish_collecting,
    finish_ready,
    start_capture,
)
from pin.contexts.conversation.domain.proposals import InputKind, SourceLanguage
from pin.contexts.identity.api import PreferencesSnapshot
from pin.contexts.planning.api import OffsetKind

NOW = datetime(2026, 10, 14, 8, tzinfo=UTC)
OWNER = uuid4()


def _preferences() -> PreferencesSnapshot:
    return PreferencesSnapshot(
        owner_id=OWNER,
        language="en",
        home_zone="Asia/Qyzylorda",
        reminder_calendar_days=1,
        reminder_elapsed_hours=1,
        quiet_start=time(23),
        quiet_end=time(7),
        review_time=time(18),
        version=3,
    )


def _proposal(payload: dict[str, object]) -> ActionProposal:
    return ActionProposal.model_validate_json(json.dumps(payload))


def test_missing_appointment_end_and_missing_time_remain_clarifications() -> None:
    preferences = _preferences()
    appointment = _proposal(
        {
            "kind": "proposal",
            "language": "en",
            "operation": "propose_create_plan",
            "fields": {
                "title": "Design review",
                "schedule": {
                    "kind": "appointment",
                    "local_start": "2026-10-16T14:00:00",
                    "timezone": "Asia/Qyzylorda",
                },
            },
        }
    )
    candidates, question = _candidates((appointment,), preferences, NOW)
    assert candidates is None
    assert isinstance(question, CaptureClarification)
    assert "How long" in question.text
    assert [choice.id for choice in question.choices] == [
        "appointment:0:duration:30",
        "appointment:0:duration:60",
        "appointment:0:ask_duration",
    ]

    missing_date = _proposal(
        {
            "kind": "proposal",
            "language": "en",
            "operation": "propose_create_plan",
            "fields": {"title": "Prepare draft", "schedule": {"kind": "task"}},
        }
    )
    candidates, question = _candidates((missing_date,), preferences, NOW)
    assert candidates is None
    assert isinstance(question, str) and "date" in question


def test_timed_suggestion_is_clickable_but_never_assumed_or_offered_for_past_time() -> None:
    def task(day: str) -> ActionProposal:
        return _proposal(
            {
                "kind": "proposal",
                "language": "en",
                "operation": "propose_create_plan",
                "fields": {
                    "title": "Exercise",
                    "schedule": {
                        "kind": "task",
                        "local_date": day,
                        "timing": "unspecified",
                    },
                },
            }
        )

    candidates, future_question = _candidates((task("2026-10-16"),), _preferences(), NOW)
    assert candidates is None and isinstance(future_question, CaptureClarification)
    assert [choice.id for choice in future_question.choices] == [
        "task:0:preferred_time",
        "task:0:untimed",
        "task:0:ask_time",
    ]
    assert "Suggestion" in future_question.choices[0].label

    _, past_question = _candidates((task("2026-10-13"),), _preferences(), NOW)
    assert isinstance(past_question, CaptureClarification)
    assert [choice.id for choice in past_question.choices] == [
        "task:0:untimed",
        "task:0:ask_time",
    ]


def test_explicit_two_hour_reminder_keeps_calendar_default_and_replaces_hour_default() -> None:
    preferences = _preferences()
    proposal = _proposal(
        {
            "kind": "proposal",
            "language": "en",
            "operation": "propose_create_plan",
            "fields": {
                "title": "Design review",
                "schedule": {
                    "kind": "appointment",
                    "local_start": "2026-10-16T14:00:00",
                    "timezone": "Asia/Qyzylorda",
                    "duration_minutes": 30,
                },
                "reminders": [
                    {
                        "kind": "explicit",
                        "local_at": "2026-10-16T12:00:00",
                        "timezone": "Asia/Qyzylorda",
                    }
                ],
            },
        }
    )
    candidates, question = _candidates((proposal,), preferences, NOW)
    assert question is None
    assert candidates is not None
    preview, proof_parts = _preview(OWNER, candidates, preferences, NOW)
    items = cast(list[dict[str, object]], preview["items"])
    details = cast(dict[str, object], items[0]["details"])
    reminders = cast(list[dict[str, object]], details["reminders"])
    assert [reminder["due_at"] for reminder in reminders] == [
        "2026-10-15T09:00:00+00:00",
        "2026-10-16T07:00:00+00:00",
    ]
    assert [(part.key, part.projection_sha256) for part in proof_parts] == [
        ("one_off", proof_parts[0].projection_sha256)
    ]
    one_off = candidates[0].one_off
    assert one_off is not None and one_off.reminders is not None
    assert [(offset.kind, offset.amount) for offset in one_off.reminders] == [
        (OffsetKind.CALENDAR_DAYS, 1),
        (OffsetKind.ELAPSED_SECONDS, 7_200),
    ]


def test_past_explicit_reminder_is_clarified_instead_of_silently_omitted() -> None:
    proposal = _proposal(
        {
            "kind": "proposal",
            "language": "en",
            "operation": "propose_create_plan",
            "fields": {
                "title": "Design review",
                "schedule": {
                    "kind": "appointment",
                    "local_start": "2026-10-16T14:00:00",
                    "timezone": "Asia/Qyzylorda",
                    "duration_minutes": 30,
                },
                "reminders": [
                    {
                        "kind": "explicit",
                        "local_at": "2026-10-14T12:00:00",
                        "timezone": "Asia/Qyzylorda",
                    }
                ],
            },
        }
    )
    candidates, question = _candidates((proposal,), _preferences(), NOW)
    assert question is None and candidates
    with pytest.raises(ValueError, match="explicitly requested reminder is in the past"):
        _preview(OWNER, candidates, _preferences(), NOW)


def test_reminder_modes_distinguish_defaults_override_and_empty_replacement() -> None:
    preferences = _preferences()
    default, error = _effective_reminders(None, None, preferences)
    assert error is None and default is None
    empty, error = _effective_reminders((), None, preferences)
    assert error is None and empty == ()
    only, error = _effective_reminders(
        (
            RelativeReminder(
                kind="relative",
                basis="elapsed",
                amount=2,
                unit="hour",
                provenance="explicit",
            ),
        ),
        "only",
        preferences,
    )
    assert error is None
    assert only is not None
    assert [(item.kind, item.amount) for item in only] == [(OffsetKind.ELAPSED_SECONDS, 7_200)]


def test_untimed_choice_does_not_silently_drop_explicit_reminder() -> None:
    proposal = _proposal(
        {
            "kind": "proposal",
            "language": "ru",
            "operation": "propose_create_plan",
            "fields": {
                "title": "Зарядка",
                "schedule": {
                    "kind": "task",
                    "local_date": "2026-10-16",
                    "timing": "untimed",
                },
                "reminders": [
                    {
                        "kind": "relative",
                        "basis": "elapsed",
                        "amount": 2,
                        "unit": "hour",
                        "provenance": "explicit",
                    }
                ],
            },
        }
    )
    candidates, question = _candidates((proposal,), _preferences(), NOW)
    assert candidates is None
    assert isinstance(question, str)
    assert question.startswith("Для напоминания")


def test_mixed_recurring_batch_has_one_projection_and_separate_exact_proof_parts() -> None:
    preferences = _preferences()
    one_off = _proposal(
        {
            "kind": "proposal",
            "language": "en",
            "operation": "propose_create_plan",
            "fields": {
                "title": "Prepare agenda",
                "schedule": {
                    "kind": "task",
                    "local_date": "2026-10-15",
                    "timing": "untimed",
                },
            },
        }
    )
    recurring = _proposal(
        {
            "kind": "proposal",
            "language": "en",
            "operation": "propose_create_plan",
            "fields": {
                "title": "Weekly review",
                "schedule": {
                    "kind": "appointment",
                    "local_start": "2026-10-16T14:00:00",
                    "timezone": "Asia/Qyzylorda",
                    "duration_minutes": 30,
                },
                "recurrence": {"frequency": "weekly", "weekdays": [4], "occurrence_count": 4},
            },
        }
    )
    candidates, question = _candidates((one_off, recurring), preferences, NOW)
    assert question is None
    assert candidates and candidates[0].one_off and candidates[1].recurring
    preview, parts = _preview(OWNER, candidates, preferences, NOW)
    items = cast(list[dict[str, object]], preview["items"])
    assert [item["kind"] for item in items] == ["plan", "recurring"]
    assert {part.key for part in parts} == {"one_off", "r1"}
    recurrent = cast(dict[str, object], items[1]["details"])
    assert recurrent["title"] == "Weekly review"
    assert recurrent["occurrences"]


def test_correction_invalidates_ready_projection_and_stale_confirmation() -> None:
    draft = start_capture(
        draft_id=uuid4(),
        owner_id=OWNER,
        text="Plan an appointment",
        request_key="update-1",
        at=NOW,
        expires_at=NOW + timedelta(hours=24),
        sequence=1,
    )
    ready = finish_ready(
        draft,
        request_key="update-1",
        actions=({"kind": "proposal"},),
        preview={"items": []},
        projection_sha256="a" * 64,
        reply="Review and confirm",
        at=NOW + timedelta(seconds=1),
    )
    correction = append_user_input(
        ready, text="Make it Friday", request_key="update-2", at=NOW + timedelta(seconds=2)
    )
    assert correction.state is CaptureState.PROCESSING
    assert correction.version == ready.version + 1
    assert correction.preview is None
    assert correction.normalized_actions == ready.normalized_actions
    with pytest.raises(CaptureConflict):
        confirm_capture(
            correction,
            expected_version=correction.version,
            result={"items": []},
            at=NOW + timedelta(seconds=3),
        )


class _MemoryCaptureStore:
    def __init__(self) -> None:
        self.draft: CaptureDraft | None = None
        self.parts: tuple[CaptureProofPart, ...] = ()
        self.completed: dict[str, object] | None = None

    async def persist_draft(self, draft: CaptureDraft, *, expected_version: int) -> None:
        self.draft = draft

    async def issue_proofs(
        self,
        *,
        owner_id: UUID,
        draft_id: UUID,
        preview_version: int,
        parts: tuple[CaptureProofPart, ...],
        issued_at: datetime,
        expires_at: datetime,
    ) -> str:
        self.parts = parts
        return "injected-capture-proof"

    async def complete_input(
        self,
        *,
        owner_id: UUID,
        input_key: str,
        state: str,
        response: dict[str, object],
        at: datetime,
    ) -> None:
        self.completed = response


def _create_proposal(
    title: str, *, recurrence: bool = False, local_time: str = "18:00:00"
) -> ActionProposal:
    fields: dict[str, object] = {
        "title": title,
        "schedule": {
            "kind": "task",
            "local_date": "2026-10-16",
            "local_time": local_time,
            "timezone": "Asia/Qyzylorda",
            "timing": "timed",
        },
        "reminders": [],
    }
    if recurrence:
        fields["recurrence"] = {
            "frequency": "weekly",
            "weekdays": [4],
            "occurrence_count": 3,
        }
    return _proposal(
        {
            "kind": "proposal",
            "language": "en",
            "operation": "propose_create_plan",
            "fields": fields,
        }
    )


def _processing_correction(
    actions: tuple[ActionProposal, ...],
) -> tuple[CaptureDraft, CaptureInterpretation]:
    serialized = tuple(action.model_dump(mode="json", exclude_unset=True) for action in actions)
    draft = start_capture(
        draft_id=uuid4(),
        owner_id=OWNER,
        text="Create two plans",
        request_key="capture-original",
        at=NOW,
        expires_at=NOW + timedelta(hours=24),
        sequence=1,
    )
    collecting = finish_collecting(
        draft,
        request_key="capture-original",
        reply="Review these two plans.",
        actions=serialized,
        at=NOW + timedelta(seconds=1),
    )
    processing = append_user_input(
        collecting,
        text="Change the first plan",
        request_key="capture-correction",
        at=NOW + timedelta(seconds=2),
    )
    context = ConversationContext(
        owner_id=OWNER,
        request_id="capture-correction",
        language=SourceLanguage.EN,
        input_kind=InputKind.TEXT,
        text="Pending normalized plan proposals: [two items]. Current user turn: change the first.",
        reference_date=NOW.date(),
        timezone="Asia/Qyzylorda",
    )
    return processing, CaptureInterpretation(processing, "capture-correction", context)


@pytest.mark.asyncio
async def test_mixed_batch_correction_replaces_all_items_and_previews_the_complete_batch() -> None:
    original = (_create_proposal("Exercise"), _create_proposal("Weekly class", recurrence=True))
    processing, interpretation = _processing_correction(original)
    assert processing.normalized_actions and len(processing.normalized_actions) == 2

    corrected = ProposalBatch(
        kind="proposal_batch",
        schema_version=1,
        language=SourceLanguage.EN,
        actions=(
            _create_proposal("Exercise", local_time="19:00:00"),
            original[1],
        ),
    )
    store = _MemoryCaptureStore()
    reply = await finish_interpretation(
        cast(CaptureStore, store),
        interpretation,
        ModelSuccess(result=corrected, provider_request_ids=("fake-correction",)),
        preferences=_preferences(),
        at=NOW + timedelta(seconds=3),
    )

    assert reply.state == "ready" and reply.confirmation_credential
    assert store.draft is not None and store.draft.normalized_actions
    assert len(store.draft.normalized_actions) == 2
    assert [
        cast(dict[str, object], item["fields"])["title"]
        for item in store.draft.normalized_actions
    ] == [
        "Exercise",
        "Weekly class",
    ]
    assert [part.key for part in store.parts] == ["one_off", "r1"]
    assert store.draft.preview is not None
    preview_items = cast(list[dict[str, object]], store.draft.preview["items"])
    assert [item["kind"] for item in preview_items] == ["plan", "recurring"]


@pytest.mark.asyncio
async def test_unresolved_batch_correction_keeps_pending_items_and_saves_no_preview() -> None:
    original = (_create_proposal("Exercise"), _create_proposal("Weekly class", recurrence=True))
    processing, interpretation = _processing_correction(original)
    store = _MemoryCaptureStore()
    clarification = Reply(
        kind="reply", language=SourceLanguage.EN, text="Which Friday should I use?"
    )

    reply = await finish_interpretation(
        cast(CaptureStore, store),
        interpretation,
        ModelSuccess(result=clarification, provider_request_ids=("fake-clarification",)),
        preferences=_preferences(),
        at=NOW + timedelta(seconds=3),
    )

    assert reply.state == "collecting"
    assert store.draft is not None and store.draft.preview is None
    assert store.draft.normalized_actions == processing.normalized_actions
    assert store.parts == ()


@pytest.mark.asyncio
async def test_single_item_output_cannot_silently_shrink_pending_multi_item_draft() -> None:
    original = (_create_proposal("Exercise"), _create_proposal("Weekly class", recurrence=True))
    processing, interpretation = _processing_correction(original)
    store = _MemoryCaptureStore()

    reply = await finish_interpretation(
        cast(CaptureStore, store),
        interpretation,
        ModelSuccess(
            result=_create_proposal("Only the corrected item"),
            provider_request_ids=("fake-partial-correction",),
        ),
        preferences=_preferences(),
        at=NOW + timedelta(seconds=3),
    )

    assert reply.state == "collecting"
    assert "complete set of plans" in cast(str, reply.response["text"])
    assert store.draft is not None and store.draft.preview is None
    assert store.draft.normalized_actions == processing.normalized_actions
    assert store.parts == ()


def test_cancel_expiry_and_confirmation_callback_are_fenced() -> None:
    draft = start_capture(
        draft_id=uuid4(),
        owner_id=OWNER,
        text="Add a task",
        request_key="update-1",
        at=NOW,
        expires_at=NOW + timedelta(hours=24),
        sequence=1,
    )
    with pytest.raises(CaptureConflict):
        cancel_capture(draft, expected_version=draft.version, at=NOW)
    ready = finish_ready(
        draft,
        request_key="update-1",
        actions=({"kind": "proposal"},),
        preview={"items": []},
        projection_sha256="b" * 64,
        reply="Review and confirm",
        at=NOW + timedelta(seconds=1),
    )
    cancelled = cancel_capture(ready, expected_version=ready.version, at=NOW + timedelta(seconds=2))
    assert cancelled.state is CaptureState.CANCELLED
    assert capture_confirmation_callback_data("a" * 32) == "capc:" + "a" * 32
    assert capture_choice_callback_data("q" * 32) == "capq:" + "q" * 32
    markup = build_capture_keyboard(
        confirm_callback_data="capc:" + "a" * 32,
        cancel_callback_data="capx:" + "b" * 40,
        choices=({"id": "task:0:untimed", "label": "Leave untimed"},),
        choice_callback_data={"task:0:untimed": "capq:" + "c" * 32},
        language="en",
    )
    assert markup is not None and len(markup.inline_keyboard) == 3
    pages = render_capture_response(
        {
            "kind": "preview",
            "text": "Confirm only after reviewing.",
            "preview": {
                "items": [
                    {
                        "kind": "plan",
                        "details": {
                            "title": "A_B",
                            "kind": "task",
                            "local_date": "2026-10-15",
                            "reminders": [],
                        },
                    }
                ]
            },
        },
        language="en",
    )
    assert len(pages) == 1
    assert "A\\_B" in pages[0].markdown
    assert "Confirm only after reviewing." in pages[0].plain_text
