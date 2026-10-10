#!/usr/bin/env bash
# Backs up or restores the Sealed Secrets private key. WITHOUT this key, a rebuilt cluster cannot decrypt the sealed
# files in git. The backup lives outside the repo (default below) and must never be committed.
#   ./key.sh backup    after the controller has started (it creates the key on first start)
#   ./key.sh restore   before the controller starts on a new cluster (bootstrap.sh does this for you)
set -euo pipefail
BACKUP="${SEALED_SECRETS_KEY_BACKUP:-$HOME/.config/k8s-agent-lab/sealed-secrets-key.yaml}"
NS=sealed-secrets
case "${1:-}" in
  backup)
    umask 077; mkdir -p "$(dirname "$BACKUP")"
    kubectl -n "$NS" get secret -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml > "$BACKUP"
    chmod 600 "$BACKUP"
    echo "backed up $(grep -c 'kind: Secret' "$BACKUP") key(s) to $BACKUP"
    ;;
  restore)
    [ -f "$BACKUP" ] || { echo "no key backup at $BACKUP; the controller will create a new key and the sealed files in git will NOT decrypt"; exit 1; }
    kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
    kubectl apply -f "$BACKUP"
    echo "restored the sealing key from $BACKUP"
    ;;
  *) echo "usage: $0 backup|restore"; exit 1 ;;
esac
