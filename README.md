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
demo/base/        the demo namespace and a healthy workload (storefront) that git owns
demo/scenarios/   broken workloads, applied by hand for one test
bootstrap/        kind config, bootstrap.sh, Argo CD values, project and root app, Sealed Secrets helpers
scripts/          port-forward helpers; scripts/ci/ holds the checks CI runs
.github/workflows/ CI: builds and publishes the agent image
```

## Checks

Every pull request and push to `main` runs `.github/workflows/ci.yml`: unit tests, lint (YAML, shell, Python), a scan that rejects
plain Secrets, keys and tokens, schema validation of every manifest and Helm chart, and a Docker build. The same scripts run
locally: `PYTHON=<python with the agent requirements> ./scripts/ci/run-all.sh` (see RUNNING.md).

## Quick start

```bash
./lab up          # cluster, Argo CD, monitoring, agent, Ollama and the port-forwards (2 to 5 minutes)
./lab status      # what is running, and any drift from git
./lab stop        # pause (history is kept); ./lab start resumes
./lab down        # destroy (asks first); ./lab up brings it all back
./lab link        # optional: type `lab` from anywhere
```

Then open http://localhost:8000. `./lab doctor` checks that everything a rebuild needs is in place. Details in RUNNING.md.
