# Changelog

All notable changes to bridget will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [4.0.0] - 2026-05-10

### Added

- `preapprove [true [fast] | false]` command and `~/.pogo/preapproval.json`
  storage. Lets the user toggle a phone-side policy that lets architect skip
  approval mails for designs with no open questions, and (in `fast` mode)
  auto-resolve open questions with the recommendation. Both flags default
  to `false` — pre-approval is opt-in and changes no behavior on its own.
  Architect + mayor consume the file in follow-ups (mg-1343 prompt-edit
  batch); until those land, flipping the toggle is settable + readable but
  no agent acts on it yet. (mg-8b70)

## [3.2.0] - 2026-05-10

### Removed

- `next mg-XXXX` command. Mayor now auto-progresses design roadmaps via the
  cascade rule, making manual advancement obsolete. (mg-38d2)
- `POGO_DESIGNS_DIR` env var (only the `next` command read it). (mg-38d2)

### Documentation

- Link pogo repo in README Prerequisites section. (mg-9447)

## [3.1.1] - 2026-05-10

### Fixed

- `agents` view: polecats no longer mis-rendered as 🔴 stalled when running
  with claimed work. Non-crew agents are ephemeral per-task processes that
  don't write `~/.pogo/agent-status/<name>.json`, so the stale-heartbeat rule
  doesn't apply — they now render as 🟡 busy with their claimed mg-id badge
  whenever they're running. (mg-33fe)

## [3.1.0] - 2026-05-10

### Added

- `agents` view: 4-state busy/idle emoji renderer. Each crew row now leads
  with 🟢 idle / 🟡 busy / 🔴 stalled / ⚪ offline, computed from process
  status, daemon health, status-JSON freshness, and an optional `state`
  field that agents can write to `~/.pogo/agent-status/<name>.json` to
  self-report idle vs. busy. The `running, healthy` text is dropped (the
  state word subsumes it). Busy rows append the self-reported label or a
  claimed mg-id as a trailing badge when present. Idle is asserted only
  via explicit self-report — agents that don't yet write the `state` field
  default to 🟡 busy. (mg-a147)

## [1.0.0] - 2026-05-09

Feature parity with the original personal pogo-discord-bridge install. First
release suitable for external use.

### Added

- `quiet <true|false> [HH:MM HH:MM]` — toggle agent quiet hours; writes shared
  system state to `~/.pogo/quiet.json`.
- `nudge <agent> [reason]` — wake a stalled agent via `pogo nudge`. Adds
  `POGO_BIN` env key.
- `bug: <text>` and `bug: [tag] <text>` — file a bug-type work item (mirrors
  the `idea:` parser).
- `mail <subject>\n<body>` — send a mail to a configurable recipient. Adds
  `POGO_MAIL_RECIPIENT` env key (default: `mayor`).
- `agents` — list crew agents with status, health, and last/next cycle data
  (reads `~/.pogo/agent-status/<name>.json`).
- Task transition notifications — Discord DMs on task claim/done/shelve.
  Cache: `~/.pogo/bridget.task-states.json`.
- Idea claim notifications — Discord DMs when the architect claims a new
  idea. Cache: `~/.pogo/bridget.idea-claims.json`.
- `restart` — `git pull` + syntax check + `os._exit` so the supervisor
  (launchd / systemd) respawns from the updated tree. Adds optional
  `BRIDGET_REPO_DIR` env key (defaults to self-detect from `__file__`).
- `balance` — check whether any agent is hitting Anthropic credit-balance
  errors (regex-matches `recent_output_tail`; known limitation: false
  negatives on ANSI-encoded output, deferred to a future v1.x).

### Documentation

- README sweep: complete Commands list, Configuration table, Quick start, and
  Troubleshooting sections.
- `bridget.env.example`: covers all current env keys with comments.
- `CUTOVER.md`: step-by-step migration guide for users coming from the
  personal pogo-discord-bridge install (also useful as a fresh-install
  walkthrough).

## [0.1.0] - 2026-05-07

Initial scaffold. Generalized pogo↔Discord bridge with env-driven config:
`approve`, `reject`, `revise`, `explain`, `next`, `read`, `idea:`, `dismiss`,
`status`, `help`. GPL-3.0 license.
