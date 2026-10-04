#!/usr/bin/env bash
# Keeps the agent and Grafana port-forwards alive: kubectl port-forward exits on any dropped connection,
# so each one runs in a restart loop. Stop everything with Ctrl+C.
trap 'kill 0' INT TERM
forward() { while true; do kubectl -n "$1" port-forward "$2" "$3" >/dev/null 2>&1; sleep 1; done; }
forward ai-agent svc/k8s-agent 8000:8000 &
forward monitoring svc/grafana 3000:80 &
echo "agent http://localhost:8000 | grafana http://localhost:3000 (Ctrl+C to stop)"
wait
