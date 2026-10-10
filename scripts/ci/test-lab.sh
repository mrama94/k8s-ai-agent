#!/usr/bin/env bash
# Checks the parts of ./lab that need neither Docker nor a cluster: usage, exit codes, the PATH link, and that it
# refuses to act on a cluster that does not exist. (up, down, stop and start are exercised by hand; they change real state.)
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
fail=0
check() { # description, then a command; the command's success is the test
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "ok   $desc"; else echo "FAIL $desc"; fail=1; fi
}
exit_code() { "$@" >/dev/null 2>&1; echo $?; }

help_text=$(./lab help)
for word in up down stop start status forward doctor link; do
  check "help mentions '$word'" grep -q "lab $word" <<<"$help_text"
done
check "help does not leak script source" bash -c '! ./lab help | grep -q "set -uo"'
check "unknown command exits 2" test "$(exit_code ./lab bogus)" = 2
check "forward with a bad argument exits 2" test "$(exit_code ./lab forward bogus)" = 2
check "down on a cluster that does not exist does nothing and succeeds" \
  bash -c 'LAB_CLUSTER=lab-test-no-such-cluster ./lab down </dev/null | grep -q "does not exist"'
check "status on a cluster that does not exist reports it and succeeds" \
  bash -c 'LAB_CLUSTER=lab-test-no-such-cluster ./lab status 2>/dev/null | grep -q "does not exist"'
check "stop on a cluster that is not running does nothing and succeeds" \
  bash -c 'LAB_CLUSTER=lab-test-no-such-cluster ./lab stop 2>/dev/null | grep -q "not running"'

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
check "link creates a symlink" bash -c "./lab link '$tmp/bin' && test -L '$tmp/bin/lab'"
check "the symlinked lab still finds the repo and runs" bash -c "cd / && '$tmp/bin/lab' help | grep -q 'lab up'"

exit "$fail"
