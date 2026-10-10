#!/usr/bin/env bash
# Runs every check that CI runs, on this machine. Needs: kubectl, helm, kubeconform, shellcheck, and a Python with the
# agent's requirements, requirements-dev and PyYAML installed. Point PYTHON at it (default: python3 on the PATH).
#   PYTHON=/path/to/venv/bin/python ./scripts/ci/run-all.sh
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
PYTHON="${PYTHON:-python3}"
BIN="$(dirname "$(command -v "$PYTHON")")"
export PATH="$BIN:$PATH"   # so yamllint and ruff from the same environment are found
fail=0
run() { echo; echo "######## $1"; shift; "$@" || { echo ">>> FAILED: $*"; fail=1; }; }

run "unit tests"           bash -c "cd agent && '$PYTHON' -m pytest -q"
run "lint"                 ./scripts/ci/lint.sh
run "no plain secrets"     ./scripts/ci/check-no-plain-secrets.sh
run "manifests and charts" env PYTHON="$PYTHON" ./scripts/ci/validate-manifests.sh

echo; [ "$fail" -eq 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit "$fail"
