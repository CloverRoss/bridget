#!/usr/bin/env bash
# Resolves the Python interpreter that the quality gates (build.sh, test.sh)
# run under. Sourced by those scripts — not executed directly. Sets GATE_PYTHON.
#
# The gates deliberately use the SAME interpreter bridget runs under in
# production (~/.pogo/venv-bridget, created by install.sh) rather than
# whatever `python3` happens to be first on PATH:
#
#   - Version parity. The live service is Python 3.9; `python3` on a dev Mac
#     is commonly 3.13/3.14, which accepts syntax 3.9 rejects outright. A
#     `match` statement py_compiles clean on 3.14 and is a SyntaxError on
#     3.9, so a gate pointed at the wrong interpreter green-lights a script
#     that cannot even parse in production.
#   - Dependencies. The test suite imports discord.py, which only exists
#     inside the venv.
#
# Set BRIDGET_GATE_PYTHON to override for a non-standard install.

_gate_die() {
    printf 'gate: %s\n' "$*" >&2
    exit 1
}

GATE_PYTHON="${BRIDGET_GATE_PYTHON:-$HOME/.pogo/venv-bridget/bin/python3}"

if [[ ! -x "$GATE_PYTHON" ]]; then
    _gate_die "no Python interpreter at $GATE_PYTHON.
  Run ./install.sh to create the bridget venv, or set BRIDGET_GATE_PYTHON to
  an interpreter with requirements.txt and requirements-dev.txt installed.
  The gates refuse to fall back to a bare python3: a gate that silently runs
  under the wrong interpreter is worse than no gate at all."
fi
