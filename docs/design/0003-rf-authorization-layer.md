# RF authorization layer: frequencies, filings, and reachability

Research consolidated 2026-07-29 from two independent investigations (FCC data landscape; prior
art and gap analysis). Status: feasibility established, v1 scope recommended, not yet scheduled.

## The idea being evaluated

Fuse what OEI already knows (where every satellite is, from nightly GP elements) with what the
regulatory record knows (what each satellite is authorized to transmit, on which frequencies),
so a user can ask: standing at this location, right now, which satellites are above the horizon,
transmitting, and receivable, and where do I tune? Secondary prize: FCC authorization precedes
launch by months to years, so filings are a queryable pipeline of satellites that do not exist
in the catalogs yet.

## What the research established

**The white space is real and is exactly our shape.** Every open tool in this space (N2YO,
Look4Sat, gpredict, Heavens-Above, SatDump) is downstream of just two amateur-curated sources:
SatNOGS DB and JE9PEL's list. No open project joins authorization data (FCC or ITU) to tracked
catalog objects. The one academic attempt (MIT's ITU Compliance Assessment Monitor) is GEO-only,
matches by longitude proximity, and takes NORAD ids as manual input. The closed-source proof of
value is Seradata's analyst-curated subscription product. The blocking problem everywhere is
entity resolution between filing identities (call signs, network names) and catalog identities
(NORAD, COSPAR), which is the machinery OEI already runs for GCAT versus Space-Track.

**The data is more structured than feared.** The user's expectation of dense PDFs is half right.
Band-level truth needs no PDFs at all: the legacy IBFS bulk dump
(ftp://ftp.fcc.gov/pub/Bureaus/International/databases/IBFS.zip, 48.8 MB, public domain, still
refreshed after the 2025 ICFS migration) contains the full relational database, including a
FREQUENCY table with lower/upper frequency, EIRP, emission designator, polarization and
modulation as clean fields, and a SPACE_STATION table that carries the ITU network name, which
is the future bridge to non-US filings. The Approved Space Station List (ssal.xlsx) gives every
current license and market-access grant with call sign, frequency ranges, orbital slot and
launch date in one spreadsheet. PDFs only gate beam/channel-level Schedule S detail and the
experimental (cubesat) filings, neither needed for v1.

**The join decomposes by difficulty.** Constellation blanket licenses cover roughly 75% of
active satellites with about a dozen manual mappings (one call sign covers every Starlink Gen2
bird). GSO joins on orbital slot plus fuzzy name against ssal.xlsx, moderate effort for ~600
satellites. The smallsat tail is genuinely hard from FCC data (names live inside exhibit PDFs)
and is precisely what SatNOGS DB already solved with NORAD-keyed, moderator-curated transmitter
records under CC BY-SA.

**Honest coverage caveats, to be published if built.** FCC covers US-licensed and US
market-access satellites only; the Chinese and Russian fleets have no FCC record and the ITU
bulk data (SRS/BR IFIC) is paywalled. US federal satellites (NOAA APT, GPS) are NTIA-authorized,
not FCC, so the most classically hearable birds need the SatNOGS layer, not the FCC one. An
authorization is not a guarantee of transmission, and a SatNOGS "active" flag is not an
authorization: the layered answer is the product.

**Ingestion mechanics.** fcc.gov main properties sit behind Akamai bot protection (403 to
non-browser clients); ftp.fcc.gov, data.fcc.gov, opendata.fcc.gov and the fcc.report mirror are
open. Pre-launch pipeline confirmed: pending SAT-* applications are visible in the bulk dump
with filed dates and no grant date (Starlink Gen1 grant preceded first launch by 14 months,
Kuiper by 39).

## Recommended v1 scope, in order

1. **Ingest SatNOGS DB** as a provenance-tracked assertion source keyed on norad_cat_id (open
   API, nightly pull). Keep it a separable, attributed layer: CC BY-SA share-alike means the
   blended product must credit it and derivative transmitter layers inherit the license. This
   buys the entire receivable amateur/LEO layer in days.
2. **Ingest IBFS.zip plus ssal.xlsx** into raw tables with the existing ingest ledger, then
   resolve authorizations to canonical satellites through the identity graph in three tiers:
   curated constellation mappings, GSO slot-plus-name matching, and unresolved-by-design for
   the tail. Every authorization is a source assertion with filing provenance, including
   pending applications as the pre-launch pipeline.
3. **Ship the reachability endpoint**: location in, currently-and-soon-visible satellites out,
   each with its layered RF picture (catalog identity, SatNOGS transmitters with citations, FCC
   authorization with file numbers, pending filings). Vectorized SGP4 over the existing GP
   store; inclination and footprint prefilters; milliseconds per query, cacheable.
4. **Defer** ITU SRS (paywalled), Schedule S beam parsing, and ELS exhibit PDFs until the FCC
   join proves out. Defer any claim of global coverage; publish the coverage split instead,
   in the same style as the bus methodology's honesty meters.

## Why this fits OEI specifically

It is the same play as Bus Benchmarks: public data everyone can see, fused through identity
resolution nobody else does, published with per-value provenance and honest coverage
disclosure. The pre-launch pipeline also feeds the provisional-identity work (filings name
satellites months before catalogs do) and gives Bus Benchmarks a forward-looking column.
