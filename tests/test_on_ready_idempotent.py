"""Tests for on_ready idempotence on Discord reconnect (mg-f7c5).

Discord re-fires on_ready on every gateway reconnect (e.g., laptop wake).
Without guarding, each fire spawned a fresh set of watcher tasks, so after
N reconnects the user received N× duplicate DMs of every agent mail. The
fix gates the spawn step on a module-level `_watchers_started` flag;
subsequent on_ready fires log a reconnect notice and return early. The
watcher coroutines themselves survive reconnects (they sleep between
ticks; the gateway reconnects under them), so only the spawn step needs
guarding.

The watcher count is whatever bridget currently spawns (4 as of mg-c05a:
watch_mailbox, watch_task_transitions, watch_idea_claims, watch_chat).
Tests reference EXPECTED_WATCHERS so adding a new watcher in the future
only requires bumping the constant, not chasing magic numbers.
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

# Count of background watchers on_ready spawns. Bump when adding a new
# watcher task in on_ready (mg-c05a: watch_chat brought this to 4;
# ia-5f66: _run_inject_api brought this to 5 — bumped in mg-beb5, the
# constant had gone stale).
EXPECTED_WATCHERS = 5


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
    """Stand in for the discord client + watcher coroutines.

    Returns the fake client so tests can assert on create_task.
    """
    bridget._watchers_started = False
    fake_client = MagicMock()
    fake_client.user = MagicMock(id=42)
    fake_client.fetch_user = AsyncMock(return_value=MagicMock(id=1))
    fake_client.loop = MagicMock()
    monkeypatch.setattr(bridget, 'client', fake_client)
    # Replace the watcher coroutines with sync stubs so the call expression
    # `watch_mailbox(user)` doesn't leave un-awaited coroutines hanging.
    monkeypatch.setattr(bridget, 'watch_mailbox', MagicMock(return_value=object()))
    monkeypatch.setattr(bridget, 'watch_task_transitions', MagicMock(return_value=object()))
    monkeypatch.setattr(bridget, 'watch_idea_claims', MagicMock(return_value=object()))
    monkeypatch.setattr(bridget, 'watch_chat', MagicMock(return_value=object()))
    return fake_client


def test_on_ready_first_fire_spawns_all_watchers(bridget, monkeypatch):
    fake_client = _install_fakes(bridget, monkeypatch)
    asyncio.run(bridget.on_ready())
    assert fake_client.loop.create_task.call_count == EXPECTED_WATCHERS
    assert bridget._watchers_started is True


def test_on_ready_second_fire_does_not_respawn(bridget, monkeypatch, capsys):
    fake_client = _install_fakes(bridget, monkeypatch)
    asyncio.run(bridget.on_ready())
    asyncio.run(bridget.on_ready())
    # Still exactly EXPECTED_WATCHERS create_task calls after the reconnect
    # re-fire — the guard short-circuits before the second batch of spawns.
    assert fake_client.loop.create_task.call_count == EXPECTED_WATCHERS
    # fetch_user must not run a second time either; it's the work the guard
    # is protecting against (along with the spawn step).
    assert fake_client.fetch_user.await_count == 1
    out = capsys.readouterr().out
    assert 'on_ready re-fired' in out


def test_on_ready_three_reconnects_still_one_set_of_watchers(bridget, monkeypatch):
    # The bug's pathological case: N reconnects → N×EXPECTED_WATCHERS without
    # the guard. Verify the guard holds across multiple re-fires.
    fake_client = _install_fakes(bridget, monkeypatch)
    for _ in range(4):
        asyncio.run(bridget.on_ready())
    assert fake_client.loop.create_task.call_count == EXPECTED_WATCHERS
