"""Tests for the `nudge` command (mg-8ef8 / mg-cb95).

`pogo nudge` defaults to waiting for the agent to be idle (30s timeout)
and fails if the target is mid-cycle. The bridget nudge command must
pass `--immediate` so the message lands regardless of agent state.
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
def bridget(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    return _load_bridget(tmp_path)


def test_nudge_passes_immediate_flag(bridget):
    """nudge mayor check → run_pogo called with ['nudge', '--immediate', 'mayor', 'check']."""
    calls = []

    def fake_run_pogo(args):
        calls.append(args)
        if args[0] == 'nudge':
            return 0, '', ''
        if args[0] == 'agent' and args[1] == 'diagnose':
            return 1, '', ''
        return 0, '', ''

    with mock.patch.object(bridget, 'run_pogo', side_effect=fake_run_pogo):
        reply = bridget.handle_command('nudge mayor check')

    assert calls[0] == ['nudge', '--immediate', 'mayor', 'check']
    assert reply.startswith('✓ nudged mayor')


def test_nudge_default_reason_passes_immediate(bridget):
    """nudge mayor (no reason) → uses default 'status check' with --immediate."""
    calls = []

    def fake_run_pogo(args):
        calls.append(args)
        if args[0] == 'nudge':
            return 0, '', ''
        return 1, '', ''

    with mock.patch.object(bridget, 'run_pogo', side_effect=fake_run_pogo):
        reply = bridget.handle_command('nudge mayor')

    assert calls[0] == ['nudge', '--immediate', 'mayor', 'status check']
    assert reply.startswith('✓ nudged mayor')


def test_nudge_failure_returns_error(bridget):
    """pogo nudge rc!=0 → bridget reply reports failure."""
    def fake_run_pogo(args):
        if args[0] == 'nudge':
            return 1, '', 'no such agent'
        return 1, '', ''

    with mock.patch.object(bridget, 'run_pogo', side_effect=fake_run_pogo):
        reply = bridget.handle_command('nudge ghost check')

    assert reply.startswith('✗ nudge failed')
    assert 'no such agent' in reply


def test_nudge_credit_balance_warning_still_surfaces(bridget):
    """When recent agent tail shows credit-balance error, nudge reply
    surfaces the ⚠️ warning even though pogo nudge succeeded.

    Regression guard for the diagnose-tail check at bridget:1894.
    """
    diag_out = (
        '{"recent_output_tail": "Error: your credit balance is too low to '
        'access the Anthropic API"}'
    )

    def fake_run_pogo(args):
        if args[0] == 'nudge':
            assert args == ['nudge', '--immediate', 'mayor', 'check']
            return 0, '', ''
        if args[0] == 'agent' and args[1] == 'diagnose':
            return 0, diag_out, ''
        return 1, '', ''

    with mock.patch.object(bridget, 'run_pogo', side_effect=fake_run_pogo):
        reply = bridget.handle_command('nudge mayor check')

    assert '⚠️' in reply
    assert 'credit balance' in reply
