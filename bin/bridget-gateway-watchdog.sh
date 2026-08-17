#!/bin/zsh
# bridget-gateway-watchdog.sh — kickstart the Discord bridge ONLY on proof its
# gateway websocket went dead. Never on process-level signals.
#
# THE FAILURE (mg-c1d5, observed 2026-08-17)
#   Clover could not reach mayor: the bot read OFFLINE in Discord. Every
#   process-level signal said fine —
#       launchctl print gui/501/com.pogo.discord-bridge
#         state = running, keepalive, pid = 770, last exit code = (never exited)
#   — because nothing at the process level WAS wrong. The gateway websocket
#   underneath had gone stale across a host sleep. It recovered only by
#   accident, when mayor happened to send an outbound message an hour later
#   (the err log shows `on_ready fired (pid=770 ...)` at 06:36:05Z, coincident
#   with the send). Left alone it had simply been sitting dead.
#
#   PROCESS ALIVE IS NOT CHANNEL ALIVE. launchd's KeepAlive is structurally
#   blind to this, so adding another process-level check would reproduce the
#   bug, not fix it. Exactly the same failure class as land-robin-receive's
#   silent death (mg-206e), and this watchdog is deliberately a close mirror of
#   bin/land-robin-watchdog.sh — every suppression rule below was learned the
#   hard way over there and is not re-litigated here.
#
# WHAT MAKES THE SIGNAL REAL
#   bridget stamps ~/.pogo/bridget-gateway.heartbeat from inside
#   `on_socket_raw_receive` — i.e. only when a frame actually arrives DOWN THE
#   GATEWAY WEBSOCKET. Nothing else writes it, and no timer inside the process
#   can, which matters because the process is perfectly alive throughout this
#   failure. Discord ACKs our heartbeat every ~41s even on a totally idle
#   connection, so a live socket freshens the file continuously; a dead one
#   cannot freshen it at all. A fresh file is therefore round-trip evidence,
#   not an inference from "the log is quiet".
#
#   The stamp also carries `latency_s`, discord.py's measured
#   HEARTBEAT -> HEARTBEAT_ACK round trip. This watchdog REQUIRES that to be a
#   finite positive number before it will read the file as liveness. A stamp
#   with `latency_s: null` means "no round trip has been measured" and counts
#   as NO PROOF — never as healthy. That specific false positive is why this
#   rule exists: the robin heartbeat once emitted a startup stamp with
#   rtt_s: null and the watchdog read it as healthy while the channel was not.
#
# WHY THE STALENESS THRESHOLD IS 300s
#   Discord's gateway heartbeat interval is ~41.25s and bridget rate-limits
#   its writes to one per 30s, so on the QUIETEST possible healthy connection
#   the file is refreshed every ~41s and its worst-case healthy age is ~75s
#   (one heartbeat interval landing just after a rate-limit window closed).
#   300s is four times that: comfortably longer than the normal gateway
#   heartbeat interval, so a slow ACK, a brief network hiccup or a discord.py
#   reconnect (which completes in seconds) can never trip it, while a genuinely
#   dead socket is caught within one 5-minute watchdog tick of going stale.
#
# HEAL ACTION — kickstart ONLY, NEVER bootout
#   launchctl kickstart -k gui/<uid>/com.pogo.discord-bridge
#   This service is also the inject server, Clover's DM channel and the claim
#   announcer. `launchctl bootout` takes all of that down and it does NOT come
#   back on its own, so it is not an option here at any severity.
#
# THE SIGNALS, IN ORDER
#   1. recent system_wake   -> skip (a stale stamp right after a wake is
#                              EXPECTED, not a fault), but still CLEAR a stale
#                              alert state if the heartbeat is fresh AND
#                              carries a finite latency
#   2. heartbeat missing    -> alert only, NEVER kickstart
#   3. fresh + finite latency -> ok
#   4. host downtime explains the whole gap -> informational log, skip
#   5. the bridge itself started too recently to have proven anything -> skip
#   6. otherwise            -> DEAD: kickstart (cooldown-gated) + mail mayor
#
# Usage:
#   bridget-gateway-watchdog.sh              # one check (what launchd runs)
#   bridget-gateway-watchdog.sh --dry-run    # decide and log; never heals or mails
#
# Exit: 0 = ok, skipped, or kickstart fired; 1 = needs a human (missing
#       heartbeat, or dead again inside the cooldown).
# Log:  ~/.pogo/log/bridget-gateway-watchdog.log
set -u

# $HOME/go/bin FIRST and non-negotiable: `mg` is macguffin at ~/go/bin/mg, but
# macOS ships an unrelated /usr/bin/mg (the MicroEMACS editor). launchd hands us
# a minimal PATH, so without this every `mg mail send` below would run the
# EDITOR headless and die with "panic: Terminal setup failed" — every alert
# silently lost. That exact bug ate mayor-health-monitor's alerts for weeks.
export PATH="$HOME/go/bin:$HOME/.local/bin:$HOME/go-toolchain/go/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# Defaults are the live paths. The BRIDGET_GATEWAY_WATCHDOG_* overrides exist so
# tests/test_gateway_watchdog.py can drive every branch against synthetic files
# and stubbed commands — proving the heal decision without ever cycling the real
# bridge, which is Clover's only DM channel to the crew.
LOG="${BRIDGET_GATEWAY_WATCHDOG_LOG:-$HOME/.pogo/log/bridget-gateway-watchdog.log}"
HEARTBEAT="${BRIDGET_GATEWAY_WATCHDOG_HEARTBEAT:-$HOME/.pogo/bridget-gateway.heartbeat}"
COOLDOWN="${BRIDGET_GATEWAY_WATCHDOG_COOLDOWN_FILE:-$HOME/.pogo/bridget-gateway-watchdog.last-restart}"
COOLDOWN_SECS="${BRIDGET_GATEWAY_WATCHDOG_COOLDOWN_SECS:-1800}"  # max one auto-kickstart / 30min
STALE_SECS="${BRIDGET_GATEWAY_WATCHDOG_STALE_SECS:-300}"          # see "WHY 300s" above
SERVICE="${BRIDGET_GATEWAY_WATCHDOG_SERVICE:-com.pogo.discord-bridge}"
KICKSTART_CMD="${BRIDGET_GATEWAY_WATCHDOG_KICKSTART_CMD:-launchctl}"
WAKE_CMD="${BRIDGET_GATEWAY_WATCHDOG_WAKE_CMD:-pogo events list --since=20m --type=system_wake --json}"
MAIL_CMD="${BRIDGET_GATEWAY_WATCHDOG_MAIL_CMD:-script -q /dev/null mg}"
# How long the host has been RUNNING, which is the only clock on which a silent
# gateway means anything. `sysctl -n kern.boottime` prints
#   { sec = 1785953248, usec = 621001 } Wed Aug  5 19:07:28 2026
# and boot_epoch() below also accepts a bare epoch so the suite can stub it.
BOOT_CMD="${BRIDGET_GATEWAY_WATCHDOG_BOOT_CMD:-sysctl -n kern.boottime}"
# Grace on top of STALE_SECS before a post-boot silence counts as evidence.
# bridget has to be started by launchd, read its env, log in to Discord, sync
# the slash-command tree and then wait out one ~41s gateway heartbeat before it
# can stamp anything with a finite latency. 300s + this = 10min, the same order
# as the wake window in signal 1 — a boot is just the coldest possible wake.
BOOT_GRACE_SECS="${BRIDGET_GATEWAY_WATCHDOG_BOOT_GRACE_SECS:-300}"
# Slop on the "heartbeat predates this boot" comparison. The last stamp lands
# some seconds before the machine actually goes down, and an NTP step across the
# downtime moves the two clocks relative to each other. Small on purpose: this
# margin only ever makes the watchdog QUIETER, so it must not be wide enough to
# swallow a heartbeat that was really stamped after boot.
BOOT_SLOP_SECS="${BRIDGET_GATEWAY_WATCHDOG_BOOT_SLOP_SECS:-120}"
# Seconds the bridge PROCESS has been running (signal 5). Same argument as the
# host-boot cross-check one level down: a bridge that started 20s ago has not
# yet had time to open a gateway, receive a frame and measure a round trip, so
# its silence is not evidence of anything. Without this, any restart of the
# bridge from any cause (a deploy, a manual kickstart, mayor's own healing) that
# happened to land shortly before a watchdog tick would produce a false alarm —
# and a false alarm at 3am is its own bug. Prints NOTHING when it cannot tell.
SERVICE_UPTIME_CMD="${BRIDGET_GATEWAY_WATCHDOG_SERVICE_UPTIME_CMD:-}"
SERVICE_GRACE_SECS="${BRIDGET_GATEWAY_WATCHDOG_SERVICE_GRACE_SECS:-120}"
# Transition-gated alerting (see report_bad). STATE_FILE remembers the last
# verdict alerted on; REMINDER_SECS is the low-frequency re-alert cadence while
# one condition persists unchanged. Hourly, not per-run: N identical mails bury
# the real one.
STATE_FILE="${BRIDGET_GATEWAY_WATCHDOG_STATE_FILE:-$HOME/.pogo/bridget-gateway-watchdog.alert-state}"
REMINDER_SECS="${BRIDGET_GATEWAY_WATCHDOG_REMINDER_SECS:-3600}"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

mkdir -p "$(dirname "$LOG")"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

# boot_epoch — unix time the host last booted, or nothing at all if it cannot be
# read. Prints NOTHING rather than a guess: callers must be able to tell "the
# host has been up 4 minutes" from "I do not know how long the host has been
# up", because only the first may silence an alert.
boot_epoch() {
  local out sec
  out=$(${=BOOT_CMD} 2>/dev/null)
  [ -n "$out" ] || return 1
  # macOS form: "{ sec = 1785953248, usec = 621001 } Wed Aug  5 19:07:28 2026".
  # Take the FIRST run of digits, which is the epoch in that form and is also
  # the whole answer for a bare-epoch printer. Do NOT reach for `.*sec *= *`:
  # that matches greedily and lands on the u-sec field, handing back 621001 —
  # a boot in 1970, i.e. an uptime of 56 years, i.e. no suppression ever.
  sec=$(printf '%s\n' "$out" | sed -n 's/^[^0-9]*\([0-9][0-9]*\).*/\1/p' | head -1)
  case "$sec" in (''|*[!0-9]*) return 1 ;; esac
  printf '%s\n' "$sec"
}

# service_uptime — seconds the bridge process has been running, or NOTHING when
# it cannot be determined (service not loaded, no pid, unparseable start time).
# Same discipline as boot_epoch: "I could not read it" must stay distinguishable
# from "it just started", because only the second may silence an alert.
service_uptime() {
  if [ -n "$SERVICE_UPTIME_CMD" ]; then
    local out
    out=$(${=SERVICE_UPTIME_CMD} 2>/dev/null)
    case "$out" in (''|*[!0-9]*) return 1 ;; esac
    printf '%s\n' "$out"
    return 0
  fi
  local pid started epoch
  pid=$(launchctl print "gui/$(id -u)/$SERVICE" 2>/dev/null \
        | sed -n 's/^[[:space:]]*pid = \([0-9][0-9]*\).*/\1/p' | head -1)
  case "$pid" in (''|*[!0-9]*) return 1 ;; esac
  # macOS ps has no `etimes`, so take lstart and convert. Trailing padding in
  # the lstart column would make `date -j -f` fail, hence the squeeze.
  started=$(ps -o lstart= -p "$pid" 2>/dev/null | sed -e 's/[[:space:]]*$//')
  [ -n "$started" ] || return 1
  epoch=$(date -j -f "%a %b %d %T %Y" "$started" +%s 2>/dev/null)
  case "$epoch" in (''|*[!0-9]*) return 1 ;; esac
  local up=$(( $(date -u +%s) - epoch ))
  [ "$up" -lt 0 ] && up=0
  printf '%s\n' "$up"
}

# heartbeat_age — age in seconds of the gateway heartbeat, or NOTHING AT ALL if
# there is no file or its mtime cannot be read.
heartbeat_age() {
  [ -f "$HEARTBEAT" ] || return 1
  local mtime
  mtime=$(stat -f %m "$HEARTBEAT" 2>/dev/null)
  case "$mtime" in (''|*[!0-9]*) return 1 ;; esac
  printf '%s\n' $(( $(date -u +%s) - mtime ))
}

# heartbeat_latency — the `latency_s` field, or NOTHING when it is absent, null
# or not a positive finite number. THE NULL CASE IS THE POINT: bridget writes
# `latency_s: null` when no HEARTBEAT_ACK round trip has been measured, and a
# null must never read as liveness (that exact false positive burned the robin
# heartbeat). Deliberately no jq — this runs from launchd with a minimal PATH
# and must not acquire a dependency to answer a one-field question.
heartbeat_latency() {
  [ -f "$HEARTBEAT" ] || return 1
  local raw
  raw=$(sed -n 's/.*"latency_s"[[:space:]]*:[[:space:]]*\([^,}[:space:]]*\).*/\1/p' \
        "$HEARTBEAT" 2>/dev/null | head -1)
  [ -n "$raw" ] || return 1
  case "$raw" in
    null|Null|NULL) return 1 ;;
    ''|*[!0-9.eE+-]*) return 1 ;;   # "NaN", "Infinity", quoted junk
  esac
  # Positive and finite. awk's numeric coercion also rejects a bare "." or "-".
  awk -v v="$raw" 'BEGIN { exit !(v + 0 > 0) }' || return 1
  printf '%s\n' "$raw"
}

# mg needs a TTY, hence the `script -q /dev/null` wrapper and the </dev/null.
# Mails MAYOR (not human): a dead gateway is an operational action — she owns
# the kickstart and the re-verification round trip.
alert() {
  local subject="$1" body="$2"
  if [ "$DRY_RUN" -eq 1 ]; then
    log "DRY-RUN would mail mayor: $subject"
    return 0
  fi
  ${=MAIL_CMD} mail send mayor --from=bridget-watchdog \
    --subject="$subject" --body="$body" </dev/null >> "$LOG" 2>&1
}

# --- transition-gated alerting ------------------------------------------------
# States: ok | no-heartbeat | dead. Anything else on disk is corruption and reads
# as state-UNKNOWN, which ALERTS — a mangled file must never eat a page. Skip
# branches leave the last verdict standing, EXCEPT that the wake skip may clear
# it on positive proof (see signal 1).

read_state() {
  LAST_STATE="" LAST_ALERT=0 STATE_ENTERED=0
  [ -f "$STATE_FILE" ] || return 0
  read -r LAST_STATE LAST_ALERT STATE_ENTERED < "$STATE_FILE" 2>/dev/null
  case "$LAST_ALERT" in (*[!0-9]*|"") LAST_ALERT=0 ;; esac
  case "$STATE_ENTERED" in (*[!0-9]*|"") STATE_ENTERED=0 ;; esac
  case "$LAST_STATE" in
    ok|no-heartbeat|dead) ;;
    *) LAST_STATE="" LAST_ALERT=0 STATE_ENTERED=0 ;;   # corrupt -> unknown -> alerts
  esac
}

# Atomic temp-file + rename, same discipline as bridget's own heartbeat write:
# the next run can never read a half-written line. --dry-run never writes, so a
# dry-run cannot consume a transition a real run should still alert on.
write_state() {
  [ "$DRY_RUN" -eq 1 ] && return 0
  local tmp="$STATE_FILE.tmp.$$"
  printf '%s %s %s\n' "$1" "$2" "$3" > "$tmp" && mv -f "$tmp" "$STATE_FILE"
}

# report_bad <state> <subject> <body> — mail on transition; stay quiet while the
# SAME state persists (one reminder per REMINDER_SECS). Gates only the mail;
# callers keep their kickstart/cooldown/exit behaviour.
report_bad() {
  local state="$1" subject="$2" body="$3"
  local now; now=$(date -u +%s)
  read_state
  if [ "$state" != "$LAST_STATE" ]; then
    log "state transition: '${LAST_STATE:-unknown}' -> '$state' — alerting mayor"
    if alert "$subject" "$body"; then
      write_state "$state" "$now" "$now"
    else
      # Mail failed: leave state unchanged so the NEXT run retries the alert
      # instead of recording a page that never went out.
      log "mail send FAILED — alert-state left unchanged so the next run retries"
    fi
  elif [ $(( now - LAST_ALERT )) -ge "$REMINDER_SECS" ]; then
    local since_min=$(( (now - STATE_ENTERED) / 60 ))
    log "reminder: '$state' persists (${since_min}m; last alert $(( now - LAST_ALERT ))s ago)"
    if alert "REMINDER (${since_min}m in state '$state'): $subject" "$body"; then
      write_state "$state" "$now" "$STATE_ENTERED"
    fi
  else
    log "suppressed repeat '$state' alert — state unchanged, last alert $(( now - LAST_ALERT ))s ago (< ${REMINDER_SECS}s reminder cadence). Decision/actions unaffected."
  fi
}

# report_ok — on a bad->ok transition send ONE clear notice; without it the only
# way to learn a condition ended is noticing the alerts stopped, which looks
# identical to the watchdog itself dying. ok->ok and first-ever-run stay silent.
report_ok() {
  local now; now=$(date -u +%s)
  read_state
  if [ -n "$LAST_STATE" ] && [ "$LAST_STATE" != "ok" ]; then
    local dur=""
    [ "$STATE_ENTERED" -gt 0 ] && dur=" after $(( (now - STATE_ENTERED) / 60 ))m"
    log "state transition: '$LAST_STATE' -> 'ok' — sending clear notice"
    if alert "CLEARED: bridget gateway '$LAST_STATE' resolved$dur — socket carrying traffic again" \
      "bridget-gateway-watchdog: the '$LAST_STATE' condition it alerted on has resolved$dur. The gateway heartbeat at $HEARTBEAT is fresh again AND carries a finite HEARTBEAT_ACK latency, which means frames are provably arriving down the live Discord websocket — the bot is reachable.

No action needed. This notice exists so a stopped alert stream is distinguishable from the watchdog itself dying."; then
      write_state ok "$now" "$now"
    fi
  elif [ -z "$LAST_STATE" ]; then
    write_state ok 0 "$now"   # first run / recovered-from-corruption: record quietly
  fi
}

# gateway_live — the one liveness predicate, so no branch can invent its own.
# Requires BOTH a fresh stamp AND a finite positive latency. Sets HB_AGE and
# HB_LAT for the caller's log lines.
HB_AGE=""; HB_LAT=""
gateway_live() {
  HB_AGE=$(heartbeat_age)
  HB_LAT=$(heartbeat_latency)
  [ -n "$HB_AGE" ] || return 1
  [ "$HB_AGE" -le "$STALE_SECS" ] || return 1
  [ -n "$HB_LAT" ] || return 1
  return 0
}

# 1. HOST SLEEP / RECENT WAKE. A stale gateway heartbeat right after a wake is
#    EXPECTED, not a fault: nothing was running to stamp it, and discord.py's
#    own reconnect is already in flight. Kickstarting here would cycle a bridge
#    that is about to come back by itself.
#
#    The skip covers the ALERT side only. A FRESH stamp with a finite latency is
#    positive proof frames are arriving right now, which is valid regardless of
#    the host's sleep history — so an alert state left standing from before the
#    sleep is allowed to CLEAR here. Without that the state machine pins on
#    'dead' across a laptop's constant DarkWakes and the NEXT genuine death
#    produces no fresh transition at all, i.e. the alarm masks itself.
WAKES=$(${=WAKE_CMD} 2>/dev/null | grep -c '"event_type"')
if [ "${WAKES:-0}" -gt 0 ]; then
  if gateway_live; then
    log "ok skip — host woke within 20m ($WAKES system_wake event(s)); discord.py owns the reconnect. Gateway heartbeat age=${HB_AGE}s (<= ${STALE_SECS}s) with latency=${HB_LAT}s is proof of traffic, so the alert state is still allowed to clear here."
    report_ok
  else
    log "ok skip — host woke within 20m ($WAKES system_wake event(s)); discord.py owns the reconnect. Gateway heartbeat age=${HB_AGE:-unreadable}s latency=${HB_LAT:-none} is NOT proof of liveness, but a sleep explains that — no verdict either way, alert state left as it stands."
  fi
  exit 0
fi

# 2. No heartbeat at all. A kickstart cannot fix a bridge whose build has no
#    stamping code in it, so cycling would re-run the same binary and loop
#    forever. Alert instead — that needs a human (or a deploy).
if [ ! -f "$HEARTBEAT" ]; then
  log "NO HEARTBEAT at $HEARTBEAT — the running bridget may predate the mg-c1d5 gateway stamp, or it cannot write there. NOT kickstarting (a cycle would not fix it)."
  report_bad no-heartbeat \
    "bridget: no gateway heartbeat — channel liveness is UNVERIFIABLE" \
    "bridget-gateway-watchdog found no heartbeat file at $HEARTBEAT.

That means the running bridge is not stamping proof-of-life for its gateway socket: either it is a build that predates mg-c1d5 (needs a deploy — git pull + 'launchctl kickstart -k gui/$(id -u)/$SERVICE' — and remember the PID must actually change), or it cannot write to that path.

NOT auto-kickstarting: if the running build has no stamp in it, cycling changes nothing and would repeat every 5 minutes.

Discord channel liveness is currently UNVERIFIABLE — the same blind spot as the 2026-08-17 outage, where the bot sat OFFLINE for an hour while launchd reported it perfectly healthy."
  exit 1
fi

NOW_TS=$(date -u +%s)

# 3. Fresh stamp AND a finite round trip. Frames are provably arriving down the
#    websocket. Quiet or busy, the channel is alive — this is the branch that
#    makes "nobody messaged the bot today" distinguishable from "the bot is
#    dead", which is exactly what launchctl could not do.
if gateway_live; then
  log "ok — gateway heartbeat age=${HB_AGE}s (<= ${STALE_SECS}s), HEARTBEAT_ACK latency=${HB_LAT}s; socket provably carrying traffic"
  report_ok
  exit 0
fi

AGE="$HB_AGE"
LAT="$HB_LAT"
if [ -z "$AGE" ]; then
  # File exists but its mtime is unreadable. Not a verdict either way.
  log "ok — heartbeat unreadable at $HEARTBEAT; NOT kickstarting"
  exit 0
fi
AGE_MIN=$(( AGE / 60 ))

# Which half of the liveness predicate failed, in one sentence, so the mails
# below say the true thing instead of a blanket "stale".
if [ "$AGE" -gt "$STALE_SECS" ]; then
  FAULT_KIND="stale"
  FAULT_EVIDENCE="The stamp is ${AGE}s old (> ${STALE_SECS}s). Only a frame arriving down the gateway websocket can freshen it, so no frame has arrived in that window — not merely that nobody messaged the bot."
else
  FAULT_KIND="no-ack"
  FAULT_EVIDENCE="The stamp is only ${AGE}s old, so frames ARE arriving, but its latency_s is ${LAT:-null} — no HEARTBEAT -> HEARTBEAT_ACK round trip has been measured. That is a half-open gateway, and a null latency is explicitly NOT liveness (reading one as healthy is how the robin heartbeat produced a false positive)."
fi

# 4. HOST DOWNTIME, not a dead gateway. The heartbeat ages on the WALL clock but
#    bridget only stamps it while the host is running, so ask how long the host
#    has actually been up before accusing anything. Suppress ONLY when both:
#      (a) the heartbeat predates this boot (age >= uptime, within slop) — the
#          whole gap is accounted for by downtime; and
#      (b) uptime <= STALE_SECS + BOOT_GRACE_SECS — the bridge has not yet had
#          the threshold amount of AWAKE time, so its silence means nothing.
#    Fail (a) and it stamped during this boot and then stopped: real. Fail (b)
#    and the host has been awake past the threshold with nothing stamped: also
#    real, and that is the case that stops a bridge which never came back after
#    a reboot from hiding here forever. Unreadable boot time falls through too —
#    fail toward paging, never toward silence.
UPTIME=-1; UPTIME_MIN=-1; BOOT_AT=""
BOOT_EPOCH=$(boot_epoch)
# A boot time in the FUTURE by more than clock slop is not a reading, it is a
# misparse or a broken clock — and believing it would mean "uptime 0", the one
# value that silences this branch unconditionally. Discard it and page.
if [ -n "$BOOT_EPOCH" ] && [ "$BOOT_EPOCH" -gt $(( NOW_TS + BOOT_SLOP_SECS )) ]; then
  log "boot check: '$BOOT_CMD' reported a boot time in the future ($BOOT_EPOCH > now $NOW_TS) — discarding it rather than reading it as 'just booted'"
  BOOT_EPOCH=""
fi
if [ -n "$BOOT_EPOCH" ]; then
  UPTIME=$(( NOW_TS - BOOT_EPOCH ))
  [ "$UPTIME" -lt 0 ] && UPTIME=0     # small backwards NTP step; treat as just-booted
  UPTIME_MIN=$(( UPTIME / 60 ))
  BOOT_AT=$(date -u -r "$BOOT_EPOCH" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)
  if [ "$AGE" -ge $(( UPTIME - BOOT_SLOP_SECS )) ] && [ "$UPTIME" -le $(( STALE_SECS + BOOT_GRACE_SECS )) ]; then
    DOWN_MIN=$(( (AGE - UPTIME) / 60 ))
    [ "$DOWN_MIN" -lt 0 ] && DOWN_MIN=0
    log "ok skip — HOST DOWNTIME, not a fault: heartbeat age=${AGE_MIN}m but the host has only been up ${UPTIME_MIN}m (booted ${BOOT_AT:-?}). The host was down ~${DOWN_MIN}m and bridget has had <$(( (STALE_SECS + BOOT_GRACE_SECS) / 60 ))m of awake time to open a gateway and measure a round trip, so the staleness is fully explained by downtime. NOT kickstarting, NOT alerting. If it is still stale once uptime passes that window, the dead branch below fires for real."
    exit 0
  fi
  log "boot check: uptime=${UPTIME_MIN}m (booted ${BOOT_AT:-?}), heartbeat age=${AGE_MIN}m — host downtime does NOT explain this; continuing"
else
  log "boot check: could not read host boot time via '$BOOT_CMD' — cannot rule out host downtime, so continuing to the fault branches (fail toward paging, never toward silence)"
fi

# 5. THE BRIDGE ITSELF JUST STARTED. Same argument one level in: a bridge that
#    started 20s ago has not had time to open a gateway, receive a frame and
#    measure a HEARTBEAT_ACK, so its silence is not evidence. Any restart from
#    any cause — a deploy, a manual kickstart, mayor healing something else —
#    landing shortly before a tick would otherwise produce a false alarm, and
#    then a kickstart that resets the clock and does it again.
#
#    Deliberately NOT symmetric with signal 4's condition (a): the bridge is
#    supposed to stamp within its first minute, so "started recently" alone is
#    enough to withhold a verdict. The cost is that a genuinely crash-looping
#    bridge stays inside this window forever — but a crash loop is visible to
#    launchd (non-zero exit codes, restart counts), which is precisely the class
#    of failure launchd CAN see, so it is not this watchdog's blind spot. The
#    skip is logged loudly every tick so it is greppable if it ever persists.
SVC_UP=$(service_uptime)
if [ -n "$SVC_UP" ] && [ "$SVC_UP" -le $(( STALE_SECS + SERVICE_GRACE_SECS )) ]; then
  log "ok skip — SERVICE JUST STARTED: $SERVICE has been running ${SVC_UP}s (<= $(( STALE_SECS + SERVICE_GRACE_SECS ))s) and the heartbeat is ${AGE}s old ($FAULT_KIND). Too early for silence to be evidence; NOT kickstarting, NOT alerting. If this repeats every tick the bridge is crash-looping — check 'launchctl print gui/$(id -u)/$SERVICE' for a non-zero last exit code."
  exit 0
fi
if [ -n "$SVC_UP" ]; then
  SERVICE_EVIDENCE="The bridge process has been running ${SVC_UP}s, so this is not a just-restarted false positive — that cross-check ran and cleared."
else
  SERVICE_EVIDENCE="Bridge process uptime could NOT be read, so the just-restarted cross-check did not run. If the service was restarted in the last few minutes, this alert is that false positive and not a fault."
fi

# 6. Not fresh-with-a-round-trip, not a wake, not host downtime, not a fresh
#    start. The gateway is dead while the process is fine — the exact 2026-08-17
#    state. Heal it.
if [ "$UPTIME" -ge 0 ]; then
  AWAKE_EVIDENCE="The host has been up ${UPTIME_MIN}m (booted ${BOOT_AT:-?}), so this is not the overnight-shutdown false positive — that cross-check ran and cleared."
else
  AWAKE_EVIDENCE="Host uptime could NOT be read ('$BOOT_CMD' gave no answer), so the host-downtime cross-check did not run. Check 'sysctl -n kern.boottime' first: if the host booted recently, this alert is that false positive and not a fault."
fi

log "DEAD ($FAULT_KIND) — gateway heartbeat age=${AGE}s latency=${LAT:-null} with uptime=${UPTIME_MIN}m, service up ${SVC_UP:-unknown}s. Checking cooldown."

NOW=$(date -u +%s)
LAST=0; [ -f "$COOLDOWN" ] && LAST=$(cat "$COOLDOWN" 2>/dev/null || echo 0)
case "$LAST" in (*[!0-9]*|"") LAST=0 ;; esac

if [ $((NOW - LAST)) -lt "$COOLDOWN_SECS" ]; then
  log "within cooldown ($((NOW-LAST))s < ${COOLDOWN_SECS}s) — NOT kickstarting again"
  report_bad dead \
    "ALERT: bridget gateway dead AGAIN within cooldown — Clover cannot reach the crew" \
    "bridget's Discord gateway is dead again but an auto-kickstart already fired <$(( COOLDOWN_SECS / 60 ))min ago. A freshly restarted bridge is losing its gateway immediately — this is deeper than a wedged socket.

EVIDENCE: $FAULT_EVIDENCE

$AWAKE_EVIDENCE
$SERVICE_EVIDENCE

The bot will read OFFLINE in Discord and Clover cannot send to mayor through it. The bridge is also the inject server and the claim announcer. Please look — and do NOT 'launchctl bootout' it, that takes all of those down and it does not come back on its own."
  exit 1
fi

if [ "$DRY_RUN" -eq 1 ]; then
  log "DRY-RUN would kickstart $SERVICE (gateway $FAULT_KIND, heartbeat ${AGE}s, latency ${LAT:-null})"
  exit 0
fi

echo "$NOW" > "$COOLDOWN"
log "kickstarting $SERVICE (gateway $FAULT_KIND, heartbeat ${AGE}s old, latency ${LAT:-null})"
# kickstart -k ONLY. Never bootout: this service is also the inject server,
# Clover's DM channel and the claim announcer, and a booted-out one does not
# come back on its own.
${=KICKSTART_CMD} kickstart -k "gui/$(id -u)/$SERVICE" >> "$LOG" 2>&1
KICK_RC=$?
log "kickstart exit=$KICK_RC"

# State 'dead' covers BOTH the kickstart and the within-cooldown branch: they
# are one episode, so the first detection pages and repeat kickstarts within the
# same episode act silently (the hourly reminder covers persistence).
report_bad dead \
  "bridget gateway was DEAD — auto-kickstarted (watchdog)" \
  "bridget-gateway-watchdog confirmed the Discord gateway websocket had stopped carrying traffic and kickstarted the bridge.

EVIDENCE (not a guess): $FAULT_EVIDENCE

Heartbeat file: $HEARTBEAT
$AWAKE_EVIDENCE
$SERVICE_EVIDENCE

launchctl kickstart -k gui/$(id -u)/$SERVICE exit=$KICK_RC.

WHY THIS MATTERS: the process stays perfectly healthy through this failure — on 2026-08-17 launchd reported state=running, keepalive, pid=770, 'last exit code = (never exited)' while the bot read OFFLINE to Clover for an hour and only recovered by accident when mayor happened to send something. PROCESS ALIVE IS NOT CHANNEL ALIVE; this heartbeat is the only signal that can tell them apart.

Please re-verify end to end by round-tripping an actual message through the bot — an empty, quiet channel is not proof."
exit 0
