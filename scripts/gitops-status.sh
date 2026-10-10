#!/usr/bin/env bash
# Shows every Argo CD app and, for any that is not in sync, which objects differ from git.
# Drift in the demo app is expected after the agent fixes something: git still holds the old version.
set -uo pipefail
kubectl -n argocd get applications -o custom-columns=APP:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status
JSON="$(mktemp)"
trap 'rm -f "$JSON"' EXIT
kubectl -n argocd get applications -o json > "$JSON"
python3 - "$JSON" <<'PY'
import json
import sys

for app in json.load(open(sys.argv[1]))["items"]:
    drifted = [r for r in app["status"].get("resources", []) if r.get("status") == "OutOfSync"]
    if drifted:
        print(f"\n{app['metadata']['name']}: {len(drifted)} object(s) differ from git")
        for r in drifted:
            print(f"  {r['kind']:<14} {r.get('namespace', '')}/{r['name']}")
PY
