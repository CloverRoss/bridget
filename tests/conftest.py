"""Shared test guards (mg-2d4d, extended to mg in mg-bfd7).

The suite once fired a REAL `pogo nudge --immediate mayor ...` at the
live mayor on every full run: test_restart_cause_logging's on_message
test fell through to the chat-relay path with run_pogo unpatched.
Individual tests patch bridget.run_pogo, but nothing stopped the next
test from missing the patch — so this conftest interposes guard `pogo`
and `mg` executables at the front of PATH that block the invocation,
record it, and fail the offending test with a pointer here.

`mg` joined the guard in mg-bfd7: the inbox/scan paths call
_mg_item_closed → run_mg(['show', <id>]), and four tests were exec'ing
the real mg with fake ids (read-only, but the same leak class). bridget
resolves MG_BIN via shutil.which('mg') at import time, and every test
loads bridget after this fixture patches PATH, so the guard shim is what
MG_BIN points at unless the test stubs run_mg (the normal idiom — see
test_inbox_top_list_filter.py / test_pending_reports.py).

The guard only trips when code actually resolves `pogo`/`mg` via PATH at
subprocess time; tests that stub run_pogo/run_mg never get here. No test
in the suite intentionally execs a real pogo or mg binary (verified
2026-07-23). If a future test legitimately needs its own shim, prepend
that shim's dir to PATH inside the test — it will shadow this guard.
"""
import os
import stat

import pytest

_GUARDED_BINARIES = ('pogo', 'mg')


@pytest.fixture(scope='session')
def _exec_guard_bin(tmp_path_factory):
    """Session-scoped dir holding the guard `pogo` and `mg` executables."""
    bin_dir = tmp_path_factory.mktemp('exec-guard-bin')
    for name in _GUARDED_BINARIES:
        shim = bin_dir / name
        shim.write_text(
            '#!/bin/sh\n'
            f'# Test-suite guard (mg-2d4d/mg-bfd7): no test may exec the real {name}.\n'
            f'echo "{name} $*" >> "${{EXEC_GUARD_LOG:-/dev/null}}"\n'
            f'echo "test-suite exec guard: real {name} invocation blocked'
            ' (mg-2d4d/mg-bfd7)" >&2\n'
            'exit 97\n'
        )
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


@pytest.fixture(autouse=True)
def block_real_execs(_exec_guard_bin, tmp_path, monkeypatch):
    """Fail any test whose code path execs `pogo` or `mg` for real."""
    log = tmp_path / 'exec-guard.log'
    monkeypatch.setenv('EXEC_GUARD_LOG', str(log))
    monkeypatch.setenv(
        'PATH', f'{_exec_guard_bin}{os.pathsep}{os.environ["PATH"]}'
    )
    yield
    if log.exists() and log.read_text().strip():
        pytest.fail(
            'test exec\'d a real pogo/mg binary — patch bridget.run_pogo / '
            'bridget.run_mg (see tests/conftest.py, mg-2d4d/mg-bfd7). '
            'Blocked invocation(s):\n'
            + log.read_text()
        )
