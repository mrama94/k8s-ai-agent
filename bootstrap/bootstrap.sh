#!/usr/bin/env bash
# Rebuilds the whole lab from this repository: cluster, metrics-server, monitoring platform, agent.
#
# Phase 0 version: it still uses the manual steps (Helm CLI, a locally built image loaded with `kind load`).
# Phase 1 replaces the metrics-server, platform and agent steps with "install Argo CD, apply the root app",
# and Phase 3 replaces the local image with one pulled from the registry.
#
# Usage: ./bootstrap/bootstrap.sh            (safe to re-run; every step is idempotent)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLUSTER=agent-lab
METRICS_SERVER_VERSION=v0.9.0

for tool in docker kind kubectl helm openssl; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool"; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "Docker is not running"; exit 1; }

echo "==> 1/6 kind cluster"
if kind get clusters | grep -qx "$CLUSTER"; then
  echo "cluster $CLUSTER already exists"
else
  kind create cluster --name "$CLUSTER" --config "$ROOT/bootstrap/kind-config.yaml"
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null
kubectl wait --for=condition=Ready nodes --all --timeout=180s

echo "==> 2/6 metrics-server (lab setting: --kubelet-insecure-tls, because kind kubelets use self-signed certificates)"
kubectl apply -f "https://github.com/kubernetes-sigs/metrics-server/releases/download/$METRICS_SERVER_VERSION/components.yaml"
if ! kubectl -n kube-system get deploy metrics-server -o jsonpath='{.spec.template.spec.containers[0].args}' | grep -q kubelet-insecure-tls; then
  kubectl -n kube-system patch deployment metrics-server --type=json \
    -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
fi

echo "==> 3/6 monitoring platform (legacy Helm install, replaced by Argo CD in Phase 1)"
"$ROOT/bootstrap/install-platform.sh"

echo "==> 4/6 agent image (built locally and loaded into kind, replaced by a registry image in Phase 3)"
docker build -t k8s-agent:local "$ROOT/agent"
kind load docker-image k8s-agent:local --name "$CLUSTER"

echo "==> 5/6 namespaces and the agent"
kubectl apply -k "$ROOT/demo/base"
kubectl apply -k "$ROOT/apps/agent"
kubectl -n ai-agent rollout status deploy/k8s-agent --timeout=180s

echo "==> 6/6 done"
echo "Next: ./scripts/portforward.sh   and start Ollama:  OLLAMA_HOST=0.0.0.0:11434 ollama serve"
