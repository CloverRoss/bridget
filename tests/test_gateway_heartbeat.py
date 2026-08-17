"""Tests for the gateway channel-liveness heartbeat (mg-c1d5).

On 2026-08-17 the bot read OFFLINE in Discord while every process-level
signal said fine (`launchctl print` reported state = running, keepalive,
pid = 770, "last exit code = (never exited)"). The gateway websocket
underneath had gone stale across a host sleep, and it only recovered by
accident when mayor happened to send an outbound message an hour later.

PROCESS ALIVE IS NOT CHANNEL ALIVE. bridget now stamps a heartbeat file
that only a genuinely live gateway can freshen, and these tests pin the
two properties that make it evidence rather than noise:

- the write is driven by `on_socket_raw_receive`, i.e. by a frame that
  actually arrived down the websocket — never by a timer, because the
  process and its timers stay perfectly healthy through this failure;
- a stamp carries `latency_s`, discord.py's measured HEARTBEAT ->
  HEARTBEAT_ACK round trip, and a nan/inf measurement surfaces as null
  rather than as a number. The watchdog refuses to read a null as
  liveness — the robin heartbeat once emitted a startup stamp with
  rtt_s: null and it was read as healthy while the channel was not.
"""
import asyncio
import importlib.util
import json
import os
import re
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / 'bridget'


def _load_bridget(home: Path):
    os.environ['HOME'] = str(home)
    env_dir = home / '.pogo'
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / 'bridget.env').write_text(
        'DISCORD_BOT_TOKEN=fake-token-for-tests\n'
        'DISCORD_USER_ID=1\n'
        'DISCORD_SERVER_ID=2\n'
    )
    loader = SourceFileLoader('bridget', str(SCRIPT))
    spec = importlib.util.spec_from_loader('bridget', loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def bridget(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    mod = _load_bridget(tmp_path)
    mod.GATEWAY_HEARTBEAT_FILE = tmp_path / '.pogo' / 'bridget-gateway.heartbeat'
    mod._gateway_hb_last_write = None
    mod._gateway_hb_frames = 0
    mod._gateway_hb_latency_seen = False
    mod._gateway_hb_skipped_no_latency = 0
    return mod


def _fake_client(bridget, monkeypatch, latency):
    client = MagicMock()
    client.latency = latency
    monkeypatch.setattr(bridget, 'client', client)
    return client


def _stamp(bridget):
    return json.loads(bridget.GATEWAY_HEARTBEAT_FILE.read_text())


# --- what actually drives the write ------------------------------------------

def test_socket_receive_stamps_with_finite_latency(bridget, monkeypatch):
    _fake_client(bridget, monkeypatch, 0.0512)
    asyncio.run(bridget.on_socket_raw_receive('{"op":11}'))
    stamp = _stamp(bridget)
    assert stamp['latency_s'] == 0.0512
    assert stamp['reason'] == 'socket_receive'
    assert stamp['pid'] == os.getpid()
    assert stamp['frames'] == 1
    assert stamp['stamped_at'].endswith('Z')
    assert stamp['nonce']


def test_frames_counter_tracks_inbound_traffic(bridget, monkeypatch):
    _fake_client(bridget, monkeypatch, 0.02)
    for _ in range(3):
        asyncio.run(bridget.on_socket_raw_receive('{"op":11}'))
    # Only the first write lands (rate limit), but every frame is counted —
    # the counter is the evidence that inbound traffic is still arriving.
    assert bridget._gateway_hb_frames == 3


def test_the_only_caller_is_the_socket_event(bridget):
    """A timer-driven stamp would report a dead gateway as alive forever.

    The process, its event loop and its timers all stay healthy through
    this failure, so the stamp must be reachable ONLY from evidence that
    the socket carried a frame. This guards the design constraint against
    a future 'while True: stamp; sleep(30)' watcher being added.
    """
    src = SCRIPT.read_text()
    calls = [
        line.strip() for line in src.splitlines()
        if '_stamp_gateway_heartbeat(' in line
        and not line.strip().startswith('def ')
    ]
    assert len(calls) == 1, f'unexpected stamp call sites: {calls}'
    # ...and it is inside the socket-receive handler.
    handler = src.split('async def on_socket_raw_receive')[1].split('\n@client.event')[0]
    assert '_stamp_gateway_heartbeat(' in handler


def test_debug_events_are_enabled_on_the_client(bridget):
    """Without enable_debug_events discord.py's log_receive is a no-op stub
    and on_socket_raw_receive never dispatches — the heartbeat would sit
    frozen at its first value and every gateway death would go unnoticed."""
    assert bridget.client._enable_debug_events is True


# --- the null-latency rule ----------------------------------------------------

@pytest.mark.parametrize('latency', [float('nan'), float('inf'), 0.0, -1.0])
def test_unmeasurable_latency_stamps_null_not_a_number(bridget, monkeypatch, latency):
    # nan = no websocket at all; inf = a websocket with no ACK back yet.
    # Both mean "no round trip has been measured" and must not masquerade
    # as a measurement.
    _fake_client(bridget, monkeypatch, latency)
    asyncio.run(bridget.on_socket_raw_receive('{"op":10}'))
    assert _stamp(bridget)['latency_s'] is None


def test_missing_latency_attribute_stamps_null(bridget, monkeypatch):
    client = MagicMock()
    del client.latency
    monkeypatch.setattr(bridget, 'client', client)
    asyncio.run(bridget.on_socket_raw_receive('{"op":10}'))
    assert _stamp(bridget)['latency_s'] is None


def test_null_latency_never_clobbers_a_measured_stamp(bridget, monkeypatch):
    """Losing the measurement means the gateway went away, and the correct
    signal for that is the file AGEING OUT — not a fresh stamp the watchdog
    then has to argue with."""
    client = _fake_client(bridget, monkeypatch, 0.031)
    asyncio.run(bridget.on_socket_raw_receive('{"op":11}'))
    assert _stamp(bridget)['latency_s'] == 0.031

    bridget.GATEWAY_HEARTBEAT_MIN_INTERVAL = 0   # take the rate limit out of it
    client.latency = float('nan')
    asyncio.run(bridget.on_socket_raw_receive('{"op":0}'))
    assert _stamp(bridget)['latency_s'] == 0.031   # untouched
    assert bridget._gateway_hb_skipped_no_latency == 1


def test_null_latency_is_stamped_before_the_first_measurement(bridget, monkeypatch):
    """The ~41s between connect and the first HEARTBEAT_ACK still deserves a
    stamp: it records the pid and the connect. The watchdog already refuses
    to read it as liveness, so it is diagnostics, not a claim."""
    _fake_client(bridget, monkeypatch, float('inf'))
    asyncio.run(bridget.on_socket_raw_receive('{"op":10}'))
    stamp = _stamp(bridget)
    assert stamp['latency_s'] is None
    assert stamp['pid'] == os.getpid()


# --- mechanics ----------------------------------------------------------------

def test_write_is_rate_limited(bridget, monkeypatch):
    _fake_client(bridget, monkeypatch, 0.01)
    assert bridget._stamp_gateway_heartbeat('socket_receive') is True
    first = _stamp(bridget)
    assert bridget._stamp_gateway_heartbeat('socket_receive') is False
    assert _stamp(bridget)['nonce'] == first['nonce']


def test_force_bypasses_the_rate_limit(bridget, monkeypatch):
    _fake_client(bridget, monkeypatch, 0.01)
    assert bridget._stamp_gateway_heartbeat('socket_receive') is True
    first = _stamp(bridget)
    assert bridget._stamp_gateway_heartbeat('socket_receive', force=True) is True
    assert _stamp(bridget)['nonce'] != first['nonce']


def test_stamp_write_failure_is_swallowed(bridget, monkeypatch, tmp_path):
    # A heartbeat that raises would take the gateway event handler down with
    # it, turning a diagnostic into an outage.
    _fake_client(bridget, monkeypatch, 0.01)
    bridget.GATEWAY_HEARTBEAT_FILE = tmp_path / 'nope' / 'x' / 'hb'
    monkeypatch.setattr(
        bridget.Path, 'mkdir',
        lambda *a, **k: (_ for _ in ()).throw(OSError('read-only')),
    )
    assert bridget._stamp_gateway_heartbeat('socket_receive') is False


def test_stamp_is_written_atomically(bridget, monkeypatch):
    """The watchdog polls this file on a timer and must never read half a
    line, so the write is tmp + os.replace."""
    _fake_client(bridget, monkeypatch, 0.01)
    seen = {}
    real_replace = os.replace

    def spy(src, dst):
        seen['src'] = str(src)
        seen['dst'] = str(dst)
        return real_replace(src, dst)

    monkeypatch.setattr(bridget.os, 'replace', spy)
    bridget._stamp_gateway_heartbeat('socket_receive')
    assert seen['src'].endswith('.tmp')
    assert seen['dst'] == str(bridget.GATEWAY_HEARTBEAT_FILE)
    assert not Path(seen['src']).exists()


def test_heartbeat_path_is_overridable(tmp_path, monkeypatch):
    target = tmp_path / 'elsewhere.heartbeat'
    monkeypatch.setenv('POGO_BRIDGET_GATEWAY_HEARTBEAT', str(target))
    mod = _load_bridget(tmp_path)
    assert mod.GATEWAY_HEARTBEAT_FILE == target


def test_default_heartbeat_path_matches_the_watchdog_default(bridget):
    """Both sides hardcode this path; a drift between them is a watchdog
    that watches nothing and reports 'no heartbeat' forever."""
    assert bridget.GATEWAY_HEARTBEAT_FILE.name == 'bridget-gateway.heartbeat'
    watchdog = (REPO / 'bin' / 'bridget-gateway-watchdog.sh').read_text()
    assert 'HOME/.pogo/bridget-gateway.heartbeat' in watchdog


def test_stamp_json_is_single_line_and_watchdog_parseable(bridget, monkeypatch):
    """The watchdog reads latency_s with sed, not jq (it runs from launchd
    with a minimal PATH). That only works while the writer emits compact,
    one-line JSON."""
    _fake_client(bridget, monkeypatch, 0.0512)
    bridget._stamp_gateway_heartbeat('socket_receive')
    text = bridget.GATEWAY_HEARTBEAT_FILE.read_text()
    assert len(text.strip().splitlines()) == 1
    m = re.search(r'"latency_s"\s*:\s*([^,}\s]*)', text)
    assert m and m.group(1) == '0.0512'
