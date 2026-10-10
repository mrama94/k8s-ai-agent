# Running the local Kubernetes agent: start, expose, stop

Everything runs on this Mac: a kind cluster (`agent-lab`), Ollama on the host, and port-forwards to reach the
services. Nothing is exposed beyond `localhost` except Ollama, which listens on all interfaces (see Safety).

## What is exposed

| Service | URL | How it is exposed | Needed for |
|---|---|---|---|
| Kubernetes agent UI | http://localhost:8000 | `kubectl port-forward` | Chat, alerts panel, approvals |
| Grafana | http://localhost:3000 | `kubectl port-forward` | Dashboards, alert history |
| Argo CD | http://localhost:8080 | `kubectl port-forward` | See what git deployed, sync state, drift |
| Ollama | http://localhost:11434 | Host process, port 11434 | The model; the agent pod reaches it at `host.docker.internal:11434` |
| Prometheus (optional) | http://localhost:9090 | `kubectl port-forward` | Debugging queries and rules only |
| Alertmanager (optional) | http://localhost:9093 | `kubectl port-forward` | Debugging alert routing only |

Prometheus and Alertmanager talk to the agent inside the cluster, so the last two are not needed for normal use.

## 1. Start the cluster

Start Docker Desktop first (memory limit about 5 GB), then:

```bash
docker start agent-lab-control-plane agent-lab-worker
kubectl get nodes        # wait until both nodes show Ready
kubectl get pods -A      # pods restart on their own; wait for Running
```

If the nodes never become Ready, recreate the cluster instead (see "Rebuild from scratch").

## 2. Start Ollama (exactly one server)

Do not open the Ollama menu-bar app. It starts a second server, and two servers made model requests hang.

```bash
pgrep -fl -i ollama                          # should print nothing before you start
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

Keep this terminal open. The first model call after a start is slower because Ollama loads the model.

## 3. Expose the agent and Grafana

Run each command in its own terminal tab:

```bash
kubectl -n ai-agent port-forward svc/k8s-agent 8000:8000
```

```bash
kubectl -n monitoring port-forward svc/grafana 3000:80
```

Optional, for debugging:

```bash
kubectl -n monitoring port-forward svc/prometheus-server 9090:80
kubectl -n monitoring port-forward svc/prometheus-alertmanager 9093:9093
```

Or start the two main ones in the background from one terminal:

```bash
for s in "ai-agent svc/k8s-agent 8000:8000" "monitoring svc/grafana 3000:80"; do
  set -- $s; nohup kubectl -n $1 port-forward $2 $3 >/tmp/pf-$3.log 2>&1 &
done
```

Port-forwards stop whenever the cluster, the agent pod or your Mac restarts. Run them again after any restart.

Or use the scripts: `./scripts/portforward.sh` starts the agent and Grafana forwards and restarts them if they drop;
`./scripts/stop-portforward.sh` stops them all.

## 4. Log in to Grafana

The username is `admin`. The password was generated at install time and lives only in a cluster Secret:

```bash
kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d; echo
```

Open http://localhost:3000, then Dashboards, Kubernetes agent, Demo workloads.

### Log in to Argo CD

The username is `admin`. The first password is generated at install time:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
```

Open http://localhost:8080. `./scripts/portforward.sh` starts this forward too.

## 5. Check that everything is reachable

```bash
curl -s localhost:8000/healthz                 # {"ok":true}
curl -s localhost:3000/api/health              # JSON with "database": "ok"
curl -s localhost:11434/api/tags | head -c 80  # lists qwen2.5:7b
kubectl -n ai-agent exec deploy/k8s-agent -- python -c \
  "import httpx; print(httpx.get('http://host.docker.internal:11434/api/tags', timeout=5).status_code)"   # 200
```

## 6. Try it

Create the demo incident, then open http://localhost:8000:

```bash
kubectl apply -f demo/scenarios/broken-pod.yaml
```

- **Chat:** send "The pod web-service in namespace demo is stuck in Pending. Can you fix it?"
- **Alert:** after 1 to 2 minutes the `PodPending` alert fires, and a run appears under "Alerts".
- **Approve:** the plan proposes `label_node` on `agent-lab-worker` with `type=web`. Nothing changes until you click Approve.

Reset the incident between tests:

```bash
kubectl label node agent-lab-worker type-
kubectl -n demo delete pod web-service
kubectl apply -f demo/scenarios/broken-pod.yaml
```

Clean up the namespace between other scenarios: `kubectl -n demo delete deploy,svc,configmap --all`.

## 7. Stop everything

```bash
# Ctrl+C in each port-forward and Ollama terminal, or:
pkill -f "port-forward"
pkill -f "ollama serve"
docker stop agent-lab-control-plane agent-lab-worker
```

Then quit Docker Desktop if you want the memory back.

## What survives a restart

| Survives | Does not survive |
|---|---|
| Cluster objects, RBAC, the loaded agent image | Port-forwards |
| Agent history (SQLite on its volume) | Ollama (start it yourself) |
| Secrets (webhook token, Grafana password) | Prometheus data (storage is off), so Grafana graphs start empty |

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Page says "ReadTimeout" or a run never finishes | Two Ollama servers, or one request stuck. Run `pgrep -fl -i ollama`, stop all (`pkill -f "ollama serve"; pkill -f llama-server`), start one. Replies are capped at 1,500 tokens (`OLLAMA_NUM_PREDICT`). |
| `localhost:8000` refuses the connection | The agent port-forward is not running. Start it (step 3). |
| Grafana returns 000 or does not load | The Grafana port-forward is not running. Start it (step 3). |
| Alert row shows `interrupted` | The agent restarted mid-run. Unfinished alert runs resume automatically at startup. |
| No new alert row for a pod that broke again | One run per resource per 15 minutes, and Alertmanager re-sends a firing alert every hour. |
| Nodes stay NotReady after `docker start` | Recreate the cluster (below). |

## Rebuild from scratch

```bash
kind delete cluster --name agent-lab
./bootstrap/bootstrap.sh
```

`bootstrap/bootstrap.sh` creates the cluster from `bootstrap/kind-config.yaml`, installs metrics-server (pinned), creates the
secrets, installs Argo CD, registers the repo (read-only deploy key at `~/.config/k8s-agent-lab/argocd-deploy-key`), applies
the root app so Argo CD pulls the monitoring platform from git, then builds and loads the agent image and applies
`demo/base` and `apps/agent`. It is safe to re-run. The Grafana password and the webhook token are generated again, and the
agent's history is lost. Argo CD reads from GitHub `main`, so the platform files must be pushed first. Secrets (Phase 2)
and the agent image (Phase 3) are still manual steps; see `GITOPS-PLAN.md`.

## Safety notes

- **Ollama listens on all interfaces** (`0.0.0.0`) so the cluster can reach it. Anyone on your network can use the model while it runs. Use a trusted network, and stop it when you are done.
- **The agent API has no login.** It is reachable only through the port-forward on your Mac. Do not expose the Service.
- **Every change needs your approval.** The model has no write tools. The guards and RBAC limit what an approved plan can do.
- **Lab settings only:** metrics-server runs with `--kubelet-insecure-tls`, and Prometheus and Grafana have no storage.

## Logs (Loki and Alloy)

Deployed by Argo CD from `platform/` (Loki 7.3.0 and Alloy 1.13.0, namespace `monitoring`). Alloy tails pod logs from every
namespace through the Kubernetes API and pushes them to Loki, which keeps 6 hours (no persistence, so a Loki restart clears them).

- **Grafana:** Dashboards, Kubernetes agent, Demo workloads, "Logs" panel at the bottom. Or Explore, data source Loki.
- **Agent:** `search_logs` reads stored logs, and `get_pod_logs` falls back to Loki when the pod no longer exists.
- **Quick check:** `kubectl -n monitoring port-forward svc/loki 3100:3100`, then
  `curl -s -G localhost:3100/loki/api/v1/label/namespace/values`.
- **Demo:** `kubectl apply -f demo/scenarios/crash.yaml`, delete it, then ask the agent what the deleted `crasher` pod printed.
