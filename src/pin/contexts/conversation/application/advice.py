"""Localized presentation for structured, non-mutating planning advice."""

from pin.contexts.conversation.application.dto import Advice


def render_advice(advice: Advice) -> str:
    """Render ordered advice as durable conversation text for later explicit selection."""
    return advice.to_reply().text
