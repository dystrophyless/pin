"""Speech adapter loading, kept out of non-speech process imports."""

from __future__ import annotations

from typing import Any


def load_speech_model(model_name: str = "small", device: str = "cpu") -> Any:
    """Load Whisper on demand; only the speech worker should call this."""
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-untyped]
    except ImportError as error:
        raise RuntimeError(
            "Speech dependencies are missing; install the `speech` extra."
        ) from error
    return WhisperModel(model_name, device=device, compute_type="int8")


def require_speech_runtime() -> None:
    """Check the optional backend without loading model weights."""
    try:
        import faster_whisper  # noqa: F401
    except ImportError as error:
        raise RuntimeError(
            "Speech dependencies are missing; install the `speech` extra."
        ) from error

