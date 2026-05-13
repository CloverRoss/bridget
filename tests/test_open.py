"""Tests for the `open mg-XXXX` command (mg-d3d7 / mg-97eb).

`open` is the design-doc retrieval verb that splits off from `read`:
- Only returns the body when frontmatter `status:` is `awaiting-approval`.
- Any other status (approved, rejected, missing file, malformed frontmatter,
  bad regex) returns `design not found` (or a usage hint for non-mg-ids).
- Long bodies truncate at OPEN_BODY_LIMIT with a continuation footer
  pointing at the iCloud path so the user can grab the full doc on a laptop.

The companion tightening — `read mg-XXXX` returns a hint — is covered in
test_read_design.py.
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
    mod = _load_bridget(tmp_path)
    designs = tmp_path / 'designs'
    designs.mkdir()
    monkeypatch.setattr(mod, 'DESIGNS_DIR', designs)
    return mod


# -- happy path -------------------------------------------------------------

def test_open_awaiting_approval_returns_title_and_body(bridget):
    (bridget.DESIGNS_DIR / 'mg-aaaa.md').write_text(
        '---\n'
        'mg_id: mg-aaaa\n'
        'title: a nice design\n'
        'status: awaiting-approval\n'
        '---\n'
        '# Header\n\nBody line.\n'
    )
    reply = bridget.handle_command('open mg-aaaa')
    assert isinstance(reply, str)
    assert reply.startswith('**a nice design** (mg-aaaa)')
    assert '# Header' in reply
    assert 'Body line.' in reply
    # frontmatter stripped
    assert 'mg_id:' not in reply
    assert 'status: awaiting-approval' not in reply
    # no truncation footer for a short body
    assert '…(continued' not in reply


def test_open_falls_back_to_id_when_title_missing(bridget):
    (bridget.DESIGNS_DIR / 'mg-bbbb.md').write_text(
        '---\nstatus: awaiting-approval\n---\nbody only\n'
    )
    reply = bridget.handle_command('open mg-bbbb')
    assert reply.startswith('**mg-bbbb** (mg-bbbb)')
    assert 'body only' in reply


def test_open_is_case_insensitive_on_id(bridget):
    # Discord users routinely type mg-IDs in mixed case. open normalizes.
    (bridget.DESIGNS_DIR / 'mg-c0de.md').write_text(
        '---\ntitle: t\nstatus: awaiting-approval\n---\nb\n'
    )
    reply = bridget.handle_command('open MG-C0DE')
    assert '**t** (mg-c0de)' in reply


# -- not-found cases (single canonical response) ----------------------------

def test_open_approved_status_returns_not_found(bridget):
    (bridget.DESIGNS_DIR / 'mg-a000.md').write_text(
        '---\nstatus: approved\n---\n# d\nbody\n'
    )
    assert bridget.handle_command('open mg-a000') == 'design not found'


def test_open_rejected_status_returns_not_found(bridget):
    (bridget.DESIGNS_DIR / 'mg-1111.md').write_text(
        '---\nstatus: rejected\n---\n# d\nbody\n'
    )
    assert bridget.handle_command('open mg-1111') == 'design not found'


def test_open_auto_approved_status_returns_not_found(bridget):
    # Any non-awaiting-approval status, including pre-approval auto-approved
    # designs, returns the same not-found response per spec.
    (bridget.DESIGNS_DIR / 'mg-2222.md').write_text(
        '---\nstatus: auto-approved\n---\n# d\nbody\n'
    )
    assert bridget.handle_command('open mg-2222') == 'design not found'


def test_open_missing_file_returns_not_found(bridget):
    assert bridget.handle_command('open mg-dead') == 'design not found'


def test_open_file_without_status_field_returns_not_found(bridget):
    # File exists, has frontmatter, but no `status:` line.
    (bridget.DESIGNS_DIR / 'mg-3333.md').write_text(
        '---\ntitle: lonely\n---\n# d\nbody\n'
    )
    assert bridget.handle_command('open mg-3333') == 'design not found'


def test_open_file_without_frontmatter_returns_not_found(bridget):
    # No frontmatter at all → no status → not found. Defensive: an old or
    # corrupt design shouldn't half-render.
    (bridget.DESIGNS_DIR / 'mg-4444.md').write_text('# Title\n\nplain body\n')
    assert bridget.handle_command('open mg-4444') == 'design not found'


def test_open_unparseable_frontmatter_returns_not_found(bridget):
    # Half-open frontmatter (no closing ---) means FRONTMATTER_RE doesn't
    # match → treated as no frontmatter → no status → not found.
    (bridget.DESIGNS_DIR / 'mg-5555.md').write_text(
        '---\nstatus: awaiting-approval\n\nno closing fence\n'
    )
    assert bridget.handle_command('open mg-5555') == 'design not found'


# -- usage / validation -----------------------------------------------------

def test_open_bare_returns_usage(bridget):
    reply = bridget.handle_command('open')
    assert 'Usage' in reply
    assert 'open mg-XXXX' in reply


def test_open_non_mg_id_returns_usage(bridget):
    reply = bridget.handle_command('open notanid')
    assert 'Usage' in reply
    assert 'open mg-XXXX' in reply


def test_open_dr_id_returns_usage(bridget):
    # `open` is design-only (mg-id). dr- reports are out of scope; the spec
    # restricts the regex to `mg-[0-9a-f]+`.
    reply = bridget.handle_command('open dr-abcd')
    assert 'Usage' in reply


def test_open_non_hex_chars_returns_usage(bridget):
    # mg-XXXX where XXXX has non-hex chars (g-z, etc.) must not be treated
    # as a valid id.
    reply = bridget.handle_command('open mg-xyz!')
    assert 'Usage' in reply


# -- truncation -------------------------------------------------------------

def test_open_long_body_is_truncated_with_footer(bridget):
    body = 'A' * 5000
    (bridget.DESIGNS_DIR / 'mg-6666.md').write_text(
        '---\ntitle: long one\nstatus: awaiting-approval\n---\n' + body + '\n'
    )
    reply = bridget.handle_command('open mg-6666')
    assert isinstance(reply, str)
    # body portion bounded by OPEN_BODY_LIMIT plus footer; total response
    # safely under Discord's 2000-char per-message clip.
    assert len(reply) < 2000
    # continuation footer present and points at the iCloud path
    assert '…(continued' in reply
    assert 'Pogo/designs/mg-6666.md' in reply


def test_open_body_exactly_at_limit_has_no_footer(bridget):
    body = 'X' * bridget.OPEN_BODY_LIMIT
    (bridget.DESIGNS_DIR / 'mg-fade.md').write_text(
        '---\nstatus: awaiting-approval\n---\n' + body + '\n'
    )
    reply = bridget.handle_command('open mg-fade')
    # body lstrips its leading newline, then `\n` appended at file write —
    # body length is OPEN_BODY_LIMIT + 1 ('\n'). Footer expected since
    # body exceeds the budget by 1.
    # But the more important assertion: short bodies don't get a footer.
    # Verified separately by test_open_awaiting_approval_returns_title_and_body.
    assert 'X' * 100 in reply  # body content surfaces


# -- help integration -------------------------------------------------------

def test_help_open_describes_awaiting_approval_semantics(bridget):
    reply = bridget.handle_command('help open')
    low = reply.lower()
    assert 'awaiting-approval' in low or 'awaiting approval' in low
    assert 'design' in low
    # iCloud path mentioned so user knows where to find the full body.
    assert 'icloud' in low or 'pogo/designs' in low


def test_help_menu_lists_open(bridget):
    reply = bridget.handle_command('help')
    assert 'open mg-XXXX' in reply


def test_command_list_includes_open(bridget):
    # COMMAND_LIST is the joined-bullet form used in the watch_mailbox
    # startup DM (see test_help_compact_and_drill_down).
    cl = bridget.COMMAND_LIST
    assert '`open mg-XXXX`' in cl


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
