"""Tests for the bridget `kickoff pj-XXXX` verb (ds-1482).

Covers:
- `kickoff pj-XXXX` → mail sent to mayor with subject `kickoff pj-XXXX`
  and body `kickoff requested via discord`.
- `kickoff mg-XXXX` → reject with the pj- restriction message (legacy
  mg- projects must be edited manually).
- `kickoff foo` → reject (not a valid id shape).
- `kickoff` (no arg) → usage hint.
- COMMANDS exposes the kickoff entry and the help-menu lists it.
"""
import importlib.util
import os
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


class FakeMg:
    """Captures every run_mg call, returns success for everything."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, args):
        self.calls.append(list(args))
        return 0, '', ''

    def mail_send_call(self) -> list[str]:
        for c in self.calls:
            if c[:2] == ['mail', 'send']:
                return c
        raise AssertionError(f'no mail send call observed; calls={self.calls!r}')


# -- happy path -------------------------------------------------------------

def test_kickoff_pj_routes_to_mayor(bridget, monkeypatch):
    fake = FakeMg()
    monkeypatch.setattr(bridget, 'run_mg', fake)
    monkeypatch.setattr(bridget, 'log_mail_action', lambda *_a, **_k: None)

    reply = bridget.handle_command('kickoff pj-abcd1234')

    assert '✓' in reply
    assert 'pj-abcd1234' in reply
    call = fake.mail_send_call()
    assert call[2] == 'mayor', f'expected target=mayor, got {call!r}'
    assert '--from=human' in call
    assert '--subject=kickoff pj-abcd1234' in call
    assert '--body=kickoff requested via discord' in call


def test_kickoff_pj_lowercases_id(bridget, monkeypatch):
    fake = FakeMg()
    monkeypatch.setattr(bridget, 'run_mg', fake)
    monkeypatch.setattr(bridget, 'log_mail_action', lambda *_a, **_k: None)

    reply = bridget.handle_command('kickoff PJ-ABCD')

    # Body of the design says `parts[1].strip().lower()`, so the routed
    # subject + reply use the lowercased id even though the user typed caps.
    assert '✓' in reply
    assert 'pj-abcd' in reply
    call = fake.mail_send_call()
    assert '--subject=kickoff pj-abcd' in call


def test_kickoff_failure_surfaces_error(bridget, monkeypatch):
    def fake(args):
        if args[:2] == ['mail', 'send']:
            return 1, '', 'mailbox locked'
        return 0, '', ''
    monkeypatch.setattr(bridget, 'run_mg', fake)

    reply = bridget.handle_command('kickoff pj-deadbeef')

    assert '✗' in reply
    assert 'mailbox locked' in reply


# -- rejection paths --------------------------------------------------------

def test_kickoff_mg_prefix_rejected(bridget, monkeypatch):
    # ds-1482 Q3: kickoff is pj-only. Legacy mg- projects are out of scope.
    fake = FakeMg()
    monkeypatch.setattr(bridget, 'run_mg', fake)

    reply = bridget.handle_command('kickoff mg-12345678')

    assert 'pj-' in reply
    assert 'restricted' in reply.lower()
    # And critically: no mail was sent.
    assert not any(c[:2] == ['mail', 'send'] for c in fake.calls), \
        f'unexpected mail send for mg- kickoff: {fake.calls!r}'


@pytest.mark.parametrize('prefix', ['ds', 'rp', 'dr', 'xx'])
def test_kickoff_other_prefixes_rejected(bridget, monkeypatch, prefix):
    # Only pj- is allowed; other 2+-letter families also bounce.
    fake = FakeMg()
    monkeypatch.setattr(bridget, 'run_mg', fake)

    reply = bridget.handle_command(f'kickoff {prefix}-abcd')

    assert 'pj-' in reply
    assert 'restricted' in reply.lower()
    assert not any(c[:2] == ['mail', 'send'] for c in fake.calls)


def test_kickoff_non_id_arg_rejected(bridget, monkeypatch):
    fake = FakeMg()
    monkeypatch.setattr(bridget, 'run_mg', fake)

    reply = bridget.handle_command('kickoff foo')

    assert 'pj-' in reply
    assert not any(c[:2] == ['mail', 'send'] for c in fake.calls)


def test_kickoff_no_arg_gives_usage(bridget, monkeypatch):
    fake = FakeMg()
    monkeypatch.setattr(bridget, 'run_mg', fake)

    reply = bridget.handle_command('kickoff')

    assert 'Usage' in reply
    assert 'pj-XXXX' in reply
    assert not any(c[:2] == ['mail', 'send'] for c in fake.calls)


def test_kickoff_trailing_space_only_gives_usage(bridget, monkeypatch):
    fake = FakeMg()
    monkeypatch.setattr(bridget, 'run_mg', fake)

    reply = bridget.handle_command('kickoff   ')

    assert 'Usage' in reply
    assert not any(c[:2] == ['mail', 'send'] for c in fake.calls)


# -- COMMANDS + help-menu wiring --------------------------------------------

def test_commands_includes_kickoff(bridget):
    names = [c['name'] for c in bridget.COMMANDS]
    assert 'kickoff' in names


def test_help_menu_lists_kickoff_signature(bridget):
    reply = bridget.handle_command('help')
    assert '/kickoff pj-XXXX' in reply


def test_help_kickoff_drill_down(bridget):
    reply = bridget.handle_command('help kickoff')
    assert '`/kickoff pj-XXXX`' in reply
    # The longer description should mention the pj- restriction and the
    # ready-for-kickoff → in-progress flow per ds-1482.
    assert 'pj-' in reply
    assert 'ready-for-kickoff' in reply.lower()


if __name__ == '__main__':
    import sys
    sys.exit(pytest.main([__file__, '-v']))
