-- Initialization must never replace an existing application data set.
DO $$
DECLARE
    table_record record;
    table_has_rows boolean;
BEGIN
    IF to_regclass('platform.recovery_gate') IS NOT NULL THEN
        IF (SELECT count(*) FROM platform.recovery_gate) <> 1
           OR NOT EXISTS (
                SELECT 1
                FROM platform.recovery_gate
                WHERE singleton = true
                  AND state = 'ready'
                  AND snapshot_id IS NULL
                  AND ledger_id IS NULL
                  AND snapshot_at IS NULL
                  AND fenced_at IS NULL
                  AND released_at IS NULL
                  AND deletion_evidence_sha256 IS NULL
                  AND delivery_evidence_sha256 IS NULL
                  AND payment_evidence_sha256 IS NULL
                  AND usage_evidence_sha256 IS NULL
           ) THEN
            RAISE EXCEPTION 'refusing deletion-ledger initialization: recovery state is not pristine';
        END IF;
    END IF;

    FOR table_record IN
        SELECT schemaname, tablename
        FROM pg_tables
        WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
          AND NOT (schemaname = 'public' AND tablename = 'alembic_version')
          AND NOT (schemaname = 'platform' AND tablename = 'recovery_gate')
    LOOP
        EXECUTE format(
            'SELECT EXISTS (SELECT 1 FROM %I.%I)',
            table_record.schemaname,
            table_record.tablename
        ) INTO table_has_rows;
        IF table_has_rows THEN
            RAISE EXCEPTION 'refusing deletion-ledger initialization: application data exists';
        END IF;
    END LOOP;
END $$;
