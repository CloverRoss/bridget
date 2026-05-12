"""Regression test: bridget always delivers approval-needed DMs (mg-a0be).

Originally added in mg-bc75: when ~/.pogo/preapproval.json was enabled,
bridge silently moved 'approval needed' mails to cur/ and skipped the DM.

That behavior was reverted in mg-a0be — it was masking designs with open
questions for the user. Dedupe of auto-approvable cases is now mayor's
responsibility (the pre-approval mail-from-human pattern). Bridge always
delivers the DM regardless of preapproval state, and always leaves the
mail in new/ for the human to dismiss normally.

This test file keeps the original fixture scaffolding but inverts the
assertions: preapproval=True must NOT suppress the DM, and the mail must
NOT be auto-moved to cur/.
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


def _write_mail(mail_dir: Path, name: str, subject: str,
                body: str = 'body text', sender: str = 'architect') -> Path:
    mail_dir.mkdir(parents=True, exist_ok=True)
    p = mail_dir / name
    p.write_text(f"From: {sender}\nSubject: {subject}\n\n{body}\n")
    return p


def _run_watch_once(bridget_mod, user):
    """Drive watch_mailbox through exactly one poll iteration.

    The function loops until `client.is_closed()` is True, so we patch the
    check to return False on the first call (entering the loop body) and
    True on the next (exiting). asyncio.sleep is patched to a no-op so the
    test doesn't actually wait POLL_INTERVAL seconds.
    """
    state = {'calls': 0}

    def fake_is_closed():
        state['calls'] += 1
        return state['calls'] > 1

    bridget_mod.client.is_closed = fake_is_closed

    async def no_sleep(_):
        return None

    original_sleep = bridget_mod.asyncio.sleep
    bridget_mod.asyncio.sleep = no_sleep
    try:
        asyncio.run(bridget_mod.watch_mailbox(user))
    finally:
        bridget_mod.asyncio.sleep = original_sleep


def _stub_startup(bridget_mod, monkeypatch):
    """Make the startup branch deterministic: SEEN_FILE present so we
    take the status-summary branch, and the summary itself is stubbed
    to avoid shelling out to `mg`."""
    bridget_mod.SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    bridget_mod.SEEN_FILE.write_text('')
    monkeypatch.setattr(bridget_mod, 'get_status_summary', lambda: 'test summary')


def test_dm_delivered_when_preapproval_on(bridget, monkeypatch):
    """Preapproval=on must NOT suppress an approval-needed DM (mg-a0be)."""
    bridget.save_preapproval({'enabled': True, 'fast': False})
    _stub_startup(bridget, monkeypatch)
    fname = '01.eml'
    _write_mail(bridget.MAIL_DIR, fname, 'approval needed mg-abcd1')

    user = AsyncMock()
    _run_watch_once(bridget, user)

    assert (bridget.MAIL_DIR / fname).exists(), \
        'approval-needed mail must stay in new/ regardless of preapproval state'
    assert not (bridget.MAIL_DIR.parent / 'cur' / fname).exists(), \
        'bridge must not auto-move approval-needed mails'

    sent_combined = '\n'.join(
        (call.args[0] if call.args else '') for call in user.send.call_args_list
    )
    assert 'approval needed mg-abcd1' in sent_combined, (
        'expected a Discord DM for the approval-needed mail '
        'even when preapproval is enabled'
    )

    seen = bridget.load_seen()
    assert fname in seen


def test_dm_delivered_when_preapproval_off(bridget, monkeypatch):
    bridget.save_preapproval({'enabled': False, 'fast': False})
    _stub_startup(bridget, monkeypatch)
    fname = '02.eml'
    _write_mail(bridget.MAIL_DIR, fname, 'approval needed mg-abcd2')

    user = AsyncMock()
    _run_watch_once(bridget, user)

    assert (bridget.MAIL_DIR / fname).exists(), 'mail should stay in new/'
    assert not (bridget.MAIL_DIR.parent / 'cur' / fname).exists()

    sent_combined = '\n'.join(
        (call.args[0] if call.args else '') for call in user.send.call_args_list
    )
    assert 'approval needed mg-abcd2' in sent_combined, (
        'expected a Discord DM for the approval-needed mail when preapproval off'
    )

    seen = bridget.load_seen()
    assert fname in seen


def test_report_ready_unaffected_when_preapproval_on(bridget, monkeypatch):
    """Report-ready mails use a different prefix and must still be DM'd."""
    bridget.save_preapproval({'enabled': True, 'fast': True})
    _stub_startup(bridget, monkeypatch)
    fname = '03.eml'
    _write_mail(bridget.MAIL_DIR, fname, 'Report ready: dr-abcd3', sender='director')

    user = AsyncMock()
    _run_watch_once(bridget, user)

    assert (bridget.MAIL_DIR / fname).exists(), 'Report mail should stay in new/'
    assert not (bridget.MAIL_DIR.parent / 'cur' / fname).exists()

    sent_combined = '\n'.join(
        (call.args[0] if call.args else '') for call in user.send.call_args_list
    )
    assert 'Report ready: dr-abcd3' in sent_combined


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
