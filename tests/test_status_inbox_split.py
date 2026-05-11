"""Tests for the bridget status → status + inbox split (mg-91de).

`status` now shows in-flight work categorized by type
(Reports / Designs / Bugs / Tasks / Other). `inbox` shows the decide queue
(unread mail + pending approvals + pending Reports). The two views MUST
NOT duplicate each other's content.
"""
import importlib.util
import json
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


def _ndjson(items: list[dict]) -> str:
    return '\n'.join(json.dumps(i) for i in items) + '\n'


# -- categorize_in_flight ---------------------------------------------------

def test_categorize_buckets_each_type(bridget):
    items = [
        {'id': 'dr-1', 'type': 'report'},
        {'id': 'mg-1', 'type': 'idea'},
        {'id': 'mg-2', 'type': 'bug'},
        {'id': 'mg-3', 'type': 'task'},
    ]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Reports']] == ['dr-1']
    assert [i['id'] for i in buckets['Designs']] == ['mg-1']
    assert [i['id'] for i in buckets['Bugs']] == ['mg-2']
    assert [i['id'] for i in buckets['Tasks']] == ['mg-3']
    assert buckets['Other'] == []


def test_categorize_unknown_type_goes_to_other(bridget):
    items = [
        {'id': 'mg-q', 'type': 'qa'},
        {'id': 'mg-x', 'type': 'spike'},
        {'id': 'mg-?'},  # no type at all
    ]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Other']] == ['mg-q', 'mg-x', 'mg-?']
    assert buckets['Reports'] == []
    assert buckets['Designs'] == []
    assert buckets['Bugs'] == []
    assert buckets['Tasks'] == []


def test_categorize_empty_input(bridget):
    buckets = bridget.categorize_in_flight([])
    assert all(buckets[s] == [] for s in bridget.STATUS_SECTION_ORDER)


# -- derive_review_label ----------------------------------------------------

def test_review_label_report_with_unread_mail(bridget):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: dr-aaaa')
    item = {'id': 'dr-aaaa', 'type': 'report', 'status': 'available'}
    assert bridget.derive_review_label(item) == 'review'


def test_review_label_report_no_matching_mail(bridget):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: dr-bbbb')
    item = {'id': 'dr-aaaa', 'type': 'report', 'status': 'claimed'}
    assert bridget.derive_review_label(item) == 'claimed'


def test_review_label_report_no_mail_dir(bridget):
    # MAIL_DIR doesn't exist (no mails ever written) → no "review" label.
    item = {'id': 'dr-aaaa', 'type': 'report', 'status': 'available'}
    assert bridget.derive_review_label(item) == 'available'


def test_review_label_idea_returns_status_verbatim(bridget):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: mg-1111')
    item = {'id': 'mg-1111', 'type': 'idea', 'status': 'claimed'}
    # Non-report items don't consult the mailbox.
    assert bridget.derive_review_label(item) == 'claimed'


def test_review_label_task_returns_status_verbatim(bridget):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: mg-2222')
    item = {'id': 'mg-2222', 'type': 'task', 'status': 'available'}
    assert bridget.derive_review_label(item) == 'available'


def test_review_label_report_with_only_read_mail(bridget):
    # A Report-ready mail in cur/ (read) doesn't trigger the review label —
    # only unread mail in new/ counts.
    cur_dir = bridget.MAIL_DIR.parent / 'cur'
    _write_mail(cur_dir, '01.eml', 'Report ready: dr-aaaa')
    item = {'id': 'dr-aaaa', 'type': 'report', 'status': 'claimed'}
    assert bridget.derive_review_label(item) == 'claimed'


# -- get_status_summary -----------------------------------------------------

def test_status_renders_four_sections_in_order(bridget, monkeypatch):
    items = [
        {'id': 'dr-1', 'type': 'report', 'status': 'available',
         'title': 'a report'},
        {'id': 'mg-i1', 'type': 'idea', 'status': 'available',
         'title': 'an idea'},
        {'id': 'mg-b1', 'type': 'bug', 'status': 'claimed',
         'title': 'a bug'},
        {'id': 'mg-t1', 'type': 'task', 'status': 'claimed',
         'title': 'a task'},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    # Section headers appear in fixed order.
    pr = summary.index('**Reports**')
    pd = summary.index('**Designs**')
    pb = summary.index('**Bugs**')
    pt = summary.index('**Tasks**')
    assert pr < pd < pb < pt
    # Item lines: no leading bullet, "[id] label: title".
    assert '[dr-1] available: a report' in summary
    assert '[mg-i1] available: an idea' in summary
    assert '[mg-b1] claimed: a bug' in summary
    assert '[mg-t1] claimed: a task' in summary


def test_status_omits_empty_sections(bridget, monkeypatch):
    items = [
        {'id': 'mg-t1', 'type': 'task', 'status': 'claimed', 'title': 'just a task'},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '**Tasks**' in summary
    assert '**Reports**' not in summary
    assert '**Designs**' not in summary
    assert '**Bugs**' not in summary
    # No filler.
    assert '(none)' not in summary


def test_status_all_empty_returns_no_work(bridget, monkeypatch):
    monkeypatch.setattr(bridget, 'run_mg', lambda args: (0, '', ''))
    assert bridget.get_status_summary() == 'No work in flight.'


def test_status_excludes_archived_and_shelved(bridget, monkeypatch):
    items = [
        {'id': 'mg-x', 'type': 'task', 'status': 'archived', 'title': 'old'},
        {'id': 'mg-y', 'type': 'task', 'status': 'shelved', 'title': 'parked'},
        {'id': 'mg-z', 'type': 'task', 'status': 'done', 'title': 'finished'},
        {'id': 'mg-a', 'type': 'task', 'status': 'claimed', 'title': 'live'},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '[mg-a]' in summary
    for stale in ('mg-x', 'mg-y', 'mg-z'):
        assert stale not in summary


def test_status_includes_pending_status(bridget, monkeypatch):
    items = [
        {'id': 'mg-p', 'type': 'task', 'status': 'pending', 'title': 'pending one'},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '[mg-p] pending: pending one' in summary


def test_status_review_label_appears_when_mail_matches(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'Report ready: dr-aaaa')
    items = [
        {'id': 'dr-aaaa', 'type': 'report', 'status': 'available',
         'title': 'a report awaiting reply'},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '[dr-aaaa] review:' in summary
    assert 'available' not in summary.split('[dr-aaaa] review:')[1].split('\n')[0]


def test_status_other_section_for_unknown_type(bridget, monkeypatch):
    items = [
        {'id': 'mg-q', 'type': 'qa', 'status': 'claimed', 'title': 'qa item'},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '**Other**' in summary
    assert '[mg-q] claimed: qa item' in summary


def test_status_truncates_long_title(bridget, monkeypatch):
    items = [
        {'id': 'mg-t', 'type': 'task', 'status': 'claimed', 'title': 'x' * 200},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    # Title is capped at 80 chars.
    line = next(l for l in summary.splitlines() if l.startswith('[mg-t]'))
    title_part = line.split(': ', 1)[1]
    assert len(title_part) == 80


def test_status_does_not_include_mail_or_approvals(bridget, monkeypatch):
    # The whole point of the split: status MUST NOT duplicate inbox content.
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-1111')
    _write_mail(bridget.MAIL_DIR, '02.eml', 'Report ready: dr-2222')
    items = [
        {'id': 'mg-t1', 'type': 'task', 'status': 'claimed', 'title': 'work'},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert 'Unread mail' not in summary
    assert 'Pending approvals' not in summary
    assert 'Pending reports' not in summary


# -- get_inbox_summary ------------------------------------------------------

def test_inbox_shows_mail_count_and_listing(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'hello from architect mg-1111',
                sender='architect')
    monkeypatch.setattr(bridget, 'run_mg', lambda args: (0, '', ''))
    summary = bridget.get_inbox_summary()
    assert 'Unread mail to human: **1**' in summary
    assert 'architect' in summary


def test_inbox_shows_approvals_and_reports(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-1111')
    _write_mail(bridget.MAIL_DIR, '02.eml', 'Report ready: dr-2222')
    monkeypatch.setattr(bridget, 'run_mg', lambda args: (0, '', ''))
    summary = bridget.get_inbox_summary()
    assert 'Pending approvals:' in summary
    assert 'approval needed mg-1111' in summary
    assert 'Pending reports:' in summary
    assert 'Report ready: dr-2222' in summary


def test_inbox_does_not_show_in_flight_items(bridget, monkeypatch):
    # And the reverse: inbox MUST NOT duplicate status content.
    items = [
        {'id': 'mg-t1', 'type': 'task', 'status': 'claimed', 'title': 'work'},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_inbox_summary()
    assert '[mg-t1]' not in summary
    assert 'Reports**' not in summary  # no in-flight section header
    assert 'Tasks**' not in summary


def test_inbox_empty(bridget, monkeypatch):
    monkeypatch.setattr(bridget, 'run_mg', lambda args: (0, '', ''))
    summary = bridget.get_inbox_summary()
    assert 'Unread mail to human: **0**' in summary
    assert 'Pending approvals:' not in summary
    assert 'Pending reports:' not in summary


# -- handle_command integration --------------------------------------------

def test_handle_command_inbox_returns_inbox_summary(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-1111')
    monkeypatch.setattr(bridget, 'run_mg', lambda args: (0, '', ''))
    reply = bridget.handle_command('inbox')
    assert 'Unread mail to human:' in reply
    assert 'Pending approvals:' in reply


def test_handle_command_status_returns_status_summary(bridget, monkeypatch):
    _write_mail(bridget.MAIL_DIR, '01.eml', 'approval needed mg-1111')
    items = [
        {'id': 'mg-t1', 'type': 'task', 'status': 'claimed', 'title': 'work'},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    monkeypatch.setattr(bridget, 'run_pogo', lambda args: (0, '', ''))
    reply = bridget.handle_command('status')
    # status no longer carries mail/approvals content.
    assert 'Unread mail' not in reply
    assert 'Pending approvals' not in reply
    assert '**Tasks**' in reply


def test_command_list_lists_both_status_and_inbox(bridget):
    cl = bridget.COMMAND_LIST
    assert '`status`' in cl
    assert '`inbox`' in cl
    # New status description mentions the categorized layout.
    assert 'Reports' in cl
    assert 'Designs' in cl


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
