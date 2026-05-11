"""Tests for `kickoff <id>` and `hold <id> [<reason>]` commands.

Covers, for each command:
- valid id success → mails mayor with from=human and the right subject/body.
- invalid id (mg show fails) → returns the "no such work item" error.
- missing id (bare command, or no mg-/dr- prefix) → returns the usage hint.
- COMMAND_LIST and help include both verbs.
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


class FakeMg:
    """Captures every run_mg call, returns canned show output."""

    def __init__(self, show_ok: bool = True):
        self.show_ok = show_ok
        self.calls: list[list[str]] = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args and args[0] == 'show':
            if self.show_ok:
                return 0, 'ID: mg-1\nType: task\nStatus: available\n', ''
            return 1, '', 'no such item'
        if args and args[0:2] == ['mail', 'send']:
            return 0, '', ''
        return 0, '', ''

    def mail_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ['mail', 'send']]


# -- kickoff -----------------------------------------------------------------

@pytest.mark.parametrize('prefix', ['mg-', 'dr-'])
def test_kickoff_valid_id_mails_mayor_with_empty_body(bridget, monkeypatch, prefix):
    fake = FakeMg(show_ok=True)
    monkeypatch.setattr(bridget, 'run_mg', fake)
    reply = bridget.handle_command(f'kickoff {prefix}1234')
    assert '✓' in reply, f'expected success reply, got: {reply!r}'
    assert f'{prefix}1234' in reply
    assert 'kickoff' in reply.lower()
    mails = fake.mail_calls()
    assert len(mails) == 1, f'expected one mail send, got: {fake.calls!r}'
    call = mails[0]
    assert call[2] == 'mayor', f'expected mayor target, got: {call[2]!r}'
    assert '--from=human' in call
    assert f'--subject=kickoff {prefix}1234' in call
    assert '--body=' in call  # empty body


def test_kickoff_invalid_id_returns_no_such_work_item(bridget, monkeypatch):
    fake = FakeMg(show_ok=False)
    monkeypatch.setattr(bridget, 'run_mg', fake)
    reply = bridget.handle_command('kickoff mg-deadbeef')
    assert '✗' in reply
    assert 'no such work item' in reply
    assert 'mg-deadbeef' in reply
    # No mail should have been sent on a failed id lookup.
    assert fake.mail_calls() == []


def test_kickoff_missing_id_returns_usage(bridget, monkeypatch):
    fake = FakeMg(show_ok=True)
    monkeypatch.setattr(bridget, 'run_mg', fake)
    reply = bridget.handle_command('kickoff')
    assert 'Usage' in reply
    assert 'kickoff' in reply
    assert fake.calls == []  # never called mg


def test_kickoff_bad_prefix_returns_usage(bridget, monkeypatch):
    fake = FakeMg(show_ok=True)
    monkeypatch.setattr(bridget, 'run_mg', fake)
    reply = bridget.handle_command('kickoff xx-1234')
    assert 'Usage' in reply
    assert fake.calls == []


# -- hold --------------------------------------------------------------------

@pytest.mark.parametrize('prefix', ['mg-', 'dr-'])
def test_hold_valid_id_with_reason_mails_mayor(bridget, monkeypatch, prefix):
    fake = FakeMg(show_ok=True)
    monkeypatch.setattr(bridget, 'run_mg', fake)
    reply = bridget.handle_command(f'hold {prefix}1234 waiting on legal')
    assert '✓' in reply
    assert f'{prefix}1234' in reply
    mails = fake.mail_calls()
    assert len(mails) == 1
    call = mails[0]
    assert call[2] == 'mayor'
    assert '--from=human' in call
    assert f'--subject=hold {prefix}1234' in call
    assert '--body=waiting on legal' in call


def test_hold_valid_id_no_reason_sends_empty_body(bridget, monkeypatch):
    fake = FakeMg(show_ok=True)
    monkeypatch.setattr(bridget, 'run_mg', fake)
    reply = bridget.handle_command('hold mg-1234')
    assert '✓' in reply
    mails = fake.mail_calls()
    assert len(mails) == 1
    call = mails[0]
    assert call[2] == 'mayor'
    assert '--from=human' in call
    assert '--subject=hold mg-1234' in call
    assert '--body=' in call  # empty body present


def test_hold_invalid_id_returns_no_such_work_item(bridget, monkeypatch):
    fake = FakeMg(show_ok=False)
    monkeypatch.setattr(bridget, 'run_mg', fake)
    reply = bridget.handle_command('hold mg-deadbeef because reasons')
    assert '✗' in reply
    assert 'no such work item' in reply
    assert 'mg-deadbeef' in reply
    assert fake.mail_calls() == []


def test_hold_missing_id_returns_usage(bridget, monkeypatch):
    fake = FakeMg(show_ok=True)
    monkeypatch.setattr(bridget, 'run_mg', fake)
    reply = bridget.handle_command('hold')
    assert 'Usage' in reply
    assert 'hold' in reply
    assert fake.calls == []


def test_hold_bad_prefix_returns_usage(bridget, monkeypatch):
    fake = FakeMg(show_ok=True)
    monkeypatch.setattr(bridget, 'run_mg', fake)
    reply = bridget.handle_command('hold xx-1234 because')
    assert 'Usage' in reply
    assert fake.calls == []


# -- COMMAND_LIST + help -----------------------------------------------------

def test_command_list_mentions_kickoff_and_hold(bridget):
    cl = bridget.COMMAND_LIST
    assert 'kickoff' in cl
    assert 'hold' in cl


def test_help_includes_kickoff_and_hold(bridget):
    reply = bridget.handle_command('help')
    assert 'kickoff' in reply
    assert 'hold' in reply


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
