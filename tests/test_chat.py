"""Tests for the bridget chat function (mg-c05a — Robin port item 6).

Covers:
- `bridget chat <agent> <body>` CLI: writes a file to BRIDGET_CHAT_DIR/new/
  with the right envelope (From/Date headers + body), atomic via .tmp +
  rename, rejects empty / missing args with exit 2.
- write_chat_drop sanitizes hostile agent names in the filename so a
  caller can't traverse the filesystem.
- format_chat_dm renders `[From <agent>]: <body>` exactly.
- watch_chat: picks up new/ files, emits DM via send_dm_chunked in
  the expected format, moves to cur/ on success, leaves the file in
  new/ on send failure, skips .tmp half-written files.
- watch_chat + long body integrates with send_dm_chunked: a >chunk-size
  body produces multiple DM chunks for one chat drop.
"""
import asyncio
import importlib.util
import os
import subprocess
import sys
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
    chat_dir = tmp_path / 'chat-inbox'
    monkeypatch.setenv('POGO_BRIDGET_CHAT_DIR', str(chat_dir))
    # Clear any inherited agent-context signal so the explicit-form tests
    # have a deterministic baseline; env-inference tests (mg-ad08) set
    # POGO_AGENT_NAME explicitly via monkeypatch.
    monkeypatch.delenv('POGO_AGENT_NAME', raising=False)
    return _load_bridget(tmp_path)


# -- format_chat_dm ---------------------------------------------------------


def test_format_chat_dm_includes_agent_and_body(bridget):
    assert bridget.format_chat_dm('mayor', 'all clear') == (
        '[From mayor]: all clear'
    )


def test_format_chat_dm_strips_trailing_whitespace(bridget):
    # parse_mail leaves a trailing \n from the maildir entry. The DM
    # shouldn't render with a dangling blank line.
    assert bridget.format_chat_dm('designer', 'done\n') == (
        '[From designer]: done'
    )


def test_format_chat_dm_preserves_internal_newlines(bridget):
    # Internal newlines should pass through (multi-line bodies stay
    # multi-line in the DM); only trailing whitespace is dropped.
    msg = bridget.format_chat_dm('doctor', 'line1\nline2')
    assert msg == '[From doctor]: line1\nline2'


# -- write_chat_drop --------------------------------------------------------


def test_write_chat_drop_creates_file_in_new(bridget, tmp_path):
    inbox = tmp_path / 'chat'
    final = bridget.write_chat_drop('mayor', 'ping', inbox_dir=inbox)

    assert final.parent == inbox / 'new'
    assert final.exists()
    content = final.read_text()
    assert content.startswith('From: mayor\n')
    assert 'Date: ' in content
    assert content.endswith('\nping\n')
    # The headers/body separator is the canonical blank line.
    assert '\n\nping\n' in content


def test_write_chat_drop_atomic_via_tmp(bridget, tmp_path):
    """The drop must rename .tmp → final so the watcher never reads a
    half-written file. Verify no .tmp residue remains after a successful
    write."""
    inbox = tmp_path / 'chat'
    bridget.write_chat_drop('mayor', 'hi', inbox_dir=inbox)
    new_dir = inbox / 'new'
    tmps = list(new_dir.glob('*.tmp'))
    assert tmps == []


def test_write_chat_drop_sanitizes_agent_in_filename(bridget, tmp_path):
    """A hostile sender name (e.g. with / or ..) must not affect the
    on-disk filename — write_chat_drop collapses non-[A-Za-z0-9._-]
    chars to _. The From: header still carries the raw name verbatim
    so the DM rendering is unaffected."""
    inbox = tmp_path / 'chat'
    final = bridget.write_chat_drop(
        'evil/../boom', 'payload', inbox_dir=inbox,
    )
    # The filename must live inside new/. The path-traversal protection
    # we care about is "the file lands strictly inside new/" — a `.` is
    # in the allow set (kept for filename separators), but `/` is not,
    # so an `evil/../boom` agent name can't escape the inbox.
    assert final.parent == inbox / 'new'
    assert '/' not in final.name
    # The raw name is still in the file content (parse_mail will read it).
    assert 'From: evil/../boom\n' in final.read_text()


def test_write_chat_drop_creates_inbox_if_missing(bridget, tmp_path):
    """Caller doesn't need to mkdir the inbox first — write_chat_drop
    creates new/ on demand."""
    inbox = tmp_path / 'never-existed-before'
    assert not inbox.exists()
    final = bridget.write_chat_drop('mayor', 'first', inbox_dir=inbox)
    assert final.exists()
    assert (inbox / 'new').is_dir()


# -- _chat_cli_main ---------------------------------------------------------


def test_chat_cli_main_drops_file(bridget, tmp_path, monkeypatch):
    rc = bridget._chat_cli_main(['mayor', 'hello', 'world'])
    assert rc == 0
    new_dir = Path(os.environ['POGO_BRIDGET_CHAT_DIR']) / 'new'
    files = list(new_dir.iterdir())
    assert len(files) == 1
    content = files[0].read_text()
    assert 'From: mayor\n' in content
    # Multiple body args join with a single space.
    assert content.endswith('\n\nhello world\n')


def test_chat_cli_main_requires_agent_and_body(bridget, capsys):
    assert bridget._chat_cli_main([]) == 2
    assert bridget._chat_cli_main(['mayor']) == 2
    err = capsys.readouterr().err
    assert 'usage' in err.lower() or 'agent_name' in err.lower()


def test_chat_cli_main_rejects_empty_body(bridget, capsys):
    rc = bridget._chat_cli_main(['mayor', '   '])
    assert rc == 2
    assert 'body' in capsys.readouterr().err.lower()


def test_chat_cli_main_rejects_empty_agent(bridget, capsys):
    rc = bridget._chat_cli_main(['   ', 'body text'])
    assert rc == 2
    assert 'agent' in capsys.readouterr().err.lower()


# -- 'send' synonym verb (mg-ad08) -----------------------------------------


def _only_drop_content():
    new_dir = Path(os.environ['POGO_BRIDGET_CHAT_DIR']) / 'new'
    files = list(new_dir.iterdir())
    assert len(files) == 1, f'expected exactly one drop, got {files}'
    return files[0].read_text()


def test_chat_cli_main_send_synonym_matches_bare_form(bridget):
    """`chat send <agent> <body>` is identical to `chat <agent> <body>`.

    This is the mg-ad08 bug fix: previously `chat send mayor "hi"` parsed
    `send` as the agent name and `mayor hi` as the body, surfacing as
    `[From send]: mayor hi`."""
    rc = bridget._chat_cli_main(['send', 'mayor', 'hello', 'world'])
    assert rc == 0
    content = _only_drop_content()
    assert 'From: mayor\n' in content
    assert content.endswith('\n\nhello world\n')


def test_chat_cli_main_send_synonym_rejects_missing_body(bridget, capsys):
    """`chat send <agent>` with no body is still a usage error — the
    synonym doesn't change arg requirements (the fixture clears
    POGO_AGENT_NAME, so the env-inferred form isn't in play here)."""
    rc = bridget._chat_cli_main(['send', 'mayor'])
    assert rc == 2
    assert 'usage' in capsys.readouterr().err.lower()


def test_chat_cli_main_send_alone_is_usage_error(bridget, capsys):
    """`chat send` with nothing after it strips to an empty argv → the
    no-args usage error."""
    rc = bridget._chat_cli_main(['send'])
    assert rc == 2
    assert 'usage' in capsys.readouterr().err.lower()


# -- env-inferred sender (mg-ad08) -----------------------------------------


def test_chat_cli_main_infers_sender_from_env(bridget, monkeypatch):
    """With POGO_AGENT_NAME set and a single positional, the sender is
    inferred from the env var and the lone arg is the whole body."""
    monkeypatch.setenv('POGO_AGENT_NAME', 'ad08')
    rc = bridget._chat_cli_main(['all systems green'])
    assert rc == 0
    content = _only_drop_content()
    assert 'From: ad08\n' in content
    assert content.endswith('\n\nall systems green\n')


def test_chat_cli_main_env_infer_via_send_synonym(bridget, monkeypatch):
    """`chat send <body>` with env set also infers the sender — the
    `send` verb composes with env inference."""
    monkeypatch.setenv('POGO_AGENT_NAME', 'crew-doctor')
    rc = bridget._chat_cli_main(['send', 'patched it'])
    assert rc == 0
    content = _only_drop_content()
    assert 'From: crew-doctor\n' in content
    assert content.endswith('\n\npatched it\n')


def test_chat_cli_main_explicit_form_wins_over_env(bridget, monkeypatch):
    """Two positionals always take the explicit `<agent> <body>` form
    even when POGO_AGENT_NAME is set — backward compatibility."""
    monkeypatch.setenv('POGO_AGENT_NAME', 'ad08')
    rc = bridget._chat_cli_main(['mayor', 'explicit body'])
    assert rc == 0
    content = _only_drop_content()
    assert 'From: mayor\n' in content
    assert content.endswith('\n\nexplicit body\n')


def test_chat_cli_main_single_arg_without_env_is_usage_error(
    bridget, capsys, monkeypatch
):
    """One positional and no POGO_AGENT_NAME → usage error (the
    env-inferred form needs the env signal; explicit form needs 2 args)."""
    monkeypatch.delenv('POGO_AGENT_NAME', raising=False)
    rc = bridget._chat_cli_main(['lonely'])
    assert rc == 2
    assert 'usage' in capsys.readouterr().err.lower()


def test_chat_cli_main_env_infer_rejects_empty_body(bridget, monkeypatch):
    """Env-inferred form with a whitespace-only body still exits 2."""
    monkeypatch.setenv('POGO_AGENT_NAME', 'ad08')
    rc = bridget._chat_cli_main(['   '])
    assert rc == 2


# -- script-level CLI invocation -------------------------------------------


def test_script_chat_subcommand_writes_file(tmp_path):
    """End-to-end: invoke the bridget script as a subprocess with
    `chat ...` and verify the file appears + the daemon path (load_config
    / discord import) does NOT run (the CLI exits before that).

    Setting HOME to a fresh tmpdir means there's no bridget.env — if the
    CLI fast path didn't short-circuit, load_config would die() with a
    non-zero exit and a 'config file not found' error. The test asserts
    the CLI exits 0 anyway, proving it ran before load_config.
    """
    env = os.environ.copy()
    env['HOME'] = str(tmp_path)
    chat_dir = tmp_path / 'chat-out'
    env['POGO_BRIDGET_CHAT_DIR'] = str(chat_dir)
    # No bridget.env in HOME — load_config would die() if reached.
    r = subprocess.run(
        [sys.executable, str(SCRIPT), 'chat', 'designer', 'short', 'msg'],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0, (
        f'stdout={r.stdout!r} stderr={r.stderr!r}'
    )
    files = list((chat_dir / 'new').iterdir())
    assert len(files) == 1
    content = files[0].read_text()
    assert 'From: designer\n' in content
    assert content.endswith('\n\nshort msg\n')


def test_script_chat_subcommand_usage_error(tmp_path):
    """Missing args → exit 2 with usage on stderr, no file created."""
    env = os.environ.copy()
    env['HOME'] = str(tmp_path)
    chat_dir = tmp_path / 'chat-out'
    env['POGO_BRIDGET_CHAT_DIR'] = str(chat_dir)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), 'chat'],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 2
    assert not chat_dir.exists() or not any((chat_dir / 'new').iterdir())


# -- watch_chat -------------------------------------------------------------


def _run_one_tick(bridget, user):
    """Drive watch_chat for exactly one iteration of its while loop.

    The loop is `while not client.is_closed(): ... await sleep`. Patch
    client.is_closed to return True after the first body executes, so
    the loop body runs once and then exits cleanly.
    """
    iterations = {'count': 0}

    def is_closed():
        if iterations['count'] >= 1:
            return True
        return False

    # Bump the counter on each sleep call — the sleep is at end-of-loop,
    # so after one tick the next is_closed check returns True.
    original_sleep = bridget.asyncio.sleep

    async def fake_sleep(s):
        iterations['count'] += 1
        return None

    bridget.client = MagicMock()
    bridget.client.is_closed = is_closed
    bridget.asyncio.sleep = fake_sleep
    try:
        asyncio.run(bridget.watch_chat(user))
    finally:
        bridget.asyncio.sleep = original_sleep


def test_watch_chat_emits_dm_and_moves_to_cur(bridget):
    """new/ → DM via send_dm_chunked → cur/. File no longer in new/."""
    inbox = bridget.BRIDGET_CHAT_DIR
    final = bridget.write_chat_drop('mayor', 'all green', inbox_dir=inbox)
    fname = final.name

    user = MagicMock()
    user.send = AsyncMock()

    _run_one_tick(bridget, user)

    # DM was sent with the canonical format.
    assert user.send.await_count >= 1
    sent_texts = [c.args[0] for c in user.send.call_args_list]
    assert '[From mayor]: all green' in sent_texts[0]

    # File was moved to cur/.
    assert not (inbox / 'new' / fname).exists()
    assert (inbox / 'cur' / fname).exists()


def test_watch_chat_skips_tmp_files(bridget):
    """Half-written .tmp files (mid-rename) must be ignored by the watcher.

    Otherwise a racing reader could pick up a partial body."""
    inbox = bridget.BRIDGET_CHAT_DIR
    new_dir = inbox / 'new'
    new_dir.mkdir(parents=True, exist_ok=True)
    # Drop a .tmp file directly (simulating mid-rename).
    (new_dir / '1234.5678.mayor.tmp').write_text(
        'From: mayor\nDate: 2026\n\npartial', encoding='utf-8',
    )

    user = MagicMock()
    user.send = AsyncMock()
    _run_one_tick(bridget, user)

    # No DM sent.
    assert user.send.await_count == 0
    # The .tmp file is still in new/, untouched.
    assert (new_dir / '1234.5678.mayor.tmp').exists()


def test_watch_chat_leaves_file_in_new_on_send_failure(bridget):
    """If send raises HTTPException, the file MUST stay in new/ so the
    next tick retries — losing chat messages on transient send errors
    is worse than a duplicate DM."""
    inbox = bridget.BRIDGET_CHAT_DIR
    final = bridget.write_chat_drop('mayor', 'flaky send', inbox_dir=inbox)
    fname = final.name

    # discord.HTTPException needs both a response and a message arg.
    import discord
    fake_resp = MagicMock()
    fake_resp.status = 500
    fake_resp.reason = 'boom'

    user = MagicMock()
    user.send = AsyncMock(side_effect=discord.HTTPException(fake_resp, 'boom'))

    _run_one_tick(bridget, user)

    # File still in new/, not moved.
    assert (inbox / 'new' / fname).exists()
    assert not (inbox / 'cur' / fname).exists()


def test_watch_chat_long_body_splits_into_chunked_dms(bridget):
    """Integration with send_dm_chunked (mg-a3ef): a >DM_CHUNK_SIZE body
    must arrive as multiple DMs, in order, for a single chat drop.

    send_dm_chunked captures DM_CHUNK_SIZE as a default arg at def-time,
    so patching the module attribute won't shrink the threshold —
    write a body that genuinely exceeds DM_CHUNK_SIZE (1800) instead."""
    inbox = bridget.BRIDGET_CHAT_DIR
    # 10 paragraphs × ~310 chars = ~3100 chars, well over 1800.
    body = '\n\n'.join(f'paragraph {i}: ' + 'x' * 300 for i in range(10))
    assert len(body) > bridget.DM_CHUNK_SIZE  # guard the premise
    bridget.write_chat_drop('designer', body, inbox_dir=inbox)

    user = MagicMock()
    user.send = AsyncMock()

    # Patch asyncio.sleep so the inter-chunk delay doesn't slow the test.
    original_sleep = bridget.asyncio.sleep

    async def fast_sleep(s):
        return None

    bridget.asyncio.sleep = fast_sleep
    try:
        _run_one_tick(bridget, user)
    finally:
        bridget.asyncio.sleep = original_sleep

    assert user.send.await_count >= 2, (
        f'expected >=2 DMs for {len(body)}-char chat body, got '
        f'{user.send.await_count}'
    )
    # First DM carries the [From designer]: prefix.
    sent_texts = [c.args[0] for c in user.send.call_args_list]
    assert sent_texts[0].startswith('[From designer]: ')


def test_watch_chat_processes_files_in_sorted_order(bridget):
    """Two drops should DM in filename-sort order. Filename is
    `<time_ns>.<pid>.<agent>`, so sorted == newest-last by default.
    The user wants chat messages delivered in the order they were
    sent, so we assert sort order matches drop order under a
    fixed-clock simulation."""
    inbox = bridget.BRIDGET_CHAT_DIR
    new_dir = inbox / 'new'
    new_dir.mkdir(parents=True, exist_ok=True)
    # Manually write files with monotonically-increasing prefixes so
    # the sort order is deterministic regardless of time_ns() resolution.
    (new_dir / '001.0.mayor').write_text(
        'From: mayor\n\nfirst', encoding='utf-8',
    )
    (new_dir / '002.0.mayor').write_text(
        'From: mayor\n\nsecond', encoding='utf-8',
    )
    (new_dir / '003.0.mayor').write_text(
        'From: mayor\n\nthird', encoding='utf-8',
    )

    user = MagicMock()
    user.send = AsyncMock()
    _run_one_tick(bridget, user)

    sent = [c.args[0] for c in user.send.call_args_list]
    # Three DMs in the right order.
    assert len(sent) == 3
    assert 'first' in sent[0]
    assert 'second' in sent[1]
    assert 'third' in sent[2]


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
