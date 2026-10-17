from __future__ import annotations

import hashlib
import json
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
    (snapshot / "deletion-ledger.tar.gz").write_bytes(
        (ledger / "deletion-ledger.tar.gz").read_bytes()
    )
    (ledger / "database.dump").write_bytes(b"synthetic PostgreSQL custom dump")
    for directory in (snapshot, ledger):
        (directory / "backup-metadata.json").write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "backup_id": directory.name,
                    "started_at": (
                        f"{directory.name[4:8]}-{directory.name[8:10]}-"
                        f"{directory.name[10:12]}T{directory.name[13:15]}:"
                        f"{directory.name[15:17]}:{directory.name[17:19]}Z"
                    ),
                },
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
    for directory, names in (
        (snapshot, ["backup-metadata.json", "database.dump", "deletion-ledger.tar.gz"]),
        (ledger, ["backup-metadata.json", "database.dump", "deletion-ledger.tar.gz"]),
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
    nonmatching_snapshot_metadata: bool = False,
    metadata_change: str | None = None,
    tamper_snapshot_metadata: bool = False,
    omit_snapshot_payload_checksum: bool = False,
    remove_snapshot_metadata: bool = False,
    bundles: tuple[Path, Path] | None = None,
) -> tuple[CompletedProcess[str], str]:
    bash = _bash()
    snapshot, ledger = bundles if bundles is not None else _verified_bundles(tmp_path)
    if nonmatching_snapshot_metadata:
        metadata_path = snapshot / "backup-metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["backup_id"] = "pin-20261016T120001Z"
        metadata["started_at"] = "2026-10-16T12:00:01Z"
    elif metadata_change:
        metadata_path = snapshot / "backup-metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata_change == "version":
            metadata["format_version"] = 2
        elif metadata_change == "time":
            metadata["started_at"] = "2026-10-16T12:00:01Z"
        elif metadata_change == "id":
            metadata["backup_id"] = "pin-20260230T120000Z"
        else:
            raise AssertionError(f"unknown metadata change: {metadata_change}")
    else:
        metadata_path = None
        metadata = None
    if metadata_path is not None and metadata is not None:
        metadata_path.write_text(json.dumps(metadata) + "\n", encoding="utf-8")
    if not tamper_snapshot_metadata and (
        metadata_path is not None or omit_snapshot_payload_checksum
    ):
        names = (
            ("backup-metadata.json",)
            if omit_snapshot_payload_checksum
            else ("backup-metadata.json", "database.dump", "deletion-ledger.tar.gz")
        )
        (snapshot / "SHA256SUMS").write_text(
            "".join(
                f"{hashlib.sha256((snapshot / name).read_bytes()).hexdigest()}  {name}\n"
                for name in names
            ),
            encoding="utf-8",
            newline="\n",
        )
    if tamper_snapshot_metadata:
        metadata_path = snapshot / "backup-metadata.json"
        metadata_path.write_text(metadata_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    if remove_snapshot_metadata:
        (snapshot / "backup-metadata.json").unlink()
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
    (snapshot / "deletion-ledger.tar.gz").write_bytes(
        (ledger / "deletion-ledger.tar.gz").read_bytes()
    )
    (ledger / "database.dump").write_bytes(b"synthetic PostgreSQL custom dump")
    for directory in (snapshot, ledger):
        (directory / "backup-metadata.json").write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "backup_id": directory.name,
                    "started_at": (
                        f"{directory.name[4:8]}-{directory.name[8:10]}-"
                        f"{directory.name[10:12]}T{directory.name[13:15]}:"
                        f"{directory.name[15:17]}:{directory.name[17:19]}Z"
                    ),
                },
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
    for directory, names in (
        (snapshot, ["backup-metadata.json", "database.dump", "deletion-ledger.tar.gz"]),
        (ledger, ["backup-metadata.json", "database.dump", "deletion-ledger.tar.gz"]),
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


def test_restore_rejects_metadata_id_that_does_not_match_directory(tmp_path: Path) -> None:
    completed, commands = _hosted_run(tmp_path, nonmatching_snapshot_metadata=True)

    assert completed.returncode != 0
    assert "backup directory name does not match" in completed.stderr
    assert commands == ""


@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"metadata_change": "version"}, "backup metadata format is unsupported"),
        ({"metadata_change": "time"}, "backup metadata start time does not match"),
        ({"metadata_change": "id"}, "backup metadata ID is invalid"),
        (
            {"omit_snapshot_payload_checksum": True},
            "checksum manifest must cover metadata and both payloads exactly",
        ),
    ],
)
def test_restore_rejects_invalid_or_incomplete_metadata_provenance(
    tmp_path: Path, options: dict[str, object], error: str
) -> None:
    completed, commands = _hosted_run(tmp_path, **options)

    assert completed.returncode != 0
    assert error in completed.stderr
    assert commands == ""


def test_restore_rejects_snapshot_missing_metadata_even_with_timestamp_directory(
    tmp_path: Path,
) -> None:
    # Exercise the strict new-bundle path; timestamp directory names do not establish provenance.
    completed, commands = _hosted_run(tmp_path, remove_snapshot_metadata=True)

    assert completed.returncode != 0
    assert "legacy bundles require independently verified provenance" in completed.stderr
    assert commands == ""


def test_restore_rejects_metadata_digest_tampering_before_host_actions(tmp_path: Path) -> None:
    completed, commands = _hosted_run(tmp_path, tamper_snapshot_metadata=True)

    assert completed.returncode != 0
    assert commands == ""


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


def test_backup_shell_embeds_the_single_start_clock_in_its_checksummed_archive(
    tmp_path: Path,
) -> None:
    bash = _bash()
    backup_root = tmp_path / "backups"
    release = tmp_path / ("b" * 40)
    release.mkdir()
    (release / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    runtime_env = tmp_path / "runtime.env"
    runtime_env.write_text("synthetic-only\n", encoding="utf-8")
    ledger_source = tmp_path / "deletion-ledger.jsonl"
    ledger_source.write_text("{}\n", encoding="utf-8")
    ledger_archive = tmp_path / "ledger.tar.gz"
    with tarfile.open(ledger_archive, "w:gz") as archive:
        archive.add(ledger_source, arcname="deletion-ledger.jsonl")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace = tmp_path / "docker.log"
    date_trace = tmp_path / "date.log"
    docker = fake_bin / "docker"
    docker.write_text(
        '''#!/bin/sh
printf '%s\\n' "$*" >> "$DOCKER_TRACE"
case "$*" in
  *"test -f /var/lib/pin-retention/deletion-ledger.jsonl"*) exit 0 ;;
  *" stop -t 60 "*) exit 0 ;;
  *"pg_dump"*) printf 'synthetic PostgreSQL dump\\n'; exit 0 ;;
  *"tar -czf -"*) cat "$LEDGER_ARCHIVE"; exit 0 ;;
  *" up -d --wait "*) exit 0 ;;
  *" run --rm operations"*) exit 0 ;;
esac
exit 99
''',
        encoding="utf-8",
        newline="\n",
    )
    docker.chmod(0o755)
    date = fake_bin / "date"
    date.write_text(
        '''#!/bin/sh
printf '%s\\n' "$*" >> "$DATE_TRACE"
case "$*" in
  *"%Y%m%dT%H%M%SZ %Y-%m-%dT%H:%M:%SZ"*)
    starts=$(grep -c '%Y%m%dT%H%M%SZ %Y-%m-%dT%H:%M:%SZ' "$DATE_TRACE" || true)
    if [ "$starts" -ge 2 ]; then printf '20261017T120000Z 2026-10-17T12:00:00Z\\n';
    else printf '20261016T120000Z 2026-10-16T12:00:00Z\\n'; fi ;;
  *) printf '2026-10-17T12:00:01Z\\n' ;;
esac
''',
        encoding="utf-8",
        newline="\n",
    )
    date.chmod(0o755)
    python_stub = fake_bin / "python3"
    python_stub.write_text(
        f"#!/bin/sh\nexec {shlex.quote(_bash_path(bash, Path(sys.executable)))} \"$@\"\n",
        encoding="utf-8",
        newline="\n",
    )
    python_stub.chmod(0o755)

    env = {
        **os.environ,
        "PIN_BACKUP_DIR": _bash_path(bash, backup_root),
        "PIN_RUNTIME_ENV_FILE": _bash_path(bash, runtime_env),
        "DOCKER_TRACE": _bash_path(bash, trace),
        "DATE_TRACE": _bash_path(bash, date_trace),
        "LEDGER_ARCHIVE": _bash_path(bash, ledger_archive),
    }
    completed = subprocess.run(
        [
            bash,
            "-c",
            'export PATH="$1:$PATH"; shift; exec "$@"',
            "backup-smoke",
            _bash_path(bash, fake_bin),
            _bash_path(bash, Path("deploy/backup.sh").resolve()),
            _bash_path(bash, release),
        ],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    backup = backup_root / "pin-20261016T120000Z"
    newer_backup = backup_root / "pin-20261017T120000Z"
    assert completed.returncode == 0, completed.stderr
    assert f"backup={_bash_path(bash, backup)}" in completed.stdout
    metadata = json.loads((backup / "backup-metadata.json").read_text(encoding="utf-8"))
    assert metadata == {
        "format_version": 1,
        "backup_id": backup.name,
        "started_at": "2026-10-16T12:00:00Z",
    }
    for name in ("backup-metadata.json", "database.dump", "deletion-ledger.tar.gz"):
        digest, listed_name = next(
            (line[:64], line[66:]) for line in (backup / "SHA256SUMS").read_text().splitlines()
            if line[66:] == name
        )
        assert listed_name.lstrip(" *") == name
        assert digest == hashlib.sha256((backup / name).read_bytes()).hexdigest()
    start_clock_reads = sum(
        "%Y%m%dT%H%M%SZ %Y-%m-%dT%H:%M:%SZ" in line
        for line in date_trace.read_text().splitlines()
    )
    assert start_clock_reads == 1
    assert (backup / ".verified").is_file()
    assert "pg_dump" in trace.read_text()
    assert "tar -czf -" in trace.read_text()

    newer_completed = subprocess.run(
        [
            bash,
            "-c",
            'export PATH="$1:$PATH"; shift; exec "$@"',
            "backup-smoke",
            _bash_path(bash, fake_bin),
            _bash_path(bash, Path("deploy/backup.sh").resolve()),
            _bash_path(bash, release),
        ],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert newer_completed.returncode == 0, newer_completed.stderr
    assert f"backup={_bash_path(bash, newer_backup)}" in newer_completed.stdout
    newer_metadata = json.loads(
        (newer_backup / "backup-metadata.json").read_text(encoding="utf-8")
    )
    assert newer_metadata == {
        "format_version": 1,
        "backup_id": newer_backup.name,
        "started_at": "2026-10-17T12:00:00Z",
    }

    restore_root = tmp_path / "restore"
    restore_root.mkdir()
    restored, restore_commands = _hosted_run(
        restore_root,
        bundles=(backup, newer_backup),
    )
    assert restored.returncode == 0, restored.stderr
    assert "restore complete" in restored.stdout
    assert "pg_restore --no-owner" in restore_commands
    assert "pin-retention retention replay" in restore_commands

    archive_path = tmp_path / "workflow-archive.tar.gz"
    archived = subprocess.run(
        [
            bash,
            "-c",
            'tar -czf "$1" --exclude=./.verified -C "$2" .',
            "backup-archive-smoke",
            _bash_path(bash, archive_path),
            _bash_path(bash, backup),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert archived.returncode == 0, archived.stderr
    with tarfile.open(archive_path, "r:gz") as archive:
        archived_names = {
            name
            for member in archive.getmembers()
            if (name := member.name.removeprefix("./").rstrip("/")) not in {"", "."}
        }
    assert archived_names == {
        "backup-metadata.json",
        "database.dump",
        "deletion-ledger.tar.gz",
        "SHA256SUMS",
    }
    workflow = (Path(__file__).parents[1] / ".github/workflows/backup.yml").read_text()
    assert "--exclude=./.verified" in workflow

    (backup / ".verified").unlink()
    verified = subprocess.run(
        [
            bash,
            "-c",
            'export PATH="$1:$PATH"; shift; exec "$@"',
            "backup-verify-smoke",
            _bash_path(bash, fake_bin),
            _bash_path(bash, Path("deploy/backup.sh").resolve()),
            "--mark-verified",
            _bash_path(bash, backup),
        ],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert verified.returncode == 0, verified.stderr
    assert (backup / ".verified").is_file()

    (backup / ".verified").unlink()
    (backup / "backup-metadata.json").write_text(
        json.dumps({**metadata, "started_at": "2026-10-17T12:00:01Z"}) + "\n",
        encoding="utf-8",
    )
    rejected = subprocess.run(
        [
            bash,
            "-c",
            'export PATH="$1:$PATH"; shift; exec "$@"',
            "backup-verify-smoke",
            _bash_path(bash, fake_bin),
            _bash_path(bash, Path("deploy/backup.sh").resolve()),
            "--mark-verified",
            _bash_path(bash, backup),
        ],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert rejected.returncode != 0
    assert not (backup / ".verified").exists()
