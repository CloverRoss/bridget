# Bridget Roadmap

Current planned work. Completed items are removed in the PR that closes them, so this file always reflects what's still ahead. Deferred items live under "Backlog" at the bottom — they're parked behind a trigger, not in flight.

No active work in flight. Everything below is backlog awaiting its trigger.

## Backlog

These items are deferred behind explicit triggers — they re-enter the roadmap when the trigger fires, not on a fixed date.

### CI check that PR template was respected
GH Actions workflow that diffs commit-modified roadmap/bug-list files against README mentions and warns if out of sync.
**Trigger:** evidence that manual discipline (the existing PR-template checkbox) is failing — i.e., a roadmap or known-bug change lands without the matching README update across enough PRs to call it a pattern. Until then, automation is solving a problem we don't have.

### Auto-wire fresh-install smoke test into `test.sh`
`tests/smoke-fresh-install.sh` currently runs only when invoked explicitly. Auto-wire into `test.sh` (or add a `test.sh --full` flag) without breaking the current no-venv-required ergonomics.
**Trigger:** evidence that contributors are missing fresh-install regressions because they don't run the smoke test by hand — i.e., a fresh-install bug ships to main. Until then, the manual step is sufficient.
