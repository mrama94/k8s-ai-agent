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
check "help documents --keep-ollama" grep -q -- "--keep-ollama" <<<"$help_text"
check "help does not leak script source" bash -c '! ./lab help | grep -q "set -uo"'
check "unknown command exits 2" test "$(exit_code ./lab bogus)" = 2
check "forward with a bad argument exits 2" test "$(exit_code ./lab forward bogus)" = 2
if docker info >/dev/null 2>&1; then
check "down on a cluster that does not exist does nothing and succeeds" \
  bash -c 'LAB_CLUSTER=lab-test-no-such-cluster ./lab down </dev/null | grep -q "does not exist"'
check "status on a cluster that does not exist reports it and succeeds" \
  bash -c 'LAB_CLUSTER=lab-test-no-such-cluster ./lab status 2>/dev/null | grep -q "does not exist"'
check "stop on a cluster that is not running does nothing and succeeds" \
  bash -c 'LAB_CLUSTER=lab-test-no-such-cluster ./lab stop 2>/dev/null | grep -q "not running"'
else
  echo "skip the cluster-state checks (Docker is not running here)"
fi
check "with Docker down, down says so instead of claiming the cluster does not exist" \
  bash -c 'd="$(mktemp -d)"; printf "#!/bin/sh\nexit 1\n" > "$d/docker"; chmod +x "$d/docker"; out=$(PATH="$d:$PATH" ./lab down </dev/null 2>&1); echo "$out" | grep -q "Docker is not running" && ! echo "$out" | grep -q "does not exist"'

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# --- the confirmation prompt of `down`, with stand-ins for docker and kind (no real cluster is touched) ---
stubs="$tmp/stubs"; mkdir -p "$stubs"
cat > "$stubs/docker" <<'STUB'
#!/bin/sh
case "$1" in info) exit 0 ;; ps) echo agent-lab-control-plane ;; esac
STUB
cat > "$stubs/kind" <<'STUB'
#!/bin/sh
echo "$@" >> "${KIND_LOG:?}"
[ "$1 $2" = "get clusters" ] && echo agent-lab
exit 0
STUB
chmod +x "$stubs/docker" "$stubs/kind"
export KIND_LOG="$tmp/kind.log"; : > "$KIND_LOG"
prompt_default=$(PATH="$stubs:$PATH" ./lab down <<<"wrong answer" 2>&1)
prompt_keep=$(PATH="$stubs:$PATH" ./lab down --keep-ollama <<<"wrong answer" 2>&1)
check "down by default announces that Ollama is stopped too" grep -q "Ollama will be stopped too" <<<"$prompt_default"
check "down --keep-ollama announces that Ollama is left running" grep -q "Ollama will be left running" <<<"$prompt_keep"
check "down aborts on a wrong confirmation" grep -q "Not confirmed" <<<"$prompt_default"
check "a refused down never calls kind delete" bash -c "! grep -q delete '$KIND_LOG'"
check "link creates a symlink" bash -c "./lab link '$tmp/bin' && test -L '$tmp/bin/lab'"
check "the symlinked lab still finds the repo and runs" bash -c "cd / && '$tmp/bin/lab' help | grep -q 'lab up'"

exit "$fail"
