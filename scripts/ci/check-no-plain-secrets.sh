#!/usr/bin/env bash
# Fails if a committed (or about to be committed) file contains a plain Kubernetes Secret manifest, a private key, or a token that looks real.
# Only SealedSecret files (encrypted) may be committed. Run from anywhere; checks tracked and new, non-ignored files.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
SELF="scripts/ci/check-no-plain-secrets.sh"
FILES=$(git ls-files --cached --others --exclude-standard | grep -vE "^(${SELF}|agent/tests/)" || true)
fail=0
report() { echo "FOUND $1:"; echo "$2" | sed 's/^/    /'; fail=1; }

# 1. a plain Secret manifest (YAML "kind: Secret", not SealedSecret)
hits=$(echo "$FILES" | xargs grep -nE '^kind:[[:space:]]*"?Secret"?[[:space:]]*$' 2>/dev/null || true)
[ -n "$hits" ] && report "a plain Secret manifest (use a SealedSecret instead)" "$hits"

# 2. private keys
hits=$(echo "$FILES" | xargs grep -nE 'BEGIN ([A-Z]+ )?PRIVATE KEY' 2>/dev/null || true)
[ -n "$hits" ] && report "a private key" "$hits"

# 3. tokens that look real (GitHub, AWS)
hits=$(echo "$FILES" | xargs grep -nE '(ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|gho_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16})' 2>/dev/null || true)
[ -n "$hits" ] && report "something that looks like an access token" "$hits"

# 4. a data: or stringData: block of a Secret kept in a non-YAML file is out of scope; sealed files must be encrypted
for f in $(echo "$FILES" | grep -E 'sealedsecret\.ya?ml$' || true); do
  if ! grep -q 'encryptedData:' "$f"; then echo "FOUND a *.sealedsecret.yaml without encryptedData: $f"; fail=1; fi
done

[ "$fail" -eq 0 ] && echo "no plain secrets, keys or tokens in $(echo "$FILES" | wc -l | tr -d ' ') tracked files"
exit "$fail"
