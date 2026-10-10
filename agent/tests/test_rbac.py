"""The agent's permissions are part of its safety. This test pins them: a permission may only be added by changing this
test too, which makes the change visible in review. Every read below is one the agent's code actually performs."""
import pathlib

import yaml

RBAC = pathlib.Path(__file__).resolve().parents[2] / "apps" / "agent" / "rbac.yaml"

# Cluster-wide, read-only. Each entry is used by a registered tool, a guard, or the post-fix health check.
EXPECTED_CLUSTER_READ = {
    ("", "nodes"), ("", "pods"), ("", "pods/log"), ("", "events"), ("", "services"), ("", "endpoints"),
    ("apps", "deployments"), ("apps", "replicasets"), ("apps", "statefulsets"), ("apps", "daemonsets"),
    ("batch", "jobs"), ("batch", "cronjobs"),
}
# Inside the demo namespace only.
EXPECTED_DEMO_WRITE = {
    ("", "pods", "delete"),
    ("apps", "deployments", "patch"), ("apps", "deployments/scale", "patch"),
    ("apps", "statefulsets", "patch"), ("apps", "daemonsets", "patch"),
    ("batch", "jobs", "patch"), ("batch", "jobs", "delete"),
    ("batch", "cronjobs", "patch"), ("batch", "cronjobs", "delete"),
    ("", "services", "patch"), ("", "services", "delete"),
    ("", "configmaps", "patch"), ("", "configmaps", "delete"),
}


def docs():
    return [d for d in yaml.safe_load_all(RBAC.read_text()) if d]


def triples(rules):
    return {(g, r, v) for rule in rules for g in rule["apiGroups"] for r in rule["resources"] for v in rule["verbs"]}


def by_name(kind, name):
    return next(d for d in docs() if d["kind"] == kind and d["metadata"]["name"] == name)


def test_cluster_wide_read_is_exactly_what_the_code_uses():
    got = triples(by_name("ClusterRole", "k8s-agent-read")["rules"])
    assert {(g, r) for g, r, _ in got} == EXPECTED_CLUSTER_READ
    assert {v for _, _, v in got} == {"get", "list"}, "the cluster-wide role must be read-only"


def test_writes_are_limited_to_the_demo_namespace_role():
    role = by_name("Role", "k8s-agent-write")
    assert role["metadata"]["namespace"] == "demo"
    assert triples(role["rules"]) == EXPECTED_DEMO_WRITE


def test_the_only_cluster_wide_write_is_labelling_nodes():
    role = by_name("ClusterRole", "k8s-agent-patch-nodes")
    assert triples(role["rules"]) == {("", "nodes", "patch")}


def test_no_role_is_broader_than_it_should_be():
    for d in docs():
        if d["kind"] not in ("Role", "ClusterRole"):
            continue
        for rule in d["rules"]:
            assert "*" not in rule["apiGroups"] + rule["resources"] + rule["verbs"], d["metadata"]["name"]
            assert not {"create", "update", "watch", "deletecollection", "escalate", "bind", "impersonate"} & set(rule["verbs"]), d["metadata"]["name"]
            assert "secrets" not in rule["resources"], d["metadata"]["name"] + " must never touch secrets"


def test_every_binding_points_at_a_role_that_exists_and_only_the_agent_gets_it():
    names = {(d["kind"], d["metadata"]["name"]) for d in docs()}
    for d in docs():
        if d["kind"] in ("RoleBinding", "ClusterRoleBinding"):
            assert (d["roleRef"]["kind"], d["roleRef"]["name"]) in names
            assert [(s["kind"], s["name"], s["namespace"]) for s in d["subjects"]] == [("ServiceAccount", "k8s-agent", "ai-agent")]
