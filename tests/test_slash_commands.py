"""Tests for native Discord slash-command registration (mg-db57).

Bridget already parses /-prefixed text DMs (mg-a0f3 — the text path stays
intact for back-compat). mg-db57 adds a parallel native-slash route: each
text verb gets an @tree.command wrapper that forwards into handle_command
so Discord's slash UI surfaces the same set without duplicating logic.

This file covers:
- tree is a discord.app_commands.CommandTree on bridget's Client.
- Every text verb in COMMANDS has a corresponding registered slash command.
- Multi-word text verbs (`librarian sync`) use hyphenated slash names.
- _run_slash forwards into handle_command and gates non-configured users.
- on_ready calls tree.sync() exactly once across reconnects.
- Text-parser path is untouched (existing /approve etc. still resolves
  through handle_command without going through the slash path).
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import discord

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
    # Robin profile unlocks every verb so all slash commands resolve through
    # the dispatch tests below.
    monkeypatch.setenv('BRIDGET_PROFILE', 'robin')
    return _load_bridget(tmp_path)


# -- Surface: tree exists + every text verb has a slash entry --------------

def test_tree_is_command_tree(bridget):
    assert isinstance(bridget.tree, discord.app_commands.CommandTree)


# Maps each entry from COMMANDS (`name` field) to the Discord slash name it
# should register under. Colon-suffixed text verbs and space-separated
# multi-word verbs are remapped because Discord slash names cannot contain
# `:` or spaces. Any text verb that is NOT yet wrapped should be added here
# (or to the wrapper) — the surface_complete test below enforces parity.
_EXPECTED_SLASH_NAMES = {
    'approve': 'approve',
    'reject': 'reject',
    'revise': 'revise',
    'explain': 'explain',
    'read': 'read',
    'open': 'open',
    'mail': 'mail',
    'dismiss': 'dismiss',
    'dismiss all': 'dismiss',  # `/dismiss all` is the same slash command as `/dismiss`
    'status': 'status',
    'inbox': 'inbox',
    'nudge': 'nudge',
    'restart': 'restart',
    'route': 'route',
    'librarian sync': 'librarian-sync',
    'librarian search': 'librarian-search',
    'spend': 'spend',
    'accountant run-now': 'accountant-run-now',
    'accountant status': 'accountant-status',
    'help': 'help',
}


def test_every_text_verb_has_slash_wrapper(bridget):
    """Each entry in COMMANDS must have a registered slash command."""
    registered = {c.name for c in bridget.tree.get_commands()}
    for text_name in (c['name'] for c in bridget.COMMANDS):
        slash_name = _EXPECTED_SLASH_NAMES.get(text_name)
        assert slash_name is not None, (
            f"COMMANDS entry {text_name!r} missing from _EXPECTED_SLASH_NAMES; "
            f"add the mapping or wrap the verb in _register_slash_commands."
        )
        assert slash_name in registered, (
            f"text verb {text_name!r} expected to register as /{slash_name} "
            f"but slash tree only has: {sorted(registered)}"
        )


def test_no_unexpected_slash_commands(bridget):
    """The registered slash commands must all map back to a text verb.

    Otherwise we'd ship a slash command the text parser can't handle —
    a silent drift between the two surfaces."""
    expected_slash = set(_EXPECTED_SLASH_NAMES.values())
    registered = {c.name for c in bridget.tree.get_commands()}
    extras = registered - expected_slash
    assert not extras, (
        f"unexpected slash commands not mapped to a text verb: {extras}"
    )


def test_slash_names_are_discord_safe(bridget):
    """Discord rejects slash names with `:`, spaces, or uppercase letters.
    The text parser uses all three, so this catches forgotten remappings."""
    import re
    for c in bridget.tree.get_commands():
        assert re.match(r'^[a-z0-9-]{1,32}$', c.name), (
            f"slash name {c.name!r} not Discord-safe (must match "
            f"^[a-z0-9-]{{1,32}}$ — no colons/spaces/uppercase)"
        )


# -- _run_slash forwards into handle_command + gates by USER_ID ------------

def _fake_interaction(bridget, user_id=None):
    """Build a stub `interaction` exposing the surface _run_slash touches:
    user.id, response.defer/send_message, followup.send. Each callable is
    an AsyncMock so assertions on .await_count / .called survive across
    the dispatch path."""
    if user_id is None:
        user_id = bridget.USER_ID
    interaction = MagicMock()
    interaction.user = MagicMock(id=user_id)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def test_run_slash_forwards_into_handle_command(bridget, monkeypatch):
    monkeypatch.setattr(bridget, 'handle_command', MagicMock(return_value='OK'))
    interaction = _fake_interaction(bridget)
    asyncio.run(bridget._run_slash(interaction, 'approve mg-abcd'))
    bridget.handle_command.assert_called_once_with('approve mg-abcd')
    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_with('OK')


def test_run_slash_rejects_non_configured_user(bridget, monkeypatch):
    monkeypatch.setattr(bridget, 'handle_command', MagicMock(return_value='OK'))
    interaction = _fake_interaction(bridget, user_id=bridget.USER_ID + 999)
    asyncio.run(bridget._run_slash(interaction, 'approve mg-abcd'))
    # handle_command must NOT be invoked when the caller isn't the
    # configured single user — otherwise an interloper could run mg/pogo
    # work under the configured user's identity.
    bridget.handle_command.assert_not_called()
    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert kwargs.get('ephemeral') is True
    # Defer must not fire either — we replied directly with the rejection.
    interaction.response.defer.assert_not_awaited()


def test_run_slash_handles_list_reply(bridget, monkeypatch):
    """handle_command can return a list[str] for multi-chunk replies (e.g.
    long help dumps). _run_slash must split each into a followup.send."""
    monkeypatch.setattr(
        bridget, 'handle_command',
        MagicMock(return_value=['first', 'second']),
    )
    interaction = _fake_interaction(bridget)
    asyncio.run(bridget._run_slash(interaction, 'status'))
    sent = [c.args[0] for c in interaction.followup.send.await_args_list]
    assert sent == ['first', 'second']


def test_run_slash_dispatch_exception_is_reported(bridget, monkeypatch):
    """If handle_command raises, the user gets an error reply rather than
    a silent timeout (Discord shows "interaction failed" otherwise)."""
    def _boom(_text):
        raise RuntimeError('boom')
    monkeypatch.setattr(bridget, 'handle_command', _boom)
    interaction = _fake_interaction(bridget)
    asyncio.run(bridget._run_slash(interaction, 'status'))
    interaction.followup.send.assert_awaited_once()
    msg = interaction.followup.send.await_args.args[0]
    assert 'internal error' in msg.lower()
    assert 'boom' in msg


def test_run_slash_empty_reply_emits_placeholder(bridget, monkeypatch):
    """Discord rejects empty followup.send payloads. _run_slash must
    substitute a one-liner so we never produce an HTTPException."""
    monkeypatch.setattr(bridget, 'handle_command', MagicMock(return_value=''))
    interaction = _fake_interaction(bridget)
    asyncio.run(bridget._run_slash(interaction, 'status'))
    interaction.followup.send.assert_awaited_once()
    msg = interaction.followup.send.await_args.args[0]
    assert msg.strip()


# -- on_ready: tree.sync() fires once across reconnects --------------------

def _install_on_ready_fakes(bridget, monkeypatch):
    bridget._watchers_started = False
    bridget._slash_synced = False
    fake_client = MagicMock()
    fake_client.user = MagicMock(id=42)
    fake_client.fetch_user = AsyncMock(return_value=MagicMock(id=1))
    fake_client.loop = MagicMock()
    monkeypatch.setattr(bridget, 'client', fake_client)
    monkeypatch.setattr(bridget, 'watch_mailbox', MagicMock(return_value=object()))
    monkeypatch.setattr(bridget, 'watch_task_transitions', MagicMock(return_value=object()))
    monkeypatch.setattr(bridget, 'watch_idea_claims', MagicMock(return_value=object()))
    monkeypatch.setattr(bridget, 'watch_chat', MagicMock(return_value=object()))
    fake_tree = MagicMock()
    fake_tree.sync = AsyncMock(return_value=[MagicMock(), MagicMock()])
    monkeypatch.setattr(bridget, 'tree', fake_tree)
    return fake_client, fake_tree


def test_on_ready_first_fire_syncs_slash_tree(bridget, monkeypatch):
    _, fake_tree = _install_on_ready_fakes(bridget, monkeypatch)
    asyncio.run(bridget.on_ready())
    fake_tree.sync.assert_awaited_once()
    assert bridget._slash_synced is True


def test_on_ready_second_fire_does_not_resync(bridget, monkeypatch):
    """Discord rate-limits global command registration; we must not burn
    that quota on every gateway reconnect (laptop wake fires on_ready)."""
    _, fake_tree = _install_on_ready_fakes(bridget, monkeypatch)
    asyncio.run(bridget.on_ready())
    asyncio.run(bridget.on_ready())
    # First call synced; second call short-circuits on _watchers_started
    # well before reaching the sync block, so sync still ran exactly once.
    assert fake_tree.sync.await_count == 1


def test_on_ready_survives_sync_failure(bridget, monkeypatch):
    """Discord's commands API can be flaky. A sync error must not block
    the watcher spawn or crash the daemon — it logs + continues."""
    fake_client, fake_tree = _install_on_ready_fakes(bridget, monkeypatch)
    fake_tree.sync = AsyncMock(side_effect=RuntimeError('discord-side flake'))
    asyncio.run(bridget.on_ready())
    # Sync raised; we did NOT mark _slash_synced (so the next on_ready
    # would retry once watchers come up again — though in practice the
    # watcher guard prevents that too).
    assert bridget._slash_synced is False
    # Watchers still spawn — that's the point of the try/except.
    assert fake_client.loop.create_task.call_count == 4
    assert bridget._watchers_started is True


# -- Text-parser back-compat: existing /approve still works ---------------

def test_text_slash_approve_still_dispatches(bridget, monkeypatch):
    """Adding the native slash route must not regress the text path.
    A user typing `/approve mg-abcd` in a DM still goes through
    handle_command, not the slash wrapper."""
    monkeypatch.setattr(bridget, 'run_mg', lambda _args: (0, '', ''))
    monkeypatch.setattr(bridget, 'mark_mail_read', lambda **_k: 0)
    monkeypatch.setattr(bridget, '_clear_approval_mail', lambda _id: 0)
    monkeypatch.setattr(bridget, 'log_mail_action', lambda *_a, **_k: None)
    monkeypatch.setattr(bridget, 'route_recipient', lambda _id: 'architect')
    reply = bridget.handle_command('/approve mg-abcd')
    assert '✓ approve sent' in reply


def test_text_legacy_unprefixed_command_still_works(bridget, monkeypatch):
    """The slash command surface is additive — un-prefixed text still
    dispatches with the existing deprecation warning."""
    monkeypatch.setattr(bridget, 'run_mg', lambda _args: (0, '', ''))
    monkeypatch.setattr(bridget, 'mark_mail_read', lambda **_k: 0)
    monkeypatch.setattr(bridget, '_clear_approval_mail', lambda _id: 0)
    monkeypatch.setattr(bridget, 'log_mail_action', lambda *_a, **_k: None)
    monkeypatch.setattr(bridget, 'route_recipient', lambda _id: 'architect')
    reply = bridget.handle_command('approve mg-abcd')
    assert '✓ approve sent' in reply


# -- Slash wrapper integration: a couple end-to-end paths -----------------

def _find_command(bridget, name: str):
    for c in bridget.tree.get_commands():
        if c.name == name:
            return c
    raise AssertionError(f"slash command /{name} not registered")


def test_slash_approve_invokes_handle_command_with_text_form(bridget, monkeypatch):
    """The /approve wrapper must reconstruct `approve <id>` and pass that
    into handle_command — verifies the slash → text bridge for a basic
    one-arg verb."""
    monkeypatch.setattr(bridget, 'handle_command', MagicMock(return_value='✓ approve sent'))
    cmd = _find_command(bridget, 'approve')
    interaction = _fake_interaction(bridget)
    asyncio.run(cmd.callback(interaction, target='mg-abcd'))
    bridget.handle_command.assert_called_once_with('approve mg-abcd')



def test_slash_librarian_sync_reconstructs_space_form(bridget, monkeypatch):
    """Multi-word verb: the slash is `/librarian-sync` but the text
    parser keys off `librarian sync <space>` (space-separated). The
    wrapper rejoins with a space when forwarding."""
    monkeypatch.setattr(
        bridget, 'handle_command',
        MagicMock(return_value='🔄 librarian sync started for `ENG`; will DM when done.'),
    )
    cmd = _find_command(bridget, 'librarian-sync')
    interaction = _fake_interaction(bridget)
    asyncio.run(cmd.callback(interaction, space='ENG'))
    bridget.handle_command.assert_called_once_with('librarian sync ENG')


def test_slash_mail_joins_subject_and_body_with_newline(bridget, monkeypatch):
    """The text-form `/mail <subject>\\n<body>` uses a newline as the
    separator between subject and body. The slash wrapper takes
    separate subject/body params and must rejoin them on \\n so the
    dispatcher's `'\\n' in rest` branch fires correctly."""
    monkeypatch.setattr(bridget, 'handle_command', MagicMock(return_value='✓'))
    cmd = _find_command(bridget, 'mail')
    interaction = _fake_interaction(bridget)
    asyncio.run(cmd.callback(interaction, subject='hello', body='multi\nline body'))
    bridget.handle_command.assert_called_once_with('mail hello\nmulti\nline body')


def test_slash_mail_subject_only_omits_newline(bridget, monkeypatch):
    """Without a body, the text-form expression is `mail <subject>` — no
    trailing newline (which would dispatch the empty-body branch)."""
    monkeypatch.setattr(bridget, 'handle_command', MagicMock(return_value='✓'))
    cmd = _find_command(bridget, 'mail')
    interaction = _fake_interaction(bridget)
    asyncio.run(cmd.callback(interaction, subject='hi'))
    bridget.handle_command.assert_called_once_with('mail hi')


def test_slash_route_no_arg_omits_trailing_space(bridget, monkeypatch):
    """`/route` (no arg) must forward as the bare verb so the dispatcher's
    `low == 'route'` branch fires — `'route '` (trailing space) would still
    match `startswith('route ')` and demand a positional arg."""
    monkeypatch.setattr(bridget, 'handle_command', MagicMock(return_value='Current chat route: mayor'))
    cmd = _find_command(bridget, 'route')
    interaction = _fake_interaction(bridget)
    asyncio.run(cmd.callback(interaction))
    bridget.handle_command.assert_called_once_with('route')


if __name__ == '__main__':
    import sys
    sys.exit(pytest.main([__file__, '-v']))
