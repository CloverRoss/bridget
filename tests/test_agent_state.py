"""Tests for agent_state, compute_agent_state, and fetch_mg_claims_by_assignee
in bridget.

Loads the bridget script under a fake $HOME so import-time config loading
succeeds without a real Discord token or pogo install. Each test exercises
one branch of the state-derivation decision tree.

mg-b4c0 changed the model: state derives from `pogo agent diagnose` health
(not the agent's self-reported JSON state field). The JSON is now advisory
— used only for the optional badge label when the derived state is `busy`
AND the JSON file mtime is within AGENT_STATUS_FRESH (2 min).
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / 'bridget'


def _load_bridget():
    fake_home = Path(tempfile.mkdtemp(prefix='bridget-test-'))
    os.environ['HOME'] = str(fake_home)
    env_dir = fake_home / '.pogo'
    env_dir.mkdir(parents=True)
    (env_dir / 'bridget.env').write_text(
        'DISCORD_BOT_TOKEN=fake-token-for-tests\n'
        'DISCORD_USER_ID=1\n'
        'DISCORD_SERVER_ID=2\n'
    )
    loader = SourceFileLoader('bridget', str(SCRIPT))
    spec = importlib.util.spec_from_loader('bridget', loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    # The module captured AGENT_STATUS_DIR at import time relative to fake
    # HOME; make sure it exists for status-file fixtures.
    module.AGENT_STATUS_DIR.mkdir(parents=True, exist_ok=True)
    return module


bridget = _load_bridget()


def _write_status(name: str, payload: dict, *, age_seconds: float = 0.0) -> Path:
    """Write a status JSON for `name` and stamp its mtime to `age_seconds`
    in the past. Returns the path."""
    path = bridget.AGENT_STATUS_DIR / f'{name}.json'
    path.write_text(json.dumps(payload))
    if age_seconds:
        ts = time.time() - age_seconds
        os.utime(path, (ts, ts))
    return path


def _clear_status(name: str) -> None:
    path = bridget.AGENT_STATUS_DIR / f'{name}.json'
    if path.exists():
        path.unlink()


# -- _state_from_diag: health → state mapping (per design §A) ---------------

@pytest.mark.parametrize('health,expected_state', [
    ('healthy', 'busy'),
    ('idle',    'idle'),
    ('stalled', 'stalled'),
    ('exited',  'offline'),
    ('dead',    'stalled'),  # registered as running but OS proc gone — wedge
])
def test_state_from_diag_maps_each_health_value(health, expected_state):
    _clear_status('agent-x')
    out = bridget._state_from_diag('agent-x', {'health': health, 'idle_duration': '5m'})
    assert out['state'] == expected_state
    assert out['health_raw'] == health
    assert out['idle_duration'] == '5m'
    # No fresh JSON exists, so badge is None regardless of state.
    assert out['badge'] is None


def test_state_from_diag_unknown_health_treated_as_stalled(capsys):
    _clear_status('agent-x')
    out = bridget._state_from_diag('agent-x', {'health': 'mystery'})
    assert out['state'] == 'stalled'
    assert out['badge'] is None
    err = capsys.readouterr().err
    assert 'unknown diagnose health' in err
    assert 'agent-x' in err


def test_state_from_diag_missing_health_treated_as_stalled(capsys):
    _clear_status('agent-x')
    out = bridget._state_from_diag('agent-x', {})
    assert out['state'] == 'stalled'
    assert out['health_raw'] == ''


# -- badge: fresh JSON gating ------------------------------------------------

def test_badge_appears_when_state_busy_and_json_fresh():
    _write_status('busybee', {'state': 'busy: drafting mg-9dea'}, age_seconds=10)
    out = bridget._state_from_diag('busybee', {'health': 'healthy'})
    assert out['state'] == 'busy'
    assert out['badge'] == 'drafting mg-9dea'


def test_badge_dropped_when_state_busy_but_json_stale():
    """JSON older than AGENT_STATUS_FRESH (2min) → no badge, even though
    state derives as busy. This is the core mg-b4c0 fix: stale 'drafted'
    labels can't linger after the agent has gone back to idle wait."""
    _write_status('busybee', {'state': 'busy: drafting mg-9dea'}, age_seconds=300)
    out = bridget._state_from_diag('busybee', {'health': 'healthy'})
    assert out['state'] == 'busy'
    assert out['badge'] is None


def test_badge_dropped_when_state_idle_even_if_json_fresh():
    """Even with a fresh JSON, an idle derived state should never carry a
    busy-label badge. Resolves the user's pain about stale 'busy' labels."""
    _write_status('rester', {'state': 'busy: drafting mg-9dea'}, age_seconds=10)
    out = bridget._state_from_diag('rester', {'health': 'idle'})
    assert out['state'] == 'idle'
    assert out['badge'] is None


def test_badge_dropped_when_state_stalled():
    _write_status('zombie', {'state': 'busy: drafting mg-9dea'}, age_seconds=10)
    out = bridget._state_from_diag('zombie', {'health': 'stalled'})
    assert out['state'] == 'stalled'
    assert out['badge'] is None


def test_badge_dropped_when_json_missing():
    _clear_status('ghost')
    out = bridget._state_from_diag('ghost', {'health': 'healthy'})
    assert out['state'] == 'busy'
    assert out['badge'] is None


def test_badge_dropped_when_json_state_field_is_bare_busy():
    """`busy` with no colon yields no badge — there's no label to surface
    and we don't repeat the word 'busy' as a badge."""
    _write_status('plain', {'state': 'busy'}, age_seconds=10)
    out = bridget._state_from_diag('plain', {'health': 'healthy'})
    assert out['badge'] is None


def test_badge_dropped_when_json_state_field_is_idle_but_derived_busy():
    """If JSON contradicts diagnose (says idle while we derive busy), the
    JSON is stale — drop the badge rather than show 'idle' under a busy
    row."""
    _write_status('mismatch', {'state': 'idle'}, age_seconds=10)
    out = bridget._state_from_diag('mismatch', {'health': 'healthy'})
    assert out['state'] == 'busy'
    assert out['badge'] is None


def test_badge_handles_busy_colon_with_empty_label():
    _write_status('terse', {'state': 'busy:'}, age_seconds=10)
    out = bridget._state_from_diag('terse', {'health': 'healthy'})
    assert out['badge'] is None


def test_badge_dropped_when_json_malformed():
    path = bridget.AGENT_STATUS_DIR / 'broken.json'
    path.write_text('not json {')
    out = bridget._state_from_diag('broken', {'health': 'healthy'})
    assert out['state'] == 'busy'
    assert out['badge'] is None


# -- compute_agent_state: end-to-end with run_pogo mocked --------------------

def test_compute_agent_state_diagnose_failure_returns_offline():
    """Hard rule: a known-running agent whose diagnose call fails falls back
    to 'offline' with a faded '(diagnose failed)' suffix — NOT 'busy by
    default'. This explicitly drops the v3.1.0 defaulting rule."""
    with mock.patch.object(bridget, 'run_pogo', return_value=(1, '', 'boom')):
        out = bridget.compute_agent_state('unreachable')
    assert out['state'] == 'offline'
    assert out['badge'] == '(diagnose failed)'
    assert out['health_raw'] == ''


def test_compute_agent_state_diagnose_json_parse_error_returns_stalled(capsys):
    with mock.patch.object(bridget, 'run_pogo', return_value=(0, 'not json', '')):
        out = bridget.compute_agent_state('garbled')
    assert out['state'] == 'stalled'
    assert out['badge'] is None
    err = capsys.readouterr().err
    assert 'JSON parse error' in err
    assert 'garbled' in err


@pytest.mark.parametrize('health,expected', [
    ('healthy', 'busy'),
    ('idle',    'idle'),
    ('stalled', 'stalled'),
    ('exited',  'offline'),
    ('dead',    'stalled'),
])
def test_compute_agent_state_full_loop_each_health(health, expected):
    diag_out = json.dumps({'health': health, 'idle_duration': '0s'})
    with mock.patch.object(bridget, 'run_pogo', return_value=(0, diag_out, '')):
        out = bridget.compute_agent_state(f'agent-{health}')
    assert out['state'] == expected
    assert out['health_raw'] == health


def test_compute_agent_state_invokes_diagnose_with_expected_args():
    captured = {}

    def fake_run(args):
        captured['args'] = args
        return (0, json.dumps({'health': 'idle'}), '')

    with mock.patch.object(bridget, 'run_pogo', side_effect=fake_run):
        bridget.compute_agent_state('thearchitect')
    assert captured['args'] == ['agent', 'diagnose', 'thearchitect', '--json']


# -- agent_state: process status short-circuits -----------------------------

def test_agent_state_offline_when_process_not_running():
    """Process not running → offline regardless of type. No diagnose call
    required (and none should happen — that would be wasted work)."""
    with mock.patch.object(bridget, 'run_pogo') as m:
        result = bridget.agent_state('foo', {'status': 'stopped'}, {})
    assert result == ('⚪', 'offline', None)
    m.assert_not_called()


def test_agent_state_offline_when_status_field_missing():
    with mock.patch.object(bridget, 'run_pogo') as m:
        result = bridget.agent_state('foo', {}, {})
    assert result == ('⚪', 'offline', None)
    m.assert_not_called()


# -- agent_state: non-crew (polecat) short-circuit --------------------------

def test_polecat_running_no_claim_renders_busy():
    """Polecats are ephemeral and don't write status JSONs / aren't checked
    via diagnose by the agents view; if they're running, they're busy."""
    with mock.patch.object(bridget, 'run_pogo') as m:
        result = bridget.agent_state(
            'cat-mg-1234', {'status': 'running', 'type': 'polecat'}, {},
        )
    assert result == ('🟡', 'busy', None)
    m.assert_not_called()


def test_polecat_running_with_claim_uses_mg_id_badge():
    with mock.patch.object(bridget, 'run_pogo'):
        result = bridget.agent_state(
            'cat-mg-1234', {'status': 'running', 'type': 'polecat'},
            {'cat-mg-1234': ['mg-1234']},
        )
    assert result == ('🟡', 'busy', 'mg-1234')


# -- agent_state: crew agent → derived from compute_agent_state -------------

def _patch_diagnose(health: str):
    return mock.patch.object(
        bridget, 'run_pogo',
        return_value=(0, json.dumps({'health': health, 'idle_duration': '0s'}), ''),
    )


def test_crew_busy_when_diagnose_healthy_no_json():
    """No fresh JSON → derived busy with no badge (NOT the old 'no JSON
    means stalled' rule)."""
    _clear_status('architect')
    with _patch_diagnose('healthy'):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('🟡', 'busy', None)


def test_crew_idle_when_diagnose_idle():
    _clear_status('architect')
    with _patch_diagnose('idle'):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('🟢', 'idle', None)


def test_crew_stalled_when_diagnose_stalled():
    with _patch_diagnose('stalled'):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('🔴', 'stalled', None)


def test_crew_offline_when_diagnose_exited():
    with _patch_diagnose('exited'):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('⚪', 'offline', None)


def test_crew_stalled_when_diagnose_dead():
    """`dead` (registered as running but OS proc gone — wedge) maps to
    🔴 stalled, not ⚪ offline, because it indicates an unhealthy mismatch
    between pogod's state and reality."""
    with _patch_diagnose('dead'):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('🔴', 'stalled', None)


def test_crew_busy_includes_badge_from_fresh_json():
    _write_status('architect', {'state': 'busy: drafting mg-9dea'}, age_seconds=5)
    with _patch_diagnose('healthy'):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('🟡', 'busy', 'drafting mg-9dea')


def test_crew_busy_drops_badge_when_json_stale():
    _write_status('architect', {'state': 'busy: drafting mg-9dea'}, age_seconds=600)
    with _patch_diagnose('healthy'):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('🟡', 'busy', None)


def test_crew_idle_drops_badge_even_with_fresh_json():
    """Core mg-b4c0 behavior: badge disappears the moment derived state
    flips to idle, regardless of what the JSON still says."""
    _write_status('architect', {'state': 'busy: drafting mg-9dea'}, age_seconds=5)
    with _patch_diagnose('idle'):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('🟢', 'idle', None)


def test_crew_diagnose_failure_renders_offline_with_faded_badge():
    """Hard rule: diagnose failure for a known-running agent → offline,
    not 'busy by default'."""
    with mock.patch.object(bridget, 'run_pogo', return_value=(1, '', 'boom')):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('⚪', 'offline', '(diagnose failed)')


def test_missing_type_treated_as_crew():
    """No 'type' key falls through to the crew path so unknown agent kinds
    aren't silently downgraded to 'always busy'."""
    with _patch_diagnose('idle'):
        result = bridget.agent_state(
            'foo', {'status': 'running'}, {},
        )
    assert result == ('🟢', 'idle', None)


# -- fetch_mg_claims_by_assignee --------------------------------------------

def test_fetch_mg_claims_groups_by_assignee():
    fake_out = (
        '{"id":"mg-aaaa","assignee":"architect","status":"claimed"}\n'
        '{"id":"mg-bbbb","assignee":"mayor","status":"claimed"}\n'
        '{"id":"mg-cccc","assignee":"architect","status":"claimed"}\n'
    )
    with mock.patch.object(bridget, 'run_mg', return_value=(0, fake_out, '')):
        result = bridget.fetch_mg_claims_by_assignee()
    assert result == {
        'architect': ['mg-aaaa', 'mg-cccc'],
        'mayor': ['mg-bbbb'],
    }


def test_fetch_mg_claims_returns_empty_dict_on_failure():
    """Errors degrade silently to {} so the renderer drops the badge,
    not the row."""
    with mock.patch.object(bridget, 'run_mg', return_value=(1, '', 'boom')):
        assert bridget.fetch_mg_claims_by_assignee() == {}


def test_fetch_mg_claims_skips_blank_and_invalid_lines():
    fake_out = (
        '\n'
        '{"id":"mg-aaaa","assignee":"architect"}\n'
        'not json\n'
        '{"id":"mg-bbbb"}\n'
        '{"assignee":"mayor"}\n'
    )
    with mock.patch.object(bridget, 'run_mg', return_value=(0, fake_out, '')):
        assert bridget.fetch_mg_claims_by_assignee() == {
            'architect': ['mg-aaaa'],
        }


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
