"""Tests for the preapprove command + ~/.pogo/preapproval.json storage.

Loads the bridget script under a fake $HOME so import-time config loading
succeeds without a real Discord token or pogo install. Each test exercises
one branch of the preapprove decision tree, plus the load/save round-trip.
"""
import importlib.util
import json
import os
import re
import sys
import tempfile
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
    """Fresh bridget module with $HOME pointed at a clean tmpdir per test."""
    monkeypatch.setenv('HOME', str(tmp_path))
    return _load_bridget(tmp_path)


# -- load_preapproval / save_preapproval -------------------------------------

def test_load_missing_file_returns_defaults(bridget):
    state = bridget.load_preapproval()
    assert state['enabled'] is False
    assert state['fast'] is False
    assert state['updated_at'] == ''


def test_load_corrupt_file_returns_defaults(bridget, capsys):
    bridget.PREAPPROVAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    bridget.PREAPPROVAL_FILE.write_text('not json {{')
    state = bridget.load_preapproval()
    assert state == {'enabled': False, 'fast': False, 'updated_at': ''}
    err = capsys.readouterr().err
    assert 'load_preapproval parse error' in err


def test_save_then_load_round_trip(bridget):
    bridget.save_preapproval({'enabled': True, 'fast': True})
    state = bridget.load_preapproval()
    assert state['enabled'] is True
    assert state['fast'] is True
    assert state['updated_at']  # non-empty ISO-8601 string


def test_save_writes_iso8601_utc_with_z_suffix(bridget):
    bridget.save_preapproval({'enabled': True, 'fast': False})
    raw = json.loads(bridget.PREAPPROVAL_FILE.read_text())
    assert raw['enabled'] is True
    assert raw['fast'] is False
    # ISO-8601 UTC with Z suffix per the design contract.
    assert re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$', raw['updated_at'])


def test_save_is_atomic_via_tmp_then_rename(bridget):
    bridget.save_preapproval({'enabled': True, 'fast': False})
    # The .tmp file should not be left behind after a successful save.
    tmp = bridget.PREAPPROVAL_FILE.parent / (bridget.PREAPPROVAL_FILE.name + '.tmp')
    assert not tmp.exists()
    assert bridget.PREAPPROVAL_FILE.exists()


def test_save_coerces_truthy_values_to_bool(bridget):
    bridget.save_preapproval({'enabled': 1, 'fast': 'yes'})
    raw = json.loads(bridget.PREAPPROVAL_FILE.read_text())
    assert raw['enabled'] is True
    assert raw['fast'] is True


# -- handle_command: preapprove branch --------------------------------------

def test_preapprove_no_args_when_disabled_shows_disabled(bridget):
    reply = bridget.handle_command('preapprove')
    assert 'disabled' in reply.lower()


def test_preapprove_no_args_when_enabled_shows_status_with_fast_off(bridget):
    bridget.save_preapproval({'enabled': True, 'fast': False})
    reply = bridget.handle_command('preapprove')
    assert 'enabled' in reply.lower()
    assert 'fast: off' in reply.lower()


def test_preapprove_no_args_when_fast_enabled_shows_fast_on(bridget):
    bridget.save_preapproval({'enabled': True, 'fast': True})
    reply = bridget.handle_command('preapprove')
    assert 'enabled' in reply.lower()
    assert 'fast: on' in reply.lower()


def test_preapprove_true_enables_with_fast_off(bridget):
    reply = bridget.handle_command('preapprove true')
    assert reply.startswith('✓')
    state = bridget.load_preapproval()
    assert state['enabled'] is True
    assert state['fast'] is False


def test_preapprove_true_fast_enables_both(bridget):
    reply = bridget.handle_command('preapprove true fast')
    assert reply.startswith('✓')
    state = bridget.load_preapproval()
    assert state['enabled'] is True
    assert state['fast'] is True


def test_preapprove_false_disables_and_forces_fast_off(bridget):
    # Start with both enabled so we can see fast getting forced off.
    bridget.save_preapproval({'enabled': True, 'fast': True})
    reply = bridget.handle_command('preapprove false')
    assert reply.startswith('✓')
    state = bridget.load_preapproval()
    assert state['enabled'] is False
    assert state['fast'] is False


def test_preapprove_bare_fast_returns_usage(bridget):
    reply = bridget.handle_command('preapprove fast')
    assert 'Usage' in reply
    # State unchanged — no save happened.
    assert not bridget.PREAPPROVAL_FILE.exists()


def test_preapprove_invalid_junk_returns_usage(bridget):
    reply = bridget.handle_command('preapprove bogus')
    assert 'Usage' in reply
    assert not bridget.PREAPPROVAL_FILE.exists()


def test_preapprove_false_with_extra_arg_returns_usage(bridget):
    # Disallowed: `preapprove false fast` — fast cannot ride with false.
    reply = bridget.handle_command('preapprove false fast')
    assert 'Usage' in reply
    assert not bridget.PREAPPROVAL_FILE.exists()


def test_preapprove_true_with_unknown_modifier_returns_usage(bridget):
    reply = bridget.handle_command('preapprove true bogus')
    assert 'Usage' in reply
    assert not bridget.PREAPPROVAL_FILE.exists()


def test_preapprove_is_case_insensitive(bridget):
    bridget.handle_command('PreApprove True Fast')
    state = bridget.load_preapproval()
    assert state['enabled'] is True
    assert state['fast'] is True


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
