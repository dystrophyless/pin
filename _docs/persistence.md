# Local PostgreSQL persistence

Pin uses one PostgreSQL database and one Alembic revision history. Each bounded context owns its schema and migration revisions; the persistence package owns the async engine/session conventions and the shared command-receipt table. A command opens one `SqlAlchemyUnitOfWork`, uses its `session` for every feature write, receipt, and event, then exits to commit. Any exception rolls the whole unit back. Keep network/model/speech calls outside that transaction.

`create_engine()` accepts `postgresql://` or `postgresql+psycopg://` and selects the locked Psycopg 3 driver at `READ COMMITTED`. `create_session_factory()` creates independent sessions. A synchronous local process entry point that uses async persistence must call `run_local_async(main_coroutine_factory)` from `pin.persistence`; it selects Windows' selector event loop before creating the loop (required by async Psycopg) and then runs the coroutine. Call it before any event loop exists. A feature table that uses optimistic concurrency owns a non-null integer `version` starting at 1 and a database check constraint `version >= 1`; `update_versioned()` applies the requested update only when the persisted version matches and increments it in the same SQL statement.

`pin_persistence.action_receipts` uses an internal UUID owner and a bounded action key. PostgreSQL enforces uniqueness on `(owner_id, action_key)`. Every command acquires `lock_action()` as its first database operation in the unit of work, then checks for a receipt. A replay returns the stored result immediately. If no receipt exists, the command performs its feature mutation and event writes, records the result, and commits all writes together. The PostgreSQL transaction lock serializes concurrent retries of the same owner/action pair; uniqueness remains the database backstop. Each feature owns its event schema; event and feature writes share the same session and transaction.

Apply the reviewed migration history from a configured local environment with `PIN_DATABASE_URL` set to a PostgreSQL URL:

```powershell
uv run alembic upgrade head
uv run alembic current
```

The migration environment reads the URL through validated bootstrap settings and uses the sync Psycopg 3 dialect. Migrations target the configured database, so use an isolated disposable database for rollback/re-upgrade checks.

Real PostgreSQL persistence tests are opt-in and never substitute SQLite. Set `PIN_TEST_DATABASE_URL` to a disposable database for transaction, receipt, and concurrency tests, and `PIN_TEST_MIGRATION_DATABASE_URL` to a separate fresh disposable database for migration lifecycle tests:

```powershell
$env:PIN_TEST_DATABASE_URL = 'postgresql+psycopg://...'
$env:PIN_TEST_MIGRATION_DATABASE_URL = 'postgresql+psycopg://...'
uv run pytest tests/test_persistence.py tests/test_persistence_migrations.py
```
