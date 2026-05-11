"""Tests for the per-agent honoring lines in preapprove handler replies.

mg-628d / mg-ac5a: bridget reads a static PREAPPROVE_SUPPORT map and renders
which agents currently honor enabled / fast in its replies, so the user can
tell what's actually live without inspecting each agent's prompt.

Each test pins PREAPPROVE_SUPPORT to a fixture so future bumps (when mayor
or director gain support, or fast lands somewhere) don't break this file —
the rendering behavior is what's under test, not the support roster.
"""
import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

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


@pytest.fixture
def pin_support(bridget, monkeypatch):
    """Pin PREAPPROVE_SUPPORT to a known map so test assertions are stable
    even if the production roster changes."""
    def _pin(support):
        monkeypatch.setattr(bridget, 'PREAPPROVE_SUPPORT', support)
    return _pin


# -- _preapprove_honor_lists helper ------------------------------------------

def test_honor_lists_split_by_field(bridget, pin_support):
    pin_support({
        'architect': {'enabled': True,  'fast': False},
        'mayor':     {'enabled': False, 'fast': False},
        'director':  {'enabled': True,  'fast': True},
    })
    he, hf, nhe = bridget._preapprove_honor_lists(bridget.PREAPPROVE_SUPPORT)
    assert he == ['architect', 'director']
    assert hf == ['director']
    assert nhe == ['mayor']


def test_honor_lists_preserve_map_insertion_order(bridget, pin_support):
    pin_support({
        'director':  {'enabled': True,  'fast': False},
        'architect': {'enabled': True,  'fast': False},
        'mayor':     {'enabled': False, 'fast': False},
    })
    he, _, _ = bridget._preapprove_honor_lists(bridget.PREAPPROVE_SUPPORT)
    # Order tracks the map, not alphabetical — bumping the source dict order
    # is the lever for changing rendered order.
    assert he == ['director', 'architect']


def test_fmt_agent_list_empty_default(bridget):
    assert bridget._fmt_agent_list([]) == '(none yet)'


def test_fmt_agent_list_custom_empty(bridget):
    assert bridget._fmt_agent_list([], empty='nope') == 'nope'


def test_fmt_agent_list_joins(bridget):
    assert bridget._fmt_agent_list(['a', 'b']) == 'a, b'


# -- status query (no args) --------------------------------------------------

def test_status_disabled_unchanged_no_honor_lines(bridget, pin_support):
    pin_support({
        'architect': {'enabled': True,  'fast': False},
        'mayor':     {'enabled': False, 'fast': False},
    })
    reply = bridget.handle_command('preapprove')
    assert reply == 'pre-approval: disabled.'
    assert 'honored by' not in reply


def test_status_enabled_renders_both_honor_lists(bridget, pin_support):
    pin_support({
        'architect': {'enabled': True,  'fast': False},
        'mayor':     {'enabled': False, 'fast': False},
        'director':  {'enabled': False, 'fast': False},
    })
    bridget.save_preapproval({'enabled': True, 'fast': False})
    reply = bridget.handle_command('preapprove')
    assert '🟢 pre-approval: enabled (fast: off).' in reply
    assert 'enabled honored by: architect' in reply
    assert 'fast honored by: (none yet)' in reply
    # Status query doesn't surface the NOT-honored list — that's a set-action
    # caveat, not a query one.
    assert 'NOT honored by' not in reply


def test_status_enabled_fast_on_shows_fast_on(bridget, pin_support):
    pin_support({
        'architect': {'enabled': True,  'fast': False},
    })
    bridget.save_preapproval({'enabled': True, 'fast': True})
    reply = bridget.handle_command('preapprove')
    assert 'fast: on' in reply
    assert 'fast honored by: (none yet)' in reply


# -- preapprove true ---------------------------------------------------------

def test_true_renders_enabled_honor_and_not_honored(bridget, pin_support):
    pin_support({
        'architect': {'enabled': True,  'fast': False},
        'mayor':     {'enabled': False, 'fast': False},
        'director':  {'enabled': False, 'fast': False},
    })
    reply = bridget.handle_command('preapprove true')
    assert reply.startswith('✓ pre-approval: enabled (fast: off).')
    assert 'enabled honored by: architect' in reply
    assert 'NOT honored by: mayor, director' in reply
    # No fast lines on plain `true`.
    assert 'fast honored by' not in reply
    assert '(enabled)' not in reply  # the disambiguating suffix is fast-only


def test_true_when_all_agents_honor_enabled_omits_not_honored(bridget, pin_support):
    pin_support({
        'architect': {'enabled': True, 'fast': False},
    })
    reply = bridget.handle_command('preapprove true')
    assert 'enabled honored by: architect' in reply
    # When the NOT-honored list is empty the line is dropped — no awkward
    # `NOT honored by: ` trailing nothing.
    assert 'NOT honored by' not in reply


# -- preapprove true fast ----------------------------------------------------

def test_true_fast_renders_fast_caveat_and_disambiguated_not_honored(bridget, pin_support):
    pin_support({
        'architect': {'enabled': True,  'fast': False},
        'mayor':     {'enabled': False, 'fast': False},
        'director':  {'enabled': False, 'fast': False},
    })
    reply = bridget.handle_command('preapprove true fast')
    assert reply.startswith('✓ pre-approval: enabled (fast: on).')
    assert 'enabled honored by: architect' in reply
    assert 'fast honored by: (none yet — currently behaves like fast=off)' in reply
    # The NOT-honored line is suffixed (enabled) when both honor lists are
    # in play, so the user can tell which field the absent agents fail on.
    assert 'NOT honored by (enabled): mayor, director' in reply


def test_true_fast_with_real_fast_support_drops_caveat(bridget, pin_support):
    # Hypothetical future state: an agent ships fast support. The caveat line
    # should fall away cleanly without code changes.
    pin_support({
        'architect': {'enabled': True, 'fast': True},
        'mayor':     {'enabled': True, 'fast': False},
    })
    reply = bridget.handle_command('preapprove true fast')
    assert 'fast honored by: architect' in reply
    assert 'currently behaves like fast=off' not in reply
    # All agents honor enabled in this fixture → no NOT-honored line.
    assert 'NOT honored by' not in reply


# -- preapprove false (unchanged) --------------------------------------------

def test_false_response_is_unchanged_no_honor_lines(bridget, pin_support):
    pin_support({
        'architect': {'enabled': True,  'fast': False},
        'mayor':     {'enabled': False, 'fast': False},
    })
    bridget.save_preapproval({'enabled': True, 'fast': True})
    reply = bridget.handle_command('preapprove false')
    assert reply == '✓ pre-approval: disabled.'
    assert 'honored by' not in reply


# -- production PREAPPROVE_SUPPORT shape -------------------------------------

def test_production_support_map_has_expected_agents(bridget):
    """Pin the production map's shape so a typo in the dict (missing key,
    wrong field name) trips immediately rather than silently rendering an
    incomplete reply."""
    support = bridget.PREAPPROVE_SUPPORT
    assert set(support.keys()) == {'architect', 'mayor', 'director'}
    for agent, fields in support.items():
        assert set(fields.keys()) == {'enabled', 'fast'}, agent
        assert isinstance(fields['enabled'], bool), agent
        assert isinstance(fields['fast'], bool), agent


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
