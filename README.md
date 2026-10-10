# Local Kubernetes troubleshooting agent

An AI agent that investigates Kubernetes problems, proposes a fix, and waits for your approval before changing anything.
Runs entirely on one Mac: Ollama (qwen2.5:7b), a kind cluster, and a Prometheus, Grafana and Loki monitoring stack.

| Read | For |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | How it works, in plain terms |
| [RUNNING.md](RUNNING.md) | Start, expose, stop, troubleshoot |
| [TESTING.md](TESTING.md) | Test scenarios with commands, expected behaviour, expected fix and clean-up |
| [GITOPS-PLAN.md](GITOPS-PLAN.md) | The plan for moving the cluster to GitOps (Argo CD) |

## Layout

```
agent/            the agent's source code and Dockerfile
apps/agent/       Kustomize app for the agent (the image is set in kustomization.yaml)
platform/         Argo CD Applications (platform/apps) and Helm values for Prometheus, Loki, Alloy, Grafana; the Grafana dashboard
demo/base/        the demo namespace
demo/scenarios/   broken workloads, applied by hand for one test
bootstrap/        kind config, bootstrap.sh, Argo CD values, project and root app, Sealed Secrets helpers
scripts/          port-forward helpers
```

## Quick start

```bash
./bootstrap/bootstrap.sh                    # cluster, monitoring, agent (safe to re-run)
OLLAMA_HOST=0.0.0.0:11434 ollama serve      # in its own terminal; run only one Ollama server
./scripts/portforward.sh                    # agent on :8000, Grafana on :3000
```

Then open http://localhost:8000. Teardown: `kind delete cluster --name agent-lab`, stop `ollama serve`.
