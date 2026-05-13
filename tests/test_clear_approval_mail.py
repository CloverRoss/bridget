"""Tests for _clear_approval_mail + its wiring into approve/reject/revise (mg-ae6f).

Helper contract:
- Only mails whose Subject line starts with 'Subject: approval needed ' AND
  contains the mg-id move from human/new/ to human/cur/.
- Other subjects (e.g. 'Report ready: mg-xxxx') are left alone even when the
  mg-id appears in the body.
- Missing MAIL_DIR is a no-op (returns 0, no crash).
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


def _write_mail(mail_dir: Path, name: str, subject: str, body: str = 'body text',
                sender: str = 'architect') -> Path:
    mail_dir.mkdir(parents=True, exist_ok=True)
    p = mail_dir / name
    p.write_text(f"From: {sender}\nSubject: {subject}\n\n{body}\n")
    return p


# -- _clear_approval_mail unit tests ----------------------------------------

def test_clear_approval_mail_moves_matching_subject(bridget):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-aaaa')
    _write_mail(bridget.MAIL_DIR, '02.eml', 'approval needed mg-bbbb')
    _write_mail(bridget.MAIL_DIR, '03.eml', 'Report ready: mg-cccc')

    n = bridget._clear_approval_mail('mg-aaaa')
    assert n == 1

    cur_dir = bridget.MAIL_DIR.parent / 'cur'
    assert (cur_dir / '01.eml').exists()
    # The non-matching mails stay in new/.
    assert (bridget.MAIL_DIR / '02.eml').exists()
    assert (bridget.MAIL_DIR / '03.eml').exists()


def test_clear_approval_mail_no_match_returns_zero(bridget):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-aaaa')
    n = bridget._clear_approval_mail('mg-no-match')
    assert n == 0
    assert (bridget.MAIL_DIR / '01.eml').exists()


def test_clear_approval_mail_missing_dir_returns_zero(bridget):
    # Ensure MAIL_DIR doesn't exist.
    if bridget.MAIL_DIR.exists():
        for p in bridget.MAIL_DIR.iterdir():
            p.unlink()
        bridget.MAIL_DIR.rmdir()
    assert bridget._clear_approval_mail('mg-aaaa') == 0


def test_clear_approval_mail_ignores_body_only_mention(bridget):
    # mg-aaaa only appears in body, not subject — should NOT move.
    _write_mail(bridget.MAIL_DIR, '01.eml',
                'Report ready: dr-zzzz',
                body='see mg-aaaa for details')
    n = bridget._clear_approval_mail('mg-aaaa')
    assert n == 0
    assert (bridget.MAIL_DIR / '01.eml').exists()


def test_clear_approval_mail_ignores_subject_wrong_prefix(bridget):
    # Subject contains mg-id but doesn't start with 'approval needed '.
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: mg-aaaa')
    n = bridget._clear_approval_mail('mg-aaaa')
    assert n == 0
    assert (bridget.MAIL_DIR / '01.eml').exists()


def test_clear_approval_mail_handles_multiple_matches(bridget):
    # Defensive against duplicate approval-needed mails for the same id.
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-aaaa')
    _write_mail(bridget.MAIL_DIR, '02.eml', 'approval needed mg-aaaa retry')
    n = bridget._clear_approval_mail('mg-aaaa')
    assert n == 2
    cur_dir = bridget.MAIL_DIR.parent / 'cur'
    assert (cur_dir / '01.eml').exists()
    assert (cur_dir / '02.eml').exists()


def test_clear_approval_mail_tolerates_unreadable_file(bridget):
    # A directory in place of a file raises on read_text() — must be swallowed.
    bridget.MAIL_DIR.mkdir(parents=True, exist_ok=True)
    (bridget.MAIL_DIR / 'subdir').mkdir()
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-aaaa')
    n = bridget._clear_approval_mail('mg-aaaa')
    assert n == 1


# -- end-to-end through handle_command --------------------------------------

def _fake_mg_idea(args):
    if args[:1] == ['show']:
        return 0, 'ID: mg-aaaa\nType: idea\nAssignee: architect\nStatus: available\n', ''
    if args[:2] == ['mail', 'send']:
        return 0, '', ''
    if args[:1] == ['unshelve']:
        return 0, '', ''
    return 0, '', ''


def test_approve_mg_moves_matching_approval_mail(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-aaaa')
    monkeypatch.setattr(bridget, 'run_mg', _fake_mg_idea)
    reply = bridget.handle_command('approve mg-aaaa')
    assert '✓' in reply
    cur_dir = bridget.MAIL_DIR.parent / 'cur'
    assert (cur_dir / '01.eml').exists()
    assert not (bridget.MAIL_DIR / '01.eml').exists()


def test_reject_mg_moves_matching_approval_mail(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-aaaa')
    monkeypatch.setattr(bridget, 'run_mg', _fake_mg_idea)
    reply = bridget.handle_command('reject mg-aaaa because reasons')
    assert '✓' in reply
    cur_dir = bridget.MAIL_DIR.parent / 'cur'
    assert (cur_dir / '01.eml').exists()


def test_revise_mg_moves_matching_approval_mail(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-aaaa')
    monkeypatch.setattr(bridget, 'run_mg', _fake_mg_idea)
    reply = bridget.handle_command('revise mg-aaaa tweak the bit about X')
    assert '✓' in reply
    cur_dir = bridget.MAIL_DIR.parent / 'cur'
    assert (cur_dir / '01.eml').exists()


def test_approve_mg_leaves_unrelated_approval_mail(bridget, monkeypatch):
    # Approving mg-aaaa must not clear mg-bbbb's approval-needed mail.
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-aaaa')
    _write_mail(bridget.MAIL_DIR, '02.eml', 'approval needed mg-bbbb')
    monkeypatch.setattr(bridget, 'run_mg', _fake_mg_idea)
    reply = bridget.handle_command('approve mg-aaaa')
    assert '✓' in reply
    cur_dir = bridget.MAIL_DIR.parent / 'cur'
    assert (cur_dir / '01.eml').exists()
    assert (bridget.MAIL_DIR / '02.eml').exists()
    assert not (cur_dir / '02.eml').exists()


def test_explain_mg_moves_matching_approval_mail(bridget, monkeypatch):
    # mg-f7b5: explain verb now clears matching approval-needed mail too.
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-aaaa')
    monkeypatch.setattr(bridget, 'run_mg', _fake_mg_idea)
    reply = bridget.handle_command('explain mg-aaaa what is this about')
    assert '✓' in reply
    cur_dir = bridget.MAIL_DIR.parent / 'cur'
    assert (cur_dir / '01.eml').exists()
    assert not (bridget.MAIL_DIR / '01.eml').exists()


def test_explain_mg_reply_reports_cleared_count(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-aaaa')
    monkeypatch.setattr(bridget, 'run_mg', _fake_mg_idea)
    reply = bridget.handle_command('explain mg-aaaa why')
    assert 'cleared 1 related mail' in reply


def test_explain_mg_leaves_unrelated_approval_mail(bridget, monkeypatch):
    # Explaining mg-aaaa must not clear mg-bbbb's approval-needed mail.
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-aaaa')
    _write_mail(bridget.MAIL_DIR, '02.eml', 'approval needed mg-bbbb')
    monkeypatch.setattr(bridget, 'run_mg', _fake_mg_idea)
    reply = bridget.handle_command('explain mg-aaaa why')
    assert '✓' in reply
    cur_dir = bridget.MAIL_DIR.parent / 'cur'
    assert (cur_dir / '01.eml').exists()
    assert (bridget.MAIL_DIR / '02.eml').exists()
    assert not (cur_dir / '02.eml').exists()


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
