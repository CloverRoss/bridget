"""Tests for agent_state, compute_agent_state, and fetch_mg_claims_by_assignee
in bridget.

Loads the bridget script under a fake $HOME so import-time config loading
succeeds without a real Discord token or pogo install. Each test exercises
one branch of the state-derivation decision tree.

mg-9939 refined the mg-b4c0 model. pogod's `pogo agent diagnose` returns
healthy or stalled for any alive crew agent — never idle — so a pure
health→state map renders every alive agent as busy. Fix: when health is
healthy, consult the agent's self-reported JSON state as the tiebreaker.
State=busy iff the JSON `state` starts with 'busy:' AND mtime is within
AGENT_STATUS_FRESH (2 min); otherwise state=idle. Other health values
(stalled / exited / dead) ignore the JSON.
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


# -- _state_from_diag: non-healthy health values ----------------------------
# 'healthy' is exercised separately below since it consults the JSON state.

@pytest.mark.parametrize('health,expected_state', [
    ('idle',    'idle'),
    ('stalled', 'stalled'),  # with work; stalled-no-work covered separately (mg-3538)
    ('exited',  'offline'),
    ('dead',    'offline'),  # mg-9939: dead → offline (was stalled)
])
def test_state_from_diag_maps_non_healthy_values(health, expected_state):
    _clear_status('agent-x')
    # Stalled now consults a live work check (mg-3538); mock work-present
    # so this test exercises the original stalled→stalled mapping branch.
    with mock.patch.object(bridget, '_agent_has_pending_work', return_value=True):
        out = bridget._state_from_diag('agent-x', {'health': health, 'idle_duration': '5m'})
    assert out['state'] == expected_state
    assert out['health_raw'] == health
    assert out['idle_duration'] == '5m'
    assert out['badge'] is None


def test_state_from_diag_non_healthy_ignores_json_state():
    """A fresh JSON saying 'busy: …' must not flip a stalled/exited/dead
    agent into busy. Only `healthy` consults the JSON tiebreaker."""
    _write_status('worker', {'state': 'busy: drafting mg-9dea'}, age_seconds=5)
    with mock.patch.object(bridget, '_agent_has_pending_work', return_value=True):
        for health, expected in [('stalled', 'stalled'), ('exited', 'offline'), ('dead', 'offline')]:
            out = bridget._state_from_diag('worker', {'health': health})
            assert out['state'] == expected, f'{health} should map to {expected}'
            assert out['badge'] is None, f'{health} should not carry the JSON badge'


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


# -- _state_from_diag: healthy + JSON tiebreaker (mg-9939) ------------------
# pogod's diagnose returns healthy for any alive crew agent, so a static
# healthy → busy map renders every alive agent as busy. The agent's
# self-reported JSON state is the tiebreaker.

def test_healthy_with_no_json_yields_idle():
    """Core mg-9939 fix: without a fresh self-report we cannot tell busy
    apart from idle, so default to idle. (The old healthy → busy map was
    the mg-eb6e regression.)"""
    _clear_status('quiet')
    out = bridget._state_from_diag('quiet', {'health': 'healthy'})
    assert out['state'] == 'idle'
    assert out['badge'] is None


def test_healthy_with_fresh_busy_json_yields_busy_with_badge():
    _write_status('worker', {'state': 'busy: drafting mg-9dea'}, age_seconds=10)
    out = bridget._state_from_diag('worker', {'health': 'healthy'})
    assert out['state'] == 'busy'
    assert out['badge'] == 'drafting mg-9dea'


def test_healthy_with_stale_busy_json_yields_idle():
    """JSON older than AGENT_STATUS_FRESH loses its vote — back to idle
    even when state field still says 'busy: …'. This is what stops stale
    'drafted mg-XXXX' labels lingering for an hour after the agent slept."""
    _write_status('worker', {'state': 'busy: drafting mg-9dea'}, age_seconds=300)
    out = bridget._state_from_diag('worker', {'health': 'healthy'})
    assert out['state'] == 'idle'
    assert out['badge'] is None


def test_healthy_with_fresh_non_busy_json_yields_idle():
    """If the JSON's state doesn't start with 'busy:' the agent is not
    actively working — render as idle."""
    _write_status('rester', {'state': 'idle'}, age_seconds=10)
    out = bridget._state_from_diag('rester', {'health': 'healthy'})
    assert out['state'] == 'idle'
    assert out['badge'] is None


def test_healthy_with_bare_busy_yields_idle():
    """'busy' without a colon doesn't start with 'busy:' — treat as idle
    rather than guessing. (Bare 'busy' shouldn't be written by anything we
    ship; this just guards the prefix check.)"""
    _write_status('plain', {'state': 'busy'}, age_seconds=10)
    out = bridget._state_from_diag('plain', {'health': 'healthy'})
    assert out['state'] == 'idle'
    assert out['badge'] is None


def test_healthy_with_busy_colon_empty_label_yields_busy_no_badge():
    """'busy:' with empty label still starts with 'busy:' → state=busy.
    Badge stays None because there's no human-readable label content."""
    _write_status('terse', {'state': 'busy:'}, age_seconds=10)
    out = bridget._state_from_diag('terse', {'health': 'healthy'})
    assert out['state'] == 'busy'
    assert out['badge'] is None


def test_healthy_with_malformed_json_yields_idle():
    """Unparseable JSON contributes no tiebreaker vote — default to idle."""
    path = bridget.AGENT_STATUS_DIR / 'broken.json'
    path.write_text('not json {')
    out = bridget._state_from_diag('broken', {'health': 'healthy'})
    assert out['state'] == 'idle'
    assert out['badge'] is None


def test_idle_health_drops_badge_even_if_json_fresh():
    """A fresh JSON 'busy: …' must not carry over to idle health — the
    JSON only votes when health is healthy."""
    _write_status('rester', {'state': 'busy: drafting mg-9dea'}, age_seconds=10)
    out = bridget._state_from_diag('rester', {'health': 'idle'})
    assert out['state'] == 'idle'
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
    # 'healthy' is JSON-state-dependent — covered separately above.
    ('idle',    'idle'),
    ('stalled', 'stalled'),  # with work; stalled-no-work covered separately (mg-3538)
    ('exited',  'offline'),
    ('dead',    'offline'),
])
def test_compute_agent_state_full_loop_each_health(health, expected):
    _clear_status(f'agent-{health}')
    diag_out = json.dumps({'health': health, 'idle_duration': '0s'})
    with mock.patch.object(bridget, 'run_pogo', return_value=(0, diag_out, '')), \
         mock.patch.object(bridget, '_agent_has_pending_work', return_value=True):
        out = bridget.compute_agent_state(f'agent-{health}')
    assert out['state'] == expected
    assert out['health_raw'] == health


def test_compute_agent_state_healthy_with_fresh_busy_json():
    _write_status('thearchitect', {'state': 'busy: drafting mg-1234'}, age_seconds=10)
    diag_out = json.dumps({'health': 'healthy', 'idle_duration': '0s'})
    with mock.patch.object(bridget, 'run_pogo', return_value=(0, diag_out, '')):
        out = bridget.compute_agent_state('thearchitect')
    assert out['state'] == 'busy'
    assert out['badge'] == 'drafting mg-1234'


def test_compute_agent_state_healthy_without_json_yields_idle():
    _clear_status('thearchitect')
    diag_out = json.dumps({'health': 'healthy', 'idle_duration': '0s'})
    with mock.patch.object(bridget, 'run_pogo', return_value=(0, diag_out, '')):
        out = bridget.compute_agent_state('thearchitect')
    assert out['state'] == 'idle'
    assert out['badge'] is None


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


def test_crew_idle_when_diagnose_healthy_no_json():
    """mg-9939: healthy with no fresh JSON tiebreaker → idle, not busy.
    pogod's healthy doesn't distinguish actively-working from waiting."""
    _clear_status('architect')
    with _patch_diagnose('healthy'):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('🟢', 'idle', None)


def test_crew_idle_when_diagnose_idle():
    _clear_status('architect')
    with _patch_diagnose('idle'):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('🟢', 'idle', None)


def test_crew_stalled_when_diagnose_stalled():
    with _patch_diagnose('stalled'), \
         mock.patch.object(bridget, '_agent_has_pending_work', return_value=True):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('🔴', 'stalled', None)


def test_crew_idle_when_diagnose_stalled_with_no_work():
    """mg-3538: diagnose=stalled + no pending work → reclassify as idle.
    Matches Clover's spec ('stalled = not responding to work items'). A
    sleepy agent with an empty mg+mail queue is just quiet, not wedged."""
    with _patch_diagnose('stalled'), \
         mock.patch.object(bridget, '_agent_has_pending_work', return_value=False):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('🟢', 'idle', None)


def test_crew_offline_when_diagnose_exited():
    with _patch_diagnose('exited'):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('⚪', 'offline', None)


def test_crew_offline_when_diagnose_dead():
    """mg-9939: `dead` (registered as running but OS proc gone — wedge)
    maps to ⚪ offline. The previous `dead → stalled` mapping conflated
    a wedge with a slow agent."""
    with _patch_diagnose('dead'):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('⚪', 'offline', None)


def test_crew_busy_includes_badge_from_fresh_json():
    _write_status('architect', {'state': 'busy: drafting mg-9dea'}, age_seconds=5)
    with _patch_diagnose('healthy'):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('🟡', 'busy', 'drafting mg-9dea')


def test_crew_idle_when_diagnose_healthy_and_json_stale():
    """mg-9939: stale JSON loses its tiebreaker vote — agent renders as
    idle (not 'busy with no badge'), so a sleeping agent doesn't keep
    flashing yellow forever."""
    _write_status('architect', {'state': 'busy: drafting mg-9dea'}, age_seconds=600)
    with _patch_diagnose('healthy'):
        result = bridget.agent_state(
            'architect', {'status': 'running', 'type': 'crew'}, {},
        )
    assert result == ('🟢', 'idle', None)


def test_crew_idle_drops_badge_even_with_fresh_json():
    """When diagnose says idle directly, the JSON is not consulted."""
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


# -- mg-3538: stalled-no-work reclassification ------------------------------
# diagnose=stalled now consults live mg+mail queries. Stalled-with-work
# stays 🔴 (real wedge); stalled-with-no-work reclassifies as 🟢 idle.

def test_state_from_diag_stalled_with_no_work_reclassifies_as_idle():
    """mg-3538: pogod says stalled, mg+mail both empty → render as idle.
    health_raw becomes 'stalled (no work)' so the diagnose origin is
    preserved (debuggable) while the user-visible state matches the
    actual situation: nothing to do, not wedged."""
    with mock.patch.object(bridget, '_agent_has_pending_work', return_value=False):
        out = bridget._state_from_diag('quiet', {'health': 'stalled', 'idle_duration': '2h'})
    assert out['state'] == 'idle'
    assert out['health_raw'] == 'stalled (no work)'
    assert out['badge'] is None
    assert out['idle_duration'] == '2h'


def test_state_from_diag_stalled_with_pending_work_stays_stalled():
    """mg-3538: a stalled agent that DOES have queued work is a real wedge
    — keep it 🔴 so the operator notices."""
    with mock.patch.object(bridget, '_agent_has_pending_work', return_value=True):
        out = bridget._state_from_diag('wedged', {'health': 'stalled', 'idle_duration': '2h'})
    assert out['state'] == 'stalled'
    assert out['health_raw'] == 'stalled'
    assert out['badge'] is None


# -- _agent_has_pending_work: live mg + mail query --------------------------

def test_agent_has_pending_work_returns_true_when_mg_list_has_items():
    """First available mg item short-circuits — no need to check mail."""
    fake_out = '{"id":"mg-1234","assignee":"architect","status":"available"}\n'
    calls = []

    def fake_run_mg(args):
        calls.append(args)
        if args[0] == 'list':
            return (0, fake_out, '')
        return (0, 'should-not-reach-mail', '')

    with mock.patch.object(bridget, 'run_mg', side_effect=fake_run_mg):
        assert bridget._agent_has_pending_work('architect') is True
    # mail list never queried — the mg-list hit was decisive.
    assert all(c[0] != 'mail' for c in calls)


def test_agent_has_pending_work_returns_true_when_mail_unread():
    """Empty mg queue + unread mail → still has work."""
    def fake_run_mg(args):
        if args[0] == 'list':
            return (0, '', '')
        if args[0] == 'mail':
            return (0, '1 unread message for architect\n  m1  mg-9dea  hello\n', '')
        return (1, '', 'unexpected')

    with mock.patch.object(bridget, 'run_mg', side_effect=fake_run_mg):
        assert bridget._agent_has_pending_work('architect') is True


def test_agent_has_pending_work_returns_false_when_both_empty():
    """Empty mg queue + 'No unread messages' from mail → quiet, not wedged."""
    def fake_run_mg(args):
        if args[0] == 'list':
            return (0, '', '')
        if args[0] == 'mail':
            return (0, 'No unread messages for architect\n', '')
        return (1, '', 'unexpected')

    with mock.patch.object(bridget, 'run_mg', side_effect=fake_run_mg):
        assert bridget._agent_has_pending_work('architect') is False


def test_agent_has_pending_work_returns_false_when_both_fail():
    """Defensive: subcommand errors fall through to False rather than
    false-positive a wedge. Missing a real wedge for one refresh tick is
    cheaper than reporting every dead inbox as 🔴 stalled."""
    with mock.patch.object(bridget, 'run_mg', return_value=(1, '', 'boom')):
        assert bridget._agent_has_pending_work('architect') is False


def test_agent_has_pending_work_skips_blank_lines_in_mg_list():
    """mg list emits JSONL — blank lines and parse errors don't count as
    work, but a single valid line does."""
    fake_out = '\nnot-json\n{"id":"mg-1234","assignee":"architect"}\n'
    with mock.patch.object(bridget, 'run_mg', return_value=(0, fake_out, '')):
        assert bridget._agent_has_pending_work('architect') is True


def test_agent_has_pending_work_excludes_project_only_items():
    """Type=project items are Roadmap records managed by mayor, not
    actionable agent to-dos. A project-only assignee list + empty mail
    must NOT count as pending work (mg-e528: director false-stalled)."""
    fake_out = (
        '{"id":"mg-aaaa","assignee":"director","type":"project"}\n'
        '{"id":"mg-bbbb","assignee":"director","type":"project"}\n'
    )

    def fake_run_mg(args):
        if args[0] == 'list':
            return (0, fake_out, '')
        if args[0] == 'mail':
            return (0, 'No unread messages for director\n', '')
        return (1, '', 'unexpected')

    with mock.patch.object(bridget, 'run_mg', side_effect=fake_run_mg):
        assert bridget._agent_has_pending_work('director') is False


def test_agent_has_pending_work_true_when_project_plus_actionable():
    """A single non-project item among Projects is enough — the actionable
    item is real work even if the rest are Roadmap records."""
    fake_out = (
        '{"id":"mg-aaaa","assignee":"director","type":"project"}\n'
        '{"id":"mg-bbbb","assignee":"director","type":"report"}\n'
    )
    with mock.patch.object(bridget, 'run_mg', return_value=(0, fake_out, '')):
        assert bridget._agent_has_pending_work('director') is True


def test_agent_has_pending_work_true_for_typeless_items():
    """Items without a `type` field (legacy or non-project default) still
    count as actionable — the filter is strictly `type == 'project'`,
    not a project-only allowlist."""
    fake_out = '{"id":"mg-cccc","assignee":"architect"}\n'
    with mock.patch.object(bridget, 'run_mg', return_value=(0, fake_out, '')):
        assert bridget._agent_has_pending_work('architect') is True


def test_agent_has_pending_work_project_only_plus_unread_mail_is_true():
    """Project-only mg list but unread mail → still has work. The mail
    branch must run after the mg-list loop skips all-project items."""
    fake_out = '{"id":"mg-aaaa","assignee":"director","type":"project"}\n'

    def fake_run_mg(args):
        if args[0] == 'list':
            return (0, fake_out, '')
        if args[0] == 'mail':
            return (0, '1 unread message for director\n  m1  mg-9dea  hi\n', '')
        return (1, '', 'unexpected')

    with mock.patch.object(bridget, 'run_mg', side_effect=fake_run_mg):
        assert bridget._agent_has_pending_work('director') is True


def test_agent_has_pending_work_calls_mg_list_with_assignee_filter():
    """Live query must scope by assignee so cross-agent items don't
    false-positive a wedge."""
    captured = []

    def fake_run_mg(args):
        captured.append(args)
        if args[0] == 'list':
            return (0, '', '')
        return (0, 'No unread messages\n', '')

    with mock.patch.object(bridget, 'run_mg', side_effect=fake_run_mg):
        bridget._agent_has_pending_work('architect')
    list_call = next(c for c in captured if c[0] == 'list')
    assert '--status=available' in list_call
    assert '--assignee=architect' in list_call
    assert '--json' in list_call


# -- agent_cycle_paren: stale-window short-circuit dropped (mg-9c3a) --------
# Before mg-9c3a, a status file whose `updated_at` was older than 30 minutes
# returned '?, ?' regardless of how informative its last_cycle / next_cycle
# fields still were. The fix removes that gate so an agent on a long
# inter-cycle gap (e.g. architect's 60-min nap) still renders real deltas.

def test_agent_cycle_paren_returns_question_marks_when_file_missing():
    _clear_status('absent')
    assert bridget.agent_cycle_paren('absent') == '(last cycle ?, next cycle ?)'


def test_agent_cycle_paren_returns_question_marks_when_payload_malformed():
    _write_status('broken', {'unrelated': 'noise'})
    # Missing required keys → KeyError → except branch.
    assert bridget.agent_cycle_paren('broken') == '(last cycle ?, next cycle ?)'


def test_agent_cycle_paren_renders_upcoming_next_cycle():
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {
        'last_cycle': (now - _dt.timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'next_cycle': (now + _dt.timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'updated_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    _write_status('alive', payload)
    out = bridget.agent_cycle_paren('alive')
    assert 'last cycle' in out
    assert 'next cycle in' in out
    assert '?' not in out


def test_agent_cycle_paren_renders_overdue_next_cycle():
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {
        'last_cycle': (now - _dt.timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'next_cycle': (now - _dt.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'updated_at': (now - _dt.timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    _write_status('overdue', payload)
    out = bridget.agent_cycle_paren('overdue')
    assert 'next cycle overdue' in out
    assert '?' not in out


def test_agent_cycle_paren_renders_when_updated_at_older_than_30m_but_next_cycle_upcoming():
    """Core mg-9c3a regression: an agent on a 60-minute inter-cycle gap has a
    stale updated_at by definition, but its next_cycle is still in the future
    and informative. The pre-mg-9c3a 30-minute short-circuit hid that signal
    behind '?, ?'."""
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {
        'last_cycle': (now - _dt.timedelta(minutes=45)).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'next_cycle': (now + _dt.timedelta(minutes=15)).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'updated_at': (now - _dt.timedelta(minutes=45)).strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    _write_status('napper', payload)
    out = bridget.agent_cycle_paren('napper')
    assert 'last cycle' in out
    assert 'next cycle in' in out
    assert '?' not in out


def test_agent_cycle_paren_no_longer_references_AGENT_STATUS_STALE():
    """Guard against accidental reintroduction of the 30-minute short-circuit."""
    assert not hasattr(bridget, 'AGENT_STATUS_STALE')


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
