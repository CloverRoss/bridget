# ia-5f66 — Bridget Inject API

Cross-host message-injection endpoint that surfaces Ocean → Land
agent pings into mayor's existing bridget chat-perception path.

Per pj-7d2b Robin v2.1 (Track 4) gate; spec drafted by Land Mayor,
director-approved 2026-05-24 19:59Z (dispatch-ok ia-5f66).

## Why

Today mayor (Land Mayor) perceives incoming agent communications via
two channels:
1. **bridget chat from human** — Clover's Discord DMs land in mayor's
   bridget message buffer; mayor polls on each loop tick.
2. **mg mail** — agents (architect, director, mayor on Ocean) send mg
   mail; mayor's `mg mail list` polls each tick.

Ocean → Land cross-host pings currently use mg-mail with Robin push
to Discord. Round-trip latency is multi-minute. Robin v2.1 wants
sub-second cross-host pings.

Per Clover 2026-05-24 18:29Z UX directive: incoming Robin v2.1 pings
to Land Mayor should feel like a bridget nudge (same perception path,
same `[From X via Robin] body` rendering) rather than spawn a
parallel channel mayor has to learn.

This spec defines the API that the Ocean-side Robin v2.1 receiver
daemon (mg-b0d2) calls on Mac to inject incoming pings into the
bridget message buffer.

## Decides (director-approved)

- **OQ1 — transport between Ocean and Mac**: reuse the existing
  ntfy.sh push channel from Land Bridge v1. Ocean publisher pushes
  to ntfy; Mac-side receiver script unpacks the push and calls this
  API on `127.0.0.1`. No new network exposure; composes with the
  posture v1 / Land Bridge bearer envelope Clover already approved.

- **OQ2 — HMAC secret location**: `~/.pogo/bridget.inject-secret`,
  user-owned (mode 0600, owned by cloverross). Bridget runs as user;
  secret should match its trust boundary. Distinct from the
  system-daemon Robin v2.1 `/etc/pogo-robin-ping/*` files.

- **OQ3 — mayor-down detection**: Land Mayor's implementation picks
  at code time. Preference: reuse an existing bridget poll-cycle
  marker if one exists; otherwise add `~/.pogo/bridget.last-poll`
  with a one-line atomic write on each cycle. Not blocking the spec.

## Endpoint

```
POST http://127.0.0.1:8765/v1/inject
```

- Bound to `127.0.0.1` only. Never accessible from the LAN or
  internet. The Mac-side receiver script is the only legitimate
  caller; sshd from Ocean cannot reach it.
- Port 8765 chosen for memorability; configurable via
  `BRIDGET_INJECT_PORT` env in `~/.pogo/bridget.env`.

## Request

### Headers

| Header | Required | Description |
|---|---|---|
| `X-Robin-Sender` | yes | Sending agent name. Vocabulary: `mayor`, `director`, `architect`, `polecat-*`. Used in the bridget message label. |
| `X-Robin-Recipient` | no (default `mayor`) | Target agent. Today only `mayor` is delivered; future bridget-perceiving agents add to the routing here. |
| `X-Robin-Ts` | yes | Unix timestamp (integer seconds). Reject if `\|now − ts\| > 300s` (replay defense). |
| `X-Robin-Hmac` | yes | Hex SHA-256 HMAC over the pipe-delimited tuple `sender\|recipient\|ts\|body` (UTF-8 encoded). Matches Ocean's `cross_host_ping.auth.compute_hmac` field order so the receiver script can pass the publisher's HMAC through unchanged. Lowercase hex. |
| `Content-Type` | yes | `text/plain; charset=utf-8` |

### Body

Raw UTF-8 message text. ≤ 4096 bytes (longer rejected with 413).

## Response

| Status | Meaning | Body |
|---|---|---|
| 200 | Delivered to mayor's bridget queue | `{"delivered": true, "audit_id": "<uuid>"}` |
| 400 | Malformed (missing header, bad sender format, etc.) | `{"error": "<short reason>"}` |
| 401 | HMAC mismatch OR timestamp skew > 300s | `{"error": "auth"}` |
| 413 | Body > 4096 bytes | `{"error": "too large"}` |
| 429 | Rate limit breached (30 msg/min/sender) | `{"error": "rate limit", "retry_after_seconds": <N>}` |
| 503 | Bridget chat-poll loop hasn't ticked in > 5min (mayor likely dead) | `{"error": "mayor not responding"}` |

## Side effect — bridget message buffer write

On 200 response, bridget writes an entry into the bridget chat
buffer for mayor that renders, on the next mayor tick, as:

```
[From <sender> via Robin] <body>
```

Identical rendering to a Discord-DM `bridget chat mayor "..."` from
human, except for the `via Robin` qualifier. Mayor's perception path
makes no distinction beyond the sender label and the qualifier.

Buffer file: `~/.pogo/bridget-chat-buffer.json` (existing format,
read by `bridget chat read mayor`). The API appends a new entry with
shape:

```json
{
  "ts": "2026-05-24T19:54:30Z",
  "from": "<sender>-via-robin",
  "body": "<body>",
  "audit_id": "<uuid>"
}
```

The `-via-robin` suffix on `from` lets the bridget UI render the
qualifier without bridget needing to know about the inject path.

## Rate limit

Per-sender sliding-window counter: 30 messages per 60-second window.
Sender identity is the value of `X-Robin-Sender`.

Implementation: in-memory `dict[sender, deque[float]]`; pop entries
older than 60s; reject if `len > 30`. Reset on bridget restart (60s
window so practical reset takes < 1min).

Configurable via `BRIDGET_INJECT_RATE_LIMIT` env (default `30/60`).

## Audit

Appends one JSONL line per inject attempt (delivered OR rejected) to
`~/.pogo/bridget.inject-audit.jsonl`:

```json
{
  "ts": "2026-05-24T19:54:30Z",
  "audit_id": "<uuid>",
  "from": "<sender>",
  "body_sha256_first16": "abc1234567890def",
  "body_bytes": 142,
  "status": "delivered" | "rejected_<reason>",
  "rejected_reason": "auth" | "rate_limit" | "too_large" | "malformed" | "mayor_down" | null
}
```

Body content itself is NOT logged (per [[bearer-credentials-no-llm]]
adjacency — even non-bearer pings may carry sensitive coordination
text). Body is hashed for forensic correlation only.

Audit file is append-only. Rotate manually if it grows; no automated
rotation in v1.

## Mayor-down detection (503 path)

If `(now - bridget_last_poll_ts) > 300 seconds`, return 503 instead
of writing the buffer entry. Receiver writes `delivery_failed` to
its own audit. Sender's UI (Ocean side) surfaces "nudge failed".

Bridget writes its last-poll timestamp:
- Preferred: reuse existing internal marker if bridget has one
  (search before adding new file).
- Fallback: `~/.pogo/bridget.last-poll` — one-line ISO-8601 UTC
  timestamp, atomically rewritten on each poll cycle.

Pick at code time, not blocking the spec.

## Auth: HMAC envelope

Shared secret lives in `~/.pogo/bridget.inject-secret` (mode 0600,
owned by cloverross). 32 bytes of random data, hex-encoded:

```
# ~/.pogo/bridget.inject-secret
SECRET=a1b2c3d4e5f6...
```

The secret value MUST match Ocean's `/etc/pogo-robin-ping/ocean-to-land.env`
BEARER so the publisher's HMAC verifies on Mac side without re-signing.
(Receiver script is a thin pass-through; it forwards Ocean's headers
unchanged.) Same value, distinct files because the trust boundaries
differ — Ocean's is a system daemon, Mac's is a user process.

HMAC scheme — pipe-delimited tuple, matches Ocean's
`cross_host_ping.auth.compute_hmac` exactly:

```python
import hmac, hashlib
mac = hmac.new(
    secret.encode("utf-8"),
    f"{sender}|{recipient}|{ts}|{body}".encode("utf-8"),
    hashlib.sha256,
).hexdigest()
```

Field invariants (mirror Ocean's): none of `sender`, `recipient`, `ts`
may contain `|` or newlines. `body` may contain `|` (the parser splits
on the first three `|` only, so message bodies round-trip safely).

Why HMAC and not bearer-token-only: prevents replay via the
timestamp window AND tampering of body. Loopback-only binding
narrows the attack surface; HMAC defends against process-local
adversaries (e.g., another process on the Mac wanting to inject
fake pings).

Why not mTLS: overhead too high for loopback-only; HMAC is sufficient
when the network layer is `127.0.0.1`.

## Failure mode — bridget down

If bridget process is itself down, the API listener doesn't exist;
TCP connect on port 8765 refuses. Receiver writes
`delivery_failed_connection_refused` to its own audit; sender's UI
surfaces "nudge failed; bridget down".

Recovery: bridget restart (launchd auto-restart) brings listener
back. Pings during the gap are dropped (no buffering in v1).

## Implementation notes (for the impl polecat)

- aiohttp integration: bridget already uses asyncio. Add an aiohttp
  application alongside the existing Discord-bot loop. Both run in
  the same event loop. Listener binds in `_main_async()` next to
  the existing Discord-client startup.
- Approximately 150 LOC for the listener + 50 LOC for the HMAC +
  rate-limit + audit helpers.
- Tests in `tests/test_inject_api.py`: cover happy path, HMAC
  mismatch, timestamp skew, body-too-large, rate limit, mayor-down
  503, malformed headers.
- Per CONTRIBUTING.md flow: update CHANGELOG.md, ROADMAP.md, README.md
  as the impl ships.

## Source of truth

This repo: `~/DUGLocal/bridget/docs/ia-5f66-inject-api.md`.

Impl will land at `~/DUGLocal/bridget/bridget` (the main Python
file) with helpers split into `~/DUGLocal/bridget/bridget_inject.py`
if useful for test isolation.

## Out of scope (v1)

- Bidirectional push (Mac → Ocean) — that's the publisher CLI on
  Land side, separate ia-.
- Group send (one ping to multiple agents) — single-recipient only
  for v1.
- Persisted queue on 503 — drop on the floor; sender retries are
  the recovery story.
- Message editing / deletion after delivery.
- Per-sender authz (which Ocean agents can inject) — auth is at the
  HMAC layer; if you have the secret you're trusted.

## Refer-back

- pj-7d2b Robin v2.1 Vision (Ocean side): `/opt/pogo/process-bible/Director Guidance/Products/Product Vision- Robin v2.1.txt`
- ds-7d2b Robin v2.1 architect design (Ocean): `/opt/pogo/process-bible/Architect Guidance/Designs/ds-7d2b-robin-v2.1.md`
- Clover 2026-05-24 18:29Z UX directive: pings feel like bridget nudge
- Director 2026-05-24 19:59Z dispatch-ok ia-5f66

— Land Mayor, 2026-05-24
