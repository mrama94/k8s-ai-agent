#!/usr/bin/env bash
# Rebuilds the whole lab from this repository: cluster, Argo CD, and everything Argo CD then pulls from git.
#
# Phase 2 version: the platform and its secrets come from git through Argo CD (secrets as encrypted SealedSecrets).
# Still manual (replaced later): the locally built agent image (Phase 3, registry image).
#
# Needs, outside git:
#   ~/.config/k8s-agent-lab/argocd-deploy-key          read-only deploy key so Argo CD can read the private repo
#   ~/.config/k8s-agent-lab/sealed-secrets-key.yaml    backup of the Sealed Secrets key (bootstrap/sealed-secrets/key.sh backup)
# and the files pushed to GitHub main, because Argo CD reads them from there.
# Usage: ./bootstrap/bootstrap.sh            (safe to re-run; every step is idempotent)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLUSTER=agent-lab
METRICS_SERVER_VERSION=v0.9.0
ARGOCD_CHART_VERSION=10.10.2

for tool in docker kind kubectl helm openssl; do
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
"$ROOT/bootstrap/sealed-secrets/key.sh" restore || echo "WARNING: continuing without the key. Run bootstrap/sealed-secrets/regenerate.sh, commit the new sealed files, then re-run."

echo "==> 4/7 Argo CD"
helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install argocd argo/argo-cd --version "$ARGOCD_CHART_VERSION" \
  --namespace argocd --create-namespace -f "$ROOT/bootstrap/argocd/values.yaml" --wait --timeout 6m

echo "==> 5/7 repository access, project and root app"
"$ROOT/bootstrap/argocd/register-repo.sh"
kubectl apply -f "$ROOT/bootstrap/argocd/project.yaml"
kubectl apply -f "$ROOT/bootstrap/root-app.yaml"

echo "==> 6/7 waiting for the platform (Argo CD pulls it from git)"
for app in sealed-secrets secrets loki alloy grafana-dashboards grafana prometheus; do
  until kubectl -n argocd get application "$app" >/dev/null 2>&1; do sleep 3; done
  kubectl -n argocd wait --for=jsonpath='{.status.health.status}'=Healthy "application/$app" --timeout=600s
done

echo "==> 7/7 agent image (built locally and loaded into kind, replaced by a registry image in Phase 3) and the agent"
docker build -t k8s-agent:local "$ROOT/agent"
kind load docker-image k8s-agent:local --name "$CLUSTER"
kubectl apply -k "$ROOT/demo/base"
kubectl apply -k "$ROOT/apps/agent"
kubectl -n ai-agent rollout status deploy/k8s-agent --timeout=180s

echo "Done. Next: ./scripts/portforward.sh   and start Ollama:  OLLAMA_HOST=0.0.0.0:11434 ollama serve"
echo "Argo CD UI: http://localhost:8080  (user admin; password:"
echo "  kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
