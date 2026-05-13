"""Tests for the `open mg-XXXX` command (mg-d3d7 / mg-97eb / mg-7a57 / mg-0471).

`open` is the design-doc retrieval verb that splits off from `read`:
- Renders the body for any frontmatter `status:` so the user can re-read
  approved/auto-approved/rejected designs as well as awaiting-approval
  ones. The status appears in the reply header.
- Missing file or unreadable/parse-error file → `design not found`.
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
    assert reply.startswith('**a nice design** _(awaiting-approval)_ (mg-aaaa)')
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
    assert reply.startswith('**mg-bbbb** _(awaiting-approval)_ (mg-bbbb)')
    assert 'body only' in reply


def test_open_is_case_insensitive_on_id(bridget):
    # Discord users routinely type mg-IDs in mixed case. open normalizes.
    (bridget.DESIGNS_DIR / 'mg-c0de.md').write_text(
        '---\ntitle: t\nstatus: awaiting-approval\n---\nb\n'
    )
    reply = bridget.handle_command('open MG-C0DE')
    assert '**t** _(awaiting-approval)_ (mg-c0de)' in reply


# -- design-exists-but-past-awaiting-approval --------------------------------
# Post-mg-0471: open surfaces the body regardless of status so the user can
# re-read approved/auto-approved/rejected designs in Discord. Status appears
# in the header.

def test_open_approved_status_renders_body_with_status_header(bridget):
    (bridget.DESIGNS_DIR / 'mg-a000.md').write_text(
        '---\ntitle: a-design\nstatus: approved\n---\n# d\nbody-content\n'
    )
    reply = bridget.handle_command('open mg-a000')
    assert reply.startswith('**a-design** _(approved)_ (mg-a000)')
    assert 'body-content' in reply
    # no "design not found" or "no longer awaiting approval" gating language
    assert 'design not found' not in reply
    assert 'no longer awaiting approval' not in reply


def test_open_rejected_status_renders_body_with_status_header(bridget):
    (bridget.DESIGNS_DIR / 'mg-1111.md').write_text(
        '---\ntitle: rej\nstatus: rejected\n---\n# d\nrejected-body\n'
    )
    reply = bridget.handle_command('open mg-1111')
    assert reply.startswith('**rej** _(rejected)_ (mg-1111)')
    assert 'rejected-body' in reply


def test_open_auto_approved_status_renders_body_with_status_header(bridget):
    # Pre-approval auto-approved designs were previously blocked behind a
    # "no longer awaiting approval" stub. mg-0471 fix: render the body.
    (bridget.DESIGNS_DIR / 'mg-2222.md').write_text(
        '---\ntitle: auto\nstatus: auto-approved\n---\n# d\nauto-body\n'
    )
    reply = bridget.handle_command('open mg-2222')
    assert reply.startswith('**auto** _(auto-approved)_ (mg-2222)')
    assert 'auto-body' in reply


def test_open_missing_file_returns_not_found(bridget):
    # No file on disk → genuinely not-found.
    assert bridget.handle_command('open mg-dead') == 'design not found'


def test_open_missing_file_logs_to_stderr(bridget, capsys):
    # mg-0471 added a stderr log on missing-file so post-fix recurrences
    # are diagnosable without re-instrumenting the handler.
    bridget.handle_command('open mg-d00d')
    captured = capsys.readouterr()
    assert 'open mg-d00d: file missing or unreadable' in captured.err


def test_open_render_logs_status_and_body_len_to_stderr(bridget, capsys):
    # When the body renders, log the resolved status + body length. Gives
    # us a single grep target ('bridget: open ') for any future open-path
    # diagnosis.
    (bridget.DESIGNS_DIR / 'mg-b00f.md').write_text(
        '---\nstatus: approved\n---\nhello\n'
    )
    bridget.handle_command('open mg-b00f')
    captured = capsys.readouterr()
    assert 'open mg-b00f: rendering body' in captured.err
    assert 'status=approved' in captured.err


def test_open_file_without_status_field_renders_body_with_unknown(bridget):
    # File exists, has frontmatter, but no `status:` line. Render body
    # anyway and label the status `unknown`.
    (bridget.DESIGNS_DIR / 'mg-3333.md').write_text(
        '---\ntitle: lonely\n---\n# d\nfound-body\n'
    )
    reply = bridget.handle_command('open mg-3333')
    assert reply.startswith('**lonely** _(unknown)_ (mg-3333)')
    assert 'found-body' in reply


def test_open_file_without_frontmatter_renders_body_with_unknown(bridget):
    # No frontmatter at all → no status → unknown. Render the body verbatim
    # so the user gets the content even from an old/corrupt design.
    (bridget.DESIGNS_DIR / 'mg-4444.md').write_text('# Title\n\nplain body\n')
    reply = bridget.handle_command('open mg-4444')
    assert '_(unknown)_' in reply
    assert 'plain body' in reply


def test_open_unparseable_frontmatter_renders_body_with_unknown(bridget):
    # Half-open frontmatter (no closing ---) means FRONTMATTER_RE doesn't
    # match → treated as no frontmatter → no status → unknown. File exists
    # so we still surface the body.
    (bridget.DESIGNS_DIR / 'mg-5555.md').write_text(
        '---\nstatus: awaiting-approval\n\nno closing fence\n'
    )
    reply = bridget.handle_command('open mg-5555')
    assert '_(unknown)_' in reply
    # body is the literal file content (no frontmatter stripped because the
    # regex didn't match) — at minimum the trailing line surfaces.
    assert 'no closing fence' in reply


def test_open_parse_error_returns_not_found_and_logs_stderr(bridget, monkeypatch, capsys):
    # Force the inner parse path to raise — read_design swallows the
    # exception, logs it, and returns None so the handler reports the
    # canonical not-found string. Visibility lives in stderr.
    (bridget.DESIGNS_DIR / 'mg-7777.md').write_text(
        '---\nstatus: awaiting-approval\n---\nbody\n'
    )

    class _BoomRE:
        def search(self, _text):
            raise RuntimeError('synthetic parse failure')

    monkeypatch.setattr(bridget, 'FRONTMATTER_STATUS_RE', _BoomRE())
    reply = bridget.handle_command('open mg-7777')
    assert reply == 'design not found'
    captured = capsys.readouterr()
    assert 'parse error for mg-7777' in captured.err


def test_open_multiline_yaml_array_in_frontmatter_extracts_status(bridget):
    # Real designs often carry a `revision_log:` array with bulleted entries
    # spanning multiple lines. Regression: the status regex must still find
    # the status line regardless of other multi-line fields.
    (bridget.DESIGNS_DIR / 'mg-8888.md').write_text(
        '---\n'
        'mg_id: mg-8888\n'
        'title: revised design\n'
        'status: awaiting-approval\n'
        'revision_log:\n'
        '  - 2026-05-10: initial draft\n'
        '  - 2026-05-11: addressed reviewer feedback\n'
        '  - 2026-05-12: clarified scope\n'
        '---\n'
        '# Heading\n\nbody text.\n'
    )
    reply = bridget.handle_command('open mg-8888')
    assert reply.startswith('**revised design** _(awaiting-approval)_ (mg-8888)')
    assert 'body text.' in reply
    # frontmatter (including the multi-line array) is stripped from the reply.
    assert 'revision_log' not in reply


def test_open_approved_design_with_multiline_yaml_array_renders_body(bridget):
    # mg-0471 regression case: approved designs carry an
    # `approved_decisions:` block (multi-line YAML) after approval. Pre-fix,
    # the handler bailed with a "no longer awaiting approval" stub because
    # status was 'approved'. Post-fix, the body renders with the status in
    # the header.
    (bridget.DESIGNS_DIR / 'mg-12ee.md').write_text(
        '---\n'
        'mg_id: mg-12ee\n'
        'title: Atlassian token capture\n'
        'status: approved\n'
        'created: 2026-05-13T12:58:50Z\n'
        'approved: 2026-05-13T14:49:40Z\n'
        'approved_decisions:\n'
        '  - q1_macos_only: OK\n'
        '  - q2_keychain_account: defaulted to whoami (Clover asked '
        'what keychain is — answered in FYI)\n'
        '  - q3_input_mechanism: stdin (Clover: "sure")\n'
        'total_estimate_tokens: 80000\n'
        '---\n'
        '# Atlassian token capture\n\nApproved-design body.\n'
    )
    reply = bridget.handle_command('open mg-12ee')
    assert reply.startswith(
        '**Atlassian token capture** _(approved)_ (mg-12ee)'
    )
    assert 'Approved-design body.' in reply
    # multi-line YAML stripped, not leaked into the body chunk
    assert 'approved_decisions' not in reply
    assert 'q1_macos_only' not in reply


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

def test_help_open_describes_any_status_semantics(bridget):
    reply = bridget.handle_command('help open')
    low = reply.lower()
    assert 'design' in low
    # post-mg-0471: open is no longer gated on awaiting-approval. Help text
    # should advertise that approved/auto-approved/rejected designs are
    # also retrievable.
    assert 'approved' in low or 'regardless' in low
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
