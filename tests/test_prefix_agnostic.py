"""Tests that bridget's id-bearing commands accept any 2+-letter prefix (mg-f282).

Before mg-f282 the approve/reject/revise/explain command dispatchers were
hard-coded to mg-/dr- via startswith() checks, and the `open` MG_ID_RE was
mg- only. That broke the user's workflow once new prefixes (ds- mayor
design, rp- director report, pj- reserved) entered circulation.

The fix is a single generalization: any `<2+ lowercase letters>-<hex>`
shape routes through the same code path. These tests pin the behavior
for every verb the user invokes against an id (approve/reject/revise/
explain/dismiss/open).
"""
import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / 'bridget'

PREFIXES = ['mg', 'dr', 'ds', 'rp', 'pj']


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
    monkeypatch.setattr(mod, 'mark_mail_read', lambda **_k: 0)
    monkeypatch.setattr(mod, '_clear_approval_mail', lambda _id: 0)
    monkeypatch.setattr(mod, 'log_mail_action', lambda *_a, **_k: None)
    monkeypatch.setattr(mod, 'route_recipient', lambda _id: 'designer')
    return mod


# -- regex constants --------------------------------------------------------

@pytest.mark.parametrize('id_', [f'{p}-abcd' for p in PREFIXES])
def test_mg_id_re_matches_any_known_prefix(bridget, id_):
    assert bridget.MG_ID_RE.match(id_)


@pytest.mark.parametrize('bad', ['notanid', 'mg-xyz!', 'MG-ABCD', '', '-abcd'])
def test_mg_id_re_rejects_non_id_shape(bridget, bad):
    assert not bridget.MG_ID_RE.match(bad)


def test_design_id_re_matches_design_prefixes_only(bridget):
    # mg- and ds- redirect through `open`; dr-/rp-/pj- fall through to the
    # find_mails_for / design_doc read path.
    assert bridget.DESIGN_ID_RE.match('mg-abcd')
    assert bridget.DESIGN_ID_RE.match('ds-abcd')
    assert not bridget.DESIGN_ID_RE.match('dr-abcd')
    assert not bridget.DESIGN_ID_RE.match('rp-abcd')
    assert not bridget.DESIGN_ID_RE.match('pj-abcd')


# -- approve / reject / revise / explain accept every known prefix ---------

@pytest.mark.parametrize('prefix', PREFIXES)
def test_approve_accepts_any_prefix(bridget, monkeypatch, prefix):
    sent = {}

    def fake_run_mg(args):
        sent['args'] = args
        return 0, '', ''
    monkeypatch.setattr(bridget, 'run_mg', fake_run_mg)
    reply = bridget.handle_command(f'approve {prefix}-abcd')
    assert '✓ approve sent' in reply
    assert f'{prefix}-abcd' in reply
    # the mail subject carried the id through unchanged
    assert any(f'--subject=approve {prefix}-abcd' == a for a in sent['args'])


@pytest.mark.parametrize('prefix', PREFIXES)
def test_reject_accepts_any_prefix(bridget, monkeypatch, prefix):
    monkeypatch.setattr(bridget, 'run_mg', lambda _args: (0, '', ''))
    reply = bridget.handle_command(f'reject {prefix}-abcd some reason')
    assert '✓ reject sent' in reply
    assert f'{prefix}-abcd' in reply


@pytest.mark.parametrize('prefix', PREFIXES)
def test_revise_accepts_any_prefix(bridget, monkeypatch, prefix):
    monkeypatch.setattr(bridget, 'run_mg', lambda _args: (0, '', ''))
    reply = bridget.handle_command(f'revise {prefix}-abcd please tighten the wording')
    assert '✓ revise sent' in reply
    assert f'{prefix}-abcd' in reply


@pytest.mark.parametrize('prefix', PREFIXES)
def test_explain_accepts_any_prefix(bridget, monkeypatch, prefix):
    monkeypatch.setattr(bridget, 'run_mg', lambda _args: (0, '', ''))
    reply = bridget.handle_command(f'explain {prefix}-abcd what does this mean')
    assert '✓ explain request sent' in reply
    assert f'{prefix}-abcd' in reply


# -- read: design prefixes redirect; mail/report prefixes fall through -----

@pytest.mark.parametrize('prefix', ['mg', 'ds'])
def test_read_design_prefix_redirects_to_open(bridget, monkeypatch, prefix):
    monkeypatch.setattr(bridget, 'find_mails_for', lambda _id: [])
    reply = bridget.handle_command(f'read {prefix}-abcd')
    assert 'open mg-XXXX' in reply
    assert 'mail message-ids' in reply


@pytest.mark.parametrize('prefix', ['dr', 'rp', 'pj'])
def test_read_non_design_prefix_falls_through(bridget, monkeypatch, prefix):
    # No mail and no design file → 'No mail found' message that still
    # carries the id so callers can see what they asked about.
    monkeypatch.setattr(bridget, 'find_mails_for', lambda _id: [])
    reply = bridget.handle_command(f'read {prefix}-abcd')
    assert 'Usage' not in reply
    assert f'{prefix}-abcd' in reply


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
