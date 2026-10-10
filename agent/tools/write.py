import datetime

from kubernetes import client

from k8s_client import apps, core
from tools import guards, patching

REVISION = "deployment.kubernetes.io/revision"


def label_node(node: str, key: str, value: str) -> str:
    core.patch_node(node, {"metadata": {"labels": {key: value}}})
    return f"labelled node {node} with {key}={value}"


def delete_pod(namespace: str, name: str) -> str:
    core.delete_namespaced_pod(name, namespace)
    return f"deleted pod {namespace}/{name}"


def restart_deployment(namespace: str, name: str) -> str:
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    body = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": stamp}}}}}
    apps.patch_namespaced_deployment(name, namespace, body)
    return f"restarted deployment {namespace}/{name}"


def scale_deployment(namespace: str, name: str, replicas: int) -> str:
    apps.patch_namespaced_deployment_scale(name, namespace, {"spec": {"replicas": int(replicas)}})
    return f"scaled {namespace}/{name} to {int(replicas)} replicas"


def rollback_deployment(namespace: str, name: str) -> str:
    """Re-apply the pod template of the previous revision, as `kubectl rollout undo` does."""
    dep = apps.read_namespaced_deployment(name, namespace)
    current = int(dep.metadata.annotations.get(REVISION, "0"))
    owned = [
        rs for rs in apps.list_namespaced_replica_set(namespace).items
        if any(o.uid == dep.metadata.uid for o in (rs.metadata.owner_references or []))
    ]
    older = [rs for rs in owned if int(rs.metadata.annotations.get(REVISION, "0")) < current]
    if not older:
        return f"no earlier revision of {namespace}/{name} to roll back to"
    target = max(older, key=lambda rs: int(rs.metadata.annotations[REVISION]))
    template = client.ApiClient().sanitize_for_serialization(target.spec.template)
    template.get("metadata", {}).get("labels", {}).pop("pod-template-hash", None)
    apps.patch_namespaced_deployment(name, namespace, {"spec": {"template": template}})
    return f"rolled back {namespace}/{name} to revision {target.metadata.annotations[REVISION]}"


def patch_resource(kind: str, namespace: str, name: str, patch) -> str:
    return patching.apply(kind, namespace, name, patching.parse_patch(patch, (kind, namespace, name)))


def delete_resource(kind: str, namespace: str, name: str) -> str:
    return patching.delete(kind, namespace, name)


WRITE_TOOLS = {
    "patch_resource": patch_resource,
    "delete_resource": delete_resource,
    "label_node": label_node,
    "delete_pod": delete_pod,
    "restart_deployment": restart_deployment,
    "scale_deployment": scale_deployment,
    "rollback_deployment": rollback_deployment,
}


def execute(action: dict) -> str:
    """Run one approved action. The guard check always runs first, even after approval."""
    try:
        guards.check(action)
    except guards.GuardError as exc:
        return f"BLOCKED by guard: {exc}"
    try:
        return WRITE_TOOLS[action["tool"]](**action["args"])
    except client.ApiException as exc:
        return f"FAILED: Kubernetes API {exc.status} {exc.reason}"
    except Exception as exc:
        return f"FAILED: {exc}"
