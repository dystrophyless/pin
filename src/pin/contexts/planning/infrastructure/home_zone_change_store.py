"""Owner-scoped SQL projections and atomic schedule qualification for zone changes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.identity.api import Preferences
from pin.contexts.planning.application.home_zone_contracts import (
    HomeZoneProjection,
    HomeZoneReminderChoiceTarget,
    SavedHomeZonePreview,
)
from pin.contexts.planning.application.reminder_policy import apply_quiet_policy
from pin.contexts.planning.domain.plans import (
    OffsetKind,
    OneOffPlanDraft,
    PlanKind,
    ReminderNotice,
    ReminderOffset,
    ReminderOmission,
    ReminderResolution,
    ResolvedSchedule,
    resolve_reminder_projection,
    resolve_schedule,
)
from pin.contexts.planning.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    recurrence_date,
)
from pin.contexts.planning.infrastructure.home_zone_change_models import (
    HomeZoneChangePreviewRecord,
)
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.contexts.planning.infrastructure.series_models import (
    SeriesOccurrenceRecord,
    SeriesRecord,
)
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event

MAX_ZONE_OCCURRENCES = 250
MAX_ZONE_SERIES = 100


@dataclass(frozen=True, slots=True)
class _Occurrence:
    record: OccurrenceRecord
    item: PlanItemRecord
    series_id: UUID | None
    series_version: int | None
    reminders: tuple[ReminderRuleRecord, ...]
    schedule: ResolvedSchedule
    resolution: ReminderResolution
    notices: tuple[ReminderNotice, ...]
    unresolved: bool


@dataclass(frozen=True, slots=True)
class _Series:
    record: SeriesRecord
    next_local_date: date
    event_zone: str
    effective_reminders: tuple[ReminderOffset, ...]
    approved_preferences_version: int
    quiet_zone: str
    quiet_start: time
    quiet_end: time
    unresolved: bool


class SqlAlchemyHomeZoneChangeStore:
    """Caller-UoW store; it never commits or performs external I/O."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._loaded_occurrences: tuple[_Occurrence, ...] = ()
        self._loaded_series: tuple[_Series, ...] = ()

    async def save_preview(self, preview: SavedHomeZonePreview) -> None:
        self._session.add(
            HomeZoneChangePreviewRecord(
                id=preview.id,
                owner_id=preview.owner_id,
                profile_version=preview.profile_version,
                preview_version=preview.preview_version,
                projection_sha256=preview.projection_sha256,
                payload=preview.payload,
                state=preview.state,
                created_at=preview.created_at,
                expires_at=preview.expires_at,
            )
        )
        await self._session.flush()

    async def lock_preview(self, owner_id: UUID, preview_id: UUID) -> SavedHomeZonePreview | None:
        record = await self._session.scalar(
            select(HomeZoneChangePreviewRecord)
            .where(
                HomeZoneChangePreviewRecord.owner_id == owner_id,
                HomeZoneChangePreviewRecord.id == preview_id,
            )
            .with_for_update()
        )
        if record is None:
            return None
        return SavedHomeZonePreview(
            record.id,
            record.owner_id,
            record.profile_version,
            record.preview_version,
            record.projection_sha256,
            record.payload,
            record.state,
            record.created_at,
            record.expires_at,
        )

    async def mark_preview_confirmed(
        self, *, owner_id: UUID, preview_id: UUID, at: datetime
    ) -> None:
        record = await self._session.scalar(
            select(HomeZoneChangePreviewRecord)
            .where(
                HomeZoneChangePreviewRecord.owner_id == owner_id,
                HomeZoneChangePreviewRecord.id == preview_id,
            )
            .with_for_update()
        )
        if record is None or record.state != "pending":
            raise ValueError("home-zone preview is no longer pending")
        record.state = "confirmed"
        record.confirmed_at = at

    async def project(
        self,
        *,
        owner_id: UUID,
        at: datetime,
        current_home_zone: str,
        preferences: Preferences,
        reinterpret_occurrence_ids: tuple[UUID, ...],
        reinterpret_series_ids: tuple[UUID, ...],
        reminder_overrides: dict[UUID, tuple[ReminderOffset, ...]],
        lock: bool,
    ) -> HomeZoneProjection:
        current_local_date = at.astimezone(ZoneInfo(current_home_zone)).date()
        query = (
            select(OccurrenceRecord, PlanItemRecord, SeriesOccurrenceRecord, SeriesRecord)
            .join(
                PlanItemRecord,
                (PlanItemRecord.id == OccurrenceRecord.plan_item_id)
                & (PlanItemRecord.owner_id == OccurrenceRecord.owner_id),
            )
            .outerjoin(
                SeriesOccurrenceRecord,
                (SeriesOccurrenceRecord.occurrence_id == OccurrenceRecord.id)
                & (SeriesOccurrenceRecord.owner_id == OccurrenceRecord.owner_id),
            )
            .outerjoin(
                SeriesRecord,
                (SeriesRecord.id == SeriesOccurrenceRecord.series_id)
                & (SeriesRecord.owner_id == SeriesOccurrenceRecord.owner_id),
            )
            .where(
                OccurrenceRecord.owner_id == owner_id,
                PlanItemRecord.owner_id == owner_id,
                OccurrenceRecord.state == "open",
                (OccurrenceRecord.starts_at >= at)
                | (OccurrenceRecord.deadline_at >= at)
                | (
                    (OccurrenceRecord.kind == PlanKind.UNTIMED.value)
                    & (OccurrenceRecord.local_date >= current_local_date)
                ),
            )
            .order_by(OccurrenceRecord.local_date, OccurrenceRecord.local_time, OccurrenceRecord.id)
        )
        if lock:
            query = query.with_for_update(of=(OccurrenceRecord, PlanItemRecord))
        rows = (await self._session.execute(query.limit(MAX_ZONE_OCCURRENCES + 1))).all()
        if len(rows) > MAX_ZONE_OCCURRENCES:
            raise ValueError(
                "home-zone preview exceeds 250 future occurrences; no changes were made"
            )
        occurrences: list[_Occurrence] = []
        unresolved: list[HomeZoneReminderChoiceTarget] = []
        occurrence_ids = {row[0].id for row in rows}
        if not set(reinterpret_occurrence_ids).issubset(occurrence_ids):
            raise ValueError(
                "a selected occurrence is missing, not future, or not owned by this user"
            )
        for occurrence, item, link, series in rows:
            rule_query = (
                select(ReminderRuleRecord)
                .where(
                    ReminderRuleRecord.owner_id == owner_id,
                    ReminderRuleRecord.occurrence_id == occurrence.id,
                )
                .order_by(ReminderRuleRecord.offset_kind, ReminderRuleRecord.offset_amount)
            )
            if lock:
                rule_query = rule_query.with_for_update()
            rules = tuple((await self._session.scalars(rule_query)).all())
            offsets = reminder_overrides.get(occurrence.id)
            if offsets is None and link is not None:
                offsets = reminder_overrides.get(link.series_id)
            untimed = occurrence.kind == PlanKind.UNTIMED.value
            missing = (
                not rules and occurrence.reminder_preferences_version is None and offsets is None
            ) and not untimed
            if missing:
                unresolved.append(
                    HomeZoneReminderChoiceTarget("occurrence", occurrence.id, item.title)
                )
                offsets = ()
            if untimed:
                offsets = ()
            elif offsets is None:
                offsets = tuple(
                    ReminderOffset(OffsetKind(rule.offset_kind), rule.offset_amount, rule.source)
                    for rule in rules
                )
            schedule = _resolved_schedule(occurrence)
            if occurrence.id in reinterpret_occurrence_ids or (
                link is not None and link.series_id in reinterpret_series_ids
            ):
                schedule = resolve_schedule(
                    _draft_from_record(item, occurrence, preferences.home_zone),
                    default_zone=preferences.home_zone,
                    now=at,
                )
            resolution = resolve_reminder_projection(schedule, offsets, now=at)
            resolution, notices = apply_quiet_policy(
                schedule,
                resolution,
                now=at,
                quiet_zone=preferences.home_zone,
                quiet_start=preferences.quiet_start,
                quiet_end=preferences.quiet_end,
            )
            occurrences.append(
                _Occurrence(
                    occurrence,
                    item,
                    link.series_id if link is not None else None,
                    series.version if series is not None else None,
                    rules,
                    schedule,
                    resolution,
                    notices,
                    missing,
                )
            )

        linked_future_series_ids = {link.series_id for _, _, link, _ in rows if link is not None}
        series_query = (
            select(SeriesRecord)
            .where(
                SeriesRecord.owner_id == owner_id,
                or_(
                    SeriesRecord.state == "active",
                    SeriesRecord.id.in_(linked_future_series_ids),
                ),
            )
            .order_by(SeriesRecord.id)
        )
        if lock:
            series_query = series_query.with_for_update()
        series_records = (
            await self._session.scalars(series_query.limit(MAX_ZONE_SERIES + 1))
        ).all()
        if len(series_records) > MAX_ZONE_SERIES:
            raise ValueError("home-zone preview exceeds 100 active series; no changes were made")
        series_records = [
            record
            for record in series_records
            if record.typed_draft.get("local_time") is not None
            or _next_series_date(record) >= current_local_date
            or record.id in linked_future_series_ids
        ]
        series_ids = {record.id for record in series_records}
        if not set(reinterpret_series_ids).issubset(series_ids):
            raise ValueError("a selected series is missing or not owned by this user")
        if not set(reminder_overrides).issubset(occurrence_ids | series_ids):
            raise ValueError("a reminder override target is missing, not future, or not owned")
        series_changes: list[_Series] = []
        for record in series_records:
            raw = record.typed_draft
            effective = _restore_offsets(raw.get("effective_reminders"))
            untimed_series = raw.get("local_time") is None
            override = reminder_overrides.get(record.id)
            if untimed_series:
                effective = ()
            elif override is not None:
                effective = override
            missing = (
                (
                    raw.get("approved_preferences_version") is None
                    or raw.get("quiet_zone") is None
                    or (not effective and raw.get("effective_reminders") is None)
                )
                and override is None
                and not untimed_series
            )
            if missing:
                unresolved.append(
                    HomeZoneReminderChoiceTarget(
                        "series", record.id, str(raw.get("title") or "Recurring plan")
                    )
                )
                effective = ()
            event_zone = (
                preferences.home_zone
                if record.id in reinterpret_series_ids
                else str(raw.get("default_zone") or preferences.home_zone)
            )
            series_changes.append(
                _Series(
                    record,
                    _next_series_date(record),
                    event_zone,
                    effective,
                    preferences.version,
                    preferences.home_zone,
                    preferences.quiet_start,
                    preferences.quiet_end,
                    missing,
                )
            )

        self._loaded_occurrences = tuple(occurrences)
        self._loaded_series = tuple(series_changes)
        return HomeZoneProjection(
            payload={
                "candidate_profile_version": preferences.version,
                "candidate_home_zone": preferences.home_zone,
                "candidate_quiet_zone": preferences.home_zone,
                "candidate_quiet_start": preferences.quiet_start.isoformat(),
                "candidate_quiet_end": preferences.quiet_end.isoformat(),
                "reinterpret_occurrence_ids": sorted(
                    str(value) for value in reinterpret_occurrence_ids
                ),
                "reinterpret_series_ids": sorted(str(value) for value in reinterpret_series_ids),
                "occurrences": [_occurrence_payload(item) for item in occurrences],
                "series": [_series_payload(item) for item in series_changes],
                "unresolved_reminder_targets": [
                    {
                        "target_type": value.target_type,
                        "target_id": str(value.target_id),
                        "title": value.title,
                    }
                    for value in unresolved
                ],
            },
            unresolved_reminder_targets=tuple(unresolved),
        )

    async def apply_projection(
        self,
        *,
        owner_id: UUID,
        projection: HomeZoneProjection,
        candidate_preferences: Preferences,
        at: datetime,
    ) -> dict[str, object]:
        updated_occurrences: list[str] = []
        for item in self._loaded_occurrences:
            row = item.record
            if row.owner_id != owner_id or row.state != "open" or row.version < 1:
                raise ValueError("a selected occurrence changed before confirmation")
            _apply_schedule(row, item.schedule)
            row.version += 1
            row.reminder_preferences_version = candidate_preferences.version
            desired = {
                (entry.offset.kind.value, entry.offset.amount, entry.offset.source): entry.due_at
                for entry in item.resolution.scheduled
            }
            current = {
                (entry.offset_kind, entry.offset_amount, entry.source): entry
                for entry in item.reminders
            }
            for key, rule in current.items():
                due = desired.pop(key, None)
                if due is None:
                    await self._session.delete(rule)
                else:
                    rule.due_at = due
            for (kind, amount, source), due in desired.items():
                self._session.add(
                    ReminderRuleRecord(
                        owner_id=owner_id,
                        occurrence_id=row.id,
                        offset_kind=kind,
                        offset_amount=amount,
                        source=source,
                        due_at=due,
                    )
                )
            await add_outbox_event(
                self._session,
                EventEnvelope(
                    id=uuid4(),
                    event_type="planning.plan_rescheduled",
                    schema_version=1,
                    aggregate_id=row.id,
                    aggregate_version=row.version,
                    owner_id=owner_id,
                    occurred_at=at,
                    payload={
                        "plan_item_id": str(row.plan_item_id),
                        "event_zone": row.event_zone,
                        "schedule": _schedule_payload(row),
                        "reminders": [
                            _reminder_payload(offset, due)
                            for offset, due in _resolution_reminders(item.resolution)
                        ],
                        "omitted_reminders": [
                            _omission_payload(value) for value in item.resolution.omitted
                        ],
                        "reminder_notices": [_notice_payload(value) for value in item.notices],
                        "reason": "home_zone_change",
                    },
                ),
            )
            updated_occurrences.append(str(row.id))

        updated_series: list[str] = []
        for series_projection in self._loaded_series:
            series_row = series_projection.record
            raw = dict(series_row.typed_draft)
            if series_row.owner_id != owner_id or series_row.state not in {"active", "stopped"}:
                raise ValueError("a selected recurrence changed before confirmation")
            # The occurrence event aggregate is the occurrence; series policy is
            # independently versioned so consumers can invalidate a series once.
            raw["default_zone"] = series_projection.event_zone
            raw["effective_reminders"] = [
                {"kind": offset.kind.value, "amount": offset.amount, "source": offset.source}
                for offset in series_projection.effective_reminders
            ]
            raw["approved_preferences_version"] = candidate_preferences.version
            raw["quiet_zone"] = candidate_preferences.home_zone
            raw["quiet_start"] = candidate_preferences.quiet_start.isoformat()
            raw["quiet_end"] = candidate_preferences.quiet_end.isoformat()
            if series_row.id in {
                UUID(value)
                for value in _string_values(projection.payload.get("reinterpret_series_ids"))
            }:
                raw["event_zone"] = candidate_preferences.home_zone
            series_row.typed_draft = raw
            series_row.version += 1
            await add_outbox_event(
                self._session,
                EventEnvelope(
                    id=uuid4(),
                    event_type="planning.series_approval_updated",
                    schema_version=1,
                    aggregate_id=series_row.id,
                    aggregate_version=series_row.version,
                    owner_id=owner_id,
                    occurred_at=at,
                    payload={
                        "series_id": str(series_row.id),
                        "plan_item_id": str(series_row.plan_item_id),
                        "approved_preferences_version": candidate_preferences.version,
                        "quiet_zone": candidate_preferences.home_zone,
                        "quiet_start": candidate_preferences.quiet_start.isoformat(),
                        "quiet_end": candidate_preferences.quiet_end.isoformat(),
                        "event_zone": series_projection.event_zone,
                        "reason": "home_zone_change",
                    },
                ),
            )
            updated_series.append(str(series_row.id))
        await self._session.flush()
        return {"occurrence_ids": updated_occurrences, "series_ids": updated_series}


def _resolved_schedule(row: OccurrenceRecord) -> ResolvedSchedule:
    return ResolvedSchedule(
        kind=PlanKind(row.kind),
        event_zone=row.event_zone,
        local_date=row.local_date,
        local_time=row.local_time,
        fold=row.fold,
        end_local_date=row.end_local_date,
        end_local_time=row.end_local_time,
        end_fold=row.end_fold,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        deadline_at=row.deadline_at,
        duration_seconds=row.duration_seconds,
    )


def _draft_from_record(item: PlanItemRecord, row: OccurrenceRecord, zone: str) -> OneOffPlanDraft:
    return OneOffPlanDraft(
        kind=PlanKind(row.kind),
        title=item.title,
        local_date=row.local_date,
        local_time=row.local_time,
        event_zone=zone,
        end_local_date=row.end_local_date,
        end_local_time=row.end_local_time,
        duration_seconds=row.duration_seconds,
        fold=row.fold,
        end_fold=row.end_fold,
        reminders=(),
    )


def _apply_schedule(row: OccurrenceRecord, schedule: ResolvedSchedule) -> None:
    row.kind = schedule.kind.value
    row.event_zone = schedule.event_zone
    row.local_date = schedule.local_date
    row.local_time = schedule.local_time
    row.fold = schedule.fold
    row.end_local_date = schedule.end_local_date
    row.end_local_time = schedule.end_local_time
    row.end_fold = schedule.end_fold
    row.starts_at = schedule.starts_at
    row.ends_at = schedule.ends_at
    row.deadline_at = schedule.deadline_at
    row.duration_seconds = schedule.duration_seconds


def _restore_offsets(raw: object) -> tuple[ReminderOffset, ...]:
    if not isinstance(raw, list):
        return ()
    restored: list[ReminderOffset] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("stored recurrence reminder is invalid")
        restored.append(
            ReminderOffset(
                OffsetKind(str(item["kind"])),
                _json_int(item.get("amount")),
                str(item["source"]),
            )
        )
    return tuple(restored)


def _next_series_date(record: SeriesRecord) -> date:
    raw = record.rule
    end_date = date.fromisoformat(str(raw["end_date"])) if raw.get("end_date") else None
    weekdays = _string_or_int_values(raw.get("weekdays"))
    rule = RecurrenceRule(
        frequency=RecurrenceFrequency(str(raw["frequency"])),
        interval=_json_int(raw["interval"]),
        weekdays=tuple(_json_int(value) for value in weekdays),
        monthly_day=(_json_int(raw["monthly_day"]) if raw.get("monthly_day") is not None else None),
        last_day=bool(raw["last_day"]),
        count=_json_int(raw["count"]) if raw.get("count") is not None else None,
        end_date=end_date,
    )
    sequence_offset = record.next_sequence - record.anchor_sequence
    return recurrence_date(record.anchor_date, rule, sequence_offset)


def _json_int(value: object) -> int:
    if type(value) is int:
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    raise ValueError("stored recurrence number is invalid")


def _string_or_int_values(value: object) -> list[object]:
    if not isinstance(value, list) or any(
        type(item) is not int and not isinstance(item, str) for item in value
    ):
        raise ValueError("stored recurrence weekdays are invalid")
    return value


def _string_values(value: object) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("stored home-zone scope is invalid")
    return value


def _occurrence_payload(value: _Occurrence) -> dict[str, object]:
    return {
        "occurrence_id": str(value.record.id),
        "occurrence_version": value.record.version,
        "plan_item_id": str(value.item.id),
        "plan_version": value.item.version,
        "series_id": str(value.series_id) if value.series_id else None,
        "series_version": value.series_version,
        "state": value.record.state,
        "title": value.item.title,
        "before": _schedule_payload(value.record),
        "after": _schedule_payload(value.schedule),
        "reminders": [
            _reminder_payload(item.offset, item.due_at) for item in value.resolution.scheduled
        ],
        "omitted_reminders": [_omission_payload(item) for item in value.resolution.omitted],
        "reminder_notices": [_notice_payload(item) for item in value.notices],
        "unresolved_reminders": value.unresolved,
    }


def _series_payload(value: _Series) -> dict[str, object]:
    return {
        "series_id": str(value.record.id),
        "series_version": value.record.version,
        "state": value.record.state,
        "next_sequence": value.record.next_sequence,
        "anchor_date": value.record.anchor_date.isoformat(),
        "anchor_sequence": value.record.anchor_sequence,
        "total_count": value.record.total_count,
        "end_date": value.record.end_date.isoformat() if value.record.end_date else None,
        "recurrence_rule": value.record.rule,
        "next_local_date": value.next_local_date.isoformat(),
        "before_default_zone": value.record.typed_draft.get("default_zone"),
        "after_default_zone": value.event_zone,
        "before_approved_preferences_version": value.record.typed_draft.get(
            "approved_preferences_version"
        ),
        "after_approved_preferences_version": value.approved_preferences_version,
        "before_quiet_policy": {
            "zone": value.record.typed_draft.get("quiet_zone"),
            "start": value.record.typed_draft.get("quiet_start"),
            "end": value.record.typed_draft.get("quiet_end"),
        },
        "after_quiet_policy": {
            "zone": value.quiet_zone,
            "start": value.quiet_start.isoformat(),
            "end": value.quiet_end.isoformat(),
        },
        "before_effective_reminders": value.record.typed_draft.get("effective_reminders"),
        "after_effective_reminders": [
            {"kind": item.kind.value, "amount": item.amount, "source": item.source}
            for item in value.effective_reminders
        ],
        "unresolved_reminders": value.unresolved,
        "preserves_anchor_rule_count_end_and_history": True,
    }


def _schedule_payload(value: OccurrenceRecord | ResolvedSchedule) -> dict[str, object]:
    return {
        "kind": PlanKind(value.kind).value,
        "event_zone": value.event_zone,
        "local_date": value.local_date.isoformat(),
        "local_time": value.local_time.isoformat() if value.local_time else None,
        "fold": value.fold,
        "end_local_date": value.end_local_date.isoformat() if value.end_local_date else None,
        "end_local_time": value.end_local_time.isoformat() if value.end_local_time else None,
        "end_fold": value.end_fold,
        "starts_at": value.starts_at.astimezone(UTC).isoformat() if value.starts_at else None,
        "ends_at": value.ends_at.astimezone(UTC).isoformat() if value.ends_at else None,
        "deadline_at": value.deadline_at.astimezone(UTC).isoformat() if value.deadline_at else None,
        "duration_seconds": value.duration_seconds,
    }


def _reminder_payload(offset: ReminderOffset, due_at: datetime) -> dict[str, object]:
    return {
        "kind": offset.kind.value,
        "amount": offset.amount,
        "source": offset.source,
        "due_at": due_at.astimezone(UTC).isoformat(),
    }


def _omission_payload(value: ReminderOmission) -> dict[str, object]:
    return {
        "kind": value.offset.kind.value,
        "amount": value.offset.amount,
        "source": value.offset.source,
        "due_at": value.due_at.astimezone(UTC).isoformat(),
        "reason": value.reason,
    }


def _notice_payload(value: ReminderNotice) -> dict[str, object]:
    return {
        "kind": value.kind,
        "offset_kind": value.offset.kind.value,
        "offset_amount": value.offset.amount,
        "source": value.offset.source,
        "original_due_at": value.original_due_at.astimezone(UTC).isoformat(),
        "due_at": value.due_at.astimezone(UTC).isoformat(),
    }


def _resolution_reminders(value: ReminderResolution) -> tuple[tuple[ReminderOffset, datetime], ...]:
    return tuple((item.offset, item.due_at) for item in value.scheduled)
