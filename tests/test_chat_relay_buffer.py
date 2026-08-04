"""Tests for the chat-relay buffer + N-new-messages nudge (mg-c869).

Robin port item 1: the integration piece that wires `/route` (mg-6b2b)
to a per-agent buffer, drives a `pogo nudge <agent> "N new bridget
messages"` on each user DM, and exposes the agent-side drain via
`bridget chat read <agent>` (CLI fast-path, no discord venv needed).

Covered:
- Buffer add increments per-recipient count + persists the body verbatim.
- Buffer add isolates recipients (mayor's buffer doesn't see director's).
- Drain returns all queued msgs in insertion order and clears the entry.
- Drain on an empty / unknown agent is a no-op returning [].
- Concurrent appends from two callers (subprocess) interleave safely
  under the fcntl lock — final count matches total appends.
- handle_command non-slash DM:
    • buffers under the current /route target.
    • invokes pogo nudge with the exact `--immediate <agent> "N new
      bridget message[s]"` argv shape, singular vs plural.
    • surfaces nudge failure but still buffers.
    • empty / whitespace-only DMs do NOT buffer and do NOT nudge.
- `bridget chat read <agent>` CLI:
    • prints the formatted drain output to stdout.
    • exits 0 on empty buffer with the no-messages line.
    • exits 2 on missing / empty agent name.
    • runs without bridget.env present (fast-path before load_config).
- format_chat_buffer_drain singular vs plural header + ordering.
"""
import importlib.util
import json
import os
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch

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


# -- append_chat_buffer / drain_chat_buffer ---------------------------------


def test_append_returns_per_recipient_count(bridget):
    assert bridget.append_chat_buffer('mayor', 'first') == 1
    assert bridget.append_chat_buffer('mayor', 'second') == 2
    assert bridget.append_chat_buffer('mayor', 'third') == 3


def test_append_isolates_recipients(bridget):
    # director's count must not see mayor's appends.
    bridget.append_chat_buffer('mayor', 'm1')
    bridget.append_chat_buffer('mayor', 'm2')
    assert bridget.append_chat_buffer('director', 'd1') == 1
    assert bridget.append_chat_buffer('mayor', 'm3') == 3


def test_append_persists_to_disk(bridget):
    bridget.append_chat_buffer('mayor', 'hello world')
    assert bridget.CHAT_BUFFER_FILE.exists()
    data = json.loads(bridget.CHAT_BUFFER_FILE.read_text())
    assert 'mayor' in data
    assert len(data['mayor']) == 1
    entry = data['mayor'][0]
    assert entry['body'] == 'hello world'
    # ts is iso-utc with Z suffix (machine-parseable).
    assert isinstance(entry['ts'], str)
    assert entry['ts'].endswith('Z')


def test_drain_returns_messages_in_insertion_order(bridget):
    bridget.append_chat_buffer('mayor', 'one')
    bridget.append_chat_buffer('mayor', 'two')
    bridget.append_chat_buffer('mayor', 'three')
    msgs = bridget.drain_chat_buffer('mayor')
    assert [m['body'] for m in msgs] == ['one', 'two', 'three']


def test_drain_clears_buffer(bridget):
    bridget.append_chat_buffer('mayor', 'hi')
    bridget.drain_chat_buffer('mayor')
    # Subsequent drain returns nothing — and the key is gone from disk
    # so we don't pile empty lists for every recipient ever seen.
    assert bridget.drain_chat_buffer('mayor') == []
    data = json.loads(bridget.CHAT_BUFFER_FILE.read_text())
    assert 'mayor' not in data
    # First append after drain resets count to 1.
    assert bridget.append_chat_buffer('mayor', 'fresh') == 1


def test_drain_unknown_agent_returns_empty(bridget):
    # No append → drain returns []. No crash, no file change.
    assert bridget.drain_chat_buffer('nobody') == []


def test_drain_one_agent_leaves_others_intact(bridget):
    bridget.append_chat_buffer('mayor', 'm1')
    bridget.append_chat_buffer('director', 'd1')
    bridget.append_chat_buffer('director', 'd2')
    bridget.drain_chat_buffer('mayor')
    # director's buffer untouched.
    assert len(bridget.drain_chat_buffer('director')) == 2


def test_load_chat_buffer_handles_corrupt_file(bridget, capsys):
    bridget.CHAT_BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
    bridget.CHAT_BUFFER_FILE.write_text('{not valid json')
    # Should not raise; subsequent append starts fresh.
    assert bridget.append_chat_buffer('mayor', 'x') == 1


def test_load_chat_buffer_handles_non_dict_top_level(bridget):
    bridget.CHAT_BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
    bridget.CHAT_BUFFER_FILE.write_text(json.dumps(['not', 'a', 'dict']))
    # Buffer treated as empty; first append starts at count 1.
    assert bridget.append_chat_buffer('mayor', 'x') == 1


def test_load_chat_buffer_drops_non_list_values(bridget):
    """A corrupted entry where a per-agent value isn't a list should be
    dropped silently rather than crashing the whole buffer.

    Insurance against partial writes / hand-edits — the cost of pretending
    the bad entry doesn't exist is one lost message; crashing the buffer
    would lose all messages across all agents."""
    bridget.CHAT_BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
    bridget.CHAT_BUFFER_FILE.write_text(json.dumps({
        'mayor': [{'ts': '2026-01-01T00:00:00Z', 'body': 'kept'}],
        'director': 'not a list',
    }))
    assert bridget.append_chat_buffer('mayor', 'extra') == 2
    # director's bad entry filtered out — append starts at 1.
    assert bridget.append_chat_buffer('director', 'd1') == 1


# -- format_chat_buffer_drain ----------------------------------------------


def test_format_chat_buffer_drain_empty(bridget):
    out = bridget.format_chat_buffer_drain('mayor', [])
    assert 'No new bridget messages for mayor.' in out


def test_format_chat_buffer_drain_singular(bridget):
    out = bridget.format_chat_buffer_drain('mayor', [
        {'ts': '2026-05-20T12:00:00Z', 'body': 'lonely'},
    ])
    # Header uses singular form.
    assert '1 new bridget message for mayor:' in out
    assert '[2026-05-20T12:00:00Z] lonely' in out


def test_format_chat_buffer_drain_plural(bridget):
    msgs = [
        {'ts': '2026-05-20T12:00:00Z', 'body': 'first'},
        {'ts': '2026-05-20T12:01:00Z', 'body': 'second'},
    ]
    out = bridget.format_chat_buffer_drain('mayor', msgs)
    assert '2 new bridget messages for mayor:' in out
    # Insertion order preserved in output (first comes before second).
    assert out.index('first') < out.index('second')


# -- handle_command non-slash → chat-relay ---------------------------------


def test_non_slash_dm_buffers_under_current_route(
    bridget, tmp_path, monkeypatch, write_crew_prompt
):
    # mg-4d10: load_route only honours a persisted target that is still
    # discoverable, so designer needs a prompt on the scan path — and
    # must be declared running, or the reply is the not-delivered warning
    # instead of the 💬 confirmation.
    write_crew_prompt(tmp_path, 'designer')
    monkeypatch.setenv('POGO_BRIDGET_RUNNING_AGENTS', 'designer')
    bridget.save_route('designer')

    pogo_calls = []
    with patch.object(bridget, 'run_pogo',
                      side_effect=lambda args: pogo_calls.append(args) or (0, '', '')):
        reply = bridget.handle_command('hello agents')

    # Buffered under designer (the active route), not mayor (the default).
    msgs = bridget.drain_chat_buffer('designer')
    assert [m['body'] for m in msgs] == ['hello agents']
    # mayor untouched.
    assert bridget.drain_chat_buffer('mayor') == []
    assert '💬' in reply
    assert 'designer' in reply
    # Exactly one pogo nudge dispatched.
    assert len(pogo_calls) == 1


def test_non_slash_dm_nudges_with_immediate_singular(bridget):
    pogo_calls = []
    with patch.object(bridget, 'run_pogo',
                      side_effect=lambda args: pogo_calls.append(args) or (0, '', '')):
        bridget.handle_command('first message')

    # Singular form for the first message.
    assert pogo_calls[0] == [
        'nudge', '--immediate', 'mayor', '1 new bridget message',
    ]


def test_non_slash_dm_nudges_with_immediate_plural(bridget):
    pogo_calls = []
    with patch.object(bridget, 'run_pogo',
                      side_effect=lambda args: pogo_calls.append(args) or (0, '', '')):
        bridget.handle_command('first')
        bridget.handle_command('second')
        bridget.handle_command('third')

    # Each call carries the running per-recipient count, not a global one.
    assert pogo_calls[0][-1] == '1 new bridget message'
    assert pogo_calls[1][-1] == '2 new bridget messages'
    assert pogo_calls[2][-1] == '3 new bridget messages'
    # And every call uses --immediate so it lands regardless of agent state.
    for call in pogo_calls:
        assert '--immediate' in call


def test_non_slash_dm_buffer_persists_when_nudge_fails(bridget):
    """Buffer write is independent of nudge success — the message must
    still be queued so the agent picks it up on its next mail check.
    User sees the nudge failure in the reply so they know delivery
    wasn't immediate, but the message isn't lost."""
    with patch.object(bridget, 'run_pogo',
                      return_value=(1, '', 'no such agent')):
        reply = bridget.handle_command('hello')

    # Message buffered despite nudge failure.
    msgs = bridget.drain_chat_buffer('mayor')
    assert len(msgs) == 1
    assert msgs[0]['body'] == 'hello'
    # Reply surfaces the nudge failure.
    assert 'nudge failed' in reply
    assert '💬' in reply  # still confirms buffering


def test_non_slash_dm_empty_does_not_buffer_or_nudge(bridget):
    pogo_calls = []
    with patch.object(bridget, 'run_pogo',
                      side_effect=lambda args: pogo_calls.append(args) or (0, '', '')):
        reply_empty = bridget.handle_command('')
        reply_ws = bridget.handle_command('    ')

    assert reply_empty == bridget.CHAT_RELAY_EMPTY_REPLY
    assert reply_ws == bridget.CHAT_RELAY_EMPTY_REPLY
    # Nothing buffered, no nudge.
    assert pogo_calls == []
    assert bridget.drain_chat_buffer('mayor') == []


def test_non_slash_dm_multiline_body_buffered_verbatim(bridget):
    """Multi-line non-slash DMs should preserve internal newlines so
    code snippets / multi-paragraph chat survives the round trip."""
    body = 'line 1\nline 2\n\nparagraph 2'
    with patch.object(bridget, 'run_pogo', return_value=(0, '', '')):
        bridget.handle_command(body)

    msgs = bridget.drain_chat_buffer('mayor')
    assert msgs[0]['body'] == body


def test_slash_commands_not_routed_to_chat_relay(bridget):
    """Sanity: a real slash command MUST NOT land in the buffer or
    trigger a nudge — that would double-handle every command."""
    pogo_calls = []
    with patch.object(bridget, 'run_pogo',
                      side_effect=lambda args: pogo_calls.append(args) or (0, '', '')):
        # /help is the safest verb to fire — no mg shellouts needed.
        bridget.handle_command('/help')

    # No buffer entry for any agent.
    data = bridget._load_chat_buffer_unlocked()
    assert data == {}
    # No pogo nudge dispatched.
    assert pogo_calls == []


def test_legacy_unprefixed_verb_not_routed_to_chat_relay(bridget):
    """Back-compat un-slashed verbs (mg-a0f3) must keep dispatching as
    commands, not get routed into the chat-relay buffer."""
    with patch.object(bridget, 'run_mg', return_value=(0, '', '')), \
         patch.object(bridget, 'run_pogo', return_value=(0, '', '')):
        reply = bridget.handle_command('approve mg-abcd')

    assert '✓ approve sent' in reply
    # Buffer empty — the verb was dispatched, not chat-relayed.
    assert bridget._load_chat_buffer_unlocked() == {}


# -- `bridget chat read <agent>` CLI (fast-path) ----------------------------


def test_chat_read_cli_main_prints_drain(bridget, capsys):
    bridget.append_chat_buffer('mayor', 'hi')
    bridget.append_chat_buffer('mayor', 'two')

    rc = bridget._chat_read_cli_main(['mayor'])
    assert rc == 0
    captured = capsys.readouterr()
    assert '2 new bridget messages for mayor:' in captured.out
    assert 'hi' in captured.out
    assert 'two' in captured.out
    # And drains the buffer — second invocation reports empty.
    assert bridget.drain_chat_buffer('mayor') == []


def test_chat_read_cli_main_empty_buffer_succeeds(bridget, capsys):
    """An empty buffer is a valid state, not an error. Exit 0 so the
    agent's nudge-handler script can run unconditionally."""
    rc = bridget._chat_read_cli_main(['mayor'])
    assert rc == 0
    assert 'No new bridget messages for mayor.' in capsys.readouterr().out


def test_chat_read_cli_main_missing_agent_arg(bridget, capsys):
    rc = bridget._chat_read_cli_main([])
    assert rc == 2
    err = capsys.readouterr().err
    assert 'usage' in err.lower()
    assert 'bridget chat read' in err


def test_chat_read_cli_main_empty_agent_arg(bridget, capsys):
    rc = bridget._chat_read_cli_main(['   '])
    assert rc == 2
    assert 'agent' in capsys.readouterr().err.lower()


def test_chat_cli_main_dispatches_read_subcommand(bridget, capsys):
    """`bridget chat read mayor` should land in the read path even though
    the legacy form (`bridget chat <agent> <body>`) takes a positional
    agent in slot 0."""
    bridget.append_chat_buffer('mayor', 'queued')
    rc = bridget._chat_cli_main(['read', 'mayor'])
    assert rc == 0
    assert 'queued' in capsys.readouterr().out


def test_chat_cli_main_no_args_shows_both_usages(bridget, capsys):
    rc = bridget._chat_cli_main([])
    assert rc == 2
    err = capsys.readouterr().err
    # Help mentions every form so the user can recover. The send form
    # carries the optional `[send]` verb (mg-ad08); the env-inferred
    # form and the read drain form are listed too.
    assert 'bridget chat [send] <agent_name> <body...>' in err
    assert 'bridget chat <body...>' in err
    assert 'POGO_AGENT_NAME' in err
    assert 'bridget chat read <agent_name>' in err


# -- script-level CLI invocation (full subprocess fast-path) ----------------


def test_script_chat_read_subcommand_drains(tmp_path, bridget):
    """End-to-end: invoke the bridget script as `python bridget chat read
    mayor`. Must work WITHOUT a bridget.env in HOME (the chat fast-path
    short-circuits before load_config / discord import)."""
    # Use bridget fixture's HOME — already has a buffered message.
    bridget.append_chat_buffer('mayor', 'preloaded')

    # Drop the env file so the subprocess can prove load_config isn't run.
    env = os.environ.copy()
    env['HOME'] = str(bridget.HOME)
    # Use a tmpdir that genuinely has no bridget.env: copy buffer over.
    fresh_home = tmp_path / 'fresh-home'
    (fresh_home / '.pogo').mkdir(parents=True)
    # Move buffer into fresh_home so the subprocess finds it.
    (fresh_home / '.pogo' / 'bridget-chat-buffer.json').write_text(
        bridget.CHAT_BUFFER_FILE.read_text()
    )
    env['HOME'] = str(fresh_home)

    r = subprocess.run(
        [sys.executable, str(SCRIPT), 'chat', 'read', 'mayor'],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0, (
        f'stdout={r.stdout!r} stderr={r.stderr!r}'
    )
    assert 'preloaded' in r.stdout
    # Buffer drained on disk.
    data = json.loads(
        (fresh_home / '.pogo' / 'bridget-chat-buffer.json').read_text()
    )
    assert 'mayor' not in data


def test_script_chat_read_no_agent_exits_2(tmp_path):
    env = os.environ.copy()
    env['HOME'] = str(tmp_path)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), 'chat', 'read'],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 2


# -- concurrent appends serialize under fcntl.flock -------------------------


@pytest.mark.skipif(
    not hasattr(os, 'fork'), reason='fork-based concurrency test (POSIX only)',
)
def test_concurrent_appends_do_not_lose_messages(bridget):
    """Two forked children each append N messages for the same recipient.
    Final count must equal 2N — no lost writes under the fcntl lock.

    Fork (not subprocess) so the children inherit the already-loaded
    bridget module — running it fresh would re-trigger load_config and
    the `import discord` at module scope, neither of which the test env
    necessarily has. The cross-process lock semantics that matter
    (daemon append vs CLI drain) are still exercised: each child gets
    its own file descriptors and fcntl.flock honors that boundary."""
    pids = []
    for tag in ('A', 'B'):
        pid = os.fork()
        if pid == 0:
            # Child — append 50 messages and exit.
            try:
                for i in range(50):
                    bridget.append_chat_buffer('mayor', f'{tag}-{i}')
                os._exit(0)
            except Exception:
                os._exit(1)
        pids.append(pid)

    for pid in pids:
        _, status = os.waitpid(pid, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, (
            f'child {pid} exit status: {status}'
        )

    data = json.loads(bridget.CHAT_BUFFER_FILE.read_text())
    msgs = data.get('mayor', [])
    assert len(msgs) == 100, (
        f'expected 100 messages after concurrent appends, got {len(msgs)}'
    )
    # All A-* and B-* messages present, no duplicates or losses.
    bodies = sorted(m['body'] for m in msgs)
    expected = sorted([f'A-{i}' for i in range(50)] + [f'B-{i}' for i in range(50)])
    assert bodies == expected


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
