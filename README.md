# bridget

A Pogo ↔ Discord bridge. Watches your local pogo mailbox
(`~/.macguffin/mail/human/new/`) and DMs you on Discord whenever new mail
arrives, and listens for command DMs back from you (approve / reject / file
ideas / read mail / etc.) — routing them to `mg`. It's a one-file Python
service driven by a small env file, so you can run it under launchd, systemd,
nohup, or whatever supervisor you like.

## Prerequisites

- **[pogo](https://github.com/drellem2/pogo)** installed, with `mg` on your `PATH`. (If `mg` is in a non-standard
  location, set `MG_BIN` in the env file — see below.)
- A canonical pogo mail layout at `~/.macguffin/mail/human/{new,cur}/`, or set
  `POGO_MAIL_DIR` to the parent of `new/` and `cur/`.
- **Python 3.10+** with `venv` available (`python3 -m venv ...`).
- A **Discord bot** with the "Message Content" privileged intent enabled
  ([Discord developer portal](https://discord.com/developers/applications)),
  installed in a server you control. You need three values:
  - The bot token.
  - Your own Discord user ID (snowflake — bridget only DMs and only listens to
    this user).
  - The Discord server (guild) ID the bot lives in.

  In Discord, enable Developer Mode (Settings → Advanced → Developer Mode),
  then right-click your name / the server icon → "Copy ID".

## Roadmap & known bugs

Current planned work and known bugs are mirrored below from [ROADMAP.md](ROADMAP.md) and [KNOWN_BUGS.md](KNOWN_BUGS.md). Both files are the canonical source — update them (and this README section) in the same PR if you change roadmap or bug state. See [CONTRIBUTING.md](CONTRIBUTING.md).

### Roadmap

No active work in flight; everything below is backlog awaiting its trigger.

#### Backlogged items

Parked behind explicit triggers — re-enter the roadmap when the trigger fires.

##### CI check that PR template was respected
GH Actions workflow that diffs commit-modified roadmap/bug-list files against README mentions and warns if out of sync. **Trigger:** evidence that the existing manual discipline (PR-template checkbox) is failing across enough PRs to call drift a real pattern.

##### Auto-wire fresh-install smoke test into `test.sh`
`tests/smoke-fresh-install.sh` currently runs only when invoked explicitly. Auto-wire into `test.sh` (or add a `test.sh --full` flag) without breaking the current no-venv-required ergonomics. **Trigger:** a fresh-install regression lands on main because contributors didn't run the smoke test by hand.

### Known bugs

Open bugs against `bridget`. Maintained alongside mg state (the maintainer's local work tracker); update this file in the same PR that adds, dispatches, or closes a bug.

_No open bugs._

## Quick start

1. Clone and run the installer:
   ```bash
   git clone https://github.com/CloverRoss/bridget.git
   cd bridget
   ./install.sh
   ```
   `install.sh` is idempotent — it creates `~/.pogo/venv-bridget/`, installs
   `discord.py`, symlinks `~/.pogo/bin/bridget` to the script in your clone,
   and seeds `~/.pogo/bridget.env` from `bridget.env.example` (if no env file
   exists yet). Re-running it after a `git pull` is the supported upgrade path.
2. Edit your config:
   ```bash
   $EDITOR ~/.pogo/bridget.env
   ```
   At minimum, fill in `DISCORD_BOT_TOKEN`, `DISCORD_USER_ID`, and
   `DISCORD_SERVER_ID`. See [Configuration](#configuration) for optional keys.
3. Smoke-test in the foreground:
   ```bash
   ~/.pogo/bin/bridget
   ```
   You should see `logged in as <bot> (id=…)` and a startup DM in Discord.
   Stop with Ctrl-C once that works.
4. Run under a supervisor for the long term — launchd on macOS, systemd on
   Linux, or `nohup` for quick-and-dirty. See
   [Running as a service](#running-as-a-service) for templates.
5. If something goes wrong, see [Troubleshooting](#troubleshooting).

## Configuration

All config lives in `~/.pogo/bridget.env`. See
[`bridget.env.example`](bridget.env.example) for the full template.

| Key | Required? | Purpose |
|---|---|---|
| `DISCORD_BOT_TOKEN`  | yes | Discord bot token. |
| `DISCORD_USER_ID`    | yes | Your Discord user ID — bridget DMs and only listens to this user. |
| `DISCORD_SERVER_ID`  | yes | Guild the bot is installed in. |
| `MG_BIN`             | no  | Absolute path to `mg`. Default: resolved via `PATH`. |
| `POGO_BIN`           | no  | Absolute path to `pogo`. Default: resolved via `PATH`. |
| `POGO_MAIL_DIR`      | no  | Parent of `new/` and `cur/`. Default: `~/.macguffin/mail/human`. |
| `POGO_INBOX_REPO`    | no  | Repo where `idea:` and `bug:` file new items. Default: `~/.pogo/inbox`. |
| `POGO_MAIL_RECIPIENT` | no | Default recipient for `mail` command. Default: `mayor`. |
| `BRIDGET_REPO_DIR`   | no  | Override for the bridget git checkout. Default: self-detected from the script's location (works for the install.sh-managed symlink). |
| `BRIDGET_STUCK_MIN`  | no  | Minutes a `busy` agent must hold the same (state, label) before the `agents` view flips its glyph to 🟠 and adds a `long-running, check for stall` note. Default `30`. Bump this if you routinely run polecat batches that legitimately exceed 30m. Garbage values fall back to 30; values < 1 clamp to 1. |

Process environment variables override values in the env file, so a
launchd/systemd unit can inject overrides without editing the file.

### Reply routing (architect vs. director)

`approve`, `reject`, `revise`, and `explain` deliver the reply mail to the
mailbox that owns the work item. Routing is decided by the item's `Type:`
field (read from `mg show`):

- `type=report` → reply goes to **director** (director files its own
  approve-cycle items with this type).
- Anything else (`idea`, `bug`, `task`, …) → reply goes to **architect**
  (today's default; preserves existing behavior).

`mg show` failures or items with no `Type:` line fall back to architect,
so a missing item never silently drops a reply. The decision is
case-sensitive and exact-match — only the literal string `report` routes
to director.

Both id prefixes are accepted across all id-bearing commands: `mg-XXXX`
(today's prefix) and `dr-XXXX` (director's prefix once macguffin ships
per-project prefixes; bridget supports it now so the eventual flip is
director-side configuration only).

## Commands (DM the bot)

All commands are prefixed with `/` (Robin port item 2, mg-a0f3). Non-slash
DMs flow to the **chat-relay** (mg-c869): they're buffered for the crew
agent set by `/route` and the recipient gets a `pogo nudge … "N new
bridget messages"`. See [Chat-relay](#chat-relay-user--agent-dms) below.
Un-prefixed legacy commands still execute for one release with a stderr
deprecation warning, but the back-compat path drops in a follow-up.

- `/approve mg-XXXX` (or `dr-XXXX`) — approve a design (auto-clears related mails).
- `/reject mg-XXXX <reason>` (or `dr-XXXX`) — shelve idea + clear mails.
- `/revise mg-XXXX <feedback>` (or `dr-XXXX`) — request changes (auto-unshelves; clears mails).
- `/explain mg-XXXX <what>` (or `dr-XXXX`) — ask architect to elaborate without redesigning.
- `/kickoff pj-XXXX` — flip a Project from `ready-for-kickoff` to `in-progress` (mayor handles). Restricted to the `pj-` prefix (projects only) — legacy `mg-` projects must be edited manually via `mg edit mg-XXXX --add-tags=in-progress`. Routes as a verb-mail to the mayor; the mayor processes it asynchronously and mails you back when the transition is done. (ds-1482)
- `/read m<N>` (or `dr-XXXX`) — show a mail. `mN` indices come from the most recent `/inbox` output and may shift if new mail arrives in between. For designs awaiting approval, use `/open mg-XXXX` instead — `/read mg-XXXX` only returns a hint. (approval/Report mails stay unread until you reply with `/approve` / `/reject` / `/revise` / `/explain`.)
- `/open mg-XXXX` — return a Notes-app pointer for a design. Reply is `Notes app → Pogo Designs → <id>: <title>`; the architect publishes each design to the Notes folder on save. If `mg show` fails (unknown id) replies `open: mg item mg-XXXX not found`. Forward-only: designs filed before this change landed are not in Notes.
- `/idea: [tag] [tag] ... <text>` — file an idea with optional scope tags (e.g. `[bridget]`). *(Requires `POGO_INBOX_REPO`.)*
- `/bug: [tag] [tag] ... <text>` — file a bug with optional scope tags (e.g. `[discord-bridge]`). Use `[critical]` for crash-class bugs that should skip architect's design phase — routes directly to mayor. (Existing software is broken — not a new feature.) *(Requires `POGO_INBOX_REPO`.)*
- `/mail <subject>\n<body>` — send a mail to the configured recipient (default `mayor`; override via `POGO_MAIL_RECIPIENT`). Without a newline, the whole text becomes the subject.
- `/dismiss mg-XXXX` (or `dr-XXXX`) — mark all unread mail about an id as read. *Actionable mails — design approvals and Reports — are preserved; clear them via `/approve` / `/reject` / `/revise` / `/explain` instead.*
- `/dismiss all` — inbox-zero everything except actionable mails (design approvals + Reports — see [Pending reports & actionable mail](#pending-reports--actionable-mail)).
- `/status` — work in flight, by type (Projects / Reports / Designs / Bugs / Tasks). Project children flow into their natural type bucket; approved Reports are hidden once they have been scheduled into a Project. Reports awaiting your reply are labeled `review` (derived from a matching unread `Report ready: …` mail in `human/new/` — it isn't a tag on the item).
- `/inbox` — the decide queue: unread mail, pending approvals, and pending Reports awaiting your reply.
- `/agents` — list crew agents with a 4-state busy/idle indicator. Each row leads with a colored-circle emoji and state word: 🟢 **idle** (diagnose health `idle` — quiet but within the per-agent stall threshold), 🟡 **busy** (diagnose health `healthy` — recently active output), 🔴 **stalled** (diagnose health `stalled` or `dead`), or ⚪ **offline** (process not running, diagnose health `exited`, or the diagnose call itself failed). State is derived from `pogo agent diagnose <name> --json` — pogod is the authority on whether an agent is currently working. Busy rows may append a self-reported label as a trailing badge: bridget reads the optional `state` field (e.g. `"busy: drafting mg-XXXX"`) from `~/.pogo/agent-status/<name>.json`, but only renders it when the derived state is busy AND the JSON file mtime is within the last 2 minutes — older self-reports are dropped, so a stale "drafted" label can't linger after the agent has gone back to its idle wait. Self-reported JSON is therefore advisory only; if it's missing, malformed, or out of date the row still renders correctly from diagnose. Non-crew agents (e.g. polecats) are ephemeral per-task processes; they short-circuit to 🟡 **busy** while running, badged with their claimed mg-id when available. Each row also carries a duration appendix derived from bridget's state-history sidecar at `~/.pogo/bridget-state-history.json`: busy rows append `(Nm on <label>)` (or `(busy Nm)` if no label) and idle rows append `(idle Nm)`. The clock resets whenever the (state, label) tuple changes. Busy agents whose (state, label) has held longer than `BRIDGET_STUCK_MIN` minutes (default 30) get their 🟡 swapped for 🟠 plus a `long-running, check for stall` note so a wedge becomes obvious at a glance. (ds-25d7)
- `/nudge <agent> [reason]` — wake a stalled agent.
- `/restart` — git pull + restart bridget (after merging a PR; see [Remote restart](#remote-restart)).
- `/quiet <true|false> [HH:MM HH:MM]` — toggle agent quiet hours (default 23:00–06:00).
- `/preapprove [true [fast] | false]` — toggle architect + mayor pre-approval. `true`: skip approval/holdoff mails for designs with no open questions. `true fast`: also auto-resolve open questions with the recommendation. `false`: standard mail-approval flow (the default).
- `/librarian sync <space> [page-id]` — trigger a Confluence ingest via the librarian wrapper. Background fire-and-forget — bridget replies immediately and a separate DM lands from the `librarian` sender when the ingest finishes (success or failure). Full-space mode uses `--dedupe` so pages unchanged since the last sync are skipped. Space-key must match `^[A-Z][A-Z0-9_-]*$` (Confluence space-key shape). While a sync is in flight, `/tmp/librarian.lock` exists and a second invocation is rejected with "ingest already running" — `rm /tmp/librarian.lock` if stuck after a bridget crash. (mg-7c91 / mg-fea0 P1 #5)
- `/librarian search <query>` — grep ingested Confluence content via ripgrep. Shells out to `rg --type=md --max-count=2 --max-columns=200 --no-heading --line-number <query> <data-dir>` against `~/DUGLocal/confluence-ingestion/data/` (configurable via `CONFLUENCE_DATA_DIR`). Results are grouped by file with up to 2 matching lines per file; total output is capped at ~1500 chars (a truncation marker is appended if hit). No relevance ranking — refine the query if hits are noisy. Trusts ingest-time redaction (`REDACTION_POLICY` applied at write). Requires `rg` on PATH (`brew install ripgrep`). (mg-b853 / mg-fea0 P2 #8)
- `/spend` — show live Anthropic token quota consumption. Probes the Anthropic API to read rate-limit headers (input + output token windows) and reports % used + when each window resets. Requires `ANTHROPIC_API_KEY` in `~/.pogo/bridget.env`. Costs ~1 token per probe. For historical spend, use Claude Code `/cost` or `mg spend`. Note: the API surfaces its own rate-limit windows (typically per-minute) — Claude Code subscription quotas (5-hour session, weekly) are not exposed via the API.
- `/accountant run-now [<week>]` — trigger an out-of-cadence auto-budget cycle via the accountant wrapper. Background fire-and-forget — bridget replies immediately and a separate DM lands from the `accountant` sender when the cycle finishes (success or failure). Optional week selector must match ISO `YYYY-Www` (e.g. `2026-W18`); omit to run for the current week. While a cycle is in flight `/tmp/budget-cycle.lock` exists and a second invocation is rejected with "already running" — the wrapper script (mg-6064) owns the lock lifecycle, so `rm /tmp/budget-cycle.lock` only if stuck after a wrapper crash. (ds-3123 / mg-b64f P2 #8)
- `/accountant status` — last auto-budget cycle timestamp + log tail. Reads `~/.pogo/auto-budget.log` (stdout) and `~/.pogo/auto-budget.err.log` (stderr) — the launchd log paths from mg-6064 — and surfaces each file's mtime + last 5 lines (capped at ~400 chars per log). An empty err log is omitted. If neither log exists the reply is `no runs yet`. (ds-3123 / mg-b64f P2 #8)
- `/help [<command>]` (or `/?`) — print this list inside Discord, or full details for one command. *Note: cancelling a Project uses `/mail cancel project <mg-id>` — there is no separate verb for that.*

bridget only acts on DMs from the user whose ID is in `DISCORD_USER_ID`;
messages from anyone else are ignored.

## Chat-relay (user ↔ agent DMs)

Bridget mirrors Robin's Discord-native two-way chat in iMessage/DM form.
Slash commands (`/help`, `/approve`, …) are reserved for the bridget
control surface; **everything that isn't a slash command is chat**.

### User → agent (non-slash DM)

Any non-slash DM is buffered for the crew agent set by `/route`
(`mayor` / `director` / `architect` / `doctor`; default `mayor`) and the
recipient gets a `pogo nudge --immediate <agent> "N new bridget
messages"`, where N is the per-recipient pending count. The buffer
lives at `~/.pogo/bridget-chat-buffer.json` (schema `{agent: [{ts,
body}, …]}`), serialized via fcntl-locked read-modify-write so the
daemon's append and the agent-side drain don't race. The user's reply
is `💬 sent to \`<agent>\` (<N> pending)`; if the nudge itself fails
the message is still buffered and the reply surfaces the nudge error
so delivery isn't silent. Empty / whitespace DMs aren't buffered —
they reply with a `/help` hint.

The agent drains its queue with the CLI:

```
bridget chat read <agent_name>
```

Prints `<N> new bridget message(s) for <agent>:` followed by `[<iso-ts>]
<body>` lines (oldest first) and clears that recipient's buffer
atomically. An empty buffer is a normal result and exits 0 with `No
new bridget messages for <agent>.` (mg-c869, Robin port item 1)

### Agent → user (CLI)

Crew agents (and polecats) can push a DM to the user by invoking the
bridget script as a CLI:

```
bridget chat <agent_name> <body...>
```

This drops a maildir-style file in `~/.macguffin/mail/bridget-chat/new/`
(override the parent dir with `POGO_BRIDGET_CHAT_DIR`); the running
bridget daemon polls that directory on the standard 5-second tick and
emits each entry as a DM in `[From <agent-name>]: <body>` form. Long
bodies are split into multiple sequential DMs by `send_dm_chunked`
(paragraph / line / fence-aware — see mg-a3ef).

- Sender attribution comes from the explicit `<agent_name>` argument,
  not process identity — the caller passes its own crew name or polecat
  work-item id.
- Body args after `<agent_name>` are joined with single spaces. Empty
  body or empty agent name exits 2 with a usage line on stderr.
- The CLI path short-circuits before `load_config` and the `discord`
  import, so it runs in environments without bridget's full venv
  installed (polecat subprocess calls, ephemeral agent contexts). The
  `bridget chat read <agent>` drain form shares the same fast path.
- Successful delivery moves the file from `new/` to `cur/`, so a daemon
  restart doesn't redeliver. On a send failure the file stays in `new/`
  and the next tick retries.

Robin's equivalent on Ocean is `/opt/pogo/robin/bin/robin <body>`; the
bridget CLI follows the same file-drop transport.

## Inbox vs status

bridget splits the global pull view into two commands so "things I
need to read or decide" don't get mixed with "work the system is
currently doing":

- `inbox` — the decide queue. Unread mail count + listing, plus
  separate **Pending approvals** and **Pending reports** blocks
  (see [Pending reports & actionable mail](#pending-reports--actionable-mail)).
- `status` — work in flight, categorized into **Reports**,
  **Designs**, **Bugs**, **Tasks** (and a defensive **Other** bucket
  for unknown types). Only items with status `available` / `claimed`
  / `pending` appear; archived and shelved items are excluded.
  Empty sections are omitted entirely — when nothing is in flight,
  status returns `No work in flight.`

Inside the **Reports** section, an item may render with the derived
label `review` instead of its mg status. That label means "there's
an unread `Report ready: <id>` mail in `human/new/` awaiting your
`approve` / `reject` / `revise` / `explain` reply." It is **not** a
tag on the item — it disappears the moment you act on the matching
mail. Dismissing the mail is blocked for actionable subjects (see
below), so the only way to clear the `review` state is the matching
action verb.

## Pending reports & actionable mail

`inbox` lists two separate blocks for mail that needs an explicit
decision:

- **Pending approvals** — architect mails with `Subject: approval
  needed …`, awaiting `approve` / `reject` / `revise` / `explain`.
- **Pending reports** — director mails with `Subject: Report ready:
  …`, awaiting the same verbs against the relevant `dr-XXXX` id.

Both categories are **protected from `dismiss`**: neither
`dismiss mg-XXXX` / `dismiss dr-XXXX` nor `dismiss all` will move
them out of `human/new/`. The intent is "anything actionable must
be cleared by the matching action verb" — the verbs are the only
way to mark these mails read, which keeps decisions from getting
accidentally inbox-zeroed.

The protected prefixes are defined in the module-level
`PROTECTED_SUBJECT_PREFIXES` tuple in `bridget`; expanding the list
(e.g., to cover a future `Project ready:` workflow) is a one-line
change.

## Quiet hours

Quiet hours are a shared signal to crew agents (architect, mayor, etc.) that
they should skip polling during a configured window — e.g. so background
sweeps don't churn overnight. bridget owns the toggle; agents read the same
state file and decide what to do with it.

Toggle from Discord:

- `quiet` (or `quiet status`) — show the current state.
- `quiet true` — enable, using the previously-stored window (default
  23:00–06:00).
- `quiet false` — disable; the window is preserved for next enable.
- `quiet true 23:00 06:00` — enable with an explicit window. Times must match
  `HH:MM` (24-hour).

State lives at `~/.pogo/quiet.json`. This file is **shared with crew agents**,
not bridget-private — don't move or rename it. It's runtime state; not
committed to the repo.

## Pre-approval

Pre-approval is a phone-side policy that lets architect (and mayor) skip the
human-mail round-trip for designs that don't actually need a decision. Both
modes are off by default — pre-approval is opt-in.

- `enabled` — when `true`, architect skips the approval mail for designs
  with **no open questions** and just dispatches (with a one-line FYI mail).
  Mayor stops sending the redundant "held off due to open questions"
  follow-up mail. The user is pinged only when there's a genuine question.
- `fast` — when `true` (only meaningful with `enabled=true`), architect also
  auto-resolves open questions with its own recommendation and INFORMS the
  user via a single FYI mail listing each decision. Worst case: revise after
  the fact.

Toggle from Discord:

- `preapprove` — show the current state.
- `preapprove true` — enable; `fast` off.
- `preapprove true fast` — enable both.
- `preapprove false` — disable (also forces `fast` off; you can't fast-mode
  while pre-approval is disabled).

`preapprove fast` (without an explicit `true`/`false`) is rejected with a
usage hint — the enabled flag must always be stated.

State lives at `~/.pogo/preapproval.json`. This file is **shared with crew
agents**, not bridget-private — don't move or rename it. The JSON shape is
`{enabled: bool, fast: bool, updated_at: ISO-8601 UTC}`; both architect and
mayor consume it. It's runtime state; not committed to the repo.

> **Note:** Architect + mayor consume `~/.pogo/preapproval.json` once their
> follow-up prompt edits land (mg-1343 prompt-edit batch). Until that ships,
> flipping the toggle on changes no behavior outside this command — the
> file is settable and readable, but no agent acts on it yet.

## Task transition notifications

bridget pushes a Discord DM when a polecat task transitions to one of the
notable statuses:

- `🚀 claimed mg-XXXX [by <assignee>]: <title>`
- `✅ done mg-XXXX: <title>`
- `📦 shelved mg-XXXX: <title>`

State lives at `~/.pogo/bridget.task-states.json` (runtime; not committed).
The first run after deleting the cache silently re-primes — bridget records
current status without DMing, so you don't get a flood of notifications for
work that's already in flight. Only ideas/bugs/etc. with `type=task` trigger
notifications; other types are filtered out.

## Idea claim notifications

bridget pushes a Discord DM when the architect claims an idea:

- `🧠 architect claimed mg-XXXX: <title>`

State lives at `~/.pogo/bridget.idea-claims.json` (runtime; not committed).
The first run after deleting the cache silently re-primes — only ideas newly
appearing in `mg list --status=claimed` after that point produce a DM. Only
items with `type=idea` trigger notifications; tasks and other types are
filtered out.

## Mail action log

bridget appends a JSON line to `~/.pogo/bridget.mail-actions.log` every
time a Discord command changes mail state — `read mg-XXXX`, `dismiss
mg-XXXX`, or `dismiss all`. Each line carries an ISO8601 UTC timestamp,
the action, the mg-id (when scoped), and `by: human`.

The log is bridge-private runtime cache (not committed). Crew agents can
consume it on resume to reconstruct mail-state changes that happened
during an outage. If the file gets too large, delete it — bridget
re-creates it on the next mail-state change.

## Remote restart

The `restart` Discord command upgrades a running bridget to the latest
`origin/main` without touching the host. The flow is: `git pull --ff-only` in
the bridget checkout, run `build.sh` as a syntax check, then `os._exit(0)` so
the supervisor (launchd / systemd) respawns the process.

bridget self-detects its checkout from `Path(__file__).resolve().parent`, which
works whenever `~/.pogo/bin/bridget` is the install.sh-managed symlink to the
script in your clone. Set `BRIDGET_REPO_DIR` in `bridget.env` only if you run
bridget from an unusual setup where that resolution doesn't land on the repo
root.

If the pull or syntax check fails, bridget reports the stderr in Discord and
keeps running on the old code — you don't get stranded.

**Bootstrap caveat.** The first `restart` after merging a PR that itself
modifies the `restart` command must be done manually on the host (since the
running bridge is still on the old code). After that, `restart` keeps you in
sync.

## Running as a service

For v0.1, bridget is just a long-running Python process — supervise it however
you'd supervise any other foreground service. A few options:

- **macOS (launchd):** wrap `~/.pogo/bin/bridget` in a `~/Library/LaunchAgents/`
  plist with `RunAtLoad`, `KeepAlive`, and `StandardOutPath` /
  `StandardErrorPath` set to log files under `~/.pogo/`.
- **Linux (systemd):** a user unit (`~/.config/systemd/user/bridget.service`)
  with `ExecStart=%h/.pogo/bin/bridget`, `Restart=always`, then
  `systemctl --user enable --now bridget`.
- **Quick-and-dirty:** `nohup ~/.pogo/bin/bridget >>~/.pogo/bridget.log 2>&1 &`.

Bundled launchd / systemd templates and an `install.sh --service=...` flag are
on the roadmap; for now, write the unit yourself. PRs welcome.

## Troubleshooting

When bridget is running under a supervisor, stderr is the first place to look.
With the launchd / systemd templates in [Running as a service](#running-as-a-service),
that's whatever path you set for `StandardErrorPath` (launchd) or whatever
`journalctl --user -u bridget` returns (systemd). Foreground runs print
straight to your terminal.

Common failure modes:

- **`could not find the mg binary on PATH`** — pogo isn't installed, or its
  `bin/` isn't on the PATH that bridget sees (this is common under launchd,
  which runs with a minimal PATH). Set `MG_BIN` (and optionally `POGO_BIN`)
  in `~/.pogo/bridget.env` to absolute paths.
- **`config file not found: ~/.pogo/bridget.env`** — re-run `./install.sh`
  from the repo, or copy `bridget.env.example` to `~/.pogo/bridget.env`
  manually.
- **`missing required key(s) in ~/.pogo/bridget.env`** — fill in the three
  `DISCORD_*` values; they're all required.
- **`DISCORD_USER_ID and DISCORD_SERVER_ID must be integers`** — these are
  Discord *snowflake IDs*, not usernames. Enable Developer Mode in Discord,
  right-click the user / server, and "Copy ID".
- **Bot logs in but never DMs you** — most likely the "Message Content"
  privileged intent isn't enabled on the bot in the Discord developer portal,
  or the bot isn't a member of the server in `DISCORD_SERVER_ID`.
- **No mail notifications** — verify `~/.macguffin/mail/human/new/` exists
  (or whatever you set `POGO_MAIL_DIR` to). bridget skips mail-watching
  silently when the directory is missing.
- **`restart` says git pull failed** — the bridget checkout has uncommitted
  changes or a divergent branch. Resolve manually in the repo; bridget keeps
  running on the old code in the meantime.

## Project status

**v1.0 — feature parity with the original author's personal install.** Should
work on any macOS or Linux machine with Python 3.10+, pogo installed, and a
Discord bot. Issues and patches that improve portability or add platform
support are welcome.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
