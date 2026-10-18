"""Transport-neutral Telegram runtime for owner-confirmed plan mutations."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pin.adapters.telegram.mutation_runtime_store import (
    MutationRuntimePendingRecord,
    SqlAlchemyMutationRuntimeStore,
)
from pin.adapters.telegram.mutations import (
    MUTATION_CALLBACK_PREFIX,
    SqlAlchemyTelegramMutationVerifier,
    render_confirmable_mutation,
)
from pin.adapters.telegram.presentation.callbacks import CallbackTokens, InvalidCallbackToken
from pin.adapters.telegram.presentation.tokens import (
    SqlAlchemyCallbackTokenStore,
    TelegramCallbackTokenRecord,
)
from pin.contexts.billing.infrastructure.store import SqlAlchemyAccessUsageStore
from pin.contexts.conversation.application.dto import (
    ActionProposal,
    AppointmentSchedule,
    DeadlineSchedule,
    ExplicitReminder,
    RelativeReminder,
    TaskSchedule,
)
from pin.contexts.conversation.domain.proposals import ProposalOperation
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.planning.domain.mutations import (
    MutationAction,
    MutationRequest,
    MutationScope,
)
from pin.contexts.planning.domain.plans import (
    OccurrenceState,
    OffsetKind,
    OneOffPlanDraft,
    PlanKind,
    ReminderOffset,
)
from pin.contexts.planning.mutation_read_api import (
    MutationTargetCursor,
    MutationTargetSummary,
    get_owner_mutation_target,
    get_owner_mutation_target_page,
    get_owner_mutation_targets,
)
from pin.contexts.planning.mutations_api import (
    MutationAccessDenied,
    MutationProof,
    MutationRejected,
    MutationStalePreview,
    SqlAlchemyMutationStore,
    confirm_mutation,
    preview_mutation,
)

MUTATION_MENU_PAGE_SIZE = 10
_CALLBACK_TTL = timedelta(minutes=15)
_PENDING_TTL = timedelta(hours=24)
_ACTION_PATTERN = re.compile(r"^/(done|cancel|reschedule)(?:\s+(.*))?$", re.IGNORECASE)
_SCHEDULE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}))?$")


@dataclass(frozen=True, slots=True)
class MutationMessage:
    text: str
    keyboard: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class _Clock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


def is_mutation_callback(data: str | None) -> bool:
    return bool(data and data.startswith(MUTATION_CALLBACK_PREFIX))


async def process_mutation_command(
    session: AsyncSession,
    *,
    owner_id: UUID,
    text: str,
    language: str,
    now: datetime,
) -> MutationMessage | None:
    """Handle deterministic slash commands or a pending reschedule date reply."""
    _require_aware(now)
    lang = _language(language)
    runtime = SqlAlchemyMutationRuntimeStore(session)

    command = _ACTION_PATTERN.fullmatch(text.strip())
    if command is None:
        pending = await runtime.get_latest_schedule(owner_id=owner_id, at=now, lock=True)
        if pending is None:
            return None
        return await _continue_reschedule(session, pending, text.strip(), lang, now)

    await _invalidate_owner_flow(session, owner_id, now)

    action = {
        "done": MutationAction.COMPLETE,
        "cancel": MutationAction.CANCEL,
        "reschedule": MutationAction.RESCHEDULE,
    }[command.group(1).lower()]
    argument = (command.group(2) or "").strip()
    if not argument:
        return await _target_menu(session, owner_id, action, lang, now)

    try:
        pieces = argument.split()
        occurrence_id = UUID(pieces[0])
    except (ValueError, IndexError):
        return await _target_menu(session, owner_id, action, lang, now)
    if action is not MutationAction.RESCHEDULE and len(pieces) > 1:
        return MutationMessage(
            _msg(
                lang,
                "Используйте кнопки списка для этого действия.",
                "Use the task list buttons for this action.",
            )
        )
    target = await _owned_target(session, owner_id, occurrence_id, now)
    if target is None:
        return MutationMessage(
            _msg(
                lang,
                "Эта задача недоступна или уже изменилась.",
                "That task is unavailable or has changed.",
            )
        )

    if action is MutationAction.RESCHEDULE:
        schedule_text = " ".join(pieces[1:])
        if schedule_text:
            parsed = _parse_schedule_text(schedule_text, target, lang)
            if isinstance(parsed, MutationMessage):
                return parsed
            fields: dict[str, object] = {"draft": _draft_json(parsed)}
        else:
            fields = {}
        return await _start_or_preview(
            session,
            owner_id=owner_id,
            target=target,
            action=action,
            scope=None,
            language=lang,
            now=now,
            pending_fields=fields,
        )

    return await _start_or_preview(
        session,
        owner_id=owner_id,
        target=target,
        action=action,
        scope=None,
        language=lang,
        now=now,
        pending_fields={},
    )


async def prepare_proposed_mutation(
    session: AsyncSession,
    *,
    owner_id: UUID,
    proposal: ActionProposal,
    language: str,
    now: datetime,
) -> MutationMessage:
    """Validate an AI proposal against the current owner snapshot, then preview."""
    _require_aware(now)
    lang = _language(language)
    await _invalidate_owner_flow(session, owner_id, now)
    if proposal.operation is ProposalOperation.CREATE_PLAN:
        return MutationMessage(
            _msg(
                lang,
                "Новую задачу нужно подтвердить через обычный просмотр задачи.",
                "New plans must use the regular plan preview.",
            ),
        )
    if proposal.target_id is None or proposal.expected_version is None:
        return MutationMessage(
            _msg(
                lang,
                "Не удалось определить актуальную задачу.",
                "I could not identify the current task.",
            )
        )

    targets = await get_owner_mutation_targets(session, owner_id=owner_id, at=now)
    target: MutationTargetSummary | None
    if proposal.operation is ProposalOperation.CHANGE_SERIES:
        candidates = tuple(
            item
            for item in targets
            if item.series_id == proposal.target_id
            and item.series_version == proposal.expected_version
        )
        if not candidates:
            return MutationMessage(
                _msg(
                    lang,
                    "Серия изменилась или недоступна.",
                    "That series changed or is unavailable.",
                )
            )
        if len(candidates) > 1:
            pending = await SqlAlchemyMutationRuntimeStore(session).start(
                owner_id=owner_id,
                occurrence_id=candidates[0].occurrence_id,
                expected_occurrence_version=candidates[0].occurrence_version,
                expected_series_version=proposal.expected_version,
                action=MutationAction.RESCHEDULE.value,
                scope=None,
                state="awaiting_anchor",
                language=lang,
                pending_fields={
                    "proposal": proposal.model_dump(mode="json", exclude_unset=True),
                    "anchor_targets": [
                        {
                            "occurrence_id": str(item.occurrence_id),
                            "occurrence_version": item.occurrence_version,
                        }
                        for item in candidates
                    ],
                },
                expires_at=now.astimezone(UTC) + _PENDING_TTL,
            )
            return await _series_anchor_menu(session, pending, candidates, lang, now)
        target = candidates[0]
    else:
        target = next(
            (
                item
                for item in targets
                if item.occurrence_id == proposal.target_id
                and item.occurrence_version == proposal.expected_version
            ),
            None,
        )
    if target is None or target.owner_id != owner_id:
        return MutationMessage(
            _msg(
                lang,
                "Эта задача изменилась или недоступна.",
                "That task changed or is unavailable.",
            )
        )

    action = {
        ProposalOperation.COMPLETE: MutationAction.COMPLETE,
        ProposalOperation.CANCEL: MutationAction.CANCEL,
        ProposalOperation.CHANGE_OCCURRENCE: MutationAction.RESCHEDULE,
        ProposalOperation.CHANGE_SERIES: MutationAction.RESCHEDULE,
    }[proposal.operation]
    replacement: OneOffPlanDraft | None = None
    if action is MutationAction.RESCHEDULE:
        draft_result = _draft_from_proposal(proposal, target, lang)
        if isinstance(draft_result, MutationMessage):
            return draft_result
        replacement = draft_result
        pending_fields: dict[str, object] = {
            "draft": _draft_json(draft_result),
            "proposal": proposal.model_dump(mode="json", exclude_unset=True),
        }
    else:
        pending_fields = {"proposal": proposal.model_dump(mode="json", exclude_unset=True)}

    requires_scope = target.series_id is not None and action in {
        MutationAction.CANCEL,
        MutationAction.RESCHEDULE,
    }
    if requires_scope:
        pending = await SqlAlchemyMutationRuntimeStore(session).start(
            owner_id=owner_id,
            occurrence_id=target.occurrence_id,
            expected_occurrence_version=target.occurrence_version,
            expected_series_version=target.series_version,
            action=action.value,
            scope=None,
            state="awaiting_scope",
            language=lang,
            pending_fields=pending_fields,
            expires_at=now.astimezone(UTC) + _PENDING_TTL,
        )
        return await _scope_menu(session, pending, lang, now)

    return await _preview_request(
        session,
        owner_id=owner_id,
        target=target,
        action=action,
        scope=MutationScope.THIS_OCCURRENCE,
        replacement=replacement,
        language=lang,
        now=now,
    )


async def process_mutation_callback(
    session: AsyncSession,
    *,
    owner_id: UUID,
    data: str,
    language: str,
    now: datetime,
) -> MutationMessage:
    """Consume one private opaque mutation token and continue its owner-bound flow."""
    _require_aware(now)
    lang = _language(language)
    if not is_mutation_callback(data) or len(data.encode("utf-8")) > 64:
        return MutationMessage(
            _msg(
                lang,
                "Эта кнопка устарела. Выберите действие заново.",
                "That button is invalid or expired. Choose the action again.",
            )
        )
    token = data.removeprefix(MUTATION_CALLBACK_PREFIX)
    binding = await _binding_for_token(session, owner_id, token)
    if binding is None:
        return MutationMessage(
            _msg(
                lang,
                "Эта кнопка устарела. Выберите действие заново.",
                "That button is invalid or expired. Choose the action again.",
            )
        )

    if binding.action == "mutation.confirm":
        try:
            preview_id = UUID(binding.target_id)
        except ValueError:
            return MutationMessage(
                _msg(lang, "Предпросмотр устарел.", "The preview is no longer available.")
            )
        runtime = SqlAlchemyMutationRuntimeStore(session)
        pending = await runtime.get_preview(owner_id=owner_id, preview_id=preview_id, lock=True)
        if (
            pending is None
            or pending.expires_at <= now
            or type(pending.pending_fields.get("preview_version")) is not int
            or pending.pending_fields.get("preview_version") != binding.target_version
        ):
            return MutationMessage(
                _msg(
                    lang,
                    "Предпросмотр устарел. Создайте запрос заново.",
                    "That preview is stale. Start again.",
                )
            )
        try:
            async with session.begin_nested():
                result = await confirm_mutation(
                    MutationProof(
                        preview_id=preview_id, preview_version=binding.target_version, token=token
                    ),
                    authenticated_owner_id=owner_id,
                    store=SqlAlchemyMutationStore(session),
                    identity=SqlAlchemyIdentityStore(session),
                    billing=SqlAlchemyAccessUsageStore(session),
                    confirmation_verifier=SqlAlchemyTelegramMutationVerifier(session),
                    clock=_Clock(now),
                )
        except (MutationStalePreview, MutationRejected, MutationAccessDenied, ValueError):
            await runtime.update(pending, state="stale")
            return MutationMessage(
                _msg(
                    lang,
                    "Задача изменилась или доступ истёк. Ничего не изменено.",
                    "The task changed or access expired. Nothing was changed.",
                )
            )
        await runtime.update(pending, state="confirmed")
        action = cast(str, result.get("action", ""))
        return MutationMessage(_confirmed_text(lang, action))

    consumed = await _consume_bound_token(session, owner_id, token, binding, now)
    if consumed is None:
        return MutationMessage(
            _msg(
                lang,
                "Эта кнопка уже использована или устарела.",
                "That button was already used or is stale.",
            )
        )
    if binding.action == "mutation.dismiss":
        dismissed_preview_id: UUID | None
        try:
            dismissed_preview_id = UUID(binding.target_id)
        except ValueError:
            dismissed_preview_id = None
        if dismissed_preview_id is not None:
            pending = await SqlAlchemyMutationRuntimeStore(session).get_preview(
                owner_id=owner_id, preview_id=dismissed_preview_id, lock=True
            )
            if pending is not None:
                await SqlAlchemyMutationRuntimeStore(session).update(pending, state="dismissed")
            await session.execute(
                update(TelegramCallbackTokenRecord)
                .where(
                    TelegramCallbackTokenRecord.owner_id == owner_id,
                    TelegramCallbackTokenRecord.target_id == binding.target_id,
                    TelegramCallbackTokenRecord.action.in_(
                        ("mutation.confirm", "mutation.dismiss")
                    ),
                    TelegramCallbackTokenRecord.consumed_at.is_(None),
                )
                .values(consumed_at=now)
            )
        return MutationMessage(
            _msg(
                lang,
                "Предпросмотр отменён. Задача не изменена.",
                "Preview dismissed. The task was not changed.",
            )
        )
    if binding.action.startswith("mutation.select."):
        return await _select_target(
            session,
            binding.target_id,
            binding.action.removeprefix("mutation.select."),
            owner_id,
            lang,
            now,
            binding.target_version,
        )
    if binding.action.startswith("mutation.scope."):
        try:
            pending_id = UUID(binding.target_id)
        except ValueError:
            return MutationMessage(
                _msg(lang, "Выбор области устарел.", "That scope choice has expired.")
            )
        scope_name = binding.action.removeprefix("mutation.scope.")
        return await _choose_scope(
            session, owner_id, pending_id, binding.target_version, scope_name, lang, now
        )
    if binding.action == "mutation.anchor":
        try:
            pending_text, occurrence_text = binding.target_id.split(":", maxsplit=1)
            pending_id, occurrence_id = UUID(pending_text), UUID(occurrence_text)
        except ValueError:
            return MutationMessage(
                _msg(lang, "Выбор случая устарел.", "That occurrence choice is no longer current.")
            )
        return await _choose_series_anchor(
            session, owner_id, pending_id, occurrence_id, binding.target_version, lang, now
        )
    if binding.action == "mutation.anchor.page":
        try:
            pending_text, page_text = binding.target_id.split(":", maxsplit=1)
            pending_id, page = UUID(pending_text), int(page_text)
        except ValueError:
            return MutationMessage(
                _msg(lang, "Список серии устарел.", "The series list is no longer current.")
            )
        return await _series_anchor_page(
            session, owner_id, pending_id, binding.target_version, page, lang, now
        )
    if binding.action == "mutation.page":
        try:
            action_name, *parts = binding.target_id.split("|")
            action = MutationAction(action_name)
            cursor = _parse_cursor_parts(parts)
        except ValueError:
            return MutationMessage(
                _msg(lang, "Список устарел.", "That list page is no longer available.")
            )
        return await _target_menu(session, owner_id, action, lang, now, after=cursor)
    return MutationMessage(
        _msg(lang, "Эта кнопка больше недоступна.", "That button is no longer available.")
    )


async def _target_menu(
    session: AsyncSession,
    owner_id: UUID,
    action: MutationAction,
    language: str,
    now: datetime,
    *,
    after: MutationTargetCursor | None = None,
) -> MutationMessage:
    page = await get_owner_mutation_target_page(
        session, owner_id=owner_id, at=now, after=after, limit=MUTATION_MENU_PAGE_SIZE
    )
    targets = page.items
    if not targets:
        return MutationMessage(
            _msg(
                language,
                "Нет открытых задач для этого действия.",
                "There are no open tasks for this action.",
            )
        )
    tokens = CallbackTokens(SqlAlchemyCallbackTokenStore(session))
    expiry = now.astimezone(UTC) + _CALLBACK_TTL
    rows: list[list[dict[str, str]]] = []
    for target in targets:
        token = await tokens.issue(
            owner_id=owner_id,
            action=f"mutation.select.{action.value}",
            target_id=str(target.occurrence_id),
            target_version=target.occurrence_version,
            expires_at=expiry,
            now=now,
        )
        label = _target_label(target, language)
        rows.append([{"text": label, "callback_data": f"{MUTATION_CALLBACK_PREFIX}{token}"}])
    nav: list[dict[str, str]] = []
    if after is not None:
        token = await tokens.issue(
            owner_id=owner_id,
            action="mutation.page",
            target_id=f"{action.value}|-",
            target_version=0,
            expires_at=expiry,
            now=now,
        )
        nav.append({"text": "‹", "callback_data": f"{MUTATION_CALLBACK_PREFIX}{token}"})
    if page.next_cursor is not None:
        token = await tokens.issue(
            owner_id=owner_id,
            action="mutation.page",
            target_id=_cursor_target(action, page.next_cursor),
            target_version=0,
            expires_at=expiry,
            now=now,
        )
        nav.append({"text": "›", "callback_data": f"{MUTATION_CALLBACK_PREFIX}{token}"})
    if nav:
        rows.append(nav)
    heading = {
        MutationAction.COMPLETE: _msg(
            language, "Какую задачу отметить выполненной?", "Which task should be marked done?"
        ),
        MutationAction.CANCEL: _msg(
            language, "Какую задачу отменить?", "Which task should be cancelled?"
        ),
        MutationAction.RESCHEDULE: _msg(
            language, "Какую задачу перенести?", "Which task should be rescheduled?"
        ),
    }[action]
    return MutationMessage(heading, {"inline_keyboard": rows})


async def _select_target(
    session: AsyncSession,
    target_id: str,
    action_name: str,
    owner_id: UUID,
    language: str,
    now: datetime,
    expected_version: int,
) -> MutationMessage:
    try:
        action = MutationAction(action_name)
        occurrence_id = UUID(target_id)
    except ValueError:
        return MutationMessage(
            _msg(language, "Эта задача недоступна.", "That task is unavailable.")
        )
    target = await _owned_target(session, owner_id, occurrence_id, now)
    if target is None or target.occurrence_version != expected_version:
        return MutationMessage(
            _msg(
                language,
                "Задача изменилась. Откройте список снова.",
                "The task changed. Open the list again.",
            )
        )
    return await _start_or_preview(
        session,
        owner_id=owner_id,
        target=target,
        action=action,
        scope=None,
        language=language,
        now=now,
        pending_fields={},
    )


async def _start_or_preview(
    session: AsyncSession,
    *,
    owner_id: UUID,
    target: MutationTargetSummary,
    action: MutationAction,
    scope: MutationScope | None,
    language: str,
    now: datetime,
    pending_fields: dict[str, object],
) -> MutationMessage:
    if target.state is not OccurrenceState.OPEN:
        return MutationMessage(
            _msg(language, "Эту задачу уже нельзя изменить.", "This task can no longer be changed.")
        )
    if action is MutationAction.COMPLETE:
        scope = MutationScope.THIS_OCCURRENCE
    needs_scope = (
        target.series_id is not None
        and action
        in {
            MutationAction.CANCEL,
            MutationAction.RESCHEDULE,
        }
        and scope is None
    )
    if needs_scope:
        pending = await SqlAlchemyMutationRuntimeStore(session).start(
            owner_id=owner_id,
            occurrence_id=target.occurrence_id,
            expected_occurrence_version=target.occurrence_version,
            expected_series_version=target.series_version,
            action=action.value,
            scope=None,
            state="awaiting_scope",
            language=language,
            pending_fields=pending_fields,
            expires_at=now.astimezone(UTC) + _PENDING_TTL,
        )
        return await _scope_menu(session, pending, language, now)
    if action is MutationAction.RESCHEDULE and "draft" not in pending_fields:
        pending = await SqlAlchemyMutationRuntimeStore(session).start(
            owner_id=owner_id,
            occurrence_id=target.occurrence_id,
            expected_occurrence_version=target.occurrence_version,
            expected_series_version=target.series_version,
            action=action.value,
            scope=scope.value if scope else None,
            state="awaiting_schedule",
            language=language,
            pending_fields=pending_fields,
            expires_at=now.astimezone(UTC) + _PENDING_TTL,
        )
        return MutationMessage(_schedule_prompt(target, language))
    draft = (
        _draft_from_json(pending_fields["draft"]) if action is MutationAction.RESCHEDULE else None
    )
    return await _preview_request(
        session,
        owner_id=owner_id,
        target=target,
        action=action,
        scope=scope or MutationScope.THIS_OCCURRENCE,
        replacement=draft,
        language=language,
        now=now,
    )


async def _scope_menu(
    session: AsyncSession,
    pending: MutationRuntimePendingRecord,
    language: str,
    now: datetime,
) -> MutationMessage:
    tokens = CallbackTokens(SqlAlchemyCallbackTokenStore(session))
    expires = min(pending.expires_at, now.astimezone(UTC) + _CALLBACK_TTL)
    rows = []
    for scope, en, ru in (
        (MutationScope.THIS_OCCURRENCE, "This occurrence", "Только этот случай"),
        (MutationScope.THIS_AND_FUTURE, "This and future", "Этот и будущие"),
    ):
        token = await tokens.issue(
            owner_id=pending.owner_id,
            action=f"mutation.scope.{scope.value}",
            target_id=str(pending.id),
            target_version=pending.version,
            expires_at=expires,
            now=now,
        )
        rows.append(
            [
                {
                    "text": ru if language == "ru" else en,
                    "callback_data": f"{MUTATION_CALLBACK_PREFIX}{token}",
                }
            ]
        )
    title = _msg(
        language,
        "Применить действие к этому случаю или к этому и будущим?",
        "Apply this to this occurrence or this and future occurrences?",
    )
    return MutationMessage(title, {"inline_keyboard": rows})


async def _series_anchor_menu(
    session: AsyncSession,
    pending: MutationRuntimePendingRecord,
    targets: tuple[MutationTargetSummary, ...],
    language: str,
    now: datetime,
    *,
    page: int = 0,
) -> MutationMessage:
    tokens = CallbackTokens(SqlAlchemyCallbackTokenStore(session))
    expires = min(pending.expires_at, now.astimezone(UTC) + _CALLBACK_TTL)
    rows: list[list[dict[str, str]]] = []
    page_count = (len(targets) + MUTATION_MENU_PAGE_SIZE - 1) // MUTATION_MENU_PAGE_SIZE
    if page < 0 or page >= page_count:
        return MutationMessage(
            _msg(language, "Список серии устарел.", "The series list changed. Start again.")
        )
    page_targets = targets[page * MUTATION_MENU_PAGE_SIZE : (page + 1) * MUTATION_MENU_PAGE_SIZE]
    for target in page_targets:
        token = await tokens.issue(
            owner_id=pending.owner_id,
            action="mutation.anchor",
            target_id=f"{pending.id}:{target.occurrence_id}",
            target_version=pending.version,
            expires_at=expires,
            now=now,
        )
        rows.append(
            [
                {
                    "text": _target_label(target, language),
                    "callback_data": f"{MUTATION_CALLBACK_PREFIX}{token}",
                }
            ]
        )
    if page_count > 1:
        nav: list[dict[str, str]] = []
        for next_page, label in ((page - 1, "‹"), (page + 1, "›")):
            if 0 <= next_page < page_count:
                token = await tokens.issue(
                    owner_id=pending.owner_id,
                    action="mutation.anchor.page",
                    target_id=f"{pending.id}:{next_page}",
                    target_version=pending.version,
                    expires_at=expires,
                    now=now,
                )
                nav.append({"text": label, "callback_data": f"{MUTATION_CALLBACK_PREFIX}{token}"})
        rows.append(nav)
    return MutationMessage(
        _msg(
            language,
            "Какой будущий случай серии изменить?",
            "Which occurrence in the series should be changed?",
        ),
        {"inline_keyboard": rows},
    )


async def _series_anchor_page(
    session: AsyncSession,
    owner_id: UUID,
    pending_id: UUID,
    pending_version: int,
    page: int,
    language: str,
    now: datetime,
) -> MutationMessage:
    pending = await SqlAlchemyMutationRuntimeStore(session).get(
        owner_id=owner_id, pending_id=pending_id
    )
    if (
        pending is None
        or pending.state != "awaiting_anchor"
        or pending.version != pending_version
        or pending.expires_at <= now
    ):
        return MutationMessage(
            _msg(language, "Список серии устарел.", "The series list is no longer current.")
        )
    ids = {
        cast(str, item["occurrence_id"])
        for item in cast(list[dict[str, object]], pending.pending_fields.get("anchor_targets", []))
    }
    targets = await get_owner_mutation_targets(session, owner_id=owner_id, at=now, limit=50)
    current = tuple(
        item
        for item in targets
        if str(item.occurrence_id) in ids and item.series_version == pending.expected_series_version
    )
    return await _series_anchor_menu(session, pending, current, language, now, page=page)


async def _choose_series_anchor(
    session: AsyncSession,
    owner_id: UUID,
    pending_id: UUID,
    occurrence_id: UUID,
    pending_version: int,
    language: str,
    now: datetime,
) -> MutationMessage:
    pending_store = SqlAlchemyMutationRuntimeStore(session)
    pending = await pending_store.get(owner_id=owner_id, pending_id=pending_id, lock=True)
    if (
        pending is None
        or pending.state != "awaiting_anchor"
        or pending.version != pending_version
        or pending.expires_at <= now
        or pending.expected_series_version is None
    ):
        return MutationMessage(
            _msg(
                language,
                "Этот выбор случая устарел.",
                "That occurrence choice is no longer current.",
            )
        )
    expected = next(
        (
            item
            for item in cast(
                list[dict[str, object]], pending.pending_fields.get("anchor_targets", [])
            )
            if item.get("occurrence_id") == str(occurrence_id)
        ),
        None,
    )
    target = await _owned_target(session, owner_id, occurrence_id, now)
    if (
        expected is None
        or target is None
        or target.occurrence_version != expected.get("occurrence_version")
        or target.series_version != pending.expected_series_version
    ):
        await pending_store.update(pending, state="stale")
        return MutationMessage(
            _msg(
                language,
                "Серия или задача изменилась. Создайте запрос заново.",
                "The series or task changed. Start again from the current plan.",
            )
        )
    await pending_store.update(
        pending,
        state="awaiting_scope",
        occurrence_id=target.occurrence_id,
        expected_occurrence_version=target.occurrence_version,
        pending_fields={"proposal": pending.pending_fields["proposal"]},
    )
    return await _scope_menu(session, pending, language, now)


async def _choose_scope(
    session: AsyncSession,
    owner_id: UUID,
    pending_id: UUID,
    pending_version: int,
    scope_name: str,
    language: str,
    now: datetime,
) -> MutationMessage:
    pending_store = SqlAlchemyMutationRuntimeStore(session)
    pending = await pending_store.get(owner_id=owner_id, pending_id=pending_id, lock=True)
    if (
        pending is None
        or pending.state != "awaiting_scope"
        or pending.version != pending_version
        or pending.expires_at <= now
    ):
        return MutationMessage(
            _msg(language, "Этот выбор области устарел.", "That scope choice is no longer current.")
        )
    try:
        scope = MutationScope(scope_name)
    except ValueError:
        return MutationMessage(
            _msg(language, "Неизвестная область изменения.", "Unknown mutation scope.")
        )
    if (
        pending.action == MutationAction.COMPLETE.value
        and scope is not MutationScope.THIS_OCCURRENCE
    ):
        return MutationMessage(
            _msg(
                language,
                "Выполнение отмечается только для одного случая.",
                "Completion applies to one occurrence only.",
            )
        )
    target = await _owned_target(session, owner_id, pending.occurrence_id, now)
    if target is None or target.occurrence_version != pending.expected_occurrence_version:
        await pending_store.update(pending, state="stale")
        return MutationMessage(
            _msg(
                language,
                "Задача изменилась. Создайте запрос заново.",
                "The task changed. Start again from the current task.",
            )
        )
    if pending.expected_series_version is not None and target.series_id is not None:
        snapshot = await SqlAlchemyMutationStore(session).get_series_snapshot(
            owner_id=owner_id,
            series_id=target.series_id,
            lock=True,
        )
        if snapshot is None or snapshot.version != pending.expected_series_version:
            await pending_store.update(pending, state="stale")
            return MutationMessage(
                _msg(
                    language,
                    "Серия изменилась. Создайте запрос заново.",
                    "The series changed. Start again from the current plan.",
                )
            )
    action = MutationAction(pending.action)
    fields = pending.pending_fields
    if action is MutationAction.RESCHEDULE and "proposal" in fields:
        proposal = ActionProposal.model_validate_json(json.dumps(fields["proposal"]))
        proposal_draft = _draft_from_proposal(proposal, target, language)
        if isinstance(proposal_draft, MutationMessage):
            return proposal_draft
        fields = {**fields, "draft": _draft_json(proposal_draft)}
    if action is MutationAction.RESCHEDULE and "draft" not in fields:
        await pending_store.update(pending, state="awaiting_schedule", scope=scope.value)
        return MutationMessage(_schedule_prompt(target, language))
    if action is MutationAction.RESCHEDULE:
        draft: OneOffPlanDraft | MutationMessage = _draft_from_json(fields["draft"])
    else:
        draft = None  # type: ignore[assignment]
    if isinstance(draft, MutationMessage):
        return draft
    await pending_store.update(pending, state="previewed", scope=scope.value)
    return await _preview_request(
        session,
        owner_id=owner_id,
        target=target,
        action=action,
        scope=scope,
        replacement=draft,
        language=language,
        now=now,
    )


async def _continue_reschedule(
    session: AsyncSession,
    pending: MutationRuntimePendingRecord,
    text: str,
    language: str,
    now: datetime,
) -> MutationMessage:
    target = await _owned_target(session, pending.owner_id, pending.occurrence_id, now)
    pending_store = SqlAlchemyMutationRuntimeStore(session)
    if target is None or target.occurrence_version != pending.expected_occurrence_version:
        await pending_store.update(pending, state="stale")
        return MutationMessage(
            _msg(
                language,
                "Задача изменилась. Начните перенос заново.",
                "The task changed. Start the reschedule again.",
            )
        )
    parsed = _parse_schedule_text(text, target, language)
    if isinstance(parsed, MutationMessage):
        return parsed
    if pending.scope is None and target.series_id is not None:
        # A correction can be entered before scope selection; preserve it while asking.
        fields: dict[str, object] = {"draft": _draft_json(parsed)}
        await pending_store.update(pending, state="awaiting_scope", pending_fields=fields)
        return await _scope_menu(session, pending, language, now)
    scope = MutationScope(pending.scope) if pending.scope else MutationScope.THIS_OCCURRENCE
    await pending_store.update(
        pending, state="previewed", pending_fields={"draft": _draft_json(parsed)}
    )
    return await _preview_request(
        session,
        owner_id=pending.owner_id,
        target=target,
        action=MutationAction.RESCHEDULE,
        scope=scope,
        replacement=parsed,
        language=language,
        now=now,
    )


async def _preview_request(
    session: AsyncSession,
    *,
    owner_id: UUID,
    target: MutationTargetSummary,
    action: MutationAction,
    scope: MutationScope,
    replacement: OneOffPlanDraft | None,
    language: str,
    now: datetime,
) -> MutationMessage:
    runtime = SqlAlchemyMutationRuntimeStore(session)
    pending_fields: dict[str, object] = {}
    if replacement is not None:
        pending_fields["draft"] = _draft_json(replacement)
    pending = await runtime.start(
        owner_id=owner_id,
        occurrence_id=target.occurrence_id,
        expected_occurrence_version=target.occurrence_version,
        expected_series_version=target.series_version,
        action=action.value,
        scope=scope.value,
        state="previewed",
        language=language,
        pending_fields=pending_fields,
        expires_at=now.astimezone(UTC) + _PENDING_TTL,
    )
    try:
        preview = await preview_mutation(
            MutationRequest(
                owner_id=owner_id,
                occurrence_id=target.occurrence_id,
                expected_occurrence_version=target.occurrence_version,
                action=action,
                scope=scope,
                replacement=replacement,
            ),
            authenticated_owner_id=owner_id,
            store=SqlAlchemyMutationStore(session),
            identity=SqlAlchemyIdentityStore(session),
            billing=SqlAlchemyAccessUsageStore(session),
            clock=_Clock(now),
        )
        message, keyboard = await render_confirmable_mutation(
            session,
            preview,
            language=language,
            now=now,
        )
    except (MutationAccessDenied, MutationRejected, MutationStalePreview, ValueError):
        await runtime.update(pending, state="stale")
        return MutationMessage(
            _msg(
                language,
                "Не удалось подготовить актуальный просмотр. Задача не изменена.",
                "I could not prepare a current preview. The task was not changed.",
            )
        )
    pending.pending_fields = {
        **pending.pending_fields,
        "preview_id": str(preview.preview_id),
        "preview_version": preview.preview_version,
    }
    await session.flush()
    return MutationMessage(message, _serialize_keyboard(keyboard))


async def _owned_target(
    session: AsyncSession,
    owner_id: UUID,
    occurrence_id: UUID,
    now: datetime,
) -> MutationTargetSummary | None:
    return await get_owner_mutation_target(
        session, owner_id=owner_id, occurrence_id=occurrence_id, at=now
    )


async def _binding_for_token(
    session: AsyncSession, owner_id: UUID, token: str
) -> TelegramCallbackTokenRecord | None:
    if not token or len(token.encode("utf-8")) > 64:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return cast(
        TelegramCallbackTokenRecord | None,
        await session.scalar(
            select(TelegramCallbackTokenRecord)
            .where(
                TelegramCallbackTokenRecord.token_hash == digest,
                TelegramCallbackTokenRecord.owner_id == owner_id,
            )
            .execution_options(populate_existing=True)
        ),
    )


async def _invalidate_owner_flow(session: AsyncSession, owner_id: UUID, now: datetime) -> None:
    states = ("awaiting_anchor", "awaiting_scope", "awaiting_schedule", "previewed")
    pending_records = (
        await session.scalars(
            select(MutationRuntimePendingRecord)
            .where(
                MutationRuntimePendingRecord.owner_id == owner_id,
                MutationRuntimePendingRecord.state.in_(states),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    preview_ids = [
        str(record.pending_fields["preview_id"])
        for record in pending_records
        if record.state == "previewed" and record.pending_fields.get("preview_id")
    ]
    for record in pending_records:
        record.state = "stale"
        record.version += 1
    if pending_records:
        await session.flush()
    if preview_ids:
        await session.execute(
            update(TelegramCallbackTokenRecord)
            .where(
                TelegramCallbackTokenRecord.owner_id == owner_id,
                TelegramCallbackTokenRecord.target_id.in_(preview_ids),
                TelegramCallbackTokenRecord.action.in_(("mutation.confirm", "mutation.dismiss")),
                TelegramCallbackTokenRecord.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )


async def _consume_bound_token(
    session: AsyncSession,
    owner_id: UUID,
    token: str,
    binding: TelegramCallbackTokenRecord,
    now: datetime,
) -> object | None:
    try:
        return await CallbackTokens(SqlAlchemyCallbackTokenStore(session)).consume(
            token,
            actor_id=owner_id,
            current_target_version=binding.target_version,
            now=now,
        )
    except InvalidCallbackToken:
        return None


def _parse_schedule_text(
    text: str,
    target: MutationTargetSummary,
    language: str,
) -> OneOffPlanDraft | MutationMessage:
    match = _SCHEDULE_PATTERN.fullmatch(text)
    if match is None:
        return MutationMessage(
            _msg(
                language,
                "Укажите дату YYYY-MM-DD и при необходимости время HH:MM.",
                "Use YYYY-MM-DD and, when needed, HH:MM.",
            )
        )
    try:
        local_date = date.fromisoformat(match.group(1))
        local_time = (
            time.fromisoformat(match.group(2)) if match.group(2) else target.schedule.local_time
        )
    except ValueError:
        return MutationMessage(
            _msg(language, "Дата или время указаны неверно.", "The date or time is invalid.")
        )
    if target.schedule.kind in {PlanKind.TASK, PlanKind.APPOINTMENT} and match.group(2) is None:
        return MutationMessage(
            _msg(
                language,
                "Для этой задачи нужно также указать время HH:MM.",
                "This task also needs a time in HH:MM format.",
            )
        )
    if target.schedule.kind is PlanKind.UNTIMED and match.group(2) is not None:
        return MutationMessage(
            _msg(
                language,
                "Для задачи без времени укажите только дату.",
                "Use only a date for an untimed task.",
            )
        )
    return OneOffPlanDraft(
        kind=target.schedule.kind,
        title=target.title,
        local_date=local_date,
        local_time=local_time,
        event_zone=target.schedule.event_zone,
        end_local_date=None,
        end_local_time=None,
        duration_seconds=target.schedule.duration_seconds,
        fold=target.schedule.fold,
        end_fold=None,
    )


def _cursor_target(action: MutationAction, cursor: MutationTargetCursor) -> str:
    local_time = cursor.local_time.isoformat() if cursor.local_time is not None else "-"
    return f"{action.value}|{cursor.local_date.isoformat()}|{local_time}|{cursor.occurrence_id}"


def _parse_cursor_parts(parts: list[str]) -> MutationTargetCursor | None:
    if parts == ["-"]:
        return None
    if len(parts) != 3:
        raise ValueError("invalid mutation list cursor")
    local_date = date.fromisoformat(parts[0])
    local_time = None if parts[1] == "-" else time.fromisoformat(parts[1])
    return MutationTargetCursor(local_date, local_time, UUID(parts[2]))


def _draft_from_proposal(
    proposal: ActionProposal,
    target: MutationTargetSummary,
    language: str,
) -> OneOffPlanDraft | MutationMessage:
    schedule = proposal.fields.schedule
    if schedule is None:
        return MutationMessage(
            _msg(
                language,
                "Укажите новую дату и время для переноса.",
                "Please provide the new date and time for the reschedule.",
            )
        )
    if "recurrence" in proposal.fields.model_fields_set:
        return MutationMessage(
            _msg(
                language,
                "Изменение правила повтора требует уточнения.",
                "Changing the recurrence rule needs clarification.",
            )
        )
    if "description" in proposal.fields.model_fields_set:
        return MutationMessage(
            _msg(
                language,
                "Описание этой задачи нельзя изменить через перенос.",
                "Description changes are not supported by reschedule.",
            )
        )
    title = proposal.fields.title or target.title
    if title != target.title:
        return MutationMessage(
            _msg(
                language,
                "При переносе название остаётся прежним. Уточните дату и время.",
                "A reschedule keeps the title. Please clarify the date and time.",
            )
        )
    reminders: tuple[ReminderOffset, ...] | None = None
    if "reminders" in proposal.fields.model_fields_set and proposal.fields.reminders is None:
        return MutationMessage(
            _msg(
                language,
                "Уточните, оставить напоминания или удалить их.",
                "Clarify whether to keep or remove the reminders.",
            )
        )
    if "reminders" in proposal.fields.model_fields_set:
        converted: list[ReminderOffset] = []
        for reminder in proposal.fields.reminders or ():
            if isinstance(reminder, ExplicitReminder):
                return MutationMessage(
                    _msg(
                        language,
                        "Для точного времени напоминания нужно уточнение.",
                        "Exact reminder times need clarification.",
                    )
                )
            if not isinstance(reminder, RelativeReminder):
                return MutationMessage(
                    _msg(
                        language,
                        "Напоминание нельзя перенести без уточнения.",
                        "That reminder needs clarification.",
                    )
                )
            if reminder.basis == "calendar_day":
                converted.append(
                    ReminderOffset(OffsetKind.CALENDAR_DAYS, reminder.amount, reminder.provenance)
                )
            else:
                seconds = reminder.amount * {"hour": 3_600, "minute": 60}[reminder.unit]
                converted.append(
                    ReminderOffset(OffsetKind.ELAPSED_SECONDS, seconds, reminder.provenance)
                )
        reminders = tuple(converted)

    local_date: date
    local_time: time | None
    zone: str
    if isinstance(schedule, AppointmentSchedule):
        kind = PlanKind.APPOINTMENT
        duration = schedule.duration_minutes * 60 if schedule.duration_minutes is not None else None
        if (
            "duration_minutes" not in schedule.model_fields_set
            and "local_end" not in schedule.model_fields_set
        ):
            duration = target.schedule.duration_seconds
        end_date = schedule.local_end.date() if schedule.local_end is not None else None
        end_time = schedule.local_end.time() if schedule.local_end is not None else None
        fold, end_fold = schedule.fold, schedule.end_fold
        local_date, local_time, zone = (
            schedule.local_start.date(),
            schedule.local_start.time(),
            schedule.timezone,
        )
        if duration is None and end_time is None:
            return MutationMessage(
                _msg(
                    language,
                    "Укажите длительность или время окончания встречи.",
                    "Please specify the appointment duration or end time.",
                )
            )
    elif isinstance(schedule, DeadlineSchedule):
        kind = PlanKind.DEADLINE
        duration = end_date = end_time = end_fold = None
        fold = schedule.fold
        local_date = schedule.local_date
        local_time = schedule.local_time or target.schedule.local_time
        zone = schedule.timezone
    elif isinstance(schedule, TaskSchedule):
        if schedule.timing == "unspecified" or schedule.local_date is None:
            return MutationMessage(
                _msg(language, "Уточните дату новой задачи.", "Please clarify the task's new date.")
            )
        kind = PlanKind.UNTIMED if schedule.timing == "untimed" else PlanKind.TASK
        if kind is PlanKind.TASK and schedule.local_time is None:
            return MutationMessage(
                _msg(
                    language, "Уточните новое время задачи.", "Please clarify the task's new time."
                )
            )
        duration = end_date = end_time = end_fold = None
        fold = schedule.fold
        local_date = schedule.local_date
        local_time = schedule.local_time
        zone = schedule.timezone or target.schedule.event_zone
    else:
        return MutationMessage(
            _msg(
                language,
                "Расписание нельзя применить без уточнения.",
                "That schedule needs clarification.",
            )
        )
    if kind is not target.schedule.kind:
        return MutationMessage(
            _msg(
                language,
                "Тип задачи нельзя изменить при переносе.",
                "A reschedule cannot change the task type.",
            )
        )
    return OneOffPlanDraft(
        kind=kind,
        title=title,
        local_date=local_date,
        local_time=local_time,
        event_zone=zone,
        end_local_date=end_date,
        end_local_time=end_time,
        duration_seconds=duration,
        fold=fold,
        end_fold=end_fold,
        reminders=reminders,
    )


def _draft_json(draft: OneOffPlanDraft) -> dict[str, object]:
    return {
        "kind": draft.kind.value,
        "title": draft.title,
        "local_date": draft.local_date.isoformat(),
        "local_time": draft.local_time.isoformat() if draft.local_time else None,
        "event_zone": draft.event_zone,
        "end_local_date": draft.end_local_date.isoformat() if draft.end_local_date else None,
        "end_local_time": draft.end_local_time.isoformat() if draft.end_local_time else None,
        "duration_seconds": draft.duration_seconds,
        "fold": draft.fold,
        "end_fold": draft.end_fold,
        "reminders": [
            {"kind": item.kind.value, "amount": item.amount, "source": item.source}
            for item in draft.reminders or ()
        ]
        if draft.reminders is not None
        else None,
    }


def _draft_from_json(raw: object) -> OneOffPlanDraft:
    data = cast(dict[str, object], raw)
    reminders_raw = data.get("reminders")
    reminders = (
        tuple(
            ReminderOffset(
                OffsetKind(cast(str, cast(dict[str, object], item)["kind"])),
                int(cast(int, cast(dict[str, object], item)["amount"])),
                cast(str, cast(dict[str, object], item)["source"]),
            )
            for item in cast(list[object], reminders_raw)
        )
        if reminders_raw is not None
        else None
    )
    return OneOffPlanDraft(
        kind=PlanKind(cast(str, data["kind"])),
        title=cast(str, data["title"]),
        local_date=date.fromisoformat(cast(str, data["local_date"])),
        local_time=time.fromisoformat(cast(str, data["local_time"]))
        if data.get("local_time")
        else None,
        event_zone=cast(str | None, data.get("event_zone")),
        end_local_date=date.fromisoformat(cast(str, data["end_local_date"]))
        if data.get("end_local_date")
        else None,
        end_local_time=time.fromisoformat(cast(str, data["end_local_time"]))
        if data.get("end_local_time")
        else None,
        duration_seconds=cast(int | None, data.get("duration_seconds")),
        fold=cast(str | None, data.get("fold")),
        end_fold=cast(str | None, data.get("end_fold")),
        reminders=reminders,
    )


def _schedule_prompt(target: MutationTargetSummary, language: str) -> str:
    if target.schedule.kind is PlanKind.UNTIMED:
        return _msg(
            language,
            "Отправьте новую дату в формате YYYY-MM-DD.",
            "Send the new date as YYYY-MM-DD.",
        )
    return _msg(
        language,
        "Отправьте новую дату и время в формате YYYY-MM-DD HH:MM.",
        "Send the new date and time as YYYY-MM-DD HH:MM.",
    )


def _target_label(target: MutationTargetSummary, language: str) -> str:
    when = target.schedule.local_date.isoformat()
    if target.schedule.local_time is not None:
        when += " " + target.schedule.local_time.strftime("%H:%M")
    title = target.title.strip()
    if len(title) > 48:
        title = title[:45] + "…"
    return f"{when} · {title}"


def _serialize_keyboard(keyboard: object) -> dict[str, object]:
    dump = getattr(keyboard, "model_dump", None)
    if dump is None:
        raise TypeError("mutation keyboard must be serializable")
    return cast(dict[str, object], dump(mode="json", exclude_none=True))


def _confirmed_text(language: str, action: str) -> str:
    if action == MutationAction.COMPLETE.value:
        return _msg(language, "Задача отмечена выполненной.", "Task marked done.")
    if action == MutationAction.CANCEL.value:
        return _msg(language, "Задача отменена.", "Task cancelled.")
    if action == MutationAction.RESCHEDULE.value:
        return _msg(language, "Расписание задачи обновлено.", "Task rescheduled.")
    return _msg(language, "Изменение подтверждено.", "Change confirmed.")


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")


def _language(value: str) -> str:
    return "ru" if value == "ru" else "en"


def _msg(language: str, ru: str, en: str) -> str:
    return ru if language == "ru" else en


__all__ = [
    "MutationMessage",
    "MUTATION_MENU_PAGE_SIZE",
    "is_mutation_callback",
    "prepare_proposed_mutation",
    "process_mutation_callback",
    "process_mutation_command",
]
