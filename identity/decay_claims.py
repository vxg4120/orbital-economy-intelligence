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

A second rule, since 2026-11-20, is about any source's decay date: tracked_after() below. An
element set is a measurement of an object in orbit, so a decay date is contradicted when the
object's latest element set is dated more than TRACKED_AFTER_DAYS after it. The resolver sets
such a claim aside (identity/resolve.py) and the satellite card marks it
(api/routers/satellites.py). The SQL readers below do not apply it, and should not: a date
that is set aside still disagrees with the other source's date, and that disagreement is what
the Conflicts page reports. On 2026-11-20 it held for twelve GCAT claims: three were the
object's only decay claim, and nine sat about a year before a SATCAT date that already won.
"""

import datetime as dt

from identity.normalize import last_possible_day

# Production, 2026-11-20: of 882 objects with a resolved decay date and an element set, 9 had an
# element set dated after the decay date. The gaps were 1 day (five objects, the ordinary lag
# between a reentry and the last element set published for it), 17 days (Dragon C206.6), and
# then 196, 2,072 and 6,357 days: NORAD 56915, a Starlink that GCAT has as reentered while
# SATCAT has it operational at 482 km, and 44547 and 24946, both GCAT status C ("collided"),
# whose parent bodies are still tracked. Thirty days separates the two groups with room on
# both sides, and it is the same month the decay-message rule in resolve.py uses.
TRACKED_AFTER_DAYS = 30


def tracked_after(value, latest_epoch) -> bool:
    """Whether a decay date is contradicted by the object's latest element set: the element set
    is dated more than TRACKED_AFTER_DAYS after the date. A date that gives only a month or a
    year is measured from the end of that span, since the object may have decayed at any time
    in it. An epoch with a time is compared by its UTC day."""
    last_day = last_possible_day(value)
    if last_day is None or latest_epoch is None:
        return False
    if isinstance(latest_epoch, dt.datetime):
        if latest_epoch.tzinfo is not None:
            latest_epoch = latest_epoch.astimezone(dt.timezone.utc)
        latest_epoch = latest_epoch.date()
    return (latest_epoch - last_day).days > TRACKED_AFTER_DAYS


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
