import pathlib
import re

import httpx
import pytest

from conftest import NS, items
from tools import read
from tools.read import READ_TOOLS, search_logs, get_metric_history


def test_long_output_is_clipped():
    assert read._clip("x" * 10) == "x" * 10
    clipped = read._clip("x" * (read.MAX_CHARS + 50))
    assert clipped.endswith("[truncated]") and len(clipped) < read.MAX_CHARS + 20


def test_metric_queries_are_built_for_the_namespace_and_pod_prefix():
    q = read._metric_query("memory_mib", "demo", "hog.*")
    assert 'namespace="demo"' in q and 'pod=~"hog.*"' in q and "container_memory_working_set_bytes" in q
    assert "kube_pod_container_resource_limits" in read._metric_query("memory_limit_mib", "demo", ".*")
    for metric in ("cpu_millicores", "restarts", "oom_killed"):
        assert read._metric_query(metric, "demo", ".*")


@pytest.mark.parametrize("namespace,prefix", [('x"; drop', ""), ("demo", 'a"b'), ("", ""), ("DEMO", ""), ("demo", "a b"), ("d" * 70, "")])
def test_unsafe_names_never_reach_prometheus(monkeypatch, namespace, prefix):
    monkeypatch.setattr(read.httpx, "get", lambda *a, **k: pytest.fail("a request was sent"))
    out = get_metric_history.invoke({"namespace": namespace, "metric": "restarts", "pod_prefix": prefix})
    assert out == "invalid namespace or pod_prefix"


@pytest.mark.parametrize("namespace,prefix,text", [('x"', "", ""), ("demo", "a b", ""), ("demo", "", '" or 1=1'), ("demo", "", "a" * 90),
                                                   ("demo", "", "{x}"), ("demo", "", "`cmd`")])
def test_unsafe_log_queries_never_reach_loki(monkeypatch, namespace, prefix, text):
    monkeypatch.setattr(read.httpx, "get", lambda *a, **k: pytest.fail("a request was sent"))
    assert search_logs.invoke({"namespace": namespace, "pod_prefix": prefix, "text": text}) == "invalid namespace, pod_prefix or text"


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def raise_for_status(self):
        if self.status >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)

    def json(self):
        return self.payload


def loki(streams):
    return FakeResponse({"data": {"result": streams}})


def test_log_lines_come_back_newest_first_with_the_pod_name(monkeypatch):
    sent = {}

    def fake_get(url, params, timeout):
        sent.update(url=url, params=params)
        return loki([{"stream": {"pod": "crasher-a"}, "values": [["100", "old line"], ["300", "newest line"]]},
                     {"stream": {"pod": "crasher-b"}, "values": [["200", "middle line"]]}])
    monkeypatch.setattr(read.httpx, "get", fake_get)
    out = search_logs.invoke({"namespace": "demo", "pod_prefix": "crasher", "text": "error"})
    assert out.splitlines() == ["crasher-a: newest line", "crasher-b: middle line", "crasher-a: old line"]
    assert '|= "error"' in sent["params"]["query"] and 'namespace="demo"' in sent["params"]["query"]
    assert sent["params"]["limit"] == 30


def test_long_log_lines_are_cut(monkeypatch):
    monkeypatch.setattr(read.httpx, "get", lambda *a, **k: loki([{"stream": {"pod": "p"}, "values": [["1", "y" * 1000]]}]))
    assert len(search_logs.invoke({"namespace": "demo"}).splitlines()[0]) < 320


def test_no_logs_and_an_unreachable_loki_are_reported_plainly(monkeypatch):
    monkeypatch.setattr(read.httpx, "get", lambda *a, **k: loki([]))
    assert search_logs.invoke({"namespace": "demo"}) == "no matching log lines"

    def down(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(read.httpx, "get", down)
    assert search_logs.invoke({"namespace": "demo"}).startswith("loki unavailable")


def test_metric_history_is_summarised_per_pod(monkeypatch):
    values = [[i, str(v)] for i, v in enumerate([10, 12, 30, 64, 60, 62])]
    monkeypatch.setattr(read.httpx, "get", lambda *a, **k: FakeResponse({"data": {"result": [{"metric": {"pod": "hog-1"}, "values": values}]}}))
    out = get_metric_history.invoke({"namespace": "demo", "metric": "memory_mib", "pod_prefix": "hog", "minutes": 1})
    assert '"pod":"hog-1"' in out and '"max":64.0' in out and '"min":10.0' in out and '"window_minutes":5' in out


def test_metric_history_with_no_data_says_so(monkeypatch):
    monkeypatch.setattr(read.httpx, "get", lambda *a, **k: FakeResponse({"data": {"result": []}}))
    assert get_metric_history.invoke({"namespace": "demo", "metric": "restarts"}).startswith("no data")


def test_a_pod_that_is_gone_falls_back_to_stored_logs(fresh_cluster, monkeypatch):
    from kubernetes import client
    fresh_cluster.core.read_namespaced_pod_log.side_effect = client.ApiException(status=404, reason="Not Found")
    monkeypatch.setattr(read, "_loki_lines", lambda ns, name, *a: f"stored lines for {name}")
    out = read.get_pod_logs.invoke({"namespace": "demo", "name": "crasher"})
    assert out.startswith("pod not found; stored logs from Loki:") and "stored lines for crasher" in out


def test_other_log_errors_are_not_mistaken_for_a_missing_pod(fresh_cluster):
    from kubernetes import client
    fresh_cluster.core.read_namespaced_pod_log.side_effect = client.ApiException(status=400, reason="Bad Request")
    assert read.get_pod_logs.invoke({"namespace": "demo", "name": "p"}) == "could not read logs: Bad Request"


def test_a_service_with_no_matching_pods_gets_a_plain_explanation(fresh_cluster):
    fresh_cluster.core.read_namespaced_service.return_value = NS(
        spec=NS(selector={"app": "shopp"}, type="ClusterIP", ports=[NS(port=80, target_port=80)]))
    fresh_cluster.core.list_namespaced_pod.return_value = items()
    fresh_cluster.core.read_namespaced_endpoints.return_value = NS(subsets=None)
    out = read.get_service.invoke({"namespace": "demo", "name": "shop"})
    assert "matches NO pods" in out and "shopp" in out


# ---- the runbooks ---------------------------------------------------------------------------------------------

RUNBOOKS = pathlib.Path(__file__).resolve().parent.parent / "runbooks"


def test_runbooks_only_name_tools_that_exist():
    names = {t.name for t in READ_TOOLS}
    write = {"label_node", "delete_pod", "restart_deployment", "scale_deployment", "rollback_deployment", "patch_resource", "delete_resource"}
    problems = []
    for path in sorted(RUNBOOKS.glob("*.md")):
        for token in set(re.findall(r"\b(?:get|list|describe|search)_[a-z_]+\b", path.read_text())):
            if token not in names:
                problems.append(f"{path.name}: {token}")
    assert not problems, problems
    assert len(list(RUNBOOKS.glob("*.md"))) >= 6 and write


def test_the_runbook_search_finds_the_right_guide():
    assert "ImagePullBackOff" in read.search_runbooks.invoke({"query": "ImagePullBackOff"})
    assert "Service has no endpoints" in read.search_runbooks.invoke({"query": "service endpoints"})
    assert read.search_runbooks.invoke({"query": "zzzz qqqq"}) == "No matching runbook."


# ---- what the tools show about a container -----------------------------------------------------------------------

def container():
    from kubernetes import client
    return client.V1Container(
        name="web", image="nginx", ports=[client.V1ContainerPort(container_port=80)], command=["sh", "-c"], args=["run"],
        env=[client.V1EnvVar(name="MODE", value="ok"),
             client.V1EnvVar(name="DB_PASSWORD", value="hunter2"),
             client.V1EnvVar(name="FROM_MAP", value_from=client.V1EnvVarSource(
                 config_map_key_ref=client.V1ConfigMapKeySelector(name="app-config", key="MODE"))),
             client.V1EnvVar(name="FROM_SECRET", value_from=client.V1EnvVarSource(
                 secret_key_ref=client.V1SecretKeySelector(name="creds", key="pw")))],
        readiness_probe=client.V1Probe(http_get=client.V1HTTPGetAction(path="/", port=8081), period_seconds=5),
        liveness_probe=client.V1Probe(_exec=client.V1ExecAction(command=["cat", "/tmp/ok"])),
        resources=client.V1ResourceRequirements(limits={"memory": "64Mi"}),
    )


def test_a_container_shows_its_ports_probes_command_and_environment_sources():
    spec = read.container_spec(container())
    assert spec["ports"] == [80] and spec["command"] == ["sh", "-c", "run"]
    assert spec["readiness_probe"]["httpGet"] == {"path": "/", "port": 8081}
    assert spec["liveness_probe"]["exec"] == ["cat", "/tmp/ok"]
    assert "FROM_MAP <- configmap app-config/MODE" in spec["env"] and "FROM_SECRET <- secret creds/pw" in spec["env"]
    assert spec["resources"]["limits"] == {"memory": "64Mi"}


def test_secret_looking_values_are_never_shown():
    spec = read.container_spec(container())
    assert "DB_PASSWORD=(hidden)" in spec["env"] and "MODE=ok" in spec["env"]
    assert "hunter2" not in str(spec)


def test_a_bare_container_has_only_its_name_and_image():
    from kubernetes import client
    assert read.container_spec(client.V1Container(name="a", image="b")) == {"name": "a", "image": "b"}


def test_describe_pod_and_get_deployment_include_the_container_settings(fresh_cluster):
    from kubernetes import client
    c = container()
    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(labels={"app": "x"}), spec=client.V1PodSpec(containers=[c]),
        status=client.V1PodStatus(phase="Running", conditions=[], container_statuses=[]))
    fresh_cluster.core.read_namespaced_pod.return_value = pod
    assert read.container_spec(c)["readiness_probe"]["httpGet"]["port"] == 8081
    assert "8081" in read.describe_pod.invoke({"namespace": "demo", "name": "p"})

    dep = client.V1Deployment(
        metadata=client.V1ObjectMeta(uid="u", annotations={}),
        spec=client.V1DeploymentSpec(
            replicas=1, selector=client.V1LabelSelector(match_labels={"app": "x"}),
            strategy=client.V1DeploymentStrategy(type="RollingUpdate"),
            template=client.V1PodTemplateSpec(spec=client.V1PodSpec(containers=[c]))),
        status=client.V1DeploymentStatus(conditions=[]))
    fresh_cluster.apps.read_namespaced_deployment.return_value = dep
    fresh_cluster.apps.list_namespaced_replica_set.return_value = NS(items=[])
    assert "8081" in read.get_deployment.invoke({"namespace": "demo", "name": "x"})
