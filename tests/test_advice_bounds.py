from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from pin.contexts.conversation.application.advice import render_advice
from pin.contexts.conversation.application.contracts import (
    CallStart,
    CallUsage,
    CompletionTransport,
    ProviderUsageRecorder,
    ReservationStatus,
    TurnAllowance,
)
from pin.contexts.conversation.application.dto import (
    Advice,
    ConversationContext,
    ModelFailure,
    Reply,
    SuggestedStep,
)
from pin.contexts.conversation.application.service import (
    ROUTE,
    BoundedPlanningAssistant,
)
from pin.contexts.conversation.domain.proposals import (
    FailureCategory,
    InputKind,
    SourceLanguage,
)
from pin.contexts.identity.application.processing_consent import PROCESSING_CONSENT_POLICY_VERSION

OWNER = UUID("e232952d-3f9a-4f3d-a65a-09073dd06686")
PROCESSING_GRANT_ID = UUID("fd8f3069-c261-4b3d-97d4-97471312ba53")
NOW = datetime(2026, 10, 13, 10, tzinfo=UTC)


def _advice(language: SourceLanguage, summary: str) -> Advice:
    return Advice(
        kind="advice",
        language=language,
        summary=summary,
        steps=tuple(
            SuggestedStep(title="T" * 160, reason="R" * 181) for _ in range(8)
        ),
    )


@pytest.mark.parametrize("language", [SourceLanguage.RU, SourceLanguage.EN])
def test_maximum_rendered_advice_is_a_valid_durable_reply(language: SourceLanguage) -> None:
    base = _advice(language, "S")
    summary_length = 4_000 - len(render_advice(base)) + 1

    advice = _advice(language, "S" * summary_length)
    reply = advice.to_reply()

    assert isinstance(reply, Reply)
    assert len(reply.text) == 4_000
    assert render_advice(advice) == reply.text
    assert [line.split(".", 1)[0] for line in reply.text.splitlines()[2:]] == [
        str(index) for index in range(1, 9)
    ]
    with pytest.raises(ValidationError):
        _advice(language, "S" * (summary_length + 1))


@pytest.mark.parametrize(
    ("summary", "title", "reason"),
    [("   ", "title", "reason"), ("summary", " \t", "reason"), ("summary", "title", "\n")],
)
def test_whitespace_only_advice_fields_are_rejected(
    summary: str, title: str, reason: str
) -> None:
    with pytest.raises(ValidationError):
        Advice(
            kind="advice",
            language=SourceLanguage.EN,
            summary=summary,
            steps=(SuggestedStep(title=title, reason=reason),),
        )


class _Transport:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def complete(self, payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        return {
            "id": f"generation-{self.calls}",
            "model": ROUTE,
            "choices": [{"message": {"content": self.content}}],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 35,
                "total_tokens": 155,
                "cost": "0.0000073",
            },
        }


class _UsageRecorder:
    def __init__(self) -> None:
        self.started: list[CallStart] = []
        self.reported: list[tuple[CallStart, CallUsage, str]] = []
        self.results: list[dict[str, object]] = []

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
        raise AssertionError("complete provider usage should be recorded")

    async def store_result(
        self,
        *,
        owner_id: UUID,
        turn_id: str,
        result: dict[str, object],
        provider_request_ids: tuple[str, ...],
        claim: object | None = None,
    ) -> None:
        self.results.append(result)

    async def get_result(
        self, *, owner_id: UUID, turn_id: str
    ) -> tuple[dict[str, object], tuple[str, ...]] | None:
        return None


class _Allowance:
    def __init__(self) -> None:
        self.refunded: list[str] = []
        self.settled: list[str] = []

    async def reserve(
        self,
        *,
        owner_id: UUID,
        request_id: str,
        at: datetime,
        processing_grant_id: UUID | None = None,
        processing_policy_version: str | None = None,
        claim: object | None = None,
    ) -> ReservationStatus:
        assert owner_id == OWNER
        assert processing_grant_id == PROCESSING_GRANT_ID
        assert processing_policy_version == PROCESSING_CONSENT_POLICY_VERSION
        return ReservationStatus.RESERVED

    async def settle(
        self, *, owner_id: UUID, request_id: str, at: datetime, claim: object | None = None
    ) -> None:
        self.settled.append(request_id)

    async def refund(
        self, *, owner_id: UUID, request_id: str, at: datetime, claim: object | None = None
    ) -> None:
        self.refunded.append(request_id)


@pytest.mark.asyncio
async def test_overlong_provider_advice_is_invalid_output_and_refunds_without_result_write(
) -> None:
    raw_advice = {
        "kind": "advice",
        "language": "en",
        "summary": "S" * 1_200,
        "steps": [{"title": "T" * 160, "reason": "R" * 400} for _ in range(8)],
    }
    transport = _Transport(json.dumps(raw_advice))
    usage = _UsageRecorder()
    allowance = _Allowance()
    assistant = BoundedPlanningAssistant(
        transport=cast(CompletionTransport, transport),
        usage=cast(ProviderUsageRecorder, usage),
        allowance=cast(TurnAllowance, allowance),
        now=lambda: NOW,
    )
    context = ConversationContext(
        owner_id=OWNER,
        processing_grant_id=PROCESSING_GRANT_ID,
        processing_policy_version=PROCESSING_CONSENT_POLICY_VERSION,
        request_id="advice-too-long",
        language=SourceLanguage.EN,
        input_kind=InputKind.TEXT,
        text="What should I do next?",
        reference_date=date(2026, 10, 13),
        reference_instant=datetime(2026, 10, 13, 7, tzinfo=UTC),
        timezone="Asia/Qyzylorda",
    )

    outcome = await assistant.interpret(context, at=NOW)

    assert isinstance(outcome, ModelFailure)
    assert outcome.category is FailureCategory.INVALID_OUTPUT
    assert transport.calls == len(usage.started) == 2
    assert [entry[2] for entry in usage.reported] == ["invalid_output"] * 2
    assert usage.results == []
    assert allowance.settled == []
    assert allowance.refunded == [context.request_id]
    assert all(entry[1].cost_usd == Decimal("0.0000073") for entry in usage.reported)
