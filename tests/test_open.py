"""Tests for the `open mg-XXXX` command (mg-10e2 / mg-6245).

Post-mg-10e2 the `open` verb no longer reads iCloud-stored design docs. It
returns a Notes-app pointer of the form
`Notes app → Pogo Designs → **<id>: <title>**` so the user can locate the
note the architect publishes on each design save (closes the macOS TCC
blocker for `~/Library/Mobile Documents` and the Discord DM truncation
issue). Title is sourced from `mg show <id>`; if mg show fails the reply
is `open: mg item <id> not found`.
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


# -- happy path -------------------------------------------------------------

def test_open_returns_notes_pointer_with_title_from_mg_show(bridget, monkeypatch):
    monkeypatch.setattr(
        bridget,
        'run_mg',
        lambda args: (0, 'ID:        mg-aaaa\nTitle:     a nice design\n', ''),
    )
    reply = bridget.handle_command('open mg-aaaa')
    assert reply == 'Notes app → Pogo Designs → **mg-aaaa: a nice design**'


def test_open_is_case_insensitive_on_id(bridget, monkeypatch):
    captured = {}

    def fake_run_mg(args):
        captured['args'] = args
        return 0, 'Title: t\n', ''

    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    reply = bridget.handle_command('open MG-C0DE')
    assert reply == 'Notes app → Pogo Designs → **mg-c0de: t**'
    # mg show is called with the lowercased id so downstream tooling sees
    # the canonical form.
    assert captured['args'] == ['show', 'mg-c0de']


def test_open_falls_back_to_question_mark_when_title_missing(bridget, monkeypatch):
    # mg show succeeds but the output has no `Title:` line (unlikely, but
    # guard against malformed mg fixtures).
    monkeypatch.setattr(
        bridget,
        'run_mg',
        lambda args: (0, 'ID:        mg-bbbb\nStatus:    claimed\n', ''),
    )
    reply = bridget.handle_command('open mg-bbbb')
    assert reply == 'Notes app → Pogo Designs → **mg-bbbb: ?**'


# -- mg show failure -------------------------------------------------------

def test_open_mg_show_failure_returns_not_found(bridget, monkeypatch):
    monkeypatch.setattr(
        bridget,
        'run_mg',
        lambda args: (1, '', 'no such work item'),
    )
    reply = bridget.handle_command('open mg-dead')
    assert reply == 'open: mg item mg-dead not found'


# -- usage / validation -----------------------------------------------------

def test_open_bare_returns_usage(bridget):
    reply = bridget.handle_command('open')
    assert 'Usage' in reply
    assert 'open mg-XXXX' in reply


def test_open_non_mg_id_returns_usage(bridget):
    reply = bridget.handle_command('open notanid')
    assert 'Usage' in reply
    assert 'open mg-XXXX' in reply


def test_open_dr_id_returns_usage(bridget):
    # `open` is design-only (mg-id). dr- reports are out of scope; the regex
    # restricts the prefix to `mg-`.
    reply = bridget.handle_command('open dr-abcd')
    assert 'Usage' in reply


def test_open_non_hex_chars_returns_usage(bridget):
    reply = bridget.handle_command('open mg-xyz!')
    assert 'Usage' in reply


def test_open_does_not_invoke_mg_show_for_invalid_id(bridget, monkeypatch):
    # Guard: validation rejects non-mg-ids before we shell out to `mg show`.
    def boom(_args):
        raise AssertionError('run_mg must not be called for invalid ids')

    monkeypatch.setattr(bridget, 'run_mg', boom)
    bridget.handle_command('open notanid')
    bridget.handle_command('open dr-abcd')
    bridget.handle_command('open')


# -- help integration -------------------------------------------------------

def test_help_open_describes_notes_pointer(bridget):
    reply = bridget.handle_command('help open')
    low = reply.lower()
    assert 'notes' in low
    assert 'pogo designs' in low


def test_help_menu_lists_open(bridget):
    reply = bridget.handle_command('help')
    assert 'open mg-XXXX' in reply


def test_command_list_includes_open(bridget):
    cl = bridget.COMMAND_LIST
    assert '`open mg-XXXX`' in cl


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
