"""Tests for the Pending Reports status block + actionable-mail dismiss
protection (mg-af02 / mg-dbf6).

Covers:
- scan_pending_reports(): empty, single match, multi match, mixed with
  approvals, malformed file tolerated.
- mark_mail_read(protect_actionable=True): Report-ready and approval-needed
  mails stay in new/; counted total excludes them; other subjects pass
  through. mg-id-scoped and all_unread paths both honor the flag.
- mark_mail_read(protect_actionable=False) (default): both protected
  subjects ARE cleared (the approve/reject/revise/explain code path).
- Integration via handle_command:
  - dismiss all leaves Reports and approval-needed mails unread.
  - approve mg-XXXX clears the approval-needed mail for that id.
  - approve dr-XXXX clears the Report mail for that dr-id.
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
                sender: str = 'director') -> Path:
    mail_dir.mkdir(parents=True, exist_ok=True)
    p = mail_dir / name
    p.write_text(f"From: {sender}\nSubject: {subject}\n\n{body}\n")
    return p


# -- scan_pending_reports ----------------------------------------------------

def test_scan_pending_reports_no_dir(bridget):
    # MAIL_DIR may not exist on fresh test home; function should not raise.
    if bridget.MAIL_DIR.exists():
        for p in bridget.MAIL_DIR.iterdir():
            p.unlink()
        bridget.MAIL_DIR.rmdir()
    assert bridget.scan_pending_reports() == []


def test_scan_pending_reports_empty_dir(bridget):
    bridget.MAIL_DIR.mkdir(parents=True, exist_ok=True)
    assert bridget.scan_pending_reports() == []


def test_scan_pending_reports_one_match(bridget):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: dr-abcd')
    assert bridget.scan_pending_reports() == ['Report ready: dr-abcd']


def test_scan_pending_reports_multi_match(bridget):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: dr-aaaa')
    _write_mail(bridget.MAIL_DIR, '02.eml', 'Report ready: dr-bbbb')
    result = bridget.scan_pending_reports()
    assert result == ['Report ready: dr-aaaa', 'Report ready: dr-bbbb']


def test_scan_pending_reports_mixed_with_approvals(bridget):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-1111')
    _write_mail(bridget.MAIL_DIR, '02.eml', 'Report ready: dr-2222')
    _write_mail(bridget.MAIL_DIR, '03.eml', 'just a regular mail')
    # Reports list ignores approvals and unrelated subjects.
    assert bridget.scan_pending_reports() == ['Report ready: dr-2222']
    # And the approvals scanner still sees its own.
    assert bridget.scan_pending_approvals() == ['approval needed mg-1111']


def test_scan_pending_reports_malformed_file_tolerated(bridget):
    bridget.MAIL_DIR.mkdir(parents=True, exist_ok=True)
    # An unreadable file shouldn't blow up the scan — write a directory
    # in its place, which will raise on read_text() and be swallowed.
    (bridget.MAIL_DIR / 'subdir').mkdir()
    _write_mail(bridget.MAIL_DIR, 'ok.eml', 'Report ready: dr-zzzz')
    assert bridget.scan_pending_reports() == ['Report ready: dr-zzzz']


def test_scan_pending_reports_ignores_similar_but_distinct_prefix(bridget):
    # "Project ready:" mails are NOT yet protected/listed (deferred per design).
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Project ready: dr-aaaa')
    assert bridget.scan_pending_reports() == []


# -- mark_mail_read(protect_actionable=True) --------------------------------

def test_protect_actionable_true_keeps_report_mail(bridget):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: dr-aaaa')
    n = bridget.mark_mail_read(all_unread=True, protect_actionable=True)
    assert n == 0
    assert (bridget.MAIL_DIR / '01.eml').exists()


def test_protect_actionable_true_keeps_approval_needed_mail(bridget):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-1111')
    n = bridget.mark_mail_read(all_unread=True, protect_actionable=True)
    assert n == 0
    assert (bridget.MAIL_DIR / '01.eml').exists()


def test_protect_actionable_true_clears_unrelated_mails(bridget):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: dr-aaaa')
    _write_mail(bridget.MAIL_DIR, '02.eml', 'just an FYI')
    _write_mail(bridget.MAIL_DIR, '03.eml', 'approval needed mg-1111')
    _write_mail(bridget.MAIL_DIR, '04.eml', 'random update')
    n = bridget.mark_mail_read(all_unread=True, protect_actionable=True)
    # Only the two unrelated mails were moved.
    assert n == 2
    assert (bridget.MAIL_DIR / '01.eml').exists()  # Report preserved
    assert (bridget.MAIL_DIR / '03.eml').exists()  # approval preserved
    assert not (bridget.MAIL_DIR / '02.eml').exists()
    assert not (bridget.MAIL_DIR / '04.eml').exists()


def test_protect_actionable_true_id_scoped_keeps_report(bridget):
    # id-scoped dismiss for the Report mail's own id must still preserve it.
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: dr-aaaa',
                body='see dr-aaaa for details')
    n = bridget.mark_mail_read(mg_id='dr-aaaa', protect_actionable=True)
    assert n == 0
    assert (bridget.MAIL_DIR / '01.eml').exists()


def test_protect_actionable_true_id_scoped_keeps_approval(bridget):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-1111',
                body='see mg-1111 for details')
    n = bridget.mark_mail_read(mg_id='mg-1111', protect_actionable=True)
    assert n == 0
    assert (bridget.MAIL_DIR / '01.eml').exists()


# -- mark_mail_read(protect_actionable=False, default) ----------------------

def test_protect_actionable_false_clears_report(bridget):
    # The approve/reject/revise/explain path leaves protect_actionable at False;
    # both protected subjects must clear normally.
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: dr-aaaa',
                body='see dr-aaaa for details')
    n = bridget.mark_mail_read(mg_id='dr-aaaa')
    assert n == 1
    assert not (bridget.MAIL_DIR / '01.eml').exists()


def test_protect_actionable_false_clears_approval(bridget):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-1111',
                body='see mg-1111 for details')
    n = bridget.mark_mail_read(mg_id='mg-1111')
    assert n == 1
    assert not (bridget.MAIL_DIR / '01.eml').exists()


def test_protect_actionable_false_all_unread_clears_everything(bridget):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: dr-aaaa')
    _write_mail(bridget.MAIL_DIR, '02.eml', 'approval needed mg-1111')
    _write_mail(bridget.MAIL_DIR, '03.eml', 'plain mail')
    n = bridget.mark_mail_read(all_unread=True)
    assert n == 3
    assert not any(bridget.MAIL_DIR.iterdir())


# -- handle_command integration ---------------------------------------------

def test_handle_command_dismiss_all_protects_actionable_mails(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: dr-aaaa')
    _write_mail(bridget.MAIL_DIR, '02.eml', 'approval needed mg-1111')
    _write_mail(bridget.MAIL_DIR, '03.eml', 'inbox noise')
    monkeypatch.setattr(bridget, 'log_mail_action', lambda *_a, **_k: None)
    reply = bridget.handle_command('dismiss all')
    assert '✓' in reply
    # Only the noise mail was cleared.
    assert (bridget.MAIL_DIR / '01.eml').exists()
    assert (bridget.MAIL_DIR / '02.eml').exists()
    assert not (bridget.MAIL_DIR / '03.eml').exists()


def test_handle_command_dismiss_id_protects_report(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: dr-aaaa',
                body='see dr-aaaa for details')
    monkeypatch.setattr(bridget, 'log_mail_action', lambda *_a, **_k: None)
    reply = bridget.handle_command('dismiss dr-aaaa')
    assert '✓' in reply
    assert (bridget.MAIL_DIR / '01.eml').exists()


def test_handle_command_approve_mg_clears_approval_mail(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-1111',
                body='please act on mg-1111')

    def fake_run_mg(args):
        if args[:1] == ['show']:
            return 0, 'ID: mg-1111\nType: idea\nStatus: available\n', ''
        if args[:2] == ['mail', 'send']:
            return 0, '', ''
        return 0, '', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    reply = bridget.handle_command('approve mg-1111')
    assert '✓' in reply
    # approve path leaves protect_actionable at False, so the approval mail clears.
    assert not (bridget.MAIL_DIR / '01.eml').exists()


def test_handle_command_approve_dr_clears_report_mail(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: dr-aaaa',
                body='please act on dr-aaaa')

    def fake_run_mg(args):
        if args[:1] == ['show']:
            return 0, 'ID: dr-aaaa\nType: report\nStatus: available\n', ''
        if args[:2] == ['mail', 'send']:
            return 0, '', ''
        return 0, '', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    reply = bridget.handle_command('approve dr-aaaa')
    assert '✓' in reply
    assert not (bridget.MAIL_DIR / '01.eml').exists()


def test_handle_command_revise_dr_clears_report_mail(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: dr-aaaa',
                body='please act on dr-aaaa')

    def fake_run_mg(args):
        if args[:1] == ['show']:
            return 0, 'ID: dr-aaaa\nType: report\nStatus: available\n', ''
        if args[:2] == ['mail', 'send']:
            return 0, '', ''
        if args[:1] == ['unshelve']:
            return 0, '', ''
        return 0, '', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    reply = bridget.handle_command('revise dr-aaaa try this instead')
    assert '✓' in reply
    assert not (bridget.MAIL_DIR / '01.eml').exists()


# -- inbox view renders the Pending reports block --------------------------
# (mg-91de split: mail / approvals / Reports moved from `status` to `inbox`.)

def test_inbox_includes_pending_reports_block(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: dr-aaaa')
    monkeypatch.setattr(bridget, 'run_mg', lambda args: (0, '', ''))
    summary = bridget.get_inbox_summary()
    assert 'Pending reports:' in summary
    assert 'Report ready: dr-aaaa' in summary


def test_inbox_omits_reports_block_when_none(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'random update')
    monkeypatch.setattr(bridget, 'run_mg', lambda args: (0, '', ''))
    summary = bridget.get_inbox_summary()
    assert 'Pending reports:' not in summary


def test_inbox_shows_approvals_and_reports_both(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-1111')
    _write_mail(bridget.MAIL_DIR, '02.eml', 'Report ready: dr-2222')
    monkeypatch.setattr(bridget, 'run_mg', lambda args: (0, '', ''))
    summary = bridget.get_inbox_summary()
    assert 'Pending approvals:' in summary
    assert 'Pending reports:' in summary
    # Each is its own block — the reports block appears after the approvals one.
    assert summary.index('Pending approvals:') < summary.index('Pending reports:')


# -- COMMAND_LIST text reflects the protection ------------------------------

def test_command_list_mentions_actionable_protection(bridget):
    cl = bridget.COMMAND_LIST
    assert 'actionable' in cl.lower()
    # And both protected categories are named in the dismiss-all line.
    assert 'Reports' in cl
    assert 'design approval' in cl.lower()


# -- module-level constant exposed for future expansion ---------------------

def test_protected_subject_prefixes_constant(bridget):
    assert isinstance(bridget.PROTECTED_SUBJECT_PREFIXES, tuple)
    assert 'Report ready:' in bridget.PROTECTED_SUBJECT_PREFIXES
    assert 'approval needed' in bridget.PROTECTED_SUBJECT_PREFIXES


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
