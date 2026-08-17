"""Tests for DM gap backfill on reconnect (mg-c1d5, amended).

THE BUG THIS REPRODUCES. On 2026-08-17 Clover sent mayor two messages and
neither ever arrived; they were permanently lost. Outbound was working the
whole time — she received mayor's replies — and every process-level signal
said healthy. The gateway websocket had died, and **Discord does not
re-deliver DMs that arrived while the bot was disconnected**: a fresh
IDENTIFY starts the event stream at now.

So detecting the dead gateway and kickstarting the bridge is not a fix. It
shortens the outage and makes it visible, but every message sent during the
gap is still gone. The fix is to stop trusting the live stream to be
complete: persist the id of the last message actually processed and, on every
reconnect, fetch the channel's own history AFTER that id and replay what was
missed.

`test_message_sent_during_the_gap_is_recovered_on_reconnect` below is the
literal reproduction: disconnect, a message arrives while disconnected,
reconnect — and it must be processed. If that test fails, the bug is back.
"""
import asyncio
import importlib.util
import json
import os
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / 'bridget'

CHANNEL_ID = 555000111


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
    mod = _load_bridget(tmp_path)
    mod.DM_WATERMARK_FILE = tmp_path / '.pogo' / 'bridget-dm-watermark.json'
    mod._dm_processed_ids.clear()
    mod._dm_processed_id_set.clear()
    mod._dm_backfill_running = False
    return mod


def _msg(bridget, msg_id, content='hello', *, from_user=True, bot=False):
    import discord
    m = MagicMock()
    m.id = msg_id
    m.content = content
    m.attachments = []
    m.author.bot = bot
    m.author.id = bridget.USER_ID if from_user else 999
    m.created_at = MagicMock()
    m.created_at.isoformat.return_value = '2026-08-17T06:40:00+00:00'
    m.channel = MagicMock(spec=discord.DMChannel)
    m.channel.id = CHANNEL_ID
    m.channel.send = AsyncMock()
    return m


class FakeHistory:
    """Stand-in for discord.py's channel.history(...) async iterator."""

    def __init__(self, channel):
        self.channel = channel

    def __call__(self, *, after=None, oldest_first=False, limit=None):
        msgs = sorted(self.channel._messages, key=lambda m: m.id)
        if after is not None:
            msgs = [m for m in msgs if m.id > after.id]
        if not oldest_first:
            msgs = list(reversed(msgs))
        if limit is not None:
            msgs = msgs[:limit]
        self.channel._history_calls.append(
            {'after': getattr(after, 'id', None),
             'oldest_first': oldest_first, 'limit': limit}
        )

        async def _gen():
            for m in msgs:
                yield m
        return _gen()


def _fake_channel(messages):
    import discord
    ch = MagicMock(spec=discord.DMChannel)
    ch.id = CHANNEL_ID
    ch._messages = list(messages)
    ch._history_calls = []
    ch.history = FakeHistory(ch)
    ch.send = AsyncMock()
    return ch


def _install_client(bridget, monkeypatch, channel):
    user = MagicMock()
    user.dm_channel = channel
    user.create_dm = AsyncMock(return_value=channel)
    client = MagicMock()
    client.get_user = MagicMock(return_value=user)
    client.fetch_user = AsyncMock(return_value=user)
    monkeypatch.setattr(bridget, 'client', client)
    return client


@pytest.fixture
def dispatched(bridget, monkeypatch):
    """Record every message that reaches the shared DM handler."""
    seen = []

    async def fake_dispatch(message, source):
        seen.append((message.id, source))

    monkeypatch.setattr(bridget, '_dispatch_dm', fake_dispatch)
    return seen


# --- THE REPRODUCTION --------------------------------------------------------

def test_message_sent_during_the_gap_is_recovered_on_reconnect(
        bridget, monkeypatch, dispatched):
    """Clover's bug, end to end.

    m100 arrives normally. The gateway then dies; m101 and m102 arrive while
    the bot is disconnected, so no on_message ever fires for them — Discord
    will never deliver them. On reconnect both must be recovered, in order.
    """
    m100 = _msg(bridget, 100, 'seen before the outage')
    asyncio.run(bridget.on_message(m100))
    assert dispatched == [(100, 'on_message')]
    assert bridget.load_dm_watermark(CHANNEL_ID) == 100

    # --- the gateway dies here. Two messages arrive with nobody listening. ---
    m101 = _msg(bridget, 101, 'mayor are you there')
    m102 = _msg(bridget, 102, 'hello?')
    channel = _fake_channel([m100, m101, m102])
    _install_client(bridget, monkeypatch, channel)

    # --- reconnect ---
    recovered = asyncio.run(bridget._backfill_dm_gap('on_ready'))

    assert recovered == 2
    assert dispatched == [
        (100, 'on_message'),
        (101, 'backfill/on_ready'),
        (102, 'backfill/on_ready'),
    ], 'both gap messages must be recovered, oldest first'
    assert bridget.load_dm_watermark(CHANNEL_ID) == 102


def test_backfill_asks_discord_only_for_what_it_missed(
        bridget, monkeypatch, dispatched):
    bridget.save_dm_watermark(CHANNEL_ID, 100)
    channel = _fake_channel([_msg(bridget, i) for i in (98, 99, 100, 101)])
    _install_client(bridget, monkeypatch, channel)
    asyncio.run(bridget._backfill_dm_gap('on_ready'))
    call = channel._history_calls[0]
    assert call['after'] == 100
    assert call['oldest_first'] is True
    assert call['limit'] == bridget.DM_BACKFILL_LIMIT
    assert [i for i, _ in dispatched] == [101]


def test_nothing_missed_means_nothing_replayed(bridget, monkeypatch, dispatched):
    bridget.save_dm_watermark(CHANNEL_ID, 100)
    channel = _fake_channel([_msg(bridget, 100)])
    _install_client(bridget, monkeypatch, channel)
    assert asyncio.run(bridget._backfill_dm_gap('on_ready')) == 0
    assert dispatched == []


# --- idempotence -------------------------------------------------------------

def test_backfill_twice_processes_a_message_once(bridget, monkeypatch, dispatched):
    bridget.save_dm_watermark(CHANNEL_ID, 100)
    channel = _fake_channel([_msg(bridget, 101)])
    _install_client(bridget, monkeypatch, channel)
    asyncio.run(bridget._backfill_dm_gap('on_ready'))
    asyncio.run(bridget._backfill_dm_gap('on_resumed'))
    assert [i for i, _ in dispatched] == [101]


def test_live_stream_does_not_redeliver_what_backfill_already_handled(
        bridget, monkeypatch, dispatched):
    """The race the watermark alone cannot cover: a message the backfill just
    replayed also arrives on the live stream."""
    bridget.save_dm_watermark(CHANNEL_ID, 100)
    m101 = _msg(bridget, 101)
    channel = _fake_channel([m101])
    _install_client(bridget, monkeypatch, channel)
    asyncio.run(bridget._backfill_dm_gap('on_ready'))
    asyncio.run(bridget.on_message(m101))
    assert [i for i, _ in dispatched] == [101]


def test_backfill_does_not_redeliver_what_the_live_stream_handled(
        bridget, monkeypatch, dispatched):
    m101 = _msg(bridget, 101)
    asyncio.run(bridget.on_message(m101))
    channel = _fake_channel([m101])
    _install_client(bridget, monkeypatch, channel)
    assert asyncio.run(bridget._backfill_dm_gap('on_ready')) == 0
    assert [i for i, _ in dispatched] == [101]


def test_concurrent_backfills_do_not_overlap(bridget, monkeypatch, dispatched):
    bridget.save_dm_watermark(CHANNEL_ID, 100)
    channel = _fake_channel([_msg(bridget, 101)])
    _install_client(bridget, monkeypatch, channel)

    async def both():
        return await asyncio.gather(
            bridget._backfill_dm_gap('on_ready'),
            bridget._backfill_dm_gap('on_resumed'),
        )

    asyncio.run(both())
    assert [i for i, _ in dispatched] == [101]


# --- what does and does not count as a message to replay ---------------------

def test_bot_and_stranger_messages_advance_the_mark_without_being_replayed(
        bridget, monkeypatch, dispatched):
    """Our own outbound DMs sit in this channel too. Skipping them is right,
    but the watermark must still move past them or every reconnect re-scans
    the same range forever."""
    bridget.save_dm_watermark(CHANNEL_ID, 100)
    channel = _fake_channel([
        _msg(bridget, 101, bot=True),
        _msg(bridget, 102, from_user=False),
        _msg(bridget, 103),
    ])
    _install_client(bridget, monkeypatch, channel)
    assert asyncio.run(bridget._backfill_dm_gap('on_ready')) == 1
    assert [i for i, _ in dispatched] == [103]
    assert bridget.load_dm_watermark(CHANNEL_ID) == 103


def test_replay_uses_the_same_handler_as_the_live_stream(bridget, monkeypatch):
    """A backfilled message must be treated exactly like a live one, or the
    replay quietly behaves differently from the thing it replays."""
    src = SCRIPT.read_text()
    live = src.split('async def on_message')[1]
    assert '_dispatch_dm(message,' in live
    backfill = src.split('async def _backfill_dm_gap_inner')[1].split(
        '\n@client.event')[0]
    assert '_dispatch_dm(msg,' in backfill


# --- the loud-failure rule ---------------------------------------------------

def test_missing_watermark_is_loud_and_does_not_replay_history(
        bridget, monkeypatch, dispatched):
    """A silent fallback to 'start from now' is exactly how the gap became
    invisible in the first place. Seeding is correct on a first run —
    replaying an entire DM history would re-run every command in it — but it
    must be announced, not swallowed."""
    logs = []
    monkeypatch.setattr(bridget, '_log', logs.append)
    channel = _fake_channel([_msg(bridget, i) for i in (98, 99, 100)])
    _install_client(bridget, monkeypatch, channel)
    assert asyncio.run(bridget._backfill_dm_gap('on_ready')) == 0
    assert dispatched == []
    assert any('NO WATERMARK' in m for m in logs)
    assert bridget.load_dm_watermark(CHANNEL_ID) == 100


def test_corrupt_watermark_file_reads_as_unknown_and_says_so(bridget, monkeypatch):
    logs = []
    monkeypatch.setattr(bridget, '_log', logs.append)
    bridget.DM_WATERMARK_FILE.parent.mkdir(parents=True, exist_ok=True)
    bridget.DM_WATERMARK_FILE.write_text('{not json')
    assert bridget.load_dm_watermark(CHANNEL_ID) is None
    assert any('unreadable' in m for m in logs)


def test_hitting_the_backfill_cap_is_loud(bridget, monkeypatch, dispatched):
    logs = []
    monkeypatch.setattr(bridget, '_log', logs.append)
    monkeypatch.setattr(bridget, 'DM_BACKFILL_LIMIT', 2)
    bridget.save_dm_watermark(CHANNEL_ID, 100)
    channel = _fake_channel([_msg(bridget, i) for i in (101, 102, 103)])
    _install_client(bridget, monkeypatch, channel)
    asyncio.run(bridget._backfill_dm_gap('on_ready'))
    assert any('cap' in m and 'NOT replayed' in m for m in logs)


def test_history_failure_leaves_the_watermark_for_the_next_try(
        bridget, monkeypatch, dispatched):
    import discord
    logs = []
    monkeypatch.setattr(bridget, '_log', logs.append)
    bridget.save_dm_watermark(CHANNEL_ID, 100)
    channel = _fake_channel([])
    err = discord.HTTPException(MagicMock(status=500, reason='boom'), 'nope')

    def boom(**kwargs):
        async def _gen():
            raise err
            yield  # pragma: no cover
        return _gen()

    channel.history = boom
    _install_client(bridget, monkeypatch, channel)
    assert asyncio.run(bridget._backfill_dm_gap('on_ready')) == 0
    assert bridget.load_dm_watermark(CHANNEL_ID) == 100
    assert any('history fetch failed' in m for m in logs)


def test_one_poison_message_does_not_strand_the_rest_of_the_gap(
        bridget, monkeypatch):
    logs = []
    monkeypatch.setattr(bridget, '_log', logs.append)
    seen = []

    async def flaky(message, source):
        if message.id == 102:
            raise RuntimeError('bad message')
        seen.append(message.id)

    monkeypatch.setattr(bridget, '_dispatch_dm', flaky)
    bridget.save_dm_watermark(CHANNEL_ID, 100)
    channel = _fake_channel([_msg(bridget, i) for i in (101, 102, 103)])
    _install_client(bridget, monkeypatch, channel)
    asyncio.run(bridget._backfill_dm_gap('on_ready'))
    assert seen == [101, 103]
    assert any('102' in m and 'skipped' in m for m in logs)


def test_backfill_failure_never_breaks_the_ready_path(bridget, monkeypatch):
    logs = []
    monkeypatch.setattr(bridget, '_log', logs.append)
    client = MagicMock()
    client.get_user = MagicMock(return_value=None)
    client.fetch_user = AsyncMock(side_effect=RuntimeError('discord sad'))
    monkeypatch.setattr(bridget, 'client', client)
    assert asyncio.run(bridget._backfill_dm_gap('on_ready')) == 0
    assert any('FAILED' in m for m in logs)


# --- watermark mechanics -----------------------------------------------------

def test_watermark_round_trips(bridget):
    assert bridget.load_dm_watermark(CHANNEL_ID) is None
    bridget.save_dm_watermark(CHANNEL_ID, 4242)
    assert bridget.load_dm_watermark(CHANNEL_ID) == 4242
    stored = json.loads(bridget.DM_WATERMARK_FILE.read_text())
    assert stored[str(CHANNEL_ID)]['last_message_id'] == 4242
    assert stored[str(CHANNEL_ID)]['updated_at'].endswith('Z')


def test_watermark_never_moves_backwards(bridget):
    """Rewinding would re-replay — and re-act on — messages already handled."""
    bridget.save_dm_watermark(CHANNEL_ID, 200)
    bridget.save_dm_watermark(CHANNEL_ID, 150)
    assert bridget.load_dm_watermark(CHANNEL_ID) == 200


def test_watermark_is_per_channel(bridget):
    bridget.save_dm_watermark(CHANNEL_ID, 200)
    bridget.save_dm_watermark(CHANNEL_ID + 1, 300)
    assert bridget.load_dm_watermark(CHANNEL_ID) == 200
    assert bridget.load_dm_watermark(CHANNEL_ID + 1) == 300


def test_watermark_write_failure_is_swallowed(bridget, monkeypatch):
    logs = []
    monkeypatch.setattr(bridget, '_log', logs.append)
    monkeypatch.setattr(
        bridget.Path, 'mkdir',
        lambda *a, **k: (_ for _ in ()).throw(OSError('read-only')),
    )
    bridget.save_dm_watermark(CHANNEL_ID, 1)   # must not raise
    assert any('could not persist watermark' in m for m in logs)


def test_watermark_path_is_overridable(tmp_path, monkeypatch):
    target = tmp_path / 'wm.json'
    monkeypatch.setenv('POGO_BRIDGET_DM_WATERMARK', str(target))
    mod = _load_bridget(tmp_path)
    assert mod.DM_WATERMARK_FILE == target


# --- live path ---------------------------------------------------------------

def test_live_message_advances_the_watermark_after_handling(
        bridget, monkeypatch):
    """After, never before: a crash mid-dispatch must leave the message
    inside the next backfill's range rather than marked done."""
    order = []

    async def fake_dispatch(message, source):
        order.append(('dispatch', bridget.load_dm_watermark(CHANNEL_ID)))

    monkeypatch.setattr(bridget, '_dispatch_dm', fake_dispatch)
    asyncio.run(bridget.on_message(_msg(bridget, 500)))
    assert order == [('dispatch', None)]
    assert bridget.load_dm_watermark(CHANNEL_ID) == 500


def test_reconnect_hooks_both_backfill(bridget):
    src = SCRIPT.read_text()
    ready = src.split('async def on_ready')[1].split('\n@client.event')[0]
    resumed = src.split('async def on_resumed')[1].split('\n@client.event')[0]
    assert '_backfill_dm_gap(' in ready
    assert '_backfill_dm_gap(' in resumed
    # ...and on_ready backfills BEFORE the _watchers_started early return,
    # because reconnect re-fires are exactly the ones with a gap behind them.
    assert ready.index('_backfill_dm_gap(') < ready.index('if _watchers_started')
