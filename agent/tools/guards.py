import os

from kubernetes import client

from k8s_client import apps, core
from tools import patching

ALLOWED_ACTIONS = {
    "label_node", "delete_pod", "restart_deployment", "scale_deployment", "rollback_deployment",
    "patch_resource", "delete_resource",
}
ALLOWED_NAMESPACES = set(os.getenv("ALLOWED_NAMESPACES", "demo").split(","))
ALLOWED_LABEL_KEYS = {"type"}
MAX_ACTIONS = 3
MAX_REPLICAS = 5


class GuardError(Exception):
    """Raised when an action is not allowed to run."""


def check(action: dict) -> None:
    """Deny by default: raise GuardError unless every rule passes."""
    tool = action.get("tool")
    args = action.get("args") or {}
    if tool not in ALLOWED_ACTIONS:
        raise GuardError(f"tool '{tool}' is not on the allowlist")
    if tool != "label_node" and args.get("namespace") not in ALLOWED_NAMESPACES:
        raise GuardError(f"namespace '{args.get('namespace')}' is not allowed")
    if tool == "label_node":
        if args.get("key") not in ALLOWED_LABEL_KEYS:
            raise GuardError(f"label key '{args.get('key')}' is not allowed")
        node = core.read_node(args.get("node", ""))
        if "node-role.kubernetes.io/control-plane" in (node.metadata.labels or {}):
            raise GuardError("control-plane nodes are off limits")
        wanted = (args.get("key"), args.get("value"))
        if not any(
            (p.spec.node_selector or {}).get(wanted[0]) == wanted[1]
            for ns in ALLOWED_NAMESPACES
            for p in core.list_namespaced_pod(ns).items
        ):
            raise GuardError(f"no pod in the allowed namespaces has nodeSelector {wanted[0]}={wanted[1]}")
    if tool == "rollback_deployment":
        dep = apps.read_namespaced_deployment(args.get("name", ""), args["namespace"])
        revision = "deployment.kubernetes.io/revision"
        current = int(dep.metadata.annotations.get(revision, "0"))
        earlier = [
            rs for rs in apps.list_namespaced_replica_set(args["namespace"]).items
            if any(o.uid == dep.metadata.uid for o in (rs.metadata.owner_references or []))
            and int(rs.metadata.annotations.get(revision, "0")) < current
        ]
        if not earlier:
            raise GuardError("deployment has no earlier revision to roll back to")
    if tool == "scale_deployment":
        replicas = args.get("replicas")
        if not isinstance(replicas, int) or not 1 <= replicas <= MAX_REPLICAS:
            raise GuardError(f"replicas must be an integer from 1 to {MAX_REPLICAS}")
    if tool == "delete_pod":
        pod = core.read_namespaced_pod(args.get("name", ""), args["namespace"])
        if not pod.metadata.owner_references:
            raise GuardError("pod has no controller, so deleting it would remove it permanently")
    if tool in {"patch_resource", "delete_resource"}:
        kind = args.get("kind")
        if kind not in patching.KINDS:
            raise GuardError(f"kind '{kind}' is not allowed (allowed: {sorted(patching.KINDS)})")
        if tool == "delete_resource" and kind not in patching.DELETE_KINDS:
            raise GuardError(f"deleting a {kind} is not allowed; patch, restart, scale or roll it back instead")
        try:
            patching.read(kind, args.get("name", ""), args["namespace"])
        except client.ApiException as exc:
            raise GuardError(f"{kind} {args['namespace']}/{args.get('name')} could not be read ({exc.status} {exc.reason})")
    if tool == "patch_resource":
        try:
            patching.parse_patch(args.get("patch"))
        except patching.PatchError as exc:
            raise GuardError(str(exc))
