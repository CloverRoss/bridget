"""Tests for the slash-command parser refactor (mg-a0f3, Robin port item 2).

Covers:
- `/<verb> …` strips the slash and routes to the same dispatcher.
- `<verb> …` (no slash) still executes but logs a stderr deprecation warning
  (back-compat for one release).
- Empty / whitespace-only DMs reply with the empty-message hint (no
  buffer, no nudge).
- Freeform non-slash text routes to the live chat-relay buffer
  (mg-c869) — full coverage lives in test_chat_relay_buffer; this file
  only verifies the parser dispatches to the relay rather than falling
  through to `Unrecognized`.
- `_is_known_verb` recognizes every command verb (plus the colon-suffixed
  `idea:` / `bug:` forms) and rejects freeform chat.
"""
import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch

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
    'status', 'inbox',
    'nudge mayor', 'restart', 'preapprove true',
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


# -- handle_command: non-slash, non-verb → live chat-relay (mg-c869) -------

@pytest.mark.parametrize('text', [
    'hello there',
    'how are things going',
    'I was wondering what you thought',
    'thanks for the help',
    'whatever',
])
def test_freeform_text_routes_to_chat_relay(bridget, text):
    # mg-c869 wired the relay: freeform text gets buffered for the
    # /route target and triggers a `pogo nudge`. We don't recheck the
    # buffer contents here (that's test_chat_relay_buffer); we just
    # assert the reply shape is the relay-confirmation, not the legacy
    # `Unrecognized` fallback.
    with patch.object(bridget, 'run_pogo', return_value=(0, '', '')):
        reply = bridget.handle_command(text)
    assert '💬' in reply
    # Default route is mayor, so confirmation names mayor.
    assert 'mayor' in reply
    # Drain so subsequent tests in this module don't see stray entries.
    bridget.drain_chat_buffer('mayor')


def test_empty_message_returns_empty_reply(bridget):
    # Empty / whitespace-only DMs aren't worth buffering — we return a
    # one-liner pointing at /help rather than dispatching to the legacy
    # `Unrecognized` fallback or queuing noise for the route target.
    assert bridget.handle_command('') == bridget.CHAT_RELAY_EMPTY_REPLY
    assert bridget.handle_command('    ') == bridget.CHAT_RELAY_EMPTY_REPLY


def test_empty_reply_mentions_slash_help(bridget):
    # The empty-message reply is the user's first signal that bridget
    # didn't parse their DM as a command — point them at `/help` so
    # they have a path back.
    assert '/help' in bridget.CHAT_RELAY_EMPTY_REPLY


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


def test_freeform_text_does_not_log_deprecation_warning(bridget, capsys, monkeypatch):
    # Chat-relay path is the *expected* future flow, not a deprecation.
    monkeypatch.setattr(bridget, 'run_pogo', lambda _args: (0, '', ''))
    bridget.handle_command('hello bridget')
    err = capsys.readouterr().err
    assert 'deprecated' not in err
    # Cleanup: drain the buffer entry the relay just added so we don't
    # leak state into other tests sharing the bridget fixture's HOME.
    bridget.drain_chat_buffer('mayor')


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
