from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from subprocess import CompletedProcess

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


def _bash() -> str:
    if os.name == "nt":
        git_bash = (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Git"
            / "bin"
            / "bash.exe"
        )
        if git_bash.is_file():
            return str(git_bash)
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash is required for the restore script smoke test")
    return bash


def _verified_bundles(root: Path) -> tuple[Path, Path]:
    snapshot = root / "pin-20261016T120000Z"
    ledger = root / "pin-20261017T120000Z"
    snapshot.mkdir()
    ledger.mkdir()
    (snapshot / "database.dump").write_bytes(b"synthetic PostgreSQL custom dump")
    ledger_file = root / "empty-ledger.jsonl"
    ledger_file.touch()
    with tarfile.open(ledger / "deletion-ledger.tar.gz", "w:gz") as archive:
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
    return snapshot, ledger


def _hosted_run(
    tmp_path: Path,
    *,
    database_name: str = "pin_recovery_smoke",
    database_host: str = "postgres",
    database_port: int = 5432,
    database_user: str = "pin",
    postgres_image: str = "postgres:18.6-bookworm",
    nonempty: bool = False,
    fail_replay: bool = False,
) -> tuple[CompletedProcess[str], str]:
    bash = _bash()
    snapshot, ledger = _verified_bundles(tmp_path)
    release_dir = tmp_path / ("a" * 40)
    deploy = release_dir / "deploy"
    deploy.mkdir(parents=True)
    script = deploy / "restore_snapshot.sh"
    script.write_bytes((Path(__file__).parents[1] / "deploy" / script.name).read_bytes())
    (release_dir / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    runtime_env = tmp_path / "runtime.env"
    runtime_env.write_text("PIN_DB_PASSWORD=synthetic-only\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace = tmp_path / "commands.log"
    docker = fake_bin / "docker"
    docker.write_text(
        '''#!/bin/sh
printf 'PIN_IMAGE=%s ARGS=%s\\n' "${PIN_IMAGE:-}" "$*" >> "$TRACE_FILE"
if [ "$1" = inspect ]; then
  printf 'INSPECTED_POSTGRES_IMAGE=%s\\n' "$TEST_POSTGRES_IMAGE" >> "$TRACE_FILE"
  printf '%s true\\n' "$TEST_POSTGRES_IMAGE"
  exit 0
fi
if [ "$1" = image ]; then printf 'sha256:synthetic\\n'; exit 0; fi
[ "$1" = compose ] || exit 98
shift
case "$*" in
  *" ps -q postgres") printf 'existing-postgres-container\\n'; exit 0 ;;
  *"pg_restore --list"*) exit 0 ;;
  *"pg_restore --no-owner"*) exit 0 ;;
  *"SELECT current_database()"*) printf '%s\\n' "$TEST_DATABASE_NAME"; exit 0 ;;
  *pg_get_userbyid*) printf 'pin\\n'; exit 0 ;;
  *pg_class*) printf '%s\\n' "$TEST_NONEMPTY"; exit 0 ;;
  *pg_namespace*) printf '0\\n'; exit 0 ;;
  *"run"*)
    [ "$PIN_DATABASE_URL" = "$TEST_EXPECTED_DATABASE_URL" ] || exit 97
    printf 'container-target-environment-ok\\n' >> "$TRACE_FILE"
    case "$*" in
      *"pin-retention retention replay"*) [ "$TEST_FAIL_REPLAY" != 1 ] || exit 7 ;;
    esac
    exit 0
    ;;
esac
exit 99
''',
        encoding="utf-8",
        newline="\n",
    )
    docker.chmod(0o755)
    python_stub = fake_bin / "python3"
    python_stub.write_text(
        f"#!/bin/sh\nexec {shlex.quote(_bash_path(bash, Path(sys.executable)))} \"$@\"\n",
        encoding="utf-8",
        newline="\n",
    )
    python_stub.chmod(0o755)

    encoded_password = "synthetic%40secret%20word"
    url = (
        f"postgresql+psycopg://{database_user}:{encoded_password}"
        f"@{database_host}:{database_port}/{database_name}"
    )
    env = {
        **os.environ,
        "PATH": os.environ.get("PATH", ""),
        "PIN_DATABASE_URL": url,
        "PIN_RUNTIME_ENV_FILE": _bash_path(bash, runtime_env),
        "TRACE_FILE": _bash_path(bash, trace),
        "TEST_DATABASE_NAME": database_name,
        "TEST_EXPECTED_DATABASE_URL": url,
        "TEST_POSTGRES_IMAGE": postgres_image,
        "TEST_NONEMPTY": "1" if nonempty else "0",
        "TEST_FAIL_REPLAY": "1" if fail_replay else "0",
    }
    completed = subprocess.run(
        [
            bash,
            "-c",
            'export PATH="$1:$PATH"; shift; exec "$@"',
            "restore-smoke",
            _bash_path(bash, fake_bin),
            _bash_path(bash, script),
            _bash_path(bash, snapshot),
            _bash_path(bash, ledger),
        ],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed, trace.read_text(encoding="utf-8") if trace.exists() else ""


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


def test_hosted_restore_uses_existing_containers_and_never_starts_runtime_roles(
    tmp_path: Path,
) -> None:
    completed, commands = _hosted_run(tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert "restore complete" in completed.stdout
    assert "synthetic%40secret%20word" not in completed.stdout + completed.stderr + commands
    assert "synthetic@secret word" not in completed.stdout + completed.stderr + commands
    assert "postgres:18.6-bookworm" in commands
    assert "pg_restore --list" in commands
    assert "pg_restore --no-owner" in commands
    assert "--pull never" in commands
    assert "-e PIN_DATABASE_URL" in commands
    assert "container-target-environment-ok" in commands
    assert "migrate" in commands
    assert "pin-recovery operations arm" in commands
    assert "pin-retention retention replay" in commands
    assert " --user " in commands
    assert "pin-recovery operations status" in commands
    assert " createdb" not in commands
    assert "DROP DATABASE" not in commands
    assert "compose up" not in commands
    assert "gateway" not in commands
    assert "worker" not in commands
    assert "docker pull" not in commands
    assert " build" not in commands


@pytest.mark.parametrize(
    ("options", "error", "expected_trace"),
    [
        (
            {"database_name": "pin"},
            "PIN_DATABASE_URL must target postgres:5432 as pin",
            "",
        ),
        (
            {"database_host": "127.0.0.1"},
            "PIN_DATABASE_URL must target postgres:5432 as pin",
            "",
        ),
        (
            {"database_port": 5433},
            "PIN_DATABASE_URL must target postgres:5432 as pin",
            "",
        ),
        (
            {"database_user": "postgres"},
            "PIN_DATABASE_URL must target postgres:5432 as pin",
            "",
        ),
        (
            {"postgres_image": "postgres:18.5-bookworm"},
            "not the pinned 18.6 image",
            "ps -q postgres",
        ),
        ({"nonempty": True}, "must be empty and owned", "pg_class"),
    ],
)
def test_hosted_restore_fails_closed_before_writing_snapshot_data(
    tmp_path: Path,
    options: dict[str, object],
    error: str,
    expected_trace: str,
) -> None:
    completed, commands = _hosted_run(tmp_path, **options)

    assert completed.returncode != 0
    assert error in completed.stderr
    assert expected_trace in commands
    assert "pg_restore --no-owner" not in commands
    assert "--entrypoint" not in commands


def test_hosted_restore_replay_failure_leaves_gate_fenced_and_does_not_start_roles(
    tmp_path: Path,
) -> None:
    completed, commands = _hosted_run(tmp_path, fail_replay=True)

    assert completed.returncode != 0
    assert commands.index("pin-recovery operations arm") < commands.index(
        "pin-retention retention replay"
    )
    assert "pin-recovery operations status" not in commands
    assert "restore complete" not in completed.stdout
    assert "compose up" not in commands
    assert "gateway" not in commands
    assert "worker" not in commands
