#!/usr/bin/env bash
# LEGACY, replaced in Phase 1 by Argo CD Applications under platform/.
# Installs Prometheus, Alertmanager, kube-state-metrics, Loki, Alloy and Grafana into "monitoring" with Helm.
# The shared webhook token and the Grafana password are generated once and stored only in cluster Secrets (never in git).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLATFORM="$ROOT/platform"

PROMETHEUS_CHART_VERSION=29.35.0
LOKI_CHART_VERSION=7.3.0
ALLOY_CHART_VERSION=1.13.0
GRAFANA_CHART_VERSION=10.5.15

kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace ai-agent --dry-run=client -o yaml | kubectl apply -f -
if ! kubectl -n monitoring get secret alert-webhook-token >/dev/null 2>&1; then
  TOKEN=$(openssl rand -hex 24)
  kubectl -n monitoring create secret generic alert-webhook-token --from-literal=token="$TOKEN"
  kubectl -n ai-agent create secret generic alert-webhook-token --from-literal=token="$TOKEN" \
    --dry-run=client -o yaml | kubectl apply -f -
fi
if ! kubectl -n monitoring get secret grafana-admin >/dev/null 2>&1; then
  kubectl -n monitoring create secret generic grafana-admin \
    --from-literal=admin-user=admin --from-literal=admin-password="$(openssl rand -hex 12)"
fi

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo add grafana https://grafana.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null

helm upgrade --install prometheus prometheus-community/prometheus \
  --namespace monitoring --version "$PROMETHEUS_CHART_VERSION" -f "$PLATFORM/prometheus/values.yaml" --wait --timeout 5m
helm upgrade --install loki grafana/loki \
  --namespace monitoring --version "$LOKI_CHART_VERSION" -f "$PLATFORM/loki/values.yaml" --wait --timeout 5m
helm upgrade --install alloy grafana/alloy \
  --namespace monitoring --version "$ALLOY_CHART_VERSION" -f "$PLATFORM/alloy/values.yaml" --wait --timeout 5m

kubectl -n monitoring create configmap grafana-dashboards \
  --from-file="$PLATFORM/grafana/dashboards/demo-workloads.json" --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install grafana grafana/grafana \
  --namespace monitoring --version "$GRAFANA_CHART_VERSION" -f "$PLATFORM/grafana/values.yaml" --wait --timeout 5m

echo "Grafana: kubectl -n monitoring port-forward svc/grafana 3000:80  (user admin; password:"
echo "  kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d)"
