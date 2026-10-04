#!/usr/bin/env bash
# Installs Prometheus, Alertmanager and kube-state-metrics into the "monitoring" namespace.
# The shared webhook token is generated once and stored only in cluster Secrets (never in git).
set -euo pipefail
cd "$(dirname "$0")"
CHART_VERSION=29.35.0

kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace ai-agent --dry-run=client -o yaml | kubectl apply -f -
if ! kubectl -n monitoring get secret alert-webhook-token >/dev/null 2>&1; then
  TOKEN=$(openssl rand -hex 24)
  kubectl -n monitoring create secret generic alert-webhook-token --from-literal=token="$TOKEN"
  kubectl -n ai-agent create secret generic alert-webhook-token --from-literal=token="$TOKEN" \
    --dry-run=client -o yaml | kubectl apply -f -
fi

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm upgrade --install prometheus prometheus-community/prometheus \
  --namespace monitoring --version "$CHART_VERSION" -f values.yaml --wait --timeout 5m
