import pytest
from kubernetes import client

from conftest import NS, items, pod
from tools import guards


def check(**action):
    guards.check(action)


def blocked(match, **action):
    with pytest.raises(guards.GuardError, match=match):
        guards.check(action)


def test_unknown_actions_are_denied_by_default():
    blocked("not on the allowlist", tool="drain_node", args={})
    blocked("not on the allowlist", tool=None, args={})


def test_actions_outside_the_allowed_namespace_are_blocked():
    for tool in ("delete_pod", "restart_deployment", "scale_deployment", "rollback_deployment", "patch_resource"):
        blocked("namespace 'kube-system' is not allowed", tool=tool, args={"namespace": "kube-system", "name": "x", "replicas": 2})
    blocked("namespace 'None' is not allowed", tool="restart_deployment", args={"name": "x"})


def test_restart_in_the_allowed_namespace_passes():
    check(tool="restart_deployment", args={"namespace": "demo", "name": "web"})


@pytest.mark.parametrize("replicas", [0, -1, 6, 50, "3", None, 2.0, True])
def test_replicas_must_be_a_real_integer_from_1_to_5(replicas):
    blocked("replicas must be an integer", tool="scale_deployment", args={"namespace": "demo", "name": "c", "replicas": replicas})


@pytest.mark.parametrize("replicas", [1, 3, 5])
def test_valid_replica_counts_pass(replicas):
    check(tool="scale_deployment", args={"namespace": "demo", "name": "c", "replicas": replicas})


def test_a_pod_with_a_controller_may_be_deleted(fresh_cluster):
    fresh_cluster.core.read_namespaced_pod.return_value = pod(owner=True)
    check(tool="delete_pod", args={"namespace": "demo", "name": "p"})


def test_a_bare_pod_may_not_be_deleted(fresh_cluster):
    fresh_cluster.core.read_namespaced_pod.return_value = pod(owner=False)
    blocked("no controller", tool="delete_pod", args={"namespace": "demo", "name": "p"})


def node(labels):
    return NS(metadata=NS(labels=labels))


def test_label_node_only_with_an_allowed_key(fresh_cluster):
    fresh_cluster.core.read_node.return_value = node({})
    blocked("label key 'app' is not allowed", tool="label_node", args={"node": "w", "key": "app", "value": "shop"})


def test_the_control_plane_is_off_limits(fresh_cluster):
    fresh_cluster.core.read_node.return_value = node({"node-role.kubernetes.io/control-plane": ""})
    blocked("control-plane nodes are off limits", tool="label_node", args={"node": "cp", "key": "type", "value": "web"})


def test_label_needs_a_pod_that_asks_for_it(fresh_cluster):
    fresh_cluster.core.read_node.return_value = node({})
    fresh_cluster.core.list_namespaced_pod.return_value = items(pod(selector=None), pod(selector={"type": "db"}))
    blocked("no pod in the allowed namespaces has nodeSelector type=web", tool="label_node", args={"node": "w", "key": "type", "value": "web"})


def test_label_passes_when_a_pod_selects_it(fresh_cluster):
    fresh_cluster.core.read_node.return_value = node({})
    fresh_cluster.core.list_namespaced_pod.return_value = items(pod(selector={"type": "web"}))
    check(tool="label_node", args={"node": "w", "key": "type", "value": "web"})


def deployment(revision):
    return NS(metadata=NS(uid="d1", annotations={"deployment.kubernetes.io/revision": str(revision)}))


def replicaset(revision, owner="d1"):
    return NS(metadata=NS(annotations={"deployment.kubernetes.io/revision": str(revision)}, owner_references=[NS(uid=owner)]))


def test_rollback_needs_an_earlier_revision(fresh_cluster):
    fresh_cluster.apps.read_namespaced_deployment.return_value = deployment(1)
    fresh_cluster.apps.list_namespaced_replica_set.return_value = items(replicaset(1))
    blocked("no earlier revision", tool="rollback_deployment", args={"namespace": "demo", "name": "shop"})


def test_rollback_ignores_replicasets_of_other_deployments(fresh_cluster):
    fresh_cluster.apps.read_namespaced_deployment.return_value = deployment(2)
    fresh_cluster.apps.list_namespaced_replica_set.return_value = items(replicaset(2), replicaset(1, owner="someone-else"))
    blocked("no earlier revision", tool="rollback_deployment", args={"namespace": "demo", "name": "shop"})


def test_rollback_passes_with_an_earlier_revision(fresh_cluster):
    fresh_cluster.apps.read_namespaced_deployment.return_value = deployment(2)
    fresh_cluster.apps.list_namespaced_replica_set.return_value = items(replicaset(2), replicaset(1))
    check(tool="rollback_deployment", args={"namespace": "demo", "name": "shop"})


def test_patch_and_delete_only_for_the_allowed_kinds():
    blocked("kind 'Secret' is not allowed", tool="patch_resource", args={"kind": "Secret", "namespace": "demo", "name": "x", "patch": "{}"})
    blocked("kind 'Ingress' is not allowed", tool="delete_resource", args={"kind": "Ingress", "namespace": "demo", "name": "x"})
    blocked("kind 'None' is not allowed", tool="patch_resource", args={"namespace": "demo", "name": "x"})


@pytest.mark.parametrize("kind", ["Deployment", "StatefulSet", "DaemonSet"])
def test_workloads_cannot_be_deleted(kind):
    blocked("deleting a", tool="delete_resource", args={"kind": kind, "namespace": "demo", "name": "x"})


@pytest.mark.parametrize("kind", ["Job", "CronJob", "Service", "ConfigMap"])
def test_small_objects_can_be_deleted(kind):
    check(tool="delete_resource", args={"kind": kind, "namespace": "demo", "name": "x"})


def test_a_missing_object_is_reported_not_crashed(fresh_cluster):
    fresh_cluster.core.read_namespaced_service.side_effect = client.ApiException(status=404, reason="Not Found")
    blocked("could not be read \\(404 Not Found\\)", tool="patch_resource",
            args={"kind": "Service", "namespace": "demo", "name": "ghost", "patch": '{"spec": {"x": 1}}'})


def test_patch_contents_are_checked():
    blocked("may not be changed", tool="patch_resource",
            args={"kind": "Deployment", "namespace": "demo", "name": "x", "patch": '{"spec": {"template": {"spec": {"hostNetwork": true}}}}'})
    blocked("not valid JSON", tool="patch_resource", args={"kind": "Service", "namespace": "demo", "name": "x", "patch": "{oops"})
    check(tool="patch_resource", args={"kind": "Service", "namespace": "demo", "name": "x", "patch": '{"spec": {"selector": {"app": "shop"}}}'})


def test_max_actions_constant_is_a_small_number():
    assert 1 <= guards.MAX_ACTIONS <= 3


def test_a_patch_with_a_stray_brace_and_its_own_name_passes_the_guard():
    check(tool="patch_resource", args={"kind": "Service", "namespace": "demo", "name": "shop",
                                       "patch": '{"metadata": {"name": "shop"}, "spec": {"selector": {"app": "shop"}}}}'})
    blocked("labels and annotations", tool="patch_resource", args={"kind": "Service", "namespace": "demo", "name": "shop",
                                                                  "patch": '{"metadata": {"name": "other"}, "spec": {"x": 1}}'})
