#!/usr/bin/env bash
# Renders every Kustomize app and Helm chart, then checks all of it against the Kubernetes (and CRD) schemas with
# kubeconform. A manifest with a wrong field name, a wrong type, or an unknown kind fails here, before Argo CD sees it.
# Needs: kubectl (for kustomize), helm, kubeconform, python3 with PyYAML. Network access for schemas and chart repos.
#   PYTHON=/path/to/python ./validate-manifests.sh      (to pick the interpreter that has PyYAML)
# Exit status: 0 only if every group rendered and validated.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
PYTHON="${PYTHON:-python3}"
K8S_VERSION="${K8S_VERSION:-1.33.0}"
CRDS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fail=0

validate() { # label, file with manifests
  local out
  if out=$(kubeconform -strict -summary -kubernetes-version "$K8S_VERSION" -schema-location default \
           -schema-location "$CRDS" -ignore-missing-schemas "$2" 2>&1); then
    echo "ok   [$1] $(echo "$out" | tail -1)"
  else
    echo "FAIL [$1]"; echo "$out" | sed 's/^/       /'; fail=1
  fi
}

join_yaml() { # prints each file followed by a document separator
  for f in "$@"; do cat "$f"; printf '\n---\n'; done
}

for dir in apps/agent demo/base platform/secrets platform/grafana/dashboards; do
  if kubectl kustomize "$dir" > "$WORK/out.yaml" 2> "$WORK/err.txt"; then
    validate "kustomize $dir" "$WORK/out.yaml"
  else
    echo "FAIL [kustomize $dir] did not render"; sed 's/^/       /' "$WORK/err.txt"; fail=1
  fi
done

# Plain YAML: Argo CD projects and Applications, and the demo scenarios (deliberately broken at runtime, valid as manifests).
join_yaml bootstrap/argocd/project.yaml bootstrap/argocd/agent-project.yaml bootstrap/root-app.yaml platform/apps/*.yaml > "$WORK/argo.yaml"
validate "argo projects and applications" "$WORK/argo.yaml"
join_yaml demo/scenarios/*.yaml > "$WORK/scenarios.yaml"
validate "demo scenarios" "$WORK/scenarios.yaml"

if "$PYTHON" scripts/ci/render-charts.py > "$WORK/charts.yaml" 2> "$WORK/charts.err"; then
  validate "helm charts as Argo CD renders them" "$WORK/charts.yaml"
else
  echo "FAIL [helm charts] did not render"; sed 's/^/       /' "$WORK/charts.err"; fail=1
fi
exit "$fail"
