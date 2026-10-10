#!/usr/bin/env bash
# Seals the credential the cluster uses to pull the private agent image from ghcr.io.
# Create a classic GitHub token with ONLY the read:packages scope, save it (one line, nothing else) to the file below,
# then run this script and commit the output. The token itself is never printed or committed.
#   ./seal-ghcr-pull.sh [github-username]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TOKEN_FILE="${GHCR_TOKEN_FILE:-$HOME/.config/k8s-agent-lab/ghcr-read-token}"
USER_NAME="${1:-mrama94}"
OUT="$ROOT/apps/agent/ghcr-pull.sealedsecret.yaml"
[ -s "$TOKEN_FILE" ] || { echo "token file not found or empty: $TOKEN_FILE"; exit 1; }
command -v kubeseal >/dev/null || { echo "install kubeseal first: brew install kubeseal"; exit 1; }
python3 - "$TOKEN_FILE" "$USER_NAME" <<'PY' | kubeseal --format yaml --controller-name sealed-secrets-controller --controller-namespace sealed-secrets > "$OUT"
import base64, json, sys
token = open(sys.argv[1]).read().strip()
user = sys.argv[2]
auth = base64.b64encode(f"{user}:{token}".encode()).decode()
cfg = {"auths": {"ghcr.io": {"username": user, "password": token, "auth": auth}}}
print(json.dumps({"apiVersion": "v1", "kind": "Secret",
                  "metadata": {"name": "ghcr-pull", "namespace": "ai-agent"},
                  "type": "kubernetes.io/dockerconfigjson",
                  "data": {".dockerconfigjson": base64.b64encode(json.dumps(cfg).encode()).decode()}}))
PY
echo "sealed the image pull secret -> $OUT"
