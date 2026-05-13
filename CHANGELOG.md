# Changelog

All notable changes to bridget will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [4.41.0] - 2026-05-13

### Changed

- Simplified `categorize_in_flight` Type=project branch post-mg-e68c
  tag migration. Only canonical `in-progress` bucks into Projects;
  all other states hidden. Dropped `_CANONICAL_NOT_RUNNING` and
  `_IN_PROGRESS_TAGS` frozensets and the legacy `kickoff-done`
  fallback path. Cleaner code; fewer regressions. (mg-e68c
  follow-on / mg-b014)

## [4.40.0] - 2026-05-13

### Fixed

- `read_design` now logs the actual exception class + message on IO
  failure (closes the post-mg-5dd7 "file missing or unreadable"
  opacity; the previous catch silently returned None). On missing
  files it also performs a best-effort `brctl download` to
  materialize iCloud-Drive placeholders before giving up — the
  hypothesised root cause for `open mg-XXXX` returning "design not
  found" when the file is visible at the iCloud path but stored as
  a placeholder. The brctl fallback is a no-op on hosts without
  `brctl` (non-macOS, stripped images). (mg-7d54 / mg-b5e5)

## [4.39.0] - 2026-05-13

### Added

- bridget logs now include UTC timestamps + reason context for
  `on_ready`, the `restart` command, and Discord `HTTPException`
  catches. Closes the diagnostic gap from untraced uncommanded
  restarts (bridget.log previously held 31 "logged in as Bird Get"
  lines with no timestamps and no way to tell user-triggered
  restarts from launchd respawns after a crash). Lines go to stderr,
  which launchd captures into `bridget.log`. (mg-5d0e / mg-7f65)

## [4.38.0] - 2026-05-13

### Added

- `librarian search <query>` command. Shells out to ripgrep against
  the ingested Confluence data tree (default
  `~/DUGLocal/confluence-ingestion/data/`, configurable via
  `CONFLUENCE_DATA_DIR`). Results are grouped by file with up to 2
  matching lines per file (`rg --max-count=2`); total output is
  capped at ~1500 chars with a `…(truncated; refine query)` marker
  appended when the cap is hit. No relevance ranking — directory
  order. Trusts ingest-time redaction (REDACTION_POLICY applied at
  write time per mg-b8a4). If `rg` isn't on PATH the reply hints at
  `brew install ripgrep`. (mg-b853 / mg-fea0 P2 #8)

## [4.37.0] - 2026-05-13

### Fixed

- `_agent_has_pending_work` now excludes `type=project` items from the
  actionable-work check. Project items are Product-Roadmap records
  managed by mayor, not individual agent to-dos. Fixes director
  showing as stalled when its only assigned items are Project records.
  (mg-e528)

## [4.36.0] - 2026-05-13

### Added

- `librarian sync <space> [page-id]` command. Triggers a Confluence
  ingest via `confluence-ingestion/scripts/run-ingest.sh`; bridget
  returns immediately while the ingest runs in the background and
  mails human from the `librarian` sender on completion (success or
  failure). Full-space mode runs with `--dedupe` so unchanged pages
  are skipped. Space key must match `^[A-Z][A-Z0-9_-]*$` (Confluence
  space-key shape); page-id must be all digits. Concurrency is
  guarded by `/tmp/librarian.lock` — a second invocation while one
  is in flight is rejected with "ingest already running". If bridget
  crashes mid-sync the lock-file lingers; `rm /tmp/librarian.lock`
  to clear it. (mg-7c91 / mg-fea0 P1 #5)

## [4.35.0] - 2026-05-13

### Changed

- `bridget nudge` command now passes `--immediate` to `pogo nudge` so
  the message always lands without waiting for the agent to be idle.
  Fixes the case where nudging a busy mayor times out.
  (mg-cb95)

## [4.34.0] - 2026-05-13

### Changed

- `explain mg-XXXX <topic>` now clears the matching
  `approval needed` mail from human/new/ → human/cur/ on success,
  matching the approve/reject/revise behavior shipped in mg-ae6f.
  Reply text reports the cleared count (`(cleared N related mail...)`)
  when non-zero. Inbox = actionables only; sending an explain
  request is itself an action on the approval item.
  (mg-f7b5)

## [4.33.0] - 2026-05-13

### Fixed

- `open mg-XXXX` now renders the design body regardless of
  frontmatter `status:` — the status appears in the reply header
  (`**title** _(status)_ (mg-XXXX)`). Previously the handler bailed
  out with a "no longer awaiting approval" stub for any non-`awaiting-
  approval` design, which combined with mg-d3d7 tightening `read` to
  mail-only left no way to surface an approved design's body in
  Discord. Root cause for the user repro on mg-12ee (approved
  ~30s before the repro) was the over-narrow status gate added by
  mg-5dd7, not iCloud path resolution or multi-line YAML in the
  frontmatter. Also added an `open <id>: rendering body` stderr
  log line on every render (alongside the existing parse-error
  log) so post-fix recurrences are diagnosable from logs alone.
  (mg-0471)

## [4.32.0] - 2026-05-13

### Fixed

- Projects bucket: canonical in-progress tag now wins over canonical
  not-running tags when both present. Fixes mg-fea0 disappearance
  after mg-5f78 / mg-7555 shipped. Precedence: in-progress (canonical)
  > not-running (canonical) > kickoff-done (legacy fallback).
  (mg-a106)

## [4.31.0] - 2026-05-13

### Changed

- `get_inbox_summary` top list now hides `approval needed` and
  `Report ready:` mails for mg items whose status is
  shelved/done/archived. Matches the same filtering Pending
  approvals + Pending reports already apply at the bucket level,
  so a stale Report/approval doesn't keep showing in the unread
  count after its mg item closes. Plus a one-time admin cleanup
  of 7 stale mails on 2026-05-13. (mg-472c)

## [4.30.0] - 2026-05-13

### Fixed

- Projects bucket: canonical 5-state tags (scheduled /
  ready-for-kickoff / done / cancelled) now take precedence over
  the legacy in-progress signal (kickoff-done). Fixes the case
  where a Project has both canonical and legacy tags during the
  mg-1d2b Phase 4 transition window. (mg-5f78)

## [4.29.0] - 2026-05-13

### Changed

- `open` command now distinguishes file missing (`design not
  found`) from design exists but past awaiting-approval phase
  (returns status + iCloud path). Added parse-error log line on
  frontmatter parse failures for better diagnostics. (mg-7a57)

## [4.28.0] - 2026-05-13

### Changed

- `agents` no longer reports stalled-with-no-work as stalled;
  reclassified as idle (matches user spec: "stalled = not
  responding to work items"). When `pogo agent diagnose` returns
  health=stalled, bridget now runs a live `mg list
  --status=available --assignee=<name>` + `mg mail list <name>`
  check: if both queues are empty the agent renders 🟢 idle
  (health_raw='stalled (no work)' preserves the diagnose origin);
  if either has items the agent stays 🔴 stalled (real wedge).
  Reclassification uses live queries — true at query time, not
  dependent on agent self-reports. (mg-3538)

## [4.27.0] - 2026-05-13

### Changed

- `approve` / `reject` / `revise` verbs now route by mg item
  Assignee field instead of inferring from Type=report. Fixes
  misrouting of Director-Flow handoffs (Type=report reassigned
  to mayor) and is more resilient to future type-vs-routing
  divergence. Architect remains the fallback when `mg show`
  fails or no Assignee line is found. (mg-0e2d)

## [4.26.0] - 2026-05-13

### Added

- `spend` command (DM `spend` to bridget) reports live Anthropic
  rate-limit consumption. Probes `api.anthropic.com` with a
  minimal `max_tokens=1` request and parses the response headers
  for input-tokens + output-tokens windows (limit / remaining /
  reset). Probe costs ~1 token. Requires `ANTHROPIC_API_KEY` in
  `~/.pogo/bridget.env`; replies with a configuration hint when
  unset. The original mg-cac7 mockup showed 5-hour session +
  weekly windows, but those are Claude Code subscription
  concepts and are not surfaced by the Anthropic API; per the
  design's risk section we ship the per-window data the API
  actually exposes. For historical spend, use Claude Code
  `/cost` or `mg spend`. (mg-cac7, mg-79e8 P1 #10)

## [4.25.0] - 2026-05-13

### Changed

- `preapprove` no-arg state output now includes the next-action
  hint (e.g. "send 'preapprove true' to enable"). Behavior with
  args unchanged. (mg-7e0a)

## [4.24.0] - 2026-05-13

### Changed

- Approve / reject / revise replies now clear the matching
  approval-needed mail from human/new/ → human/cur/, preventing
  the directory from growing without bound and removing the
  soft-dependency on mg-show filtering for inbox cleanliness.
  (mg-14da)

## [4.23.0] - 2026-05-13

### Added

- `open mg-XXXX` command to retrieve designs awaiting approval.
  `read` now hints to use `open` when given an mg-id; mail
  message-id behavior unchanged. Closes the mg-93cb confusion.
  (mg-d3d7)

## [4.22.0] - 2026-05-13

### Changed

- Status view's Projects bucket now reads Type=project items
  filtered to in-progress lifecycle tags (in-progress /
  kickoff-done). Reports bucket reads Type=report items with
  awaiting-approval. Replaces the previous tag-overlay approach.
  Type=idea items with legacy Project tags still suppressed from
  Designs during the mayor.md transition window. (mg-1d2b Phase 5)

## [4.21.0] - 2026-05-12

### Reverted

- Reverted mg-bc75/mg-b3e1 awaiting-approval DM suppression.
  Bridge always delivers approval-needed Discord DMs regardless
  of preapproval state — the suppression was masking designs
  with open questions for the user. Dedupe of auto-approvable
  cases is now mayor's responsibility (via the pre-approval
  mail-from-human pattern). (mg-a0be)

## [4.20.0] - 2026-05-12

### Fixed

- `scan_pending_reports` filters out items whose underlying
  mg work item is shelved / done / archived. Stale
  Report-ready mails for closed items no longer pollute
  inbox view. Same filter is applied to
  `scan_pending_approvals` for approval-needed mails whose
  item has since been shelved. Defensive: any `mg show`
  failure falls through and surfaces the mail anyway
  (false positive preferable to false negative). (mg-403e)

## [4.19.0] - 2026-05-12

### Changed

- Status view hides Type=idea items with Project-lifecycle
  tags that aren't actively running. Recognizes both
  `in-progress` (canonical 5-state vocab) and `kickoff-done`
  (current mayor flow) as in-progress signals. Other Project
  states (`scheduled`, `handed-off-to-mayor`, `staged`, etc.)
  are hidden from the status view entirely — they live on
  the Product Roadmap. (mg-4955)

## [4.18.0] - 2026-05-12

### Changed

- Status view's Projects bucket now contains only Type=idea
  items tagged `in-progress`. Scheduled / Done / Cancelled
  projects no longer crowd the section. (mg-b824)

## [4.17.0] - 2026-05-12

### Changed

- Status view reverts the children-collapse from mg-44fe per
  mg-1ef2 revise. Children with `parent-project:` tag now appear
  in their natural type bucket (Designs / Tasks / Bugs); the
  Project entry stays in Projects. Approved Reports are filtered
  from the Reports bucket — Project entry surfaces them
  post-scheduling. (mg-1ef2 revise)

## [4.16.0] - 2026-05-12

### Fixed

- Bridge no longer spawns watcher tasks on every Discord reconnect.
  Previously, each reconnect (e.g., after laptop sleep) added 3 new
  watcher loops, causing N× duplicate DMs of agent mails after N
  wakes. (mg-f7c5)

## [4.15.0] - 2026-05-12

### Changed

- Status view surfaces children with `awaiting-approval` tag as a
  flagged sub-list under their Project parent (in addition to the
  aggregate count). Also extends `_PROJECT_TAGS` with
  `handed-off-to-mayor` + `staged` so director/mayor
  hand-off-stage items correctly bucket as Projects, not Designs.
  (mg-1ef2)

## [4.14.0] - 2026-05-12

### Changed

- Bridge skips Discord DMs + auto-moves approval-needed mails to
  `cur/` when preapproval is enabled. Replaces mayor's mg-85e3
  shell-pattern cleanup rule. (mg-bc75)

## [4.13.0] - 2026-05-12

### Changed

- help is now compact (one-liner per command); `help <command>` shows
  the full description. Resolves Discord truncation at the bottom of
  the help list (preapprove etc. now visible). Supersedes mg-a339's
  chunking approach. (mg-91d2)

## [4.12.0] - 2026-05-12

### Fixed

- help reply now packs bullets into multiple Discord messages when the
  COMMAND_LIST exceeds the per-message budget. Last bullets (preapprove
  etc.) no longer truncate. (mg-91d2)

## [4.11.0] - 2026-05-11

### Changed

- Status view collapses Project children (`parent-project:mg-XXXX` tag)
  under their parent Project entry. Children no longer render as
  separate items in Designs/Tasks/Bugs sections; instead the Project
  entry shows a count + status breakdown summary line. (mg-313f)

## [4.10.0] - 2026-05-11

### Removed

- Removed deprecated kickoff and hold Bridget verbs (help entries
  + handlers). New Director Flow makes kickoff automatic on
  roadmap-drafted; hold is replaced by mail cancel project <id>
  via the generic mail verb. (mg-0fd9)

## [4.9.0] - 2026-05-11

### Removed

- Removed deprecated balance command (help entry + handler).
  Implicit credit-error detection in the nudge handler unchanged.
  (mg-00b8)

## [4.8.0] - 2026-05-11

### Changed

- Reverted mg-ac5a's verbose preapprove handler rendering.
  Bridget just persists the flag and confirms — architect
  reads `~/.pogo/preapproval.json` directly each cycle, so
  per-agent surfacing was unnecessary noise. (mg-6767)

## [4.7.0] - 2026-05-11

### Changed

- Status view now splits Project-roots (Type=idea + Project-status
  tag) into a dedicated `Projects` section, rendered before
  `Designs`. Children of Projects (`parent-project:` tag only)
  stay in Designs/Tasks per their type. Project-status tags are
  the 5-state vocab plus `kickoff-pending` / `kickoff-done` /
  `roadmap-drafted`. (mg-946e)

## [4.6.0] - 2026-05-11

### Changed

- `preapprove` handler now reports which agents currently
  honor `enabled` and `fast`, and warns when `fast` is set
  but no agent implements it. The reply renders three lines
  derived from a static `PREAPPROVE_SUPPORT` map: who honors
  `enabled`, who honors `fast`, and (on set actions) who does
  *not* honor `enabled`. Bridget used to silently accept
  settings no agent respected — now the user can tell at a
  glance what's live. Bump `PREAPPROVE_SUPPORT` when an agent
  gains honoring. (mg-628d)

## [4.5.0] - 2026-05-11

### Added

- `read m<N>` reads mails by inbox index; works for id-less
  mails that had no `mg-XXXX` to address. `inbox` now numbers
  each unread mail as `[m1 / …]`, `[m2 / …]`, … so the slot is
  copy-paste-ready. Indices come from the most recent `inbox`
  output and may shift if new mail arrives in between. (mg-8d3d)

## [4.4.1] - 2026-05-11

### Fixed

- `read mg-XXXX` no longer auto-marks approval/Report mails as
  read; they stay surfaced in inbox until you reply with the
  matching action verb. The `read` handler now honors
  `PROTECTED_SUBJECT_PREFIXES` (the same list `dismiss` already
  respects), so `inbox`'s pending-approvals view survives a
  re-read of the underlying design. The mail footer renders
  `unread (action required)` for these mails. (mg-e818)

## [4.4.0] - 2026-05-11

### Changed

- `bug: [critical]` now routes to mayor instead of architect,
  eliminating the architect-mayor handoff round-trip. Filing a
  critical bug from Discord lands directly in mayor's inbox so a
  polecat can be dispatched without architect's design pass.
  Architect's existing mg-fb17 defensive shelve-and-mail-mayor
  rule remains for any critical bug that arrives via other
  routes. (mg-64b1)

## [4.3.0] - 2026-05-11

### Added

- New `kickoff <id>` and `hold <id> [<reason>]` commands. Both mail
  `mayor` with `--from=human`; subjects are `kickoff <id>` and
  `hold <id>` respectively. `kickoff` sends an empty body; `hold`
  sends the reason (empty if omitted). The id is validated via
  `mg show <id>` before the mail is sent, so a typo returns
  `✗ no such work item: <id>` instead of silently mailing mayor.
  Both id prefixes (`mg-` and `dr-`) are accepted. Mayor is the
  next agent in the project-kickoff workflow per mg-94dc; this
  unblocks the director→mayor→user kickoff flow that was deferred
  from mg-5418 Task 2. (mg-d6da / mg-5418)

## [4.2.0] - 2026-05-11

### Added

- New `inbox` command — the decide queue. Shows unread mail count +
  listing, pending approvals, and pending Reports awaiting your
  reply. Refactored out of the old kitchen-sink `status` view.
  (mg-3996 / mg-91de)

### Changed

- `status` now shows work in flight categorized by type, with
  fixed-order sections: **Reports**, **Designs**, **Bugs**,
  **Tasks**, and a defensive **Other** bucket for unknown types.
  Empty sections are omitted; if nothing is in flight, status
  returns `No work in flight.` Items render as `[<id>] <label>:
  <title>` with no leading bullet and title truncated to 80 chars.
  Items with status outside `available` / `claimed` / `pending`
  (archived, shelved, done) are excluded. Mail / approvals /
  Reports content moved to `inbox`. Inside the Reports section,
  items with a matching unread `Report ready: <id>` mail in
  `human/new/` are labeled `review` — a derived label, not a tag,
  that disappears the moment the matching mail is acted on.
  (mg-3996 / mg-91de)

## [4.1.1] - 2026-05-11

### Fixed

- `agents` view no longer renders every alive agent as 🟡 busy. pogod's
  `pogo agent diagnose` returns `healthy` for any crew agent whose
  process is alive within its idle threshold — never `idle` — so the
  4.1.0 healthy → busy map (mg-eb6e) tagged every healthy agent as
  busy. State now consults the agent's self-reported JSON state field
  as a tiebreaker when health is `healthy`: state=busy iff the JSON
  `state` starts with `busy:` AND its mtime is within 2 minutes;
  otherwise state=idle. Other health values (stalled / exited / dead)
  still ignore the JSON. Also: `dead` (registered as running but OS
  proc gone — wedge) now maps to ⚪ offline instead of 🔴 stalled, to
  match the rest of the offline lineage. (mg-9939)

## [4.1.0] - 2026-05-11

### Added

- `status` view now lists pending Reports alongside pending approvals.
  A new `scan_pending_reports()` scanner walks `human/new/` for subjects
  starting with `Report ready:` (director → human) and renders them as
  their own "Pending reports:" block when non-empty. (mg-af02 / mg-dbf6)

### Changed

- `agents` view: state (busy/idle/stalled/offline) is now derived from
  `pogo agent diagnose <name> --json` instead of the agent's
  self-reported `~/.pogo/agent-status/<name>.json`. pogod's `health`
  enum (`healthy`/`idle`/`stalled`/`exited`/`dead`) is the authority on
  whether an agent is working; the JSON state field no longer
  influences the badge color. Eliminates stale "busy" badges that
  persisted after the agent returned to its idle wait. Self-reported
  JSON is now advisory only — used for the optional busy-label badge
  when the derived state is busy AND the JSON file mtime is within the
  last 2 minutes (older labels are dropped). A diagnose failure for a
  known-running agent now falls back to ⚪ offline with a faded
  `(diagnose failed)` suffix instead of the v3.1.0 "busy by default"
  rule. (mg-b4c0 / mg-eb6e)
- `dismiss` and `dismiss all` no longer clear actionable mails — both
  `approval needed …` (design approvals) and `Report ready: …` (director
  Reports) now require the matching `approve` / `reject` / `revise` /
  `explain` reply to be marked read. Protected prefixes live in a new
  module-level `PROTECTED_SUBJECT_PREFIXES` tuple so future additions
  (e.g., a kickoff workflow for `Project ready: …`) are a one-line
  change. (mg-af02 / mg-dbf6)

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
- `approve`, `reject`, `revise`, and `explain` now route to director's
  mailbox when the work item has `type=report`; everything else continues
  to route to architect (today's default). Both `mg-XXXX` and `dr-XXXX`
  id forms are accepted across all id-bearing commands (`approve`,
  `reject`, `revise`, `explain`, `read`, `dismiss`). (mg-bf12)

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
