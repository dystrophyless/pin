"""Explicit live RU/EN model-smoke composition root for a disposable billing owner."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pin.bootstrap.config import load_model_smoke_settings
from pin.contexts.billing.api import CheckAccess, Operation, SqlAlchemyAccessUsageStore
from pin.contexts.conversation.application.service import ROUTE, BoundedPlanningAssistant
from pin.contexts.conversation.infrastructure.openrouter import OpenRouterTransport
from pin.contexts.conversation.infrastructure.smoke import (
    MAX_SMOKE_SPEND_USD,
    SmokeBudgetExceeded,
    SmokeBudgetTransport,
    SmokeRunReport,
    fetch_current_catalog_preview,
)
from pin.contexts.conversation.infrastructure.usage import (
    SqlAlchemyProviderUsageRecorder,
    SqlAlchemyTurnAllowance,
)
from pin.contexts.identity.api import (
    SqlAlchemyIdentityStore,
    current_processing_authorization,
)
from pin.persistence.async_runner import run_local_async
from pin.persistence.uow import SqlAlchemyUnitOfWork


async def _run_live_smoke(owner_id: UUID) -> SmokeRunReport:
    """Require current public pricing and an existing active owner before provider I/O."""
    settings = load_model_smoke_settings()
    engine = create_async_engine(settings.database_url.get_secret_value())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider = OpenRouterTransport(api_key=settings.openrouter_api_key)
    try:
        async with SqlAlchemyUnitOfWork(factory) as uow:
            identity = SqlAlchemyIdentityStore(uow.session)
            authorization = await current_processing_authorization(identity, owner_id=owner_id)
            if authorization is None:
                raise SmokeBudgetExceeded(
                    "the requested disposable owner has no current processing-consent grant"
                )
            access = await SqlAlchemyAccessUsageStore(uow.session).lock_access(
                CheckAccess(owner_id, Operation.AI_TURN, datetime.now(UTC))
            )
            if not access.allowed:
                raise SmokeBudgetExceeded(
                    "the requested disposable owner has no active AI-turn access period"
                )
        preview = await fetch_current_catalog_preview(cap_usd=MAX_SMOKE_SPEND_USD)
        bounded_transport = SmokeBudgetTransport(transport=provider, preview=preview)
        assistant = BoundedPlanningAssistant(
            transport=bounded_transport,
            usage=SqlAlchemyProviderUsageRecorder(factory),
            allowance=SqlAlchemyTurnAllowance(factory),
            now=lambda: datetime.now(UTC),
        )
        return await bounded_transport.run(
            assistant, owner_id=owner_id, processing_authorization=authorization
        )
    finally:
        await provider.aclose()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument(
        "--owner-id",
        type=UUID,
        required=True,
        help="existing disposable billing owner with an active period and two AI turns",
    )
    args = parser.parse_args()
    report: SmokeRunReport = run_local_async(lambda: _run_live_smoke(args.owner_id))
    print(
        json.dumps(
            {
                "mode": "live-smoke-complete",
                "route": ROUTE,
                "provider_calls": report.provider_calls,
                "provider_request_ids": report.provider_request_ids,
                "actual_cost_usd": str(report.actual_cost_usd),
                "cap_usd": str(MAX_SMOKE_SPEND_USD),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
