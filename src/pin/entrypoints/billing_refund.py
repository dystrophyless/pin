"""Explicit operator-only Telegram Stars refund command."""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.bootstrap.config import ProcessRole, SettingsError, load_settings
from pin.contexts.billing.api import (
    FinishStarsRefund,
    RequestStarsRefund,
    SqlAlchemyStarsPaymentStore,
)
from pin.observability import configure_logging, log_startup_configuration_rejection
from pin.persistence import create_engine, create_session_factory, run_local_async

logger = logging.getLogger("pin.billing.refund")


async def issue_manual_refund(
    sessions: async_sessionmaker[AsyncSession],
    bot: Bot,
    *,
    charge_id: str,
    operator: str,
) -> str:
    """Commit intent, call Telegram outside SQL, then commit its confirmed/unknown outcome."""
    now = datetime.now(UTC)
    async with sessions() as session, session.begin():
        user_id, charge_id = await SqlAlchemyStarsPaymentStore(session).begin_refund(
            RequestStarsRefund(charge_id, operator, now)
        )
    try:
        refunded = await bot.refund_star_payment(
            user_id=user_id,
            telegram_payment_charge_id=charge_id,
        )
    except TelegramBadRequest:
        outcome = "failed"
    except Exception as error:
        logger.error("stars_refund_outcome_unknown error=%s", type(error).__name__)
        outcome = "unknown"
    else:
        outcome = "confirmed" if refunded else "unknown"
    async with sessions() as session, session.begin():
        await SqlAlchemyStarsPaymentStore(session).finish_refund(
            FinishStarsRefund(charge_id, outcome, datetime.now(UTC))
        )
    return outcome


def main() -> None:
    parser = argparse.ArgumentParser(prog="pin-billing-refund")
    parser.add_argument("--charge-id", required=True)
    parser.add_argument(
        "--operator", required=True, help="internal operator identity for the audit record"
    )
    parser.add_argument("--execute", action="store_true", help="issue this manual full refund")
    args = parser.parse_args()
    if not args.execute:
        parser.error("pass --execute after reviewing the exact charge ID and owner")
    configure_logging()
    try:
        settings = load_settings(ProcessRole.DELIVERY_WORKER)
    except SettingsError as error:
        log_startup_configuration_rejection(
            logger, error.diagnostic_category, error.diagnostic_fields
        )
        raise SystemExit(2) from None
    if settings.bot_token is None:
        raise SystemExit(2)
    bot = Bot(token=settings.bot_token.get_secret_value())
    engine = create_engine(settings.database_url.get_secret_value())
    sessions = create_session_factory(engine)

    async def run() -> None:
        try:
            result = await issue_manual_refund(
                sessions, bot, charge_id=args.charge_id, operator=args.operator
            )
            print(f"refund outcome: {result}")
        finally:
            await bot.session.close()
            await engine.dispose()

    run_local_async(run)


if __name__ == "__main__":
    main()
