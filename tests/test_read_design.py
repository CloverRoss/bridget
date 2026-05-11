"""Tests for read mg-XXXX surfacing the design doc, with mail fallback.

Covers:
- read_design_doc / read_design_status frontmatter handling.
- chunk_for_discord boundary splitting + truncation footer.
- handle_command's read branch: design surfaced as list[str], frontmatter
  stripped, mail footer cites the latest referencing mail.
- No-design fallback: legacy mail-showing behavior preserved (single str).
- No design + no mail: same "No mail found" reply as before.
- Mail auto-mark-read (new → cur) still fires when a design exists.
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


# -- read_design_doc --------------------------------------------------------

def test_read_design_doc_strips_frontmatter(bridget):
    (bridget.DESIGNS_DIR / 'mg-aaaa.md').write_text(
        '---\nmg_id: mg-aaaa\nstatus: awaiting-approval\n---\n'
        '# Title\n\nBody line.\n'
    )
    body = bridget.read_design_doc('mg-aaaa')
    assert body is not None
    assert 'mg_id' not in body
    assert 'status: awaiting' not in body
    assert body.startswith('# Title')
    assert 'Body line.' in body


def test_read_design_doc_handles_no_frontmatter(bridget):
    (bridget.DESIGNS_DIR / 'mg-bbbb.md').write_text('# Plain\n\nNo frontmatter.\n')
    body = bridget.read_design_doc('mg-bbbb')
    assert body == '# Plain\n\nNo frontmatter.\n'


def test_read_design_doc_returns_none_when_missing(bridget):
    assert bridget.read_design_doc('mg-nope') is None


def test_read_design_status_reads_frontmatter(bridget):
    (bridget.DESIGNS_DIR / 'mg-cccc.md').write_text(
        '---\nmg_id: mg-cccc\nstatus: awaiting-approval\n---\nbody\n'
    )
    assert bridget.read_design_status('mg-cccc') == 'awaiting-approval'


def test_read_design_status_returns_none_without_frontmatter(bridget):
    (bridget.DESIGNS_DIR / 'mg-dddd.md').write_text('# plain\nbody\n')
    assert bridget.read_design_status('mg-dddd') is None


# -- chunk_for_discord ------------------------------------------------------

def test_chunk_for_discord_short_text_single_chunk(bridget):
    assert bridget.chunk_for_discord('short body') == ['short body']


def test_chunk_for_discord_splits_on_section_boundary(bridget):
    text = (
        '# Title\n\n'
        + ('A' * 1500)
        + '\n## Section 2\n\n'
        + ('B' * 1500)
    )
    chunks = bridget.chunk_for_discord(text, limit=1700, max_chunks=3)
    assert len(chunks) == 2
    assert chunks[1].startswith('## Section 2')
    assert all(len(c) <= 1700 for c in chunks)


def test_chunk_for_discord_truncation_footer_when_too_many_sections(bridget):
    text = '\n'.join(f'## Section {i}\n' + ('X' * 1500) for i in range(6))
    chunks = bridget.chunk_for_discord(text, limit=1700, max_chunks=3)
    assert len(chunks) == 3
    assert 'truncated' in chunks[-1]
    assert 'full doc at' in chunks[-1]


def test_chunk_for_discord_hard_splits_oversize_section(bridget):
    # A single 8000-char block with no section boundaries hard-splits into
    # ceil(8000/1700)=5 chunks; max_chunks=3 truncates with a footer.
    text = 'A' * 8000
    chunks = bridget.chunk_for_discord(text, limit=1700, max_chunks=3)
    assert len(chunks) == 3
    assert 'truncated' in chunks[-1]


# -- handle_command read branch --------------------------------------------

def test_read_surfaces_design_with_mail_footer(bridget, monkeypatch):
    (bridget.DESIGNS_DIR / 'mg-1234.md').write_text(
        '---\nmg_id: mg-1234\nstatus: awaiting-approval\n---\n'
        '# Cool design\n\nDecision body.\n'
    )
    monkeypatch.setattr(bridget, 'find_mails_for', lambda _id: [
        (
            Path('/tmp/fake'),
            {'from': 'architect', 'subject': 'approval needed: mg-1234', 'body': 'mail body'},
            'cur',
        ),
    ])
    reply = bridget.handle_command('read mg-1234')
    assert isinstance(reply, list)
    joined = '\n'.join(reply)
    assert '📐' in joined
    assert 'mg-1234' in joined
    assert 'awaiting-approval' in joined
    assert '# Cool design' in joined
    assert 'Decision body.' in joined
    # frontmatter stripped
    assert 'total_estimate_tokens' not in joined
    assert 'mg_id:' not in joined
    # mail footer present
    assert '📧 latest mail' in joined
    assert 'approval needed: mg-1234' in joined
    # full mail body NOT included
    assert 'mail body' not in joined


def test_read_design_with_no_mail_omits_footer(bridget, monkeypatch):
    (bridget.DESIGNS_DIR / 'mg-5678.md').write_text(
        '---\nstatus: drafted\n---\n# d\nbody\n'
    )
    monkeypatch.setattr(bridget, 'find_mails_for', lambda _id: [])
    reply = bridget.handle_command('read mg-5678')
    assert isinstance(reply, list)
    joined = '\n'.join(reply)
    assert '📐' in joined
    assert '📧' not in joined


def test_read_falls_back_to_mail_when_no_design(bridget, monkeypatch, tmp_path):
    fake_mail = tmp_path / 'fakemail'
    fake_mail.write_text('placeholder')
    monkeypatch.setattr(bridget, 'find_mails_for', lambda _id: [
        (
            fake_mail,
            {'from': 'architect', 'subject': 'idea filed', 'body': 'classic mail body'},
            'cur',
        ),
    ])
    reply = bridget.handle_command('read mg-nodesign')
    # legacy behavior: single string
    assert isinstance(reply, str)
    assert '📐' not in reply
    assert 'classic mail body' in reply
    assert 'idea filed' in reply


def test_read_no_design_no_mail_returns_not_found(bridget, monkeypatch):
    monkeypatch.setattr(bridget, 'find_mails_for', lambda _id: [])
    reply = bridget.handle_command('read mg-empty')
    assert isinstance(reply, str)
    assert 'No mail found' in reply
    assert 'mg-empty' in reply


def test_read_design_still_marks_referencing_mail_read(bridget, monkeypatch, tmp_path):
    # auto-mark-read: a 'new' mail referencing the id is moved to cur/ even
    # though we surface the design (not the mail) inline. Protected mails
    # (approval needed / Report ready:) skip this rename — covered in
    # test_read_protected_mails.py.
    (bridget.DESIGNS_DIR / 'mg-9999.md').write_text(
        '---\nstatus: awaiting-approval\n---\n# d\nbody\n'
    )
    new_dir = tmp_path / 'mail' / 'new'
    cur_dir = tmp_path / 'mail' / 'cur'
    new_dir.mkdir(parents=True)
    monkeypatch.setattr(bridget, 'MAIL_DIR', new_dir)
    mail_path = new_dir / 'm.txt'
    mail_path.write_text('From: human\nSubject: approve mg-9999\n\nbody\n')
    monkeypatch.setattr(bridget, 'find_mails_for', lambda _id: [
        (mail_path, {'from': 'human', 'subject': 'approve mg-9999', 'body': 'body'}, 'new'),
    ])
    monkeypatch.setattr(bridget, 'log_mail_action', lambda *_a, **_k: None)
    reply = bridget.handle_command('read mg-9999')
    assert isinstance(reply, list)
    assert not mail_path.exists()
    assert (cur_dir / 'm.txt').exists()


def test_read_dr_prefix_also_surfaces_design(bridget, monkeypatch):
    (bridget.DESIGNS_DIR / 'dr-abcd.md').write_text(
        '---\nstatus: drafted\n---\n# rep\n\nreport text\n'
    )
    monkeypatch.setattr(bridget, 'find_mails_for', lambda _id: [])
    reply = bridget.handle_command('read dr-abcd')
    assert isinstance(reply, list)
    joined = '\n'.join(reply)
    assert 'dr-abcd' in joined
    assert 'report text' in joined


# -- COMMAND_LIST description -----------------------------------------------

def test_command_list_describes_design_first_behavior(bridget):
    cl = bridget.COMMAND_LIST
    # Mentions both new design behavior and legacy fallback.
    assert 'design' in cl.lower()


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
