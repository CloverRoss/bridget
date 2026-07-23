"""Tests for presence re-assert on ready/resume (mg-beb5).

The client never set a presence, so after sleep-driven gateway drops +
RESUMEs Discord could keep showing the bot as offline even though the
session delivered DMs fine (process healthy, on_ready firing, messages
flowing). The fix re-asserts `Status.online` via change_presence:

- in on_ready, on EVERY fire (including reconnect re-fires that the
  `_watchers_started` guard short-circuits),
- in a new on_resumed handler (RESUMEd sessions skip on_ready entirely),
- periodically from watch_mailbox as belt-and-braces.

Presence is cosmetic — a change_presence failure must be logged and
swallowed, never allowed to break the ready path or watchers.
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


def _install_fakes(bridget, monkeypatch):
    """Stand in for the discord client + watcher coroutines."""
    bridget._watchers_started = False
    fake_client = MagicMock()
    fake_client.user = MagicMock(id=42)
    fake_client.fetch_user = AsyncMock(return_value=MagicMock(id=1))
    fake_client.change_presence = AsyncMock()
    fake_client.loop = MagicMock()
    monkeypatch.setattr(bridget, 'client', fake_client)
    monkeypatch.setattr(bridget, 'watch_mailbox', MagicMock(return_value=object()))
    monkeypatch.setattr(bridget, 'watch_task_transitions', MagicMock(return_value=object()))
    monkeypatch.setattr(bridget, 'watch_idea_claims', MagicMock(return_value=object()))
    monkeypatch.setattr(bridget, 'watch_chat', MagicMock(return_value=object()))
    return fake_client


def test_on_ready_asserts_online_presence(bridget, monkeypatch):
    fake_client = _install_fakes(bridget, monkeypatch)
    asyncio.run(bridget.on_ready())
    fake_client.change_presence.assert_awaited_once()
    import discord
    assert fake_client.change_presence.await_args.kwargs['status'] is discord.Status.online


def test_on_ready_reconnect_refire_reasserts_presence(bridget, monkeypatch):
    # Reconnect re-fires short-circuit the watcher spawn via the
    # _watchers_started guard, but presence must be re-asserted every time —
    # the stale-offline bug happens exactly on reconnects.
    fake_client = _install_fakes(bridget, monkeypatch)
    asyncio.run(bridget.on_ready())
    asyncio.run(bridget.on_ready())
    assert fake_client.change_presence.await_count == 2


def test_on_resumed_reasserts_presence(bridget, monkeypatch):
    fake_client = _install_fakes(bridget, monkeypatch)
    asyncio.run(bridget.on_resumed())
    fake_client.change_presence.assert_awaited_once()
    import discord
    assert fake_client.change_presence.await_args.kwargs['status'] is discord.Status.online


def test_presence_failure_does_not_break_on_ready(bridget, monkeypatch):
    # Presence errors are cosmetic; the ready path (watcher spawn) must
    # complete even when change_presence raises.
    fake_client = _install_fakes(bridget, monkeypatch)
    fake_client.change_presence = AsyncMock(side_effect=RuntimeError('gateway sad'))
    asyncio.run(bridget.on_ready())
    assert bridget._watchers_started is True
    assert fake_client.loop.create_task.called


def test_presence_failure_does_not_break_on_resumed(bridget, monkeypatch):
    fake_client = _install_fakes(bridget, monkeypatch)
    fake_client.change_presence = AsyncMock(side_effect=RuntimeError('gateway sad'))
    asyncio.run(bridget.on_resumed())  # must not raise


def test_assert_presence_stamps_attempt_time_even_on_failure(bridget, monkeypatch):
    # The periodic re-assert in watch_mailbox keys off _presence_last_attempt.
    # Stamping on ATTEMPT (not success) keeps a persistent failure from
    # retrying every poll tick and spamming the log.
    fake_client = _install_fakes(bridget, monkeypatch)
    fake_client.change_presence = AsyncMock(side_effect=RuntimeError('gateway sad'))
    bridget._presence_last_attempt = 0.0
    asyncio.run(bridget._assert_presence('test'))
    assert bridget._presence_last_attempt > 0.0
