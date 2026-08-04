"""mg-4d10 — /route must not accept agents that can never read a message.

The bug, as it actually happened:

    ~/.pogo/bridget-chat-buffer.json
      {"doctor": [{"body": "I closed the laptop which is probably why
                            mayor showed as unhealthy. Is it ok now?",
                   "ts": "2026-07-19T17:05:52Z"}]}
    ~/.pogo/bridget-route.json
      {"route": "mayor", "updated_at": "2026-07-19T17:06:04Z"}

At 17:05:52Z Clover asked `doctor` a direct question while her chat route
pointed there. Twelve seconds later she gave up and re-routed to mayor.
`doctor` had been retired — its prompt carries `auto_start = false`, so
pogod never starts it, and the chat buffer only ever empties when the
recipient itself runs `bridget chat read <name>`. Her question sat
unreachable on disk for 16 days.

`/route` validated against a hardcoded `ROUTE_VALID_AGENTS = ('mayor',
'designer', 'doctor')` tuple, which by construction cannot track
retirement, and `append_chat_buffer` buffered unconditionally — no
liveness check, no TTL, no dead-letter.

These tests assert the three properties that make that impossible now:

1. Validity is DISCOVERED from pogod's prompt scan path plus the running
   set, so retiring an agent (renaming its prompt off the scan path)
   immediately makes it unroutable.
2. A message addressed to an agent that is not running is never reported
   as delivered — the user is told, loudly, on every single send.
3. An entry with no possible drainer is handed to mayor as a dead-letter
   rather than deleted, so a human-facing agent can answer it.

The end-to-end test at the bottom is the one that matters: it replays
Clover's exact bytes through the real `bridget chat read mayor` CLI in a
subprocess and asserts her words come out the other side, addressed to an
agent that is actually running. An assertion that merely mirrored the
buffer's shape would prove nothing.
"""
import datetime
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

# Clover's message, verbatim from ~/.pogo/bridget-chat-buffer.json.
CLOVER_BODY = (
    'I closed the laptop which is probably why mayor showed as unhealthy. '
    'Is it ok now?'
)
CLOVER_TS = '2026-07-19T17:05:52Z'


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
def land(tmp_path, monkeypatch, write_crew_prompt):
    """A fake HOME shaped like Clover's actual host at the time of the bug:

      mayor    — prompt present, running
      doctor   — prompt present, auto_start=false, NOT running (retired
                 in practice: pogod never starts it)
      designer — prompt renamed to designer.md.disabled, i.e. off the
                 scan path entirely (retired outright)
    """
    monkeypatch.setenv('HOME', str(tmp_path))
    write_crew_prompt(tmp_path, 'mayor', auto_start=True)
    write_crew_prompt(tmp_path, 'doctor', auto_start=False)
    # Retire designer the way it was actually retired on the host: rename
    # the prompt so it no longer ends in .md and drops off pogod's scan.
    designer = write_crew_prompt(tmp_path, 'designer')
    designer.rename(designer.with_suffix('.md.disabled'))
    monkeypatch.setenv('POGO_BRIDGET_RUNNING_AGENTS', 'mayor')
    return _load_bridget(tmp_path)


# -- discovery mirrors pogod's scan path ------------------------------------


def test_discovery_finds_mayor_and_crew_prompts(land):
    found = land.discover_startable_agents()
    assert 'mayor' in found
    assert 'doctor' in found


def test_discovery_excludes_prompt_renamed_off_scan_path(land):
    # This is the whole fix in one assertion: retiring designer by
    # renaming its prompt makes it undiscoverable, where the hardcoded
    # tuple would have gone on listing it forever.
    assert 'designer' not in land.discover_startable_agents()


def test_discovery_ignores_non_md_and_backup_files(land, tmp_path):
    crew = tmp_path / '.pogo' / 'agents' / 'crew'
    (crew / 'architect.md.preDesigner.bak').write_text('old')
    (crew / 'notes.txt').write_text('not a prompt')
    (crew / 'subdir').mkdir()
    found = land.discover_startable_agents()
    assert found == {'mayor', 'doctor'}


def test_discovery_survives_missing_agents_dir(tmp_path, monkeypatch):
    # Fresh install with no ~/.pogo/agents at all must not explode, and
    # mayor must stay routable so the user is never locked out.
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('POGO_BRIDGET_RUNNING_AGENTS', '-')
    mod = _load_bridget(tmp_path)
    assert mod.discover_startable_agents() == set()
    assert 'mayor' in mod.known_route_agents()
    assert mod.route_target_status('mayor') != mod.ROUTE_STATUS_RETIRED


# -- status classification --------------------------------------------------


def test_status_running_for_live_agent(land):
    assert land.route_target_status('mayor') == land.ROUTE_STATUS_RUNNING


def test_status_stopped_for_startable_but_not_running(land):
    assert land.route_target_status('doctor') == land.ROUTE_STATUS_STOPPED


def test_status_retired_for_agent_off_scan_path(land):
    assert land.route_target_status('designer') == land.ROUTE_STATUS_RETIRED
    assert land.route_target_status('director') == land.ROUTE_STATUS_RETIRED


def test_status_unknown_when_pogod_unreachable(land, monkeypatch):
    # Cannot reach pogod → must NOT claim the agent is dead.
    monkeypatch.setenv('POGO_BRIDGET_RUNNING_AGENTS', '-')
    land.reset_running_agents_cache()
    assert land.route_target_status('doctor') == land.ROUTE_STATUS_UNKNOWN


def test_running_polecat_is_routable_without_a_crew_prompt(land, monkeypatch):
    # A live polecat has no crew prompt but does drain its buffer, so it
    # is a legitimate target.
    monkeypatch.setenv('POGO_BRIDGET_RUNNING_AGENTS', 'mayor,mg-4d10')
    land.reset_running_agents_cache()
    assert land.route_target_status('mg-4d10') == land.ROUTE_STATUS_RUNNING
    assert 'mg-4d10' in land.known_route_agents()


# -- /route refuses retired agents ------------------------------------------


def test_route_to_retired_agent_is_refused(land):
    reply = land.handle_command('/route designer')
    assert 'Cannot route to' in reply
    assert 'designer' in reply
    # The reason is concrete, not a bare "unknown agent".
    assert 'scan path' in reply
    # And the route is NOT changed.
    assert land.load_route() == 'mayor'
    assert not land.ROUTE_FILE.exists()


def test_route_to_stopped_agent_warns_instead_of_confirming(land):
    reply = land.handle_command('/route doctor')
    # The original bug was a cheerful "✓ chat route set to doctor".
    assert not reply.startswith('✓')
    assert '⚠️' in reply
    assert 'not\nrunning' in reply or 'not running' in reply.replace('\n', ' ')
    assert 'pogo agent start doctor' in reply
    # Still honoured — the user may be about to start it — but they were
    # told the truth.
    assert land.load_route() == 'doctor'


def test_route_to_running_agent_confirms_cleanly(land):
    reply = land.handle_command('/route mayor')
    assert '✓' in reply
    assert '⚠️' not in reply
    assert land.load_route() == 'mayor'


def test_route_no_arg_separates_running_from_stopped(land):
    reply = land.handle_command('/route')
    assert 'Running' in reply
    assert 'mayor' in reply
    assert 'Stopped' in reply
    assert 'doctor' in reply
    # A retired agent is not offered at all.
    assert 'designer' not in reply


def test_route_no_arg_flags_a_current_route_that_went_stale(land):
    land.save_route('doctor')
    reply = land.handle_command('/route')
    assert '**doctor**' in reply
    assert '⚠️' in reply


# -- load_route self-heals off a retired target -----------------------------


def test_load_route_falls_back_when_saved_target_was_retired(land):
    # Simulate the sidecar written before the agent was retired.
    land.ROUTE_FILE.parent.mkdir(parents=True, exist_ok=True)
    land.ROUTE_FILE.write_text(json.dumps({'route': 'designer'}))
    assert land.load_route() == 'mayor'


def test_load_route_keeps_a_stopped_but_startable_target(land):
    # Stopped is not retired — the user's choice stands, they just get
    # warned. Silently rewriting it would be its own surprise.
    land.ROUTE_FILE.parent.mkdir(parents=True, exist_ok=True)
    land.ROUTE_FILE.write_text(json.dumps({'route': 'doctor'}))
    assert land.load_route() == 'doctor'


# -- every send to a non-running agent is loudly flagged --------------------


def test_send_to_stopped_agent_is_never_reported_as_delivered(land):
    land.save_route('doctor')
    # pogo nudge exits 0 here on purpose: `pogo nudge` falls back to mail
    # when the agent is down, so a zero exit is NOT evidence of delivery.
    # That false signal is what made the original loss silent.
    with patch.object(land, 'run_pogo', return_value=(0, '', '')):
        reply = land.handle_command(CLOVER_BODY)
    assert 'NOT DELIVERED' in reply
    assert '💬 sent' not in reply
    assert 'doctor' in reply
    # The words are kept, not dropped.
    assert [m['body'] for m in land.drain_chat_buffer('doctor')] == [CLOVER_BODY]


def test_send_warning_repeats_on_every_message(land):
    land.save_route('doctor')
    with patch.object(land, 'run_pogo', return_value=(0, '', '')):
        replies = [land.handle_command(f'msg {i}') for i in range(3)]
    # Not just the first one — a warning the user can scroll past once is
    # how 16 days of silence happens.
    assert all('NOT DELIVERED' in r for r in replies)


def test_send_to_running_agent_still_confirms_normally(land):
    land.save_route('mayor')
    with patch.object(land, 'run_pogo', return_value=(0, '', '')):
        reply = land.handle_command('hello mayor')
    assert '💬 sent' in reply
    assert 'NOT DELIVERED' not in reply


def test_send_when_pogod_unreachable_does_not_cry_wolf(land, monkeypatch):
    monkeypatch.setenv('POGO_BRIDGET_RUNNING_AGENTS', '-')
    land.reset_running_agents_cache()
    land.save_route('doctor')
    with patch.object(land, 'run_pogo', return_value=(0, '', '')):
        reply = land.handle_command('hello')
    # Liveness unknown → report what we know, don't assert death.
    assert 'NOT DELIVERED' not in reply


# -- dead-letter sweep ------------------------------------------------------


def _stale_entry(days_ago: int, body: str) -> dict:
    ts = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=days_ago
    )
    return {'ts': ts.strftime('%Y-%m-%dT%H:%M:%SZ'), 'body': body}


def test_sweep_reassigns_stale_entry_from_a_dead_agent_to_mayor(land):
    land.CHAT_BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
    land.CHAT_BUFFER_FILE.write_text(
        json.dumps({'doctor': [_stale_entry(16, CLOVER_BODY)]})
    )
    moved = land.sweep_chat_buffer_deadletters({'mayor'})
    assert [m['body'] for m in moved] == [CLOVER_BODY]
    assert moved[0]['orphaned_from'] == 'doctor'
    # Landed in mayor's queue; doctor's key is gone.
    buf = json.loads(land.CHAT_BUFFER_FILE.read_text())
    assert 'doctor' not in buf
    assert [m['body'] for m in buf['mayor']] == [CLOVER_BODY]


def test_sweep_never_deletes_the_users_words(land):
    land.CHAT_BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
    land.CHAT_BUFFER_FILE.write_text(
        json.dumps({'doctor': [_stale_entry(16, CLOVER_BODY)]})
    )
    land.sweep_chat_buffer_deadletters({'mayor'})
    # Body and original timestamp both survive verbatim — the entry is
    # re-addressed, not expired.
    buf = json.loads(land.CHAT_BUFFER_FILE.read_text())
    assert buf['mayor'][0]['body'] == CLOVER_BODY
    assert buf['mayor'][0]['ts'].startswith('20')


def test_sweep_leaves_fresh_entries_alone(land):
    # A stopped agent might start in a minute; don't steal its mail
    # instantly.
    land.CHAT_BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
    land.CHAT_BUFFER_FILE.write_text(
        json.dumps({'doctor': [_stale_entry(0, 'just now')]})
    )
    assert land.sweep_chat_buffer_deadletters({'mayor'}) == []
    buf = json.loads(land.CHAT_BUFFER_FILE.read_text())
    assert [m['body'] for m in buf['doctor']] == ['just now']


def test_sweep_leaves_running_agents_queues_alone(land):
    land.CHAT_BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
    land.CHAT_BUFFER_FILE.write_text(
        json.dumps({'sidekick': [_stale_entry(30, 'old but alive')]})
    )
    assert land.sweep_chat_buffer_deadletters({'mayor', 'sidekick'}) == []
    buf = json.loads(land.CHAT_BUFFER_FILE.read_text())
    assert 'sidekick' in buf


def test_sweep_splits_a_mixed_queue(land):
    land.CHAT_BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
    land.CHAT_BUFFER_FILE.write_text(json.dumps({
        'doctor': [_stale_entry(16, 'old'), _stale_entry(0, 'new')],
    }))
    moved = land.sweep_chat_buffer_deadletters({'mayor'})
    assert [m['body'] for m in moved] == ['old']
    buf = json.loads(land.CHAT_BUFFER_FILE.read_text())
    assert [m['body'] for m in buf['doctor']] == ['new']
    assert [m['body'] for m in buf['mayor']] == ['old']


def test_sweep_ages_out_an_entry_with_an_unparseable_timestamp(land):
    land.CHAT_BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
    land.CHAT_BUFFER_FILE.write_text(
        json.dumps({'doctor': [{'ts': 'not-a-date', 'body': 'orphan'}]})
    )
    moved = land.sweep_chat_buffer_deadletters({'mayor'})
    # A malformed ts must not pin a message on disk forever.
    assert [m['body'] for m in moved] == ['orphan']


def test_sweep_preserves_attachments(land):
    land.CHAT_BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = _stale_entry(16, 'see attached')
    entry['attachments'] = [{'path': '/tmp/x.png', 'mime': 'image/png',
                             'size': 12}]
    land.CHAT_BUFFER_FILE.write_text(json.dumps({'doctor': [entry]}))
    moved = land.sweep_chat_buffer_deadletters({'mayor'})
    assert moved[0]['attachments'][0]['path'] == '/tmp/x.png'


# -- drain rendering --------------------------------------------------------


def test_drain_output_marks_dead_letters_and_says_who_they_were_for(land):
    msgs = [{
        'ts': CLOVER_TS, 'body': CLOVER_BODY,
        'orphaned_from': 'doctor', 'orphaned_at': '2026-08-04T12:00:00Z',
    }]
    out = land.format_chat_buffer_drain('mayor', msgs)
    assert 'DEAD-LETTER' in out
    assert '☠️' in out
    assert 'doctor' in out
    assert CLOVER_BODY in out
    # Tells the reading agent what to actually do about it.
    assert 'bridget chat mayor' in out


def test_drain_output_unchanged_for_ordinary_messages(land):
    out = land.format_chat_buffer_drain('mayor', [{'ts': CLOVER_TS,
                                                   'body': 'hi'}])
    assert 'DEAD-LETTER' not in out
    assert '☠️' not in out
    assert '[2026-07-19T17:05:52Z] hi' in out


# -- the drain path only sweeps when liveness is actually known -------------


def test_mayor_drain_does_not_sweep_when_pogod_unreachable(land, monkeypatch):
    monkeypatch.setenv('POGO_BRIDGET_RUNNING_AGENTS', '-')
    land.reset_running_agents_cache()
    land.CHAT_BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
    land.CHAT_BUFFER_FILE.write_text(
        json.dumps({'doctor': [_stale_entry(16, CLOVER_BODY)]})
    )
    assert land._chat_read_cli_main(['mayor']) == 0
    # "Can't reach pogod" must not be read as "nothing is running", which
    # would orphan every live agent's pending mail at once.
    buf = json.loads(land.CHAT_BUFFER_FILE.read_text())
    assert 'doctor' in buf


def test_non_mayor_drain_does_not_sweep(land):
    land.CHAT_BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
    land.CHAT_BUFFER_FILE.write_text(
        json.dumps({'doctor': [_stale_entry(16, CLOVER_BODY)]})
    )
    assert land._chat_read_cli_main(['someone-else']) == 0
    buf = json.loads(land.CHAT_BUFFER_FILE.read_text())
    assert 'doctor' in buf


# -- end-to-end: Clover's actual message reaches a live agent ---------------


def test_clovers_stranded_question_reaches_mayor_end_to_end(
    tmp_path, write_crew_prompt
):
    """Replay the real incident through the real CLI, in a subprocess.

    Given the exact on-disk state from 2026-07-19 — Clover's question
    stranded in `doctor`'s queue while the route points at mayor — running
    the command mayor actually runs (`bridget chat read mayor`) must print
    her words to a human-facing agent. Before mg-4d10 this printed "No new
    bridget messages for mayor." forever.
    """
    home = tmp_path / 'home'
    (home / '.pogo').mkdir(parents=True)
    write_crew_prompt(home, 'mayor', auto_start=True)
    write_crew_prompt(home, 'doctor', auto_start=False)

    # Clover's bytes, exactly as they sit in the live buffer today.
    (home / '.pogo' / 'bridget-chat-buffer.json').write_text(json.dumps({
        'doctor': [{'body': CLOVER_BODY, 'ts': CLOVER_TS}],
    }, indent=2))
    (home / '.pogo' / 'bridget-route.json').write_text(json.dumps({
        'route': 'mayor', 'updated_at': '2026-07-19T17:06:04Z',
    }))

    env = os.environ.copy()
    env['HOME'] = str(home)
    # Only mayor is up — doctor is stopped, exactly as on the real host.
    env['POGO_BRIDGET_RUNNING_AGENTS'] = 'mayor'
    # No bridget.env on purpose: the chat fast-path must not need one.

    r = subprocess.run(
        [sys.executable, str(SCRIPT), 'chat', 'read', 'mayor'],
        env=env, capture_output=True, text=True, timeout=20,
    )
    assert r.returncode == 0, f'stdout={r.stdout!r} stderr={r.stderr!r}'

    # The actual payoff: her question is in front of a running agent.
    assert CLOVER_BODY in r.stdout
    assert 'DEAD-LETTER' in r.stdout
    assert 'doctor' in r.stdout
    assert CLOVER_TS in r.stdout, 'original timestamp must survive'

    # And it is not left behind to be re-reported forever.
    buf = json.loads(
        (home / '.pogo' / 'bridget-chat-buffer.json').read_text()
    )
    assert buf == {} or ('doctor' not in buf and not buf.get('mayor'))

    # A second read is a clean no-op, not a duplicate delivery.
    r2 = subprocess.run(
        [sys.executable, str(SCRIPT), 'chat', 'read', 'mayor'],
        env=env, capture_output=True, text=True, timeout=20,
    )
    assert r2.returncode == 0
    assert CLOVER_BODY not in r2.stdout


def test_route_to_doctor_today_is_refused_end_to_end(land):
    """The trap, sprung the way it was sprung — but with doctor fully
    retired (prompt off the scan path, as designer's already is). The
    /route is refused outright, so no message can ever enter the buffer
    for it in the first place."""
    doctor_prompt = Path(land.POGO_AGENTS_DIR) / 'crew' / 'doctor.md'
    doctor_prompt.rename(doctor_prompt.with_suffix('.md.disabled'))
    land.reset_running_agents_cache()

    reply = land.handle_command('/route doctor')
    assert 'Cannot route to' in reply
    assert land.load_route() == 'mayor'

    # And the message that follows goes to mayor, who is alive.
    with patch.object(land, 'run_pogo', return_value=(0, '', '')):
        sent = land.handle_command(CLOVER_BODY)
    assert '💬 sent' in sent
    assert [m['body'] for m in land.drain_chat_buffer('mayor')] == [CLOVER_BODY]
    assert land.drain_chat_buffer('doctor') == []


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
