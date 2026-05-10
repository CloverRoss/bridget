# Bridget Roadmap

Current planned work. Completed items are removed in the PR that closes them, so this file always reflects what's still ahead.

v2 is complete. v3 P1 is shipped (mayor mail-action log consumer + quiet hours gating both landed via mayor.md prompt edits, mg-3ae5 and mg-34ae). v3 P2 below is deferred.

## v3

### P2 — Hardening / polish

#### CI check that PR template was respected
GH Actions workflow that diffs commit-modified roadmap/bug-list files against README mentions and warns if out of sync. Defer until manual discipline (the existing PR-template checkbox) has been observed across enough PRs to know whether drift is a real failure mode.

#### Auto-wire fresh-install smoke test into `test.sh`
`tests/smoke-fresh-install.sh` currently runs only when invoked explicitly. Auto-wire into `test.sh` (or add a `test.sh --full` flag) without breaking the current no-venv-required ergonomics. Defer until manual discipline has been tested across a few PRs.
