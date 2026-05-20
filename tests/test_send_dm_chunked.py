"""Tests for send_dm_chunked + _split_for_dm (mg-a3ef).

Covers the helper that splits long Discord DMs into multiple sequential
messages — Robin's pattern, ported so bridget stops truncating around the
1000-1900 char framing limit.

Asserts on _split_for_dm:
- Short body returns single chunk.
- Empty body returns single empty chunk (caller send_dm_chunked turns the
  empty case into a no-op).
- Long plain text splits at paragraph (\\n\\n) boundaries when possible.
- Long paragraph with no \\n\\n splits at line boundaries.
- Line that exceeds budget hard-splits as a last resort.
- Code-fenced content has the open fence closed at the split and reopened
  on the next chunk with the same language tag.

Asserts on send_dm_chunked:
- target.send is called once per chunk, in order.
- Single-chunk body yields a single .send call.
- Empty / whitespace body sends nothing.
- A >1000-char body actually produces multiple .send calls when chunk_size
  is set to a small value (the Robin port acceptance criterion).
"""
import asyncio
import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import AsyncMock

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


# -- _split_for_dm ----------------------------------------------------------


def test_split_short_body_single_chunk(bridget):
    assert bridget._split_for_dm('hello world') == ['hello world']


def test_split_empty_body_single_empty_chunk(bridget):
    assert bridget._split_for_dm('') == ['']


def test_split_at_paragraph_boundary(bridget):
    a = 'A' * 500
    b = 'B' * 500
    c = 'C' * 500
    body = f'{a}\n\n{b}\n\n{c}'
    chunks = bridget._split_for_dm(body, max_chars=600)
    assert len(chunks) >= 2
    # No chunk exceeds budget.
    for chunk in chunks:
        assert len(chunk) <= 600
    # Concatenation preserves all content (when no fences interfere).
    combined = ''.join(chunks)
    for letter in (a, b, c):
        assert letter in combined


def test_split_at_line_boundary_within_single_paragraph(bridget):
    lines = [f'line {i}: ' + 'x' * 80 for i in range(20)]
    body = '\n'.join(lines)  # one paragraph, ~1800 chars
    chunks = bridget._split_for_dm(body, max_chars=400)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 400
    # Every original line appears in some chunk, in order.
    cursor = 0
    for original in lines:
        # Find the line marker in some chunk at or after `cursor`.
        for ci in range(cursor, len(chunks)):
            if f'line {lines.index(original)}:' in chunks[ci]:
                cursor = ci
                break
        else:
            pytest.fail(f'line {original[:20]}... missing from chunks')


def test_split_hard_cuts_oversize_unbroken_line(bridget):
    body = 'x' * 5000  # no newlines anywhere
    chunks = bridget._split_for_dm(body, max_chars=1000)
    assert len(chunks) >= 5
    for chunk in chunks:
        assert len(chunk) <= 1000
    assert ''.join(chunks) == body


def test_split_preserves_code_fence_across_chunks(bridget):
    # Build a body whose code-fenced section is long enough to force a split.
    code = '\n'.join(f'    row {i}' for i in range(120))  # ~1500+ chars
    body = f'Intro line.\n\n```python\n{code}\n```\n\nOutro line.'
    chunks = bridget._split_for_dm(body, max_chars=600)
    assert len(chunks) >= 2
    # Reassembly with fence repair: each chunk must be a self-contained
    # markdown unit (fences balanced).
    for chunk in chunks:
        # Fence tokens must occur in pairs within each chunk.
        fence_count = sum(
            1 for line in chunk.split('\n') if line.lstrip().startswith('```')
        )
        assert fence_count % 2 == 0, (
            f'unbalanced fences in chunk: {chunk!r}'
        )
    # The python language tag must be reopened on chunks that continue
    # the fence (so syntax highlighting carries over). At least one
    # non-first chunk should start with ```python.
    reopened = [c for c in chunks[1:] if c.startswith('```python')]
    assert reopened, 'expected at least one chunk to reopen ```python fence'


def test_split_no_fence_no_extra_tokens_added(bridget):
    body = 'paragraph one\n\nparagraph two\n\n' + ('z' * 2000)
    chunks = bridget._split_for_dm(body, max_chars=900)
    # No fences in input → no fence tokens in output.
    for chunk in chunks:
        for line in chunk.split('\n'):
            assert not line.lstrip().startswith('```'), (
                f'unexpected fence token in chunk: {line!r}'
            )


# -- send_dm_chunked --------------------------------------------------------


def test_send_dm_chunked_single_call_for_short_body(bridget):
    target = AsyncMock()
    asyncio.run(bridget.send_dm_chunked(target, 'just a line'))
    assert target.send.await_count == 1
    assert target.send.await_args.args == ('just a line',)


def test_send_dm_chunked_empty_body_is_noop(bridget):
    target = AsyncMock()
    asyncio.run(bridget.send_dm_chunked(target, ''))
    asyncio.run(bridget.send_dm_chunked(target, '   \n\n  '))
    assert target.send.await_count == 0


def test_send_dm_chunked_long_body_splits_into_multiple_calls(bridget):
    # The acceptance criterion from the task: messages > 1000 chars must
    # split into multiple DMs in order.
    body = '\n\n'.join('paragraph ' + str(i) + ': ' + 'x' * 300
                       for i in range(8))
    target = AsyncMock()

    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)
        return None

    original = bridget.asyncio.sleep
    bridget.asyncio.sleep = fake_sleep
    try:
        asyncio.run(bridget.send_dm_chunked(target, body, chunk_size=600))
    finally:
        bridget.asyncio.sleep = original

    assert target.send.await_count >= 2, (
        f'expected >=2 DMs for {len(body)}-char body, got '
        f'{target.send.await_count}'
    )
    # Order: each successive call should contain content from later in body.
    sent_texts = [call.args[0] for call in target.send.call_args_list]
    # First chunk should contain "paragraph 0", later chunks should contain
    # higher-numbered paragraphs.
    assert 'paragraph 0' in sent_texts[0]
    assert any('paragraph 7' in t for t in sent_texts[1:])
    # An inter-chunk delay is applied before each chunk after the first.
    assert len(sleeps) == target.send.await_count - 1
    for s in sleeps:
        assert s == bridget.DM_CHUNK_DELAY


def test_send_dm_chunked_respects_chunk_size_arg(bridget):
    # Drive chunk_size small enough that even a modest body splits.
    body = 'A' * 200 + '\n\n' + 'B' * 200 + '\n\n' + 'C' * 200
    target = AsyncMock()
    asyncio.run(bridget.send_dm_chunked(target, body, chunk_size=250, delay=0))
    assert target.send.await_count >= 2
    for call in target.send.call_args_list:
        assert len(call.args[0]) <= 250


def test_send_dm_chunked_passes_through_chunks_in_order(bridget):
    body = '\n\n'.join(f'block-{i}' for i in range(6))  # short body
    target = AsyncMock()
    asyncio.run(bridget.send_dm_chunked(target, body))
    sent = [c.args[0] for c in target.send.call_args_list]
    combined = '\n\n'.join(sent)
    for i in range(6):
        assert f'block-{i}' in combined


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
