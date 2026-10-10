#!/usr/bin/env bash
# Turns a live Secret into an encrypted SealedSecret file that is safe to commit.
#   ./seal.sh <namespace> <secret-name> <output-file>
# The plaintext is read from the cluster and piped straight into kubeseal; it is never written to disk or printed.
# A sealed file only decrypts in the same namespace and under the same name (the default "strict" scope).
set -euo pipefail
NS=$1; NAME=$2; OUT=$3
command -v kubeseal >/dev/null || { echo "install kubeseal first: brew install kubeseal"; exit 1; }
kubectl -n "$NS" get secret "$NAME" -o json | python3 -c '
import json, sys
s = json.load(sys.stdin)
print(json.dumps({"apiVersion": "v1", "kind": "Secret",
                  "metadata": {"name": s["metadata"]["name"], "namespace": s["metadata"]["namespace"]},
                  "type": s.get("type", "Opaque"), "data": s["data"]}))' \
  | kubeseal --format yaml --controller-name sealed-secrets-controller --controller-namespace sealed-secrets > "$OUT"
echo "sealed $NS/$NAME -> $OUT"
