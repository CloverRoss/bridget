"""Tests for Assignee-based routing + dr- prefix support.

Covers:
- route_recipient() unit tests (mg show failure, missing Assignee line, assignee values).
- approve/reject/revise/explain × architect/director/mayor × mg-/dr- prefixes —
  verifies the mail send target is the mg item's Assignee.
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


def test_route_recipient_no_assignee_line_falls_back_to_architect(bridget, monkeypatch, capsys):
    def fake_run_mg(args):
        return 0, 'ID: mg-1234\nTitle: something\nStatus: available\n', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    assert bridget.route_recipient('mg-1234') == 'architect'
    err = capsys.readouterr().err
    assert 'no Assignee:' in err


def test_route_recipient_assignee_director_routes_to_director(bridget, monkeypatch):
    # Type=report assigned to director → director (canonical case).
    def fake_run_mg(args):
        return 0, 'ID: mg-1\nType: report\nAssignee: director\nStatus: available\n', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    assert bridget.route_recipient('mg-1') == 'director'


def test_route_recipient_assignee_architect_routes_to_architect(bridget, monkeypatch):
    # Type=idea assigned to architect → architect.
    def fake_run_mg(args):
        return 0, 'ID: mg-1\nType: idea\nAssignee: architect\nStatus: available\n', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    assert bridget.route_recipient('mg-1') == 'architect'


def test_route_recipient_assignee_mayor_routes_to_mayor(bridget, monkeypatch):
    # Director-Flow handoff: Type=report reassigned to mayor → mayor (was previously
    # misrouted to director under the Type=report heuristic).
    def fake_run_mg(args):
        return 0, 'ID: mg-1\nType: report\nAssignee: mayor\nStatus: available\n', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    assert bridget.route_recipient('mg-1') == 'mayor'


def test_route_recipient_assignee_with_surrounding_whitespace(bridget, monkeypatch):
    # The regex captures up to whitespace; group(1).strip() handles trailing space.
    def fake_run_mg(args):
        return 0, 'ID: mg-1\nAssignee:    director  \nStatus: available\n', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    assert bridget.route_recipient('mg-1') == 'director'


def test_route_recipient_returns_assignee_verbatim(bridget, monkeypatch):
    # Whatever the Assignee field says is what gets returned — no hardcoded
    # allowlist. Future agents (or polecat names) route automatically.
    def fake_run_mg(args):
        return 0, 'ID: mg-1\nAssignee: some-future-agent\nStatus: available\n', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    assert bridget.route_recipient('mg-1') == 'some-future-agent'


# -- handle_command routing matrix ------------------------------------------
#
# For each routed command × routing-state × prefix, verify the recipient
# argument passed to `run_mg(['mail', 'send', <target>, ...])`.

class FakeMg:
    """Captures every run_mg call, returns canned show output for 'show'."""

    def __init__(self, show_assignee: str):
        self.show_assignee = show_assignee
        self.calls: list[list[str]] = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args and args[0] == 'show':
            if self.show_assignee is None:
                return 0, 'ID: mg-1\nStatus: available\n', ''  # no Assignee line
            return 0, f'ID: mg-1\nAssignee: {self.show_assignee}\nStatus: available\n', ''
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
@pytest.mark.parametrize('assignee,expected_target', [
    ('architect', 'architect'),
    ('director', 'director'),
    ('mayor', 'mayor'),
])
@pytest.mark.parametrize('prefix', ['mg-', 'dr-'])
def test_routed_commands_matrix(bridget, monkeypatch, verb, extra, assignee, expected_target, prefix):
    fake = FakeMg(show_assignee=assignee)
    monkeypatch.setattr(bridget, 'run_mg', fake)
    # mark_mail_read writes to disk under HOME; harmless but ignore the count.
    monkeypatch.setattr(bridget, 'mark_mail_read', lambda **_kwargs: 0)
    cmd = f'{verb} {prefix}1234{extra}'
    reply = bridget.handle_command(cmd)
    assert '✓' in reply, f'expected success reply, got: {reply!r}'
    assert fake.mail_target() == expected_target, (
        f'verb={verb} prefix={prefix} assignee={assignee}: '
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


def test_read_mg_prefix_hints_to_open(bridget, monkeypatch):
    # mg-d3d7: `read mg-XXXX` no longer routes into the design/mail combined
    # surface — it redirects users to `open mg-XXXX`. The prefix is still
    # recognized (no Usage error), but the response is a hint.
    monkeypatch.setattr(bridget, 'find_mails_for', lambda _mg_id: [])
    reply = bridget.handle_command('read mg-abcd')
    assert 'Usage' not in reply
    assert 'open mg-XXXX' in reply
    assert 'mail message-ids' in reply


# -- COMMAND_LIST mentions both prefix forms --------------------------------

def test_command_list_mentions_dr_prefix(bridget):
    cl = bridget.COMMAND_LIST
    # Each of the five id-bearing commands should reference dr-XXXX.
    for verb in ('approve', 'reject', 'revise', 'explain', 'read', 'dismiss'):
        assert verb in cl, f'COMMAND_LIST missing verb {verb!r}'
    assert 'dr-XXXX' in cl


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
