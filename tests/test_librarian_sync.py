"""Tests for the `librarian sync` command (mg-7c91 / mg-fea0 P1 #5).

`librarian sync <space> [page-id]` shells out to the confluence-ingestion
wrapper via subprocess.Popen (fire-and-forget) so bridget stays
responsive while the ingest runs. The wrapper itself mails human from
the `librarian` sender when the ingest finishes.

These tests verify the bridget side:
- valid space + optional page-id → Popen invoked with wrapped script
- invalid space key → reject without invoking Popen
- /tmp/librarian.lock already present → reject without invoking Popen
- successful spawn writes the lock file
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
    """Point the lock at a per-test path so concurrent test runs don't collide."""
    p = tmp_path / 'librarian.lock'
    monkeypatch.setattr(bridget, 'LIBRARIAN_LOCK_PATH', str(p))
    return p


def test_sync_space_invokes_popen_with_wrapper(bridget, lock_path):
    """`librarian sync MYSPACE` → Popen invoked with the wrapper script + space arg."""
    calls = []

    class FakeProc:
        pid = 9999

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return FakeProc()

    with mock.patch.object(bridget.subprocess, 'Popen', side_effect=fake_popen):
        reply = bridget.handle_command('librarian sync MYSPACE')

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == '/bin/sh'
    assert args[1] == '-c'
    assert bridget.LIBRARIAN_SCRIPT_PATH in args[2]
    assert '"MYSPACE"' in args[2]
    assert kwargs.get('start_new_session') is True
    assert '🔄' in reply
    assert 'MYSPACE' in reply
    assert lock_path.exists()


def test_sync_with_page_id_includes_page_arg(bridget, lock_path):
    """`librarian sync MYSPACE 12345` → wrapper command includes the page-id."""
    calls = []

    def fake_popen(args, **kwargs):
        calls.append(args)
        return mock.MagicMock(pid=1)

    with mock.patch.object(bridget.subprocess, 'Popen', side_effect=fake_popen):
        reply = bridget.handle_command('librarian sync MYSPACE 12345')

    assert len(calls) == 1
    cmd = calls[0][2]
    assert '"MYSPACE"' in cmd
    assert '"12345"' in cmd
    assert 'MYSPACE/12345' in reply


def test_sync_rejects_lowercase_space(bridget, lock_path):
    """Lowercase space key violates `^[A-Z][A-Z0-9_-]*$` → reject without Popen."""
    with mock.patch.object(bridget.subprocess, 'Popen') as popen:
        reply = bridget.handle_command('librarian sync myspace')

    assert reply.startswith('✗')
    assert 'invalid space key' in reply
    popen.assert_not_called()
    assert not lock_path.exists()


def test_sync_rejects_space_with_spaces(bridget, lock_path):
    """Multi-token space (treated as extra arg) → usage error or rejection."""
    with mock.patch.object(bridget.subprocess, 'Popen') as popen:
        reply = bridget.handle_command('librarian sync FOO BAR BAZ')

    assert reply.startswith('Usage') or reply.startswith('✗')
    popen.assert_not_called()


def test_sync_rejects_special_chars_in_space(bridget, lock_path):
    """Space key with disallowed chars (e.g. dot) → reject."""
    with mock.patch.object(bridget.subprocess, 'Popen') as popen:
        reply = bridget.handle_command('librarian sync MY.SPACE')

    assert reply.startswith('✗')
    assert 'invalid space key' in reply
    popen.assert_not_called()


def test_sync_rejects_non_numeric_page_id(bridget, lock_path):
    """page-id must be all digits → reject other forms."""
    with mock.patch.object(bridget.subprocess, 'Popen') as popen:
        reply = bridget.handle_command('librarian sync MYSPACE abc123')

    assert reply.startswith('✗')
    assert 'page-id' in reply
    popen.assert_not_called()


def test_sync_rejects_when_lock_exists(bridget, lock_path):
    """Pre-existing lock file → reject with 'ingest already running'."""
    lock_path.write_text('1234 OTHER\n')

    with mock.patch.object(bridget.subprocess, 'Popen') as popen:
        reply = bridget.handle_command('librarian sync MYSPACE')

    assert reply.startswith('✗')
    assert 'already running' in reply
    popen.assert_not_called()
    assert lock_path.read_text() == '1234 OTHER\n'


def test_sync_missing_space_returns_usage(bridget, lock_path):
    """Bare `librarian sync` (no args) → usage message."""
    with mock.patch.object(bridget.subprocess, 'Popen') as popen:
        reply = bridget.handle_command('librarian sync')

    assert reply.startswith('Usage')
    popen.assert_not_called()


def test_sync_popen_failure_releases_lock(bridget, lock_path):
    """If subprocess.Popen raises OSError, the lock file is removed so the user can retry."""

    def fake_popen(*args, **kwargs):
        raise OSError(2, 'no such file')

    with mock.patch.object(bridget.subprocess, 'Popen', side_effect=fake_popen):
        reply = bridget.handle_command('librarian sync MYSPACE')

    assert reply.startswith('✗')
    assert not lock_path.exists()


def test_sync_command_is_in_help(bridget):
    """`help librarian sync` returns the long description."""
    reply = bridget.handle_command('help librarian sync')
    assert 'librarian sync' in reply
    assert 'background' in reply.lower() or 'fire-and-forget' in reply.lower()


def test_sync_signature_in_command_menu(bridget):
    """Top-level `help` lists the librarian sync signature."""
    reply = bridget.handle_command('help')
    assert 'librarian sync <space> [page-id]' in reply
