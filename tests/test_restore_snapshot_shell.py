from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest


def _bash_path(bash: str, path: Path) -> str:
    if os.name != "nt":
        return str(path)
    result = subprocess.run(
        [bash, "-c", 'cygpath -u "$1"', "test", str(path)],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
    )
    return result.stdout.strip()


def test_restore_snapshot_shell_extracts_private_pg_environment(tmp_path: Path) -> None:
    bash = None
    if os.name == "nt":
        git_bash = (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Git"
            / "bin"
            / "bash.exe"
        )
        if git_bash.is_file():
            bash = str(git_bash)
    bash = bash or shutil.which("bash")
    if bash is None:
        pytest.skip("Bash is required for the restore script smoke test")

    snapshot = tmp_path / "pin-20261016T120000Z"
    ledger = tmp_path / "pin-20261017T120000Z"
    snapshot.mkdir()
    ledger.mkdir()
    (snapshot / "database.dump").write_bytes(b"synthetic PostgreSQL custom dump")
    (ledger / "deletion-ledger.tar.gz").touch()
    with tarfile.open(ledger / "deletion-ledger.tar.gz", "w:gz") as archive:
        ledger_file = tmp_path / "empty-ledger.jsonl"
        ledger_file.touch()
        archive.add(ledger_file, arcname="deletion-ledger.jsonl")
    for directory, names in (
        (snapshot, ["database.dump"]),
        (ledger, ["deletion-ledger.tar.gz"]),
    ):
        checksums = "".join(
            f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n"
            for name in names
        )
        (directory / "SHA256SUMS").write_text(checksums, encoding="utf-8", newline="\n")
        (directory / ".verified").write_text("verified synthetic\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace = tmp_path / "commands.log"
    captured = tmp_path / "postgres-env.txt"
    stubs = {
        "pg_restore": '''#!/bin/sh
if [ "${1:-}" = --list ]; then exit 0; fi
printf 'pg_restore %s\\n' "$*" >> "$TRACE_FILE"
''',
        "psql": '''#!/bin/sh
printf '%s|%s|%s|%s|%s\\n' \\
  "$PGHOST" "$PGPORT" "$PGUSER" "$PGPASSWORD" "$PGDATABASE" > "$PG_CAPTURE_FILE"
case "$*" in
  *current_database*) printf '%s\\n' "$PGDATABASE" ;;
  *) printf '0\\n' ;;
esac
''',
        "alembic": '''#!/bin/sh
printf 'alembic %s\\n' "$*" >> "$TRACE_FILE"
''',
        "pin-recovery": '''#!/bin/sh
printf 'pin-recovery %s\\n' "$*" >> "$TRACE_FILE"
''',
        "pin-retention": '''#!/bin/sh
printf 'pin-retention %s\\n' "$*" >> "$TRACE_FILE"
''',
    }
    for name, content in stubs.items():
        path = fake_bin / name
        path.write_text(content, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    python_stub = fake_bin / "python3"
    python_stub.write_text(
        f"#!/bin/sh\nexec {shlex.quote(_bash_path(bash, Path(sys.executable)))} \"$@\"\n",
        encoding="utf-8",
        newline="\n",
    )
    python_stub.chmod(0o755)

    encoded_password = "p%40ss%20word%24"
    decoded_password = "p@ss word$"
    database_url = (
        "postgresql+psycopg://recover%20user:"
        f"{encoded_password}@db.example:5544/pin_recovery_smoke?sslmode=require"
    )
    env = {
        **os.environ,
        "PIN_DATABASE_URL": database_url,
        "TRACE_FILE": _bash_path(bash, trace),
        "PG_CAPTURE_FILE": _bash_path(bash, captured),
    }
    completed = subprocess.run(
        [
            bash,
            "-c",
            'export PATH="$1:$PATH"; shift; exec "$@"',
            "restore-smoke",
            _bash_path(bash, fake_bin),
            _bash_path(bash, Path("deploy/restore_snapshot.sh").resolve()),
            _bash_path(bash, snapshot),
            _bash_path(bash, ledger),
        ],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert "restore complete" in completed.stdout
    assert encoded_password not in completed.stdout + completed.stderr
    assert decoded_password not in completed.stdout + completed.stderr
    assert captured.read_text(encoding="utf-8").strip() == (
        "db.example|5544|recover user|p@ss word$|pin_recovery_smoke"
    )
    commands = trace.read_text(encoding="utf-8")
    assert "pg_restore --no-owner" in commands
    assert "alembic upgrade head" in commands
    assert "pin-recovery arm" in commands
    assert "pin-retention replay" in commands
    assert "pin-recovery status" in commands
