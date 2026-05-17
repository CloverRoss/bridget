"""Tests for the state-history layer in the `agents` view (ds-25d7).

Bridget tracks each crew agent's (state, label) across `agents` invocations
in a sidecar at ~/.pogo/bridget-state-history.json so it can render:

  • "(Nm on <label>)" or "(busy Nm)" for busy agents
  • "(idle Nm)" for idle agents
  • 🟠 + "long-running, check for stall" for busy agents whose (state, label)
    has held longer than BRIDGET_STUCK_MIN minutes (default 30m).

The sidecar is rebuilt in full on every `agents` invocation, so dead agents
are pruned automatically. Errors at read/write time fall through silently;
the user-visible render must never crash on a corrupted sidecar.
"""
import datetime
import importlib.util
import json
import os
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

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
    return _load_bridget(tmp_path)


# -- _humanize_minutes -----------------------------------------------------

@pytest.mark.parametrize('seconds,expected', [
    (0, '0m'),
    (59, '0m'),       # sub-minute is intentionally floored to 0m
    (60, '1m'),
    (5 * 60, '5m'),
    (59 * 60, '59m'),
    (60 * 60, '1h0m'),
    (90 * 60, '1h30m'),
    (3 * 3600 + 7 * 60, '3h7m'),
])
def test_humanize_minutes(bridget, seconds, expected):
    assert bridget._humanize_minutes(seconds) == expected


def test_humanize_minutes_clamps_negative(bridget):
    """Defensive: a negative elapsed (clock skew) must not render as
    '-1m'. Floor at 0."""
    assert bridget._humanize_minutes(-5) == '0m'


# -- _load_state_history / _save_state_history -----------------------------

def test_load_state_history_missing_file_returns_empty(bridget):
    assert not bridget.STATE_HISTORY_FILE.exists()
    assert bridget._load_state_history() == {}


def test_load_state_history_malformed_returns_empty(bridget):
    bridget.STATE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    bridget.STATE_HISTORY_FILE.write_text('not json {')
    assert bridget._load_state_history() == {}


def test_load_state_history_non_dict_top_level_returns_empty(bridget):
    """A JSON array or scalar at the top level must not poison .get() calls.
    Treat as empty so the renderer gracefully resets every agent's clock."""
    bridget.STATE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    bridget.STATE_HISTORY_FILE.write_text('[1, 2, 3]')
    assert bridget._load_state_history() == {}


def test_save_state_history_roundtrip(bridget):
    payload = {
        'architect': {
            'state': 'busy',
            'label': 'drafting mg-1234',
            'since': '2026-05-17T14:00:00Z',
        },
    }
    bridget._save_state_history(payload)
    assert bridget.STATE_HISTORY_FILE.exists()
    assert bridget._load_state_history() == payload


def test_save_state_history_swallows_errors(bridget, monkeypatch, capsys):
    """A failed write must not crash callers — the next invocation just
    sees stale data and resets the clock."""
    def boom(*a, **k):
        raise OSError('disk full')

    monkeypatch.setattr(bridget.Path, 'write_text', boom)
    # Must not raise.
    bridget._save_state_history({'x': {}})


# -- _diff_history ---------------------------------------------------------

NOW = datetime.datetime(2026, 5, 17, 14, 30, 0, tzinfo=datetime.timezone.utc)


def test_diff_history_no_prior_resets(bridget):
    entry, elapsed = bridget._diff_history(None, 'busy', 'drafting mg-1', NOW)
    assert elapsed == 0.0
    assert entry == {
        'state': 'busy',
        'label': 'drafting mg-1',
        'since': '2026-05-17T14:30:00Z',
    }


def test_diff_history_matching_state_and_label_carries_forward(bridget):
    """Same (state, label) → keep prior `since`, return elapsed since then."""
    prior = {
        'state': 'busy',
        'label': 'drafting mg-1',
        'since': '2026-05-17T14:25:00Z',  # 5 minutes ago
    }
    entry, elapsed = bridget._diff_history(prior, 'busy', 'drafting mg-1', NOW)
    assert entry is prior  # carry-forward, not a fresh dict
    assert elapsed == pytest.approx(5 * 60)


def test_diff_history_state_change_resets(bridget):
    """idle → busy is a fresh start — reset since to now."""
    prior = {
        'state': 'idle',
        'label': None,
        'since': '2026-05-17T13:00:00Z',
    }
    entry, elapsed = bridget._diff_history(prior, 'busy', 'drafting mg-1', NOW)
    assert elapsed == 0.0
    assert entry['since'] == '2026-05-17T14:30:00Z'
    assert entry['state'] == 'busy'


def test_diff_history_label_change_resets(bridget):
    """State unchanged but label changed = different work → reset.
    'busy: drafting mg-1' → 'busy: reviewing mg-2' is a context switch even
    though pogod still says healthy."""
    prior = {
        'state': 'busy',
        'label': 'drafting mg-1',
        'since': '2026-05-17T13:00:00Z',
    }
    entry, elapsed = bridget._diff_history(prior, 'busy', 'reviewing mg-2', NOW)
    assert elapsed == 0.0
    assert entry['label'] == 'reviewing mg-2'
    assert entry['since'] == '2026-05-17T14:30:00Z'


def test_diff_history_unparseable_since_resets(bridget):
    """A corrupted `since` timestamp must self-heal on next render rather
    than poisoning every subsequent read."""
    prior = {
        'state': 'busy',
        'label': 'drafting mg-1',
        'since': 'not-a-timestamp',
    }
    entry, elapsed = bridget._diff_history(prior, 'busy', 'drafting mg-1', NOW)
    assert elapsed == 0.0
    assert entry['since'] == '2026-05-17T14:30:00Z'


def test_diff_history_clock_skew_clamps_to_zero(bridget):
    """`since` in the future (clock skew between hosts via the sidecar)
    must not produce negative elapsed."""
    prior = {
        'state': 'busy',
        'label': 'drafting mg-1',
        'since': '2026-05-17T15:00:00Z',  # 30 minutes IN THE FUTURE
    }
    entry, elapsed = bridget._diff_history(prior, 'busy', 'drafting mg-1', NOW)
    assert elapsed == 0.0


def test_diff_history_idle_with_none_label_carries_forward(bridget):
    """Idle agents have label=None; matching None-to-None must still carry
    the prior `since` so '(idle Nm)' grows monotonically across renders."""
    prior = {
        'state': 'idle',
        'label': None,
        'since': '2026-05-17T14:20:00Z',  # 10 minutes ago
    }
    entry, elapsed = bridget._diff_history(prior, 'idle', None, NOW)
    assert entry is prior
    assert elapsed == pytest.approx(10 * 60)


# -- Integration: `agents` renderer end-to-end ------------------------------

def _mock_run_pogo_factory(agents_json: str):
    """Build a run_pogo replacement that returns `agents_json` for
    `agent list --json` and a healthy diagnose for everything else."""
    def fake(args):
        if args[:2] == ['agent', 'list']:
            return (0, agents_json, '')
        if args[:2] == ['agent', 'diagnose']:
            return (0, json.dumps({'health': 'healthy', 'idle_duration': '0s'}), '')
        return (0, '', '')
    return fake


def _write_status(bridget, name: str, state_field: str, age_seconds: float = 5.0) -> None:
    import time
    bridget.AGENT_STATUS_DIR.mkdir(parents=True, exist_ok=True)
    p = bridget.AGENT_STATUS_DIR / f'{name}.json'
    p.write_text(json.dumps({'state': state_field}))
    if age_seconds:
        ts = time.time() - age_seconds
        os.utime(p, (ts, ts))


def test_agents_view_first_run_renders_zero_minutes(bridget):
    """Fresh deploy: no sidecar exists, every agent gets '(0m on <label>)'
    or '(idle 0m)'. The next invocation will start showing real deltas."""
    _write_status(bridget, 'architect', 'busy: drafting mg-1', age_seconds=5)
    agents = json.dumps([
        {'name': 'architect', 'status': 'running', 'type': 'crew'},
    ])
    with mock.patch.object(bridget, 'run_pogo', side_effect=_mock_run_pogo_factory(agents)), \
         mock.patch.object(bridget, 'fetch_mg_claims_by_assignee', return_value={}):
        reply = bridget.handle_command('agents')

    assert '🟡' in reply
    assert '(0m on drafting mg-1)' in reply
    # No stuck warning at 0m.
    assert '🟠' not in reply
    assert 'long-running' not in reply
    # Sidecar was written.
    assert bridget.STATE_HISTORY_FILE.exists()
    history = json.loads(bridget.STATE_HISTORY_FILE.read_text())
    assert 'architect' in history
    assert history['architect']['state'] == 'busy'
    assert history['architect']['label'] == 'drafting mg-1'


def test_agents_view_carries_elapsed_across_invocations(bridget):
    """Pre-seed the sidecar with a 5-minute-old entry; the next render
    must show '(5m on …)' for an agent still on the same label."""
    _write_status(bridget, 'architect', 'busy: drafting mg-1', age_seconds=5)
    bridget.STATE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    five_min_ago = (datetime.datetime.now(datetime.timezone.utc)
                    - datetime.timedelta(minutes=5))
    bridget.STATE_HISTORY_FILE.write_text(json.dumps({
        'architect': {
            'state': 'busy',
            'label': 'drafting mg-1',
            'since': five_min_ago.strftime('%Y-%m-%dT%H:%M:%SZ'),
        },
    }))
    agents = json.dumps([
        {'name': 'architect', 'status': 'running', 'type': 'crew'},
    ])
    with mock.patch.object(bridget, 'run_pogo', side_effect=_mock_run_pogo_factory(agents)), \
         mock.patch.object(bridget, 'fetch_mg_claims_by_assignee', return_value={}):
        reply = bridget.handle_command('agents')

    assert '(5m on drafting mg-1)' in reply
    assert '🟠' not in reply


def test_agents_view_stuck_flag_fires_past_threshold(bridget, monkeypatch):
    """Busy agent that's been on the same (state, label) past
    BRIDGET_STUCK_MIN flips the emoji to 🟠 and gets a long-running note."""
    monkeypatch.setattr(bridget, 'BRIDGET_STUCK_MIN', 30)
    _write_status(bridget, 'director', 'busy: reviewing ds-7d1a', age_seconds=5)
    bridget.STATE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    long_ago = (datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(minutes=45))
    bridget.STATE_HISTORY_FILE.write_text(json.dumps({
        'director': {
            'state': 'busy',
            'label': 'reviewing ds-7d1a',
            'since': long_ago.strftime('%Y-%m-%dT%H:%M:%SZ'),
        },
    }))
    agents = json.dumps([
        {'name': 'director', 'status': 'running', 'type': 'crew'},
    ])
    with mock.patch.object(bridget, 'run_pogo', side_effect=_mock_run_pogo_factory(agents)), \
         mock.patch.object(bridget, 'fetch_mg_claims_by_assignee', return_value={}):
        reply = bridget.handle_command('agents')

    assert '🟠' in reply
    assert 'long-running, check for stall' in reply
    assert '(45m on reviewing ds-7d1a)' in reply
    # Stuck means emoji swap — the 🟡 busy glyph must NOT appear for this row.
    director_line = next(l for l in reply.splitlines() if 'director' in l)
    assert '🟡' not in director_line


def test_agents_view_stuck_threshold_respects_env_override(bridget, monkeypatch):
    """BRIDGET_STUCK_MIN=5 → 7-minute-old (state, label) is already stuck."""
    monkeypatch.setattr(bridget, 'BRIDGET_STUCK_MIN', 5)
    _write_status(bridget, 'director', 'busy: reviewing ds-7d1a', age_seconds=5)
    bridget.STATE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    seven_min_ago = (datetime.datetime.now(datetime.timezone.utc)
                     - datetime.timedelta(minutes=7))
    bridget.STATE_HISTORY_FILE.write_text(json.dumps({
        'director': {
            'state': 'busy',
            'label': 'reviewing ds-7d1a',
            'since': seven_min_ago.strftime('%Y-%m-%dT%H:%M:%SZ'),
        },
    }))
    agents = json.dumps([
        {'name': 'director', 'status': 'running', 'type': 'crew'},
    ])
    with mock.patch.object(bridget, 'run_pogo', side_effect=_mock_run_pogo_factory(agents)), \
         mock.patch.object(bridget, 'fetch_mg_claims_by_assignee', return_value={}):
        reply = bridget.handle_command('agents')

    assert '🟠' in reply
    assert '(7m on reviewing ds-7d1a)' in reply


def test_agents_view_idle_renders_idle_minutes(bridget):
    """Idle agent without a fresh JSON gets '(idle Nm)'."""
    # No status file → healthy + no JSON → idle via mg-9939.
    bridget.STATE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    eight_min_ago = (datetime.datetime.now(datetime.timezone.utc)
                     - datetime.timedelta(minutes=8))
    bridget.STATE_HISTORY_FILE.write_text(json.dumps({
        'mayor': {
            'state': 'idle',
            'label': None,
            'since': eight_min_ago.strftime('%Y-%m-%dT%H:%M:%SZ'),
        },
    }))
    agents = json.dumps([
        {'name': 'mayor', 'status': 'running', 'type': 'crew'},
    ])
    with mock.patch.object(bridget, 'run_pogo', side_effect=_mock_run_pogo_factory(agents)), \
         mock.patch.object(bridget, 'fetch_mg_claims_by_assignee', return_value={}):
        reply = bridget.handle_command('agents')

    assert '🟢' in reply
    assert '(idle 8m)' in reply


def test_agents_view_label_change_resets_elapsed(bridget):
    """architect switched from drafting mg-1 to reviewing mg-2: elapsed
    resets to 0m even though state stayed 'busy'."""
    _write_status(bridget, 'architect', 'busy: reviewing mg-2', age_seconds=5)
    bridget.STATE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    long_ago = (datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(minutes=20))
    bridget.STATE_HISTORY_FILE.write_text(json.dumps({
        'architect': {
            'state': 'busy',
            'label': 'drafting mg-1',
            'since': long_ago.strftime('%Y-%m-%dT%H:%M:%SZ'),
        },
    }))
    agents = json.dumps([
        {'name': 'architect', 'status': 'running', 'type': 'crew'},
    ])
    with mock.patch.object(bridget, 'run_pogo', side_effect=_mock_run_pogo_factory(agents)), \
         mock.patch.object(bridget, 'fetch_mg_claims_by_assignee', return_value={}):
        reply = bridget.handle_command('agents')

    assert '(0m on reviewing mg-2)' in reply
    assert '(20m on drafting mg-1)' not in reply
    # New since is recorded.
    history = json.loads(bridget.STATE_HISTORY_FILE.read_text())
    assert history['architect']['label'] == 'reviewing mg-2'


def test_agents_view_prunes_dead_agents_from_sidecar(bridget):
    """Only agents currently in `pogo agent list` survive the rewrite —
    a removed agent's history entry is dropped so the sidecar doesn't
    grow unboundedly across renames."""
    bridget.STATE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    bridget.STATE_HISTORY_FILE.write_text(json.dumps({
        'ghost': {
            'state': 'busy',
            'label': 'haunting',
            'since': '2026-05-17T13:00:00Z',
        },
        'mayor': {
            'state': 'idle',
            'label': None,
            'since': '2026-05-17T14:00:00Z',
        },
    }))
    agents = json.dumps([
        {'name': 'mayor', 'status': 'running', 'type': 'crew'},
    ])
    with mock.patch.object(bridget, 'run_pogo', side_effect=_mock_run_pogo_factory(agents)), \
         mock.patch.object(bridget, 'fetch_mg_claims_by_assignee', return_value={}):
        bridget.handle_command('agents')

    history = json.loads(bridget.STATE_HISTORY_FILE.read_text())
    assert 'ghost' not in history
    assert 'mayor' in history


def test_agents_view_offline_agents_get_no_appendix(bridget):
    """An offline agent gets ⚪ + no duration appendix (and no cycle
    paren — the latter was pre-existing)."""
    agents = json.dumps([
        {'name': 'gone', 'status': 'stopped', 'type': 'crew'},
    ])
    with mock.patch.object(bridget, 'run_pogo', side_effect=_mock_run_pogo_factory(agents)), \
         mock.patch.object(bridget, 'fetch_mg_claims_by_assignee', return_value={}):
        reply = bridget.handle_command('agents')

    gone_line = next(l for l in reply.splitlines() if 'gone' in l)
    assert '⚪' in gone_line
    assert 'offline' in gone_line
    assert '(idle' not in gone_line
    assert '(busy' not in gone_line
    assert '0m' not in gone_line


def test_agents_view_busy_without_badge_renders_busy_appendix(bridget):
    """'busy:' (colon, empty label) → state=busy but badge=None. Render
    '(busy Nm)' rather than '(Nm on None)'."""
    _write_status(bridget, 'terse', 'busy:', age_seconds=5)
    agents = json.dumps([
        {'name': 'terse', 'status': 'running', 'type': 'crew'},
    ])
    with mock.patch.object(bridget, 'run_pogo', side_effect=_mock_run_pogo_factory(agents)), \
         mock.patch.object(bridget, 'fetch_mg_claims_by_assignee', return_value={}):
        reply = bridget.handle_command('agents')

    assert '(busy 0m)' in reply
    assert 'None' not in reply  # no None literal leaks into output


def test_agents_view_polecat_busy_renders_appendix(bridget):
    """Polecats short-circuit through agent_state's non-crew branch to
    'busy' with the claimed mg-id as a badge. They still get tracked —
    a polecat sitting on the same task >30m flags as stuck."""
    bridget.STATE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    twelve_min_ago = (datetime.datetime.now(datetime.timezone.utc)
                      - datetime.timedelta(minutes=12))
    bridget.STATE_HISTORY_FILE.write_text(json.dumps({
        'cat-mg-1234': {
            'state': 'busy',
            'label': 'mg-1234',
            'since': twelve_min_ago.strftime('%Y-%m-%dT%H:%M:%SZ'),
        },
    }))
    agents = json.dumps([
        {'name': 'cat-mg-1234', 'status': 'running', 'type': 'polecat'},
    ])
    with mock.patch.object(bridget, 'run_pogo', side_effect=_mock_run_pogo_factory(agents)), \
         mock.patch.object(bridget, 'fetch_mg_claims_by_assignee',
                           return_value={'cat-mg-1234': ['mg-1234']}):
        reply = bridget.handle_command('agents')

    assert '(12m on mg-1234)' in reply


def test_agents_view_survives_corrupted_sidecar(bridget):
    """A corrupted sidecar must not crash the render — treat as empty
    (resetting every agent's clock) rather than propagating the exception."""
    bridget.STATE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    bridget.STATE_HISTORY_FILE.write_text('not json {')
    _write_status(bridget, 'architect', 'busy: drafting mg-1', age_seconds=5)
    agents = json.dumps([
        {'name': 'architect', 'status': 'running', 'type': 'crew'},
    ])
    with mock.patch.object(bridget, 'run_pogo', side_effect=_mock_run_pogo_factory(agents)), \
         mock.patch.object(bridget, 'fetch_mg_claims_by_assignee', return_value={}):
        reply = bridget.handle_command('agents')

    assert '(0m on drafting mg-1)' in reply
    # The corrupted file is overwritten with valid JSON on save.
    history = json.loads(bridget.STATE_HISTORY_FILE.read_text())
    assert 'architect' in history


# -- BRIDGET_STUCK_MIN env-var parsing --------------------------------------

def test_bridget_stuck_min_default_is_30(bridget):
    """Without the env var set, default to 30 minutes per ds-25d7."""
    # Reload with no override.
    assert bridget.BRIDGET_STUCK_MIN == 30


def test_bridget_stuck_min_env_var_parsed(tmp_path, monkeypatch):
    """BRIDGET_STUCK_MIN=15 → 15."""
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('BRIDGET_STUCK_MIN', '15')
    mod = _load_bridget(tmp_path)
    assert mod.BRIDGET_STUCK_MIN == 15


def test_bridget_stuck_min_garbage_falls_back_to_default(tmp_path, monkeypatch):
    """Malformed env var must not crash module import."""
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('BRIDGET_STUCK_MIN', 'not-a-number')
    mod = _load_bridget(tmp_path)
    assert mod.BRIDGET_STUCK_MIN == 30


def test_bridget_stuck_min_zero_clamps_to_one(tmp_path, monkeypatch):
    """0 or negative would mean 'every busy agent is stuck on first
    render', which is useless. Clamp to a sane minimum of 1m."""
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('BRIDGET_STUCK_MIN', '0')
    mod = _load_bridget(tmp_path)
    assert mod.BRIDGET_STUCK_MIN == 1
