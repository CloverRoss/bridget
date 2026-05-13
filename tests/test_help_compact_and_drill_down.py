"""Tests for compact `help` menu + `help <command>` drill-down (mg-91d2).

Covers:
- `help` / `?` / `commands` returns a compact menu: header, every signature,
  footer; total length stays under the Discord per-message budget.
- `help <command>` returns the full description for that command.
- `help <unknown>` returns an "Unknown command" string.
- `help status` includes the description currently in COMMAND_LIST for status
  (the verbatim-preserve property).
- `COMMAND_LIST` derived constant matches the pre-refactor joined-bullet
  format so the watch_mailbox startup DM at line ~656 ("Recognized replies")
  keeps working without changes.
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
    # These tests cover the full compact-menu shape and the COMMAND_LIST
    # derived constant. Under the mg-5059 profile gate the laptop default
    # hides Robin-only commands; opt into 'robin' so every entry in
    # COMMANDS is visible (gate-specific behavior lives in
    # tests/test_profile_gate.py).
    monkeypatch.setenv('BRIDGET_PROFILE', 'robin')
    return _load_bridget(tmp_path)


# -- compact help menu ------------------------------------------------------

def test_help_menu_has_header(bridget):
    reply = bridget.handle_command('help')
    assert isinstance(reply, str)
    assert reply.startswith('**Commands:**')


def test_help_menu_lists_every_signature(bridget):
    reply = bridget.handle_command('help')
    for c in bridget.COMMANDS:
        assert c['signature'] in reply, \
            f"signature missing from help menu: {c['signature']!r}"


def test_help_menu_has_drill_down_footer(bridget):
    reply = bridget.handle_command('help')
    assert 'Type `help <command>` for details on any one.' in reply


def test_help_menu_fits_discord_budget(bridget):
    reply = bridget.handle_command('help')
    assert len(reply) <= 1900, f'help menu is {len(reply)} chars (>1900)'


@pytest.mark.parametrize('alias', ['help', '?', 'commands', 'HELP'])
def test_help_aliases_render_compact_menu(bridget, alias):
    reply = bridget.handle_command(alias)
    assert isinstance(reply, str)
    assert reply.startswith('**Commands:**')
    assert 'Type `help <command>` for details on any one.' in reply


# -- help <command> drill-down ---------------------------------------------

def test_help_command_returns_full_description(bridget):
    reply = bridget.handle_command('help approve')
    assert 'approve a design (auto-clears related mails)' in reply
    # Signature is included as the title.
    assert 'approve mg-XXXX (or dr-XXXX)' in reply


def test_help_status_preserves_legacy_description(bridget):
    # The status description carries the categorized-layout text that
    # other tests grep for in COMMAND_LIST. Make sure it survives in the
    # per-command drill-down too.
    reply = bridget.handle_command('help status')
    assert 'Projects / Reports / Designs / Bugs / Tasks' in reply
    assert 'approved Reports are hidden' in reply


def test_help_unknown_command(bridget):
    reply = bridget.handle_command('help nonexistent')
    assert reply.startswith('Unknown command')
    assert 'nonexistent' in reply


def test_help_command_is_case_insensitive(bridget):
    reply = bridget.handle_command('help APPROVE')
    assert 'approve a design (auto-clears related mails)' in reply


def test_help_for_help_itself(bridget):
    reply = bridget.handle_command('help help')
    assert 'help [<command>]' in reply


# -- COMMAND_LIST derived constant -----------------------------------------

def test_command_list_is_joined_bullet_form(bridget):
    cl = bridget.COMMAND_LIST
    assert isinstance(cl, str)
    # Each line should be a bullet of the form "• `<signature>` — <description>".
    for c in bridget.COMMANDS:
        expected = f"• `{c['signature']}` — {c['description']}"
        assert expected in cl, f'COMMAND_LIST missing bullet for {c["name"]!r}'


def test_command_list_preserves_status_description_verbatim(bridget):
    # The startup DM at watch_mailbox() prepends "Recognized replies:\n" +
    # COMMAND_LIST. The status description must keep the same text the rest
    # of the codebase tests for.
    cl = bridget.COMMAND_LIST
    assert 'work in flight' in cl
    assert 'Projects / Reports / Designs / Bugs / Tasks' in cl


def test_commands_by_name_lookup(bridget):
    by_name = bridget.COMMANDS_BY_NAME
    assert 'approve' in by_name
    assert by_name['approve']['signature'] == 'approve mg-XXXX (or dr-XXXX)'
    assert 'dismiss' in by_name
    assert 'dismiss all' in by_name
    assert 'help' in by_name


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
