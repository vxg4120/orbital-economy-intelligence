# analysis/ — Behavioral Status Oracle (P0 research scaffold)

Starting materials for inferring operational status and death dates from element-set physics.

- **`BEHAVIORAL_STATUS.md`** — the research framing doc: commercial problem, signal taxonomy
  (with real named satellites + numbers), candidate algorithms, evaluation design against the
  512 SpaceX deorbits + GCAT labels, blind spots, and 10 open questions. **Read this first.**
- **`case_studies.py`** — pulls ~12 exemplar time series from `sat_daily`/`gp_elements` and
  renders annotated PNGs. Read-only DB access.
- **`figs/`** — 12 per-exemplar panels + `00_overview_grid.png` (contact sheet of all 12).

**Run:** `.venv/bin/python analysis/case_studies.py` (needs `matplotlib`, in `requirements-dev.txt`;
DB DSN via `$OEI_DSN`, defaults to the local `oei` database). Regenerates everything in `figs/`.
