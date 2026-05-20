"""Tests for the `/route` slash command (mg-6b2b, Robin port item 5).

Covers:
- /route with no arg reports the current target + valid-agents list.
- /route <agent> for each crew agent (mayor / director / architect / doctor)
  sets the persisted target and replies with the confirmation line.
- The slash form (`/route ...`) and the back-compat un-prefixed form
  (`route ...`) both reach the same handler.
- Default target is mayor when the sidecar is missing, empty, corrupt,
  or names an unknown agent.
- Persistence across "restart" — re-importing the bridget module against
  the same HOME picks up the saved route.
- /route <unknown> rejects with the valid-agents list and does not
  mutate the sidecar.
- /route appears in COMMAND_VERBS and COMMANDS surfaces.
- /route is exposed under the laptop profile (not Robin-only).
"""
import importlib.util
import json
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


# -- load_route / save_route round-trip -------------------------------------

def test_load_route_default_when_missing(bridget):
    # Fresh HOME → sidecar does not exist → default mayor.
    assert not bridget.ROUTE_FILE.exists()
    assert bridget.load_route() == 'mayor'


def test_load_route_default_when_empty(bridget):
    bridget.ROUTE_FILE.parent.mkdir(parents=True, exist_ok=True)
    bridget.ROUTE_FILE.write_text('')
    assert bridget.load_route() == 'mayor'


def test_load_route_default_when_corrupt(bridget):
    bridget.ROUTE_FILE.parent.mkdir(parents=True, exist_ok=True)
    bridget.ROUTE_FILE.write_text('{not json')
    assert bridget.load_route() == 'mayor'


def test_load_route_default_when_unknown_agent(bridget):
    bridget.ROUTE_FILE.parent.mkdir(parents=True, exist_ok=True)
    bridget.ROUTE_FILE.write_text(json.dumps({'route': 'rando'}))
    assert bridget.load_route() == 'mayor'


def test_load_route_default_when_not_a_dict(bridget):
    bridget.ROUTE_FILE.parent.mkdir(parents=True, exist_ok=True)
    bridget.ROUTE_FILE.write_text(json.dumps(['mayor']))
    assert bridget.load_route() == 'mayor'


@pytest.mark.parametrize('agent', ['mayor', 'director', 'architect', 'doctor'])
def test_save_route_then_load_round_trip(bridget, agent):
    bridget.save_route(agent)
    assert bridget.load_route() == agent
    # Sidecar shape: flat dict with route + updated_at.
    data = json.loads(bridget.ROUTE_FILE.read_text())
    assert data['route'] == agent
    assert isinstance(data.get('updated_at'), str)
    assert data['updated_at']  # non-empty


# -- handle_command(/route) without arg -------------------------------------

def test_route_no_arg_shows_default(bridget):
    reply = bridget.handle_command('/route')
    assert 'mayor' in reply
    # Lists every valid agent so the user knows the menu.
    for agent in ('mayor', 'director', 'architect', 'doctor'):
        assert agent in reply
    assert '/route <agent>' in reply


def test_route_no_arg_shows_current_after_set(bridget):
    bridget.save_route('director')
    reply = bridget.handle_command('/route')
    # The first agent name in the reply is the current — assert via the
    # leading "Current chat route" label so we don't depend on order of
    # the valid-agents list.
    assert 'Current chat route' in reply
    assert '**director**' in reply


# -- handle_command(/route <agent>) -----------------------------------------

@pytest.mark.parametrize('agent', ['mayor', 'director', 'architect', 'doctor'])
def test_route_sets_each_crew_agent(bridget, agent):
    reply = bridget.handle_command(f'/route {agent}')
    assert '✓' in reply
    assert f'**{agent}**' in reply
    assert bridget.load_route() == agent


@pytest.mark.parametrize('agent', ['MAYOR', 'Director', 'ARCHITECT', 'doctor'])
def test_route_case_insensitive(bridget, agent):
    reply = bridget.handle_command(f'/route {agent}')
    assert '✓' in reply
    assert bridget.load_route() == agent.lower()


def test_route_strips_surrounding_whitespace(bridget):
    reply = bridget.handle_command('/route   doctor   ')
    assert '✓' in reply
    assert bridget.load_route() == 'doctor'


# -- back-compat un-prefixed form (handle_command path covers it) ----------

def test_route_unprefixed_back_compat_still_works(bridget):
    # mg-a0f3 keeps the un-slashed verbs working for one release with a
    # stderr deprecation log; verify /route honors that path too.
    reply = bridget.handle_command('route architect')
    assert '✓' in reply
    assert bridget.load_route() == 'architect'


# -- invalid agent ----------------------------------------------------------

@pytest.mark.parametrize('bad', ['nobody', 'human', 'rando', 'mayor2'])
def test_route_unknown_agent_rejected(bridget, bad):
    reply = bridget.handle_command(f'/route {bad}')
    assert 'Unknown agent' in reply
    assert bad in reply
    # All valid agents enumerated for the user to retry from.
    for agent in ('mayor', 'director', 'architect', 'doctor'):
        assert agent in reply
    # Sidecar untouched — still default.
    assert not bridget.ROUTE_FILE.exists()
    assert bridget.load_route() == 'mayor'


def test_route_unknown_agent_does_not_overwrite_existing(bridget):
    bridget.save_route('director')
    reply = bridget.handle_command('/route nobody')
    assert 'Unknown agent' in reply
    # Existing route preserved.
    assert bridget.load_route() == 'director'


# -- persistence across "restart" ------------------------------------------

def test_route_persists_across_restart(tmp_path, monkeypatch):
    # First import: set route to architect.
    monkeypatch.setenv('HOME', str(tmp_path))
    mod1 = _load_bridget(tmp_path)
    mod1.handle_command('/route architect')
    assert mod1.load_route() == 'architect'

    # Drop the cached module so we can reload bridget as a fresh process
    # would, then re-import against the same HOME.
    sys.modules.pop('bridget', None)
    mod2 = _load_bridget(tmp_path)
    # Same HOME → same sidecar → architect survives.
    assert mod2.ROUTE_FILE == mod1.ROUTE_FILE
    assert mod2.load_route() == 'architect'
    reply = mod2.handle_command('/route')
    assert '**architect**' in reply


# -- COMMANDS / verbs surface checks ---------------------------------------

def test_route_in_command_verbs(bridget):
    assert 'route' in bridget.COMMAND_VERBS


def test_route_is_known_verb(bridget):
    assert bridget._is_known_verb('route')
    assert bridget._is_known_verb('route mayor')


def test_route_appears_in_command_list_laptop_profile(bridget):
    # mg-6b2b — /route is not Robin-only, must show in the default laptop
    # profile help bullet list.
    assert '/route' in bridget.COMMAND_LIST
    assert any(c['name'] == 'route' for c in bridget.VISIBLE_COMMANDS)


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
