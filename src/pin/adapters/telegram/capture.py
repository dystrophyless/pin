"""Telegram runtime composition for durable text capture."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.agenda import agenda_command
from pin.adapters.telegram.ingress import PostgresTelegramOwnerResolver, private_actor
from pin.adapters.telegram.presentation.callbacks import (
    CallbackBinding,
    CallbackTokens,
    InvalidCallbackToken,
)
from pin.adapters.telegram.presentation.capture import (
    CAPTURE_CANCEL_ACTION,
    CAPTURE_CANCEL_PREFIX,
    CAPTURE_CHOICE_PREFIX,
    CAPTURE_CONFIRM_PREFIX,
    build_capture_keyboard,
    capture_choice_callback_data,
    capture_confirmation_callback_data,
    render_capture_response,
)
from pin.adapters.telegram.presentation.tokens import SqlAlchemyCallbackTokenStore
from pin.contexts.billing.api import SqlAlchemyAccessUsageStore
from pin.contexts.conversation.application.dto import (
    ConversationContext,
    ModelOutcome,
    PreferenceSnapshot,
)
from pin.contexts.conversation.capture_api import (
    CaptureTurnReply,
    SqlAlchemyCaptureStore,
    apply_clarification_choice,
    cancel_draft,
    confirm_ready_draft,
    finish_interpretation,
    prepare_interpretation,
)
from pin.contexts.conversation.domain.capture import CaptureConflict, CaptureDraft
from pin.contexts.conversation.domain.proposals import InputKind, SourceLanguage
from pin.contexts.conversation.infrastructure.capture_store import (
    SqlAlchemyCaptureConfirmationVerifier,
    SqlAlchemyCaptureRecurrenceConfirmationVerifier,
)
from pin.contexts.identity.api import PreferencesSnapshot
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.planning.infrastructure.series_store import SqlAlchemySeriesStore
from pin.contexts.planning.infrastructure.store import SqlAlchemyPlanningStore
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event
from pin.platform.jobs import JobClaim, complete_job, record_job_effect_once
from pin.platform.models import InboxUpdate

CAPTURE_JOB_EFFECT = "capture_runtime_started"
PRIVATE_TEXT_EFFECT = "private_text_applied"
_CALLBACK_TTL = timedelta(minutes=15)


class PlanningAssistant(Protocol):
    async def interpret(
        self, context: ConversationContext, *, at: datetime, claim: JobClaim | None = None
    ) -> ModelOutcome: ...


@dataclass(frozen=True, slots=True)
class _Inbound:
    inbox: InboxUpdate
    telegram_user_id: int
    text: str | None
    callback_data: str | None


@dataclass(frozen=True, slots=True)
class _Clock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


async def process_capture_application_job(
    sessions: async_sessionmaker[AsyncSession],
    claim: JobClaim,
    *,
    now: datetime,
    assistant: PlanningAssistant | None,
) -> bool:
    """Handle capture text and callbacks; return False for onboarding/agenda work."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("capture worker clock must be timezone-aware")
    if claim.owner_id is None:
        raise ValueError("capture application jobs must be owner-bound")
    at = now.astimezone(UTC)
    interpretation = None
    turn_reply: CaptureTurnReply | None = None
    telegram_user_id = 0
    language = "ru"
    callback_inbound: _Inbound | None = None

    async with sessions() as session, session.begin():
        inbound = await _capture_inbound(session, claim)
        if inbound is None:
            return False
        telegram_user_id = inbound.telegram_user_id
        owner_id = inbound.inbox.owner_id
        if owner_id is None:
            raise RuntimeError("capture inbox update has no trusted owner")
        identity = SqlAlchemyIdentityStore(session)
        preferences = await identity.get_preferences(owner_id)
        onboarding = await identity.get_onboarding(owner_id)
        onboarding_active = onboarding is not None and onboarding.step != "complete"
        language = preferences.language if preferences is not None else "ru"

        is_capture_callback = _is_capture_callback(inbound.callback_data)
        is_capture_text = (
            assistant is not None
            and inbound.text is not None
            and preferences is not None
            and not onboarding_active
            and not inbound.text.lstrip().startswith("/")
            and agenda_command(inbound.text) is None
        )
        if not is_capture_callback and not is_capture_text:
            return False

        await record_job_effect_once(
            session,
            claim,
            effect_key=CAPTURE_JOB_EFFECT,
            result={"status": "capture_started"},
        )
        capture_store = SqlAlchemyCaptureStore(session)
        if is_capture_callback:
            callback_inbound = inbound
        else:
            assert inbound.text is not None and preferences is not None
            confirmed_preferences = preferences
            request_key = f"telegram-inbox:{inbound.inbox.id}"
            interpretation_or_reply = await prepare_interpretation(
                capture_store,
                owner_id=owner_id,
                request_key=request_key,
                text=inbound.text,
                at=at,
                context_factory=lambda draft, key, current_text: _conversation_context(
                    draft, key, current_text, confirmed_preferences, at
                ),
            )
            if isinstance(interpretation_or_reply, CaptureTurnReply):
                turn_reply = interpretation_or_reply
            else:
                interpretation = interpretation_or_reply

    if callback_inbound is not None:
        return await _process_capture_callback_job(
            sessions,
            claim,
            callback_inbound,
            language=language,
            chat_id=telegram_user_id,
            now=at,
        )

    if interpretation is not None:
        # The provider call must never hold the capture/job SQL transaction open.
        assert assistant is not None
        outcome = await assistant.interpret(interpretation.context, at=at, claim=claim)
        async with sessions() as session, session.begin():
            already_applied = await record_job_effect_once(
                session,
                claim,
                effect_key=PRIVATE_TEXT_EFFECT,
                result={"status": "capture_interpreted"},
            )
            if not already_applied.first_application:
                await complete_job(session, claim)
                return True
            identity = SqlAlchemyIdentityStore(session)
            preferences = await identity.get_preferences(interpretation.draft.owner_id)
            if preferences is None:
                turn_reply = _stale_reply(language)
            else:
                turn_reply = await finish_interpretation(
                    SqlAlchemyCaptureStore(session),
                    interpretation,
                    outcome,
                    preferences=preferences,
                    at=at,
                )
                language = preferences.language
            await _queue_reply(
                session,
                claim,
                chat_id=telegram_user_id,
                reply=turn_reply,
                language=language,
                now=at,
            )
            await complete_job(session, claim)
        return True

    assert turn_reply is not None
    async with sessions() as session, session.begin():
        already_applied = await record_job_effect_once(
            session,
            claim,
            effect_key=PRIVATE_TEXT_EFFECT,
            result={"status": "capture_replayed"},
        )
        if not already_applied.first_application:
            await complete_job(session, claim)
            return True
        await _queue_reply(
            session,
            claim,
            chat_id=telegram_user_id,
            reply=turn_reply,
            language=language,
            now=at,
        )
        await complete_job(session, claim)
    return True


async def _capture_inbound(session: AsyncSession, claim: JobClaim) -> _Inbound | None:
    try:
        raw_inbox_id = claim.payload.get("inbox_id")
        inbox_id = raw_inbox_id if isinstance(raw_inbox_id, UUID) else UUID(str(raw_inbox_id))
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError("claimed job has an invalid inbox_id") from None
    inbox = await session.get(InboxUpdate, inbox_id)
    if inbox is None:
        raise RuntimeError("claimed job references a missing inbox update")
    if inbox.owner_id is None or claim.owner_id != inbox.owner_id:
        raise RuntimeError("claimed job owner does not match its trusted inbox mapping")
    actor = private_actor(inbox.payload)
    callback = inbox.payload.get("callback_query")
    message = (
        callback.get("message")
        if isinstance(callback, dict)
        else inbox.payload.get("message")
    )
    if actor is None or not isinstance(message, dict):
        return None
    telegram_user_id, actor_kind, _ = actor
    if actor_kind not in {"message", "callback_query"}:
        return None
    trusted_owner = await PostgresTelegramOwnerResolver().resolve(
        session, bot_id=inbox.bot_id, telegram_user_id=telegram_user_id
    )
    if trusted_owner != inbox.owner_id:
        raise RuntimeError("persisted Telegram mapping does not match the inbox owner")
    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("id") != telegram_user_id:
        raise RuntimeError(
            "persisted private update no longer satisfies the ingress identity check"
        )
    if actor_kind == "callback_query":
        data = callback.get("data") if isinstance(callback, dict) else None
        return _Inbound(inbox, telegram_user_id, None, data if isinstance(data, str) else None)
    text = message.get("text")
    return _Inbound(inbox, telegram_user_id, text if isinstance(text, str) else None, None)


async def _apply_capture_callback(
    session: AsyncSession,
    store: SqlAlchemyCaptureStore,
    inbound: _Inbound,
    *,
    now: datetime,
) -> CaptureTurnReply:
    data = inbound.callback_data
    owner_id = inbound.inbox.owner_id
    if owner_id is None or data is None:
        raise InvalidCallbackToken("capture callback has no owner or data")
    identity = SqlAlchemyIdentityStore(session)
    profile = await identity.get_preferences(owner_id)
    if profile is None:
        raise CaptureConflict("owner profile is no longer available")

    if data.startswith(CAPTURE_CONFIRM_PREFIX):
        credential = data.removeprefix(CAPTURE_CONFIRM_PREFIX)
        target = await store.locate_confirmation(owner_id=owner_id, credential=credential, at=now)
        if target is None:
            raise CaptureConflict("confirmation is expired or stale")
        draft_id, version = target
        billing = SqlAlchemyAccessUsageStore(session)
        return await confirm_ready_draft(
            store,
            owner_id=owner_id,
            draft_id=draft_id,
            expected_version=version,
            credential=credential,
            plan_store=SqlAlchemyPlanningStore(session),
            recurrence_store=SqlAlchemySeriesStore(session),
            confirmation_verifier=SqlAlchemyCaptureConfirmationVerifier(session),
            recurrence_confirmation_verifier=SqlAlchemyCaptureRecurrenceConfirmationVerifier(
                session
            ),
            identity=identity,
            billing=billing,
            clock=_Clock(now),
        )

    if data.startswith(CAPTURE_CANCEL_PREFIX):
        token = data.removeprefix(CAPTURE_CANCEL_PREFIX)
        binding = await _consume_callback(session, token, owner_id, now)
        if binding.action != CAPTURE_CANCEL_ACTION:
            raise InvalidCallbackToken("callback action does not match capture cancellation")
        draft_id = _target_uuid(binding.target_id)
        draft = await store.get_draft(owner_id=owner_id, draft_id=draft_id)
        if draft is None or draft.version != binding.target_version:
            raise InvalidCallbackToken("capture cancellation is stale")
        return await cancel_draft(
            store,
            owner_id=owner_id,
            draft_id=draft_id,
            expected_version=draft.version,
            at=now,
        )

    if data.startswith(CAPTURE_CHOICE_PREFIX):
        token = data.removeprefix(CAPTURE_CHOICE_PREFIX)
        binding = await _consume_callback(session, token, owner_id, now)
        action_prefix = "capture.choice:"
        if not binding.action.startswith(action_prefix):
            raise InvalidCallbackToken("callback action does not match capture choice")
        draft_id = _target_uuid(binding.target_id)
        draft = await store.get_draft(owner_id=owner_id, draft_id=draft_id)
        if draft is None or draft.version != binding.target_version:
            raise InvalidCallbackToken("capture choice is stale")
        return await apply_clarification_choice(
            store,
            owner_id=owner_id,
            draft_id=draft_id,
            expected_version=draft.version,
            choice_id=binding.action.removeprefix(action_prefix),
            preferences=profile,
            at=now,
        )
    raise InvalidCallbackToken("unknown capture callback")


async def _process_capture_callback_job(
    sessions: async_sessionmaker[AsyncSession],
    claim: JobClaim,
    inbound: _Inbound,
    *,
    language: str,
    chat_id: int,
    now: datetime,
) -> bool:
    try:
        async with sessions() as session, session.begin():
            current = await _capture_inbound(session, claim)
            if current is None or current.callback_data != inbound.callback_data:
                raise InvalidCallbackToken("capture callback changed during processing")
            receipt = await record_job_effect_once(
                session,
                claim,
                effect_key=PRIVATE_TEXT_EFFECT,
                result={"status": "capture_callback_applied"},
            )
            if not receipt.first_application:
                await complete_job(session, claim)
                return True
            reply = await _apply_capture_callback(
                session,
                SqlAlchemyCaptureStore(session),
                current,
                now=now,
            )
            current_profile = await SqlAlchemyIdentityStore(session).get_preferences(
                _inbound_owner(current)
            )
            await _queue_reply(
                session,
                claim,
                chat_id=chat_id,
                reply=reply,
                language=current_profile.language if current_profile else language,
                now=now,
            )
            await complete_job(session, claim)
        return True
    except (CaptureConflict, InvalidCallbackToken, ValueError):
        # The action transaction rolled back; persist only a safe recovery reply.
        async with sessions() as session, session.begin():
            current = await _capture_inbound(session, claim)
            if current is None or current.callback_data != inbound.callback_data:
                await complete_job(session, claim)
                return True
            receipt = await record_job_effect_once(
                session,
                claim,
                effect_key=PRIVATE_TEXT_EFFECT,
                result={"status": "capture_callback_rejected"},
            )
            if not receipt.first_application:
                await complete_job(session, claim)
                return True
            current_profile = await SqlAlchemyIdentityStore(session).get_preferences(
                _inbound_owner(current)
            )
            await _queue_reply(
                session,
                claim,
                chat_id=chat_id,
                reply=_stale_reply(current_profile.language if current_profile else language),
                language=current_profile.language if current_profile else language,
                now=now,
            )
            await complete_job(session, claim)
        return True


async def _consume_callback(
    session: AsyncSession, token: str, owner_id: UUID, now: datetime
) -> CallbackBinding:
    if not token or len(token.encode("utf-8")) > 64:
        raise InvalidCallbackToken("callback token is invalid")
    binding = await SqlAlchemyCallbackTokenStore(session).consume(
        hashlib.sha256(token.encode("utf-8")).hexdigest(), owner_id, now
    )
    if binding is None:
        raise InvalidCallbackToken("callback token is invalid or expired")
    return binding


def _conversation_context(
    draft: CaptureDraft,
    request_key: str,
    current_text: str,
    preferences: PreferencesSnapshot,
    now: datetime,
) -> ConversationContext:
    """Build the strict bounded prompt from owned draft history and confirmed preferences."""
    pending = json.dumps(draft.normalized_actions, ensure_ascii=False, separators=(",", ":"))
    messages = list(draft.messages[:-1])
    while True:
        history = "\n".join(
            f"{message.role}: {message.text}" for message in messages[-8:]
        )
        sections = []
        if history:
            sections.append("Prior conversation:\n" + history)
        if pending != "[]":
            sections.append("Pending normalized plan proposals:\n" + pending)
        sections.append("Current user turn:\n" + current_text)
        context_text = "\n\n".join(sections)
        if len(context_text) <= 8_000:
            break
        if messages:
            messages.pop(0)
        else:
            # The current inbound turn remains intact; ingress bounds it below this limit.
            context_text = current_text[-8_000:]
            break

    profile_values = (
        ("profile_version", preferences.version),
        ("language", preferences.language),
        ("home_zone", preferences.home_zone),
        ("reminder_calendar_days", preferences.reminder_calendar_days),
        ("reminder_elapsed_hours", preferences.reminder_elapsed_hours),
        ("quiet_start", preferences.quiet_start.isoformat()),
        ("quiet_end", preferences.quiet_end.isoformat()),
        ("review_time", preferences.review_time.isoformat()),
        ("capture_draft_version", draft.version),
        ("capture_draft_state", draft.state.value),
    )
    return ConversationContext(
        owner_id=preferences.owner_id,
        request_id=request_key,
        language=SourceLanguage(preferences.language),
        input_kind=InputKind.TEXT,
        text=context_text,
        reference_date=now.astimezone(ZoneInfo(preferences.home_zone)).date(),
        timezone=preferences.home_zone,
        preferences=tuple(
            PreferenceSnapshot(key=key, value=str(value)) for key, value in profile_values
        ),
    )


async def _queue_reply(
    session: AsyncSession,
    claim: JobClaim,
    *,
    chat_id: int,
    reply: CaptureTurnReply,
    language: str,
    now: datetime,
) -> None:
    pages = list(render_capture_response(reply.response, language=language))
    choices = reply.response.get("choices")
    choice_rows = (
        tuple(item for item in choices if isinstance(item, dict))
        if isinstance(choices, list)
        else ()
    )
    callback_tokens = CallbackTokens(SqlAlchemyCallbackTokenStore(session))
    choice_data: dict[str, str] = {}
    for choice in choice_rows:
        choice_id = choice.get("id")
        if not isinstance(choice_id, str):
            continue
        token = await callback_tokens.issue(
            owner_id=_claim_owner(claim),
            action=f"capture.choice:{choice_id}",
            target_id=str(reply.draft_id),
            target_version=reply.version,
            expires_at=now + _CALLBACK_TTL,
            now=now,
        )
        choice_data[choice_id] = capture_choice_callback_data(token)

    cancel_data = None
    if reply.state in {"collecting", "ready"}:
        token = await callback_tokens.issue(
            owner_id=_claim_owner(claim),
            action=CAPTURE_CANCEL_ACTION,
            target_id=str(reply.draft_id),
            target_version=reply.version,
            expires_at=now + _CALLBACK_TTL,
            now=now,
        )
        cancel_data = CAPTURE_CANCEL_PREFIX + token
    confirm_data = (
        capture_confirmation_callback_data(reply.confirmation_credential)
        if reply.confirmation_credential
        else None
    )
    markup = build_capture_keyboard(
        confirm_callback_data=confirm_data,
        cancel_callback_data=cancel_data,
        choices=choice_rows,
        choice_callback_data=choice_data,
        language=language,
    )
    keyboard = markup.model_dump(exclude_none=True) if markup is not None else None
    for index, page in enumerate(pages):
        page_markup = keyboard if index == len(pages) - 1 else None
        await add_outbox_event(
            session,
            EventEnvelope(
                id=uuid4(),
                event_type="telegram.reply.requested",
                schema_version=1,
                aggregate_id=_claim_owner(claim),
                aggregate_version=max(1, claim.conversation_sequence or 1) + index,
                owner_id=_claim_owner(claim),
                occurred_at=now,
                causation_id=claim.job_id,
                payload={
                    "chat_id": chat_id,
                    "markdown": page.markdown,
                    "plain_text": page.plain_text,
                    "handling": "handled",
                    "source_job_id": str(claim.job_id),
                    "page_index": index,
                    "page_count": len(pages),
                    **({"reply_markup": keyboard} if page_markup else {}),
                },
            ),
            claim=claim,
        )


def _is_capture_callback(value: str | None) -> bool:
    return value is not None and value.startswith(
        (CAPTURE_CONFIRM_PREFIX, CAPTURE_CANCEL_PREFIX, CAPTURE_CHOICE_PREFIX)
    )


def _target_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise InvalidCallbackToken("capture callback target is invalid") from None


def _claim_owner(claim: JobClaim) -> UUID:
    if claim.owner_id is None:
        raise ValueError("capture reply requires an owner-bound job")
    return claim.owner_id


def _inbound_owner(inbound: _Inbound) -> UUID:
    if inbound.inbox.owner_id is None:
        raise RuntimeError("capture inbox update has no trusted owner")
    return inbound.inbox.owner_id


def _stale_reply(language: str) -> CaptureTurnReply:
    text = (
        "This preview changed or expired. Send the task again to prepare a fresh preview."
        if language == "en"
        else "Предпросмотр изменился или истёк. Отправьте задачу ещё раз, чтобы получить новый."
    )
    return CaptureTurnReply(UUID(int=0), 0, "stale", {"kind": "reply", "text": text})
