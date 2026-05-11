"""Tests for `read m<N>` (mg-8d3d).

`read mN` resolves the 1-indexed slot from the sorted unread-mail list — the
same order `inbox` displays — so id-less mails (and any other unread mail)
can be opened inline. `inbox` itself renders the m-index next to each line.

Coverage:
- inbox output contains [m1, [m2, [m3 prefixes.
- read m1 (mg-id, non-protected): mail body inline; file moved to cur/.
- read m2 (id-less): body inline; file moved to cur/.
- read m3 (protected): body inline with `(action required)`; file stays in new/.
- read m4: out-of-range friendly error.
- read m1 with empty MAIL_DIR: same friendly error citing 0 unread mails.
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
    new_dir = tmp_path / 'mail' / 'new'
    new_dir.mkdir(parents=True)
    monkeypatch.setattr(mod, 'MAIL_DIR', new_dir)
    monkeypatch.setattr(mod, 'log_mail_action', lambda *_a, **_k: None)
    return mod


def _write_mail(mail_dir: Path, name: str, subject: str,
                sender: str = 'architect', body: str = 'body text') -> Path:
    mail_dir.mkdir(parents=True, exist_ok=True)
    p = mail_dir / name
    p.write_text(f"From: {sender}\nSubject: {subject}\n\n{body}\n")
    return p


def _seed_three(bridget):
    """Three unread mails in deterministic order: mg-id, id-less, protected."""
    m1 = _write_mail(
        bridget.MAIL_DIR, '01-m1.txt',
        subject='fyi mg-aaaa shipped',
        sender='director',
        body='heads-up about mg-aaaa',
    )
    m2 = _write_mail(
        bridget.MAIL_DIR, '02-m2.txt',
        subject='FYI: heads-up',
        sender='architect',
        body='no work-item id mentioned here',
    )
    m3 = _write_mail(
        bridget.MAIL_DIR, '03-m3.txt',
        subject='approval needed mg-zzzz',
        sender='architect',
        body='please approve mg-zzzz',
    )
    return m1, m2, m3


def test_inbox_renders_m_index_for_each_unread(bridget):
    _seed_three(bridget)
    summary = bridget.get_inbox_summary()
    assert '[m1 ' in summary
    assert '[m2 ' in summary
    assert '[m3 ' in summary
    # the id-less mail is rendered with the m-index AND `(no mg-id)`.
    assert '[m2 / (no mg-id)]' in summary
    assert '[m1 / mg-aaaa]' in summary


def test_read_m1_mg_id_returns_body_and_moves_to_cur(bridget):
    m1, _m2, _m3 = _seed_three(bridget)
    reply = bridget.handle_command('read m1')
    # Returned as a string (no design doc exists → falls back to mail body)
    # or a chunked list[str] if a design existed. Either way, body text in.
    rendered = '\n'.join(reply) if isinstance(reply, list) else reply
    assert 'heads-up about mg-aaaa' in rendered
    # non-protected: moved out of new/ to cur/
    assert not m1.exists()
    assert (bridget.MAIL_DIR.parent / 'cur' / '01-m1.txt').exists()


def test_read_m2_id_less_returns_body_inline_and_moves(bridget):
    _m1, m2, _m3 = _seed_three(bridget)
    reply = bridget.handle_command('read m2')
    rendered = '\n'.join(reply) if isinstance(reply, list) else reply
    assert 'no work-item id mentioned here' in rendered
    assert 'FYI: heads-up' in rendered
    assert '_(unread)_' in rendered
    # non-protected, id-less: moved to cur/
    assert not m2.exists()
    assert (bridget.MAIL_DIR.parent / 'cur' / '02-m2.txt').exists()


def test_read_m3_protected_returns_content_but_stays_in_new(bridget):
    _m1, _m2, m3 = _seed_three(bridget)
    reply = bridget.handle_command('read m3')
    rendered = '\n'.join(reply) if isinstance(reply, list) else reply
    assert 'please approve mg-zzzz' in rendered
    assert 'unread (action required)' in rendered
    # protected: stays in new/
    assert m3.exists()
    assert not (bridget.MAIL_DIR.parent / 'cur' / '03-m3.txt').exists()


def test_read_m4_out_of_range_friendly_error(bridget):
    _seed_three(bridget)
    reply = bridget.handle_command('read m4')
    assert isinstance(reply, str)
    assert 'm4 not found' in reply
    assert 'inbox has 3 unread mails' in reply
    assert '`inbox`' in reply


def test_read_m1_empty_mail_dir_friendly_error(bridget):
    # MAIL_DIR exists from the fixture but is empty.
    reply = bridget.handle_command('read m1')
    assert isinstance(reply, str)
    assert 'm1 not found' in reply
    assert 'inbox has 0 unread mails' in reply


def test_read_m1_case_insensitive(bridget):
    _seed_three(bridget)
    reply = bridget.handle_command('READ M1')
    rendered = '\n'.join(reply) if isinstance(reply, list) else reply
    assert 'heads-up about mg-aaaa' in rendered


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
