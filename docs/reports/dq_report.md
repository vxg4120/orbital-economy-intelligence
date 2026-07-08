# Data Quality and Conflict Report
Generated at: 2026-07-08 03:25:46 UTC

Every number below comes from a live query against the identity graph and fact layer -- disagreements are data, not errors (SPEC.md §8).

## Ingestion ledger: last run per source/status
_(none)_

## 1. Status disagreements: SATCAT vs GCAT

Count: **0**

_(none)_

## 2. Decay-date conflicts across sources

Count: **0**

_(none)_

## 3. Stale post-M&A owners

Satellites whose latest SATCAT owner assertion still resolves to a company that has since been acquired or merged (e.g. OneWeb -> Eutelsat, Inmarsat -> Viasat, Intelsat -> SES) -- the catalog still names the child.

Count: **0**

_(none)_

## 4. SupGP cross-tag anomalies

No data yet.

## 5. Match/merge stats

### Crosswalk rows by id_type
_(none)_

### merge_log by rule_fired
_(none)_

### Review-queue size: **0**

### Unmatched objects by source (source_assertion.satellite_id IS NULL)
_(none)_

## 6. Coverage

On-orbit payloads (PAYLOAD, latest status != DECAYED): **0**

- With resolved operator: 0/0 (0.0%)
- With non-UNKNOWN status: 0/0 (0.0%)
- With >=2 source identifiers (graph vs list): 0/0 (0.0%)
