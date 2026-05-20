"""Tests for the slash-command parser refactor (mg-a0f3, Robin port item 2).

Covers:
- `/<verb> …` strips the slash and routes to the same dispatcher.
- `<verb> …` (no slash) still executes but logs a stderr deprecation warning
  (back-compat for one release; drops once chat-relay lands).
- Any non-slash, non-verb message routes to the `chat-relay not yet wired`
  placeholder instead of falling through to `Unrecognized`.
- `_is_known_verb` recognizes every command verb (plus the colon-suffixed
  `idea:` / `bug:` forms) and rejects freeform chat.
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
    # Robin profile unlocks every verb so the parametrized verb-coverage
    # tests don't fight the profile gate (which lives in test_profile_gate.py).
    monkeypatch.setenv('BRIDGET_PROFILE', 'robin')
    mod = _load_bridget(tmp_path)
    monkeypatch.setattr(mod, 'mark_mail_read', lambda **_k: 0)
    monkeypatch.setattr(mod, '_clear_approval_mail', lambda _id: 0)
    monkeypatch.setattr(mod, 'log_mail_action', lambda *_a, **_k: None)
    monkeypatch.setattr(mod, 'route_recipient', lambda _id: 'architect')
    return mod


# -- _is_known_verb ---------------------------------------------------------

@pytest.mark.parametrize('text', [
    'approve mg-abcd', 'reject mg-abcd why', 'revise mg-abcd tweak',
    'explain mg-abcd huh', 'kickoff pj-abcd',
    'read m3', 'open mg-abcd', 'mail subject',
    'dismiss mg-abcd', 'dismiss all',
    'status', 'inbox', 'agents', 'agent list', 'crew',
    'nudge mayor', 'restart', 'quiet', 'preapprove true',
    'help', 'help approve', '?', 'commands',
    'librarian sync FOO', 'librarian search needle',
    'accountant run-now', 'accountant status', 'spend',
])
def test_is_known_verb_recognizes_all_command_verbs(bridget, text):
    assert bridget._is_known_verb(text)


@pytest.mark.parametrize('text', ['idea: something', 'bug: broken'])
def test_is_known_verb_recognizes_colon_prefixed_verbs(bridget, text):
    assert bridget._is_known_verb(text)


@pytest.mark.parametrize('text', [
    '', '   ',
    'hello',
    'tell me about the project',
    'how are you',
    'what is going on with mg-abcd',
    'thanks',
    'yes please do that',
])
def test_is_known_verb_rejects_freeform_chat(bridget, text):
    assert not bridget._is_known_verb(text)


# -- handle_command: slash-prefixed form is canonical ----------------------

def test_slash_help_returns_command_menu(bridget):
    reply = bridget.handle_command('/help')
    assert isinstance(reply, str)
    assert reply.startswith('**Commands:**')
    # Footer now points users at the slash form for drill-down.
    assert 'Type `/help <command>` for details on any one.' in reply


def test_slash_approve_dispatches_command(bridget, monkeypatch):
    monkeypatch.setattr(bridget, 'run_mg', lambda _args: (0, '', ''))
    reply = bridget.handle_command('/approve mg-abcd')
    assert '✓ approve sent' in reply
    assert 'mg-abcd' in reply


def test_slash_with_extra_whitespace_after_slash(bridget, monkeypatch):
    # `/   help` should still resolve to help — the dispatcher tolerates
    # the lazy thumb-typing case.
    monkeypatch.setattr(bridget, 'run_mg', lambda _args: (0, '', ''))
    reply = bridget.handle_command('/   help')
    assert reply.startswith('**Commands:**')


def test_slash_status_dispatches(bridget, monkeypatch):
    monkeypatch.setattr(bridget, 'get_status_summary', lambda: 'STATUS_OK')
    # status nudges the mayor — stub out so we don't shell to pogo in tests.
    monkeypatch.setattr(bridget, 'run_pogo', lambda _args: (0, '', ''))
    reply = bridget.handle_command('/status')
    assert 'STATUS_OK' in reply


def test_slash_idea_filed(bridget, monkeypatch):
    monkeypatch.setattr(bridget, 'run_mg', lambda _args: (0, 'mg-new', ''))
    reply = bridget.handle_command('/idea: refactor the parser')
    assert '✓ idea filed' in reply


# -- handle_command: non-slash, non-verb → chat-relay placeholder ----------

@pytest.mark.parametrize('text', [
    'hello there',
    'how are things going',
    'I was wondering what you thought',
    'thanks for the help',
    'whatever',
])
def test_freeform_text_routes_to_chat_relay_placeholder(bridget, text):
    reply = bridget.handle_command(text)
    assert reply == bridget.CHAT_RELAY_PLACEHOLDER
    assert 'chat-relay not yet wired' in reply


def test_empty_message_routes_to_chat_relay_placeholder(bridget):
    # Empty / whitespace-only DMs aren't commands; route to placeholder
    # rather than dispatching to the legacy `Unrecognized` fallback.
    assert bridget.handle_command('') == bridget.CHAT_RELAY_PLACEHOLDER
    assert bridget.handle_command('    ') == bridget.CHAT_RELAY_PLACEHOLDER


def test_chat_relay_placeholder_mentions_slash_help(bridget):
    # The placeholder is the user's first signal that bridget didn't
    # parse their DM as a command — point them at `/help` so they have
    # a path back.
    assert '/help' in bridget.CHAT_RELAY_PLACEHOLDER


# -- handle_command: un-prefixed legacy form still works + warns -----------

def test_legacy_unprefixed_command_executes(bridget, monkeypatch):
    # Back-compat: un-prefixed commands still dispatch for one release.
    monkeypatch.setattr(bridget, 'run_mg', lambda _args: (0, '', ''))
    reply = bridget.handle_command('approve mg-abcd')
    assert '✓ approve sent' in reply


def test_legacy_unprefixed_command_logs_deprecation_warning(bridget, monkeypatch, capsys):
    monkeypatch.setattr(bridget, 'run_mg', lambda _args: (0, '', ''))
    bridget.handle_command('approve mg-abcd')
    err = capsys.readouterr().err
    assert 'deprecated' in err
    assert 'approve' in err
    # Should suggest the slash form so launchd-log readers know the fix.
    assert '/approve' in err


def test_slash_form_does_not_log_deprecation_warning(bridget, monkeypatch, capsys):
    monkeypatch.setattr(bridget, 'run_mg', lambda _args: (0, '', ''))
    bridget.handle_command('/approve mg-abcd')
    err = capsys.readouterr().err
    assert 'deprecated' not in err


def test_freeform_text_does_not_log_deprecation_warning(bridget, capsys):
    # Chat-relay path is the *expected* future flow, not a deprecation.
    bridget.handle_command('hello bridget')
    err = capsys.readouterr().err
    assert 'deprecated' not in err


# -- COMMAND_LIST + help-menu signatures all carry the slash prefix --------

def test_all_command_signatures_start_with_slash(bridget):
    for c in bridget.COMMANDS:
        assert c['signature'].startswith('/'), (
            f"{c['name']!r} signature missing slash prefix: {c['signature']!r}"
        )


def test_help_menu_renders_slash_prefixed_signatures(bridget):
    reply = bridget.handle_command('/help')
    # Spot-check a couple verbs to make sure the help body really shows /.
    assert '`/approve' in reply
    assert '`/status`' in reply
    assert '`/help' in reply


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
