# Bridget Roadmap

Current planned work. Completed items are removed in the PR that closes them, so this file always reflects what's still ahead.

v2 is complete. v3 is in progress.

## v3

### P1 — Behavioral / nice-to-have

#### Mayor consumes bridget's mail-action log
Mayor's prompt should be edited to tail `~/.pogo/bridget.mail-actions.log` (written by bridge on every `read` / `dismiss` / `dismiss-all`) so its view of mail state stays in sync with the user's Discord actions across mayor outages.

Scan-window mitigation: bound the first pass to unreads + mail from the last 48h, so memory and parsing cost stay flat as the log grows. The 48h cap is the initial heuristic; tunable.

#### Quiet hours: actually gate behavior, push to agents
The bridge's `quiet` command currently writes `~/.pogo/quiet.json` but no agent reads it. Make quiet hours observably affect agent behavior, and use the same window for staged restarts:

- Bridge holds the canonical truth and pushes quiet-hours state to agents (don't rely on agents pulling).
- Bridge re-pushes on agent reconnect so the truth carries through restarts.
- Mayor suppresses non-critical human-bound mail during quiet windows; queues for after.
- Restarts triggered by recent prompt updates get scheduled inside the quiet window rather than disrupting active work.

The push mechanism design is the hardest part — versioned snapshot file, structured mail, or events.log tail are all candidates.

### P2 — Hardening / polish

#### CI check that PR template was respected
GH Actions workflow that diffs commit-modified roadmap/bug-list files against README mentions and warns if out of sync. Defer until manual discipline (the existing PR-template checkbox) has been observed across enough PRs to know whether drift is a real failure mode.

#### Auto-wire fresh-install smoke test into `test.sh`
`tests/smoke-fresh-install.sh` currently runs only when invoked explicitly. Auto-wire into `test.sh` (or add a `test.sh --full` flag) without breaking the current no-venv-required ergonomics. Defer until manual discipline has been tested across a few PRs.
