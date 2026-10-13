"""Planning-owned PostgreSQL reads and durable agenda page snapshots."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    String,
    Uuid,
    delete,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from pin.contexts.planning.application.agenda import (
    AgendaReadStore,
    AgendaRecord,
    AgendaSnapshot,
    AgendaView,
    AgendaWindow,
)
from pin.contexts.planning.infrastructure.models import OccurrenceRecord, PlanItemRecord
from pin.persistence.models import Base


class AgendaSnapshotRecord(Base):
    """Frozen occurrence membership/order, scoped to its creating owner."""

    __tablename__ = "agenda_snapshots"
    __table_args__ = (
        CheckConstraint("view IN ('today', 'week')", name="ck_planning_agenda_view"),
        Index("ix_planning_agenda_owner_expiry", "owner_id", "expires_at"),
        {"schema": "planning"},
    )

    snapshot_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    view: Mapped[str] = mapped_column(String(8), nullable=False)
    display_zone: Mapped[str] = mapped_column(String(128), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    include_completed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    item_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    page_starts: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SqlAlchemyAgendaReadStore(AgendaReadStore):
    """Query owned plan rows and snapshots through the caller's application UoW."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_candidates(
        self,
        owner_id: UUID,
        *,
        window: AgendaWindow,
        include_completed: bool,
    ) -> tuple[AgendaRecord, ...]:
        states = ("open", "completed") if include_completed else ("open",)
        occurrence = OccurrenceRecord
        statement = (
            select(occurrence, PlanItemRecord.title)
            .join(
                PlanItemRecord,
                (PlanItemRecord.id == occurrence.plan_item_id)
                & (PlanItemRecord.owner_id == occurrence.owner_id),
            )
            .where(
                occurrence.owner_id == owner_id,
                occurrence.state.in_(states),
                or_(
                    occurrence.kind == "untimed",
                    occurrence.starts_at < window.ends_at,
                    occurrence.deadline_at < window.ends_at,
                ),
                or_(
                    occurrence.kind != "untimed",
                    occurrence.local_date < window.end_date,
                ),
            )
            .order_by(occurrence.id)
        )
        rows = (await self._session.execute(statement)).all()
        return tuple(_agenda_record(record, title) for record, title in rows)

    async def save_snapshot(self, snapshot: AgendaSnapshot) -> None:
        await self._session.execute(
            delete(AgendaSnapshotRecord).where(
                AgendaSnapshotRecord.owner_id == snapshot.owner_id,
                AgendaSnapshotRecord.expires_at <= snapshot.created_at,
            )
        )
        self._session.add(
            AgendaSnapshotRecord(
                snapshot_id=snapshot.snapshot_id,
                owner_id=snapshot.owner_id,
                view=snapshot.window.view.value,
                display_zone=snapshot.window.display_zone,
                start_date=snapshot.window.start_date,
                end_date=snapshot.window.end_date,
                starts_at=snapshot.window.starts_at,
                ends_at=snapshot.window.ends_at,
                include_completed=snapshot.include_completed,
                item_ids=list(snapshot.item_ids),
                page_starts=list(snapshot.page_starts),
                created_at=snapshot.created_at,
                expires_at=snapshot.expires_at,
            )
        )
        await self._session.flush()

    async def get_snapshot(
        self, owner_id: UUID, snapshot_id: UUID, *, now: datetime
    ) -> AgendaSnapshot | None:
        record = await self._session.scalar(
            select(AgendaSnapshotRecord).where(
                AgendaSnapshotRecord.snapshot_id == snapshot_id,
                AgendaSnapshotRecord.owner_id == owner_id,
                AgendaSnapshotRecord.expires_at > now.astimezone(UTC),
            )
        )
        return _snapshot(record) if record is not None else None

    async def list_snapshot_records(
        self, owner_id: UUID, occurrence_ids: tuple[UUID, ...]
    ) -> tuple[AgendaRecord, ...]:
        if not occurrence_ids:
            return ()
        rows = (
            await self._session.execute(
                select(OccurrenceRecord, PlanItemRecord.title)
                .join(
                    PlanItemRecord,
                    (PlanItemRecord.id == OccurrenceRecord.plan_item_id)
                    & (PlanItemRecord.owner_id == OccurrenceRecord.owner_id),
                )
                .where(
                    OccurrenceRecord.owner_id == owner_id,
                    OccurrenceRecord.id.in_(occurrence_ids),
                )
            )
        ).all()
        return tuple(_agenda_record(record, title) for record, title in rows)

    async def remember_page_start(
        self, owner_id: UUID, snapshot_id: UUID, *, start_index: int
    ) -> None:
        record = await self._session.scalar(
            select(AgendaSnapshotRecord)
            .where(
                AgendaSnapshotRecord.snapshot_id == snapshot_id,
                AgendaSnapshotRecord.owner_id == owner_id,
            )
            .with_for_update()
        )
        if record is None or not 0 <= start_index < len(record.item_ids):
            return
        starts = list(record.page_starts)
        if start_index not in starts:
            starts.append(start_index)
            starts.sort()
            record.page_starts = starts
            await self._session.flush()


def _agenda_record(record: OccurrenceRecord, title: str) -> AgendaRecord:
    return AgendaRecord(
        occurrence_id=record.id,
        owner_id=record.owner_id,
        kind=record.kind,
        title=title,
        state=record.state,
        event_zone=record.event_zone,
        local_date=record.local_date,
        local_time=record.local_time,
        end_local_date=record.end_local_date,
        end_local_time=record.end_local_time,
        starts_at=record.starts_at,
        ends_at=record.ends_at,
        deadline_at=record.deadline_at,
    )


def _snapshot(record: AgendaSnapshotRecord) -> AgendaSnapshot:
    return AgendaSnapshot(
        snapshot_id=record.snapshot_id,
        owner_id=record.owner_id,
        window=AgendaWindow(
            AgendaView(record.view),
            record.display_zone,
            record.start_date,
            record.end_date,
            record.starts_at,
            record.ends_at,
        ),
        include_completed=record.include_completed,
        item_ids=tuple(record.item_ids),
        page_starts=tuple(record.page_starts),
        created_at=record.created_at,
        expires_at=record.expires_at,
    )
