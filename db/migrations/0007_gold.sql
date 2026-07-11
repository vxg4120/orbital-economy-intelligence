-- Gold evaluation set: the ground-truth program that measures identity-resolution quality.
--
-- gold_case is a hand-arbitrated ground-truth corpus. build_gold_queue.py selects hard cases
-- (stratified, deterministic) and writes the *system's* current answer + full evidence; the repo
-- owner reviews each with review.py and records a verdict. score_gold.py turns verdicts into
-- per-stratum accuracy. Verdicts are the only human-authored column family and are never
-- overwritten by a re-selection run (build_gold_queue refreshes evidence, preserves verdicts).
CREATE TABLE gold_case (
    case_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_type        TEXT NOT NULL,                 -- stratum: ambiguous_cospar | rideshare_orphan | ...
    satellite_id     BIGINT NULL REFERENCES satellite,  -- NULL for multi-satellite cases (ambiguous_cospar)
    subject_ref      TEXT NOT NULL,                 -- stable human key (norad:/jcat:/cospar:...) for resume
    question         TEXT NOT NULL,                 -- what the arbitrator must decide
    system_answer    TEXT NOT NULL,                 -- what the identity graph currently resolves
    evidence         JSONB NOT NULL,                -- identifiers, assertions, dates, regime, resolved owner/status
    verdict          TEXT NULL CHECK (verdict IN ('correct', 'incorrect', 'partial', 'unresolvable')),
    corrected_answer TEXT NULL,                     -- the arbitrator's answer when system_answer is wrong/partial
    verdict_notes    TEXT NULL,
    labeled_at       TIMESTAMPTZ NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (case_type, subject_ref)
);
CREATE INDEX ON gold_case (case_type);
CREATE INDEX ON gold_case (verdict);
