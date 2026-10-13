"""Durable Telegram choice flow for an explicitly confirmed home-zone edit."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.adapters.telegram.presentation.callbacks import CallbackBinding
from pin.adapters.telegram.presentation.formatting import escape_markdown_v2
from pin.adapters.telegram.presentation.onboarding import OnboardingButton, OnboardingReply
from pin.contexts.identity.api import (
    HomeZoneChangeHandoff,
    IdentityStore,
    OnboardingDraft,
    Preferences,
    PreferencesSnapshot,
)
from pin.contexts.identity.application.onboarding import OnboardingResult
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.planning.application.home_zone_changes import (
    HomeZoneChangeGrant,
    HomeZoneChangePreview,
    HomeZoneChangeProof,
    HomeZoneChangeRejected,
    HomeZoneChangeRequest,
    HomeZoneChangeVerifier,
    confirm_home_zone_change,
    preview_home_zone_change,
)
from pin.contexts.planning.domain.plans import OffsetKind, ReminderOffset
from pin.contexts.planning.infrastructure.home_zone_change_models import (
    HomeZoneChangePreviewRecord,
)
from pin.contexts.planning.infrastructure.home_zone_change_store import (
    SqlAlchemyHomeZoneChangeStore,
)

HOME_ZONE_ACTIONS = frozenset(
    {
        "home_zone_keep",
        "home_zone_choose",
        "home_zone_toggle_occurrence",
        "home_zone_toggle_series",
        "home_zone_scope_page",
        "home_zone_preview",
        "home_zone_preview_page",
        "home_zone_qualify_reminders",
        "home_zone_preview_confirm",
        "home_zone_cancel",
    }
)
_FLOW_KEY = "_home_zone_change_flow"
_PREVIEW_PAGE_SIZE = 5
_SCOPE_PAGE_SIZE = 8


@dataclass(frozen=True, slots=True)
class HomeZoneRuntimeResult:
    onboarding: OnboardingResult
    reply: OnboardingReply


@dataclass(frozen=True, slots=True)
class _Clock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


def needs_home_zone_review(
    draft: OnboardingDraft | None, profile: PreferencesSnapshot | None
) -> bool:
    if draft is None or profile is None:
        return False
    if draft.mode != "preferences" or draft.step != "confirm":
        return False
    return _candidate_preferences(draft).home_zone != profile.home_zone


def pending_home_zone_text_reply(
    draft: OnboardingDraft, profile: PreferencesSnapshot
) -> HomeZoneRuntimeResult:
    language = profile.language
    text = (
        "Use the current review buttons, or send /cancel without saving."
        if language == "en"
        else "Используйте кнопки проверки или отправьте /cancel без сохранения."
    )
    return HomeZoneRuntimeResult(
        OnboardingResult(
            "confirm_required", draft.step, draft.mode, profile, draft=dict(draft.values)
        ),
        _reply(text),
    )


async def stale_home_zone_action(identity: IdentityStore, owner_id: UUID) -> HomeZoneRuntimeResult:
    profile = await identity.get_preferences(owner_id)
    draft = await identity.get_onboarding(owner_id)
    return _stale_result(profile, draft.values if draft else None)


async def begin_home_zone_review(
    identity: IdentityStore,
    *,
    draft: OnboardingDraft,
    profile: PreferencesSnapshot,
) -> HomeZoneRuntimeResult:
    flow = {"state": "scope", "occurrences": [], "series": [], "qualified": []}
    values = {**draft.values, _FLOW_KEY: flow}
    changed = await identity.advance_onboarding(draft, step=draft.step, values=values)
    if changed is None:
        return _stale_result(profile, draft.values)
    return _scope_choice(profile, changed.values)


async def handle_home_zone_action(
    session: AsyncSession,
    identity: IdentityStore,
    *,
    binding: CallbackBinding,
    callback_data: str,
    owner_id: UUID,
    now: datetime,
) -> HomeZoneRuntimeResult:
    profile = await identity.get_preferences(owner_id)
    draft = await identity.get_onboarding(owner_id)
    if profile is None or draft is None or draft.mode != "preferences" or draft.step != "confirm":
        return _expired_result(profile, draft.values if draft else None)
    if binding.target_version != draft.version or not needs_home_zone_review(draft, profile):
        return _stale_result(profile, draft.values)
    if binding.action not in HOME_ZONE_ACTIONS:
        return _stale_result(profile, draft.values)

    flow = _flow(draft)
    if binding.action == "home_zone_cancel":
        await identity.cancel_onboarding(owner_id)
        return _cancelled_result(profile, draft)
    if binding.action == "home_zone_keep":
        return await _create_preview(
            session, identity, draft=draft, profile=profile, now=now, flow=flow
        )
    if binding.action == "home_zone_choose":
        preview = await _make_preview(session, draft, profile, now, flow, (), ())
        flow["state"] = "select"
        changed = await _advance_flow(identity, draft, flow)
        if changed is None:
            return _stale_result(profile, draft.values)
        return _scope_menu(preview.projection.payload, changed.values, 0)
    if binding.action in {"home_zone_toggle_occurrence", "home_zone_toggle_series"}:
        return await _toggle_scope(
            session,
            identity,
            draft=draft,
            profile=profile,
            flow=flow,
            target=binding.target_id,
            target_type=(
                "occurrence" if binding.action == "home_zone_toggle_occurrence" else "series"
            ),
            now=now,
        )
    if binding.action == "home_zone_scope_page":
        page = _page_target(binding.target_id, prefix="scope")
        preview = await _make_preview(session, draft, profile, now, flow, (), ())
        flow["page"] = page
        changed = await _advance_flow(identity, draft, flow)
        if changed is None:
            return _stale_result(profile, draft.values)
        return _scope_menu(preview.projection.payload, changed.values, page)
    if binding.action == "home_zone_preview":
        if flow.get("state") != "select":
            return _stale_result(profile, draft.values)
        return await _create_preview(
            session, identity, draft=draft, profile=profile, now=now, flow=flow
        )
    if binding.action == "home_zone_preview_page":
        preview_id, digest, page = _preview_page_target(binding.target_id)
        saved = await _load_preview(session, owner_id, preview_id, digest, profile, now)
        if saved is None or not _preview_matches_flow(saved.payload, draft, flow):
            return _stale_result(profile, draft.values)
        return _render_preview(
            saved.payload, saved.id, saved.preview_version, digest, page, draft, profile
        )
    if binding.action == "home_zone_qualify_reminders":
        preview_id, digest, page = _preview_page_target(binding.target_id)
        saved = await _load_preview(session, owner_id, preview_id, digest, profile, now)
        if saved is None or not _preview_matches_flow(saved.payload, draft, flow):
            return _stale_result(profile, draft.values)
        projection = _object(saved.payload.get("projection"))
        items = _preview_items(projection)
        start = page * _PREVIEW_PAGE_SIZE
        visible = items[start : start + _PREVIEW_PAGE_SIZE]
        unresolved = {
            str(_object(item).get("target_id"))
            for item in _array(projection.get("unresolved_reminder_targets"))
            if str(_object(item).get("target_id"))
            in {
                str(_object(item).get("occurrence_id") or _object(item).get("series_id"))
                for item in visible
            }
        }
        if not unresolved:
            return _render_preview(
                saved.payload, saved.id, saved.preview_version, digest, page, draft, profile
            )
        flow["qualified"] = sorted(set(_strings(flow.get("qualified"))) | unresolved)
        changed = await _advance_flow(identity, draft, flow)
        if changed is None:
            return _stale_result(profile, draft.values)
        return await _create_preview(
            session, identity, draft=changed, profile=profile, now=now, flow=flow
        )
    if binding.action == "home_zone_preview_confirm":
        preview_id, preview_version, digest = _confirm_target(binding.target_id)
        saved = await _load_preview(session, owner_id, preview_id, digest, profile, now)
        if (
            saved is None
            or saved.preview_version != preview_version
            or not _preview_matches_flow(saved.payload, draft, flow)
        ):
            return _stale_result(profile, draft.values)
        verifier = _ConsumedCallbackVerifier(
            binding, callback_data, preview_id, preview_version, digest
        )
        result = await confirm_home_zone_change(
            HomeZoneChangeProof(preview_id, preview_version, callback_data),
            authenticated_owner_id=owner_id,
            store=SqlAlchemyHomeZoneChangeStore(session),
            identity=identity,
            verifier=verifier,
            clock=_Clock(now),
        )
        saved_profile = await identity.get_preferences(owner_id)
        if saved_profile is None or saved_profile.version != result["profile_version"]:
            raise RuntimeError("confirmed home-zone profile disappeared in its UoW")
        if not await identity.complete_onboarding(
            draft,
            values=_saved_draft_values(saved_profile),
            profile_version=saved_profile.version,
        ):
            raise RuntimeError("settings draft changed during zone confirmation; roll back UoW")
        handoff = HomeZoneChangeHandoff(
            owner_id,
            profile.home_zone,
            saved_profile.home_zone,
            saved_profile.version,
            now.astimezone(UTC),
        )
        result_onboarding = OnboardingResult(
            "confirmed", preferences=saved_profile, home_zone_change=handoff
        )
        return HomeZoneRuntimeResult(result_onboarding, _confirmed_reply(saved_profile))
    return _stale_result(profile, draft.values)


async def _toggle_scope(
    session: AsyncSession,
    identity: IdentityStore,
    *,
    draft: OnboardingDraft,
    profile: PreferencesSnapshot,
    flow: dict[str, object],
    target: str,
    target_type: str,
    now: datetime,
) -> HomeZoneRuntimeResult:
    try:
        target_id = UUID(target)
    except (ValueError, TypeError, AttributeError):
        return _stale_result(profile, draft.values)
    preview = await _make_preview(session, draft, profile, now, flow, (), ())
    key = "occurrences" if target_type == "occurrence" else "series"
    item_key = "occurrence_id" if target_type == "occurrence" else "series_id"
    items = _array(preview.projection.payload.get(key))
    if not any(str(_object(item).get(item_key)) == str(target_id) for item in items):
        return _stale_result(profile, draft.values)
    selected = set(_strings(flow.get(key)))
    if str(target_id) in selected:
        selected.remove(str(target_id))
    else:
        selected.add(str(target_id))
    flow[key] = sorted(selected)
    flow["state"] = "select"
    changed = await _advance_flow(identity, draft, flow)
    if changed is None:
        return _stale_result(profile, draft.values)
    return _scope_menu(preview.projection.payload, changed.values, _int(flow.get("page"), 0))


async def _create_preview(
    session: AsyncSession,
    identity: IdentityStore,
    *,
    draft: OnboardingDraft,
    profile: PreferencesSnapshot,
    now: datetime,
    flow: dict[str, object],
) -> HomeZoneRuntimeResult:
    projection = await _make_preview(
        session,
        draft,
        profile,
        now,
        flow,
        tuple(UUID(value) for value in _strings(flow.get("occurrences"))),
        tuple(UUID(value) for value in _strings(flow.get("series"))),
    )
    saved = await _load_preview(
        session, profile.owner_id, projection.preview_id, projection.projection_sha256, profile, now
    )
    if saved is None:
        raise RuntimeError("newly saved home-zone preview could not be loaded")
    return _render_preview(
        saved.payload, saved.id, saved.preview_version, saved.projection_sha256, 0, draft, profile
    )


async def _make_preview(
    session: AsyncSession,
    draft: OnboardingDraft,
    profile: PreferencesSnapshot,
    now: datetime,
    flow: dict[str, object],
    occurrences: tuple[UUID, ...],
    series: tuple[UUID, ...],
) -> HomeZoneChangePreview:
    candidate = _candidate_preferences(draft)
    reminder_overrides: dict[UUID, tuple[ReminderOffset, ...]] = {}
    for value in _strings(flow.get("qualified")):
        reminder_overrides[UUID(value)] = _candidate_offsets(candidate)
    return await preview_home_zone_change(
        HomeZoneChangeRequest(
            owner_id=profile.owner_id,
            expected_profile_version=profile.version,
            preferences=candidate,
            reinterpret_occurrence_ids=occurrences,
            reinterpret_series_ids=series,
            reminder_overrides=tuple(reminder_overrides.items()),
        ),
        store=SqlAlchemyHomeZoneChangeStore(session),
        identity=SqlAlchemyIdentityStore(session),
        clock=_Clock(now),
    )


class _ConsumedCallbackVerifier(HomeZoneChangeVerifier):
    def __init__(
        self,
        binding: CallbackBinding,
        token: str,
        preview_id: UUID,
        preview_version: int,
        digest: str,
    ) -> None:
        self.binding = binding
        self.token = token
        self.preview_id = preview_id
        self.preview_version = preview_version
        self.digest = digest

    async def verify_and_consume(
        self,
        proof: HomeZoneChangeProof,
        *,
        owner_id: UUID,
        projection_sha256: str,
        at: datetime,
    ) -> HomeZoneChangeGrant | None:
        expected_target = f"{self.preview_id}|{self.preview_version}|{self.digest}"
        if (
            self.binding.owner_id != owner_id
            or self.binding.action != "home_zone_preview_confirm"
            or self.binding.target_id != expected_target
            or hashlib.sha256(proof.token.encode("utf-8")).hexdigest() != self.binding.token_hash
            or proof.token != self.token
            or proof.preview_id != self.preview_id
            or proof.preview_version != self.preview_version
            or projection_sha256 != self.digest
            or at >= self.binding.expires_at
        ):
            return None
        return HomeZoneChangeGrant(
            owner_id,
            self.preview_id,
            self.preview_version,
            self.digest,
            self.binding.expires_at,
        )


async def _advance_flow(
    identity: IdentityStore, draft: OnboardingDraft, flow: dict[str, object]
) -> OnboardingDraft | None:
    return await identity.advance_onboarding(
        draft,
        step=draft.step,
        values={**draft.values, _FLOW_KEY: flow},
    )


def _scope_choice(profile: PreferencesSnapshot, values: dict[str, object]) -> HomeZoneRuntimeResult:
    language = _language(values)
    text = (
        "Review this zone change. Keep event schedules, or select future items to "
        "reinterpret. New quiet hours apply either way."
        if language == "en"
        else "Проверьте смену пояса. Сохраните расписания или выберите будущие "
        "задачи для переинтерпретации. Новые тихие часы будут показаны в любом случае."
    )
    buttons = (
        _button(language, "Keep local schedules", "home_zone_keep", "keep"),
        _button(language, "Choose plans", "home_zone_choose", "choose"),
        _button(language, "Cancel settings", "home_zone_cancel", "cancel"),
    )
    return HomeZoneRuntimeResult(
        OnboardingResult("confirm_required", "confirm", "preferences", profile, draft=dict(values)),
        _reply(text, buttons),
    )


def _scope_menu(
    projection: dict[str, object], values: dict[str, object], page: int
) -> HomeZoneRuntimeResult:
    language = _language(values)
    flow = _flow_from_values(values)
    items: list[tuple[str, str, str, str]] = []
    for item in _array(projection.get("occurrences")):
        value = _object(item)
        target_id = str(value.get("occurrence_id", ""))
        if target_id:
            schedule = _object(value.get("before"))
            label = f"{value.get('title', 'Plan')} · {schedule.get('local_date', '')}"
            items.append(("occurrence", target_id, label, "occurrence_id"))
    for item in _array(projection.get("series")):
        value = _object(item)
        target_id = str(value.get("series_id", ""))
        if target_id:
            label = f"{value.get('title', 'Recurring plan')} · {value.get('next_local_date', '')}"
            items.append(("series", target_id, label, "series_id"))
    selected_occurrences = set(_strings(flow.get("occurrences")))
    selected_series = set(_strings(flow.get("series")))
    start = page * _SCOPE_PAGE_SIZE
    visible = items[start : start + _SCOPE_PAGE_SIZE]
    buttons = []
    for kind, target_id, label, _ in visible:
        selected = target_id in (selected_occurrences if kind == "occurrence" else selected_series)
        action = (
            "home_zone_toggle_occurrence" if kind == "occurrence" else "home_zone_toggle_series"
        )
        marker = "✓ " if selected else "＋ "
        buttons.append(_button(language, marker + label[:38], action, target_id))
    if page > 0:
        buttons.append(_button(language, "Previous", "home_zone_scope_page", f"scope|{page - 1}"))
    if start + _SCOPE_PAGE_SIZE < len(items):
        buttons.append(_button(language, "Next", "home_zone_scope_page", f"scope|{page + 1}"))
    buttons.append(_button(language, "Preview selected", "home_zone_preview", "preview"))
    buttons.append(_button(language, "Cancel", "home_zone_cancel", "cancel"))
    total = len(items)
    heading = (
        f"Select future items to reinterpret, page {page + 1}. "
        f"Showing {len(visible)} of {total}. Selected: "
        f"{len(selected_occurrences)} occurrence(s), {len(selected_series)} series."
        if language == "en"
        else f"Выберите будущие элементы для переинтерпретации, стр. {page + 1}. "
        f"Показано {len(visible)} из {total}. Выбрано задач: "
        f"{len(selected_occurrences)}, серий: {len(selected_series)}."
    )
    if total == 0:
        heading += "\n" + (
            "No future open plans were found; you can still confirm the zone change."
            if language == "en"
            else "Будущих открытых планов нет; смену пояса всё равно можно подтвердить."
        )
    return HomeZoneRuntimeResult(
        OnboardingResult("confirm_required", "confirm", "preferences", draft=dict(values)),
        _reply(heading, tuple(buttons)),
    )


def _render_preview(
    payload: dict[str, object],
    preview_id: UUID,
    preview_version: int,
    digest: str,
    page: int,
    draft: OnboardingDraft,
    profile: PreferencesSnapshot,
) -> HomeZoneRuntimeResult:
    flow = _flow(draft)
    projection = _object(payload.get("projection"))
    items = _preview_items(projection)
    page_count = max(1, (len(items) + _PREVIEW_PAGE_SIZE - 1) // _PREVIEW_PAGE_SIZE)
    if not 0 <= page < page_count:
        page = 0
    start = page * _PREVIEW_PAGE_SIZE
    visible = items[start : start + _PREVIEW_PAGE_SIZE]
    language = _language(draft.values)
    candidate = _candidate_preferences(draft)
    selected_o = set(_strings(flow.get("occurrences")))
    selected_s = set(_strings(flow.get("series")))
    quiet_text = f"{candidate.home_zone} {candidate.quiet_start:%H:%M}–{candidate.quiet_end:%H:%M}"
    intro = (
        f"Home zone: {profile.home_zone} → {candidate.home_zone}. "
        f"Selected: {len(selected_o)} occurrence(s), {len(selected_s)} series. "
        "Keep preserves event zones/local times; reinterpret changes selected schedules. "
        f"New quiet hours: {quiet_text}. Page {page + 1}/{page_count}."
        if language == "en"
        else f"Домашний пояс: {profile.home_zone} → {candidate.home_zone}. "
        f"Выбрано задач: {len(selected_o)}, серий: {len(selected_s)}. "
        "«Оставить» сохраняет пояс и местное время; меняются только выбранные расписания. "
        f"Новые тихие часы: {quiet_text}. Страница {page + 1}/{page_count}."
    )
    lines = [intro]
    for item in visible:
        value = _object(item)
        if "occurrence_id" in value:
            before = _object(value.get("before"))
            after = _object(value.get("after"))
            lines.append(
                f"{value.get('title', 'Plan')} · "
                f"{before.get('local_date')} {before.get('local_time') or ''} "
                f"{before.get('event_zone')} → {after.get('local_date')} "
                f"{after.get('local_time') or ''} {after.get('event_zone')}"
            )
            reminders = _array(value.get("reminders"))
            due = ", ".join(
                f"{_object(item).get('kind')} {_object(item).get('amount')}: "
                f"{_object(item).get('due_at')} UTC"
                for item in reminders
            ) or ("none" if language == "en" else "нет")
            lines.append(("Reminders: " if language == "en" else "Напоминания: ") + due)
            omitted = _array(value.get("omitted_reminders"))
            notices = _array(value.get("reminder_notices"))
            if omitted:
                lines.append(
                    ("Omitted: " if language == "en" else "Пропущено: ")
                    + ", ".join(str(_object(item).get("kind")) for item in omitted)
                )
            if notices:
                lines.append(
                    (
                        "Quiet-hour adjustments: "
                        if language == "en"
                        else "Изменения из-за тихих часов: "
                    )
                    + ", ".join(str(_object(item).get("reason")) for item in notices)
                )
        else:
            quiet_policy = _object(value.get("after_quiet_policy"))
            lines.append(
                f"Series {value.get('series_id')}: next {value.get('next_local_date')}; "
                f"zone {value.get('before_default_zone')} → {value.get('after_default_zone')}; "
                f"quiet {quiet_policy.get('zone')} {quiet_policy.get('start')}–"
                f"{quiet_policy.get('end')}; "
                f"reminders {value.get('after_effective_reminders')}; "
                "anchor/count/history preserved."
            )
    unresolved = [
        _object(item)
        for item in _array(projection.get("unresolved_reminder_targets"))
        if str(_object(item).get("target_id"))
        in {
            str(_object(item).get("occurrence_id") or _object(item).get("series_id"))
            for item in visible
        }
    ]
    if unresolved:
        names = ", ".join(str(item.get("title", item.get("target_id"))) for item in unresolved)
        lines.append(
            f"Saved reminder meaning is missing for: {names}. "
            "Choose to apply the reminder settings you reviewed; nothing is inferred."
            if language == "en"
            else f"Для элементов не сохранён смысл напоминаний: {names}. "
            "Можно применить выбранные настройки напоминаний; ничего не угадывается."
        )
    buttons: list[OnboardingButton] = []
    page_target = f"{preview_id}|{digest}|{page}"
    if page > 0:
        buttons.append(
            _button(
                language,
                "Previous preview page",
                "home_zone_preview_page",
                f"{preview_id}|{digest}|{page - 1}",
            )
        )
    if page + 1 < page_count:
        buttons.append(
            _button(
                language,
                "Next preview page",
                "home_zone_preview_page",
                f"{preview_id}|{digest}|{page + 1}",
            )
        )
    if unresolved:
        buttons.append(
            _button(
                language,
                "Use reviewed reminders for these items",
                "home_zone_qualify_reminders",
                page_target,
            )
        )
    elif page + 1 == page_count and not _array(projection.get("unresolved_reminder_targets")):
        buttons.append(
            _button(
                language,
                "Confirm zone change",
                "home_zone_preview_confirm",
                f"{preview_id}|{preview_version}|{digest}",
            )
        )
    buttons.append(_button(language, "Cancel", "home_zone_cancel", "cancel"))
    return HomeZoneRuntimeResult(
        OnboardingResult(
            "confirm_required", "confirm", "preferences", profile, draft=dict(draft.values)
        ),
        _reply("\n\n".join(lines), tuple(buttons)),
    )


def _preview_items(projection: dict[str, object]) -> list[dict[str, object]]:
    return [
        *(_object(item) for item in _array(projection.get("occurrences"))),
        *(_object(item) for item in _array(projection.get("series"))),
    ]


def _preview_matches_flow(
    payload: dict[str, object], draft: OnboardingDraft, flow: dict[str, object]
) -> bool:
    candidate = _candidate_preferences(draft)
    expected_candidate = {
        "language": candidate.language,
        "home_zone": candidate.home_zone,
        "reminder_calendar_days": candidate.reminder_calendar_days,
        "reminder_elapsed_hours": candidate.reminder_elapsed_hours,
        "quiet_start": candidate.quiet_start.isoformat(),
        "quiet_end": candidate.quiet_end.isoformat(),
        "review_time": candidate.review_time.isoformat(),
        "version": candidate.version,
    }
    expected_offsets = {
        key: [
            {"kind": item.kind.value, "amount": item.amount, "source": item.source}
            for item in _candidate_offsets(candidate)
        ]
        for key in sorted(_strings(flow.get("qualified")))
    }
    expected = {
        "reinterpret_occurrence_ids": sorted(_strings(flow.get("occurrences"))),
        "reinterpret_series_ids": sorted(_strings(flow.get("series"))),
        "candidate": expected_candidate,
        "reminder_overrides": expected_offsets,
    }
    return all(payload.get(key) == value for key, value in expected.items())


def _candidate_preferences(draft: OnboardingDraft) -> Preferences:
    values = draft.values
    return Preferences(
        language=str(values["language"]),
        home_zone=str(values["home_zone"]),
        reminder_calendar_days=_int(values.get("reminder_calendar_days"), -1),
        reminder_elapsed_hours=_int(values.get("reminder_elapsed_hours"), -1),
        quiet_start=time.fromisoformat(str(values["quiet_start"])),
        quiet_end=time.fromisoformat(str(values["quiet_end"])),
        review_time=time.fromisoformat(str(values["review_time"])),
        version=int(draft.base_profile_version or 0) + 1,
    )


def _candidate_offsets(candidate: Preferences) -> tuple[ReminderOffset, ...]:
    values: list[ReminderOffset] = []
    if candidate.reminder_calendar_days:
        values.append(
            ReminderOffset(
                OffsetKind.CALENDAR_DAYS,
                candidate.reminder_calendar_days,
                "home_zone_settings_choice",
            )
        )
    if candidate.reminder_elapsed_hours:
        values.append(
            ReminderOffset(
                OffsetKind.ELAPSED_SECONDS,
                candidate.reminder_elapsed_hours * 3600,
                "home_zone_settings_choice",
            )
        )
    return tuple(values)


async def _load_preview(
    session: AsyncSession,
    owner_id: UUID,
    preview_id: UUID,
    digest: str,
    profile: PreferencesSnapshot,
    now: datetime,
) -> HomeZoneChangePreviewRecord | None:
    statement = (
        select(HomeZoneChangePreviewRecord)
        .where(
            HomeZoneChangePreviewRecord.id == preview_id,
            HomeZoneChangePreviewRecord.owner_id == owner_id,
            HomeZoneChangePreviewRecord.projection_sha256 == digest,
            HomeZoneChangePreviewRecord.profile_version == profile.version,
            HomeZoneChangePreviewRecord.state == "pending",
            HomeZoneChangePreviewRecord.expires_at > now,
        )
        .with_for_update()
    )
    record = await session.scalar(statement)
    if record is None or record.profile_version != profile.version or record.expires_at <= now:
        return None
    return record


def _confirm_target(value: str) -> tuple[UUID, int, str]:
    pieces = value.split("|", maxsplit=2)
    if len(pieces) != 3 or len(pieces[2]) != 64:
        raise HomeZoneChangeRejected("home-zone confirmation target is invalid")
    return UUID(pieces[0]), int(pieces[1]), pieces[2]


def _preview_page_target(value: str) -> tuple[UUID, str, int]:
    pieces = value.split("|", maxsplit=2)
    if len(pieces) != 3 or len(pieces[1]) != 64:
        raise HomeZoneChangeRejected("home-zone preview page is invalid")
    return UUID(pieces[0]), pieces[1], int(pieces[2])


def _page_target(value: str, *, prefix: str) -> int:
    role, raw_page = value.split("|", maxsplit=1)
    if role != prefix:
        raise HomeZoneChangeRejected("home-zone page callback is invalid")
    page = int(raw_page)
    if page < 0:
        raise HomeZoneChangeRejected("home-zone page cannot be negative")
    return page


def _flow(draft: OnboardingDraft) -> dict[str, object]:
    return _flow_from_values(draft.values)


def _flow_from_values(values: dict[str, object]) -> dict[str, object]:
    raw = values.get(_FLOW_KEY)
    if not isinstance(raw, dict):
        raise HomeZoneChangeRejected("home-zone choice flow is no longer active")
    return dict(raw)


def _saved_draft_values(profile: PreferencesSnapshot) -> dict[str, object]:
    return {
        "language": profile.language,
        "home_zone": profile.home_zone,
        "reminder_calendar_days": profile.reminder_calendar_days,
        "reminder_elapsed_hours": profile.reminder_elapsed_hours,
        "quiet_start": profile.quiet_start.isoformat(),
        "quiet_end": profile.quiet_end.isoformat(),
        "review_time": profile.review_time.isoformat(),
    }


def _cancelled_result(
    profile: PreferencesSnapshot, draft: OnboardingDraft
) -> HomeZoneRuntimeResult:
    language = profile.language
    text = (
        "Settings cancelled. Your saved profile and plans are unchanged."
        if language == "en"
        else "Настройки отменены. Профиль и планы не изменились."
    )
    return HomeZoneRuntimeResult(
        OnboardingResult(
            "cancelled", mode=draft.mode, preferences=profile, draft=dict(draft.values)
        ),
        _reply(text),
    )


def _stale_result(
    profile: PreferencesSnapshot | None, values: dict[str, object] | None
) -> HomeZoneRuntimeResult:
    language = profile.language if profile else _language(values or {})
    text = (
        "This settings review changed or expired. Send /settings to review the latest version."
        if language == "en"
        else "Эта проверка настроек устарела. Отправьте /settings, чтобы открыть актуальную версию."
    )
    return HomeZoneRuntimeResult(
        OnboardingResult("draft_conflict", "confirm", "preferences", profile, draft=values),
        _reply(text),
    )


def _expired_result(
    profile: PreferencesSnapshot | None, values: dict[str, object] | None
) -> HomeZoneRuntimeResult:
    return _stale_result(profile, values)


def _confirmed_reply(profile: PreferencesSnapshot) -> OnboardingReply:
    text = (
        f"Preferences saved. Home zone: {profile.home_zone}. "
        "Selected schedules and reminder updates were applied."
        if profile.language == "en"
        else f"Настройки сохранены. Домашний пояс: {profile.home_zone}. "
        "Выбранные расписания и напоминания обновлены."
    )
    return _reply(text, (_button(profile.language, "Edit preferences", "settings", "settings"),))


def _button(language: str, text: str, action: str, value: str) -> OnboardingButton:
    return OnboardingButton(text, action, value)


def _reply(text: str, buttons: tuple[OnboardingButton, ...] = ()) -> OnboardingReply:
    return OnboardingReply(escape_markdown_v2(text), text, buttons=buttons)


def _language(values: dict[str, object]) -> str:
    return "ru" if values.get("language") == "ru" else "en"


def _object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _array(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _int(value: object, default: int) -> int:
    return value if type(value) is int else default
