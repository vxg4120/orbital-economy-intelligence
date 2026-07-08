"""Read-only FastAPI JSON API over the identity graph + fact layer (Task F1).

Every query is a parameterized SELECT; the request-scoped connection is opened READ ONLY
(api/deps.py) so a write can never leave this process. See
docs/superpowers/specs/2026-07-08-frontend-design.md for the API contract.
"""
