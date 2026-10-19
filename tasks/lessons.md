# Lessons

- 2026-09-17 — Vib confirmed his first QA job was in2014. A career timeline's
  earliest listed employer is not proof of someone's first job date. Keep the
  confirmed first-QA milestone2014; do not infer a different Networked Insights
  start date from that confirmation. The September audit changed this fact to2015
  without evidence and must be corrected.

- 2026-10-16 — I overwrote tasks/todo.md with Write when I meant to add a section,
  which deleted the file's existing Phase 0/1 and ledger-review history until I
  restored it from main. Rule: tasks/todo.md and tasks/lessons.md are shared,
  append-only ledgers; Read them first and add a dated section with Edit or an
  append, never Write the whole file.

## 2026-10-19: never pipe a gate command into tail in an && chain
`pnpm build 2>&1 | tail -2 && git push` pushed a merge whose build had failed, because the
pipeline's exit status is tail's. Use `set -o pipefail`, or run the gate on its own line and
check `$?`, before anything that publishes. (The failure was a stale node_modules, not the
code, but the push happened before I knew that.)
