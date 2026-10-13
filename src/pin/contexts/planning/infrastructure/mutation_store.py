"""Caller-owned SQL adapter for durable Planning mutation previews."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.identity.api import PreferencesSnapshot
from pin.contexts.planning.application.mutation_contracts import MutationStore
from pin.contexts.planning.domain.mutations import (
    MutationAction,
    MutationCancellationProjection,
    MutationCancellationTarget,
    MutationPreview,
    MutationReminderProjection,
    MutationRequest,
    MutationScope,
    MutationTarget,
)
from pin.contexts.planning.domain.plans import (
    OccurrenceState,
    OffsetKind,
    OneOffPlanDraft,
    PlanKind,
    ReminderNotice,
    ReminderOffset,
    ReminderOmission,
    ReminderSchedule,
    ResolvedSchedule,
)
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.contexts.planning.infrastructure.mutation_models import MutationPreviewRecord
from pin.contexts.planning.infrastructure.series_models import (
    SeriesOccurrenceRecord,
    SeriesRecord,
)
from pin.contexts.planning.infrastructure.series_store import SqlAlchemySeriesStore
from pin.contexts.planning.infrastructure.store import SqlAlchemyPlanningStore
from pin.contexts.planning.recurrence_api import SeriesSnapshot
from pin.persistence.receipts import find_action_receipt, lock_action, record_action_receipt
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event


class SqlAlchemyMutationStore(MutationStore):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_target(
        self, *, owner_id: UUID, occurrence_id: UUID, lock: bool = False
    ) -> MutationTarget | None:
        statement = (
            select(OccurrenceRecord, PlanItemRecord, SeriesOccurrenceRecord)
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
            .where(OccurrenceRecord.id == occurrence_id, OccurrenceRecord.owner_id == owner_id)
        )
        if lock:
            statement = statement.with_for_update(of=OccurrenceRecord).execution_options(
                populate_existing=True
            )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        occurrence, item, series_link = row
        reminder_statement = (
            select(ReminderRuleRecord)
            .where(
                ReminderRuleRecord.owner_id == owner_id,
                ReminderRuleRecord.occurrence_id == occurrence_id,
            )
            .order_by(
                ReminderRuleRecord.offset_kind,
                ReminderRuleRecord.offset_amount,
                ReminderRuleRecord.source,
            )
        )
        if lock:
            reminder_statement = reminder_statement.with_for_update().execution_options(
                populate_existing=True
            )
        reminder_rows = (await self._session.scalars(reminder_statement)).all()
        return MutationTarget(
            owner_id=owner_id,
            occurrence_id=occurrence.id,
            plan_item_id=item.id,
            title=item.title,
            plan_version=item.version,
            occurrence_version=occurrence.version,
            approved_preferences_version=occurrence.reminder_preferences_version,
            state=OccurrenceState(occurrence.state),
            schedule=_resolved_schedule(occurrence),
            reminder_offsets=tuple(
                ReminderOffset(OffsetKind(item.offset_kind), item.offset_amount, item.source)
                for item in reminder_rows
            ),
            recurrence_series_id=series_link.series_id if series_link else None,
            recurrence_sequence=series_link.sequence if series_link else None,
        )

    async def save_preview(self, preview: MutationPreview) -> None:
        self._session.add(
            MutationPreviewRecord(
                id=preview.preview_id,
                owner_id=preview.owner_id,
                occurrence_id=preview.target.occurrence_id,
                occurrence_version=preview.target.occurrence_version,
                preferences_version=preview.expected_preferences_version,
                preview_version=preview.preview_version,
                action=preview.request.action.value,
                scope=preview.request.scope.value,
                projection_sha256=preview.approval_projection_sha256,
                payload=_preview_payload(preview),
                state="pending",
                created_at=preview.created_at.astimezone(UTC),
                expires_at=preview.expires_at.astimezone(UTC),
            )
        )
        await self._session.flush()

    async def get_receipt(self, *, owner_id: UUID, preview_id: UUID) -> dict[str, object] | None:
        receipt = await find_action_receipt(self._session, owner_id, str(preview_id))
        return dict(receipt.result) if receipt is not None else None

    async def get_preview(
        self, *, owner_id: UUID, preview_id: UUID, lock: bool = False
    ) -> MutationPreview | None:
        statement = select(MutationPreviewRecord).where(
            MutationPreviewRecord.id == preview_id,
            MutationPreviewRecord.owner_id == owner_id,
            MutationPreviewRecord.state == "pending",
        )
        if lock:
            statement = statement.with_for_update()
        row = await self._session.scalar(statement)
        if row is None:
            return None
        return _restore_preview(row)

    async def get_series_snapshot(
        self, *, owner_id: UUID, series_id: UUID, lock: bool = False
    ) -> SeriesSnapshot | None:
        series_store = SqlAlchemySeriesStore(self._session)
        snapshot = (
            await series_store.lock_series_snapshot(owner_id, series_id)
            if lock
            else await series_store.get_series_snapshot(owner_id, series_id)
        )
        if snapshot is None or not lock:
            return snapshot
        links = (
            await self._session.scalars(
                select(SeriesOccurrenceRecord)
                .where(
                    SeriesOccurrenceRecord.owner_id == owner_id,
                    SeriesOccurrenceRecord.series_id == series_id,
                )
                .order_by(SeriesOccurrenceRecord.sequence)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
        occurrence_ids = [link.occurrence_id for link in links]
        if occurrence_ids:
            await self._session.scalars(
                select(OccurrenceRecord)
                .where(
                    OccurrenceRecord.owner_id == owner_id,
                    OccurrenceRecord.id.in_(occurrence_ids),
                )
                .order_by(OccurrenceRecord.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        refreshed = await series_store.get_series_snapshot(owner_id, series_id)
        return refreshed

    async def apply(
        self, preview: MutationPreview, *, preferences: PreferencesSnapshot, at: datetime
    ) -> dict[str, object]:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("at must be timezone-aware")
        action_key = str(preview.preview_id)
        await lock_action(self._session, preview.owner_id, action_key)
        receipt = await find_action_receipt(self._session, preview.owner_id, action_key)
        if receipt is not None:
            return dict(receipt.result)
        row = await self._session.scalar(
            select(MutationPreviewRecord)
            .where(
                MutationPreviewRecord.id == preview.preview_id,
                MutationPreviewRecord.owner_id == preview.owner_id,
                MutationPreviewRecord.state == "pending",
            )
            .with_for_update()
        )
        if row is None or row.expires_at <= at:
            raise ValueError("mutation preview is missing, consumed, or expired")
        if (
            row.preview_version != preview.preview_version
            or row.projection_sha256 != preview.approval_projection_sha256
        ):
            raise ValueError("mutation preview changed")

        if preview.request.action is MutationAction.RESCHEDULE:
            assert preview.replacement_schedule is not None
            if preview.target.recurrence_series_id is not None:
                series_projection = preview.series_projection
                if series_projection is None:
                    raise ValueError("series reschedule preview is missing its projection")
                series_store = SqlAlchemySeriesStore(self._session)
                snapshot = await series_store.lock_series_snapshot(
                    preview.owner_id, preview.target.recurrence_series_id
                )
                if snapshot is None or snapshot.version != series_projection.series_version:
                    raise ValueError("recurrence series changed after preview")
                result = await series_store.apply_exception(
                    owner_id=preview.owner_id,
                    action_key=f"exception:{preview.preview_id}",
                    payload_sha256=preview.approval_projection_sha256,
                    snapshot=snapshot,
                    change=series_projection.change,
                    projection=series_projection.projection,
                    new_draft=series_projection.updated_draft,
                    approved_preferences_version=preferences.version,
                    quiet_zone=preferences.home_zone,
                    quiet_start=preferences.quiet_start,
                    quiet_end=preferences.quiet_end,
                    approval_projection_sha256=series_projection.approval_projection_sha256,
                    at=at,
                )
                result.update(
                    {
                        "preview_id": str(preview.preview_id),
                        "action": "reschedule",
                        "occurrence_id": str(preview.target.occurrence_id),
                        "state": preview.target.state.value,
                    }
                )
            else:
                updated = await SqlAlchemyPlanningStore(
                    self._session
                )._reschedule_one_off_occurrence(
                    owner_id=preview.owner_id,
                    occurrence_id=preview.target.occurrence_id,
                    expected_version=preview.target.occurrence_version,
                    approved_preferences_version=preferences.version,
                    schedule=preview.replacement_schedule,
                    reminder_offsets=tuple(
                        preview.request.replacement.reminders
                        if preview.request.replacement
                        and preview.request.replacement.reminders is not None
                        else preview.target.reminder_offsets
                    ),
                    quiet_zone=preferences.home_zone,
                    quiet_start=preferences.quiet_start,
                    quiet_end=preferences.quiet_end,
                    at=at,
                )
                result = {
                    "preview_id": str(preview.preview_id),
                    "action": preview.request.action.value,
                    "occurrence_id": str(updated.id),
                    "occurrence_version": updated.version,
                    "state": updated.state.value,
                    "committed_at": at.astimezone(UTC).isoformat(),
                }
        elif preview.cancellation_projection is not None:
            cancelled = await self._cancel_future_series(preview, action_key=action_key, at=at)
            result = cancelled
        else:
            occurrence = await self._session.scalar(
                select(OccurrenceRecord)
                .where(
                    OccurrenceRecord.id == preview.target.occurrence_id,
                    OccurrenceRecord.owner_id == preview.owner_id,
                    OccurrenceRecord.version == preview.target.occurrence_version,
                    OccurrenceRecord.state == "open",
                )
                .with_for_update()
            )
            if occurrence is None:
                raise ValueError("occurrence changed after preview")
            state = (
                OccurrenceState.COMPLETED
                if preview.request.action is MutationAction.COMPLETE
                else OccurrenceState.CANCELLED
            )
            occurrence.state = state.value
            occurrence.version += 1
            await add_outbox_event(
                self._session,
                EventEnvelope(
                    id=uuid4(),
                    event_type=(
                        "planning.occurrence_completed"
                        if state is OccurrenceState.COMPLETED
                        else "planning.occurrence_cancelled"
                    ),
                    schema_version=1,
                    aggregate_id=occurrence.id,
                    aggregate_version=occurrence.version,
                    owner_id=preview.owner_id,
                    occurred_at=at.astimezone(UTC),
                    payload={
                        "plan_item_id": str(occurrence.plan_item_id),
                        "occurrence_id": str(occurrence.id),
                        "state": state.value,
                    },
                ),
            )
            result = {
                "preview_id": str(preview.preview_id),
                "action": preview.request.action.value,
                "occurrence_id": str(occurrence.id),
                "occurrence_version": occurrence.version,
                "state": state.value,
                "committed_at": at.astimezone(UTC).isoformat(),
            }
        row.state = "confirmed"
        row.confirmed_at = at.astimezone(UTC)
        await record_action_receipt(self._session, preview.owner_id, action_key, result)
        await self._session.flush()
        return result

    async def _cancel_future_series(
        self, preview: MutationPreview, *, action_key: str, at: datetime
    ) -> dict[str, object]:
        projection = preview.cancellation_projection
        series_id = preview.target.recurrence_series_id
        if projection is None or series_id is None:
            raise ValueError("future cancellation projection is missing its series")
        series = await self._session.scalar(
            select(SeriesRecord)
            .where(SeriesRecord.id == series_id, SeriesRecord.owner_id == preview.owner_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            series is None
            or series.state != "active"
            or series.version != projection.series_version
        ):
            raise ValueError("recurrence series changed after cancellation preview")

        targets = {item.sequence: item for item in projection.targets}
        links = list(
            (
                await self._session.scalars(
                    select(SeriesOccurrenceRecord)
                    .where(
                        SeriesOccurrenceRecord.owner_id == preview.owner_id,
                        SeriesOccurrenceRecord.series_id == series_id,
                        SeriesOccurrenceRecord.sequence.in_(targets),
                    )
                    .order_by(SeriesOccurrenceRecord.sequence)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        if len(links) != len(targets):
            raise ValueError("future occurrence identities changed after cancellation preview")
        occurrences = list(
            (
                await self._session.scalars(
                    select(OccurrenceRecord)
                    .where(
                        OccurrenceRecord.owner_id == preview.owner_id,
                        OccurrenceRecord.id.in_([link.occurrence_id for link in links]),
                    )
                    .order_by(OccurrenceRecord.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        by_id = {item.id: item for item in occurrences}
        if len(by_id) != len(targets):
            raise ValueError("future occurrence rows changed after cancellation preview")
        for link in links:
            expected = targets[link.sequence]
            occurrence = by_id.get(link.occurrence_id)
            if (
                occurrence is None
                or expected.occurrence_id != link.occurrence_id
                or expected.recurrence_key != link.recurrence_key
                or expected.version != occurrence.version
                or occurrence.state != "open"
            ):
                raise ValueError("future occurrence changed after cancellation preview")

        now = at.astimezone(UTC)
        series.state = "stopped"
        series.version += 1
        cancelled_ids: list[str] = []
        for link in links:
            occurrence = by_id[link.occurrence_id]
            occurrence.state = OccurrenceState.CANCELLED.value
            occurrence.version += 1
            cancelled_ids.append(str(occurrence.id))
            await add_outbox_event(
                self._session,
                EventEnvelope(
                    id=uuid4(),
                    event_type="planning.occurrence_cancelled",
                    schema_version=1,
                    aggregate_id=occurrence.id,
                    aggregate_version=occurrence.version,
                    owner_id=preview.owner_id,
                    occurred_at=now,
                    payload={
                        "plan_item_id": str(occurrence.plan_item_id),
                        "occurrence_id": str(occurrence.id),
                        "state": OccurrenceState.CANCELLED.value,
                    },
                ),
            )
        await add_outbox_event(
            self._session,
            EventEnvelope(
                id=uuid4(),
                event_type="planning.series_stopped",
                schema_version=1,
                aggregate_id=series.id,
                aggregate_version=series.version,
                owner_id=preview.owner_id,
                occurred_at=now,
                payload={
                    "plan_item_id": str(series.plan_item_id),
                    "state": series.state,
                    "next_sequence": series.next_sequence,
                    "reason": "future_occurrences_cancelled",
                },
            ),
        )
        result: dict[str, object] = {
            "preview_id": str(preview.preview_id),
            "action": MutationAction.CANCEL.value,
            "occurrence_id": str(preview.target.occurrence_id),
            "state": OccurrenceState.CANCELLED.value,
            "cancelled_occurrence_ids": cancelled_ids,
            "series_id": str(series.id),
            "series_version": series.version,
            "committed_at": now.isoformat(),
        }
        return result


def _preview_payload(preview: MutationPreview) -> dict[str, object]:
    target = preview.target
    return {
        "target": {
            "plan_item_id": str(target.plan_item_id),
            "title": target.title,
            "plan_version": target.plan_version,
            "approved_preferences_version": target.approved_preferences_version,
            "reminders": [
                {"kind": item.kind.value, "amount": item.amount, "source": item.source}
                for item in target.reminder_offsets
            ],
            "state": target.state.value,
            "schedule": _schedule_payload(target.schedule),
            "series_id": str(target.recurrence_series_id) if target.recurrence_series_id else None,
            "sequence": target.recurrence_sequence,
        },
        "request": {
            "action": preview.request.action.value,
            "scope": preview.request.scope.value,
            "replacement": _draft_payload(preview.request.replacement)
            if preview.request.replacement
            else None,
        },
        "replacement_schedule": _schedule_payload(preview.replacement_schedule)
        if preview.replacement_schedule
        else None,
        "reminder_projection": _reminder_projection_payload(preview.reminder_projection),
        "series_preview_schedules": [
            _schedule_payload(item) for item in preview.series_preview_schedules
        ],
        "series_version": (
            preview.series_projection.series_version if preview.series_projection else None
        ),
        "series_projection_sha256": (
            preview.series_projection.approval_projection_sha256
            if preview.series_projection
            else None
        ),
        "cancellation_projection": (
            {
                "series_version": preview.cancellation_projection.series_version,
                "approval_projection_sha256": (
                    preview.cancellation_projection.approval_projection_sha256
                ),
                "targets": [
                    {
                        "occurrence_id": str(item.occurrence_id),
                        "sequence": item.sequence,
                        "recurrence_key": item.recurrence_key,
                        "version": item.version,
                        "local_date": item.local_date.isoformat(),
                    }
                    for item in preview.cancellation_projection.targets
                ],
            }
            if preview.cancellation_projection
            else None
        ),
    }


def _restore_preview(row: MutationPreviewRecord) -> MutationPreview:
    payload = row.payload
    target_data = cast(dict[str, object], payload["target"])
    request_data = cast(dict[str, object], payload["request"])
    replacement_data = request_data.get("replacement")
    return MutationPreview(
        preview_id=row.id,
        owner_id=row.owner_id,
        target=MutationTarget(
            owner_id=row.owner_id,
            occurrence_id=row.occurrence_id,
            plan_item_id=UUID(cast(str, target_data["plan_item_id"])),
            title=cast(str, target_data["title"]),
            plan_version=_integer(target_data["plan_version"]),
            occurrence_version=row.occurrence_version,
            approved_preferences_version=(
                _integer(target_data["approved_preferences_version"])
                if target_data.get("approved_preferences_version") is not None
                else None
            ),
            reminder_offsets=tuple(
                ReminderOffset(
                    OffsetKind(cast(str, cast(dict[str, object], item)["kind"])),
                    _integer(cast(dict[str, object], item)["amount"]),
                    cast(str, cast(dict[str, object], item)["source"]),
                )
                for item in cast(list[object], target_data.get("reminders", []))
            ),
            state=OccurrenceState(cast(str, target_data["state"])),
            schedule=_schedule_from(cast(dict[str, object], target_data["schedule"])),
            recurrence_series_id=UUID(cast(str, target_data["series_id"]))
            if target_data.get("series_id")
            else None,
            recurrence_sequence=_integer(target_data["sequence"])
            if target_data.get("sequence") is not None
            else None,
        ),
        request=MutationRequest(
            owner_id=row.owner_id,
            occurrence_id=row.occurrence_id,
            expected_occurrence_version=row.occurrence_version,
            action=MutationAction(row.action),
            scope=MutationScope(row.scope),
            replacement=_draft_from(cast(dict[str, object], replacement_data))
            if replacement_data
            else None,
        ),
        replacement_schedule=(
            _schedule_from(cast(dict[str, object], payload["replacement_schedule"]))
        )
        if payload.get("replacement_schedule")
        else None,
        series_projection=None,
        cancellation_projection=_cancellation_projection_from(
            cast(dict[str, object], payload["cancellation_projection"])
        )
        if payload.get("cancellation_projection")
        else None,
        reminder_projection=_reminder_projection_from(
            cast(dict[str, object], payload["reminder_projection"])
        )
        if payload.get("reminder_projection")
        else None,
        series_preview_schedules=tuple(
            _schedule_from(cast(dict[str, object], item))
            for item in cast(list[object], payload.get("series_preview_schedules", []))
        ),
        expected_preferences_version=row.preferences_version,
        preview_version=row.preview_version,
        created_at=row.created_at,
        expires_at=row.expires_at,
        approval_projection_sha256=row.projection_sha256,
    )


def _cancellation_projection_from(
    data: dict[str, object],
) -> MutationCancellationProjection:
    return MutationCancellationProjection(
        series_version=_integer(data["series_version"]),
        approval_projection_sha256=cast(str, data["approval_projection_sha256"]),
        targets=tuple(
            MutationCancellationTarget(
                occurrence_id=UUID(cast(str, cast(dict[str, object], item)["occurrence_id"])),
                sequence=_integer(cast(dict[str, object], item)["sequence"]),
                recurrence_key=cast(str, cast(dict[str, object], item)["recurrence_key"]),
                version=_integer(cast(dict[str, object], item)["version"]),
                local_date=date.fromisoformat(
                    cast(str, cast(dict[str, object], item)["local_date"])
                ),
            )
            for item in cast(list[object], data["targets"])
        ),
    )


def _reminder_projection_payload(
    projection: MutationReminderProjection | None,
) -> dict[str, object] | None:
    if projection is None:
        return None
    return {
        "scheduled": [
            {
                "kind": item.offset.kind.value,
                "amount": item.offset.amount,
                "source": item.offset.source,
                "due_at": item.due_at.isoformat(),
            }
            for item in projection.scheduled
        ],
        "omitted": [
            {
                "kind": item.offset.kind.value,
                "amount": item.offset.amount,
                "source": item.offset.source,
                "due_at": item.due_at.isoformat(),
                "reason": item.reason,
            }
            for item in projection.omitted
        ],
        "notices": [
            {
                "kind": item.kind,
                "offset_kind": item.offset.kind.value,
                "offset_amount": item.offset.amount,
                "source": item.offset.source,
                "original_due_at": item.original_due_at.isoformat(),
                "due_at": item.due_at.isoformat(),
            }
            for item in projection.notices
        ],
    }


def _reminder_projection_from(value: dict[str, object]) -> MutationReminderProjection:
    return MutationReminderProjection(
        scheduled=tuple(
            ReminderSchedule(
                ReminderOffset(
                    OffsetKind(cast(str, cast(dict[str, object], item)["kind"])),
                    _integer(cast(dict[str, object], item)["amount"]),
                    cast(str, cast(dict[str, object], item)["source"]),
                ),
                datetime.fromisoformat(cast(str, cast(dict[str, object], item)["due_at"])),
            )
            for item in cast(list[object], value.get("scheduled", []))
        ),
        omitted=tuple(
            ReminderOmission(
                ReminderOffset(
                    OffsetKind(cast(str, cast(dict[str, object], item)["kind"])),
                    _integer(cast(dict[str, object], item)["amount"]),
                    cast(str, cast(dict[str, object], item)["source"]),
                ),
                datetime.fromisoformat(cast(str, cast(dict[str, object], item)["due_at"])),
                cast(str, cast(dict[str, object], item)["reason"]),
            )
            for item in cast(list[object], value.get("omitted", []))
        ),
        notices=tuple(
            ReminderNotice(
                cast(str, cast(dict[str, object], item)["kind"]),
                ReminderOffset(
                    OffsetKind(cast(str, cast(dict[str, object], item)["offset_kind"])),
                    _integer(cast(dict[str, object], item)["offset_amount"]),
                    cast(str, cast(dict[str, object], item)["source"]),
                ),
                datetime.fromisoformat(cast(str, cast(dict[str, object], item)["original_due_at"])),
                datetime.fromisoformat(cast(str, cast(dict[str, object], item)["due_at"])),
            )
            for item in cast(list[object], value.get("notices", []))
        ),
    )


def _schedule_payload(value: ResolvedSchedule | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "kind": value.kind.value,
        "event_zone": value.event_zone,
        "local_date": value.local_date.isoformat(),
        "local_time": value.local_time.isoformat() if value.local_time else None,
        "fold": value.fold,
        "end_local_date": value.end_local_date.isoformat() if value.end_local_date else None,
        "end_local_time": value.end_local_time.isoformat() if value.end_local_time else None,
        "end_fold": value.end_fold,
        "starts_at": value.starts_at.isoformat() if value.starts_at else None,
        "ends_at": value.ends_at.isoformat() if value.ends_at else None,
        "deadline_at": value.deadline_at.isoformat() if value.deadline_at else None,
        "duration_seconds": value.duration_seconds,
    }


def _schedule_from(value: dict[str, object]) -> ResolvedSchedule:
    return ResolvedSchedule(
        kind=PlanKind(cast(str, value["kind"])),
        event_zone=cast(str, value["event_zone"]),
        local_date=date.fromisoformat(cast(str, value["local_date"])),
        local_time=time.fromisoformat(cast(str, value["local_time"]))
        if value.get("local_time")
        else None,
        fold=cast(str | None, value.get("fold")),
        end_local_date=date.fromisoformat(cast(str, value["end_local_date"]))
        if value.get("end_local_date")
        else None,
        end_local_time=time.fromisoformat(cast(str, value["end_local_time"]))
        if value.get("end_local_time")
        else None,
        end_fold=cast(str | None, value.get("end_fold")),
        starts_at=datetime.fromisoformat(cast(str, value["starts_at"]))
        if value.get("starts_at")
        else None,
        ends_at=datetime.fromisoformat(cast(str, value["ends_at"]))
        if value.get("ends_at")
        else None,
        deadline_at=datetime.fromisoformat(cast(str, value["deadline_at"]))
        if value.get("deadline_at")
        else None,
        duration_seconds=_integer(value["duration_seconds"])
        if value.get("duration_seconds") is not None
        else None,
    )


def _draft_payload(value: OneOffPlanDraft) -> dict[str, object]:
    return {
        "kind": value.kind.value,
        "title": value.title,
        "local_date": value.local_date.isoformat(),
        "local_time": value.local_time.isoformat() if value.local_time else None,
        "event_zone": value.event_zone,
        "end_local_date": value.end_local_date.isoformat() if value.end_local_date else None,
        "end_local_time": value.end_local_time.isoformat() if value.end_local_time else None,
        "duration_seconds": value.duration_seconds,
        "fold": value.fold,
        "end_fold": value.end_fold,
        "reminders": [
            {"kind": reminder.kind.value, "amount": reminder.amount, "source": reminder.source}
            for reminder in value.reminders or ()
        ]
        if value.reminders is not None
        else None,
    }


def _draft_from(value: dict[str, object]) -> OneOffPlanDraft:
    raw_reminders = value.get("reminders")
    return OneOffPlanDraft(
        kind=PlanKind(cast(str, value["kind"])),
        title=cast(str, value["title"]),
        local_date=date.fromisoformat(cast(str, value["local_date"])),
        local_time=time.fromisoformat(cast(str, value["local_time"]))
        if value.get("local_time")
        else None,
        event_zone=cast(str | None, value.get("event_zone")),
        end_local_date=date.fromisoformat(cast(str, value["end_local_date"]))
        if value.get("end_local_date")
        else None,
        end_local_time=time.fromisoformat(cast(str, value["end_local_time"]))
        if value.get("end_local_time")
        else None,
        duration_seconds=_integer(value["duration_seconds"])
        if value.get("duration_seconds") is not None
        else None,
        fold=cast(str | None, value.get("fold")),
        end_fold=cast(str | None, value.get("end_fold")),
        reminders=tuple(
            ReminderOffset(
                OffsetKind(cast(str, cast(dict[str, object], item)["kind"])),
                _integer(cast(dict[str, object], item)["amount"]),
                cast(str, cast(dict[str, object], item)["source"]),
            )
            for item in cast(list[object], raw_reminders)
        )
        if raw_reminders is not None
        else None,
    )


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


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("persisted mutation integers must be JSON integers")
    return value
