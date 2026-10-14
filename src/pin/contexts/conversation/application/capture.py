"""Preview and persist typed create proposals for the text-capture turn."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pin.contexts.billing.api import AccessUsageStore
from pin.contexts.conversation.application.advice import render_advice
from pin.contexts.conversation.application.contracts import CaptureProofPart, CaptureStore
from pin.contexts.conversation.application.dto import (
    ActionProposal,
    Advice,
    AppointmentSchedule,
    ConversationContext,
    DeadlineSchedule,
    ExplicitReminder,
    ModelFailure,
    ModelOutcome,
    PlanSchedule,
    ProposalBatch,
    RecurrenceSpec,
    RelativeReminder,
    TaskSchedule,
)
from pin.contexts.conversation.domain.capture import (
    CaptureConflict,
    CaptureDraft,
    begin_clarification_choice,
    finish_collecting,
    finish_failed,
    finish_ready,
)
from pin.contexts.conversation.domain.proposals import ProposalOperation
from pin.contexts.identity.api import IdentityStore, PreferencesSnapshot
from pin.contexts.planning.api import (
    Clock,
    ConfirmationProof,
    ConfirmationVerifier,
    ConfirmedPlanBatch,
    CreateConfirmedSeries,
    OffsetKind,
    OneOffPlanDraft,
    PlanCommandStore,
    PlanKind,
    ProposedPlan,
    RecurrenceCommandStore,
    RecurrenceConfirmationProof,
    RecurrenceConfirmationVerifier,
    RecurrenceFrequency,
    RecurrencePreview,
    RecurrenceRule,
    RecurringPlanDraft,
    ReminderOffset,
    create_confirmed_plan_batch,
    create_confirmed_series,
    preview_plan_batch,
    preview_recurring_series,
)

MAX_BATCH_ITEMS = 10
CAPTURE_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class CaptureInterpretation:
    draft: CaptureDraft
    request_key: str
    context: ConversationContext


@dataclass(frozen=True, slots=True)
class CaptureTurnReply:
    draft_id: UUID
    version: int
    state: str
    response: dict[str, object]
    confirmation_credential: str | None = None


@dataclass(frozen=True, slots=True)
class CaptureClarificationChoice:
    id: str
    label: str


@dataclass(frozen=True, slots=True)
class CaptureClarification:
    text: str
    choices: tuple[CaptureClarificationChoice, ...] = ()


@dataclass(frozen=True, slots=True)
class _Candidate:
    action: ActionProposal
    one_off: OneOffPlanDraft | None = None
    recurring: RecurringPlanDraft | None = None


async def prepare_interpretation(
    store: CaptureStore,
    *,
    owner_id: UUID,
    request_key: str,
    text: str,
    at: datetime,
    context_factory: Callable[[CaptureDraft, str, str], ConversationContext],
) -> CaptureInterpretation | CaptureTurnReply:
    """Persist the update fence, then return model work without holding a transaction."""
    begun = await store.begin(
        owner_id=owner_id,
        input_key=request_key,
        text=text,
        at=at,
        expires_at=at + CAPTURE_TTL,
    )
    if not begun.is_new_input:
        if begun.response is not None:
            credential = None
            if begun.draft.state.value == "ready":
                credential = await store.rotate_proofs(
                    owner_id=owner_id,
                    draft_id=begun.draft.id,
                    preview_version=begun.draft.version,
                    at=at,
                )
            return CaptureTurnReply(
                begun.draft.id,
                begun.draft.version,
                begun.draft.state.value,
                begun.response,
                credential,
            )
        # The provider result may be safely replayed by BoundedPlanningAssistant
        # using this exact request key; unresolved calls remain unknown there.
    context = context_factory(begun.draft, request_key, text)
    if not isinstance(context, ConversationContext):
        raise TypeError("context_factory must return a ConversationContext")
    return CaptureInterpretation(begun.draft, request_key, context)


async def restore_ready_preview(
    store: CaptureStore,
    *,
    owner_id: UUID,
    draft_id: UUID,
    at: datetime,
) -> CaptureTurnReply:
    """Load a persisted unsaved preview and issue a fresh ephemeral button token."""
    draft = await store.get_draft(owner_id=owner_id, draft_id=draft_id)
    if draft is None or draft.state.value != "ready" or draft.preview is None:
        raise CaptureConflict("there is no ready preview to restore")
    credential = await store.rotate_proofs(
        owner_id=owner_id,
        draft_id=draft_id,
        preview_version=draft.version,
        at=at,
    )
    return CaptureTurnReply(
        draft.id,
        draft.version,
        draft.state.value,
        {"kind": "preview", "preview": draft.preview, "text": ""},
        credential,
    )


async def finish_interpretation(
    store: CaptureStore,
    interpretation: CaptureInterpretation,
    outcome: ModelOutcome,
    *,
    preferences: PreferencesSnapshot,
    at: datetime,
) -> CaptureTurnReply:
    """Project a validated result and persist the draft/proof in the caller's UoW."""
    draft = interpretation.draft
    expected_version = draft.version
    if isinstance(outcome, ModelFailure):
        message = _failure_reply(outcome.category.value, interpretation.context.language.value)
        failed = finish_failed(draft, request_key=interpretation.request_key, reply=message, at=at)
        await store.persist_draft(failed, expected_version=expected_version)
        response = _reply_payload(message, state="collecting")
        await store.complete_input(
            owner_id=draft.owner_id,
            input_key=interpretation.request_key,
            state="failed",
            response=response,
            at=at,
        )
        return CaptureTurnReply(draft.id, failed.version, failed.state.value, response)

    result = outcome.result
    if not isinstance(result, (ActionProposal, ProposalBatch)):
        reply = render_advice(result) if isinstance(result, Advice) else result.text
        collecting = finish_collecting(
            draft,
            request_key=interpretation.request_key,
            reply=reply,
            at=at,
        )
        await store.persist_draft(collecting, expected_version=expected_version)
        response = _reply_payload(reply, state="collecting")
        await store.complete_input(
            owner_id=draft.owner_id,
            input_key=interpretation.request_key,
            state="complete",
            response=response,
            at=at,
        )
        return CaptureTurnReply(draft.id, collecting.version, collecting.state.value, response)

    if len(draft.normalized_actions) > 1 and isinstance(result, ActionProposal):
        return await _collect(
            store,
            interpretation,
            "Please restate the complete set of plans you want to keep. I need to review the "
            "whole batch before showing a replacement preview."
            if interpretation.context.language.value == "en"
            else (
                "Перечислите все планы, которые нужно оставить. Перед новым предварительным "
                "просмотром нужно проверить весь список целиком."
            ),
            at,
        )

    actions = result.actions if isinstance(result, ProposalBatch) else (result,)
    if len(actions) > MAX_BATCH_ITEMS:
        return await _collect(
            store,
            interpretation,
            "Please split this into 10 or fewer plans. No plans were saved."
            if interpretation.context.language.value == "en"
            else "Разделите запрос на 10 или меньше задач. Ничего не сохранено.",
            at,
        )
    if any(action.operation is not ProposalOperation.CREATE_PLAN for action in actions):
        return await _collect(
            store,
            interpretation,
            "I can prepare new plans here. Changes and cancellations are not applied from text."
            if interpretation.context.language.value == "en"
            else (
                "Здесь можно подготовить новые планы. Изменения и отмена через текст "
                "не применяются."
            ),
            at,
        )

    candidates, question = _candidates(actions, preferences, at)
    if question is not None:
        return await _collect(
            store,
            interpretation,
            question.text if isinstance(question, CaptureClarification) else question,
            at,
            actions=tuple(_action_json(action) for action in actions),
            choices=question.choices if isinstance(question, CaptureClarification) else (),
        )
    assert candidates
    try:
        preview, proof_parts = _preview(draft.owner_id, candidates, preferences, at)
    except (ValueError, OverflowError) as exc:
        reply = _planning_clarification(str(exc), interpretation.context.language.value)
        return await _collect(store, interpretation, reply, at)

    ready_reply = _confirmation_text(interpretation.context.language.value)
    ready = finish_ready(
        draft,
        request_key=interpretation.request_key,
        actions=tuple(_action_json(candidate.action) for candidate in candidates),
        preview=preview,
        projection_sha256=cast(str, preview["projection_sha256"]),
        reply=ready_reply,
        at=at,
    )
    credential = await store.issue_proofs(
        owner_id=draft.owner_id,
        draft_id=draft.id,
        preview_version=ready.version,
        parts=proof_parts,
        issued_at=at,
        expires_at=min(draft.expires_at, at + CAPTURE_TTL),
    )
    preview["proofs"] = [
        {
            "key": part.key,
            "preview_id": str(part.preview_id),
            "projection_sha256": part.projection_sha256,
        }
        for part in proof_parts
    ]
    preview["preferences_version"] = preferences.version
    await store.persist_draft(ready, expected_version=expected_version)
    response = {"kind": "preview", "text": ready_reply, "preview": preview}
    await store.complete_input(
        owner_id=draft.owner_id,
        input_key=interpretation.request_key,
        state="complete",
        response=response,
        at=at,
    )
    return CaptureTurnReply(draft.id, ready.version, ready.state.value, response, credential)


async def confirm_ready_draft(
    store: CaptureStore,
    *,
    owner_id: UUID,
    draft_id: UUID,
    expected_version: int,
    credential: str,
    plan_store: PlanCommandStore,
    recurrence_store: RecurrenceCommandStore,
    confirmation_verifier: ConfirmationVerifier,
    recurrence_confirmation_verifier: RecurrenceConfirmationVerifier,
    identity: IdentityStore,
    billing: AccessUsageStore,
    clock: Clock,
) -> CaptureTurnReply:
    """Confirm all one-off plans and recurring series in the caller's shared UoW."""
    draft = await store.get_draft(owner_id=owner_id, draft_id=draft_id)
    if draft is None:
        raise CaptureConflict("capture draft does not exist")
    if draft.state.value == "confirmed" and draft.confirmed_result is not None:
        return CaptureTurnReply(
            draft.id,
            draft.version,
            draft.state.value,
            {"kind": "confirmed", "result": draft.confirmed_result},
        )
    if draft.version != expected_version or draft.state.value != "ready":
        raise CaptureConflict("capture preview is stale")
    raw_proofs = (draft.preview or {}).get("proofs")
    if not isinstance(raw_proofs, list):
        raise CaptureConflict("capture preview has no confirmation proof parts")
    proof_rows = {
        str(row["key"]): row
        for row in raw_proofs
        if isinstance(row, dict) and isinstance(row.get("key"), str)
    }
    preferences = await identity.get_preferences(owner_id)
    if preferences is None or preferences.owner_id != owner_id:
        raise CaptureConflict("a current owner profile is required")
    preview_at = clock.now()
    actions = tuple(_parse_action(item) for item in draft.normalized_actions)
    candidates, question = _candidates(actions, preferences, preview_at)
    if question or candidates is None:
        raise CaptureConflict("the saved preview needs clarification")
    current_preview, current_parts = _preview(owner_id, candidates, preferences, preview_at)
    if current_preview["projection_sha256"] != draft.projection_sha256:
        raise CaptureConflict("the approved schedule changed; create a fresh preview")
    for key, issued in proof_rows.items():
        current = next((part for part in current_parts if part.key == key), None)
        if current is None or issued.get("projection_sha256") != current.projection_sha256:
            raise CaptureConflict("a confirmation proof no longer matches the exact preview")

    action_key = f"capture:{draft.id}:{draft.version}"
    results: list[dict[str, object]] = []
    one_offs = tuple(item.one_off for item in candidates if item.one_off is not None)
    if one_offs:
        proof = proof_rows.get("one_off")
        if proof is None:
            raise CaptureConflict("the one-off confirmation proof is missing")
        result = await create_confirmed_plan_batch(
            ConfirmedPlanBatch(
                action_key=action_key,
                expected_preferences_version=preferences.version,
                plans=one_offs,
                proof=ConfirmationProof(
                    UUID(str(proof["preview_id"])),
                    draft.version,
                    credential,
                ),
            ),
            authenticated_owner_id=owner_id,
            store=plan_store,
            identity=identity,
            billing=billing,
            confirmation_verifier=confirmation_verifier,
            clock=clock,
        )
        results.append(result)
    for index, candidate in enumerate(candidates):
        if candidate.recurring is None:
            continue
        proof = proof_rows.get(f"r{index}")
        if proof is None:
            raise CaptureConflict("a recurring confirmation proof is missing")
        result = await create_confirmed_series(
            CreateConfirmedSeries(
                action_key=f"{action_key}:r{index}",
                expected_preferences_version=preferences.version,
                draft=candidate.recurring,
                proof=RecurrenceConfirmationProof(
                    UUID(str(proof["preview_id"])), draft.version, credential
                ),
            ),
            authenticated_owner_id=owner_id,
            store=recurrence_store,
            identity=identity,
            billing=billing,
            confirmation_verifier=recurrence_confirmation_verifier,
            clock=clock,
        )
        results.append(result)
    result_payload: dict[str, object] = {"items": results}
    from pin.contexts.conversation.domain.capture import confirm_capture

    confirmed = confirm_capture(
        draft, expected_version=expected_version, result=result_payload, at=clock.now()
    )
    await store.revoke_proofs(owner_id=owner_id, draft_id=draft_id, at=clock.now())
    await store.persist_draft(confirmed, expected_version=expected_version)
    return CaptureTurnReply(
        draft.id,
        confirmed.version,
        confirmed.state.value,
        {"kind": "confirmed", "result": result_payload},
    )


async def cancel_draft(
    store: CaptureStore,
    *,
    owner_id: UUID,
    draft_id: UUID,
    expected_version: int,
    at: datetime,
) -> CaptureTurnReply:
    """Cancel an unsaved draft with a version-fenced deterministic interaction."""
    from pin.contexts.conversation.domain.capture import cancel_capture

    draft = await store.get_draft(owner_id=owner_id, draft_id=draft_id)
    if draft is None:
        raise CaptureConflict("capture draft does not exist")
    if draft.state.value == "cancelled":
        return CaptureTurnReply(draft.id, draft.version, draft.state.value, {"kind": "cancelled"})
    cancelled = cancel_capture(draft, expected_version=expected_version, at=at)
    await store.revoke_proofs(owner_id=owner_id, draft_id=draft_id, at=at)
    await store.persist_draft(cancelled, expected_version=expected_version)
    return CaptureTurnReply(
        draft.id, cancelled.version, cancelled.state.value, {"kind": "cancelled"}
    )


async def apply_clarification_choice(
    store: CaptureStore,
    *,
    owner_id: UUID,
    draft_id: UUID,
    expected_version: int,
    choice_id: str,
    preferences: PreferencesSnapshot,
    at: datetime,
) -> CaptureTurnReply:
    """Apply one currently offered deterministic choice and stop at an unsaved preview."""
    draft = await store.get_draft(owner_id=owner_id, draft_id=draft_id)
    if draft is None or draft.owner_id != owner_id:
        raise CaptureConflict("capture draft does not exist")
    if (
        draft.version != expected_version
        or draft.state.value != "collecting"
        or draft.owner_id != preferences.owner_id
    ):
        raise CaptureConflict("capture clarification is stale")
    actions = tuple(_parse_action(item) for item in draft.normalized_actions)
    candidates, question = _candidates(actions, preferences, at)
    if not isinstance(question, CaptureClarification):
        raise CaptureConflict("capture draft has no current button clarification")
    if choice_id not in {choice.id for choice in question.choices}:
        raise CaptureConflict("choice is not offered by the current draft")

    request_key = f"choice-{uuid4().hex}"
    processing = begin_clarification_choice(
        draft,
        expected_version=expected_version,
        request_key=request_key,
        at=at,
    )
    parts = choice_id.split(":")
    action_index = int(parts[1])
    action = actions[action_index]
    schedule = action.fields.schedule
    updated_schedule: PlanSchedule
    is_time_entry = choice_id.endswith(":ask_time")
    if is_time_entry or choice_id.endswith(":ask_duration"):
        if is_time_entry:
            prompt = (
                "Send the exact local time. I will show a preview before saving."
                if action.language.value == "en"
                else "Укажите точное местное время. Перед сохранением я покажу план."
            )
        else:
            prompt = (
                "Send the exact duration or local end time."
                if action.language.value == "en"
                else "Укажите точную длительность или местное время окончания."
            )
        collecting = finish_collecting(
            processing,
            request_key=request_key,
            reply=prompt,
            at=at,
            actions=tuple(_action_json(item) for item in actions),
        )
        await store.persist_draft(collecting, expected_version=expected_version)
        response = _reply_payload(prompt, state="collecting")
        await store.revoke_proofs(owner_id=owner_id, draft_id=draft_id, at=at)
        return CaptureTurnReply(draft.id, collecting.version, collecting.state.value, response)

    if choice_id.endswith(":preferred_time"):
        if not isinstance(schedule, TaskSchedule) or schedule.local_date is None:
            raise CaptureConflict("task time suggestion no longer applies")
        updated_schedule = schedule.model_copy(
            update={
                "timing": "timed",
                "local_time": preferences.review_time,
                "timezone": preferences.home_zone,
            }
        )
    elif choice_id.endswith(":untimed"):
        if not isinstance(schedule, TaskSchedule) or schedule.local_date is None:
            raise CaptureConflict("untimed choice no longer applies")
        updated_schedule = schedule.model_copy(
            update={"timing": "untimed", "local_time": None, "timezone": None, "fold": None}
        )
    else:
        duration = int(parts[-1])
        if not isinstance(schedule, AppointmentSchedule) or duration not in {30, 60}:
            raise CaptureConflict("appointment duration choice no longer applies")
        updated_schedule = schedule.model_copy(update={"duration_minutes": duration})
    updated_action = action.model_copy(
        update={"fields": action.fields.model_copy(update={"schedule": updated_schedule})}
    )
    actions = (*actions[:action_index], updated_action, *actions[action_index + 1 :])
    candidates, question = _candidates(actions, preferences, at)
    if question is not None:
        reply = question.text if isinstance(question, CaptureClarification) else question
        collecting = finish_collecting(
            processing,
            request_key=request_key,
            reply=reply,
            at=at,
            actions=tuple(_action_json(item) for item in actions),
        )
        await store.revoke_proofs(owner_id=owner_id, draft_id=draft_id, at=at)
        await store.persist_draft(collecting, expected_version=expected_version)
        response = _reply_payload(reply, state="collecting")
        if isinstance(question, CaptureClarification) and question.choices:
            response["choices"] = [
                {"id": choice.id, "label": choice.label} for choice in question.choices
            ]
        return CaptureTurnReply(draft.id, collecting.version, collecting.state.value, response)
    if candidates is None:
        raise CaptureConflict("clarification did not resolve into a preview")

    preview, proof_parts = _preview(owner_id, candidates, preferences, at)
    ready = finish_ready(
        processing,
        request_key=request_key,
        actions=tuple(_action_json(item.action) for item in candidates),
        preview=preview,
        projection_sha256=cast(str, preview["projection_sha256"]),
        reply=_confirmation_text(action.language.value),
        at=at,
    )
    await store.revoke_proofs(owner_id=owner_id, draft_id=draft_id, at=at)
    credential = await store.issue_proofs(
        owner_id=owner_id,
        draft_id=draft_id,
        preview_version=ready.version,
        parts=proof_parts,
        issued_at=at,
        expires_at=min(draft.expires_at, at + CAPTURE_TTL),
    )
    preview["proofs"] = [
        {
            "key": part.key,
            "preview_id": str(part.preview_id),
            "projection_sha256": part.projection_sha256,
        }
        for part in proof_parts
    ]
    preview["preferences_version"] = preferences.version
    await store.persist_draft(ready, expected_version=expected_version)
    response = {
        "kind": "preview",
        "text": _confirmation_text(action.language.value),
        "preview": preview,
    }
    return CaptureTurnReply(draft.id, ready.version, ready.state.value, response, credential)


async def _collect(
    store: CaptureStore,
    interpretation: CaptureInterpretation,
    reply: str,
    at: datetime,
    *,
    actions: tuple[dict[str, object], ...] | None = None,
    choices: tuple[CaptureClarificationChoice, ...] = (),
) -> CaptureTurnReply:
    draft = interpretation.draft
    collecting = finish_collecting(
        draft,
        request_key=interpretation.request_key,
        reply=reply,
        at=at,
        actions=actions,
    )
    await store.persist_draft(collecting, expected_version=draft.version)
    response = _reply_payload(reply, state="collecting")
    if choices:
        response["choices"] = [{"id": choice.id, "label": choice.label} for choice in choices]
    await store.complete_input(
        owner_id=draft.owner_id,
        input_key=interpretation.request_key,
        state="complete",
        response=response,
        at=at,
    )
    return CaptureTurnReply(draft.id, collecting.version, collecting.state.value, response)


def _candidates(
    actions: tuple[ActionProposal, ...], preferences: PreferencesSnapshot, at: datetime
) -> tuple[list[_Candidate] | None, str | CaptureClarification | None]:
    output: list[_Candidate] = []
    for index, action in enumerate(actions):
        fields = action.fields
        schedule = fields.schedule
        if schedule is None:
            return None, _ask(
                "What date and time should I use?",
                "На какую дату и время запланировать?",
                action.language.value,
            )
        if isinstance(schedule, TaskSchedule) and schedule.local_date is None:
            return None, _ask(
                "What date should I use?", "На какую дату запланировать?", action.language.value
            )
        if isinstance(schedule, TaskSchedule) and schedule.timing == "unspecified":
            review_time = preferences.review_time.strftime("%H:%M")
            choices: list[CaptureClarificationChoice] = []
            if _review_time_is_future(schedule, fields.title or "", preferences, at):
                choices.append(
                    CaptureClarificationChoice(
                        f"task:{index}:preferred_time",
                        _ask(
                            f"Suggestion: use your {review_time} review time",
                            f"Предложение: использовать время обзора {review_time}",
                            action.language.value,
                        ),
                    )
                )
            choices.extend(
                (
                    CaptureClarificationChoice(
                        f"task:{index}:untimed",
                        _ask("Leave untimed", "Оставить без времени", action.language.value),
                    ),
                    CaptureClarificationChoice(
                        f"task:{index}:ask_time",
                        _ask("Choose another time", "Указать другое время", action.language.value),
                    ),
                )
            )
            return None, CaptureClarification(
                _ask(
                    "Should this be timed or an untimed task?",
                    "Указать время или оставить задачу без времени?",
                    action.language.value,
                ),
                tuple(choices),
            )
        if (
            isinstance(schedule, TaskSchedule)
            and schedule.timing == "timed"
            and schedule.timezone is None
        ):
            return None, _ask(
                "What time zone should I use?",
                "Какой часовой пояс использовать?",
                action.language.value,
            )
        if (
            isinstance(schedule, AppointmentSchedule)
            and schedule.duration_minutes is None
            and schedule.local_end is None
        ):
            return None, CaptureClarification(
                _ask(
                    "How long is the appointment, or when does it end?",
                    "Сколько длится встреча или когда она заканчивается?",
                    action.language.value,
                ),
                (
                    CaptureClarificationChoice(
                        f"appointment:{index}:duration:30",
                        _ask("30 minutes", "30 минут", action.language.value),
                    ),
                    CaptureClarificationChoice(
                        f"appointment:{index}:duration:60",
                        _ask("1 hour", "1 час", action.language.value),
                    ),
                    CaptureClarificationChoice(
                        f"appointment:{index}:ask_duration",
                        _ask(
                            "Enter exact duration or end time",
                            "Указать точную длительность или время окончания",
                            action.language.value,
                        ),
                    ),
                ),
            )
        timezone = getattr(schedule, "timezone", None)
        if not timezone:
            # Tasks without time have no zone-sensitive instant; use the profile's
            # explicit zone only for date validation, never infer it from language.
            timezone = preferences.home_zone
        reminders = fields.reminders
        explicit_intents = tuple(
            item for item in reminders or () if isinstance(item, ExplicitReminder)
        )
        if explicit_intents and fields.recurrence is not None:
            return None, _ask(
                "Recurring reminders need a relative lead time for each occurrence.",
                "Для повторяющихся событий укажите относительный интервал напоминания.",
                action.language.value,
            )
        kind = (
            PlanKind.APPOINTMENT
            if isinstance(schedule, AppointmentSchedule)
            else PlanKind.DEADLINE
            if isinstance(schedule, DeadlineSchedule)
            else PlanKind.UNTIMED
            if schedule.timing == "untimed"
            else PlanKind.TASK
        )
        if kind is PlanKind.UNTIMED and reminders:
            return None, _ask(
                "A reminder needs a timed plan. Add a time or remove the reminder?",
                "Для напоминания нужен план со временем. Указать время или убрать напоминание?",
                action.language.value,
            )
        if isinstance(schedule, AppointmentSchedule):
            start = schedule.local_start
            plan = OneOffPlanDraft(
                kind,
                fields.title or "",
                start.date(),
                start.time(),
                timezone,
                end_local_date=schedule.local_end.date() if schedule.local_end else None,
                end_local_time=schedule.local_end.time() if schedule.local_end else None,
                duration_seconds=schedule.duration_minutes * 60
                if schedule.duration_minutes
                else None,
                fold=schedule.fold,
                end_fold=schedule.end_fold,
                reminders=None,
            )
        elif isinstance(schedule, DeadlineSchedule):
            if schedule.local_time is None:
                return None, _ask(
                    "What time is the deadline?",
                    "В какое время наступает срок?",
                    action.language.value,
                )
            plan = OneOffPlanDraft(
                kind,
                fields.title or "",
                schedule.local_date,
                schedule.local_time,
                timezone,
                fold=schedule.fold,
                reminders=None,
            )
        else:
            assert isinstance(schedule, TaskSchedule)
            assert schedule.local_date is not None
            plan = OneOffPlanDraft(
                kind,
                fields.title or "",
                schedule.local_date,
                schedule.local_time,
                timezone,
                fold=schedule.fold,
                reminders=None,
            )
        resolved_explicit: tuple[ReminderOffset, ...] = ()
        if explicit_intents:
            try:
                base = preview_plan_batch(
                    preferences.owner_id,
                    (plan,),
                    expected_preferences_version=preferences.version,
                    default_zone=preferences.home_zone,
                    default_reminders=(),
                    quiet_zone=preferences.home_zone,
                    quiet_start=preferences.quiet_start,
                    quiet_end=preferences.quiet_end,
                    at=at,
                )
                anchor = base.plans[0].occurrence.schedule.anchor_at
                if anchor is None:
                    return None, _ask(
                        "What event time should this reminder be based on?",
                        "Для какого времени события настроить напоминание?",
                        action.language.value,
                    )
                resolved_explicit = tuple(
                    _explicit_reminder_offset(intent, anchor) for intent in explicit_intents
                )
            except ValueError as exc:
                return None, _planning_clarification(str(exc), action.language.value)
        relative_intents = (
            tuple(item for item in reminders if isinstance(item, RelativeReminder))
            if reminders is not None
            else None
        )
        offsets, error = _effective_reminders(
            relative_intents,
            fields.reminder_mode if reminders is not None else None,
            preferences,
            additional=resolved_explicit,
        )
        if error:
            return None, error
        plan = replace(plan, reminders=offsets)
        recurrence = _recurrence(fields.recurrence) if fields.recurrence else None
        if recurrence is None:
            output.append(_Candidate(action, one_off=plan))
        else:
            output.append(
                _Candidate(
                    action,
                    recurring=RecurringPlanDraft(
                        plan.kind,
                        plan.title,
                        plan.local_date,
                        plan.local_time,
                        plan.event_zone,
                        recurrence,
                        plan.duration_seconds,
                        plan.end_local_time,
                        plan.end_local_date,
                        plan.fold,
                        plan.end_fold,
                        plan.reminders,
                    ),
                )
            )
    return output, None


def _effective_reminders(
    intents: tuple[RelativeReminder, ...] | None,
    mode: str | None,
    preferences: PreferencesSnapshot,
    *,
    additional: tuple[ReminderOffset, ...] = (),
) -> tuple[tuple[ReminderOffset, ...] | None, str | None]:
    if intents is None and not additional:
        if mode is not None:
            return None, "Reminder mode requires a reminder list."
        return None, None
    if not intents and not additional:
        return (), None
    defaults = _defaults(preferences)
    explicit: list[ReminderOffset] = []
    kinds: set[OffsetKind] = set()
    for intent in intents or ():
        if not isinstance(intent, RelativeReminder):
            return None, "An absolute reminder needs clarification."
        kind = (
            OffsetKind.CALENDAR_DAYS
            if intent.basis == "calendar_day"
            else OffsetKind.ELAPSED_SECONDS
        )
        amount = (
            intent.amount
            if intent.unit == "day"
            else intent.amount * (3600 if intent.unit == "hour" else 60)
        )
        explicit.append(ReminderOffset(kind, amount, "explicit"))
        kinds.add(kind)
    explicit.extend(additional)
    kinds.update(item.kind for item in additional)
    if mode == "only":
        effective = explicit
    else:
        effective = [item for item in defaults if item.kind not in kinds]
        effective.extend(explicit)
    deduped: dict[tuple[OffsetKind, int], ReminderOffset] = {}
    for offset in effective:
        deduped[(offset.kind, offset.amount)] = offset
    return tuple(deduped.values()), None


def _explicit_reminder_offset(intent: ExplicitReminder, anchor: datetime) -> ReminderOffset:
    zone = ZoneInfo(intent.timezone)
    valid: list[datetime] = []
    for fold in (0, 1):
        candidate = intent.local_at.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(UTC).astimezone(zone)
        if round_trip.replace(tzinfo=None) == intent.local_at and all(
            candidate.utcoffset() != prior.utcoffset() for prior in valid
        ):
            valid.append(candidate)
    if not valid:
        raise ValueError("explicit reminder time does not exist in its timezone")
    if len(valid) > 1:
        raise ValueError("explicit reminder time is ambiguous in its timezone")
    seconds = int((anchor.astimezone(UTC) - valid[0].astimezone(UTC)).total_seconds())
    if seconds <= 0:
        raise ValueError("explicit reminder must be before the event")
    return ReminderOffset(OffsetKind.ELAPSED_SECONDS, seconds, "explicit")


def _preview(
    owner_id: UUID, candidates: list[_Candidate], preferences: PreferencesSnapshot, at: datetime
) -> tuple[dict[str, object], tuple[CaptureProofPart, ...]]:
    one_offs = tuple(item.one_off for item in candidates if item.one_off is not None)
    one_off_preview = None
    if one_offs:
        one_off_preview = preview_plan_batch(
            owner_id,
            one_offs,
            expected_preferences_version=preferences.version,
            default_zone=preferences.home_zone,
            default_reminders=_defaults(preferences),
            quiet_zone=preferences.home_zone,
            quiet_start=preferences.quiet_start,
            quiet_end=preferences.quiet_end,
            at=at,
        )
    parts: list[CaptureProofPart] = []
    serialized: list[dict[str, object]] = []
    item_no = 0
    for index, candidate in enumerate(candidates):
        if candidate.one_off:
            assert one_off_preview is not None
            plan = one_off_preview.plans[item_no]
            item_no += 1
            serialized.append({"kind": "plan", "details": _plan_json(plan)})
        else:
            assert candidate.recurring is not None
            projection = preview_recurring_series(
                owner_id,
                candidate.recurring,
                expected_preferences_version=preferences.version,
                default_zone=preferences.home_zone,
                default_reminders=_defaults(preferences),
                quiet_zone=preferences.home_zone,
                quiet_start=preferences.quiet_start,
                quiet_end=preferences.quiet_end,
                at=at,
            )
            part_id = uuid4()
            parts.append(
                CaptureProofPart(f"r{index}", part_id, projection.approval_projection_sha256)
            )
            recurrence_details = _recurrence_json(projection)
            recurrence_details.update(
                {
                    "title": candidate.recurring.title,
                    "kind": candidate.recurring.kind.value,
                    "event_zone": candidate.recurring.event_zone or preferences.home_zone,
                }
            )
            serialized.append({"kind": "recurring", "details": recurrence_details})
    if one_off_preview is not None:
        parts.insert(
            0,
            CaptureProofPart("one_off", uuid4(), one_off_preview.approval_projection_sha256),
        )
    digest_data = {
        "preferences_version": preferences.version,
        "one_off": one_off_preview.approval_projection_sha256 if one_off_preview else None,
        "recurring": [part.projection_sha256 for part in parts if part.key != "one_off"],
        "items": serialized,
    }
    digest = hashlib.sha256(
        json.dumps(digest_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ({"items": serialized, "projection_sha256": digest}, tuple(parts))


def _plan_json(plan: ProposedPlan) -> dict[str, object]:
    schedule = plan.occurrence.schedule
    return {
        "title": plan.item.title,
        "kind": plan.item.kind.value,
        "event_zone": schedule.event_zone,
        "local_date": schedule.local_date.isoformat(),
        "local_time": schedule.local_time.isoformat() if schedule.local_time else None,
        "starts_at": _iso(schedule.starts_at),
        "ends_at": _iso(schedule.ends_at),
        "deadline_at": _iso(schedule.deadline_at),
        "duration_seconds": schedule.duration_seconds,
        "reminders": [
            {
                "offset_kind": item.offset.kind.value,
                "amount": item.offset.amount,
                "source": item.offset.source,
                "due_at": item.due_at.isoformat(),
            }
            for item in plan.reminders
        ],
        "omitted_reminders": [
            {
                "offset_kind": item.offset.kind.value,
                "amount": item.offset.amount,
                "reason": item.reason,
            }
            for item in plan.omitted_reminders
        ],
        "notices": [
            {
                "kind": notice.kind,
                "original_due_at": notice.original_due_at.isoformat(),
                "due_at": notice.due_at.isoformat(),
            }
            for notice in plan.reminder_notices
        ],
    }


def _recurrence_json(preview: RecurrencePreview) -> dict[str, object]:
    return {
        "through": preview.through.isoformat(),
        "truncated": preview.projection.truncated,
        "occurrences": [
            {
                "sequence": item.sequence,
                "local_date": item.schedule.local_date.isoformat(),
                "starts_at": _iso(item.schedule.starts_at),
                "deadline_at": _iso(item.schedule.deadline_at),
                "dst_gap_shift_seconds": item.dst_gap_shift_seconds,
                "repeated_time_choice": item.repeated_time_choice,
                "reminders": [
                    {
                        "offset_kind": offset.kind.value,
                        "amount": offset.amount,
                        "source": offset.source,
                        "due_at": due.isoformat(),
                    }
                    for offset, due in item.reminders
                ],
                "omitted_reminders": [
                    {
                        "offset_kind": item.offset.kind.value,
                        "amount": item.offset.amount,
                        "reason": item.reason,
                    }
                    for item in item.omitted_reminders
                ],
                "notices": [
                    {
                        "kind": notice.kind,
                        "original_due_at": notice.original_due_at.isoformat(),
                        "due_at": notice.due_at.isoformat(),
                    }
                    for notice in item.reminder_notices
                ],
            }
            for item in preview.projection.occurrences
        ],
    }


def _recurrence(spec: RecurrenceSpec) -> RecurrenceRule:
    return RecurrenceRule(
        RecurrenceFrequency(spec.frequency),
        spec.interval,
        spec.weekdays,
        spec.month_day,
        spec.last_day,
        spec.occurrence_count,
        spec.ends_on,
    )


def _defaults(preferences: PreferencesSnapshot) -> tuple[ReminderOffset, ...]:
    return tuple(
        item
        for item in (
            ReminderOffset(OffsetKind.CALENDAR_DAYS, preferences.reminder_calendar_days, "default")
            if preferences.reminder_calendar_days
            else None,
            ReminderOffset(
                OffsetKind.ELAPSED_SECONDS, preferences.reminder_elapsed_hours * 3600, "default"
            )
            if preferences.reminder_elapsed_hours
            else None,
        )
        if item is not None
    )


def _action_json(action: ActionProposal) -> dict[str, object]:
    # Preserve omission versus an explicitly supplied empty reminder list across
    # persisted-draft restart. Defaults materialized as null here would appear in
    # `model_fields_set` after reparsing and invalidate create reminder modes.
    return action.model_dump(mode="json", exclude_unset=True)


def _parse_action(value: dict[str, object]) -> ActionProposal:
    return ActionProposal.model_validate_json(json.dumps(value))


def _review_time_is_future(
    schedule: TaskSchedule,
    title: str,
    preferences: PreferencesSnapshot,
    at: datetime,
) -> bool:
    if schedule.local_date is None:
        return False
    candidate = OneOffPlanDraft(
        PlanKind.TASK,
        title,
        schedule.local_date,
        preferences.review_time,
        preferences.home_zone,
    )
    try:
        projection = preview_plan_batch(
            preferences.owner_id,
            (candidate,),
            expected_preferences_version=preferences.version,
            default_zone=preferences.home_zone,
            default_reminders=(),
            quiet_zone=preferences.home_zone,
            quiet_start=preferences.quiet_start,
            quiet_end=preferences.quiet_end,
            at=at,
        )
    except (ValueError, OverflowError):
        return False
    starts_at = projection.plans[0].occurrence.schedule.starts_at
    return starts_at is not None and starts_at > at


def _reply_payload(text: str, *, state: str) -> dict[str, object]:
    return {"kind": "reply", "state": state, "text": text}


def _ask(english: str, russian: str, language: str) -> str:
    return english if language == "en" else russian


def _confirmation_text(language: str) -> str:
    return (
        "Review the full preview, then confirm to save."
        if language == "en"
        else "Проверьте предварительный просмотр и подтвердите сохранение."
    )


def _failure_reply(category: str, language: str) -> str:
    english = {
        "access_denied": "Plan creation is not available for this account right now.",
        "budget_exhausted": (
            "I could not process the full request within the safe limit. Please split it."
        ),
        "usage_unknown": (
            "The provider result is still being checked; retrying would risk a duplicate request."
        ),
        "stale_job": "This request expired while it was processing. Please send it again.",
    }.get(category, "I could not safely prepare a preview. Please try again.")
    russian = {
        "access_denied": "Сейчас создание планов недоступно для этого аккаунта.",
        "budget_exhausted": (
            "Запрос не удалось безопасно обработать целиком. Разделите его на части."
        ),
        "usage_unknown": "Результат провайдера ещё проверяется; повтор может создать дубликат.",
        "stale_job": "Срок обработки запроса истёк. Отправьте его ещё раз.",
    }.get(
        category, "Не удалось безопасно подготовить предварительный просмотр. Попробуйте ещё раз."
    )
    return english if language == "en" else russian


def _planning_clarification(message: str, language: str) -> str:
    # Planning's precise semantic error is retained in logs, while the user gets
    # a safe request to resolve the local schedule.
    if "past" in message.lower():
        return (
            "That time has passed. What future date and time should I use?"
            if language == "en"
            else "Это время уже прошло. Какую будущую дату и время указать?"
        )
    return (
        "I need one more detail about the local date or time before I can show a preview."
        if language == "en"
        else "Уточните местную дату или время, чтобы я показал предварительный просмотр."
    )


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None
