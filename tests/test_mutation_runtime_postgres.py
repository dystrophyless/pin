from __future__ import annotations

import asyncio
import copy
import hashlib
import os
import sys
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified

import pin.adapters.telegram.mutation_runtime as mutation_runtime
from pin.adapters.telegram.mutation_runtime import (
    prepare_proposed_mutation,
    process_mutation_callback,
    process_mutation_command,
)
from pin.adapters.telegram.mutation_runtime_store import MutationRuntimePendingRecord
from pin.adapters.telegram.presentation.tokens import TelegramCallbackTokenRecord
from pin.contexts.billing.application.contracts import StartTrial
from pin.contexts.billing.infrastructure.models import UsageReservationRecord
from pin.contexts.billing.infrastructure.store import SqlAlchemyAccessUsageStore
from pin.contexts.conversation.application.dto import ActionProposal, ProposalFields, TaskSchedule
from pin.contexts.conversation.domain.proposals import ProposalOperation, SourceLanguage
from pin.contexts.identity.domain.preferences import Preferences
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.planning.domain.plans import OneOffPlanDraft, PlanKind, resolve_schedule
from pin.contexts.planning.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    RecurringPlanDraft,
    project_recurrence,
)
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.contexts.planning.infrastructure.mutation_models import MutationPreviewRecord
from pin.contexts.planning.infrastructure.series_models import SeriesRecord
from pin.contexts.planning.infrastructure.series_store import SqlAlchemySeriesStore
from pin.platform.models import OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue15_runtime_"
DATABASE_PORT = 51017
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _database_url() -> str:
    value = os.getenv("PIN_TEST_MUTATION_RUNTIME_DATABASE_URL")
    if not value:
        pytest.skip("set PIN_TEST_MUTATION_RUNTIME_DATABASE_URL to the assigned Issue 15 fixture")
    engine = create_engine(value)
    try:
        with engine.connect() as connection:
            name = connection.exec_driver_sql("select current_database()").scalar_one()
            port = connection.exec_driver_sql("show port").scalar_one()
            directory = connection.exec_driver_sql("show data_directory").scalar_one()
    finally:
        engine.dispose()
    if not str(name).startswith(DATABASE_PREFIX):
        pytest.fail("mutation runtime database is outside the assigned pin_issue15_runtime_ prefix")
    if int(port) != DATABASE_PORT:
        pytest.fail("mutation runtime database is not on the assigned port")
    if str(directory).replace("\\", "/") != DATA_DIRECTORY:
        pytest.fail(
            "mutation runtime database is outside the assigned disposable PostgreSQL cluster"
        )
    return value


@pytest.fixture(scope="session")
def runtime_sessions() -> Iterator[async_sessionmaker[AsyncSession]]:
    database_url = _database_url()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_async_engine(database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())


async def _seed_user(session: AsyncSession, owner_id: UUID, *, at: datetime) -> None:
    await SqlAlchemyIdentityStore(session).save_preferences(
        owner_id=owner_id,
        expected_version=None,
        preferences=Preferences(language="en", home_zone="UTC"),
    )
    await SqlAlchemyAccessUsageStore(session).activate_trial(
        StartTrial(owner_id, telegram_user_id=owner_id.int % 2_000_000_000 + 1, at=at)
    )


async def _seed_task(
    session: AsyncSession,
    *,
    owner_id: UUID,
    title: str,
    at: datetime,
    day: date,
    kind: PlanKind = PlanKind.TASK,
) -> UUID:
    draft = OneOffPlanDraft(
        kind=kind,
        title=title,
        local_date=day,
        local_time=time(12),
        event_zone="UTC",
    )
    schedule = resolve_schedule(draft, default_zone="UTC", now=at)
    item_id, occurrence_id = uuid4(), uuid4()
    session.add(
        PlanItemRecord(id=item_id, owner_id=owner_id, kind=kind.value, title=title, version=1)
    )
    await session.flush()
    session.add(
        OccurrenceRecord(
            id=occurrence_id,
            owner_id=owner_id,
            plan_item_id=item_id,
            kind=schedule.kind.value,
            event_zone=schedule.event_zone,
            local_date=schedule.local_date,
            local_time=schedule.local_time,
            fold=schedule.fold,
            end_local_date=schedule.end_local_date,
            end_local_time=schedule.end_local_time,
            end_fold=schedule.end_fold,
            starts_at=schedule.starts_at,
            ends_at=schedule.ends_at,
            deadline_at=schedule.deadline_at,
            duration_seconds=schedule.duration_seconds,
            state="open",
            version=1,
            reminder_preferences_version=1,
        )
    )
    await session.flush()
    return occurrence_id


async def _seed_weekly_series(session: AsyncSession, *, owner_id: UUID, at: datetime) -> UUID:
    draft = RecurringPlanDraft(
        kind=PlanKind.TASK,
        title="Weekly review",
        start_date=date(2026, 10, 19),
        local_time=time(12),
        event_zone="UTC",
        recurrence=RecurrenceRule(
            RecurrenceFrequency.WEEKLY,
            interval=1,
            weekdays=(0,),
            count=3,
        ),
    )
    projection = project_recurrence(
        draft,
        owner_id=owner_id,
        default_zone="UTC",
        default_reminders=(),
        at=at,
        through=date(2026, 12, 1),
    )
    await SqlAlchemySeriesStore(session).persist_series(
        owner_id=owner_id,
        action_key=f"runtime-seed-{uuid4()}",
        payload_sha256="b" * 64,
        approved_preferences_version=1,
        draft=draft,
        projection=projection,
        effective_reminders=(),
        default_zone="UTC",
        quiet_zone="UTC",
        quiet_start=time(22),
        quiet_end=time(7),
        at=at,
        horizon_date=date(2026, 12, 1),
    )
    series = await session.scalar(select(SeriesRecord).where(SeriesRecord.owner_id == owner_id))
    assert series is not None
    return series.id


async def _click(
    sessions: async_sessionmaker[AsyncSession], *, owner_id: UUID, callback: str, at: datetime
):
    async with sessions() as session, session.begin():
        return await process_mutation_callback(
            session, owner_id=owner_id, data=callback, language="en", now=at
        )


@pytest.mark.asyncio
async def test_command_menu_is_owner_scoped_and_confirmation_is_a_separate_step(runtime_sessions):
    at = datetime(2026, 10, 14, 9, tzinfo=UTC)
    owner_id, foreign_owner = uuid4(), uuid4()
    async with runtime_sessions() as session, session.begin():
        await _seed_user(session, owner_id, at=at)
        owned_id = await _seed_task(
            session, owner_id=owner_id, title="Owned release task", at=at, day=date(2026, 10, 20)
        )
        foreign_id = await _seed_task(
            session,
            owner_id=foreign_owner,
            title="Foreign private task",
            at=at,
            day=date(2026, 10, 21),
        )
        menu = await process_mutation_command(
            session, owner_id=owner_id, text="/done", language="en", now=at
        )
        assert menu is not None and menu.keyboard is not None
        menu_labels = str(menu.keyboard["inline_keyboard"])
        assert "Owned release task" in menu_labels
        assert "Foreign private task" not in menu_labels
        selection = menu.keyboard["inline_keyboard"][0][0]["callback_data"]
        assert isinstance(selection, str) and selection.startswith("pm:")
        assert len(selection.encode()) <= 64
        owned = await session.get(OccurrenceRecord, owned_id)
        foreign = await session.get(OccurrenceRecord, foreign_id)
        assert owned is not None and (owned.state, owned.version) == ("open", 1)
        assert foreign is not None and (foreign.state, foreign.version) == ("open", 1)
        assert (
            await session.scalar(
                select(MutationPreviewRecord.id).where(MutationPreviewRecord.owner_id == owner_id)
            )
            is None
        )

    rejected = await _click(runtime_sessions, owner_id=foreign_owner, callback=selection, at=at)
    assert "invalid" in rejected.text.lower() or "expired" in rejected.text.lower()

    async with runtime_sessions() as session, session.begin():
        preview = await process_mutation_callback(
            session, owner_id=owner_id, data=selection, language="en", now=at
        )
        assert preview.keyboard is not None
        assert "Owned release task" in preview.text
        occurrence = await session.get(OccurrenceRecord, owned_id)
        assert occurrence is not None and (occurrence.state, occurrence.version) == ("open", 1)
        mutation_preview = await session.scalar(
            select(MutationPreviewRecord).where(MutationPreviewRecord.owner_id == owner_id)
        )
        assert mutation_preview is not None and mutation_preview.state == "pending"
        assert mutation_preview.owner_id == owner_id
        confirm = preview.keyboard["inline_keyboard"][0][0]["callback_data"]
        assert isinstance(confirm, str)

    confirmed = await _click(runtime_sessions, owner_id=owner_id, callback=confirm, at=at)
    assert "marked done" in confirmed.text.lower()
    async with runtime_sessions() as session:
        occurrence = await session.get(OccurrenceRecord, owned_id)
        assert occurrence is not None and (occurrence.state, occurrence.version) == ("completed", 2)
        reservation = await session.scalar(
            select(UsageReservationRecord).where(UsageReservationRecord.owner_id == owner_id)
        )
        assert reservation is None
        pending = await session.scalar(
            select(MutationRuntimePendingRecord).where(
                MutationRuntimePendingRecord.owner_id == owner_id
            )
        )
        assert pending is not None and pending.state == "confirmed"


@pytest.mark.asyncio
async def test_named_cancel_uses_owner_menu_then_requires_preview_confirmation(runtime_sessions):
    at = datetime(2026, 10, 14, 9, tzinfo=UTC)
    owner_id, foreign_owner = uuid4(), uuid4()
    async with runtime_sessions() as session, session.begin():
        await _seed_user(session, owner_id, at=at)
        owned_id = await _seed_task(
            session, owner_id=owner_id, title="Cancel this task", at=at, day=date(2026, 10, 20)
        )
        foreign_id = await _seed_task(
            session,
            owner_id=foreign_owner,
            title="Foreign private task",
            at=at,
            day=date(2026, 10, 21),
        )
        menu = await process_mutation_command(
            session,
            owner_id=owner_id,
            text="/cancel Cancel this task",
            language="en",
            now=at,
        )
        assert menu is not None and menu.keyboard is not None
        menu_labels = str(menu.keyboard["inline_keyboard"])
        assert "Cancel this task" in menu_labels
        assert "Foreign private task" not in menu_labels
        selection = menu.keyboard["inline_keyboard"][0][0]["callback_data"]
        assert isinstance(selection, str) and selection.startswith("pm:")
        owned = await session.get(OccurrenceRecord, owned_id)
        foreign = await session.get(OccurrenceRecord, foreign_id)
        assert owned is not None and (owned.state, owned.version) == ("open", 1)
        assert foreign is not None and (foreign.state, foreign.version) == ("open", 1)
        assert (
            await session.scalar(
                select(MutationPreviewRecord.id).where(MutationPreviewRecord.owner_id == owner_id)
            )
            is None
        )

    async with runtime_sessions() as session, session.begin():
        preview = await process_mutation_callback(
            session, owner_id=owner_id, data=selection, language="en", now=at
        )
        assert preview.keyboard is not None
        assert "Cancel this task" in preview.text
        assert "Cancel this occurrence?" in preview.text
        occurrence = await session.get(OccurrenceRecord, owned_id)
        assert occurrence is not None and (occurrence.state, occurrence.version) == ("open", 1)
        mutation_preview = await session.scalar(
            select(MutationPreviewRecord).where(MutationPreviewRecord.owner_id == owner_id)
        )
        assert mutation_preview is not None and mutation_preview.state == "pending"
        assert mutation_preview.owner_id == owner_id
        confirm = preview.keyboard["inline_keyboard"][0][0]["callback_data"]
        assert isinstance(confirm, str)

    confirmed = await _click(runtime_sessions, owner_id=owner_id, callback=confirm, at=at)
    assert "Task cancelled." in confirmed.text
    async with runtime_sessions() as session:
        occurrence = await session.get(OccurrenceRecord, owned_id)
        assert occurrence is not None and (occurrence.state, occurrence.version) == ("cancelled", 2)


@pytest.mark.asyncio
async def test_new_preview_supersedes_old_confirmation_and_dismiss_cancels_sibling_token(
    runtime_sessions,
):
    at = datetime(2026, 10, 14, 9, tzinfo=UTC)
    owner_id = uuid4()
    async with runtime_sessions() as session, session.begin():
        await _seed_user(session, owner_id, at=at)
        occurrence_id = await _seed_task(
            session, owner_id=owner_id, title="Mutable task", at=at, day=date(2026, 10, 20)
        )
        first = await process_mutation_command(
            session,
            owner_id=owner_id,
            text=f"/done {occurrence_id}",
            language="en",
            now=at,
        )
        assert first is not None and first.keyboard is not None
        old_confirm = first.keyboard["inline_keyboard"][0][0]["callback_data"]
        assert isinstance(old_confirm, str)

    async with runtime_sessions() as session, session.begin():
        second = await process_mutation_command(
            session,
            owner_id=owner_id,
            text=f"/cancel {occurrence_id}",
            language="en",
            now=at,
        )
        assert second is not None and second.keyboard is not None
        previews = (
            await session.scalars(
                select(MutationPreviewRecord)
                .where(MutationPreviewRecord.owner_id == owner_id)
                .order_by(MutationPreviewRecord.created_at, MutationPreviewRecord.id)
            )
        ).all()
        assert len(previews) == 2
        new_confirm = second.keyboard["inline_keyboard"][0][0]["callback_data"]
        dismiss = second.keyboard["inline_keyboard"][0][1]["callback_data"]
        assert isinstance(new_confirm, str) and isinstance(dismiss, str)

    old_result = await _click(runtime_sessions, owner_id=owner_id, callback=old_confirm, at=at)
    assert "stale" in old_result.text.lower()
    async with runtime_sessions() as session:
        occurrence = await session.get(OccurrenceRecord, occurrence_id)
        assert occurrence is not None and (occurrence.state, occurrence.version) == ("open", 1)

    dismissed = await _click(runtime_sessions, owner_id=owner_id, callback=dismiss, at=at)
    assert "dismissed" in dismissed.text.lower()
    sibling = await _click(runtime_sessions, owner_id=owner_id, callback=new_confirm, at=at)
    assert "stale" in sibling.text.lower()
    async with runtime_sessions() as session:
        occurrence = await session.get(OccurrenceRecord, occurrence_id)
        assert occurrence is not None and (occurrence.state, occurrence.version) == ("open", 1)
        pending_states = (
            await session.scalars(
                select(MutationRuntimePendingRecord.state).where(
                    MutationRuntimePendingRecord.owner_id == owner_id
                )
            )
        ).all()
        assert "dismissed" in pending_states and "previewed" not in pending_states


@pytest.mark.asyncio
async def test_deterministic_menu_keyset_reaches_every_owner_target_past_fifty(runtime_sessions):
    at = datetime(2026, 10, 14, 9, tzinfo=UTC)
    owner_id, foreign_owner = uuid4(), uuid4()
    expected_titles = {f"Task {index:02}" for index in range(53)}
    async with runtime_sessions() as session, session.begin():
        await _seed_user(session, owner_id, at=at)
        for title in sorted(expected_titles):
            await _seed_task(
                session,
                owner_id=owner_id,
                title=title,
                at=at,
                day=date(2026, 11, 1),
            )
        await _seed_task(
            session,
            owner_id=foreign_owner,
            title="Never visible foreign target",
            at=at,
            day=date(2026, 11, 1),
        )
        page = await process_mutation_command(
            session, owner_id=owner_id, text="/done", language="en", now=at
        )
        assert page is not None and page.keyboard is not None
        labels = {
            button["text"]
            for row in page.keyboard["inline_keyboard"]
            for button in row
            if button["text"] not in {"‹", "›"}
        }
        next_callback = next(
            button["callback_data"]
            for row in page.keyboard["inline_keyboard"]
            for button in row
            if button["text"] == "›"
        )

    wrong_owner = await _click(
        runtime_sessions, owner_id=foreign_owner, callback=next_callback, at=at
    )
    assert "invalid" in wrong_owner.text.lower() or "expired" in wrong_owner.text.lower()
    replayed_page_token = next_callback
    current = await _click(runtime_sessions, owner_id=owner_id, callback=next_callback, at=at)
    assert current.keyboard is not None
    while True:
        labels.update(
            button["text"]
            for row in current.keyboard["inline_keyboard"]
            for button in row
            if button["text"] not in {"‹", "›"}
        )
        next_button = next(
            (
                button
                for row in current.keyboard["inline_keyboard"]
                for button in row
                if button["text"] == "›"
            ),
            None,
        )
        if next_button is None:
            break
        assert isinstance(next_button["callback_data"], str)
        current = await _click(
            runtime_sessions,
            owner_id=owner_id,
            callback=next_button["callback_data"],
            at=at,
        )
        assert current.keyboard is not None

    assert len(labels) == 53
    assert all(any(title in label for label in labels) for title in expected_titles)
    assert all("Never visible foreign target" not in label for label in labels)
    last_target = next(
        button
        for row in current.keyboard["inline_keyboard"]
        for button in row
        if button["text"] not in {"‹", "›"}
    )
    selected = await _click(
        runtime_sessions,
        owner_id=owner_id,
        callback=last_target["callback_data"],
        at=at,
    )
    assert selected.keyboard is not None and any(
        title in selected.text for title in expected_titles
    )
    replay = await _click(runtime_sessions, owner_id=owner_id, callback=replayed_page_token, at=at)
    assert "used" in replay.text.lower() or "stale" in replay.text.lower()


@pytest.mark.asyncio
async def test_validated_reschedule_proposal_previews_without_writing_and_correction_stales_it(
    runtime_sessions,
):
    at = datetime(2026, 10, 14, 9, tzinfo=UTC)
    owner_id = uuid4()
    async with runtime_sessions() as session, session.begin():
        await _seed_user(session, owner_id, at=at)
        occurrence_id = await _seed_task(
            session,
            owner_id=owner_id,
            title="Proposal target",
            at=at,
            day=date(2026, 10, 20),
        )
        proposal = ActionProposal(
            kind="proposal",
            language=SourceLanguage.EN,
            operation=ProposalOperation.CHANGE_OCCURRENCE,
            target_id=occurrence_id,
            expected_version=1,
            fields=ProposalFields(
                schedule=TaskSchedule(
                    kind="task",
                    local_date=date(2026, 10, 22),
                    local_time=time(13),
                    timezone="UTC",
                    timing="timed",
                )
            ),
        )
        preview = await prepare_proposed_mutation(
            session,
            owner_id=owner_id,
            proposal=proposal,
            language="en",
            now=at,
        )
        assert preview.keyboard is not None
        assert "Proposal target" in preview.text and "2026-10-22 13:00" in preview.text
        original = await session.get(OccurrenceRecord, occurrence_id)
        assert original is not None and (original.local_date, original.local_time) == (
            date(2026, 10, 20),
            time(12),
        )
        old_confirm = preview.keyboard["inline_keyboard"][0][0]["callback_data"]
        assert isinstance(old_confirm, str)

        correction = ActionProposal(
            kind="proposal",
            language=SourceLanguage.EN,
            operation=ProposalOperation.CHANGE_OCCURRENCE,
            target_id=occurrence_id,
            expected_version=1,
            fields=ProposalFields(
                schedule=TaskSchedule(
                    kind="task",
                    local_date=date(2026, 10, 23),
                    local_time=time(14),
                    timezone="UTC",
                    timing="timed",
                ),
                reminders=None,
            ),
        )
        clarification = await prepare_proposed_mutation(
            session,
            owner_id=owner_id,
            proposal=correction,
            language="en",
            now=at,
        )
        assert clarification.keyboard is None
        assert "Clarify" in clarification.text
        current = await session.get(OccurrenceRecord, occurrence_id)
        assert current is not None and (current.local_date, current.local_time) == (
            date(2026, 10, 20),
            time(12),
        )

    stale = await _click(runtime_sessions, owner_id=owner_id, callback=old_confirm, at=at)
    assert "stale" in stale.text.lower()
    async with runtime_sessions() as session:
        current = await session.get(OccurrenceRecord, occurrence_id)
        assert current is not None and (current.state, current.version) == ("open", 1)


@pytest.mark.asyncio
async def test_deterministic_reschedule_waits_for_explicit_schedule_and_preserves_target_zone(
    runtime_sessions,
):
    at = datetime(2026, 10, 14, 9, tzinfo=UTC)
    owner_id = uuid4()
    async with runtime_sessions() as session, session.begin():
        await _seed_user(session, owner_id, at=at)
        occurrence_id = await _seed_task(
            session,
            owner_id=owner_id,
            title="Date correction task",
            at=at,
            day=date(2026, 10, 20),
        )
        prompt = await process_mutation_command(
            session,
            owner_id=owner_id,
            text=f"/reschedule {occurrence_id}",
            language="en",
            now=at,
        )
        assert prompt is not None and "YYYY-MM-DD HH:MM" in prompt.text
        assert (
            await session.scalar(
                select(MutationPreviewRecord.id).where(MutationPreviewRecord.owner_id == owner_id)
            )
            is None
        )
        invalid = await process_mutation_command(
            session, owner_id=owner_id, text="next Tuesday", language="en", now=at
        )
        assert invalid is not None and "YYYY-MM-DD" in invalid.text
        preview = await process_mutation_command(
            session,
            owner_id=owner_id,
            text="2026-10-22 13:30",
            language="en",
            now=at,
        )
        assert preview is not None and preview.keyboard is not None
        assert "2026-10-22 13:30" in preview.text
        assert "UTC" in preview.text
        occurrence = await session.get(OccurrenceRecord, occurrence_id)
        assert occurrence is not None and (occurrence.local_date, occurrence.local_time) == (
            date(2026, 10, 20),
            time(12),
        )


@pytest.mark.asyncio
async def test_recurring_cancel_requires_scope_choice_before_preview(runtime_sessions):
    at = datetime(2026, 10, 14, 9, tzinfo=UTC)
    owner_id = uuid4()
    async with runtime_sessions() as session, session.begin():
        await _seed_user(session, owner_id, at=at)
        series_id = await _seed_weekly_series(session, owner_id=owner_id, at=at)
        occurrences = (
            await session.scalars(
                select(OccurrenceRecord)
                .where(OccurrenceRecord.owner_id == owner_id)
                .order_by(OccurrenceRecord.local_date)
            )
        ).all()
        assert len(occurrences) == 3
        cancel = await process_mutation_command(
            session,
            owner_id=owner_id,
            text=f"/cancel {occurrences[0].id}",
            language="en",
            now=at,
        )
        assert cancel is not None and cancel.keyboard is not None
        options = [button["text"] for row in cancel.keyboard["inline_keyboard"] for button in row]
        assert options == ["This occurrence", "This and future"]
        assert (
            await session.scalar(
                select(MutationPreviewRecord.id).where(MutationPreviewRecord.owner_id == owner_id)
            )
            is None
        )
        future_scope = cancel.keyboard["inline_keyboard"][1][0]["callback_data"]
        assert isinstance(future_scope, str)

    preview = await _click(runtime_sessions, owner_id=owner_id, callback=future_scope, at=at)
    assert preview.keyboard is not None
    assert "future" in preview.text.lower()
    async with runtime_sessions() as session:
        occurrences = (
            await session.scalars(
                select(OccurrenceRecord)
                .where(OccurrenceRecord.owner_id == owner_id)
                .order_by(OccurrenceRecord.local_date)
            )
        ).all()
        assert len(occurrences) == 3
        assert all(item.state == "open" and item.version == 1 for item in occurrences)

    async with runtime_sessions() as session, session.begin():
        series_change = ActionProposal(
            kind="proposal",
            language=SourceLanguage.EN,
            operation=ProposalOperation.CHANGE_SERIES,
            target_id=series_id,
            expected_version=1,
            fields=ProposalFields(
                schedule=TaskSchedule(
                    kind="task",
                    local_date=date(2026, 10, 23),
                    local_time=time(13),
                    timezone="UTC",
                    timing="timed",
                )
            ),
        )
        anchor_menu = await prepare_proposed_mutation(
            session,
            owner_id=owner_id,
            proposal=series_change,
            language="en",
            now=at,
        )
        assert anchor_menu.keyboard is not None
        assert "Which occurrence" in anchor_menu.text
        assert len(anchor_menu.keyboard["inline_keyboard"]) == 3
        anchor_callback = anchor_menu.keyboard["inline_keyboard"][0][0]["callback_data"]
        assert isinstance(anchor_callback, str)

    scope_menu = await _click(runtime_sessions, owner_id=owner_id, callback=anchor_callback, at=at)
    assert scope_menu.keyboard is not None
    assert [button["text"] for row in scope_menu.keyboard["inline_keyboard"] for button in row] == [
        "This occurrence",
        "This and future",
    ]
    this_occurrence = scope_menu.keyboard["inline_keyboard"][0][0]["callback_data"]
    series_preview = await _click(
        runtime_sessions, owner_id=owner_id, callback=this_occurrence, at=at
    )
    assert series_preview.keyboard is not None
    assert "2026-10-19" in series_preview.text and "2026-10-23 13:00" in series_preview.text


@pytest.mark.asyncio
async def test_deadline_reschedule_accepts_date_only_and_preserves_its_existing_time(
    runtime_sessions,
):
    at = datetime(2026, 10, 14, 9, tzinfo=UTC)
    owner_id = uuid4()
    async with runtime_sessions() as session, session.begin():
        await _seed_user(session, owner_id, at=at)
        deadline_id = await _seed_task(
            session,
            owner_id=owner_id,
            title="Date-only deadline",
            at=at,
            day=date(2026, 10, 20),
            kind=PlanKind.DEADLINE,
        )
        await process_mutation_command(
            session,
            owner_id=owner_id,
            text=f"/reschedule {deadline_id}",
            language="en",
            now=at,
        )
        preview = await process_mutation_command(
            session, owner_id=owner_id, text="2026-10-22", language="en", now=at
        )
        assert preview is not None and preview.keyboard is not None
        assert "2026-10-22" in preview.text
        assert "12:00" in preview.text
        deadline = await session.get(OccurrenceRecord, deadline_id)
        assert deadline is not None and deadline.local_date == date(2026, 10, 20)
        assert deadline.local_time == time(12)


@pytest.mark.parametrize(
    ("corruption", "value"),
    [
        ("preview_version", True),
        ("preview_version", "1"),
        ("reminder_amount", True),
        ("reminder_amount", "3600"),
    ],
)
@pytest.mark.asyncio
async def test_malformed_persisted_confirmation_is_atomic_and_keeps_token_unconsumed(
    runtime_sessions: async_sessionmaker[AsyncSession], corruption: str, value: object
) -> None:
    at = datetime(2026, 10, 14, 9, tzinfo=UTC)
    owner_id = uuid4()
    async with runtime_sessions() as session, session.begin():
        await _seed_user(session, owner_id, at=at)
        occurrence_id = await _seed_task(
            session,
            owner_id=owner_id,
            title="Corrupted preview target",
            at=at,
            day=date(2026, 10, 20),
        )
        if corruption == "reminder_amount":
            session.add(
                ReminderRuleRecord(
                    id=uuid4(),
                    owner_id=owner_id,
                    occurrence_id=occurrence_id,
                    offset_kind="elapsed_seconds",
                    offset_amount=3600,
                    source="explicit",
                    due_at=at + timedelta(days=1),
                )
            )
            await session.flush()
        preview = await process_mutation_command(
            session,
            owner_id=owner_id,
            text=f"/done {occurrence_id}",
            language="en",
            now=at,
        )
        assert preview is not None and preview.keyboard is not None
        rows = cast(list[list[dict[str, object]]], preview.keyboard["inline_keyboard"])
        confirm = rows[0][0]["callback_data"]
        assert isinstance(confirm, str)

    digest = hashlib.sha256(confirm.removeprefix("pm:").encode("utf-8")).hexdigest()
    async with runtime_sessions() as session, session.begin():
        callback = await session.get(TelegramCallbackTokenRecord, digest)
        pending = await session.scalar(
            select(MutationRuntimePendingRecord)
            .where(
                MutationRuntimePendingRecord.owner_id == owner_id,
                MutationRuntimePendingRecord.state == "previewed",
            )
            .with_for_update()
        )
        assert callback is not None and pending is not None
        if corruption == "preview_version":
            pending.pending_fields = {
                **pending.pending_fields,
                "preview_version": value,
            }
            flag_modified(pending, "pending_fields")
        else:
            saved = await session.get(MutationPreviewRecord, UUID(callback.target_id))
            assert saved is not None
            payload = cast(dict[str, Any], copy.deepcopy(saved.payload))
            target = payload["target"]
            reminders = target["reminders"]
            assert reminders and reminders[0]["amount"] == 3600
            reminders[0]["amount"] = value
            saved.payload = payload
            flag_modified(saved, "payload")

    if corruption == "preview_version":
        async with runtime_sessions() as session:
            saved_pending = await session.scalar(
                select(MutationRuntimePendingRecord).where(
                    MutationRuntimePendingRecord.owner_id == owner_id,
                    MutationRuntimePendingRecord.state == "previewed",
                )
            )
            assert saved_pending is not None
            assert type(saved_pending.pending_fields["preview_version"]) is type(value)
            assert saved_pending.pending_fields["preview_version"] == value
    else:
        async with runtime_sessions() as session:
            saved_preview = await session.get(MutationPreviewRecord, UUID(callback.target_id))
            assert saved_preview is not None
            payload = cast(dict[str, Any], saved_preview.payload)
            saved_amount = payload["target"]["reminders"][0]["amount"]
            assert type(saved_amount) is type(value) and saved_amount == value

    reply = await _click(runtime_sessions, owner_id=owner_id, callback=confirm, at=at)
    assert "stale" in reply.text.lower() or "nothing was changed" in reply.text.lower()
    async with runtime_sessions() as session:
        occurrence = await session.get(OccurrenceRecord, occurrence_id)
        callback = await session.get(TelegramCallbackTokenRecord, digest)
        assert callback is not None
        saved = await session.get(MutationPreviewRecord, UUID(callback.target_id))
        pending = await session.scalar(
            select(MutationRuntimePendingRecord).where(
                MutationRuntimePendingRecord.owner_id == owner_id
            )
        )
        assert occurrence is not None and (occurrence.state, occurrence.version) == ("open", 1)
        assert callback.consumed_at is None
        assert saved is not None and saved.state == "pending"
        assert pending is not None
        assert pending.state == ("previewed" if corruption == "preview_version" else "stale")


@pytest.mark.asyncio
async def test_late_confirmation_failure_rolls_back_business_writes_before_outer_commit(
    runtime_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    at = datetime(2026, 10, 14, 9, tzinfo=UTC)
    owner_id = uuid4()
    async with runtime_sessions() as session, session.begin():
        await _seed_user(session, owner_id, at=at)
        occurrence_id = await _seed_task(
            session,
            owner_id=owner_id,
            title="Late failure target",
            at=at,
            day=date(2026, 10, 20),
        )
        preview = await process_mutation_command(
            session,
            owner_id=owner_id,
            text=f"/done {occurrence_id}",
            language="en",
            now=at,
        )
        assert preview is not None and preview.keyboard is not None
        rows = cast(list[list[dict[str, object]]], preview.keyboard["inline_keyboard"])
        confirm = rows[0][0]["callback_data"]
        assert isinstance(confirm, str)

    original_confirm = cast(
        Callable[..., Awaitable[object]], mutation_runtime.__dict__["confirm_mutation"]
    )

    async def confirm_then_fail(*args: Any, **kwargs: Any) -> object:
        await original_confirm(*args, **kwargs)
        raise ValueError("injected failure after mutation effects were flushed")

    monkeypatch.setattr(mutation_runtime, "confirm_mutation", confirm_then_fail)
    reply = await _click(runtime_sessions, owner_id=owner_id, callback=confirm, at=at)
    assert "nothing was changed" in reply.text.lower()
    async with runtime_sessions() as session:
        occurrence = await session.get(OccurrenceRecord, occurrence_id)
        callback_digest = hashlib.sha256(
            confirm.removeprefix("pm:").encode("utf-8")
        ).hexdigest()
        callback = await session.get(TelegramCallbackTokenRecord, callback_digest)
        saved = await session.scalar(
            select(MutationPreviewRecord).where(MutationPreviewRecord.owner_id == owner_id)
        )
        pending = await session.scalar(
            select(MutationRuntimePendingRecord).where(
                MutationRuntimePendingRecord.owner_id == owner_id
            )
        )
        events = (
            await session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.owner_id == owner_id,
                    OutboxEvent.aggregate_id == occurrence_id,
                )
            )
        ).all()
        assert occurrence is not None and (occurrence.state, occurrence.version) == ("open", 1)
        assert callback is not None and callback.consumed_at is None
        assert saved is not None and saved.state == "pending"
        assert pending is not None and pending.state == "stale"
        assert events == []
