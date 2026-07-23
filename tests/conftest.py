"""Shared test guards (mg-2d4d).

The suite once fired a REAL `pogo nudge --immediate mayor ...` at the
live mayor on every full run: test_restart_cause_logging's on_message
test fell through to the chat-relay path with run_pogo unpatched.
Individual tests patch bridget.run_pogo, but nothing stopped the next
test from missing the patch — so this conftest interposes a guard `pogo`
executable at the front of PATH that blocks the invocation, records it,
and fails the offending test with a pointer here.

The guard only trips when code actually resolves `pogo` via PATH at
subprocess time; tests that stub run_pogo (the normal idiom — see
test_nudge.py / test_chat_relay_buffer.py) never get here. No test in
the suite intentionally execs a real pogo binary (verified 2026-07-23).
If a future test legitimately needs its own pogo shim, prepend that
shim's dir to PATH inside the test — it will shadow this guard.
"""
import os
import stat

import pytest


@pytest.fixture(scope='session')
def _pogo_guard_bin(tmp_path_factory):
    """Session-scoped dir holding the guard `pogo` executable."""
    bin_dir = tmp_path_factory.mktemp('pogo-guard-bin')
    shim = bin_dir / 'pogo'
    shim.write_text(
        '#!/bin/sh\n'
        '# Test-suite guard (mg-2d4d): no test may exec the real pogo.\n'
        'echo "pogo $*" >> "${POGO_GUARD_LOG:-/dev/null}"\n'
        'echo "test-suite pogo guard: real pogo invocation blocked (mg-2d4d)" >&2\n'
        'exit 97\n'
    )
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


@pytest.fixture(autouse=True)
def block_real_pogo(_pogo_guard_bin, tmp_path, monkeypatch):
    """Fail any test whose code path execs `pogo` for real."""
    log = tmp_path / 'pogo-guard.log'
    monkeypatch.setenv('POGO_GUARD_LOG', str(log))
    monkeypatch.setenv(
        'PATH', f'{_pogo_guard_bin}{os.pathsep}{os.environ["PATH"]}'
    )
    yield
    if log.exists() and log.read_text().strip():
        pytest.fail(
            'test exec\'d the real pogo binary — patch bridget.run_pogo '
            '(see tests/conftest.py, mg-2d4d). Blocked invocation(s):\n'
            + log.read_text()
        )
