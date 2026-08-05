#!/usr/bin/env bash
# bridget has no compile step. Validate that every Python file in the repo
# compiles under the interpreter production actually runs (see gate-python.sh
# for why that is not bare `python3`).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./gate-python.sh

echo "build.sh: using $GATE_PYTHON ($("$GATE_PYTHON" --version 2>&1))"
"$GATE_PYTHON" -m py_compile bridget
"$GATE_PYTHON" -m compileall -q tests
echo "build.sh: syntax ok"
