#!/usr/bin/env bash
# Gives Argo CD read-only access to the private repo, using an SSH deploy key. The private key stays on this Mac
# (default path below) and in a cluster Secret; it is never committed.
set -euo pipefail
KEY="${ARGOCD_REPO_KEY:-$HOME/.config/k8s-agent-lab/argocd-deploy-key}"
REPO_URL="${ARGOCD_REPO_URL:-git@github.com:mrama94/k8s-ai-agent.git}"
[ -f "$KEY" ] || { echo "deploy key not found at $KEY (create it with ssh-keygen, see GITOPS-PLAN.md)"; exit 1; }
kubectl -n argocd create secret generic repo-k8s-ai-agent \
  --from-literal=type=git --from-literal=url="$REPO_URL" --from-file=sshPrivateKey="$KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n argocd label secret repo-k8s-ai-agent argocd.argoproj.io/secret-type=repository --overwrite
