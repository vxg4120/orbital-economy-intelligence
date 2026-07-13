-- AI research dossiers for gold cases: agents gather cited evidence and recommend a verdict;
-- the human reviewer remains the adjudicator. One dossier per case (latest research wins).
CREATE TABLE IF NOT EXISTS gold_dossier (
    case_id             BIGINT PRIMARY KEY REFERENCES gold_case,
    recommended_verdict TEXT NOT NULL CHECK (recommended_verdict IN
                          ('correct','incorrect','partial','unresolvable')),
    recommended_answer  TEXT,
    confidence          TEXT NOT NULL CHECK (confidence IN ('high','medium','low')),
    summary             TEXT NOT NULL,   -- plain-language: what is going on + why this call
    evidence            JSONB NOT NULL DEFAULT '[]',  -- [{claim, source_name, url}]
    caveats             TEXT,
    researched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent_model         TEXT
);
