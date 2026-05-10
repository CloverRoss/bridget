"""Tests for agent_state and fetch_mg_claims_by_assignee in bridget.

Loads the bridget script under a fake $HOME so import-time config loading
succeeds without a real Discord token or pogo install. Each test exercises
one branch of the state-derivation decision tree.
"""
import importlib.util
import os
import sys
import tempfile
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
    return module


bridget = _load_bridget()


# -- agent_state: process status ---------------------------------------------

def test_offline_when_process_not_running():
    assert bridget.agent_state(
        'foo', {'status': 'stopped'}, {}, None, {},
    ) == ('⚪', 'offline', None)


def test_offline_when_status_field_missing():
    assert bridget.agent_state(
        'foo', {}, {}, None, {},
    ) == ('⚪', 'offline', None)


# -- agent_state: non-crew (polecat) short-circuit --------------------------

def test_polecat_running_no_claim_renders_busy():
    """Polecats are ephemeral and don't write status JSONs; when running with
    no claim they should still render 🟡 busy, not 🔴 stalled."""
    assert bridget.agent_state(
        'cat-mg-1234', {'status': 'running', 'type': 'polecat'}, {},
        None, {},
    ) == ('🟡', 'busy', None)


def test_polecat_running_with_claim_uses_mg_id_badge():
    assert bridget.agent_state(
        'cat-mg-1234', {'status': 'running', 'type': 'polecat'}, {},
        None, {'cat-mg-1234': ['mg-1234']},
    ) == ('🟡', 'busy', 'mg-1234')


def test_crew_with_missing_status_json_still_stalled():
    """Crew agents follow the existing decision tree unchanged."""
    assert bridget.agent_state(
        'architect', {'status': 'running', 'type': 'crew'},
        {'health': 'healthy'}, None, {},
    ) == ('🔴', 'stalled', 'stale heartbeat')


def test_missing_type_treated_as_crew():
    """No 'type' key → fall through to the existing crew rules so unknown
    agent kinds aren't silently downgraded to 'always busy'."""
    assert bridget.agent_state(
        'foo', {'status': 'running'}, {'health': 'healthy'}, None, {},
    ) == ('🔴', 'stalled', 'stale heartbeat')


# -- agent_state: stalled ----------------------------------------------------

def test_stalled_when_status_json_missing():
    assert bridget.agent_state(
        'foo', {'status': 'running'}, {'health': 'healthy'}, None, {},
    ) == ('🔴', 'stalled', 'stale heartbeat')


def test_stalled_when_health_unhealthy():
    assert bridget.agent_state(
        'foo', {'status': 'running'}, {'health': 'unhealthy'},
        {'state': 'idle'}, {},
    ) == ('🔴', 'stalled', 'unhealthy')


def test_stalled_when_health_field_absent():
    """Missing 'health' is conservatively treated as not healthy."""
    assert bridget.agent_state(
        'foo', {'status': 'running'}, {}, {'state': 'idle'}, {},
    ) == ('🔴', 'stalled', 'unhealthy')


# -- agent_state: idle / busy ------------------------------------------------

def test_idle_when_state_idle():
    assert bridget.agent_state(
        'foo', {'status': 'running'}, {'health': 'healthy'},
        {'state': 'idle'}, {},
    ) == ('🟢', 'idle', None)


def test_busy_plain_state_no_label():
    assert bridget.agent_state(
        'foo', {'status': 'running'}, {'health': 'healthy'},
        {'state': 'busy'}, {},
    ) == ('🟡', 'busy', None)


def test_busy_with_self_reported_label():
    assert bridget.agent_state(
        'foo', {'status': 'running'}, {'health': 'healthy'},
        {'state': 'busy: dispatching mg-9dea'}, {},
    ) == ('🟡', 'busy', 'dispatching mg-9dea')


def test_busy_with_empty_label_falls_back_to_none_badge():
    """`busy:` with nothing after the colon should not yield an empty badge."""
    assert bridget.agent_state(
        'foo', {'status': 'running'}, {'health': 'healthy'},
        {'state': 'busy:'}, {},
    ) == ('🟡', 'busy', None)


def test_busy_no_state_field_with_mg_claim():
    assert bridget.agent_state(
        'foo', {'status': 'running'}, {'health': 'healthy'},
        {'last_cycle': 'x'}, {'foo': ['mg-abcd']},
    ) == ('🟡', 'busy', 'mg-abcd')


def test_busy_no_state_field_uses_first_claim_when_multiple():
    assert bridget.agent_state(
        'foo', {'status': 'running'}, {'health': 'healthy'},
        {'last_cycle': 'x'}, {'foo': ['mg-aaaa', 'mg-bbbb']},
    ) == ('🟡', 'busy', 'mg-aaaa')


def test_busy_no_state_field_no_claim_default():
    """The 'don't assume idle' default — running, healthy, no signal of work,
    still renders 🟡 busy."""
    assert bridget.agent_state(
        'foo', {'status': 'running'}, {'health': 'healthy'},
        {'last_cycle': 'x'}, {},
    ) == ('🟡', 'busy', None)


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
