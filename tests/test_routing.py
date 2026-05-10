"""Tests for type=report routing + dr- prefix support.

Covers:
- route_recipient() unit tests (mg show failure, missing Type line, type values).
- 16-combo matrix for approve/reject/revise/explain × architect/director ×
  mg-/dr- prefixes — verifies the mail send target.
- read/dismiss accept the dr- prefix (no routing logic, just prefix relaxation).
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


# -- route_recipient unit tests ---------------------------------------------

def test_route_recipient_mg_show_failure_falls_back_to_architect(bridget, monkeypatch, capsys):
    def fake_run_mg(args):
        return 1, '', 'no such item'
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    assert bridget.route_recipient('mg-deadbeef') == 'architect'
    err = capsys.readouterr().err
    assert 'route_recipient' in err
    assert 'mg-deadbeef' in err


def test_route_recipient_no_type_line_falls_back_to_architect(bridget, monkeypatch, capsys):
    def fake_run_mg(args):
        return 0, 'ID: mg-1234\nTitle: something\nStatus: available\n', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    assert bridget.route_recipient('mg-1234') == 'architect'
    err = capsys.readouterr().err
    assert 'no Type:' in err


def test_route_recipient_type_report_routes_to_director(bridget, monkeypatch):
    def fake_run_mg(args):
        return 0, 'ID: mg-1\nType: report\nStatus: available\n', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    assert bridget.route_recipient('mg-1') == 'director'


def test_route_recipient_type_report_with_surrounding_whitespace(bridget, monkeypatch):
    # The regex captures up to whitespace; group(1).strip() handles trailing space.
    # Test that "Type:  report  \n" still routes correctly (extra whitespace).
    def fake_run_mg(args):
        return 0, 'ID: mg-1\nType:   report\nStatus: available\n', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    assert bridget.route_recipient('mg-1') == 'director'


def test_route_recipient_type_idea_routes_to_architect(bridget, monkeypatch):
    def fake_run_mg(args):
        return 0, 'ID: mg-1\nType: idea\nStatus: available\n', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    assert bridget.route_recipient('mg-1') == 'architect'


def test_route_recipient_type_bug_routes_to_architect(bridget, monkeypatch):
    def fake_run_mg(args):
        return 0, 'ID: mg-1\nType: bug\nStatus: available\n', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    assert bridget.route_recipient('mg-1') == 'architect'


def test_route_recipient_type_task_routes_to_architect(bridget, monkeypatch):
    def fake_run_mg(args):
        return 0, 'ID: mg-1\nType: task\nStatus: available\n', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    assert bridget.route_recipient('mg-1') == 'architect'


def test_route_recipient_unknown_type_routes_to_architect(bridget, monkeypatch):
    # Non-matching but valid type values (e.g. junk/typo) → architect (safe default).
    def fake_run_mg(args):
        return 0, 'ID: mg-1\nType: task-extra-junk\nStatus: available\n', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    assert bridget.route_recipient('mg-1') == 'architect'


def test_route_recipient_case_sensitive_Report_routes_to_architect(bridget, monkeypatch):
    # Strict equality — capital R or other casing must NOT match.
    def fake_run_mg(args):
        return 0, 'ID: mg-1\nType: Report\nStatus: available\n', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    assert bridget.route_recipient('mg-1') == 'architect'


# -- handle_command routing matrix ------------------------------------------
#
# For each routed command × routing-state × prefix, verify the recipient
# argument passed to `run_mg(['mail', 'send', <target>, ...])`.

class FakeMg:
    """Captures every run_mg call, returns canned show output for 'show'."""

    def __init__(self, show_type: str):
        self.show_type = show_type
        self.calls: list[list[str]] = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args and args[0] == 'show':
            if self.show_type is None:
                return 0, 'ID: mg-1\nStatus: available\n', ''  # no Type line
            return 0, f'ID: mg-1\nType: {self.show_type}\nStatus: available\n', ''
        if args and args[0:2] == ['mail', 'send']:
            return 0, '', ''
        if args and args[0] == 'unshelve':
            return 0, '', ''
        return 0, '', ''

    def mail_target(self) -> str:
        for c in self.calls:
            if c[:2] == ['mail', 'send']:
                return c[2]
        raise AssertionError(f'no mail send call observed; calls={self.calls!r}')


@pytest.mark.parametrize('verb,extra', [
    ('approve', ''),
    ('reject', ' because reasons'),
    ('revise', ' tweak this'),
    ('explain', ' the part about X'),
])
@pytest.mark.parametrize('show_type,expected_target', [
    ('idea', 'architect'),
    ('bug', 'architect'),
    ('task', 'architect'),
    ('report', 'director'),
])
@pytest.mark.parametrize('prefix', ['mg-', 'dr-'])
def test_routed_commands_matrix(bridget, monkeypatch, verb, extra, show_type, expected_target, prefix):
    fake = FakeMg(show_type=show_type)
    monkeypatch.setattr(bridget, 'run_mg', fake)
    # mark_mail_read writes to disk under HOME; harmless but ignore the count.
    monkeypatch.setattr(bridget, 'mark_mail_read', lambda **_kwargs: 0)
    cmd = f'{verb} {prefix}1234{extra}'
    reply = bridget.handle_command(cmd)
    assert '✓' in reply, f'expected success reply, got: {reply!r}'
    assert fake.mail_target() == expected_target, (
        f'verb={verb} prefix={prefix} type={show_type}: '
        f'expected {expected_target}, got {fake.mail_target()}'
    )


# -- prefix smoke tests for read / dismiss ----------------------------------

def test_dismiss_accepts_dr_prefix(bridget, monkeypatch):
    monkeypatch.setattr(bridget, 'mark_mail_read', lambda **_kwargs: 3)
    monkeypatch.setattr(bridget, 'log_mail_action', lambda *_a, **_k: None)
    reply = bridget.handle_command('dismiss dr-abcd')
    assert '✓' in reply
    assert 'dr-abcd' in reply


def test_dismiss_accepts_mg_prefix(bridget, monkeypatch):
    monkeypatch.setattr(bridget, 'mark_mail_read', lambda **_kwargs: 2)
    monkeypatch.setattr(bridget, 'log_mail_action', lambda *_a, **_k: None)
    reply = bridget.handle_command('dismiss mg-abcd')
    assert '✓' in reply
    assert 'mg-abcd' in reply


def test_dismiss_rejects_unknown_prefix(bridget):
    reply = bridget.handle_command('dismiss xx-abcd')
    assert 'Usage' in reply


def test_read_accepts_dr_prefix(bridget, monkeypatch):
    # find_mails_for returns no matches → bridget replies with the "no mail" line.
    # The prefix is accepted (no Usage error) iff this path is reached.
    monkeypatch.setattr(bridget, 'find_mails_for', lambda _mg_id: [])
    reply = bridget.handle_command('read dr-abcd')
    assert 'Usage' not in reply
    assert 'dr-abcd' in reply


def test_read_accepts_mg_prefix(bridget, monkeypatch):
    monkeypatch.setattr(bridget, 'find_mails_for', lambda _mg_id: [])
    reply = bridget.handle_command('read mg-abcd')
    assert 'Usage' not in reply
    assert 'mg-abcd' in reply


# -- COMMAND_LIST mentions both prefix forms --------------------------------

def test_command_list_mentions_dr_prefix(bridget):
    cl = bridget.COMMAND_LIST
    # Each of the five id-bearing commands should reference dr-XXXX.
    for verb in ('approve', 'reject', 'revise', 'explain', 'read', 'dismiss'):
        assert verb in cl, f'COMMAND_LIST missing verb {verb!r}'
    assert 'dr-XXXX' in cl


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
