"""Tests for the BRIDGET_PROFILE env-var gate (mg-5059).

Robin (DO bridget) and laptop bridget share the same codebase;
BRIDGET_PROFILE selects which command set is exposed. Default is
'laptop' (omits librarian sync / search + spend from help and verb
dispatch). 'robin' unlocks the full set.

These tests cover the gate from two sides:
- help rendering (menu listing + drill-down for one command)
- handle_command verb dispatch (short-circuits Robin-only verbs)

The bridget module reads BRIDGET_PROFILE at import time, so each
profile gets its own fixture that sets the env var before loading.
"""
import importlib.util
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
def bridget_laptop(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('BRIDGET_PROFILE', raising=False)
    return _load_bridget(tmp_path)


@pytest.fixture
def bridget_laptop_explicit(tmp_path, monkeypatch):
    """Same as bridget_laptop but with BRIDGET_PROFILE=laptop set explicitly."""
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('BRIDGET_PROFILE', 'laptop')
    return _load_bridget(tmp_path)


@pytest.fixture
def bridget_robin(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('BRIDGET_PROFILE', 'robin')
    return _load_bridget(tmp_path)


# -- module constants ------------------------------------------------------

def test_profile_default_is_laptop(bridget_laptop):
    assert bridget_laptop.BRIDGET_PROFILE == 'laptop'


def test_profile_explicit_laptop(bridget_laptop_explicit):
    assert bridget_laptop_explicit.BRIDGET_PROFILE == 'laptop'


def test_profile_robin(bridget_robin):
    assert bridget_robin.BRIDGET_PROFILE == 'robin'


def test_robin_only_set_matches_design(bridget_laptop):
    assert bridget_laptop.ROBIN_ONLY_COMMANDS == frozenset(
        {'librarian sync', 'librarian search', 'spend'}
    )


def test_profile_is_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('BRIDGET_PROFILE', 'ROBIN')
    mod = _load_bridget(tmp_path)
    assert mod.BRIDGET_PROFILE == 'robin'


# -- help menu listing -----------------------------------------------------

def test_help_menu_omits_robin_only_on_laptop(bridget_laptop):
    reply = bridget_laptop.handle_command('help')
    assert 'librarian sync' not in reply
    assert 'librarian search' not in reply
    # Note: `spend` is a short word; check the slash signature explicitly.
    assert '`/spend`' not in reply


def test_help_menu_keeps_non_gated_on_laptop(bridget_laptop):
    reply = bridget_laptop.handle_command('help')
    # Sanity: gating shouldn't drop unrelated commands.
    assert 'approve' in reply
    assert 'status' in reply
    assert 'help' in reply


def test_help_menu_includes_robin_only_on_robin(bridget_robin):
    reply = bridget_robin.handle_command('help')
    assert 'librarian sync' in reply
    assert 'librarian search' in reply
    # Slash-prefixed signature (mg-a0f3).
    assert '`/spend`' in reply


def test_command_list_omits_robin_only_on_laptop(bridget_laptop):
    # COMMAND_LIST powers the startup DM at watch_mailbox; it must follow
    # the same gating as the help menu.
    cl = bridget_laptop.COMMAND_LIST
    assert '`/librarian sync' not in cl
    assert '`/librarian search' not in cl
    assert '`/spend`' not in cl


def test_command_list_includes_robin_only_on_robin(bridget_robin):
    cl = bridget_robin.COMMAND_LIST
    assert '`/librarian sync' in cl
    assert '`/librarian search' in cl
    assert '`/spend`' in cl


def test_visible_commands_filtered_on_laptop(bridget_laptop):
    names = {c['name'] for c in bridget_laptop.VISIBLE_COMMANDS}
    assert 'librarian sync' not in names
    assert 'librarian search' not in names
    assert 'spend' not in names
    # COMMANDS itself is unchanged (so COMMANDS_BY_NAME can still surface
    # an informative 'Robin-only' message when the user types
    # `help librarian sync`).
    full_names = {c['name'] for c in bridget_laptop.COMMANDS}
    assert {'librarian sync', 'librarian search', 'spend'} <= full_names


# -- help <command> drill-down ---------------------------------------------

@pytest.mark.parametrize('verb', ['librarian sync', 'librarian search', 'spend'])
def test_help_command_gated_on_laptop(bridget_laptop, verb):
    reply = bridget_laptop.handle_command(f'help {verb}')
    assert 'Robin (DO bridget)' in reply
    assert verb in reply
    assert 'not available on this laptop bridget' in reply


@pytest.mark.parametrize('verb', ['librarian sync', 'librarian search', 'spend'])
def test_help_command_unblocked_on_robin(bridget_robin, verb):
    reply = bridget_robin.handle_command(f'help {verb}')
    # Robin sees the regular detail rendering (signature + description),
    # not the gating reply.
    assert 'Robin (DO bridget)' not in reply
    assert bridget_robin.COMMANDS_BY_NAME[verb]['signature'] in reply


def test_help_for_non_gated_command_unchanged_on_laptop(bridget_laptop):
    reply = bridget_laptop.handle_command('help approve')
    assert 'approve a design (auto-clears related mails)' in reply


# -- verb dispatch ----------------------------------------------------------

@pytest.mark.parametrize('text', [
    'librarian sync MYSPACE',
    'librarian sync MYSPACE 12345',
    'librarian search needle',
    'spend',
    'spend extra args',
])
def test_robin_only_verb_blocked_on_laptop(bridget_laptop, text):
    # No subprocess / API call should be made — the gate fires before
    # dispatch. We verify by patching Popen + urllib.request.urlopen to
    # fail loudly if hit.
    with mock.patch.object(bridget_laptop.subprocess, 'Popen',
                           side_effect=AssertionError('Popen should not be called')), \
         mock.patch.object(bridget_laptop.subprocess, 'run',
                           side_effect=AssertionError('run should not be called')):
        reply = bridget_laptop.handle_command(text)
    assert 'Robin (DO bridget)' in reply
    assert 'not available on this laptop bridget' in reply


@pytest.mark.parametrize('verb,expected_label', [
    ('librarian sync', 'librarian sync'),
    ('librarian search', 'librarian search'),
    ('spend', 'spend'),
])
def test_robin_only_reply_quotes_verb(bridget_laptop, verb, expected_label):
    reply = bridget_laptop.handle_command(verb if verb != 'librarian sync' else f'{verb} MYSPACE')
    assert f'`{expected_label}`' in reply


def test_librarian_sync_reaches_handler_on_robin(bridget_robin, tmp_path, monkeypatch):
    # Regression: when profile=robin, the verb falls through to the
    # original handler (Popen invoked, lock file written).
    lock_path = tmp_path / 'librarian.lock'
    monkeypatch.setattr(bridget_robin, 'LIBRARIAN_LOCK_PATH', str(lock_path))

    class FakeProc:
        pid = 9999

    calls = []
    def fake_popen(args, **kwargs):
        calls.append(args)
        return FakeProc()

    with mock.patch.object(bridget_robin.subprocess, 'Popen', side_effect=fake_popen):
        reply = bridget_robin.handle_command('librarian sync MYSPACE')

    assert len(calls) == 1
    assert '🔄' in reply
    assert 'MYSPACE' in reply
    assert lock_path.exists()


def test_librarian_search_reaches_handler_on_robin(bridget_robin, tmp_path, monkeypatch):
    # Regression: profile=robin lets `librarian search` reach the rg
    # subprocess invocation rather than being short-circuited.
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    monkeypatch.setenv('CONFLUENCE_DATA_DIR', str(data_dir))
    # Reload so the env override is picked up by _librarian_search_root.
    # (CONFLUENCE_DATA_DIR is read inside the handler via os.environ, so
    # no reload needed, but be defensive.)

    class FakeRun:
        stdout = ''
        stderr = ''
        returncode = 1  # rg "no matches" — design says friendly reply

    with mock.patch.object(bridget_robin.subprocess, 'run', return_value=FakeRun()):
        reply = bridget_robin.handle_command('librarian search needle')

    # The "no results" friendly reply confirms we reached the handler.
    assert 'Robin (DO bridget)' not in reply


def test_spend_reaches_handler_on_robin(bridget_robin, monkeypatch):
    monkeypatch.setattr(
        bridget_robin, '_probe_anthropic_quota',
        lambda: {'error': 'connection refused'},
    )
    reply = bridget_robin.handle_command('spend')
    assert 'Robin (DO bridget)' not in reply
    assert 'Quota probe failed' in reply


# -- non-gated commands unaffected -----------------------------------------

def test_non_gated_verb_still_works_on_laptop(bridget_laptop):
    # Sanity: gating shouldn't break unrelated verbs. `help` is the
    # smallest no-side-effect probe.
    reply = bridget_laptop.handle_command('help')
    assert reply.startswith('**Commands:**')


def test_unknown_help_target_unchanged_on_laptop(bridget_laptop):
    # `help nonexistent` should still say Unknown, not Robin-only.
    reply = bridget_laptop.handle_command('help nonexistent')
    assert reply.startswith('Unknown command')
    assert 'Robin' not in reply


if __name__ == '__main__':
    import sys
    sys.exit(pytest.main([__file__, '-v']))
