"""Canonical entry point for local async persistence consumers."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Coroutine
from typing import Any


def run_local_async[T](main: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Run an async entry point with Psycopg-compatible Windows I/O.

    Call this only from a synchronous process entry point, before creating a loop.
    The coroutine factory runs after the event-loop policy is configured.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(main())
