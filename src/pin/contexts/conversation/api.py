"""Published conversation interpretation contract for other contexts."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from pin.contexts.conversation.infrastructure.operations import ProviderOperations

from pin.contexts.conversation.application.contracts import PlanningAssistant
from pin.contexts.conversation.application.dto import (
    ActionProposal,
    Advice,
    ConversationContext,
    ModelFailure,
    ModelOutcome,
    ModelSuccess,
    ProposalBatch,
    Reply,
    SuggestedStep,
)
from pin.contexts.conversation.capture_api import (
    CAPTURE_TTL,
    CaptureBegin,
    CaptureClarification,
    CaptureClarificationChoice,
    CaptureInterpretation,
    CaptureProofPart,
    CaptureTurnReply,
    SqlAlchemyCaptureStore,
    apply_clarification_choice,
    cancel_draft,
    confirm_ready_draft,
    finish_interpretation,
    prepare_interpretation,
    restore_ready_preview,
)

__all__ = [
    "ActionProposal",
    "Advice",
    "CAPTURE_TTL",
    "CaptureBegin",
    "CaptureClarification",
    "CaptureClarificationChoice",
    "CaptureInterpretation",
    "CaptureProofPart",
    "CaptureTurnReply",
    "ConversationContext",
    "ModelFailure",
    "ModelOutcome",
    "ModelSuccess",
    "PlanningAssistant",
    "ProposalBatch",
    "Reply",
    "SuggestedStep",
    "SqlAlchemyCaptureStore",
    "apply_clarification_choice",
    "cancel_draft",
    "confirm_ready_draft",
    "finish_interpretation",
    "prepare_interpretation",
    "restore_ready_preview",
]


async def read_operational_projection(
    session: "AsyncSession",
) -> "ProviderOperations":
    """Return conversation-owned provider usage facts without prompt or turn data."""
    from pin.contexts.conversation.infrastructure.operations import read_provider_operations

    return await read_provider_operations(session)
