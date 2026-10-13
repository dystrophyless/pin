"""Role-specific startup composition without business adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pin.bootstrap.clock import Clock, SystemClock
from pin.bootstrap.config import ProcessRole, Settings, load_settings


@dataclass(frozen=True, slots=True)
class Runtime:
    role: ProcessRole
    settings: Settings
    clock: Clock


def build_runtime(
    role: ProcessRole,
    clock: Clock | None = None,
    environ: Mapping[str, str] | None = None,
) -> Runtime:
    return Runtime(
        role=role,
        settings=load_settings(role, environ),
        clock=clock or SystemClock(),
    )

