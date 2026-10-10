#!/usr/bin/env bash
# Style and obvious-mistake checks: YAML (yamllint), shell scripts (shellcheck, warnings and above) and Python (ruff:
# real errors only, such as undefined names and unused imports). Needs yamllint, shellcheck and ruff on the PATH.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
fail=0
step() { echo "== $1"; }

step "yamllint"
yamllint -c .yamllint . || fail=1

step "shellcheck"
# shellcheck disable=SC2046
shellcheck -S warning $(git ls-files --cached --others --exclude-standard '*.sh') || fail=1

step "ruff (errors only: E9 syntax, F pyflakes)"
ruff check --select E9,F agent || fail=1

[ "$fail" -eq 0 ] && echo "lint clean"
exit "$fail"
