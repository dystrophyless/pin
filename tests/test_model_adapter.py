from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from pin.bootstrap.config import load_model_smoke_settings
from pin.contexts.conversation.application.contracts import (
    CallStart,
    CallUsage,
    CompletionTransport,
    ReservationStatus,
)
from pin.contexts.conversation.application.dto import (
    AppointmentSchedule,
    ConversationContext,
    ModelFailure,
    ModelSuccess,
    PlanSnapshot,
    PreferenceSnapshot,
    RelativeReminder,
    TargetSnapshot,
)
from pin.contexts.conversation.application.service import (
    MAX_CALLS,
    ROUTE,
    BoundedPlanningAssistant,
)
from pin.contexts.conversation.domain.proposals import (
    FailureCategory,
    InputKind,
    SourceLanguage,
)
from pin.contexts.conversation.infrastructure.smoke import (
    MAX_SMOKE_SPEND_USD,
    SmokeBudgetExceeded,
    SmokeBudgetTransport,
    SmokeCatalogRejected,
    build_preview,
    fetch_current_catalog_preview,
    preview_payload,
    validate_live_catalog,
)

NOW = datetime(2026, 10, 13, 10, tzinfo=UTC)
OWNER = UUID("e232952d-3f9a-4f3d-a65a-09073dd06686")
PLAN = UUID("e232952d-3f9a-4f3d-a65a-09073dd06687")


class FakeTransport:
    def __init__(self, *responses: dict[str, object] | Exception) -> None:
        self.responses = list(responses)
        self.payloads: list[dict[str, object]] = []

    async def complete(self, payload: dict[str, object]) -> dict[str, object]:
        self.payloads.append(payload)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeUsageRecorder:
    def __init__(self) -> None:
        self.started: list[CallStart] = []
        self.reported: list[tuple[CallStart, CallUsage, str]] = []
        self.unknown: list[tuple[CallStart, str]] = []
        self.results: dict[tuple[UUID, str], tuple[dict[str, object], tuple[str, ...]]] = {}

    async def start_call(self, call: CallStart, *, claim: object | None = None) -> None:
        self.started.append(call)

    async def record_usage(
        self,
        call: CallStart,
        usage: CallUsage,
        *,
        result_category: str,
        claim: object | None = None,
    ) -> None:
        self.reported.append((call, usage, result_category))

    async def mark_unknown(
        self,
        call: CallStart,
        *,
        result_category: str,
        provider_request_id: str | None = None,
        claim: object | None = None,
    ) -> None:
        self.unknown.append((call, result_category))

    async def store_result(
        self,
        *,
        owner_id: UUID,
        turn_id: str,
        result: dict[str, object],
        provider_request_ids: tuple[str, ...],
        claim: object | None = None,
    ) -> None:
        self.results[(owner_id, turn_id)] = (result, provider_request_ids)

    async def get_result(
        self, *, owner_id: UUID, turn_id: str
    ) -> tuple[dict[str, object], tuple[str, ...]] | None:
        return self.results.get((owner_id, turn_id))


class FakeAllowance:
    def __init__(self, status: ReservationStatus = ReservationStatus.RESERVED) -> None:
        self.status = status
        self.reserved: list[tuple[UUID, str]] = []
        self.settled: list[tuple[UUID, str]] = []
        self.refunded: list[tuple[UUID, str]] = []

    async def reserve(
        self, *, owner_id: UUID, request_id: str, at: datetime, claim: object | None = None
    ) -> ReservationStatus:
        self.reserved.append((owner_id, request_id))
        return self.status

    async def settle(
        self, *, owner_id: UUID, request_id: str, at: datetime, claim: object | None = None
    ) -> None:
        self.settled.append((owner_id, request_id))

    async def refund(
        self, *, owner_id: UUID, request_id: str, at: datetime, claim: object | None = None
    ) -> None:
        self.refunded.append((owner_id, request_id))


def _context(
    *, language: SourceLanguage = SourceLanguage.RU, input_kind: InputKind = InputKind.TEXT
) -> ConversationContext:
    return ConversationContext(
        owner_id=OWNER,
        request_id="turn-1",
        language=language,
        input_kind=input_kind,
        text=(
            "Перенеси задачу на завтра"
            if language is SourceLanguage.RU
            else "Move it to tomorrow"
        ),
        reference_date=date(2026, 10, 13),
        timezone="Asia/Qyzylorda",
        plans=(
            PlanSnapshot(
                id=PLAN,
                version=3,
                title="Read chapter",
                due_at=datetime(2026, 10, 13, 13, tzinfo=UTC),
                status="open",
            ),
        ),
        preferences=(PreferenceSnapshot(key="language", value=language.value),),
        targets=(TargetSnapshot(id=PLAN, version=3, kind="occurrence", title="Read chapter"),),
    )


def _provider_response(content: str, request_id: str = "gen-test-1") -> dict[str, object]:
    return {
        "id": request_id,
        "model": ROUTE,
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 35,
            "total_tokens": 155,
            "cost": "0.0000073",
        },
    }


def _assistant(
    transport: CompletionTransport,
    usage: FakeUsageRecorder | None = None,
    allowance: FakeAllowance | None = None,
    *,
    max_calls: int = MAX_CALLS,
) -> tuple[BoundedPlanningAssistant, FakeUsageRecorder, FakeAllowance]:
    recorder = usage or FakeUsageRecorder()
    access = allowance or FakeAllowance()
    return (
        BoundedPlanningAssistant(
            transport=transport,
            usage=recorder,
            allowance=access,
            now=lambda: NOW,
            max_calls=max_calls,
        ),
        recorder,
        access,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "input_kind", "content"),
    [
        (
            SourceLanguage.RU,
            InputKind.TEXT,
            '{"kind":"reply","language":"ru","text":"Хорошо, перенесу после подтверждения."}',
        ),
        (
            SourceLanguage.EN,
            InputKind.TRANSCRIPT,
            '{"kind":"reply","language":"en","text":"I can move it after you confirm."}',
        ),
    ],
)
async def test_valid_ru_and_en_inputs_settle_one_turn_and_record_actual_usage(
    language: SourceLanguage, input_kind: InputKind, content: str
) -> None:
    transport = FakeTransport(_provider_response(content))
    assistant, usage, allowance = _assistant(transport)

    outcome = await assistant.interpret(_context(language=language, input_kind=input_kind), at=NOW)

    assert isinstance(outcome, ModelSuccess)
    assert outcome.result.language is language
    assert len(outcome.provider_request_ids) == 1
    assert len(usage.started) == len(usage.reported) == 1
    assert usage.reported[0][1].prompt_tokens == 120
    assert usage.reported[0][1].completion_tokens == 35
    assert usage.reported[0][1].cost_usd == Decimal("0.0000073")
    assert allowance.reserved == allowance.settled == [(OWNER, "turn-1")]
    assert allowance.refunded == []
    request = transport.payloads[0]
    assert request["model"] == ROUTE
    assert isinstance(request["max_tokens"], int) and request["max_tokens"] <= 512
    assert request["response_format"] == {"type": "json_object"}
    assert request["reasoning"] == {"enabled": False}
    prompt = str(request["messages"])
    assert str(OWNER) not in prompt
    assert "Asia/Qyzylorda" in prompt
    assert "2026-10-13" in prompt
    assert "propose_change_occurrence" in prompt
    assert "propose_complete" in prompt
    assert "or relative with basis=calendar_day" in prompt
    assert "basis=calendar_day" in prompt
    assert "provenance=default|explicit" in prompt


@pytest.mark.asyncio
async def test_valid_owner_filtered_proposal_is_typed_and_never_executes() -> None:
    content = (
        '{"kind":"proposal","language":"ru","operation":"propose_change_occurrence",'
        f'"target_id":"{PLAN}","expected_version":3,"fields":{{"schedule":{{"kind":"appointment",'
        '"local_start":"2026-10-14T09:00:00","timezone":"Asia/Qyzylorda",'
        '"duration_minutes":30}}}'
    )
    transport = FakeTransport(_provider_response(content))
    assistant, usage, allowance = _assistant(transport)

    outcome = await assistant.interpret(_context(), at=NOW)

    assert isinstance(outcome, ModelSuccess)
    assert outcome.result.kind == "proposal"
    assert outcome.result.target_id == PLAN
    assert usage.reported[0][2] == "valid"
    assert allowance.settled == [(OWNER, "turn-1")]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["propose_complete", "propose_cancel"])
async def test_complete_and_cancel_accept_empty_fields(
    operation: str,
) -> None:
    content = (
        '{"kind":"proposal","language":"ru",'
        f'"operation":"{operation}","target_id":"{PLAN}",'
        '"expected_version":3,"fields":{}}'
    )
    assistant, _, _ = _assistant(FakeTransport(_provider_response(content)))

    outcome = await assistant.interpret(_context(), at=NOW)

    assert isinstance(outcome, ModelSuccess)
    assert outcome.result.operation.value == operation


@pytest.mark.asyncio
async def test_change_proposal_can_explicitly_clear_optional_description() -> None:
    content = (
        '{"kind":"proposal","language":"ru",'
        '"operation":"propose_change_occurrence",'
        f'"target_id":"{PLAN}","expected_version":3,'
        '"fields":{"description":null}}'
    )
    assistant, _, _ = _assistant(FakeTransport(_provider_response(content)))

    outcome = await assistant.interpret(_context(), at=NOW)

    assert isinstance(outcome, ModelSuccess)
    assert outcome.result.fields.model_fields_set == {"description"}
    assert outcome.result.fields.description is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        '{"kind":"tool_call","name":"delete_database"}',
        '{"kind":"reply","language":"ru","text":"ok","owner_id":"' + str(OWNER) + '"}',
        '{"kind":"proposal","language":"ru","operation":"unknown","fields":{}}',
        '{"kind":"proposal","language":"ru","operation":"propose_change_occurrence",'
        '"target_id":"00000000-0000-0000-0000-000000000999","expected_version":1,'
        '"fields":{"title":"steal"}}',
        '{"kind":"proposal","language":"en","operation":"propose_create_plan",'
        '"fields":{"title":"not source language"}}',
        '{"kind":"proposal","language":"ru","operation":"propose_complete",'
        f'"target_id":"{PLAN}","expected_version":2,"fields":{{}}}}',
        '{"kind":"proposal","language":"ru","operation":"propose_create_plan",'
        '"fields":{"title":"secret","owner_id":"other"}}',
    ],
)
async def test_untrusted_or_invalid_outputs_get_only_one_repair_and_no_success(
    content: str,
) -> None:
    transport = FakeTransport(
        _provider_response(content, "gen-invalid"),
        _provider_response(content, "gen-invalid-again"),
    )
    assistant, usage, allowance = _assistant(transport)

    outcome = await assistant.interpret(_context(), at=NOW)

    assert isinstance(outcome, ModelFailure)
    assert outcome.category is FailureCategory.INVALID_OUTPUT
    assert len(transport.payloads) == 2
    assert len(usage.started) == len(usage.reported) == 2
    assert [entry[2] for entry in usage.reported] == ["invalid_output", "invalid_output"]
    assert allowance.settled == []
    assert allowance.refunded == [(OWNER, "turn-1")]


@pytest.mark.asyncio
async def test_one_malformed_output_can_be_repaired_once_within_call_budget() -> None:
    transport = FakeTransport(
        _provider_response('{"kind":"bad"}', "gen-invalid"),
        _provider_response(
            '{"kind":"reply","language":"ru","text":"Уточните срок?"}', "gen-repaired"
        ),
    )
    assistant, usage, allowance = _assistant(transport, max_calls=MAX_CALLS)

    outcome = await assistant.interpret(_context(), at=NOW)

    assert isinstance(outcome, ModelSuccess)
    assert outcome.provider_request_ids == ("gen-invalid", "gen-repaired")
    assert len(usage.started) == 2 <= MAX_CALLS
    assert allowance.settled == [(OWNER, "turn-1")]
    assert allowance.refunded == []


@pytest.mark.asyncio
async def test_timeout_keeps_cost_unknown_refunds_turn_and_never_retries() -> None:
    transport = FakeTransport(TimeoutError("timed out after send"))
    assistant, usage, allowance = _assistant(transport)

    outcome = await assistant.interpret(_context(), at=NOW)

    assert isinstance(outcome, ModelFailure)
    assert outcome.category is FailureCategory.TIMEOUT
    assert len(transport.payloads) == len(usage.started) == 1
    assert usage.unknown[0][1] == "timeout"
    assert usage.reported == []
    assert allowance.settled == []
    assert allowance.refunded == [(OWNER, "turn-1")]


@pytest.mark.asyncio
async def test_malformed_choices_still_records_complete_provider_financial_facts() -> None:
    response = _provider_response("unused", "gen-malformed")
    response["choices"] = [{"message": {"content": None}}]
    transport = FakeTransport(response)
    assistant, usage, allowance = _assistant(transport)

    outcome = await assistant.interpret(_context(), at=NOW)

    assert isinstance(outcome, ModelFailure)
    assert outcome.category is FailureCategory.INVALID_OUTPUT
    assert outcome.provider_request_id == "gen-malformed"
    assert outcome.usage_recorded
    assert usage.reported[0][1].cost_usd == Decimal("0.0000073")
    assert usage.reported[0][1].total_tokens == 155
    assert usage.reported[0][2] == "invalid_response"
    assert allowance.settled == []
    assert allowance.refunded == [(OWNER, "turn-1")]
    assert usage.results == {}


@pytest.mark.asyncio
async def test_partial_provider_usage_retains_request_id_and_known_cost_as_unknown() -> None:
    response = _provider_response("unused", "gen-partial")
    response["choices"] = None
    response["usage"] = {
        "prompt_tokens": 120,
        "cost": "0.0000073",
    }
    transport = FakeTransport(response)
    assistant, usage, allowance = _assistant(transport)

    outcome = await assistant.interpret(_context(), at=NOW)

    assert isinstance(outcome, ModelFailure)
    assert outcome.category is FailureCategory.USAGE_UNKNOWN
    assert outcome.provider_request_id == "gen-partial"
    assert outcome.usage_recorded
    receipt = usage.reported[0][1]
    assert receipt.provider_request_id == "gen-partial"
    assert receipt.prompt_tokens == 120
    assert receipt.completion_tokens is None
    assert receipt.total_tokens is None
    assert receipt.cost_usd == Decimal("0.0000073")
    assert usage.reported[0][2] == "partial_usage"
    assert allowance.settled == []
    assert allowance.refunded == [(OWNER, "turn-1")]
    assert usage.results == {}


@pytest.mark.asyncio
async def test_missing_usage_cannot_be_treated_as_zero_cost_success() -> None:
    response = _provider_response('{"kind":"reply","language":"ru","text":"ok"}')
    response.pop("usage")
    transport = FakeTransport(response)
    assistant, usage, allowance = _assistant(transport)

    outcome = await assistant.interpret(_context(), at=NOW)

    assert isinstance(outcome, ModelFailure)
    assert outcome.category is FailureCategory.USAGE_UNKNOWN
    assert usage.unknown[0][1] == "usage_unknown"
    assert usage.reported == []
    assert allowance.settled == []
    assert allowance.refunded == [(OWNER, "turn-1")]


@pytest.mark.asyncio
async def test_missing_provider_cost_keeps_usage_known_but_cannot_settle_success() -> None:
    response = _provider_response('{"kind":"reply","language":"ru","text":"ok"}')
    response_usage = response["usage"]
    assert isinstance(response_usage, dict)
    response_usage["cost"] = None
    transport = FakeTransport(response)
    assistant, usage, allowance = _assistant(transport)

    outcome = await assistant.interpret(_context(), at=NOW)

    assert isinstance(outcome, ModelFailure)
    assert outcome.category is FailureCategory.USAGE_UNKNOWN
    assert usage.reported[0][1].prompt_tokens == 120
    assert usage.reported[0][1].cost_usd is None
    assert usage.reported[0][2] == "cost_unknown"
    assert allowance.settled == []
    assert allowance.refunded == [(OWNER, "turn-1")]


@pytest.mark.asyncio
async def test_replayed_completed_turn_returns_saved_result_without_provider_call() -> None:
    content = '{"kind":"reply","language":"ru","text":"Уточните срок?"}'
    transport = FakeTransport(_provider_response(content))
    recorder = FakeUsageRecorder()
    first, _, allowance = _assistant(transport, usage=recorder)

    first_outcome = await first.interpret(_context(), at=NOW)
    assert isinstance(first_outcome, ModelSuccess)
    allowance.status = ReservationStatus.REPLAY
    replay, _, _ = _assistant(transport, usage=recorder, allowance=allowance)

    outcome = await replay.interpret(_context(), at=NOW)

    assert isinstance(outcome, ModelSuccess)
    assert outcome.result == first_outcome.result
    assert outcome.provider_request_ids == first_outcome.provider_request_ids
    assert len(transport.payloads) == 1
    assert allowance.settled == [(OWNER, "turn-1"), (OWNER, "turn-1")]


@pytest.mark.asyncio
async def test_replay_without_result_receipt_is_unknown_and_never_calls_provider() -> None:
    transport = FakeTransport()
    assistant, usage, allowance = _assistant(
        transport, allowance=FakeAllowance(ReservationStatus.REPLAY)
    )

    outcome = await assistant.interpret(_context(), at=NOW)

    assert isinstance(outcome, ModelFailure)
    assert outcome.category is FailureCategory.USAGE_UNKNOWN
    assert transport.payloads == []
    assert not usage.started
    assert allowance.settled == allowance.refunded == []


def test_proposal_time_and_repeat_contract_keeps_calendar_elapsed_and_dst_fields_typed() -> None:
    from pin.contexts.conversation.application.dto import (
        ProposalFields,
    )

    payload = {
            "schedule": {
                "kind": "appointment",
                "local_start": "2026-11-01T01:30:00",
                "timezone": "America/New_York",
                "local_end": "2026-11-01T02:30:00",
            },
            "recurrence": {
                "frequency": "monthly",
                "month_day": 31,
                "interval": 1,
                "ends_on": None,
                "occurrence_count": 10,
            },
            "reminders": [
                {
                    "kind": "relative",
                    "basis": "calendar_day",
                    "amount": 1,
                    "unit": "day",
                    "provenance": "explicit",
                },
                {
                    "kind": "relative",
                    "basis": "elapsed",
                    "amount": 24,
                    "unit": "hour",
                    "provenance": "default",
                },
            ],
        }
    fields = ProposalFields.model_validate_json(json.dumps(payload), strict=True)
    assert isinstance(fields.schedule, AppointmentSchedule)
    assert fields.recurrence is not None and fields.recurrence.month_day == 31
    assert fields.reminders is not None
    assert isinstance(fields.reminders[0], RelativeReminder)
    assert fields.reminders[0].basis == "calendar_day"
    assert isinstance(fields.reminders[1], RelativeReminder)
    assert fields.reminders[1].basis == "elapsed"
    with pytest.raises(ValueError, match="calendar days and elapsed"):
        ProposalFields.model_validate_json(
            json.dumps({
                "reminders": [
                    {
                        "kind": "relative",
                        "basis": "calendar_day",
                        "amount": 24,
                        "unit": "hour",
                        "provenance": "explicit",
                    }
                ]
            }),
            strict=True,
        )
    with pytest.raises(ValueError, match="duration"):
        ProposalFields.model_validate_json(
            json.dumps({
                "schedule": {
                    "kind": "appointment",
                    "local_start": "2026-11-01T01:30:00-04:00",
                    "timezone": "America/New_York",
                    "duration_minutes": 30,
                }
            }),
            strict=True,
        )


def test_context_validation_rejects_invalid_zone_and_owner_id_in_plan_row() -> None:
    with pytest.raises(ValueError, match="IANA"):
        ConversationContext(
            **{
                **_context().model_dump(),
                "timezone": "not/a-zone",
            }
        )
    raw = _context().model_dump()
    raw["plans"] = (
        {"id": PLAN, "version": 1, "title": "x", "status": "open", "owner_id": OWNER},
    )
    with pytest.raises(ValueError, match="owner_id"):
        ConversationContext.model_validate(raw)


def test_smoke_preview_is_offline_and_conservatively_bounded() -> None:
    preview = build_preview()
    payload = preview_payload(preview)
    assert payload["mode"] == "offline-preview-only"
    assert payload["reads_environment_or_key"] is False
    assert payload["provider_requests"] == 0
    assert payload["max_total_calls"] == 8
    assert preview.maximum_usd <= MAX_SMOKE_SPEND_USD
    assert preview.max_input_tokens_per_call < 32_000
    assert [item.context.language for item in preview.scenarios] == [
        SourceLanguage.RU,
        SourceLanguage.EN,
    ]


def test_model_smoke_config_alias_is_local_opt_in_and_prefixed_key_wins(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env.local").write_text(
        "PIN_DATABASE_URL=postgresql://local/test\n"
        "OPENROUTER_API_KEY=local-alias-key-value-1234567890\n",
        encoding="utf-8",
    )
    settings = load_model_smoke_settings(project_root=tmp_path, environ={})
    assert settings.database_url.get_secret_value() == "postgresql://local/test"
    assert settings.openrouter_api_key.get_secret_value() == "local-alias-key-value-1234567890"

    canonical = load_model_smoke_settings(
        project_root=tmp_path,
        environ={
            "PIN_DATABASE_URL": "postgresql://env/test",
            "PIN_OPENROUTER_API_KEY": "prefixed-key-value-1234567890",
            "OPENROUTER_API_KEY": "alias-key-value-123456789012",
        },
    )
    assert canonical.database_url.get_secret_value() == "postgresql://env/test"
    assert canonical.openrouter_api_key.get_secret_value() == "prefixed-key-value-1234567890"


def test_smoke_live_preflight_requires_exact_route_and_current_prices() -> None:
    catalog = [
        {
            "id": ROUTE,
            "context_length": 1_000_000,
            "supported_parameters": ["response_format", "reasoning"],
            "pricing": {"prompt": "0.00000003", "completion": "0.00000013"},
        }
    ]
    assert validate_live_catalog(catalog, cap_usd=MAX_SMOKE_SPEND_USD).route == ROUTE
    with pytest.raises(SmokeCatalogRejected, match="absent"):
        validate_live_catalog([], cap_usd=MAX_SMOKE_SPEND_USD)
    with pytest.raises(SmokeCatalogRejected, match="exceeds"):
        validate_live_catalog(
            [
                {
                    "id": ROUTE,
                    "context_length": 1_000_000,
                    "supported_parameters": ["response_format", "reasoning"],
                    "pricing": {"prompt": "0.00001", "completion": "0.00001"},
                }
            ],
            cap_usd=MAX_SMOKE_SPEND_USD,
        )


@pytest.mark.asyncio
async def test_smoke_budget_transport_counts_actual_request_ids_and_blocks_after_eight() -> None:
    response = _provider_response('{"kind":"reply","language":"ru","text":"ok"}')
    transport = FakeTransport(*([response] * 9))
    preview = build_preview()
    gated = SmokeBudgetTransport(transport=transport, preview=preview)
    request: dict[str, object] = {
        "model": ROUTE,
        "messages": [
            {"role": "system", "content": "test"},
            {"role": "user", "content": "bounded"},
        ],
        "max_tokens": 32,
    }
    for _ in range(8):
        await gated.complete(request)
    with pytest.raises(SmokeBudgetExceeded, match="limit"):
        await gated.complete(request)
    assert gated.calls == len(transport.payloads) == 8
    assert gated.actual_usd == Decimal("0.0000073")


@pytest.mark.asyncio
async def test_live_catalog_preflight_fetches_public_catalog_without_api_key() -> None:
    async def responder(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/models"
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": ROUTE,
                        "context_length": 1_000_000,
                        "supported_parameters": ["response_format", "reasoning"],
                        "pricing": {
                            "prompt": "0.00000003",
                            "completion": "0.00000013",
                        },
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    try:
        preview = await fetch_current_catalog_preview(client=client)
    finally:
        await client.aclose()
    assert preview.route == ROUTE
    assert preview.maximum_usd <= MAX_SMOKE_SPEND_USD


@pytest.mark.asyncio
async def test_bounded_smoke_runner_requires_ru_task_and_en_appointment() -> None:
    russian = json.dumps(
        {
            "kind": "proposal",
            "language": "ru",
            "operation": "propose_create_plan",
            "fields": {
                "title": "Прочитать главу",
                "schedule": {
                    "kind": "task",
                    "local_date": "2026-10-14",
                    "timezone": "Asia/Qyzylorda",
                    "timing": "untimed",
                },
            },
        }
    )
    english = json.dumps(
        {
            "kind": "proposal",
            "language": "en",
            "operation": "propose_create_plan",
            "fields": {
                "title": "Project review",
                "schedule": {
                    "kind": "appointment",
                    "local_start": "2026-10-14T10:00:00",
                    "timezone": "Asia/Qyzylorda",
                    "duration_minutes": 30,
                },
            },
        }
    )
    backing = FakeTransport(
        _provider_response(russian, "gen-smoke-ru"),
        _provider_response(english, "gen-smoke-en"),
    )
    preview = build_preview()
    gate = SmokeBudgetTransport(transport=backing, preview=preview)
    assistant, recorder, allowance = _assistant(gate)

    report = await gate.run(assistant, at=NOW, request_prefix="issue13-smoke")

    assert report.provider_calls == len(report.provider_request_ids) == 2
    assert report.provider_request_ids == ("gen-smoke-ru", "gen-smoke-en")
    assert report.actual_cost_usd == Decimal("0.0000146")
    assert allowance.settled == [
        (preview.scenarios[0].context.owner_id, "issue13-smoke-1"),
        (preview.scenarios[1].context.owner_id, "issue13-smoke-2"),
    ]
    assert len(recorder.reported) == 2
