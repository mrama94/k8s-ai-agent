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

# --- Grafana (option B) ---
GRAFANA_CHART_VERSION=10.5.15
if ! kubectl -n monitoring get secret grafana-admin >/dev/null 2>&1; then
  kubectl -n monitoring create secret generic grafana-admin \
    --from-literal=admin-user=admin --from-literal=admin-password="$(openssl rand -hex 12)"
fi
kubectl -n monitoring create configmap grafana-dashboards --from-file=demo-workloads.json \
  --dry-run=client -o yaml | kubectl apply -f -
helm repo add grafana https://grafana.github.io/helm-charts >/dev/null 2>&1 || true
helm upgrade --install grafana grafana/grafana \
  --namespace monitoring --version "$GRAFANA_CHART_VERSION" -f grafana-values.yaml --wait --timeout 5m
echo "Grafana: kubectl -n monitoring port-forward svc/grafana 3000:80  (user admin; password:"
echo "  kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d)"
