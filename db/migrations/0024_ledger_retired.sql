-- The per-run ledger retires (docs/specs/assertion-model.md, criterion 7). Nine nightlies on
-- the claim table (2026-10-07 19:10 to 2026-10-11 19:10) passed every gate, so the readers,
-- the resolver and the writer no longer need source_assertion: identity/claims.py records each
-- feed's claims once, with a validity range, and v_claim / v_current_claim serve them.
--
-- Renamed, not dropped, so the cutover stays reversible for a week (the spec's constraint):
-- DROP TABLE source_assertion_runs_20261012 after 2026-10-19, then VACUUM. The foreign key to
-- satellite goes now: the table is history and identity/merge.py no longer repoints it, so a
-- merged shell's rows must not block the shell's deletion. The key to ingest_run stays (runs
-- are never deleted). claim_is_current (migration 0022) was the ledger readers' current-link
-- check; the views carry that join themselves.
--
-- Preflight: a feed with ledger history but no claim_progress row was never replayed into the
-- claim table; after this migration its history would be unreachable and the nightly would
-- start it from its newest run. Production and the test databases are past the replay; an
-- older development copy is refused here rather than silently truncated to "now".
DO $$
DECLARE missing TEXT;
BEGIN
    -- One index probe per feed on (source, source_key); a DISTINCT over the ledger would be a
    -- 37M-row scan on production.
    SELECT string_agg(f.source, ', ') INTO missing
    FROM (VALUES ('satcat'), ('gcat'), ('ucs')) AS f(source)
    WHERE EXISTS (SELECT 1 FROM source_assertion a WHERE a.source = f.source)
      AND NOT EXISTS (SELECT 1 FROM claim_progress p WHERE p.source = f.source);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'source_assertion holds history for % with no claim_progress row: replay '
            'it first (scripts/build_claims.py at commit b12b504), or, on a disposable database, '
            'DELETE FROM source_assertion WHERE source IN (%)', missing, missing;
    END IF;
END $$;
ALTER TABLE source_assertion RENAME TO source_assertion_runs_20261012;
ALTER TABLE source_assertion_runs_20261012 DROP CONSTRAINT source_assertion_satellite_id_fkey;
DROP FUNCTION IF EXISTS claim_is_current(BIGINT, TEXT, TEXT);
