"""Tests for `read dr-XXXX` honoring PROTECTED_SUBJECT_PREFIXES (mg-e818).

`read` must NOT auto-mark-read mails whose Subject starts with an entry of
PROTECTED_SUBJECT_PREFIXES — those mails carry an action-required signal
that `inbox`/`scan_pending_approvals` surface from `human/new/` only. The
matching behavior `dismiss` already implements via protect_actionable=True
is extended to `read` here.

After mg-d3d7 the mg-id surface for `read` returns a hint (no mail touched
at all), so these tests cover dr-ids — the only remaining `read` path that
inspects mail.
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


def test_read_approval_needed_mail_stays_in_new(bridget):
    (bridget.DESIGNS_DIR / 'dr-aaaa.md').write_text(
        '---\nmg_id: dr-aaaa\nstatus: awaiting-approval\n---\n# d\nbody\n'
    )
    mail_path = _write_mail(bridget.MAIL_DIR, 'm.txt', 'approval needed dr-aaaa')
    reply = bridget.handle_command('read dr-aaaa')
    assert isinstance(reply, list)
    joined = '\n'.join(reply)
    # design surfaced
    assert '📐' in joined
    assert 'dr-aaaa' in joined
    # mail stayed in new/ (protected — action verb required to mark read)
    assert mail_path.exists()
    cur_path = bridget.MAIL_DIR.parent / 'cur' / 'm.txt'
    assert not cur_path.exists()
    # mail footer reflects action-required state
    assert 'unread (action required)' in joined


def test_read_report_ready_mail_stays_in_new(bridget):
    (bridget.DESIGNS_DIR / 'dr-bbbb.md').write_text(
        '---\nmg_id: dr-bbbb\nstatus: drafted\n---\n# r\nbody\n'
    )
    mail_path = _write_mail(
        bridget.MAIL_DIR, 'r.txt', 'Report ready: dr-bbbb', sender='director'
    )
    reply = bridget.handle_command('read dr-bbbb')
    assert isinstance(reply, list)
    joined = '\n'.join(reply)
    assert 'dr-bbbb' in joined
    assert mail_path.exists()
    assert not (bridget.MAIL_DIR.parent / 'cur' / 'r.txt').exists()
    assert 'unread (action required)' in joined


def test_read_non_protected_mail_still_moves_to_cur(bridget):
    # Regression: a plain mail (no PROTECTED_SUBJECT_PREFIXES match) must
    # still be auto-marked-read on `read dr-XXXX` — protection is scoped
    # to action-required subjects only.
    (bridget.DESIGNS_DIR / 'dr-cccc.md').write_text(
        '---\nmg_id: dr-cccc\nstatus: drafted\n---\n# d\nbody\n'
    )
    mail_path = _write_mail(
        bridget.MAIL_DIR, 'p.txt', 'approve dr-cccc', sender='human'
    )
    reply = bridget.handle_command('read dr-cccc')
    assert isinstance(reply, list)
    assert not mail_path.exists()
    assert (bridget.MAIL_DIR.parent / 'cur' / 'p.txt').exists()


def test_inbox_after_read_still_shows_pending_approval(bridget):
    # The whole point of the original mg-e818 fix: after `read dr-XXXX` on
    # a pending approval, `scan_pending_approvals` (which only looks at
    # human/new/) must still surface it.
    (bridget.DESIGNS_DIR / 'dr-dddd.md').write_text(
        '---\nmg_id: dr-dddd\nstatus: awaiting-approval\n---\n# d\nbody\n'
    )
    _write_mail(bridget.MAIL_DIR, 'a.txt', 'approval needed dr-dddd')
    pre = bridget.scan_pending_approvals()
    assert any('approval needed dr-dddd' in s for s in pre)

    bridget.handle_command('read dr-dddd')

    post = bridget.scan_pending_approvals()
    assert any('approval needed dr-dddd' in s for s in post), (
        'protected approval mail must remain pending after read'
    )


def test_read_protected_mail_on_cur_renders_read_state(bridget, monkeypatch):
    # Sanity: when a protected mail has already been resolved (lives in
    # cur/), the footer should say "read", not "unread (action required)".
    (bridget.DESIGNS_DIR / 'dr-eeee.md').write_text(
        '---\nmg_id: dr-eeee\nstatus: approved\n---\n# d\nbody\n'
    )
    monkeypatch.setattr(bridget, 'find_mails_for', lambda _id: [
        (
            Path('/tmp/fake'),
            {'from': 'architect', 'subject': 'approval needed dr-eeee', 'body': 'b'},
            'cur',
        ),
    ])
    reply = bridget.handle_command('read dr-eeee')
    joined = '\n'.join(reply)
    assert '_(read)_' in joined
    assert 'unread (action required)' not in joined


def test_read_mg_id_does_not_touch_mail(bridget):
    # New mg-d3d7 behavior: `read mg-XXXX` is now inert toward mail. Even
    # an unprotected mail referencing the id stays in new/ since the
    # handler short-circuits to the hint without consulting maildir.
    (bridget.DESIGNS_DIR / 'mg-ffff.md').write_text(
        '---\nmg_id: mg-ffff\nstatus: awaiting-approval\n---\n# d\nbody\n'
    )
    mail_path = _write_mail(
        bridget.MAIL_DIR, 'q.txt', 'approve mg-ffff', sender='human'
    )
    reply = bridget.handle_command('read mg-ffff')
    assert isinstance(reply, str)
    assert 'open mg-XXXX' in reply
    # mail untouched
    assert mail_path.exists()
    assert not (bridget.MAIL_DIR.parent / 'cur' / 'q.txt').exists()


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
