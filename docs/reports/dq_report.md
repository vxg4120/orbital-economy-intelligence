# Data Quality and Conflict Report
Generated at: 2026-07-08 06:15:09 UTC

Every number below comes from a live query against the identity graph and fact layer -- disagreements are data, not errors (SPEC.md §8).

## Ingestion ledger: last run per source/status
| source | endpoint | status | finished_at | rows_ingested | bytes_downloaded |
| --- | --- | --- | --- | --- | --- |
| celestrak | gp_active | error | 2026-07-08T04:24:48.910018+00:00 | 0 | 0 |
| celestrak | gp_active | ok | 2026-07-08T05:56:08.643270+00:00 | 15932 | 6686567 |
| celestrak | satcat_bulk | ok | 2026-07-08T04:24:19.753052+00:00 | 69705 | 6649851 |
| celestrak | supgp_index | ok | 2026-07-08T04:24:50.885935+00:00 | 0 | 41107 |
| gcat | gcat_orgs | ok | 2026-07-08T05:38:36.546620+00:00 | 4090 | 702914 |
| gcat | gcat_psatcat | ok | 2026-07-08T04:24:46.620670+00:00 | 27879 | 4988497 |
| gcat | gcat_satcat | ok | 2026-07-08T04:24:39.077548+00:00 | 69935 | 18981494 |

## 1. Status disagreements: SATCAT vs GCAT

Count: **35**

| norad_id | canonical_name | satcat_status | gcat_status |
| --- | --- | --- | --- |
| 23728 | Enhanced CRYSTAL 2105 | INACTIVE | DECAYED |
| 24946 | Iridium SV033 | INACTIVE | DECAYED |
| 25017 | ONYX 3 | ACTIVE | DECAYED |
| 25148 | QUASAR 11? | INACTIVE | DECAYED |
| 25417 | Orbcomm FM16 | INACTIVE | DECAYED |
| 25730 | FY-1 03 xing | INACTIVE | DECAYED |
| 25984 | Orbcomm FM36 | INACTIVE | DECAYED |
| 26473 | ONYX 4 | ACTIVE | DECAYED |
| 26934 | Enhanced CRYSTAL 2107? | INACTIVE | DECAYED |
| 28646 | ONYX 5 | ACTIVE | DECAYED |

## 2. Decay-date conflicts across sources

Count: **4240**

| norad_id | canonical_name | sources_and_dates |
| --- | --- | --- |
| 2 | PS-1 | gcat: 1958 Jan  4?; satcat: 1958-01-03 |
| 13 | CORONA Test Vehicle 2 | gcat: 1959 Mar  5; satcat: 1959-03-03 |
| 15 | Able III? | gcat: 1961 Jul  1; satcat: 1961-06-30 |
| 21 | E-2A | gcat: 1959 Oct  4 1504; satcat: 1960-04-20 |
| 28 | Altair X-248 | gcat: 1991 Jul  2; satcat: 1991-07-03 |
| 38 | Vostok-1P part | gcat: 1960 Oct  1; satcat: 1960-08-20 |
| 41 | Vostok-1P part | gcat: 1960 Oct  1; satcat: 1960-09-30 |
| 64 | Altair X-248-A5 S/N 106 | gcat: 1981 Sep 23; satcat: 1981-09-24 |
| 86 | S-56A Canister part? | gcat: 1962?; satcat: 1961-06-30 |
| 88 | Star 12 | gcat: 1961 Mar 30; satcat: 1961-04-02 |

## 3. Stale post-M&A owners

Satellites whose latest SATCAT owner assertion still resolves to a company that has since been acquired or merged (e.g. OneWeb -> Eutelsat, Inmarsat -> Viasat, Intelsat -> SES) -- the catalog still names the child.

Count: **159**

| norad_id | canonical_name | satcat_owner_code | resolved_to_child | should_be_parent | relationship | relationship_since |
| --- | --- | --- | --- | --- | --- | --- |
| 1317 | INTELSAT I | ITSO | Intelsat | SES | acquired_by | 2025-07-17 |
| 2514 | INTELSAT II F-1 | ITSO | Intelsat | SES | acquired_by | 2025-07-17 |
| 2639 | INTELSAT II F-2 | ITSO | Intelsat | SES | acquired_by | 2025-07-17 |
| 2717 | INTELSAT II F-3 | ITSO | Intelsat | SES | acquired_by | 2025-07-17 |
| 2969 | INTELSAT II F-4 | ITSO | Intelsat | SES | acquired_by | 2025-07-17 |
| 3623 | INTELSAT III F-2 | ITSO | Intelsat | SES | acquired_by | 2025-07-17 |
| 3674 | INTELSAT III F-3 | ITSO | Intelsat | SES | acquired_by | 2025-07-17 |
| 3947 | INTELSAT III F-4 | ITSO | Intelsat | SES | acquired_by | 2025-07-17 |
| 4051 | INTELSAT III F-5 | ITSO | Intelsat | SES | acquired_by | 2025-07-17 |
| 4297 | INTELSAT III F-6 | ITSO | Intelsat | SES | acquired_by | 2025-07-17 |

## 4. SupGP cross-tag anomalies

No data yet.

## 5. Match/merge stats

### Crosswalk rows by id_type
| id_type | crosswalk_rows |
| --- | --- |
| cospar | 139583 |
| gcat_id | 69878 |
| name_gcat | 69215 |
| name_satcat | 69705 |
| norad | 69705 |

### merge_log by rule_fired
| rule_fired | merges |
| --- | --- |
| cospar_exact | 1326 |
| norad_exact | 416760 |

### Review-queue size: **0**

### Unmatched objects by source (source_assertion.satellite_id IS NULL)
_(none)_

## 6. Coverage

On-orbit payloads (PAYLOAD, latest status != DECAYED): **5332**

- With resolved operator: 5332/5332 (100.0%)
- With non-UNKNOWN status: 4095/5332 (76.8%)
- With >=2 source identifiers (graph vs list): 5332/5332 (100.0%)
