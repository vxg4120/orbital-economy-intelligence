"""Which decay_date claims are claims about a decay.

SATCAT's decay date and a Space-Track decay message are what they say. GCAT's DDate is not:
it is the time a phase of the object's history ended, and the Status on the same GCAT entry
names the event that ended it. For a reentry, a deorbit or a landing (the phases that
identity/status_map.yml maps to DECAYED) the DDate is a decay date. For a docking, a grapple, a
transfer into another object or a departure to deep space it is the date of that event, and the
object's later history sits under other GCAT entries that this database does not load.

The resolver has applied this since 2026-10-07 (identity/resolve.py, _gcat_ddate_is_decay),
after it put Zarya's grapple date on the ISS card. The surfaces that compare decay dates did
not until 2026-11-13, when 812 of the 4,334 "decay-date conflicts" turned out to be a GCAT phase
date set against a real decay date. They now read their claims through this module, so there
is one statement of the rule outside the resolver, and tests/test_decay_phase_dates.py checks
that it keeps the same GCAT dates as the resolver does.

The rule is per claim: a GCAT decay_date claim counts when the current GCAT status claim on its
own source key maps to DECAYED. GCAT marks an uncertain phase with a trailing "?" ("R?" is
reentered, probably), and the phase is the same, as in the resolver's _canonical. A date whose
key carries no status, or a status the map does not know, is not counted.
"""

# The GCAT keys of each satellite whose current status claim maps to DECAYED. A CTE body: the
# readers below join their picked decay claims to it on (satellite_id, source_key).
GCAT_DECAY_KEYS_SQL = """
    SELECT DISTINCT st.satellite_id, st.source_key
    FROM v_current_claim st
    JOIN status_mapping m
      ON m.source = 'gcat' AND m.canonical_status = 'DECAYED'
     AND m.source_value IN (st.value, btrim(rtrim(st.value, '?')))
    WHERE st.source = 'gcat' AND st.attribute = 'status'
"""

# Each source's current decay claim per satellite, less the GCAT phase dates. When a satellite
# holds two keys for one source, the lesser key's claim is the one compared, as these readers
# have always had it (the resolver takes the greater key; no satellite with a decay conflict
# held two GCAT decay claims on 2026-11-13, so the two picks agree on every row counted today).
CURRENT_DECAY_CLAIMS_SQL = f"""
WITH picked AS (
    SELECT DISTINCT ON (satellite_id, source)
           satellite_id, source, source_key, value, observed_to
    FROM v_current_claim
    WHERE attribute = 'decay_date'
    ORDER BY satellite_id, source, source_key
),
gcat_decay_keys AS ({GCAT_DECAY_KEYS_SQL})
SELECT p.satellite_id, p.source, p.value, p.observed_to AS observed_at
FROM picked p
LEFT JOIN gcat_decay_keys k
  ON k.satellite_id = p.satellite_id AND k.source_key = p.source_key
WHERE p.source <> 'gcat' OR k.satellite_id IS NOT NULL
"""
