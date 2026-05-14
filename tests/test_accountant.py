"""Tests for the `accountant run-now` + `accountant status` commands
(ds-3123 / mg-b64f P2 #8).

`accountant run-now [<week>]` shells out to the auto-budget cycle
wrapper (mg-6064) via subprocess.Popen (fire-and-forget) so bridget
stays responsive while the cycle runs. The wrapper itself mails human
from the `accountant` sender on completion. Unlike `librarian sync`,
bridget does NOT create the lock file — the wrapper owns it. bridget
only checks for its presence to prevent piling cycles.

`accountant status` reads ~/.pogo/auto-budget.{log,err.log} (the
launchd plist log paths from mg-6064) and surfaces mtime + tail.

Coverage:
- valid run-now (no week) → Popen invoked with bare script path
- valid run-now with `YYYY-Www` week → script invoked with week arg
- invalid week format → reject, no Popen
- lock file already present → reject, no Popen
- Popen raises OSError → reply 'failed to start'
- status with no logs → 'no runs yet'
- status with logs present → mtime + tail in reply
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


@pytest.fixture
def lock_path(tmp_path, bridget, monkeypatch):
    """Per-test lock path so concurrent test runs don't collide."""
    p = tmp_path / 'budget-cycle.lock'
    monkeypatch.setattr(bridget, 'ACCOUNTANT_LOCK_PATH', str(p))
    return p


@pytest.fixture
def log_paths(tmp_path, bridget, monkeypatch):
    """Per-test log paths so we don't read the real ~/.pogo logs."""
    out = tmp_path / 'auto-budget.log'
    err = tmp_path / 'auto-budget.err.log'
    monkeypatch.setattr(bridget, 'ACCOUNTANT_LOG_PATH', out)
    monkeypatch.setattr(bridget, 'ACCOUNTANT_ERR_LOG_PATH', err)
    return out, err


def test_run_now_no_week_invokes_popen(bridget, lock_path):
    """`accountant run-now` (no week) → Popen with bare script path."""
    calls = []

    class FakeProc:
        pid = 9999

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return FakeProc()

    with mock.patch.object(bridget.subprocess, 'Popen', side_effect=fake_popen):
        reply = bridget.handle_command('accountant run-now')

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == [bridget.ACCOUNTANT_SCRIPT_PATH]
    assert kwargs.get('start_new_session') is True
    assert '🔄' in reply
    assert 'will DM when done' in reply
    # bridget must NOT create the lock — that's the wrapper's job.
    assert not lock_path.exists()


def test_run_now_with_week_passes_week_arg(bridget, lock_path):
    """`accountant run-now 2026-W18` → script invoked with the week."""
    calls = []

    def fake_popen(args, **kwargs):
        calls.append(args)
        return mock.MagicMock(pid=1)

    with mock.patch.object(bridget.subprocess, 'Popen', side_effect=fake_popen):
        reply = bridget.handle_command('accountant run-now 2026-W18')

    assert len(calls) == 1
    assert calls[0] == [bridget.ACCOUNTANT_SCRIPT_PATH, '2026-W18']
    assert '2026-W18' in reply
    assert '🔄' in reply


def test_run_now_rejects_invalid_week(bridget, lock_path):
    """Non-`YYYY-Www` week → usage reply, no Popen."""
    with mock.patch.object(bridget.subprocess, 'Popen') as popen:
        reply = bridget.handle_command('accountant run-now invalid')

    assert reply.startswith('Usage')
    assert 'YYYY-Www' in reply
    popen.assert_not_called()


def test_run_now_rejects_lowercase_w(bridget, lock_path):
    """`2026-w18` (lowercase w) doesn't match the ISO regex → reject."""
    with mock.patch.object(bridget.subprocess, 'Popen') as popen:
        reply = bridget.handle_command('accountant run-now 2026-w18')

    assert reply.startswith('Usage')
    popen.assert_not_called()


def test_run_now_rejects_when_lock_exists(bridget, lock_path):
    """Pre-existing lock file → reject with 'already running'."""
    lock_path.write_text('1234\n')

    with mock.patch.object(bridget.subprocess, 'Popen') as popen:
        reply = bridget.handle_command('accountant run-now')

    assert reply.startswith('✗')
    assert 'already running' in reply
    popen.assert_not_called()
    # The lock file is owned by the wrapper — bridget must not touch it.
    assert lock_path.read_text() == '1234\n'


def test_run_now_popen_failure_reports_error(bridget, lock_path):
    """If Popen raises OSError, reply with the failure reason."""

    def fake_popen(*args, **kwargs):
        raise OSError(2, 'no such file or directory')

    with mock.patch.object(bridget.subprocess, 'Popen', side_effect=fake_popen):
        reply = bridget.handle_command('accountant run-now')

    assert reply.startswith('✗')
    assert 'failed to start' in reply
    # bridget never created the lock so there's nothing to clean up.
    assert not lock_path.exists()


def test_status_no_runs_yet(bridget, log_paths):
    """No logs present → 'no runs yet'."""
    out, err = log_paths
    assert not out.exists() and not err.exists()

    reply = bridget.handle_command('accountant status')

    assert 'Budget cycle status' in reply
    assert 'no runs yet' in reply


def test_status_with_stdout_log(bridget, log_paths):
    """stdout log present → mtime + tail in reply."""
    out, err = log_paths
    out.write_text(
        'line 1\nline 2\nline 3\nline 4\nline 5\n'
        'line 6\nline 7\nfinal\n'
    )

    reply = bridget.handle_command('accountant status')

    assert 'Budget cycle status' in reply
    assert 'last stdout' in reply
    # Tail is last 5 lines, so 'line 4' onward.
    assert 'final' in reply
    assert 'line 4' in reply
    # Earlier lines outside the tail window are dropped.
    assert 'line 1' not in reply


def test_status_with_nonempty_err_log(bridget, log_paths):
    """err log with content → 'last stderr' + tail in reply."""
    out, err = log_paths
    out.write_text('ok\n')
    err.write_text('Traceback...\nBOOM\n')

    reply = bridget.handle_command('accountant status')

    assert 'last stdout' in reply
    assert 'last stderr' in reply
    assert 'BOOM' in reply


def test_status_omits_empty_err_log(bridget, log_paths):
    """Empty err log → omitted (only stdout section reported)."""
    out, err = log_paths
    out.write_text('ok\n')
    err.write_text('')  # exists but zero bytes

    reply = bridget.handle_command('accountant status')

    assert 'last stdout' in reply
    assert 'last stderr' not in reply


def test_run_now_command_is_in_help(bridget):
    """`help accountant run-now` returns the long description."""
    reply = bridget.handle_command('help accountant run-now')
    assert 'accountant run-now' in reply
    assert 'YYYY-Www' in reply


def test_status_command_is_in_help(bridget):
    """`help accountant status` returns the long description."""
    reply = bridget.handle_command('help accountant status')
    assert 'accountant status' in reply
    assert 'auto-budget.log' in reply


def test_accountant_signatures_in_command_menu(bridget):
    """Top-level `help` lists both accountant signatures (laptop profile)."""
    reply = bridget.handle_command('help')
    assert 'accountant run-now [<week>]' in reply
    assert 'accountant status' in reply
