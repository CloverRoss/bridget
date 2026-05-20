"""Tests for the `spend` command (mg-cac7 / mg-4b6e).

`spend` probes the Anthropic API to read rate-limit headers and reports
% used + refresh time for the input-tokens and output-tokens windows.

The 5-hour session and weekly windows in the original mg-cac7 mockup are
Claude Code subscription concepts that the API does not surface; per the
design's risk section, we ship what the API exposes and document the
limitation in CHANGELOG.
"""
import datetime
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
    # spend is Robin-only on laptop (mg-5059); these tests cover the
    # underlying handler, so opt into the robin profile.
    monkeypatch.setenv('BRIDGET_PROFILE', 'robin')
    return _load_bridget(tmp_path)


# -- time formatting -------------------------------------------------------

def test_format_duration_seconds(bridget):
    assert bridget._format_quota_duration(0) == '0s'
    assert bridget._format_quota_duration(45) == '45s'


def test_format_duration_minutes(bridget):
    assert bridget._format_quota_duration(60) == '1m'
    assert bridget._format_quota_duration(125) == '2m 5s'


def test_format_duration_hours(bridget):
    assert bridget._format_quota_duration(5400) == '1h 30m'
    assert bridget._format_quota_duration(3600) == '1h'


def test_format_duration_days(bridget):
    assert bridget._format_quota_duration(367200) == '4d 6h'
    assert bridget._format_quota_duration(86400) == '1d'


def test_format_duration_negative_is_zero(bridget):
    # If the reset timestamp is in the past (e.g., the window already
    # rolled), report 0s rather than a negative duration.
    assert bridget._format_quota_duration(-5) == '0s'


# -- header parsing --------------------------------------------------------

def test_parse_quota_headers_full(bridget):
    headers = {
        'anthropic-ratelimit-input-tokens-limit': '50000',
        'anthropic-ratelimit-input-tokens-remaining': '44000',
        'anthropic-ratelimit-input-tokens-reset': '2026-05-13T10:00:00Z',
        'anthropic-ratelimit-output-tokens-limit': '10000',
        'anthropic-ratelimit-output-tokens-remaining': '9500',
        'anthropic-ratelimit-output-tokens-reset': '2026-05-13T10:00:00Z',
    }
    parsed = bridget._parse_quota_headers(headers)
    assert parsed['input-tokens']['limit'] == 50000
    assert parsed['input-tokens']['remaining'] == 44000
    assert parsed['input-tokens']['reset'] == '2026-05-13T10:00:00Z'
    assert parsed['output-tokens']['limit'] == 10000


def test_parse_quota_headers_case_insensitive(bridget):
    # urllib normalizes header case differently than test dicts; ensure
    # mixed-case keys still parse.
    headers = {
        'Anthropic-Ratelimit-Input-Tokens-Limit': '100',
        'Anthropic-Ratelimit-Input-Tokens-Remaining': '50',
        'Anthropic-Ratelimit-Input-Tokens-Reset': '2026-05-13T10:00:00Z',
    }
    parsed = bridget._parse_quota_headers(headers)
    assert parsed['input-tokens']['limit'] == 100


def test_parse_quota_headers_missing_window(bridget):
    # If only input-tokens are surfaced, output-tokens is silently absent.
    headers = {
        'anthropic-ratelimit-input-tokens-limit': '100',
        'anthropic-ratelimit-input-tokens-remaining': '50',
    }
    parsed = bridget._parse_quota_headers(headers)
    assert 'input-tokens' in parsed
    assert 'output-tokens' not in parsed


def test_parse_quota_headers_non_numeric(bridget):
    # Defensive: a malformed header shouldn't crash; just skip the window.
    headers = {
        'anthropic-ratelimit-input-tokens-limit': 'oops',
        'anthropic-ratelimit-input-tokens-remaining': '50',
    }
    parsed = bridget._parse_quota_headers(headers)
    assert parsed == {}


# -- format quota ----------------------------------------------------------

def test_format_quota_with_both_windows(bridget):
    now = 1700000000.0
    reset_dt = datetime.datetime.fromtimestamp(now + 5400, tz=datetime.timezone.utc)
    reset_iso = reset_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    quota = {
        'input-tokens': {'limit': 100, 'remaining': 88, 'reset': reset_iso},
        'output-tokens': {'limit': 200, 'remaining': 106, 'reset': reset_iso},
    }
    out = bridget._format_quota(quota, now=now)
    assert out.startswith('Token quota:')
    assert 'Input tokens:' in out
    assert 'Output tokens:' in out
    assert '12% used' in out  # 12/100 input
    assert '47% used' in out  # 94/200 output
    assert 'refresh in 1h 30m' in out


def test_format_quota_at_zero_use(bridget):
    now = 1700000000.0
    reset_iso = '2026-05-13T10:00:00Z'
    quota = {
        'input-tokens': {'limit': 100, 'remaining': 100, 'reset': reset_iso},
    }
    out = bridget._format_quota(quota, now=now)
    assert '0% used' in out


def test_format_quota_empty(bridget):
    # If the API surfaces no rate-limit headers, the formatter says so
    # rather than producing a misleading 100%-used line.
    assert 'no rate-limit headers' in bridget._format_quota({})


# -- handle_command --------------------------------------------------------

def test_spend_no_api_key(bridget, monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    reply = bridget.handle_command('spend')
    assert 'not configured' in reply
    assert 'ANTHROPIC_API_KEY' in reply
    assert 'bridget.env' in reply


def test_spend_probe_error(bridget, monkeypatch):
    monkeypatch.setattr(
        bridget, '_probe_anthropic_quota',
        lambda: {'error': 'connection refused'},
    )
    reply = bridget.handle_command('spend')
    assert 'Quota probe failed' in reply
    assert 'connection refused' in reply
    assert 'Claude Code' in reply


def test_spend_happy_path(bridget, monkeypatch):
    # Future timestamp so refresh-in is positive.
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=5400)
    reset_iso = future.strftime('%Y-%m-%dT%H:%M:%SZ')
    monkeypatch.setattr(
        bridget, '_probe_anthropic_quota',
        lambda: {
            'input-tokens': {'limit': 100, 'remaining': 88, 'reset': reset_iso},
            'output-tokens': {'limit': 200, 'remaining': 106, 'reset': reset_iso},
        },
    )
    reply = bridget.handle_command('spend')
    assert reply.startswith('Token quota:')
    assert '12% used' in reply
    assert '47% used' in reply
    assert 'refresh in' in reply


def test_spend_ignores_extra_args(bridget, monkeypatch):
    # Per design: `spend <anything>` ignores args (reserved for future).
    calls = []
    monkeypatch.setattr(
        bridget, '_probe_anthropic_quota',
        lambda: (calls.append(1), {'input-tokens': {'limit': 1, 'remaining': 1, 'reset': ''}})[1],
    )
    bridget.handle_command('spend extra junk')
    assert len(calls) == 1


# -- probe contract --------------------------------------------------------

def test_probe_returns_none_without_api_key(bridget, monkeypatch):
    # The probe must return None (not {'error': ...}) when no key is set
    # so the dispatch can distinguish "configure me" from "API broke".
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    assert bridget._probe_anthropic_quota() is None


# -- help integration ------------------------------------------------------

def test_help_menu_lists_spend(bridget):
    reply = bridget.handle_command('help')
    # Slash-prefixed signature (mg-a0f3).
    assert '`/spend`' in reply


def test_help_spend_describes_command(bridget):
    reply = bridget.handle_command('help spend').lower()
    assert 'anthropic' in reply
    assert 'token' in reply
    # Mentions the historical-spend alternative so user knows the scope.
    assert '/cost' in reply or 'mg spend' in reply


def test_command_list_includes_spend(bridget):
    assert '`/spend`' in bridget.COMMAND_LIST


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
