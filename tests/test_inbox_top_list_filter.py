"""Tests for get_inbox_summary top list filtering closed-mg-id mails (mg-472c).

The 'Unread mail to human' top list at the head of inbox summary now hides
approval-needed and Report-ready mails whose referenced mg-id has a closed
status (shelved/done/archived). This matches the bucket-level filter that
scan_pending_approvals / scan_pending_reports already apply.

Generic mails (no mg-id, or subjects that don't match the approval/Report
prefixes) always pass through, regardless of any mg-id mentioned in the body.
Defensive: when `mg show` fails (rc != 0), the mail is kept (false positive
preferred over false negative).
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


def _fake_mg_status(status_by_id, rc_by_id=None):
    """Build a run_mg replacement that answers `show` from a status table."""
    rc_by_id = rc_by_id or {}

    def fake(args):
        if args[:1] == ['show'] and len(args) >= 2:
            mid = args[1]
            rc = rc_by_id.get(mid, 0)
            if rc != 0:
                return rc, '', 'not found'
            status = status_by_id.get(mid, 'available')
            return 0, f'ID:        {mid}\nType:      bug\nStatus:    {status}\n', ''
        return 0, '', ''

    return fake


# -- _inbox_top_list_should_hide unit tests --------------------------------

def test_top_list_hides_approval_for_closed_mg_id(bridget, monkeypatch):
    p = _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-aaaa')
    monkeypatch.setattr(bridget, 'run_mg', _fake_mg_status({'mg-aaaa': 'done'}))
    assert bridget._inbox_top_list_should_hide(p) is True


def test_top_list_hides_report_for_closed_mg_id(bridget, monkeypatch):
    p = _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: mg-cccc')
    monkeypatch.setattr(bridget, 'run_mg', _fake_mg_status({'mg-cccc': 'shelved'}))
    assert bridget._inbox_top_list_should_hide(p) is True


def test_top_list_keeps_approval_for_open_mg_id(bridget, monkeypatch):
    p = _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-bbbb')
    monkeypatch.setattr(bridget, 'run_mg', _fake_mg_status({'mg-bbbb': 'available'}))
    assert bridget._inbox_top_list_should_hide(p) is False


def test_top_list_keeps_generic_mail_no_mg_id(bridget, monkeypatch):
    p = _write_mail(bridget.MAIL_DIR, '01.eml', 'just an FYI')
    monkeypatch.setattr(bridget, 'run_mg', lambda args: (0, '', ''))
    assert bridget._inbox_top_list_should_hide(p) is False


def test_top_list_keeps_generic_subject_with_mg_id_in_body(bridget, monkeypatch):
    # An mg-id buried in the body but a non-approval/non-Report subject must
    # stay — only approval/Report prefixes get filtered.
    p = _write_mail(bridget.MAIL_DIR, '01.eml', 'random update',
                    body='FYI mg-aaaa is done')
    monkeypatch.setattr(bridget, 'run_mg', _fake_mg_status({'mg-aaaa': 'done'}))
    assert bridget._inbox_top_list_should_hide(p) is False


def test_top_list_keeps_approval_mg_show_failure(bridget, monkeypatch):
    # Defensive: mg show rc != 0 → _mg_item_closed False → keep mail.
    p = _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-eeee')
    monkeypatch.setattr(
        bridget, 'run_mg',
        _fake_mg_status({}, rc_by_id={'mg-eeee': 1}),
    )
    assert bridget._inbox_top_list_should_hide(p) is False


def test_top_list_keeps_approval_subject_without_mg_id(bridget, monkeypatch):
    # 'approval needed' subjects that lack a parseable mg-id (e.g. legacy
    # dr-XXXX-only subjects) pass through — no id to check.
    p = _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed dr-abcd')
    called = []

    def fake(args):
        called.append(args)
        return 0, '', ''
    monkeypatch.setattr(bridget, 'run_mg', fake)
    assert bridget._inbox_top_list_should_hide(p) is False
    # And we didn't spawn mg show for a subject without an mg-id.
    assert called == []


# -- get_inbox_summary integration -----------------------------------------

def test_inbox_summary_filters_closed_approval_and_report(bridget, monkeypatch):
    # Seed three: closed approval, open approval, closed Report.
    # Expect count==1 and only mg-bbbb in the per-mail entry list.
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-aaaa',
                body='see mg-aaaa')
    _write_mail(bridget.MAIL_DIR, '02.eml', 'approval needed mg-bbbb',
                body='see mg-bbbb')
    _write_mail(bridget.MAIL_DIR, '03.eml', 'Report ready: mg-cccc',
                body='see mg-cccc')
    monkeypatch.setattr(
        bridget, 'run_mg',
        _fake_mg_status({
            'mg-aaaa': 'done',
            'mg-bbbb': 'available',
            'mg-cccc': 'done',
        }),
    )
    summary = bridget.get_inbox_summary()
    assert '📬 Unread mail to human: **1**' in summary
    assert 'mg-bbbb' in summary
    assert 'mg-aaaa' not in summary.split('Pending')[0]  # not in top list
    assert 'mg-cccc' not in summary.split('Pending')[0]  # not in top list


def test_inbox_summary_keeps_generic_mail_regardless_of_status(bridget, monkeypatch):
    # Generic mail (no mg-id, no approval/Report prefix) always kept.
    _write_mail(bridget.MAIL_DIR, '01.eml', 'just an FYI from director')
    monkeypatch.setattr(bridget, 'run_mg', lambda args: (0, '', ''))
    summary = bridget.get_inbox_summary()
    assert '📬 Unread mail to human: **1**' in summary
    assert 'just an FYI' in summary or 'FYI' in summary


def test_inbox_summary_keeps_mail_with_body_mg_id_generic_subject(bridget, monkeypatch):
    # mg-id only in body + generic subject → kept regardless of status.
    _write_mail(bridget.MAIL_DIR, '01.eml', 'kickoff note',
                body='related to mg-dddd which is done')
    monkeypatch.setattr(bridget, 'run_mg', _fake_mg_status({'mg-dddd': 'done'}))
    summary = bridget.get_inbox_summary()
    assert '📬 Unread mail to human: **1**' in summary


def test_inbox_summary_defensive_mg_show_failure_keeps_mail(bridget, monkeypatch):
    # mg show rc != 0 → kept (false positive > false negative for visibility).
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-eeee')
    monkeypatch.setattr(
        bridget, 'run_mg',
        _fake_mg_status({}, rc_by_id={'mg-eeee': 1}),
    )
    summary = bridget.get_inbox_summary()
    assert '📬 Unread mail to human: **1**' in summary
    assert 'mg-eeee' in summary


def test_inbox_summary_count_zero_when_all_closed(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-1111')
    _write_mail(bridget.MAIL_DIR, '02.eml', 'Report ready: mg-2222')
    monkeypatch.setattr(
        bridget, 'run_mg',
        _fake_mg_status({'mg-1111': 'done', 'mg-2222': 'shelved'}),
    )
    summary = bridget.get_inbox_summary()
    assert '📬 Unread mail to human: **0**' in summary


def test_inbox_summary_count_unaffected_by_dr_id_subjects(bridget, monkeypatch):
    # dr-XXXX subjects don't match mg-[0-9a-f]+, so the filter passes them through.
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: dr-abcd')
    monkeypatch.setattr(bridget, 'run_mg', lambda args: (0, '', ''))
    summary = bridget.get_inbox_summary()
    assert '📬 Unread mail to human: **1**' in summary
    assert 'dr-abcd' in summary


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
