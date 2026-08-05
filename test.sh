#!/usr/bin/env bash
# Runs the pytest suite in tests/. This is the merge gate: the refinery runs
# it on every MR (see .pogo/refinery.toml), so a change that breaks any test
# cannot merge.
#
# It used to be a py_compile smoke check, which proved only that `bridget`
# parsed. The suite had grown to 606 tests underneath it and none of them
# gated anything — a change could break every one and still merge green
# (mg-825f). Syntax checking now lives in build.sh where it belongs.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./gate-python.sh

if ! "$GATE_PYTHON" -c 'import pytest' >/dev/null 2>&1; then
    _gate_die "pytest is not installed in $GATE_PYTHON.
  Run ./install.sh (it installs requirements-dev.txt), or:
      $GATE_PYTHON -m pip install -r requirements-dev.txt
  Refusing to pass without running the suite."
fi

echo "test.sh: using $GATE_PYTHON ($("$GATE_PYTHON" --version 2>&1))"

# -p no:cacheprovider keeps pytest from writing .pytest_cache into the
# refinery's worktree, which it merges from.
exec "$GATE_PYTHON" -m pytest tests/ -q --strict-markers -p no:cacheprovider "$@"
