#!/usr/bin/env bash
# Creates NEW random secrets and seals them into the files that git holds. Use it when the sealing key is lost
# (a rebuilt cluster cannot decrypt the old files) or for a first-time setup. Then commit the three files and
# re-run bootstrap. The old values are gone: Grafana gets a new admin password and the webhook token changes.
# Needs the controller running (kubeseal fetches its public key from it). Override the output folder for a dry run:
#   OUT_ROOT=/tmp/x ./regenerate.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT_ROOT="${OUT_ROOT:-$ROOT}"
command -v kubeseal >/dev/null || { echo "install kubeseal first: brew install kubeseal"; exit 1; }
mkdir -p "$OUT_ROOT/platform/secrets" "$OUT_ROOT/apps/agent"
seal() { # namespace name outfile ; reads a Secret manifest (JSON) on stdin
  kubeseal --format yaml --controller-name sealed-secrets-controller --controller-namespace sealed-secrets > "$3"
  echo "sealed $1/$2 -> $3"
}
python3 - "$OUT_ROOT" <<'PY' | {
import base64, json, secrets, sys
def b(v): return base64.b64encode(v.encode()).decode()
token, password = secrets.token_hex(24), secrets.token_hex(12)
def secret(ns, name, data):
    return {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": ns},
            "type": "Opaque", "data": {k: b(v) for k, v in data.items()}}
for s in (secret("monitoring", "grafana-admin", {"admin-user": "admin", "admin-password": password}),
          secret("monitoring", "alert-webhook-token", {"token": token}),
          secret("ai-agent", "alert-webhook-token", {"token": token})):
    print(json.dumps(s))
PY
  read -r GRAFANA; read -r WEBHOOK_MON; read -r WEBHOOK_AGENT
  echo "$GRAFANA"       | seal monitoring grafana-admin       "$OUT_ROOT/platform/secrets/grafana-admin.sealedsecret.yaml"
  echo "$WEBHOOK_MON"   | seal monitoring alert-webhook-token "$OUT_ROOT/platform/secrets/alert-webhook-token.sealedsecret.yaml"
  echo "$WEBHOOK_AGENT" | seal ai-agent   alert-webhook-token "$OUT_ROOT/apps/agent/alert-webhook-token.sealedsecret.yaml"
}
