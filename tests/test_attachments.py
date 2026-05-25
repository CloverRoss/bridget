"""Tests for bidirectional Discord image+PDF attachment relay (ds-ff7c).

Covered:
- _ingest_attachments: stubbed Discord message + mocked attachment.read()
    • image/png + image/jpeg pass through, get sha256-cached, return entry
    • application/pdf gets page-count probed (best-effort, None on
      unparseable bytes)
    • >25 MiB → rejected entry + warn-reply, no cache write
    • non-image / non-PDF mimes silently dropped (no entry, no warn)
    • dedup: same bytes from two attachments produce one cached file
- append_chat_buffer + format_chat_buffer_drain round-trip:
    • entry persists attachments alongside ts/body
    • drain renders one 📎 line per attachment with mime + size + pages
    • rejected entries render with ⚠️ + filename + reason
    • old entries without attachments key still render unchanged
- write_chat_drop + _parse_chat_drop_attachments round-trip:
    • Attachments: header with one path
    • Attachments: header with multiple paths (continuation lines)
    • drop file with no Attachments: header parses to []
    • body extraction (parse_mail) unaffected by Attachments: header
- _chat_cli_main --attach option:
    • --attach FILE caches the file + writes Attachments: header
    • --attach repeatable
    • --attach with bad mime → exit 2 with helpful stderr
    • --attach with missing file → exit 2
    • --attach with oversized file → exit 2
    • --attach with no FILE arg → exit 2
- Size cap: outbound + inbound both enforce 25 MiB at the right layer.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import os
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
    monkeypatch.setenv(
        'POGO_BRIDGET_CHAT_DIR', str(tmp_path / 'chat-inbox'),
    )
    return _load_bridget(tmp_path)


# Minimal PDF body with a parseable /Type /Pages /Count entry. The page-
# count probe is intentionally tolerant of whitespace; this fixture
# exercises the common `<<\n/Type /Pages\n/Count 3\n...>>` shape.
PDF_SAMPLE = (
    b'%PDF-1.4\n'
    b'1 0 obj <<\n/Type /Pages\n/Count 3\n/Kids [2 0 R 3 0 R 4 0 R]\n>>\n'
    b'endobj\n'
    b'%%EOF\n'
)


def _fake_attachment(filename: str, mime: str, data: bytes):
    """Build a stub mimicking discord.Attachment for _ingest_attachments."""
    att = MagicMock()
    att.filename = filename
    att.content_type = mime
    att.size = len(data)
    att.read = AsyncMock(return_value=data)
    return att


def _fake_message(attachments):
    msg = MagicMock()
    msg.attachments = attachments
    msg.reply = AsyncMock()
    return msg


# -- _ingest_attachments ----------------------------------------------------


def test_ingest_image_caches_and_returns_entry(bridget, tmp_path):
    data = b'PNG\x89fake-bytes-but-good-enough'
    att = _fake_attachment('cat.png', 'image/png', data)
    msg = _fake_message([att])
    out = asyncio.run(bridget._ingest_attachments(msg))
    assert len(out) == 1
    entry = out[0]
    assert entry['mime'] == 'image/png'
    assert entry['size'] == len(data)
    assert entry['sha256'] == hashlib.sha256(data).hexdigest()
    cached = Path(entry['path'])
    assert cached.exists()
    assert cached.parent == bridget.CHAT_ATTACHMENTS_DIR
    assert cached.read_bytes() == data
    # No warn-reply for non-rejected attachments.
    msg.reply.assert_not_awaited()


def test_ingest_pdf_probes_page_count(bridget):
    att = _fake_attachment('doc.pdf', 'application/pdf', PDF_SAMPLE)
    out = asyncio.run(bridget._ingest_attachments(_fake_message([att])))
    assert len(out) == 1
    assert out[0]['mime'] == 'application/pdf'
    assert out[0]['page_count'] == 3


def test_ingest_pdf_unparseable_page_count_is_none(bridget):
    # No /Pages directive → probe returns None, but the file is still
    # cached + relayed.
    att = _fake_attachment('weird.pdf', 'application/pdf', b'%PDF-1.4\nno pages\n')
    out = asyncio.run(bridget._ingest_attachments(_fake_message([att])))
    assert len(out) == 1
    assert out[0]['page_count'] is None


def test_ingest_oversize_rejects_and_warns(bridget):
    cap = bridget.CHAT_ATTACHMENT_SIZE_LIMIT
    att = MagicMock()
    att.filename = 'huge.png'
    att.content_type = 'image/png'
    att.size = cap + 1
    # read() should not be invoked for an oversized attachment — the
    # size gate runs before the download.
    att.read = AsyncMock(side_effect=AssertionError(
        'read() must not be invoked for an oversized attachment'
    ))
    msg = _fake_message([att])
    out = asyncio.run(bridget._ingest_attachments(msg))
    assert len(out) == 1
    assert out[0]['rejected'] == 'too_large'
    assert out[0]['filename'] == 'huge.png'
    # User-visible warn reply was sent.
    msg.reply.assert_awaited_once()
    warn = msg.reply.await_args.args[0]
    assert 'huge.png' in warn
    assert '25MB' in warn or '25 MiB' in warn or 'MB' in warn


def test_ingest_non_image_non_pdf_silently_dropped(bridget):
    # text/plain is not relayable; no entry, no warn.
    att = _fake_attachment('notes.txt', 'text/plain', b'hello\n')
    msg = _fake_message([att])
    out = asyncio.run(bridget._ingest_attachments(msg))
    assert out == []
    msg.reply.assert_not_awaited()


def test_ingest_dedup_same_bytes(bridget):
    data = b'twin-content'
    a = _fake_attachment('a.png', 'image/png', data)
    b = _fake_attachment('b.png', 'image/png', data)
    out = asyncio.run(bridget._ingest_attachments(_fake_message([a, b])))
    assert len(out) == 2
    # Both entries point at the same cached file.
    assert out[0]['path'] == out[1]['path']
    cache_files = [p for p in bridget.CHAT_ATTACHMENTS_DIR.iterdir()
                   if p.is_file() and not p.name.endswith('.tmp')]
    assert len(cache_files) == 1


def test_ingest_empty_message_returns_empty(bridget):
    msg = _fake_message([])
    out = asyncio.run(bridget._ingest_attachments(msg))
    assert out == []


def test_ingest_mime_with_charset_param_still_matched(bridget):
    # Discord occasionally annotates content_type with parameters
    # (e.g. `image/png; charset=binary`). The relay must match on the
    # bare mime type, not the full header value.
    data = b'png-with-params'
    att = _fake_attachment('p.png', 'image/png; charset=binary', data)
    out = asyncio.run(bridget._ingest_attachments(_fake_message([att])))
    assert len(out) == 1
    assert out[0]['mime'] == 'image/png'


# -- append_chat_buffer + format_chat_buffer_drain round-trip ----------------


def test_buffer_round_trip_attachments_render(bridget):
    atts = [
        {
            'path': '/cache/abc.png',
            'mime': 'image/png',
            'sha256': 'abc',
            'size': 1024,
            'filename': 'cat.png',
        },
        {
            'path': '/cache/def.pdf',
            'mime': 'application/pdf',
            'sha256': 'def',
            'size': 9000,
            'page_count': 5,
            'filename': 'doc.pdf',
        },
    ]
    bridget.append_chat_buffer('mayor', 'check these', attachments=atts)
    msgs = bridget.drain_chat_buffer('mayor')
    out = bridget.format_chat_buffer_drain('mayor', msgs)
    # Body line still present, with both attachment lines under it.
    assert 'check these' in out
    assert '📎 /cache/abc.png (image/png, 1024B)' in out
    assert '📎 /cache/def.pdf (application/pdf, 9000B, 5pp)' in out


def test_buffer_rejected_entry_renders_warning(bridget):
    bridget.append_chat_buffer('mayor', 'big one', attachments=[{
        'rejected': 'too_large',
        'filename': 'huge.png',
        'mime': 'image/png',
        'size': 30 * 1024 * 1024,
    }])
    msgs = bridget.drain_chat_buffer('mayor')
    out = bridget.format_chat_buffer_drain('mayor', msgs)
    assert '⚠️ rejected attachment: huge.png (too_large)' in out


def test_buffer_old_entries_without_attachments_render_unchanged(bridget):
    # Simulate a pre-ds-ff7c entry: no attachments key.
    bridget.append_chat_buffer('mayor', 'plain body')
    msgs = bridget.drain_chat_buffer('mayor')
    out = bridget.format_chat_buffer_drain('mayor', msgs)
    assert 'plain body' in out
    assert '📎' not in out
    assert '⚠️' not in out


def test_buffer_empty_attachments_omits_marker(bridget):
    # Explicit empty list: still no 📎 line.
    bridget.append_chat_buffer('mayor', 'body', attachments=[])
    msgs = bridget.drain_chat_buffer('mayor')
    out = bridget.format_chat_buffer_drain('mayor', msgs)
    assert '📎' not in out


# -- write_chat_drop + _parse_chat_drop_attachments round-trip --------------


def test_write_chat_drop_includes_attachments_header(bridget, tmp_path):
    inbox = tmp_path / 'chat'
    p1 = tmp_path / 'a.png'
    p1.write_bytes(b'a')
    final = bridget.write_chat_drop(
        'mayor', 'see attached', inbox_dir=inbox, attachments=[p1],
    )
    content = final.read_text(encoding='utf-8')
    assert f'Attachments: {p1}\n' in content
    # Body extraction by parse_mail is unaffected.
    mail = bridget.parse_mail(content)
    assert mail['from'] == 'mayor'
    assert mail['body'].rstrip() == 'see attached'
    # And the attachments parse back.
    parsed = bridget._parse_chat_drop_attachments(content)
    assert parsed == [p1]


def test_write_chat_drop_multiple_attachments_continuation_lines(
    bridget, tmp_path,
):
    inbox = tmp_path / 'chat'
    p1 = tmp_path / 'a.png'
    p1.write_bytes(b'a')
    p2 = tmp_path / 'b.pdf'
    p2.write_bytes(b'b')
    final = bridget.write_chat_drop(
        'mayor', 'two files', inbox_dir=inbox, attachments=[p1, p2],
    )
    content = final.read_text(encoding='utf-8')
    parsed = bridget._parse_chat_drop_attachments(content)
    assert parsed == [p1, p2]
    mail = bridget.parse_mail(content)
    assert mail['body'].rstrip() == 'two files'


def test_write_chat_drop_without_attachments_unchanged(bridget, tmp_path):
    inbox = tmp_path / 'chat'
    final = bridget.write_chat_drop('mayor', 'plain', inbox_dir=inbox)
    content = final.read_text(encoding='utf-8')
    assert 'Attachments:' not in content
    assert bridget._parse_chat_drop_attachments(content) == []


def test_parse_chat_drop_attachments_no_header(bridget):
    raw = 'From: mayor\nDate: 2026\n\nbody\n'
    assert bridget._parse_chat_drop_attachments(raw) == []


# -- _chat_cli_main --attach ------------------------------------------------


def test_cli_attach_caches_file_and_writes_envelope(bridget, tmp_path):
    src = tmp_path / 'pic.png'
    src.write_bytes(b'PNG-data')
    rc = bridget._chat_cli_main(['mayor', 'hi', '--attach', str(src)])
    assert rc == 0
    drop_dir = bridget.BRIDGET_CHAT_DIR / 'new'
    drops = list(drop_dir.iterdir())
    assert len(drops) == 1
    content = drops[0].read_text(encoding='utf-8')
    assert 'Attachments:' in content
    parsed = bridget._parse_chat_drop_attachments(content)
    assert len(parsed) == 1
    # Cached path is under CHAT_ATTACHMENTS_DIR (sha256+ext).
    assert parsed[0].parent == bridget.CHAT_ATTACHMENTS_DIR
    assert parsed[0].read_bytes() == b'PNG-data'


def test_cli_attach_is_repeatable(bridget, tmp_path):
    a = tmp_path / 'a.png'
    a.write_bytes(b'A')
    b = tmp_path / 'b.pdf'
    b.write_bytes(PDF_SAMPLE)
    rc = bridget._chat_cli_main([
        'mayor', 'body', '--attach', str(a), '--attach', str(b),
    ])
    assert rc == 0
    drop = next(iter((bridget.BRIDGET_CHAT_DIR / 'new').iterdir()))
    parsed = bridget._parse_chat_drop_attachments(drop.read_text())
    assert len(parsed) == 2


def test_cli_attach_rejects_unsupported_mime(bridget, tmp_path, capsys):
    notes = tmp_path / 'notes.txt'
    notes.write_text('hello')
    rc = bridget._chat_cli_main(['mayor', 'hi', '--attach', str(notes)])
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert 'unsupported' in err or 'image' in err
    # No drop file written when the CLI exits with usage error.
    assert not bridget.BRIDGET_CHAT_DIR.exists() or not list(
        (bridget.BRIDGET_CHAT_DIR / 'new').iterdir()
    )


def test_cli_attach_rejects_missing_file(bridget, tmp_path, capsys):
    rc = bridget._chat_cli_main([
        'mayor', 'hi', '--attach', str(tmp_path / 'nope.png'),
    ])
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert 'not a file' in err


def test_cli_attach_requires_argument(bridget, capsys):
    rc = bridget._chat_cli_main(['mayor', 'hi', '--attach'])
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert '--attach' in err


def test_cli_attach_rejects_oversize_file(bridget, tmp_path, capsys, monkeypatch):
    # Don't actually write 25 MiB to disk — patch the cap down to a few
    # bytes so a tiny file overflows it.
    src = tmp_path / 'big.png'
    src.write_bytes(b'big-enough-payload-for-this-test')
    monkeypatch.setattr(bridget, 'CHAT_ATTACHMENT_SIZE_LIMIT', 4)
    rc = bridget._chat_cli_main(['mayor', 'hi', '--attach', str(src)])
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert 'cap' in err or 'bytes' in err


# -- watch_chat outbound size cap -------------------------------------------


def _run_one_watch_chat_tick(bridget, user):
    """Drive watch_chat for one loop iteration (copy of the helper from
    test_chat.py — duplicated here so this test file stands alone)."""
    iterations = {'count': 0}

    def is_closed():
        return iterations['count'] >= 1

    async def fake_sleep(s):
        iterations['count'] += 1
        return None

    original_sleep = bridget.asyncio.sleep
    bridget.client = MagicMock()
    bridget.client.is_closed = is_closed
    bridget.asyncio.sleep = fake_sleep
    try:
        asyncio.run(bridget.watch_chat(user))
    finally:
        bridget.asyncio.sleep = original_sleep


def test_watch_chat_rejects_oversize_outbound_drop(bridget, tmp_path, monkeypatch):
    # Cap the limit low so a small file trips it; the daemon must
    # refuse to DM and instead write back a buffer entry for the sender.
    monkeypatch.setattr(bridget, 'CHAT_ATTACHMENT_SIZE_LIMIT', 8)
    big = bridget.CHAT_ATTACHMENTS_DIR / 'big.png'
    bridget.CHAT_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    big.write_bytes(b'this is more than eight bytes')
    inbox = bridget.BRIDGET_CHAT_DIR
    bridget.write_chat_drop(
        'mayor', 'big drop', inbox_dir=inbox, attachments=[big],
    )

    user = MagicMock()
    user.send = AsyncMock()
    _run_one_watch_chat_tick(bridget, user)

    # No DM sent — the drop was rejected.
    user.send.assert_not_awaited()
    # Sender ('mayor') got a buffer entry explaining the rejection.
    msgs = bridget.drain_chat_buffer('mayor')
    assert len(msgs) == 1
    assert 'rejected' in msgs[0]['body'].lower()


def test_watch_chat_sends_attachments_through(bridget, tmp_path):
    a = bridget.CHAT_ATTACHMENTS_DIR / 'a.png'
    bridget.CHAT_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    a.write_bytes(b'tiny-image')
    bridget.write_chat_drop(
        'mayor', 'with file', inbox_dir=bridget.BRIDGET_CHAT_DIR,
        attachments=[a],
    )

    user = MagicMock()
    user.send = AsyncMock()
    _run_one_watch_chat_tick(bridget, user)

    # One send carrying body + files kwarg.
    assert user.send.await_count == 1
    call = user.send.await_args
    # First chunk goes via content= kwarg when files are present.
    assert '[From mayor]: with file' in (
        call.kwargs.get('content') or (call.args[0] if call.args else '')
    )
    files = call.kwargs.get('files')
    assert files is not None and len(files) == 1


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
