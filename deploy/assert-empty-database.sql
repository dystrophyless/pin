-- Initialization must never replace an existing application data set.
DO $$
DECLARE
    table_record record;
    table_has_rows boolean;
BEGIN
    FOR table_record IN
        SELECT schemaname, tablename
        FROM pg_tables
        WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
          AND NOT (schemaname = 'public' AND tablename = 'alembic_version')
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
