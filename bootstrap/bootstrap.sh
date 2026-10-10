#!/usr/bin/env bash
# Rebuilds the whole lab from this repository: cluster, Argo CD, and everything Argo CD then pulls from git.
#
# Phase 3 version: platform, secrets and the agent all come from git through Argo CD. The agent image is pulled from
# ghcr.io (built by GitHub Actions); nothing is built or loaded locally. The demo namespace is an Argo CD app with manual
# sync (so approved fixes are never undone); this script gives it its first sync.
#
# Needs, outside git:
#   ~/.config/k8s-agent-lab/argocd-deploy-key          read-only deploy key so Argo CD can read the private repo
#   ~/.config/k8s-agent-lab/sealed-secrets-key.yaml    backup of the Sealed Secrets key (bootstrap/sealed-secrets/key.sh backup)
# and the files pushed to GitHub main, because Argo CD reads them from there.
# The image pull credential (ghcr-pull) is a sealed file in git, so it needs no extra step. If the sealing key is lost,
# see bootstrap/sealed-secrets/regenerate.sh and seal-ghcr-pull.sh.
# Usage: ./bootstrap/bootstrap.sh            (safe to re-run; every step is idempotent)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLUSTER=agent-lab
METRICS_SERVER_VERSION=v0.9.0
ARGOCD_CHART_VERSION=10.10.2

for tool in docker kind kubectl helm; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool"; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "Docker is not running"; exit 1; }

echo "==> 1/7 kind cluster"
if kind get clusters | grep -qx "$CLUSTER"; then
  echo "cluster $CLUSTER already exists"
else
  kind create cluster --name "$CLUSTER" --config "$ROOT/bootstrap/kind-config.yaml"
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null
kubectl wait --for=condition=Ready nodes --all --timeout=180s

echo "==> 2/7 metrics-server (lab setting: --kubelet-insecure-tls, because kind kubelets use self-signed certificates)"
kubectl apply -f "https://github.com/kubernetes-sigs/metrics-server/releases/download/$METRICS_SERVER_VERSION/components.yaml"
if ! kubectl -n kube-system get deploy metrics-server -o jsonpath='{.spec.template.spec.containers[0].args}' | grep -q kubelet-insecure-tls; then
  kubectl -n kube-system patch deployment metrics-server --type=json \
    -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
fi

echo "==> 3/7 Sealed Secrets key (must exist before the controller starts, or it makes a new key and the sealed files will not decrypt)"
"$ROOT/bootstrap/sealed-secrets/key.sh" restore || echo "WARNING: continuing without the key. Run bootstrap/sealed-secrets/regenerate.sh and seal-ghcr-pull.sh, commit the new sealed files, then re-run."

echo "==> 4/7 Argo CD"
helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install argocd argo/argo-cd --version "$ARGOCD_CHART_VERSION" \
  --namespace argocd --create-namespace -f "$ROOT/bootstrap/argocd/values.yaml" --wait --timeout 6m

echo "==> 5/7 repository access, projects, the demo namespace, and the root app"
"$ROOT/bootstrap/argocd/register-repo.sh"
kubectl apply -f "$ROOT/bootstrap/argocd/project.yaml" -f "$ROOT/bootstrap/argocd/agent-project.yaml" -f "$ROOT/bootstrap/argocd/demo-project.yaml"
kubectl apply -k "$ROOT/demo/base"          # the agent's Roles live in this namespace, so it must exist first
kubectl apply -f "$ROOT/bootstrap/root-app.yaml"

echo "==> 6/7 waiting for everything Argo CD pulls from git (the agent image downloads here, so allow a few minutes)"
for app in sealed-secrets secrets loki alloy grafana-dashboards grafana prometheus agent; do
  until kubectl -n argocd get application "$app" >/dev/null 2>&1; do sleep 3; done
  kubectl -n argocd wait --for=jsonpath='{.status.health.status}'=Healthy "application/$app" --timeout=900s
done

echo "==> 7/7 first sync of the demo app (it has no automatic sync, on purpose)"
until kubectl -n argocd get application demo >/dev/null 2>&1; do sleep 3; done
kubectl -n argocd patch application demo --type merge -p '{"operation":{"sync":{}}}'
kubectl -n argocd wait --for=jsonpath='{.status.sync.status}'=Synced application/demo --timeout=300s
kubectl -n argocd wait --for=jsonpath='{.status.health.status}'=Healthy application/demo --timeout=300s

echo "Done: cluster, platform, agent and demo are all up and in sync with git."
echo "Next: ./scripts/portforward.sh   and start Ollama:  OLLAMA_HOST=0.0.0.0:11434 ollama serve"
echo "Argo CD UI: http://localhost:8080  (user admin; password:"
echo "  kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
