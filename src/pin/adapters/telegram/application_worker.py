"""Apply durable private text updates and append render requests to the outbox."""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.agenda import (
    agenda_command,
    handle_agenda_callback,
    handle_agenda_command,
    remember_agenda_navigation,
)
from pin.adapters.telegram.capture import PlanningAssistant, process_capture_application_job
from pin.adapters.telegram.home_zone_runtime import (
    HOME_ZONE_ACTIONS,
    begin_home_zone_review,
    handle_home_zone_action,
    needs_home_zone_review,
    pending_home_zone_text_reply,
    stale_home_zone_action,
)
from pin.adapters.telegram.ingress import PostgresTelegramOwnerResolver, private_actor
from pin.adapters.telegram.onboarding import (
    DispatchDisposition,
    PrivateMessageResult,
    handle_private_text,
)
from pin.adapters.telegram.presentation.agenda import AgendaReply
from pin.adapters.telegram.presentation.callbacks import (
    CallbackTokens,
    InvalidCallbackToken,
)
from pin.adapters.telegram.presentation.formatting import escape_markdown_v2
from pin.adapters.telegram.presentation.onboarding import OnboardingReply, render_onboarding
from pin.adapters.telegram.presentation.payments import (
    render_cancel_result,
    render_payment_result,
    render_payment_support,
    render_payment_terms,
    render_terms_accepted,
)
from pin.adapters.telegram.presentation.subscription import render_subscription_status
from pin.adapters.telegram.presentation.tokens import SqlAlchemyCallbackTokenStore
from pin.contexts.billing.api import (
    AcceptPaymentTerms,
    CreateStarsOrder,
    PaymentRejected,
    ReadSubscriptionStatus,
    RecordStarsPayment,
    SqlAlchemyAccessUsageStore,
    SqlAlchemyStarsPaymentStore,
    StartTrial,
    TrialActivationResult,
    ViewPaymentTerms,
)
from pin.contexts.identity.application.onboarding import OnboardingResult
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.notifications.infrastructure.outbox_consumer import (
    SqlAlchemyNotificationScheduleEventConsumer,
)
from pin.contexts.planning.api import SqlAlchemyReminderScheduleStore
from pin.contexts.planning.application.home_zone_changes import HomeZoneChangeRejected
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event
from pin.platform.jobs import (
    JobClaim,
    JobLane,
    claim_job,
    complete_job,
    fail_job,
    record_job_effect_once,
)
from pin.platform.models import InboxUpdate

logger = logging.getLogger("pin.telegram.application_worker")
LEASE_FOR = timedelta(seconds=45)
POLL_SECONDS = 0.5


async def claim_application_job(
    sessions: async_sessionmaker[AsyncSession], *, worker_id: str
) -> JobClaim | None:
    async with sessions() as session, session.begin():
        return await claim_job(
            session,
            lane=JobLane.APPLICATION,
            worker_id=worker_id,
            lease_for=LEASE_FOR,
        )


async def process_application_job(
    sessions: async_sessionmaker[AsyncSession],
    claim: JobClaim,
    *,
    now: datetime,
    assistant: PlanningAssistant | None = None,
    payment_support_contact: str | None = None,
) -> None:
    """Apply one fenced work item and persist its reply request atomically."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("application worker clock must be timezone-aware")
    if await process_capture_application_job(sessions, claim, now=now, assistant=assistant):
        return
    async with sessions() as session, session.begin():
        receipt = await record_job_effect_once(
            session,
            claim,
            effect_key="private_text_applied",
            result={"status": "processed"},
        )
        if not receipt.first_application:
            await complete_job(session, claim)
            return

        inbox_id = _uuid_field(claim.payload.get("inbox_id"))
        inbox = await session.get(InboxUpdate, inbox_id)
        if inbox is None:
            raise RuntimeError("claimed job references a missing inbox update")
        if inbox.owner_id is None or claim.owner_id != inbox.owner_id:
            raise RuntimeError("claimed job owner does not match its trusted inbox mapping")

        actor = private_actor(inbox.payload)
        callback = inbox.payload.get("callback_query")
        message = (
            callback.get("message") if isinstance(callback, dict) else inbox.payload.get("message")
        )
        is_callback = actor is not None and actor[1] == "callback_query"
        if (
            actor is None
            or (actor[1] != "message" and not is_callback)
            or not isinstance(message, dict)
        ):
            await complete_job(session, claim)
            return
        telegram_user_id, _, _ = actor
        trusted_owner_id = await PostgresTelegramOwnerResolver().resolve(
            session, bot_id=inbox.bot_id, telegram_user_id=telegram_user_id
        )
        if trusted_owner_id != inbox.owner_id:
            raise RuntimeError("persisted Telegram mapping does not match the inbox owner")
        chat = message.get("chat")
        if not isinstance(chat, dict) or chat.get("id") != telegram_user_id:
            raise RuntimeError(
                "persisted private update no longer satisfies the ingress identity check"
            )

        store = SqlAlchemyIdentityStore(session)
        trial_activator = _BillingTrialActivator(session)
        agenda_reply: AgendaReply | None = None
        if is_callback:
            callback_data = callback.get("data") if isinstance(callback, dict) else None
            result, agenda_reply = await _handle_callback(
                session,
                store,
                owner_id=inbox.owner_id,
                telegram_user_id=telegram_user_id,
                data=callback_data,
                now=now.astimezone(UTC),
                trial_activator=trial_activator,
            )
        else:
            message_text = message.get("text")
            successful_payment = message.get("successful_payment")
            if isinstance(successful_payment, dict):
                profile = await store.get_preferences(inbox.owner_id)
                paid = False
                expiration = successful_payment.get("subscription_expiration_date")
                try:
                    paid = await SqlAlchemyStarsPaymentStore(session).record_payment(
                        RecordStarsPayment(
                            owner_id=inbox.owner_id,
                            telegram_user_id=telegram_user_id,
                            invoice_payload=successful_payment.get("invoice_payload", ""),
                            telegram_charge_id=successful_payment.get(
                                "telegram_payment_charge_id", ""
                            ),
                            provider_charge_id=(
                                successful_payment.get("provider_payment_charge_id") or None
                            ),
                            currency=successful_payment.get("currency", ""),
                            total_amount=successful_payment.get("total_amount", -1),
                            is_recurring=successful_payment.get("is_recurring") is True,
                            is_first_recurring=(
                                successful_payment.get("is_first_recurring") is True
                            ),
                            subscription_expiration_date=(
                                datetime.fromtimestamp(expiration, UTC)
                                if type(expiration) is int and expiration > 0
                                else None
                            ),
                            at=now.astimezone(UTC),
                        )
                    )
                except (PaymentRejected, OverflowError, OSError, ValueError):
                    logger.warning("telegram_stars_receipt_rejected")
                payment_result_reply = render_payment_result(
                    profile.language if profile else "en", accepted=paid
                )
                result = PrivateMessageResult(
                    DispatchDisposition.HANDLED,
                    OnboardingResult("unhandled", preferences=profile),
                    payment_result_reply,
                )
                message_text = "/__payment_receipt__"
            elif not isinstance(message_text, str):
                await complete_job(session, claim)
                return
            view = agenda_command(message_text)
            words = message_text.strip().split(maxsplit=1)
            command = words[0].split("@", 1)[0] if words else ""
            subscription = command == "/subscription"
            command_profile = await store.get_preferences(inbox.owner_id)
            active_draft = (
                await store.get_onboarding(inbox.owner_id)
                if command.casefold() == "/cancel"
                else None
            )
            cancel_onboarding = active_draft is not None and active_draft.step != "complete"
            payment_reply = (
                await _handle_payment_command(
                    session,
                    owner_id=inbox.owner_id,
                    telegram_user_id=telegram_user_id,
                    command=command,
                    now=now.astimezone(UTC),
                    payment_support_contact=payment_support_contact,
                    language=command_profile.language if command_profile else "en",
                )
                if isinstance(message_text, str) and not cancel_onboarding
                else None
            )
            profile = command_profile if view is not None or subscription else None
            if isinstance(successful_payment, dict):
                pass
            elif payment_reply is not None:
                result = PrivateMessageResult(
                    DispatchDisposition.HANDLED,
                    OnboardingResult("unhandled", preferences=command_profile),
                    payment_reply,
                )
            elif subscription and profile is not None:
                status = await SqlAlchemyAccessUsageStore(session).read_subscription_status(
                    ReadSubscriptionStatus(inbox.owner_id, now.astimezone(UTC))
                )
                renewal_state = await SqlAlchemyStarsPaymentStore(session).renewal_status(
                    inbox.owner_id
                )
                subscription_reply = render_subscription_status(
                    status, language=profile.language, zone_name=profile.home_zone
                )
                if renewal_state == "cancelled":
                    renewal_text = (
                        "Автопродление отключено; оплаченный период сохраняется до даты выше."
                        if profile.language == "ru"
                        else "Auto-renewal is disabled; paid access remains through its end date."
                    )
                elif renewal_state in {"queued", "sending"}:
                    renewal_text = (
                        "Отключение автопродления обрабатывается; проверьте статус позже."
                        if profile.language == "ru"
                        else "Renewal cancellation is being processed; check again shortly."
                    )
                elif renewal_state in {"failed", "unknown"}:
                    renewal_text = (
                        "Статус отключения продления требует проверки: /paysupport."
                        if profile.language == "ru"
                        else "Renewal cancellation needs support review: /paysupport."
                    )
                elif status.period_kind is not None and status.period_kind.value == "paid":
                    renewal_text = (
                        "Для отключения будущих продлений отправьте /cancel."
                        if profile.language == "ru"
                        else "Send /cancel to stop future renewals."
                    )
                else:
                    renewal_text = (
                        "Для перехода на подписку прочитайте /terms."
                        if profile.language == "ru"
                        else "Read /terms to upgrade to a paid subscription."
                    )
                subscription_reply = replace(
                    subscription_reply,
                    markdown=(
                        f"{subscription_reply.markdown}\n\n{escape_markdown_v2(renewal_text)}"
                    ),
                    plain_text=f"{subscription_reply.plain_text}\n\n{renewal_text}",
                )
                result = PrivateMessageResult(
                    DispatchDisposition.HANDLED,
                    OnboardingResult("unhandled", preferences=profile),
                    subscription_reply,
                )
            elif view is not None and profile is not None:
                agenda_reply = await handle_agenda_command(
                    session,
                    owner_id=inbox.owner_id,
                    preferences=profile,
                    view=view,
                    now=now.astimezone(UTC),
                )
                result = PrivateMessageResult(
                    disposition=DispatchDisposition.HANDLED,
                    onboarding=OnboardingResult("unhandled", preferences=profile),
                    reply=None,
                )
            else:
                active_draft = active_draft or await store.get_onboarding(inbox.owner_id)
                current_profile = await store.get_preferences(inbox.owner_id)
                if (
                    message_text.strip().casefold() in {"confirm", "yes", "да", "подтверждаю"}
                    and needs_home_zone_review(active_draft, current_profile)
                    and active_draft is not None
                    and current_profile is not None
                ):
                    outcome = (
                        pending_home_zone_text_reply(active_draft, current_profile)
                        if "_home_zone_change_flow" in active_draft.values
                        else await begin_home_zone_review(
                            store, draft=active_draft, profile=current_profile
                        )
                    )
                    result = PrivateMessageResult(
                        DispatchDisposition.HANDLED, outcome.onboarding, outcome.reply
                    )
                else:
                    result = await handle_private_text(
                        store,
                        owner_id=inbox.owner_id,
                        telegram_user_id=telegram_user_id,
                        text=message_text,
                        now=now.astimezone(UTC),
                        trial_activator=trial_activator,
                    )
        reply: AgendaReply | OnboardingReply | None = agenda_reply or result.reply
        reply_markup = await _reply_markup(session, store, inbox.owner_id, reply, now)
        if reply is not None:
            version = max(1, claim.conversation_sequence or 1)
            await add_outbox_event(
                session,
                EventEnvelope(
                    id=uuid4(),
                    event_type="telegram.reply.requested",
                    schema_version=1,
                    aggregate_id=inbox.owner_id,
                    aggregate_version=version,
                    owner_id=inbox.owner_id,
                    occurred_at=now.astimezone(UTC),
                    causation_id=claim.job_id,
                    payload={
                        "chat_id": telegram_user_id,
                        "markdown": reply.markdown,
                        "plain_text": reply.plain_text,
                        "handling": result.disposition.value,
                        "source_job_id": str(claim.job_id),
                        **({"reply_markup": reply_markup} if reply_markup else {}),
                    },
                ),
                claim=claim,
            )
        if result.onboarding.home_zone_change is not None:
            handoff = result.onboarding.home_zone_change
            await add_outbox_event(
                session,
                EventEnvelope(
                    id=uuid4(),
                    event_type="identity.home_zone_change.requested",
                    schema_version=1,
                    aggregate_id=handoff.owner_id,
                    aggregate_version=handoff.preferences_version,
                    owner_id=handoff.owner_id,
                    occurred_at=handoff.requested_at,
                    causation_id=claim.job_id,
                    payload={
                        "previous_home_zone": handoff.previous_home_zone,
                        "requested_home_zone": handoff.requested_home_zone,
                        "preferences_version": handoff.preferences_version,
                    },
                ),
                claim=claim,
            )
        await complete_job(session, claim)


async def run_application_worker(
    sessions: async_sessionmaker[AsyncSession],
    *,
    worker_id: str,
    assistant: PlanningAssistant | None = None,
    payment_support_contact: str | None = None,
) -> None:
    while True:
        try:
            await process_pending_reminder_schedules(sessions, now=datetime.now(UTC))
        except Exception as error:
            logger.error("reminder_reconciliation_failed: error=%s", type(error).__name__)
        claim = await claim_application_job(sessions, worker_id=worker_id)
        if claim is None:
            await asyncio.sleep(POLL_SECONDS)
            continue
        try:
            await process_application_job(
                sessions,
                claim,
                now=datetime.now(UTC),
                assistant=assistant,
                payment_support_contact=payment_support_contact,
            )
        except Exception as error:
            logger.error(
                "application_job_failed: job_id=%s error=%s",
                claim.job_id,
                type(error).__name__,
            )
            try:
                async with sessions() as session, session.begin():
                    await fail_job(
                        session,
                        claim,
                        error=type(error).__name__,
                    )
            except Exception:
                logger.warning("application_job_failure_record_failed: job_id=%s", claim.job_id)


async def process_pending_reminder_schedules(
    sessions: async_sessionmaker[AsyncSession], *, now: datetime, limit: int = 100
) -> int:
    """Reconcile a bounded committed-event batch even when Telegram work is idle."""
    async with sessions() as session, session.begin():
        current_schedules = SqlAlchemyReminderScheduleStore(
            session, SqlAlchemyIdentityStore(session)
        )
        return await SqlAlchemyNotificationScheduleEventConsumer(
            session, current_schedules
        ).consume_pending(at=now, limit=limit)


class _BillingTrialActivator:
    """Published Billing API adapter sharing Identity's transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._store = SqlAlchemyAccessUsageStore(session)

    async def activate_trial(
        self, *, owner_id: UUID, telegram_user_id: int, at: datetime
    ) -> TrialActivationResult:
        return await self._store.activate_trial(StartTrial(owner_id, telegram_user_id, at))


async def _handle_callback(
    session: AsyncSession,
    store: SqlAlchemyIdentityStore,
    *,
    owner_id: UUID,
    telegram_user_id: int,
    data: object,
    now: datetime,
    trial_activator: _BillingTrialActivator,
) -> tuple[PrivateMessageResult, AgendaReply | None]:
    draft = await store.get_onboarding(owner_id)
    profile = await store.get_preferences(owner_id)
    active_draft = draft if draft is not None and draft.step != "complete" else None
    profile_version = profile.version if profile else 0
    onboarding_version = active_draft.version if active_draft is not None else profile_version
    language = (
        profile.language
        if profile
        else "ru"
        if draft and draft.values.get("language") == "ru"
        else "en"
    )
    callbacks = CallbackTokens(SqlAlchemyCallbackTokenStore(session))
    try:
        if not isinstance(data, str):
            raise InvalidCallbackToken("missing callback data")
        binding = await callbacks.consume(
            data,
            actor_id=owner_id,
            current_target_versions={
                "agenda_page": profile_version,
                "agenda_completed": profile_version,
                "settings": profile_version,
                "answer": onboarding_version,
                "cancel": onboarding_version,
                **{action: onboarding_version for action in HOME_ZONE_ACTIONS},
            },
            now=now,
        )
        agenda_reply = (
            await handle_agenda_callback(
                session,
                owner_id=owner_id,
                preferences=profile,
                binding=binding,
                now=now,
            )
            if profile
            else None
        )
        if binding.action in HOME_ZONE_ACTIONS:
            try:
                async with session.begin_nested():
                    outcome = await handle_home_zone_action(
                        session,
                        store,
                        binding=binding,
                        callback_data=data,
                        owner_id=owner_id,
                        now=now,
                    )
            except (HomeZoneChangeRejected, ValueError):
                outcome = await stale_home_zone_action(store, owner_id)
            return (
                PrivateMessageResult(
                    DispatchDisposition.HANDLED, outcome.onboarding, outcome.reply
                ),
                None,
            )
        if agenda_reply is not None and profile is not None:
            return (
                PrivateMessageResult(
                    disposition=DispatchDisposition.HANDLED,
                    onboarding=OnboardingResult("unhandled", preferences=profile),
                    reply=None,
                ),
                agenda_reply,
            )
        if (
            binding.action == "settings"
            and binding.target_id == "settings"
            and active_draft is None
            and profile
        ):
            text = "/settings"
        elif binding.action == "cancel" and active_draft:
            generation, target = binding.target_id.split("|", maxsplit=1)
            if generation != str(active_draft.draft_id) or target != "draft":
                raise InvalidCallbackToken("callback belongs to an earlier draft")
            text = "/cancel"
        elif binding.action == "answer" and active_draft and "|" in binding.target_id:
            generation, step, value = binding.target_id.split("|", maxsplit=2)
            if generation != str(active_draft.draft_id) or step != active_draft.step:
                raise InvalidCallbackToken("callback step is stale")
            allowed = {
                "language": {"en", "ru"},
                "home_zone": {"Asia/Almaty"},
                "reminders": {"suggested", "keep"},
                "quiet_review": {"suggested", "keep"},
                "confirm": {"confirm"},
            }
            if value not in allowed.get(step, set()):
                raise InvalidCallbackToken("callback action is not valid for this step")
            text = {
                "suggested": "по умолчанию" if language == "ru" else "suggested",
                "keep": "оставить" if language == "ru" else "keep",
                "confirm": "подтверждаю" if language == "ru" else "confirm",
            }.get(value, value)
        else:
            raise InvalidCallbackToken("callback action does not match current state")
        if (
            binding.action == "answer"
            and text.strip().casefold() in {"confirm", "yes", "да", "подтверждаю"}
            and needs_home_zone_review(active_draft, profile)
            and active_draft is not None
            and profile is not None
        ):
            outcome = (
                pending_home_zone_text_reply(active_draft, profile)
                if "_home_zone_change_flow" in active_draft.values
                else await begin_home_zone_review(store, draft=active_draft, profile=profile)
            )
            return (
                PrivateMessageResult(
                    DispatchDisposition.HANDLED, outcome.onboarding, outcome.reply
                ),
                None,
            )
        return (
            await handle_private_text(
                store,
                owner_id=owner_id,
                telegram_user_id=telegram_user_id,
                text=text,
                now=now,
                trial_activator=trial_activator,
            ),
            None,
        )
    except InvalidCallbackToken:
        state = "draft_conflict" if active_draft else "not_in_progress"
        result = OnboardingResult(
            state,
            preferences=profile,
            draft=dict(active_draft.values) if active_draft else None,
        )
        recovery = (
            "For an agenda page, send /today or /week to open a fresh view."
            if language == "en"
            else "Чтобы открыть актуальную повестку, отправьте /today или /week."
        )
        recovery_reply = render_onboarding(result)
        if recovery_reply is not None:
            recovery_reply = replace(
                recovery_reply,
                markdown=f"{recovery_reply.markdown}\n\n{escape_markdown_v2(recovery)}",
                plain_text=f"{recovery_reply.plain_text}\n\n{recovery}",
            )
        else:
            recovery_reply = OnboardingReply(
                escape_markdown_v2(recovery), recovery, handling="handled"
            )
        return (
            PrivateMessageResult(
                disposition=DispatchDisposition.HANDLED,
                onboarding=result,
                reply=recovery_reply,
            ),
            None,
        )


async def _reply_markup(
    session: AsyncSession,
    store: SqlAlchemyIdentityStore,
    owner_id: UUID,
    reply: OnboardingReply | AgendaReply | None,
    now: datetime,
) -> dict[str, object] | None:
    if reply is None or not reply.buttons:
        return None
    draft = await store.get_onboarding(owner_id)
    profile = await store.get_preferences(owner_id)
    active_draft = draft if draft is not None and draft.step != "complete" else None
    callbacks = CallbackTokens(SqlAlchemyCallbackTokenStore(session))
    rows: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for button in reply.buttons:
        version = (
            profile.version
            if button.action.startswith("agenda_") and profile
            else active_draft.version
            if button.action in HOME_ZONE_ACTIONS and active_draft
            else profile.version
            if button.action == "settings" and profile
            else active_draft.version
            if active_draft
            else 0
        )
        target_id = button.value
        await remember_agenda_navigation(
            session,
            owner_id=owner_id,
            action=button.action,
            target_id=target_id,
        )
        if active_draft is not None and button.action in {"answer", "cancel"}:
            target_id = f"{active_draft.draft_id}|{button.value}"
        token = await callbacks.issue(
            owner_id=owner_id,
            action=button.action,
            target_id=target_id,
            target_version=version,
            expires_at=now + timedelta(minutes=15),
            now=now,
        )
        row.append({"text": button.text, "callback_data": token})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return {"inline_keyboard": rows}


async def _handle_payment_command(
    session: AsyncSession,
    *,
    owner_id: UUID,
    telegram_user_id: int,
    command: str,
    now: datetime,
    language: str,
    payment_support_contact: str | None,
) -> OnboardingReply | None:
    payments = SqlAlchemyStarsPaymentStore(session)
    if command == "/terms":
        await payments.view_terms(ViewPaymentTerms(owner_id, now))
        return render_payment_terms(language)
    if command == "/acceptterms":
        try:
            await payments.accept_terms(AcceptPaymentTerms(owner_id, now))
        except PaymentRejected:
            text = (
                "Open /terms and read the subscription terms first."
                if language == "en"
                else "Сначала откройте /terms и прочитайте условия."
            )
            return OnboardingReply(escape_markdown_v2(text), text)
        return render_terms_accepted(language)
    if command == "/buy":
        try:
            invoice = await payments.create_order(
                CreateStarsOrder(owner_id, telegram_user_id, secrets.token_urlsafe(32), now)
            )
        except PaymentRejected:
            text = (
                "A valid account with no active paid period and accepted terms is required. "
                "Check /terms and /subscription."
                if language == "en"
                else "Нужны действующий аккаунт, принятые условия и отсутствие активной платной "
                "подписки. Проверьте /terms и /subscription."
            )
            return OnboardingReply(escape_markdown_v2(text), text)
        if invoice.invoice_status == "sent":
            text = (
                "Счёт уже отправлен; используйте его сообщение для оплаты."
                if language == "ru"
                else "The invoice was already sent; use its existing message to pay."
            )
        elif invoice.invoice_status in {"sending", "unknown"}:
            text = (
                "Неизвестно, доставлен ли счёт. Не создавайте повторную оплату; "
                "обратитесь в /paysupport."
                if language == "ru"
                else "Invoice delivery is uncertain. Avoid creating another payment; "
                "use /paysupport."
            )
        else:
            text = (
                "Заказ создан. Счёт Telegram появится после доставки ботом; "
                "оплата пока не подтверждена."
                if language == "ru"
                else "Order created. The Telegram invoice will arrive from the delivery worker; "
                "no payment is confirmed yet."
            )
        return OnboardingReply(escape_markdown_v2(text), text)
    if command == "/cancel":
        try:
            state = await payments.request_cancellation(owner_id=owner_id, at=now)
        except PaymentRejected:
            text = (
                "No active renewal could be cancelled. Check /subscription or use /paysupport."
                if language == "en"
                else "Не удалось отключить продление. Проверьте /subscription или обратитесь "
                "в /paysupport."
            )
            return OnboardingReply(escape_markdown_v2(text), text)
        return render_cancel_result(language, state)
    if command == "/paysupport":
        return render_payment_support(language, payment_support_contact)
    return None


def _uuid_field(value: object) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError("claimed job has an invalid inbox_id") from None
