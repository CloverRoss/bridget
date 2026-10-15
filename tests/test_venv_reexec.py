"""Deferred-import venv re-exec for the bridget CLI (mg-7f24).

`./bridget` runs under `#!/usr/bin/env python3`. From a plain shell that
resolves to whatever `python3` is first on PATH (Homebrew 3.14 on the dev
Mac), which has no discord.py, so every non-`chat` invocation died at the
deferred `import discord` with ModuleNotFoundError. The install.sh venv
(~/.pogo/venv-bridget) carries requirements.txt and is the intended
runtime, so on ImportError bridget hands off to it once.

These tests never start the real bot. A shim `discord` package on
PYTHONPATH raises ImportError, which exercises the fallback before any
Discord code runs. The fake venv interpreter is a shell script that prints
a marker and exits, so a broken re-exec cannot reach the gateway either.
"""
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

BRIDGET = Path(__file__).resolve().parent.parent / 'bridget'


def _write_exec(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def fake_home(tmp_path):
    home = tmp_path / 'home'
    (home / '.pogo').mkdir(parents=True)
    (home / '.pogo' / 'bridget.env').write_text(
        'DISCORD_BOT_TOKEN=test-token\n'
        'DISCORD_USER_ID=1\n'
        'DISCORD_SERVER_ID=2\n'
    )
    shim = tmp_path / 'shim'
    (shim / 'discord').mkdir(parents=True)
    (shim / 'discord' / '__init__.py').write_text(
        "raise ImportError(\"No module named 'discord' (test shim)\")\n"
    )
    fake_mg = tmp_path / 'bin' / 'mg'
    _write_exec(fake_mg, '#!/bin/sh\nexit 0\n')
    return {'home': home, 'shim': shim, 'mg': fake_mg}


def _run(fake_home, *args):
    env = dict(os.environ)
    env['HOME'] = str(fake_home['home'])
    env['PYTHONPATH'] = str(fake_home['shim'])
    env['MG_BIN'] = str(fake_home['mg'])
    env['POGO_BIN'] = str(fake_home['mg'])
    return subprocess.run(
        [sys.executable, str(BRIDGET), *args],
        env=env, capture_output=True, text=True, timeout=30,
    )


def test_import_error_reexecs_into_install_venv(fake_home):
    venv_py = fake_home['home'] / '.pogo' / 'venv-bridget' / 'bin' / 'python'
    _write_exec(venv_py, '#!/bin/sh\necho "VENV_REEXEC $*"\nexit 0\n')

    result = _run(fake_home, '--help')

    assert result.returncode == 0, result.stderr
    assert 'VENV_REEXEC' in result.stdout
    assert str(BRIDGET) in result.stdout
    assert '--help' in result.stdout


def test_import_error_without_venv_still_fails_loudly(fake_home):
    result = _run(fake_home, '--help')

    assert result.returncode != 0
    assert 'ImportError' in result.stderr
    assert 'VENV_REEXEC' not in result.stdout
