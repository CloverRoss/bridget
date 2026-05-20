"""Tests for bug: [critical] direct-to-mayor routing.

Critical bugs filed via `bug: [critical] ...` should bypass architect
and land in mayor's queue (mg-64b1). Non-critical bugs continue to
route to architect.
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


class CapturingMg:
    """Records every run_mg call; returns success for 'new'."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args and args[0] == 'new':
            return 0, 'mg-abcd1234', ''
        return 0, '', ''

    def new_args(self) -> list[str]:
        for c in self.calls:
            if c and c[0] == 'new':
                return c
        raise AssertionError(f'no mg new call observed; calls={self.calls!r}')

    def assignee(self) -> str:
        for arg in self.new_args():
            if arg.startswith('--assignee='):
                return arg.split('=', 1)[1]
        raise AssertionError(f'no --assignee in new args: {self.new_args()!r}')

    def tags(self) -> list[str]:
        return [a.split('=', 1)[1] for a in self.new_args() if a.startswith('--tag=')]


def test_bug_critical_routes_to_mayor(bridget, monkeypatch):
    fake = CapturingMg()
    monkeypatch.setattr(bridget, 'run_mg', fake)
    reply = bridget.handle_command('bug: [critical] foo')
    assert fake.assignee() == 'mayor'
    assert 'critical' in fake.tags()
    assert 'routed to mayor' in reply
    assert '✓' in reply


def test_bug_without_critical_routes_to_architect(bridget, monkeypatch):
    fake = CapturingMg()
    monkeypatch.setattr(bridget, 'run_mg', fake)
    reply = bridget.handle_command('bug: foo')
    assert fake.assignee() == 'architect'
    assert 'routed to mayor' not in reply
    assert '✓' in reply


def test_bug_multitag_critical_routes_to_mayor(bridget, monkeypatch):
    fake = CapturingMg()
    monkeypatch.setattr(bridget, 'run_mg', fake)
    reply = bridget.handle_command('bug: [bridget] [critical] foo')
    assert fake.assignee() == 'mayor'
    tags = fake.tags()
    assert 'bridget' in tags
    assert 'critical' in tags
    assert 'routed to mayor' in reply


def test_bug_critical_case_insensitive(bridget, monkeypatch):
    fake = CapturingMg()
    monkeypatch.setattr(bridget, 'run_mg', fake)
    reply = bridget.handle_command('bug: [CRITICAL] foo')
    assert fake.assignee() == 'mayor'
    assert 'routed to mayor' in reply


def test_bug_critical_with_unrelated_tag_only_routes_to_architect(bridget, monkeypatch):
    fake = CapturingMg()
    monkeypatch.setattr(bridget, 'run_mg', fake)
    reply = bridget.handle_command('bug: [bridget] foo')
    assert fake.assignee() == 'architect'
    assert 'bridget' in fake.tags()
    assert 'routed to mayor' not in reply


def test_command_list_notes_critical_routing(bridget):
    # COMMAND_LIST entry for /bug: should explain the [critical] fast-path.
    cl = bridget.COMMAND_LIST
    bug_lines = [ln for ln in cl.splitlines() if '`/bug:' in ln]
    assert bug_lines, 'no /bug: entry in COMMAND_LIST'
    bug_line = bug_lines[0]
    assert '[critical]' in bug_line
    assert 'mayor' in bug_line


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
