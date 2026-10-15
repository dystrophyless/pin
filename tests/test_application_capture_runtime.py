from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from pin.adapters.telegram import capture
from pin.contexts.conversation.application.capture import CaptureInterpretation, CaptureTurnReply
from pin.contexts.conversation.application.dto import ConversationContext, ModelFailure
from pin.contexts.conversation.domain.capture import (
    CaptureDraft,
    CaptureMessage,
    CaptureState,
)
from pin.contexts.conversation.domain.proposals import FailureCategory, InputKind, SourceLanguage
from pin.contexts.identity.api import PreferencesSnapshot
from pin.platform.jobs import JobClaim, JobLane
from pin.platform.models import InboxUpdate


class _Session:
    active = False
    scalar_calls = 0

    def begin(self) -> AbstractAsyncContextManager[None]:
        return _Transaction(self)

    async def execute(self, _statement: object) -> _EmptyResult:
        assert self.active
        return _EmptyResult()

    async def scalar(self, _statement: object) -> UUID | None:
        assert self.active
        self.scalar_calls += 1
        # A missing Billing row is valid before trial activation. The next
        # scalar is the live-job lease fence; later lookup finds no pending
        # mutation clarification for this owner.
        return uuid4() if self.scalar_calls == 2 else None


class _EmptyResult:
    def all(self) -> tuple[()]:
        return ()


class _Transaction(AbstractAsyncContextManager[None]):
    def __init__(self, session: _Session) -> None:
        self.session = session

    async def __aenter__(self) -> None:
        assert not self.session.active
        self.session.active = True

    async def __aexit__(self, *exc_info: object) -> None:
        self.session.active = False


class _SessionContext(AbstractAsyncContextManager[_Session]):
    def __init__(self, session: _Session) -> None:
        self.session = session

    async def __aenter__(self) -> _Session:
        return self.session

    async def __aexit__(self, *exc_info: object) -> None:
        return None


@pytest.mark.asyncio
async def test_capture_runtime_calls_injected_provider_between_short_transactions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = uuid4()
    inbox_id = uuid4()
    at = datetime(2026, 10, 14, 9, tzinfo=UTC)
    profile = PreferencesSnapshot(
        owner_id,
        "en",
        "Asia/Almaty",
        1,
        1,
        time(22),
        time(8),
        time(20),
        7,
    )
    session = _Session()
    tx_states: list[bool] = []

    class Sessions:
        def __call__(self) -> _SessionContext:
            return _SessionContext(session)

    class Identity:
        async def get_preferences(self, requested_owner: UUID) -> PreferencesSnapshot:
            assert requested_owner == owner_id
            return profile

        async def get_onboarding(self, requested_owner: UUID) -> None:
            assert requested_owner == owner_id
            return None

    draft_id = uuid4()
    draft = CaptureDraft(
        draft_id,
        owner_id,
        2,
        CaptureState.PROCESSING,
        (CaptureMessage("user", "Clean the kitchen"),),
        at,
        at,
        at + timedelta(hours=1),
        1,
        processing_key=f"telegram-inbox:{inbox_id}",
    )
    context = ConversationContext(
        owner_id=owner_id,
        request_id=f"telegram-inbox:{inbox_id}",
        language=SourceLanguage.EN,
        input_kind=InputKind.TEXT,
        text="Current user turn:\nClean the kitchen",
        reference_date=date(2026, 10, 14),
        timezone="Asia/Almaty",
    )
    interpretation = CaptureInterpretation(draft, context.request_id, context)

    async def capture_inbound(_session: _Session, _claim: object) -> capture._Inbound:
        return capture._Inbound(
            cast(InboxUpdate, SimpleNamespace(id=inbox_id, owner_id=owner_id)),
            telegram_user_id=101,
            text="Clean the kitchen",
            callback_data=None,
        )

    async def prepare(*args: object, **kwargs: object) -> CaptureInterpretation:
        assert session.active
        assert kwargs["request_key"] == context.request_id
        tx_states.append(session.active)
        return interpretation

    class Assistant:
        async def interpret(
            self, received: ConversationContext, *, at: datetime, claim: JobClaim | None = None
        ) -> ModelFailure:
            assert not session.active
            assert received == context
            assert at == datetime(2026, 10, 14, 9, tzinfo=UTC)
            tx_states.append(session.active)
            return ModelFailure(category=FailureCategory.INVALID_INPUT)

    async def finish(*args: object, **kwargs: object) -> CaptureTurnReply:
        assert session.active
        tx_states.append(session.active)
        return CaptureTurnReply(draft.id, 3, "collecting", {"kind": "reply", "text": "Preview"})

    async def queue(*args: object, **kwargs: object) -> None:
        assert session.active
        tx_states.append(session.active)

    async def record_effect(*args: object, **kwargs: object) -> SimpleNamespace:
        assert session.active
        return SimpleNamespace(first_application=True)

    async def complete(*args: object, **kwargs: object) -> None:
        assert session.active

    monkeypatch.setattr(capture, "_capture_inbound", capture_inbound)
    monkeypatch.setattr(capture, "SqlAlchemyIdentityStore", lambda _session: Identity())
    monkeypatch.setattr(capture, "SqlAlchemyCaptureStore", lambda _session: object())
    monkeypatch.setattr(capture, "prepare_interpretation", prepare)
    monkeypatch.setattr(capture, "finish_interpretation", finish)
    monkeypatch.setattr(capture, "_queue_reply", queue)
    monkeypatch.setattr(capture, "record_job_effect_once", record_effect)
    monkeypatch.setattr(capture, "complete_job", complete)

    claim = JobClaim(
        job_id=uuid4(),
        lane=JobLane.APPLICATION,
        kind="telegram.private_text",
        payload_version=1,
        payload={"inbox_id": str(inbox_id)},
        owner_id=owner_id,
        conversation_sequence=1,
        attempt=1,
        worker_id="capture-runtime-test",
        token=uuid4(),
        fencing_generation=1,
        lease_until=at + timedelta(seconds=45),
    )
    handled = await capture.process_capture_application_job(
        Sessions(),  # type: ignore[arg-type]
        claim,
        now=at,
        assistant=Assistant(),
    )

    assert handled
    assert tx_states == [True, False, True, True]
    assert not session.active


@pytest.mark.asyncio
async def test_capture_runtime_keeps_all_pages_and_actions_only_on_last_outbox_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = uuid4()
    at = datetime(2026, 10, 14, 9, tzinfo=UTC)
    claim = JobClaim(
        job_id=uuid4(),
        lane=JobLane.APPLICATION,
        kind="telegram.private_text",
        payload_version=1,
        payload={},
        owner_id=owner_id,
        conversation_sequence=4,
        attempt=1,
        worker_id="capture-runtime-pages-test",
        token=uuid4(),
        fencing_generation=2,
        lease_until=at + timedelta(seconds=45),
    )
    reply = CaptureTurnReply(
        uuid4(),
        7,
        "ready",
        {"kind": "preview", "preview": {"items": []}},
        confirmation_credential="confirmation-proof",
    )
    pages = (
        SimpleNamespace(markdown="first", plain_text="first", target_ids=()),
        SimpleNamespace(markdown="second", plain_text="second", target_ids=()),
    )
    events: list[object] = []

    async def add_event(_session: _Session, event: object, *, claim: JobClaim) -> None:
        assert claim is not None
        events.append(event)

    class Callbacks:
        def __init__(self, _store: object) -> None:
            pass

        async def issue(self, **_kwargs: object) -> str:
            return "opaque-runtime-token"

    monkeypatch.setattr(capture, "render_capture_response", lambda *_args, **_kwargs: pages)
    monkeypatch.setattr(capture, "add_outbox_event", add_event)
    monkeypatch.setattr(capture, "CallbackTokens", Callbacks)

    await capture._queue_reply(
        _Session(),
        claim,
        chat_id=202,
        reply=reply,
        language="en",
        now=at,
    )

    assert len(events) == 2
    payloads = [cast(object, event).payload for event in events]
    assert [payload["page_index"] for payload in payloads] == [0, 1]
    assert [payload["page_count"] for payload in payloads] == [2, 2]
    assert "reply_markup" not in payloads[0]
    assert "reply_markup" in payloads[1]
    markup = payloads[1]["reply_markup"]
    buttons = [
        button
        for row in markup["inline_keyboard"]
        for button in row
    ]
    callback_data = [button["callback_data"] for button in buttons]
    assert any(value.startswith("capc:") for value in callback_data)
    assert any(value.startswith("capx:") for value in callback_data)


def test_conversation_context_carries_bounded_draft_history_and_profile() -> None:
    owner_id = uuid4()
    profile = PreferencesSnapshot(
        owner_id,
        "ru",
        "Asia/Almaty",
        1,
        2,
        time(22),
        time(8),
        time(20),
        9,
    )
    draft = CaptureDraft(
        uuid4(),
        owner_id,
        3,
        CaptureState.PROCESSING,
        (
            CaptureMessage("user", "Buy milk next week"),
            CaptureMessage("assistant", "Which day?"),
            CaptureMessage("user", "Wednesday"),
        ),
        datetime(2026, 10, 14, 17, tzinfo=UTC),
        datetime(2026, 10, 14, 18, tzinfo=UTC),
        datetime(2026, 10, 15, 18, tzinfo=UTC),
        2,
        processing_key="turn-2",
        normalized_actions=({"title": "Buy milk"},),
    )
    now = datetime(2026, 10, 14, 18, tzinfo=UTC)

    result = capture._conversation_context(draft, "turn-2", "Wednesday", profile, now)

    assert result.request_id == "turn-2"
    assert result.language is SourceLanguage.RU
    assert result.timezone == "Asia/Almaty"
    assert result.reference_date == now.astimezone(ZoneInfo("Asia/Almaty")).date()
    assert "user: Buy milk next week" in result.text
    assert "assistant: Which day?" in result.text
    assert "Pending normalized plan proposals" in result.text
    assert "Current user turn:\nWednesday" in result.text
    assert {item.key: item.value for item in result.preferences}["profile_version"] == "9"
    assert len(result.text) <= 8_000
