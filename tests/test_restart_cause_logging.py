"""Tests for timestamped restart-cause logging (mg-5d0e / mg-7f65).

bridget restarted itself uncommanded with no trace — bridget.log had 31
"logged in as Bird Get" lines and no timestamps. The fix adds a UTC
timestamped `_log()` helper and emits diagnostic lines from:

- on_ready (so each launchd respawn is traceable)
- the restart command handler (so user-triggered restarts are explicit)
- Discord HTTPException catches (so racing close()s are visible)

These tests pin the call sites; they intentionally do not assert on the
timestamp prefix (that's the helper's contract, not the call site's).
"""
import asyncio
import importlib.util
import os
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / 'bridget'


def _load_bridget(home: Path):
    os.environ['HOME'] = str(home)
    env_dir = home / '.pogo'
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / 'bridget.env').write_text(
        'DISCORD_BOT_TOKEN=fake-token-for-tests\n'
        'DISCORD_USER_ID=12345\n'
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


def _install_on_ready_fakes(bridget, monkeypatch):
    bridget._watchers_started = False
    fake_client = MagicMock()
    fake_client.user = MagicMock(id=42)
    fake_client.fetch_user = AsyncMock(return_value=MagicMock(id=12345))
    fake_client.loop = MagicMock()
    monkeypatch.setattr(bridget, 'client', fake_client)
    monkeypatch.setattr(bridget, 'watch_mailbox', MagicMock(return_value=object()))
    monkeypatch.setattr(bridget, 'watch_task_transitions', MagicMock(return_value=object()))
    monkeypatch.setattr(bridget, 'watch_idea_claims', MagicMock(return_value=object()))
    return fake_client


# -- _log helper -----------------------------------------------------------

def test_log_writes_iso_utc_prefix_to_stderr(bridget, capsys):
    bridget._log('hello')
    captured = capsys.readouterr().err
    # ISO-8601 UTC prefix in square brackets, then the message.
    assert captured.startswith('[')
    assert '+00:00] hello' in captured


# -- on_ready --------------------------------------------------------------

def test_on_ready_logs_fired_with_pid_and_watchers_flag(bridget, monkeypatch):
    _install_on_ready_fakes(bridget, monkeypatch)
    calls = []
    monkeypatch.setattr(bridget, '_log', lambda msg: calls.append(msg))
    asyncio.run(bridget.on_ready())
    assert any('on_ready fired' in m for m in calls)
    assert any('pid=' in m for m in calls)
    assert any('_watchers_started=' in m for m in calls)


def test_on_ready_logs_on_every_fire_including_reconnect(bridget, monkeypatch):
    # The whole point of the log line is tracing reconnects/respawns — it
    # must fire on the re-entrant path too, not just the first call.
    _install_on_ready_fakes(bridget, monkeypatch)
    calls = []
    monkeypatch.setattr(bridget, '_log', lambda msg: calls.append(msg))
    asyncio.run(bridget.on_ready())
    asyncio.run(bridget.on_ready())
    fired = [m for m in calls if 'on_ready fired' in m]
    assert len(fired) == 2
    # Second fire shows _watchers_started=True (the guard state).
    assert '_watchers_started=True' in fired[1]


# -- restart command -------------------------------------------------------

def test_restart_command_logs_invocation_before_exit(bridget, monkeypatch):
    calls = []
    monkeypatch.setattr(bridget, '_log', lambda msg: calls.append(msg))
    # git pull, rev-parse, build.sh all succeed.
    monkeypatch.setattr(
        bridget, 'run_shell',
        lambda args, timeout=30, cwd=None: (0, 'abc1234\n', ''),
    )
    fake_client = MagicMock()
    fake_client.loop = MagicMock()
    monkeypatch.setattr(bridget, 'client', fake_client)

    reply = bridget.handle_command('restart')

    assert '✅ pulled' in reply
    # The log line must be emitted BEFORE call_later schedules the exit, so
    # the timestamp captures the user-triggered intent (not the racing
    # respawn's on_ready).
    assert any('restart command invoked' in m for m in calls)
    assert any('os._exit' in m for m in calls)
    assert any(str(bridget.USER_ID) in m for m in calls if 'restart command invoked' in m)
    fake_client.loop.call_later.assert_called_once()


def test_restart_command_does_not_log_when_git_pull_fails(bridget, monkeypatch):
    # No restart attempted → no restart-cause log line. Keeps the log signal
    # honest: a "restart command invoked" entry means we actually called
    # os._exit, not that the user typed `restart` and got bounced.
    calls = []
    monkeypatch.setattr(bridget, '_log', lambda msg: calls.append(msg))
    monkeypatch.setattr(
        bridget, 'run_shell',
        lambda args, timeout=30, cwd=None: (1, '', 'fatal: cannot pull'),
    )
    fake_client = MagicMock()
    monkeypatch.setattr(bridget, 'client', fake_client)

    reply = bridget.handle_command('restart')

    assert '❌ git pull failed' in reply
    assert not any('restart command invoked' in m for m in calls)
    fake_client.loop.call_later.assert_not_called()


# -- HTTPException catches -------------------------------------------------

def test_on_message_http_exception_logs_and_does_not_raise(bridget, monkeypatch):
    # Regression guard: existing behavior preserved — the HTTPException is
    # swallowed (no flow change); we just additionally get a timestamped
    # log line.
    import discord

    calls = []
    monkeypatch.setattr(bridget, '_log', lambda msg: calls.append(msg))
    monkeypatch.setattr(bridget, 'handle_command', lambda _t: 'pong')

    fake_response = MagicMock(status=500, reason='boom')
    err = discord.HTTPException(fake_response, 'send failed')

    message = MagicMock()
    message.author.bot = False
    message.author.id = bridget.USER_ID
    message.channel = MagicMock(spec=discord.DMChannel)
    message.content = 'ping'
    message.channel.send = AsyncMock(side_effect=err)

    # Must not raise — the existing except block catches.
    asyncio.run(bridget.on_message(message))

    assert any('HTTPException' in m and 'on_message' in m for m in calls)
