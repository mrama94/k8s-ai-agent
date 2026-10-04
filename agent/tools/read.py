import json
import os
import re
import time
from typing import Literal

import httpx
from kubernetes import client
from kubernetes.utils import parse_quantity
from langchain_core.tools import tool

from k8s_client import apps, core, custom, networking
from tools.runbooks import search_runbooks

MAX_CHARS = 4000
REVISION = "deployment.kubernetes.io/revision"


def _clip(text: str) -> str:
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "\n...[truncated]"


def _j(obj) -> str:
    """Compact JSON keeps tool output small, which matters for a 7B model's context."""
    return _clip(json.dumps(obj, separators=(",", ":"), default=str))


def _state(state) -> dict:
    """Keep only the populated branch (waiting, terminated or running) of a container state."""
    if state is None:
        return {}
    for kind in ("waiting", "terminated", "running"):
        value = getattr(state, kind)
        if value:
            return {kind: {k: v for k, v in value.to_dict().items() if v}}
    return {}


@tool
def list_nodes() -> str:
    """List cluster nodes with their Ready status, labels and taints."""
    out = []
    for n in core.list_node().items:
        ready = next((c.status for c in n.status.conditions if c.type == "Ready"), "Unknown")
        out.append({
            "name": n.metadata.name,
            "ready": ready,
            "labels": n.metadata.labels,
            "taints": [f"{t.key}={t.value}:{t.effect}" for t in (n.spec.taints or [])],
        })
    return _clip(json.dumps(out, indent=2))


@tool
def list_pods(namespace: str = "default") -> str:
    """List pods in a namespace with labels, phase, node and restart counts."""
    out = []
    for p in core.list_namespaced_pod(namespace).items:
        statuses = p.status.container_statuses or []
        out.append({
            "name": p.metadata.name,
            "labels": p.metadata.labels,
            "phase": p.status.phase,
            "node": p.spec.node_name,
            "restarts": sum(s.restart_count for s in statuses),
        })
    return _j(out)


@tool
def describe_pod(namespace: str, name: str) -> str:
    """Show a pod's labels, owner, nodeSelector, tolerations, resources, conditions and container states, including the last termination reason."""
    p = core.read_namespaced_pod(name, namespace)
    limits = {c.name: c.resources.to_dict() for c in p.spec.containers}
    return _j({
        "name": name,
        "phase": p.status.phase,
        "labels": p.metadata.labels,
        "owner": [f"{o.kind}/{o.name}" for o in (p.metadata.owner_references or [])],
        "node_selector": p.spec.node_selector,
        "tolerations": [t.to_dict() for t in (p.spec.tolerations or [])],
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (p.status.conditions or [])
        ],
        "containers": [
            {
                "name": s.name, "image": s.image, "ready": s.ready, "restarts": s.restart_count,
                "state": _state(s.state), "last_state": _state(s.last_state),
                "resources": limits.get(s.name),
            }
            for s in (p.status.container_statuses or [])
        ],
    })


@tool
def get_events(namespace: str, pod_name: str) -> str:
    """Get recent events for a pod: scheduling failures, image pull errors, probe failures."""
    events = core.list_namespaced_event(namespace, field_selector=f"involvedObject.name={pod_name}")
    out = [{"type": e.type, "reason": e.reason, "message": e.message} for e in events.items]
    return _clip(json.dumps(out[-10:], indent=2))


@tool
def get_pod_logs(namespace: str, name: str, tail_lines: int = 50) -> str:
    """Get the last lines of a pod's logs."""
    try:
        return _clip(core.read_namespaced_pod_log(name, namespace, tail_lines=tail_lines))
    except client.ApiException as exc:
        if exc.status == 404:  # the pod is gone: stored logs may still exist
            return "pod not found; stored logs from Loki:\n" + _loki_lines(namespace, name)
        return f"could not read logs: {exc.reason}"
    except Exception as exc:  # a Pending pod has no logs yet
        return f"could not read logs: {exc}"


@tool
def get_namespace_events(namespace: str) -> str:
    """List the most recent Warning events across a namespace. Start here when you do not yet know which pod is affected."""
    events = core.list_namespaced_event(namespace, field_selector="type=Warning").items
    events.sort(key=lambda e: e.last_timestamp or e.event_time or e.metadata.creation_timestamp, reverse=True)
    return _j([
        {"object": f"{e.involved_object.kind}/{e.involved_object.name}", "reason": e.reason,
         "message": e.message, "count": e.count}
        for e in events[:15]
    ])


@tool
def get_previous_logs(namespace: str, name: str, tail_lines: int = 50) -> str:
    """Get logs from the previous, crashed instance of a pod's container. Use this for CrashLoopBackOff."""
    try:
        return _clip(core.read_namespaced_pod_log(name, namespace, previous=True, tail_lines=tail_lines))
    except client.ApiException as exc:
        if exc.status == 404:
            return "pod not found; stored logs from Loki:\n" + _loki_lines(namespace, name)
        return f"no previous logs: {exc.reason}"
    except Exception as exc:
        return f"no previous logs: {exc}"


@tool
def get_deployment(namespace: str, name: str) -> str:
    """Show a Deployment's replica counts, images, conditions and rollout history (revisions with their images)."""
    d = apps.read_namespaced_deployment(name, namespace)
    owned = [
        rs for rs in apps.list_namespaced_replica_set(namespace).items
        if any(o.uid == d.metadata.uid for o in (rs.metadata.owner_references or []))
    ]
    revisions = sorted(
        (
            {
                "revision": rs.metadata.annotations.get(REVISION),
                "images": [c.image for c in rs.spec.template.spec.containers],
                "replicas": rs.status.replicas,
                "ready": rs.status.ready_replicas or 0,
            }
            for rs in owned
        ),
        key=lambda r: int(r["revision"] or 0),
        reverse=True,
    )
    return _j({
        "desired": d.spec.replicas,
        "ready": d.status.ready_replicas or 0,
        "updated": d.status.updated_replicas or 0,
        "available": d.status.available_replicas or 0,
        "strategy": d.spec.strategy.type,
        "selector": d.spec.selector.match_labels,
        "images": [c.image for c in d.spec.template.spec.containers],
        "current_revision": d.metadata.annotations.get(REVISION),
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (d.status.conditions or [])
        ],
        "revisions": revisions[:5],
    })


@tool
def get_service(namespace: str, name: str) -> str:
    """Show a Service's selector and ports, its endpoints (ready and not ready), and the pods its selector matches."""
    svc = core.read_namespaced_service(name, namespace)
    selector = svc.spec.selector or {}
    matching = []
    if selector:
        label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
        matching = [
            {"name": p.metadata.name, "phase": p.status.phase,
             "ready": all(s.ready for s in (p.status.container_statuses or []))}
            for p in core.list_namespaced_pod(namespace, label_selector=label_selector).items
        ]
    try:  # the Endpoints API is deprecated in newer Kubernetes but still served
        ep = core.read_namespaced_endpoints(name, namespace)
        ready = sum(len(s.addresses or []) for s in (ep.subsets or []))
        not_ready = sum(len(s.not_ready_addresses or []) for s in (ep.subsets or []))
    except Exception:
        ready = not_ready = 0
    return _j({
        "type": svc.spec.type,
        "selector": selector,
        "ports": [{"port": p.port, "target_port": p.target_port} for p in svc.spec.ports],
        "endpoints_ready": ready,
        "endpoints_not_ready": not_ready,
        "pods_matching_selector": matching,
    })


@tool
def list_ingresses(namespace: str) -> str:
    """List Ingress rules in a namespace: hosts, paths and the Service each path routes to."""
    out = []
    for ing in networking.list_namespaced_ingress(namespace).items:
        for rule in (ing.spec.rules or []):
            for path in (rule.http.paths if rule.http else []):
                svc = path.backend.service
                out.append({"ingress": ing.metadata.name, "host": rule.host, "path": path.path,
                            "service": svc.name if svc else None,
                            "port": (svc.port.number or svc.port.name) if svc and svc.port else None})
    return _j(out)


@tool
def get_configmap_keys(namespace: str, name: str) -> str:
    """List the keys in a ConfigMap and the size of each value. Values are never returned."""
    cm = core.read_namespaced_config_map(name, namespace)
    return _j({"keys_and_sizes": {k: len(v) for k, v in (cm.data or {}).items()},
               "binary_keys": list((cm.binary_data or {}).keys())})


@tool
def list_pvcs(namespace: str) -> str:
    """List PersistentVolumeClaims with phase, storage class, requested size and bound volume."""
    return _j([
        {"name": c.metadata.name, "phase": c.status.phase, "storage_class": c.spec.storage_class_name,
         "requested": c.spec.resources.requests.get("storage"), "volume": c.spec.volume_name}
        for c in core.list_namespaced_persistent_volume_claim(namespace).items
    ])


@tool
def get_resource_quota(namespace: str) -> str:
    """Show ResourceQuotas for a namespace: what is limited and how much is used."""
    return _j([
        {"name": q.metadata.name, "hard": q.status.hard, "used": q.status.used}
        for q in core.list_namespaced_resource_quota(namespace).items
    ])


@tool
def describe_node(name: str) -> str:
    """Show a node's conditions, taints, allocatable resources, and how much CPU and memory pods request on it."""
    n = core.read_node(name)
    pods = core.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={name}").items
    cpu = mem = 0
    for p in pods:
        for c in p.spec.containers:
            req = (c.resources.requests or {}) if c.resources else {}
            cpu += parse_quantity(req.get("cpu", "0"))
            mem += parse_quantity(req.get("memory", "0"))
    alloc = n.status.allocatable
    return _j({
        "conditions": {c.type: c.status for c in n.status.conditions},
        "taints": [f"{t.key}={t.value}:{t.effect}" for t in (n.spec.taints or [])],
        "allocatable": {"cpu": alloc.get("cpu"), "memory": alloc.get("memory"), "pods": alloc.get("pods")},
        "requested": {"cpu_cores": round(float(cpu), 2), "memory_gib": round(float(mem) / 2**30, 2)},
        "pod_count": len(pods),
    })


@tool
def get_metrics(namespace: str) -> str:
    """Show live CPU (millicores) and memory (MiB) for nodes and for pods in a namespace. Needs metrics-server."""
    try:
        nodes = custom.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "nodes")["items"]
        pods = custom.list_namespaced_custom_object("metrics.k8s.io", "v1beta1", namespace, "pods")["items"]
    except Exception as exc:
        return f"metrics unavailable (is metrics-server installed?): {exc}"

    def usage(u):
        return {"cpu_m": round(float(parse_quantity(u["cpu"])) * 1000),
                "mem_mib": round(float(parse_quantity(u["memory"])) / 2**20)}

    return _j({
        "nodes": {n["metadata"]["name"]: usage(n["usage"]) for n in nodes},
        "pods": {p["metadata"]["name"]: [dict(name=c["name"], **usage(c["usage"])) for c in p["containers"]]
                 for p in pods},
    })


PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus-server.monitoring.svc:80")


def _metric_query(metric: str, ns: str, pod: str) -> str:
    sel = f'namespace="{ns}", pod=~"{pod}"'
    return {
        "memory_mib": f'sum by (pod) (container_memory_working_set_bytes{{{sel}, container!=""}}) / 1048576',
        "memory_limit_mib": f'sum by (pod) (kube_pod_container_resource_limits{{{sel}, resource="memory"}}) / 1048576',
        "cpu_millicores": f'sum by (pod) (rate(container_cpu_usage_seconds_total{{{sel}, container!=""}}[2m])) * 1000',
        "restarts": f'sum by (pod) (kube_pod_container_status_restarts_total{{{sel}}})',
        "oom_killed": f'sum by (pod) (kube_pod_container_status_last_terminated_reason{{{sel}, reason="OOMKilled"}})',
    }[metric]


@tool
def get_metric_history(
    namespace: str,
    metric: Literal["memory_mib", "memory_limit_mib", "cpu_millicores", "restarts", "oom_killed"],
    pod_prefix: str = "",
    minutes: int = 30,
) -> str:
    """Show how a metric changed over the last minutes for pods in a namespace (from Prometheus): memory_mib, memory_limit_mib, cpu_millicores, restarts or oom_killed. Use it to compare memory use with the limit or to see whether restarts are still rising. pod_prefix filters pods by name prefix."""
    if not re.fullmatch(r"[a-z0-9.-]{1,63}", namespace) or not re.fullmatch(r"[a-z0-9.-]{0,63}", pod_prefix):
        return "invalid namespace or pod_prefix"
    minutes = max(5, min(int(minutes), 180))
    query = _metric_query(metric, namespace, re.escape(pod_prefix) + ".*")
    end = time.time()
    try:
        resp = httpx.get(
            f"{PROMETHEUS_URL}/api/v1/query_range",
            params={"query": query, "start": end - minutes * 60, "end": end, "step": max(15, minutes * 60 // 12)},
            timeout=10,
        )
        resp.raise_for_status()
        series = resp.json()["data"]["result"]
    except Exception as exc:
        return f"prometheus unavailable: {exc}"
    if not series:
        return "no data (pod may be too new, or the metric does not apply)"
    out = []
    for item in series[:10]:
        values = [round(float(v), 1) for _, v in item["values"]]
        out.append({"pod": item["metric"].get("pod"), "now": values[-1], "min": min(values), "max": max(values),
                    "start": values[0], "samples": values[:: max(1, len(values) // 6)]})
    return _j({"metric": metric, "window_minutes": minutes, "pods": out})


LOKI_URL = os.getenv("LOKI_URL", "http://loki.monitoring.svc:3100")
_SAFE_TEXT = re.compile(r"[A-Za-z0-9 _.:/=,@-]{0,80}")


def _loki_lines(namespace: str, pod_prefix: str = "", text: str = "", minutes: int = 30) -> str:
    """Query Loki with fixed templates and strictly validated inputs (never a model-written query)."""
    if (not re.fullmatch(r"[a-z0-9.-]{1,63}", namespace) or not re.fullmatch(r"[a-z0-9.-]{0,63}", pod_prefix)
            or not _SAFE_TEXT.fullmatch(text)):
        return "invalid namespace, pod_prefix or text"
    minutes = max(5, min(int(minutes), 360))
    query = f'{{namespace="{namespace}", pod=~"{re.escape(pod_prefix)}.*"}}' + (f' |= "{text}"' if text else "")
    end = time.time()
    try:
        resp = httpx.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={"query": query, "start": int((end - minutes * 60) * 1e9), "end": int(end * 1e9),
                    "limit": 30, "direction": "backward"},
            timeout=10,
        )
        resp.raise_for_status()
        streams = resp.json()["data"]["result"]
    except Exception as exc:
        return f"loki unavailable: {exc}"
    lines = sorted(
        ((int(ts), s["stream"].get("pod"), line) for s in streams for ts, line in s["values"]), reverse=True
    )[:30]
    if not lines:
        return "no matching log lines"
    return _clip("\n".join(f"{pod}: {line[:300]}" for _, pod, line in lines))


@tool
def search_logs(namespace: str, pod_prefix: str = "", text: str = "", minutes: int = 30) -> str:
    """Search stored logs (from Loki) for pods in a namespace, newest first. Works for crashed, restarted and already deleted pods, so use it for CrashLoopBackOff and for what happened before a pod went away. pod_prefix filters by pod name prefix, text keeps only lines containing that word or phrase (for example error or OOM)."""
    return _loki_lines(namespace, pod_prefix, text, minutes)


READ_TOOLS = [
    list_nodes, list_pods, describe_pod, get_events, get_pod_logs,
    get_namespace_events, search_logs, get_deployment, get_service,
    describe_node, get_metric_history, search_runbooks,
]
READ_TOOL_MAP = {t.name: t for t in READ_TOOLS}
