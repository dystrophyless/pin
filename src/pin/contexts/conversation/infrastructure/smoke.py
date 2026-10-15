"""Small, explicitly bounded RU/EN provider-smoke preparation and budget gate."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

import httpx

from pin.contexts.conversation.application.contracts import (
    CompletionTransport,
    PlanningAssistant,
)
from pin.contexts.conversation.application.dto import (
    ActionProposal,
    AppointmentSchedule,
    ConversationContext,
    ModelSuccess,
    TaskSchedule,
)
from pin.contexts.conversation.application.service import (
    _SYSTEM,
    MAX_OUTPUT_TOKENS,
    ROUTE,
)
from pin.contexts.conversation.domain.proposals import (
    InputKind,
    ProposalOperation,
    SourceLanguage,
)
from pin.platform.jobs import JobClaim

MAX_SMOKE_CALLS = 8
MAX_SMOKE_SPEND_USD = Decimal("0.01")
_PROMPT_TIER_LIMIT = 32_000
_REPAIR_OUTPUT_UTF8_BOUND = 4_000 * 4
_CHAT_ENVELOPE_BOUND = 1_024
_OWNER = UUID("b7a04bbb-4f6f-4af7-8f34-a37ba83093d4")


@dataclass(frozen=True, slots=True)
class SmokeScenario:
    name: str
    context: ConversationContext


@dataclass(frozen=True, slots=True)
class SmokeBudgetPreview:
    route: str
    scenarios: tuple[SmokeScenario, ...]
    max_calls: int
    max_input_tokens_per_call: int
    max_output_tokens_per_call: int
    maximum_usd: Decimal
    cap_usd: Decimal


class SmokeBudgetExceeded(RuntimeError):
    """The configured smoke exposure cap would be exceeded before another request."""


class SmokeCatalogRejected(ValueError):
    """The current public model catalog does not support the approved route budget."""


@dataclass(frozen=True, slots=True)
class SmokeRunReport:
    provider_calls: int
    provider_request_ids: tuple[str, ...]
    actual_cost_usd: Decimal


def smoke_scenarios() -> tuple[SmokeScenario, SmokeScenario]:
    """Return synthetic RU text and EN transcript cases with no real owner data."""
    return (
        SmokeScenario(
            name="ru-text-create-task",
            context=ConversationContext(
                owner_id=_OWNER,
                request_id="issue13-smoke-ru",
                language=SourceLanguage.RU,
                input_kind=InputKind.TEXT,
                text="Создай задачу прочитать главу завтра без времени.",
                reference_date=date(2026, 10, 13),
                reference_instant=datetime(2026, 10, 13, 7, tzinfo=UTC),
                timezone="Asia/Qyzylorda",
            ),
        ),
        SmokeScenario(
            name="en-transcript-create-appointment",
            context=ConversationContext(
                owner_id=_OWNER,
                request_id="issue13-smoke-en",
                language=SourceLanguage.EN,
                input_kind=InputKind.TRANSCRIPT,
                text="Create a project review appointment tomorrow at 10 AM for 30 minutes.",
                reference_date=date(2026, 10, 13),
                reference_instant=datetime(2026, 10, 13, 7, tzinfo=UTC),
                timezone="Asia/Qyzylorda",
            ),
        ),
    )


def build_preview(
    *,
    input_usd_per_million_tokens: Decimal = Decimal("0.03"),
    output_usd_per_million_tokens: Decimal = Decimal("0.13"),
    cap_usd: Decimal = MAX_SMOKE_SPEND_USD,
) -> SmokeBudgetPreview:
    """Build an offline exposure bound; this function reads no environment or network."""
    if input_usd_per_million_tokens < 0 or output_usd_per_million_tokens < 0:
        raise ValueError("catalog prices must be nonnegative")
    if cap_usd <= 0:
        raise ValueError("smoke spend cap must be positive")
    scenarios = smoke_scenarios()
    prompt_bounds = [
        len(_context_prompt(scenario.context).encode("utf-8")) for scenario in scenarios
    ]
    max_prompt_bytes = max(prompt_bounds)
    input_bound = (
        len(_SYSTEM.encode("utf-8"))
        + max_prompt_bytes
        + _REPAIR_OUTPUT_UTF8_BOUND
        + _CHAT_ENVELOPE_BOUND
    )
    # Provider prices change above 32k input tokens; reject a preview that crosses that tier.
    if input_bound >= _PROMPT_TIER_LIMIT:
        raise SmokeCatalogRejected("smoke prompt cannot be bounded below the current price tier")
    maximum = (
        Decimal(MAX_SMOKE_CALLS)
        * (
            Decimal(input_bound) * input_usd_per_million_tokens
            + Decimal(MAX_OUTPUT_TOKENS) * output_usd_per_million_tokens
        )
        / Decimal(1_000_000)
    )
    if maximum > cap_usd:
        raise SmokeCatalogRejected(
            f"worst-case smoke exposure {maximum} USD exceeds the {cap_usd} USD cap"
        )
    return SmokeBudgetPreview(
        route=ROUTE,
        scenarios=scenarios,
        max_calls=MAX_SMOKE_CALLS,
        max_input_tokens_per_call=input_bound,
        max_output_tokens_per_call=MAX_OUTPUT_TOKENS,
        maximum_usd=maximum,
        cap_usd=cap_usd,
    )


def validate_live_catalog(catalog: object, *, cap_usd: Decimal) -> SmokeBudgetPreview:
    """Validate current unauthenticated /models response before any completion request."""
    if not isinstance(catalog, list):
        raise SmokeCatalogRejected("OpenRouter model catalog must be an array")
    route_model = next(
        (item for item in catalog if isinstance(item, dict) and item.get("id") == ROUTE),
        None,
    )
    if not isinstance(route_model, dict):
        raise SmokeCatalogRejected(f"selected route {ROUTE} is absent from the live catalog")
    pricing = route_model.get("pricing")
    if not isinstance(pricing, dict):
        raise SmokeCatalogRejected("selected route has no current pricing data")
    try:
        input_price = Decimal(str(pricing["prompt"]))
        output_price = Decimal(str(pricing["completion"]))
    except (KeyError, InvalidOperation, TypeError) as exc:
        raise SmokeCatalogRejected("selected route has invalid current pricing data") from exc
    preview = build_preview(
        input_usd_per_million_tokens=input_price * Decimal(1_000_000),
        output_usd_per_million_tokens=output_price * Decimal(1_000_000),
        cap_usd=cap_usd,
    )
    context_length = route_model.get("context_length")
    if not isinstance(context_length, int) or context_length < preview.max_input_tokens_per_call:
        raise SmokeCatalogRejected("selected route context is below the smoke prompt bound")
    parameters = route_model.get("supported_parameters")
    if not isinstance(parameters, list) or not {
        "response_format",
        "reasoning",
    }.issubset(parameters):
        raise SmokeCatalogRejected(
            "selected route lacks required JSON or reasoning-control support"
        )
    return preview


async def fetch_current_catalog_preview(
    *,
    cap_usd: Decimal = MAX_SMOKE_SPEND_USD,
    client: httpx.AsyncClient | None = None,
) -> SmokeBudgetPreview:
    """Fetch the public catalog without credentials and validate it before live calls."""
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(8, connect=3))
    try:
        async with http.stream("GET", "https://openrouter.ai/api/v1/models") as response:
            response.raise_for_status()
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 8_000_000:
                    raise SmokeCatalogRejected("public model catalog exceeded 8 MB")
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SmokeCatalogRejected("public model catalog was not valid JSON") from exc
        models = payload.get("data") if isinstance(payload, dict) else None
        return validate_live_catalog(models, cap_usd=cap_usd)
    finally:
        if owns_client:
            await http.aclose()


class SmokeBudgetTransport:
    """Count and check actual receipts across both smoke scenarios."""

    def __init__(
        self,
        *,
        transport: CompletionTransport,
        preview: SmokeBudgetPreview,
    ) -> None:
        self._transport = transport
        self._preview = preview
        self.calls = 0
        self.actual_usd = Decimal("0")
        self._request_costs: dict[str, Decimal] = {}
        self._unknown_cost = False

    async def complete(self, payload: dict[str, object]) -> dict[str, object]:
        if payload.get("model") != self._preview.route:
            raise SmokeBudgetExceeded("smoke attempted an unapproved model route")
        if self.calls >= self._preview.max_calls:
            raise SmokeBudgetExceeded("smoke reached its total provider-call limit")
        messages = payload.get("messages")
        max_tokens = payload.get("max_tokens")
        if not isinstance(messages, list) or not isinstance(max_tokens, int):
            raise SmokeBudgetExceeded("smoke request is missing bounded messages or output limit")
        prompt_bytes = _CHAT_ENVELOPE_BOUND
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str):
                raise SmokeBudgetExceeded("smoke message content must be bounded text")
            prompt_bytes += len(content.encode("utf-8"))
        if prompt_bytes > self._preview.max_input_tokens_per_call:
            raise SmokeBudgetExceeded("smoke prompt exceeded its conservative token bound")
        if max_tokens > self._preview.max_output_tokens_per_call:
            raise SmokeBudgetExceeded("smoke output limit exceeded the approved bound")
        remaining_calls = self._preview.max_calls - self.calls
        maximum_next_exposure = (
            self._preview.maximum_usd * Decimal(remaining_calls) / self._preview.max_calls
        )
        if self._unknown_cost or self.actual_usd + maximum_next_exposure > self._preview.cap_usd:
            raise SmokeBudgetExceeded("remaining smoke exposure is unknown or exceeds its cap")
        self.calls += 1
        response = await self._transport.complete(payload)
        request_id = response.get("id")
        usage = response.get("usage")
        cost_raw = usage.get("cost") if isinstance(usage, dict) else None
        if not isinstance(request_id, str) or not request_id or cost_raw is None:
            self._unknown_cost = True
            return response
        try:
            cost = Decimal(str(cost_raw))
        except InvalidOperation:
            self._unknown_cost = True
            return response
        if cost < 0:
            self._unknown_cost = True
            return response
        previous = self._request_costs.get(request_id)
        if previous is not None and previous != cost:
            self._unknown_cost = True
            return response
        if previous is None:
            self._request_costs[request_id] = cost
            self.actual_usd += cost
        if self.actual_usd > self._preview.cap_usd:
            self._unknown_cost = True
        return response

    @property
    def has_unknown_cost(self) -> bool:
        return self._unknown_cost

    async def run(
        self,
        assistant: PlanningAssistant,
        *,
        at: datetime | None = None,
        claim: JobClaim | None = None,
        owner_id: UUID = _OWNER,
        request_prefix: str | None = None,
    ) -> SmokeRunReport:
        """Run two synthetic opt-in scenarios and require both typed proposal shapes."""
        if self._unknown_cost:
            raise SmokeBudgetExceeded("smoke cannot start with unknown provider cost")
        instant = at or datetime.now(UTC)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("smoke reference instant must include a timezone")
        prefix = request_prefix or f"issue13-smoke-{uuid4().hex}"
        request_ids: list[str] = []
        for index, scenario in enumerate(self._preview.scenarios, start=1):
            context = scenario.context.model_copy(
                update={"owner_id": owner_id, "request_id": f"{prefix}-{index}"}
            )
            outcome = await assistant.interpret(context, at=instant, claim=claim)
            if not isinstance(outcome, ModelSuccess):
                raise SmokeBudgetExceeded(
                    f"{scenario.name} did not return a successful typed proposal"
                )
            proposal = outcome.result
            if (
                not isinstance(proposal, ActionProposal)
                or proposal.operation is not ProposalOperation.CREATE_PLAN
                or proposal.language is not context.language
            ):
                raise SmokeBudgetExceeded(f"{scenario.name} returned the wrong proposal kind")
            if context.language is SourceLanguage.RU and not isinstance(
                proposal.fields.schedule, TaskSchedule
            ):
                raise SmokeBudgetExceeded("Russian task smoke did not return a task schedule")
            if context.language is SourceLanguage.EN and not isinstance(
                proposal.fields.schedule, AppointmentSchedule
            ):
                raise SmokeBudgetExceeded(
                    "English appointment smoke did not return an appointment schedule"
                )
            if self._unknown_cost:
                raise SmokeBudgetExceeded("provider cost is unknown after a smoke scenario")
            request_ids.extend(outcome.provider_request_ids)
        if (
            self.calls < len(self._preview.scenarios)
            or self.calls > self._preview.max_calls
            or len(request_ids) != self.calls
        ):
            raise SmokeBudgetExceeded("smoke provider call accounting is inconsistent")
        return SmokeRunReport(
            provider_calls=self.calls,
            provider_request_ids=tuple(request_ids),
            actual_cost_usd=self.actual_usd,
        )


def _context_prompt(context: ConversationContext) -> str:
    return json.dumps(
        {
            "language": context.language.value,
            "input_kind": context.input_kind.value,
            "text": context.text,
            "reference_date": context.reference_date.isoformat(),
            "reference_instant": context.reference_instant.isoformat(),
            "timezone": context.timezone,
            "plans": [item.model_dump(mode="json") for item in context.plans],
            "preferences": [item.model_dump(mode="json") for item in context.preferences],
            "targets": [item.model_dump(mode="json") for item in context.targets],
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


def preview_payload(preview: SmokeBudgetPreview) -> dict[str, object]:
    """Small machine-readable summary safe to print before requesting live approval."""
    return {
        "mode": "offline-preview-only",
        "route": preview.route,
        "scenarios": [
            {
                "name": item.name,
                "language": item.context.language.value,
                "input_kind": item.context.input_kind.value,
            }
            for item in preview.scenarios
        ],
        "max_calls_per_scenario": 4,
        "max_total_calls": preview.max_calls,
        "input_token_upper_bound_per_call": preview.max_input_tokens_per_call,
        "output_token_limit_per_call": preview.max_output_tokens_per_call,
        "maximum_exposure_usd": str(preview.maximum_usd),
        "approved_cap_usd": str(preview.cap_usd),
        "reads_environment_or_key": False,
        "provider_requests": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    if not args.preview:
        parser.error("only --preview is available; use `pin.bootstrap.model_smoke --live` to run")
    print(json.dumps(preview_payload(build_preview()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
