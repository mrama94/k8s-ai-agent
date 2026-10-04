# Local Kubernetes troubleshooting agent

Implementation of the "Free & Local Build Guide (macOS)" (deep-troubleshooting version, steps 1-20).
Ollama (qwen2.5:7b) + kind cluster `agent-lab` + LangGraph agent with approval gate, guards and RBAC.

    ollama pull qwen2.5:7b && OLLAMA_HOST=0.0.0.0:11434 ollama serve   # quit the Ollama menu-bar app first
    kind create cluster --name agent-lab --config kind-config.yaml
    kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
    kubectl -n kube-system patch deployment metrics-server --type=json -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
    (cd agent && docker build -t k8s-agent:local . && kind load docker-image k8s-agent:local --name agent-lab)
    kubectl apply -k k8s/ && kubectl -n ai-agent rollout status deploy/k8s-agent
    kubectl -n ai-agent port-forward svc/k8s-agent 8000:8000   # http://localhost:8000

Demo incidents are in `k8s/demo/`. Teardown: `kind delete cluster --name agent-lab`, stop `ollama serve`.

## Alert-driven runs (observability)

    ./observability/install.sh   # Prometheus + Alertmanager + kube-state-metrics in "monitoring"; creates the webhook token Secret
    kubectl apply -k k8s/ && kubectl -n ai-agent rollout restart deploy/k8s-agent

Alert rules (namespace `demo`) are in `observability/values.yaml`. Alertmanager posts to the agent's `/api/alert`
(bearer token, one run per resource per 15 min, one alert run at a time). Runs stop at approval; the page lists them under "Alerts".
