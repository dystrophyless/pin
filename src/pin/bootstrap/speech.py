"""Speech adapter loading, kept out of non-speech process imports."""

from __future__ import annotations

from typing import Any


def load_speech_model(
    model_name: str = "small", device: str = "cpu", *, cpu_threads: int = 1
) -> Any:
    """Load Whisper on demand; only the speech worker should call this."""
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-untyped]
    except ImportError as error:
        raise RuntimeError(
            "Speech dependencies are missing; install the `speech` extra."
        ) from error
    if type(cpu_threads) is not int or cpu_threads < 1:
        raise ValueError("cpu_threads must be a positive integer")
    return WhisperModel(
        model_name, device=device, compute_type="int8", num_workers=1, cpu_threads=cpu_threads
    )


def require_speech_runtime() -> None:
    """Check the optional backend without loading model weights."""
    try:
        import faster_whisper  # noqa: F401
    except ImportError as error:
        raise RuntimeError(
            "Speech dependencies are missing; install the `speech` extra."
        ) from error

