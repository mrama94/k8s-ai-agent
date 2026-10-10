#!/usr/bin/env bash
# LEGACY, replaced in Phase 2 by Sealed Secrets. Creates the two secrets the platform needs, once, only in the cluster.
set -euo pipefail
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
