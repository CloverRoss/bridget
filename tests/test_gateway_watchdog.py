"""Branch tests for bin/bridget-gateway-watchdog.sh (mg-c1d5).

THE POINT: prove BOTH sides of the decision — heal / do-not-heal — without
ever cycling the real bridge, which is Clover's only DM channel to the crew,
the inject server and the claim announcer. Every case redirects the heartbeat,
log, cooldown stamp and alert state into a throwaway tmpdir via the
BRIDGET_GATEWAY_WATCHDOG_* overrides, and stubs `launchctl`, `pogo events` and
`mg` with recorders. The kickstart "fires" into a file.

The cases that matter most:

  - a fresh stamp with a finite latency must NOT heal. That is the whole
    thesis: "nobody messaged the bot today" is now distinguishable from
    "the gateway is dead", which is exactly what launchctl could not do.
  - a stale stamp MUST heal and MUST mail, with evidence.
  - a stamp whose latency_s is null must NOT count as liveness even when it
    is fresh. Reading a null as healthy is the false positive that burned
    the robin heartbeat.
  - a stale stamp right after a host wake must NOT heal and must NOT page —
    but must still be able to CLEAR a stuck alert state when the heartbeat
    is fresh, or the state machine pins on 'dead' across a laptop's
    DarkWakes and masks the next genuine death.
  - an overnight shutdown, and a bridge that only just started, must not
    fire either. A false alarm at 3am is its own bug.

TEST_BRIDGET_GATEWAY_WATCHDOG_SCRIPT points the suite at another build of
the watchdog, so a new test can be shown to FAIL against the old one.
"""
import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = Path(os.environ.get(
    'TEST_BRIDGET_GATEWAY_WATCHDOG_SCRIPT',
    str(REPO / 'bin' / 'bridget-gateway-watchdog.sh'),
))

pytestmark = pytest.mark.skipif(
    shutil.which('zsh') is None, reason='watchdog is a zsh script'
)

FRESH_ENOUGH = 60          # < the 300s stale threshold
LONG_STALE = 3600          # comfortably past it
A_MONTH = 30 * 24 * 3600


def _exe(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class Run:
    def __init__(self, rc, kicked, mailed, log, state):
        self.rc = rc
        self.kicked = kicked      # list of recorded launchctl invocations
        self.mailed = mailed      # list of recorded mg invocations
        self.log = log            # watchdog log text
        self.state = state        # alert-state file text, or None

    @property
    def state_name(self):
        return self.state.split()[0] if self.state else None


@pytest.fixture
def sandbox(tmp_path):
    """A watchdog sandbox: every external effect lands in tmp_path."""
    d = tmp_path
    _exe(d / 'fake-launchctl', '#!/bin/sh\necho "$*" >> "$FAKE_KICK"\n')
    # Mail bodies are multi-line, so records are separated by \x1e (RS) rather
    # than by newline — otherwise "how many mails went out?" counts paragraphs.
    _exe(d / 'fake-mg', '#!/bin/sh\nprintf \'%s\\036\' "$*" >> "$FAKE_MAIL"\n')

    class Sandbox:
        dir = d
        heartbeat = d / 'bridget-gateway.heartbeat'

        def write_heartbeat(self, age_secs=0, latency=0.0512, raw=None):
            if raw is None:
                raw = json.dumps({
                    'stamped_at': '2026-08-17T06:00:00Z',
                    'reason': 'socket_receive',
                    'pid': 770,
                    'nonce': 'deadbeefcafe',
                    'latency_s': latency,
                    'frames': 4211,
                }) + '\n'
            self.heartbeat.write_text(raw)
            when = time.time() - age_secs
            os.utime(self.heartbeat, (when, when))

        def set_state(self, name, entered_secs_ago=600, alerted_secs_ago=600):
            now = int(time.time())
            (d / 'alert-state').write_text(
                f'{name} {now - alerted_secs_ago} {now - entered_secs_ago}\n'
            )

        def set_cooldown(self, secs_ago):
            (d / 'cooldown').write_text(str(int(time.time()) - secs_ago))

        def run(self, *args, wakes=0, uptime=A_MONTH, service_uptime=A_MONTH,
                boot_cmd=None, **env_extra):
            env = dict(os.environ)
            env['FAKE_KICK'] = str(d / 'kicked')
            env['FAKE_MAIL'] = str(d / 'mailed')
            wake_json = '\n'.join(
                '{"event_type":"system_wake"}' for _ in range(wakes)
            )
            _exe(d / 'fake-wake', f'#!/bin/sh\ncat <<\'EOF\'\n{wake_json}\nEOF\n')
            env.update({
                'BRIDGET_GATEWAY_WATCHDOG_LOG': str(d / 'watchdog.log'),
                'BRIDGET_GATEWAY_WATCHDOG_HEARTBEAT': str(self.heartbeat),
                'BRIDGET_GATEWAY_WATCHDOG_COOLDOWN_FILE': str(d / 'cooldown'),
                'BRIDGET_GATEWAY_WATCHDOG_STATE_FILE': str(d / 'alert-state'),
                'BRIDGET_GATEWAY_WATCHDOG_KICKSTART_CMD': str(d / 'fake-launchctl'),
                'BRIDGET_GATEWAY_WATCHDOG_MAIL_CMD': str(d / 'fake-mg'),
                'BRIDGET_GATEWAY_WATCHDOG_WAKE_CMD': str(d / 'fake-wake'),
                # Every case stubs boot time. The real one answers about the
                # machine running the suite — on a laptop that was just
                # switched on (exactly the state the ticket is about) it would
                # silence the fault cases and quietly gut the suite.
                'BRIDGET_GATEWAY_WATCHDOG_BOOT_CMD': (
                    boot_cmd if boot_cmd is not None
                    else f'echo {int(time.time()) - uptime}'
                ),
                'BRIDGET_GATEWAY_WATCHDOG_SERVICE_UPTIME_CMD': (
                    'true' if service_uptime is None else f'echo {service_uptime}'
                ),
            })
            env.update(env_extra)
            proc = subprocess.run(
                ['zsh', str(SCRIPT), *args],
                env=env, capture_output=True, text=True,
            )
            def _lines(p):
                return p.read_text().splitlines() if p.exists() else []

            def _records(p):
                if not p.exists():
                    return []
                return [r for r in p.read_text().split('\x1e') if r.strip()]
            state_file = d / 'alert-state'
            return Run(
                proc.returncode,
                _lines(d / 'kicked'),
                _records(d / 'mailed'),
                (d / 'watchdog.log').read_text() if (d / 'watchdog.log').exists() else '',
                state_file.read_text().strip() if state_file.exists() else None,
            )

    return Sandbox()


# --- the healthy path must stay quiet ----------------------------------------

def test_fresh_stamp_with_finite_latency_does_not_heal(sandbox):
    sandbox.write_heartbeat(age_secs=FRESH_ENOUGH, latency=0.0512)
    r = sandbox.run()
    assert r.rc == 0
    assert r.kicked == []
    assert r.mailed == []
    assert 'socket provably carrying traffic' in r.log
    assert r.state_name == 'ok'


def test_a_quiet_channel_is_not_a_dead_one(sandbox):
    """No DMs for hours is normal. Only the socket going silent is a fault,
    and the heartbeat is what separates the two."""
    sandbox.write_heartbeat(age_secs=250, latency=1.9)
    r = sandbox.run()
    assert r.kicked == []
    assert r.mailed == []


def test_stamp_right_on_the_threshold_is_still_ok(sandbox):
    sandbox.write_heartbeat(age_secs=295, latency=0.04)
    r = sandbox.run()
    assert r.kicked == []


# --- the fault path must fire, with evidence ---------------------------------

def test_stale_stamp_heals_and_mails(sandbox):
    sandbox.write_heartbeat(age_secs=LONG_STALE, latency=0.0512)
    r = sandbox.run()
    assert r.rc == 0
    assert r.kicked == ['kickstart -k gui/%d/com.pogo.discord-bridge' % os.getuid()]
    assert len(r.mailed) == 1
    assert 'was DEAD' in r.mailed[0]
    assert r.state_name == 'dead'


def test_heal_is_kickstart_never_bootout(sandbox):
    """bootout takes the inject server, the DM channel and the claim
    announcer down and it does not come back on its own."""
    sandbox.write_heartbeat(age_secs=LONG_STALE)
    r = sandbox.run()
    assert r.kicked and all(k.startswith('kickstart -k') for k in r.kicked)
    # ...and no other branch reaches launchctl either: the ONLY invocation of
    # the kickstart command in the whole script is the guarded one.
    invocations = [
        line.strip() for line in SCRIPT.read_text().splitlines()
        if '${=KICKSTART_CMD}' in line
    ]
    assert invocations == ['${=KICKSTART_CMD} kickstart -k "gui/$(id -u)/$SERVICE" >> "$LOG" 2>&1']


def test_alert_carries_evidence_not_just_a_restart_notice(sandbox):
    sandbox.write_heartbeat(age_secs=LONG_STALE, latency=0.0512)
    body = sandbox.run().mailed[0]
    assert 'EVIDENCE' in body
    assert str(sandbox.heartbeat) in body
    assert 'gateway websocket' in body


# --- the null-latency rule ----------------------------------------------------

def test_fresh_stamp_with_null_latency_is_not_liveness(sandbox):
    """The robin heartbeat once emitted a startup stamp with rtt_s: null and
    the watchdog read it as healthy while the channel was not. A null here
    means 'no round trip has been measured', full stop."""
    sandbox.write_heartbeat(age_secs=FRESH_ENOUGH, latency=None)
    r = sandbox.run()
    assert r.kicked, 'a null-latency stamp must not pass as liveness'
    assert 'no-ack' in r.log
    assert 'null' in r.mailed[0]


@pytest.mark.parametrize('raw_latency', ['null', 'NaN', 'Infinity', '"0.05"', '0'])
def test_non_numeric_latencies_are_not_liveness(sandbox, raw_latency):
    sandbox.write_heartbeat(
        age_secs=FRESH_ENOUGH,
        raw='{"stamped_at":"x","reason":"socket_receive","pid":770,'
            '"nonce":"n","latency_s":%s,"frames":1}\n' % raw_latency,
    )
    assert sandbox.run().kicked, f'latency_s: {raw_latency} must not read as live'


def test_absent_latency_field_is_not_liveness(sandbox):
    sandbox.write_heartbeat(
        age_secs=FRESH_ENOUGH,
        raw='{"stamped_at":"x","reason":"socket_receive","pid":770}\n',
    )
    assert sandbox.run().kicked


# --- false-positive suppression ----------------------------------------------

def test_recent_wake_suppresses_the_heal(sandbox):
    """A stale heartbeat right after a wake is EXPECTED, not a fault:
    nothing was running to stamp it and discord.py's own reconnect is
    already in flight."""
    sandbox.write_heartbeat(age_secs=LONG_STALE)
    r = sandbox.run(wakes=1)
    assert r.rc == 0
    assert r.kicked == []
    assert r.mailed == []
    assert 'host woke within 20m' in r.log


def test_recent_wake_leaves_the_alert_state_alone(sandbox):
    """A stale stamp inside a wake window is ambiguous — no verdict either
    way, so it must neither page nor clear."""
    sandbox.write_heartbeat(age_secs=LONG_STALE)
    sandbox.set_state('dead')
    r = sandbox.run(wakes=1)
    assert r.state_name == 'dead'
    assert r.mailed == []


def test_recent_wake_still_clears_a_stuck_alert_on_positive_proof(sandbox):
    """The suppression must not swallow the CLEAR side. This host DarkWakes
    every few minutes, so if the wake branch could never reach report_ok the
    state would pin on 'dead' forever — and because alerts fire on
    TRANSITION, the next genuine death would produce no page at all. A fresh
    stamp with a finite latency is proof of traffic whatever the sleep log
    says, so it is allowed to clear here."""
    sandbox.write_heartbeat(age_secs=FRESH_ENOUGH, latency=0.0512)
    sandbox.set_state('dead')
    r = sandbox.run(wakes=1)
    assert r.kicked == []
    assert r.state_name == 'ok'
    assert len(r.mailed) == 1
    assert 'CLEARED' in r.mailed[0]


def test_overnight_shutdown_does_not_fire(sandbox):
    """The heartbeat ages on the WALL clock but bridget only stamps it while
    the host is running. A host that was switched off overnight explains the
    whole gap; alerting on it trains the reader to skim the one alarm that
    must be trusted."""
    sandbox.write_heartbeat(age_secs=21 * 3600)
    r = sandbox.run(uptime=360)
    assert r.rc == 0
    assert r.kicked == []
    assert r.mailed == []
    assert 'HOST DOWNTIME' in r.log


def test_host_up_past_the_window_still_fires(sandbox):
    """Fail the uptime half of the downtime cross-check and it is a real
    fault again — this is what stops a bridge that never came back after a
    reboot from hiding in the downtime branch forever."""
    sandbox.write_heartbeat(age_secs=21 * 3600)
    r = sandbox.run(uptime=4 * 3600)
    assert r.kicked


def test_heartbeat_stamped_during_this_boot_still_fires(sandbox):
    """Fail the other half — the stamp postdates boot — and the gap is not
    downtime, it is a gateway that stamped and then stopped."""
    sandbox.write_heartbeat(age_secs=400)
    r = sandbox.run(uptime=580)
    assert r.kicked


def test_unreadable_boot_time_fails_toward_paging(sandbox):
    sandbox.write_heartbeat(age_secs=LONG_STALE)
    r = sandbox.run(boot_cmd='false')
    assert r.kicked
    assert 'could not read host boot time' in r.log
    assert 'could NOT be read' in r.mailed[0]


def test_future_boot_time_is_discarded_not_believed(sandbox):
    """A boot time in the future is a misparse or a broken clock. Believing
    it means uptime 0, the one value that silences the downtime branch
    unconditionally."""
    sandbox.write_heartbeat(age_secs=LONG_STALE)
    r = sandbox.run(boot_cmd=f'echo {int(time.time()) + 99999}')
    assert r.kicked
    assert 'boot time in the future' in r.log


def test_just_restarted_bridge_does_not_fire(sandbox):
    """A bridge that started 20s ago has not had time to open a gateway,
    receive a frame and measure a round trip. Without this, any restart
    landing shortly before a tick produces a false alarm — and then a
    kickstart that resets the clock and does it again."""
    sandbox.write_heartbeat(age_secs=LONG_STALE)
    r = sandbox.run(service_uptime=20)
    assert r.rc == 0
    assert r.kicked == []
    assert r.mailed == []
    assert 'SERVICE JUST STARTED' in r.log


def test_long_running_bridge_is_not_excused(sandbox):
    sandbox.write_heartbeat(age_secs=LONG_STALE)
    r = sandbox.run(service_uptime=500)
    assert r.kicked


def test_unreadable_service_uptime_fails_toward_paging(sandbox):
    sandbox.write_heartbeat(age_secs=LONG_STALE)
    r = sandbox.run(service_uptime=None)
    assert r.kicked
    assert 'could NOT be read' in r.mailed[0]


# --- missing heartbeat, cooldown, cadence ------------------------------------

def test_missing_heartbeat_alerts_but_never_heals(sandbox):
    """A kickstart cannot fix a build with no stamping code in it — cycling
    re-runs the same binary and would loop every 5 minutes."""
    r = sandbox.run()
    assert r.rc == 1
    assert r.kicked == []
    assert len(r.mailed) == 1
    assert 'no gateway heartbeat' in r.mailed[0]
    assert r.state_name == 'no-heartbeat'


def test_cooldown_blocks_a_second_kickstart(sandbox):
    sandbox.write_heartbeat(age_secs=LONG_STALE)
    sandbox.set_cooldown(60)
    r = sandbox.run()
    assert r.rc == 1
    assert r.kicked == []
    assert 'AGAIN within cooldown' in r.mailed[0]


def test_expired_cooldown_allows_another_kickstart(sandbox):
    sandbox.write_heartbeat(age_secs=LONG_STALE)
    sandbox.set_cooldown(3600)
    assert sandbox.run().kicked


def test_repeat_dead_state_is_not_re_mailed(sandbox):
    """N identical mails for one unchanged condition train the reader to
    skim, which is how a genuinely new alert gets buried."""
    sandbox.write_heartbeat(age_secs=LONG_STALE)
    sandbox.set_state('dead', entered_secs_ago=60, alerted_secs_ago=60)
    r = sandbox.run()
    assert r.kicked, 'suppression gates the MAIL only, never the heal'
    assert r.mailed == []
    assert 'suppressed repeat' in r.log


def test_persisting_condition_still_reminds_hourly(sandbox):
    sandbox.write_heartbeat(age_secs=LONG_STALE)
    sandbox.set_state('dead', entered_secs_ago=7200, alerted_secs_ago=7200)
    r = sandbox.run()
    assert len(r.mailed) == 1
    assert 'REMINDER' in r.mailed[0]


def test_corrupt_alert_state_alerts_rather_than_eating_the_page(sandbox):
    sandbox.write_heartbeat(age_secs=LONG_STALE)
    (sandbox.dir / 'alert-state').write_text('\x00garbage not a state\n')
    r = sandbox.run()
    assert len(r.mailed) == 1
    assert r.state_name == 'dead'


def test_recovery_sends_one_clear_notice(sandbox):
    sandbox.write_heartbeat(age_secs=FRESH_ENOUGH, latency=0.03)
    sandbox.set_state('dead')
    r = sandbox.run()
    assert len(r.mailed) == 1
    assert 'CLEARED' in r.mailed[0]
    assert sandbox.run().mailed == r.mailed   # second ok run stays silent


# --- dry run ------------------------------------------------------------------

def test_dry_run_decides_but_never_acts(sandbox):
    sandbox.write_heartbeat(age_secs=LONG_STALE)
    r = sandbox.run('--dry-run')
    assert r.rc == 0
    assert r.kicked == []
    assert r.mailed == []
    assert 'DRY-RUN would kickstart' in r.log
    assert r.state is None, 'a dry run must not consume a real transition'


def test_unknown_argument_is_rejected(sandbox):
    sandbox.write_heartbeat(age_secs=FRESH_ENOUGH)
    assert sandbox.run('--wat').rc == 2


def test_script_is_executable():
    assert os.access(SCRIPT, os.X_OK)
