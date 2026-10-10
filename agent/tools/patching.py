"""Generic, guarded object edits: parse a patch, preview it with a server-side dry run, apply it."""
import json
import re

from kubernetes import client

from k8s_client import apps, batch, core

# kind -> (api, read, patch, delete)
KINDS = {
    "Deployment": (apps, "read_namespaced_deployment", "patch_namespaced_deployment", "delete_namespaced_deployment"),
    "StatefulSet": (apps, "read_namespaced_stateful_set", "patch_namespaced_stateful_set", "delete_namespaced_stateful_set"),
    "DaemonSet": (apps, "read_namespaced_daemon_set", "patch_namespaced_daemon_set", "delete_namespaced_daemon_set"),
    "Job": (batch, "read_namespaced_job", "patch_namespaced_job", "delete_namespaced_job"),
    "CronJob": (batch, "read_namespaced_cron_job", "patch_namespaced_cron_job", "delete_namespaced_cron_job"),
    "Service": (core, "read_namespaced_service", "patch_namespaced_service", "delete_namespaced_service"),
    "ConfigMap": (core, "read_namespaced_config_map", "patch_namespaced_config_map", "delete_namespaced_config_map"),
}
# Deleting a top-level workload is never a fix: patch, restart, scale or roll it back instead.
DELETE_KINDS = {"Job", "CronJob", "Service", "ConfigMap"}

MAX_PATCH_CHARS = 3000
TOP_LEVEL_KEYS = {"spec", "data", "binaryData", "metadata"}
METADATA_KEYS = {"labels", "annotations"}
# Fields that would widen what a workload can do on the node or in the cluster.
FORBIDDEN_KEYS = {
    "hostNetwork", "hostPID", "hostIPC", "hostPath", "privileged", "allowPrivilegeEscalation", "capabilities",
    "securityContext", "serviceAccountName", "serviceAccount", "externalIPs", "loadBalancerIP", "nodeName",
}
SKIP_FIELDS = {"managedFields", "resourceVersion", "generation", "uid", "creationTimestamp", "status", "selfLink"}
PATCH_TYPE = "application/strategic-merge-patch+json"


class PatchError(Exception):
    """The patch is malformed or touches something the agent may not change."""


def _load(raw):
    """Parse the model's JSON. A 7B model often adds a stray closing brace or bracket at the end; that is the only slip
    forgiven, because the parsed result is validated and shown to the human exactly as it will be applied."""
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    try:
        return json.loads(text)
    except ValueError as exc:
        try:
            obj, end = json.JSONDecoder().raw_decode(text)
        except ValueError:
            raise PatchError(f"patch is not valid JSON: {exc}")
        if re.fullmatch(r"[\]}\s]*", text[end:]):
            return obj
        raise PatchError(f"patch is not valid JSON: {exc}")


def parse_patch(raw, target: tuple | None = None) -> dict:
    """Validate a patch. `target` is (kind, namespace, name) of the object being patched: with it, the identity fields
    a manifest-style patch repeats (apiVersion, kind, metadata.name, metadata.namespace) are dropped when they match the
    target, since they cannot change anything. Without a target, or when they differ, they are refused."""
    patch = _load(raw)
    if not isinstance(patch, dict) or not patch:
        raise PatchError("patch must be a non-empty JSON object")
    if target:
        kind, namespace, name = target
        patch = dict(patch)
        patch.pop("apiVersion", None)
        if patch.get("kind") == kind:
            patch.pop("kind")
        meta = patch.get("metadata")
        if isinstance(meta, dict):
            meta = {k: v for k, v in meta.items() if not (k == "name" and v == name or k == "namespace" and v == namespace)}
            if meta:
                patch["metadata"] = meta
            else:
                patch.pop("metadata")
        if not patch:
            raise PatchError("the patch changes nothing")
    if len(json.dumps(patch)) > MAX_PATCH_CHARS:
        raise PatchError(f"patch is larger than {MAX_PATCH_CHARS} characters")
    if not set(patch) <= TOP_LEVEL_KEYS:
        raise PatchError(f"patch may only change {sorted(TOP_LEVEL_KEYS)}, not {sorted(set(patch) - TOP_LEVEL_KEYS)}")
    meta = patch.get("metadata")
    if meta is not None and (not isinstance(meta, dict) or not set(meta) <= METADATA_KEYS):
        raise PatchError("metadata may only change labels and annotations")
    _walk(patch)
    return patch


def _walk(node) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_KEYS:
                raise PatchError(f"field '{key}' may not be changed")
            if key == "type" and isinstance(value, str) and value in {"LoadBalancer", "NodePort", "ExternalName"}:
                raise PatchError(f"Service type {value} is not allowed")
            _walk(value)
    elif isinstance(node, list):
        for item in node:
            _walk(item)


def read(kind: str, name: str, namespace: str):
    api, reader, _, _ = KINDS[kind]
    return getattr(api, reader)(name, namespace)


def _plain(obj) -> dict:
    return client.ApiClient().sanitize_for_serialization(obj)


def _flat(node, prefix="") -> dict:
    out = {}
    if isinstance(node, dict):
        for key, value in node.items():
            if key not in SKIP_FIELDS:
                out.update(_flat(value, f"{prefix}.{key}" if prefix else key))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            label = item["name"] if isinstance(item, dict) and isinstance(item.get("name"), str) else i
            out.update(_flat(item, f"{prefix}[{label}]"))
    else:
        out[prefix] = node
    return out


def preview(kind: str, namespace: str, name: str, patch: dict) -> list[str]:
    """Server-side dry run. Returns the changed fields, or raises PatchError if the API rejects the patch."""
    api, _, patcher, _ = KINDS[kind]
    before = _plain(read(kind, name, namespace))
    try:
        after = getattr(api, patcher)(name, namespace, patch, dry_run="All", _content_type=PATCH_TYPE)
    except client.ApiException as exc:
        try:
            detail = json.loads(exc.body).get("message", exc.reason)
        except Exception:
            detail = exc.reason
        raise PatchError(f"the API rejected the patch in a dry run: {detail}"[:300])
    a, b = _flat(before), _flat(_plain(after))
    changes = [f"{path}: {a.get(path, '(unset)')} -> {b.get(path, '(removed)')}"
               for path in sorted(set(a) | set(b)) if a.get(path) != b.get(path)]
    if not changes:
        raise PatchError("the patch changes nothing")
    return changes[:12]


def apply(kind: str, namespace: str, name: str, patch: dict) -> str:
    api, _, patcher, _ = KINDS[kind]
    getattr(api, patcher)(name, namespace, patch, _content_type=PATCH_TYPE)
    return f"patched {kind} {namespace}/{name}"


def delete(kind: str, namespace: str, name: str) -> str:
    api, _, _, deleter = KINDS[kind]
    getattr(api, deleter)(name, namespace)
    return f"deleted {kind} {namespace}/{name}"
