"""One-shot local operator snapshot; all output is payload-free JSON."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import sys
import time
from ctypes import wintypes
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from pin.bootstrap.config import SettingsError, load_database_url
from pin.contexts.billing.api import read_operational_projection as read_billing
from pin.contexts.conversation.api import read_operational_projection as read_provider
from pin.contexts.notifications.api import read_operational_projection as read_delivery
from pin.contexts.retention.api import read_operational_progress
from pin.contexts.speech.api import read_operational_projection as read_speech
from pin.persistence import create_engine, create_session_factory, run_local_async
from pin.platform.operations import read_operational_snapshot


def _filetime_value(value: wintypes.FILETIME) -> int:
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


def _cpu_busy_percent(sample_seconds: float = 0.2) -> float | None:
    if sys.platform != "win32":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    first = [wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()]
    second = [wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()]
    if not kernel.GetSystemTimes(*(ctypes.byref(item) for item in first)):
        return None
    time.sleep(sample_seconds)
    if not kernel.GetSystemTimes(*(ctypes.byref(item) for item in second)):
        return None
    deltas = [
        _filetime_value(after) - _filetime_value(before)
        for before, after in zip(first, second, strict=True)
    ]
    total = deltas[1] + deltas[2]
    if total <= 0:
        return None
    return round(min(100.0, max(0.0, 100.0 * (total - deltas[0]) / total)), 1)


def read_host_resources() -> dict[str, Any]:
    root = Path.cwd().anchor or Path.cwd()
    disk = shutil.disk_usage(root)
    cpu_count = os.cpu_count()
    cpu_busy = _cpu_busy_percent()
    result: dict[str, Any] = {
        "cpu_count": cpu_count,
        "cpu_busy_percent": cpu_busy,
        "cpu_available_cores": (
            round(cpu_count * (100 - cpu_busy) / 100, 1)
            if cpu_count is not None and cpu_busy is not None
            else None
        ),
        "memory_total_bytes": None,
        "memory_available_bytes": None,
        "disk_total_bytes": disk.total,
        "disk_free_bytes": disk.free,
    }
    if sys.platform == "win32":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        memory = MemoryStatus()
        memory.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx(
            ctypes.byref(memory)
        ):
            result["memory_total_bytes"] = memory.ullTotalPhys
            result["memory_available_bytes"] = memory.ullAvailPhys
    return result


async def _snapshot(database_url: str) -> tuple[dict[str, Any], bool]:
    engine = create_engine(database_url)
    sessions = create_session_factory(engine)
    try:
        async with sessions() as session:
            await session.execute(text("SELECT 1"))
            snapshot = await read_operational_snapshot(session)
            now = datetime.now(UTC)
            projections: dict[str, Any] = {
                "delivery": await read_delivery(session),
                "provider": await read_provider(session),
                "speech": await read_speech(session),
                "billing": await read_billing(session, at=now),
                "retention": await read_operational_progress(session),
            }
        value = snapshot.as_dict()
        value["contexts"] = {name: asdict(projection) for name, projection in projections.items()}
        value["restore_and_deletion_progress"] = projections["retention"].as_dict()
        value["host"] = read_host_resources()
        return value, True
    except Exception as error:
        return {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "database_ready": False,
            "database_error": type(error).__name__,
            "host": read_host_resources(),
        }, False
    finally:
        await engine.dispose()


def main() -> None:
    argparse.ArgumentParser(
        prog="pin-operations", description="Print one redacted local readiness and worker snapshot."
    ).parse_args()
    try:
        database_url = load_database_url().get_secret_value()
    except SettingsError as error:
        print(json.dumps({"database_ready": False, "database_error": str(error)}))
        raise SystemExit(2) from None
    value, ready = run_local_async(lambda: _snapshot(database_url))
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    if not ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
