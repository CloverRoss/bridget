"""Tests for the `librarian search` command (mg-b853 / mg-fea0 P2 #8).

`librarian search <query>` shells out to `rg` over the ingested
Confluence data tree (default `~/DUGLocal/confluence-ingestion/data/`,
configurable via `CONFLUENCE_DATA_DIR`). Results are grouped by file,
capped at ~1500 chars.

Tests verify:
- well-formed rg output is grouped + line-numbered per design
- rc=1 (no matches) → friendly "no results" reply
- rc=2 (rg error) → "search failed" reply with stderr tail
- output > 1500 chars → truncation marker appended
- CONFLUENCE_DATA_DIR env override is honored
- missing search root → "no Confluence data ingested yet" reply
- `rg` not on PATH → "rg not installed" reply
- help integration: signature in menu + long description retrievable
"""
import importlib.util
import os
import subprocess
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
    monkeypatch.delenv('CONFLUENCE_DATA_DIR', raising=False)
    return _load_bridget(tmp_path)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A real (empty) data dir pointed at by CONFLUENCE_DATA_DIR.

    The handler's existence check (`root.exists()`) needs a real
    directory; rg itself is mocked so its contents don't matter.
    """
    d = tmp_path / 'confluence-data'
    d.mkdir()
    monkeypatch.setenv('CONFLUENCE_DATA_DIR', str(d))
    return d


def _completed(rc: int, stdout: str = '', stderr: str = ''):
    return subprocess.CompletedProcess(
        args=['rg'], returncode=rc, stdout=stdout, stderr=stderr,
    )


def test_search_groups_by_file_with_line_numbers(bridget, data_dir):
    """Well-formed rg output is grouped per-file with L<n> snippets."""
    root = str(data_dir)
    rg_out = (
        f'{root}/MYSPACE/foo.md:12:foo bar baz\n'
        f'{root}/MYSPACE/foo.md:42:another foo match\n'
        f'{root}/OTHER/notes.md:3:third match\n'
    )

    def fake_run(cmd, **kwargs):
        assert cmd[0] == 'rg'
        assert '--type=md' in cmd
        assert '--max-count=2' in cmd
        assert '--no-heading' in cmd
        assert '--line-number' in cmd
        return _completed(0, stdout=rg_out)

    with mock.patch.object(bridget.subprocess, 'run', side_effect=fake_run):
        reply = bridget.handle_command('librarian search foo')

    assert '**MYSPACE/foo**' in reply  # .md stripped
    assert '**OTHER/notes**' in reply
    assert 'L12: foo bar baz' in reply
    assert 'L42: another foo match' in reply
    assert 'L3: third match' in reply


def test_search_no_results_returns_friendly_reply(bridget, data_dir):
    """rc=1 (rg's no-match exit) → 'no results for ...' reply."""
    with mock.patch.object(
        bridget.subprocess, 'run', return_value=_completed(1)
    ):
        reply = bridget.handle_command('librarian search nothinghere')
    assert 'no results' in reply.lower()
    assert 'nothinghere' in reply


def test_search_rg_error_returns_failure(bridget, data_dir):
    """rc=2 (rg error) → 'search failed: <stderr>' reply."""
    with mock.patch.object(
        bridget.subprocess, 'run',
        return_value=_completed(2, stderr='regex parse error: bad bracket'),
    ):
        reply = bridget.handle_command('librarian search [unclosed')
    assert reply.startswith('search failed')
    assert 'regex parse error' in reply


def test_search_caps_long_output_with_truncation_marker(bridget, data_dir):
    """Output > 1500 chars is truncated and gets a refine-query marker."""
    root = str(data_dir)
    # Generate enough matches across files to blow past 1500 chars.
    lines = []
    for i in range(200):
        lines.append(f'{root}/SPACE/page{i}.md:1:match line for page {i}')
    rg_out = '\n'.join(lines) + '\n'

    with mock.patch.object(
        bridget.subprocess, 'run',
        return_value=_completed(0, stdout=rg_out),
    ):
        reply = bridget.handle_command('librarian search match')

    assert 'truncated' in reply
    assert 'refine query' in reply
    # Cap is ~1500 chars; with the truncation marker we expect close to
    # 1500 + marker length but never wildly above it.
    assert len(reply) < 1700


def test_search_env_override_is_honored(bridget, tmp_path, monkeypatch):
    """`CONFLUENCE_DATA_DIR` env var changes the directory rg searches."""
    custom = tmp_path / 'custom-data-dir'
    custom.mkdir()
    monkeypatch.setenv('CONFLUENCE_DATA_DIR', str(custom))

    seen_paths = []

    def fake_run(cmd, **kwargs):
        seen_paths.append(cmd[-1])
        return _completed(1)

    with mock.patch.object(bridget.subprocess, 'run', side_effect=fake_run):
        bridget.handle_command('librarian search anything')

    assert seen_paths == [str(custom)]


def test_search_missing_root_returns_helpful_reply(bridget, tmp_path, monkeypatch):
    """If the search root doesn't exist, we don't even shell out to rg."""
    missing = tmp_path / 'does-not-exist'
    monkeypatch.setenv('CONFLUENCE_DATA_DIR', str(missing))

    with mock.patch.object(bridget.subprocess, 'run') as run:
        reply = bridget.handle_command('librarian search foo')

    assert 'no Confluence data ingested yet' in reply
    assert str(missing) in reply
    run.assert_not_called()


def test_search_missing_rg_returns_install_hint(bridget, data_dir):
    """If `rg` isn't on PATH, subprocess.run raises FileNotFoundError."""
    with mock.patch.object(
        bridget.subprocess, 'run',
        side_effect=FileNotFoundError(2, 'rg'),
    ):
        reply = bridget.handle_command('librarian search foo')
    assert 'rg' in reply
    assert 'not installed' in reply or 'brew install ripgrep' in reply


def test_search_timeout_returns_timeout_reply(bridget, data_dir):
    """If ripgrep hangs past the timeout, surface that to the user."""
    with mock.patch.object(
        bridget.subprocess, 'run',
        side_effect=subprocess.TimeoutExpired(cmd=['rg'], timeout=10),
    ):
        reply = bridget.handle_command('librarian search foo')
    assert 'timed out' in reply.lower()


def test_search_missing_query_returns_usage(bridget):
    """Bare `librarian search` (no query) → usage message."""
    with mock.patch.object(bridget.subprocess, 'run') as run:
        reply = bridget.handle_command('librarian search')
    assert reply.startswith('Usage')
    run.assert_not_called()


def test_search_query_passed_literally_to_rg(bridget, data_dir):
    """Multi-word query is preserved as a single arg to rg (literal pattern)."""
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _completed(1)

    with mock.patch.object(bridget.subprocess, 'run', side_effect=fake_run):
        bridget.handle_command('librarian search foo bar baz')

    assert len(captured) == 1
    cmd = captured[0]
    # The query is everything between `--line-number` and the trailing dir arg.
    assert cmd[-2] == 'foo bar baz'


def test_search_command_in_help_menu(bridget):
    """Top-level `help` lists the librarian search signature."""
    reply = bridget.handle_command('help')
    assert 'librarian search <query>' in reply


def test_help_librarian_search_returns_description(bridget):
    """`help librarian search` returns the long description."""
    reply = bridget.handle_command('help librarian search')
    assert 'librarian search' in reply
    assert 'ripgrep' in reply.lower()
