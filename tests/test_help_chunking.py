"""Tests for help/?/commands replying with multi-chunk Discord output.

Covers:
- _chunk_bullet_list helper: chunks stay <= limit, each chunk starts/ends on a
  bullet boundary (no mid-bullet split), small input returns a single chunk.
- handle_command help branch: with the real COMMAND_LIST the reply is a list
  with >=2 entries; each chunk <= 1700 chars; the `preapprove` bullet appears
  intact in one of the chunks; a shrunk COMMAND_LIST returns a single string.
"""
import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

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


# -- _chunk_bullet_list helper ---------------------------------------------

def test_chunk_bullet_list_single_chunk_when_fits(bridget):
    text = '• one\n• two\n• three'
    chunks = bridget._chunk_bullet_list(text, limit=1700)
    assert chunks == [text]


def test_chunk_bullet_list_respects_limit(bridget):
    # 5 lines of 400 chars each = ~2000 chars total; with limit=1000 we expect
    # multiple chunks each <= 1000.
    lines = [f'• {"x" * 398}' for _ in range(5)]
    text = '\n'.join(lines)
    chunks = bridget._chunk_bullet_list(text, limit=1000)
    assert len(chunks) >= 2
    assert all(len(c) <= 1000 for c in chunks)


def test_chunk_bullet_list_no_mid_bullet_split(bridget):
    # Each emitted chunk should begin with a bullet marker (start-of-bullet
    # boundary) — i.e., we never slice through the middle of a bullet line.
    lines = [f'• item-{i} {"x" * 200}' for i in range(15)]
    text = '\n'.join(lines)
    chunks = bridget._chunk_bullet_list(text, limit=600)
    assert len(chunks) >= 2
    for c in chunks:
        assert c.startswith('• '), f'chunk does not start on bullet boundary: {c[:40]!r}'


# -- handle_command help branch --------------------------------------------

def test_help_returns_list_for_oversized_command_list(bridget):
    reply = bridget.handle_command('help')
    assert isinstance(reply, list)
    assert len(reply) >= 2
    assert all(len(c) <= 1700 for c in reply)


def test_help_first_chunk_has_commands_header(bridget):
    reply = bridget.handle_command('help')
    assert isinstance(reply, list)
    assert reply[0].startswith('**Commands:**\n')


def test_help_preapprove_entry_intact(bridget):
    reply = bridget.handle_command('help')
    assert isinstance(reply, list)
    # The full `preapprove` bullet must survive in exactly one chunk —
    # specifically, no chunk should end with a truncated 'preappr…'.
    full_marker = '`preapprove [true [fast] | false]`'
    assert any(full_marker in c for c in reply), \
        f'preapprove bullet missing or truncated; chunks={[c[-80:] for c in reply]}'


@pytest.mark.parametrize('alias', ['?', 'commands', 'HELP'])
def test_help_aliases_also_chunked(bridget, alias):
    reply = bridget.handle_command(alias)
    assert isinstance(reply, list)
    assert reply[0].startswith('**Commands:**\n')


def test_help_returns_string_when_command_list_fits(bridget, monkeypatch):
    # Shrink COMMAND_LIST so it fits in a single chunk → reply should be a
    # plain string, not a list (back-compat with the simple-case path).
    monkeypatch.setattr(bridget, 'COMMAND_LIST', '• `help` — this list')
    reply = bridget.handle_command('help')
    assert isinstance(reply, str)
    assert reply.startswith('**Commands:**\n')
    assert '`help`' in reply


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
