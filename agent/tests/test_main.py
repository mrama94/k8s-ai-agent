import pytest
from fastapi.testclient import TestClient

import main

client = TestClient(main.app)
AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def started(monkeypatch):
    """Alert runs would start the whole graph; record what would have been started instead."""
    calls = []
    monkeypatch.setattr(main, "_start", lambda thread_id, graph_input, gated=False: calls.append((thread_id, graph_input, gated)))
    return calls


def alert(name="PodPending", namespace="demo", status="firing", summary="Pod is stuck", **labels):
    return {"status": status, "labels": {"alertname": name, "namespace": namespace, **labels}, "annotations": {"summary": summary}}


def post(*alerts, headers=AUTH):
    return client.post("/api/alert", json={"alerts": list(alerts)}, headers=headers)


# ---- the alert webhook ----------------------------------------------------------------------------------------

def test_the_webhook_needs_the_right_token(started):
    assert client.post("/api/alert", json={"alerts": []}).status_code == 401
    assert post(headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert post(headers={"Authorization": "test-token"}).status_code == 401
    assert post(headers={"Authorization": "Bearer test-token"}).status_code == 200


def test_an_alert_from_another_namespace_is_ignored(started):
    body = post(alert(namespace="kube-system", pod="coredns")).json()
    assert body == {"started": [], "ignored": 1} and started == []


def test_a_firing_alert_starts_a_gated_run_with_a_cautious_request(started):
    body = post(alert(pod="web-start", summary='Pod stuck <script>alert(1)</script> "quoted"')).json()
    assert len(body["started"]) == 1 and len(started) == 1
    thread_id, graph_input, gated = started[0]
    assert gated is True
    request = graph_input["request"]
    assert "PodPending" in request and "web-start" in request and "untrusted" in request
    assert "<script>" not in request and "<" not in request.split("untrusted text from the alert rule): ")[1]


def test_a_second_alert_for_the_same_resource_is_deduplicated(started):
    assert len(post(alert(pod="web-dupe")).json()["started"]) == 1
    again = post(alert(name="PodNotReady", pod="web-dupe")).json()
    assert again == {"started": [], "ignored": 1}


def test_an_alert_without_a_resource_or_name_is_ignored(started):
    assert post(alert(), alert(name="")).json()["ignored"] == 2


@pytest.mark.parametrize("label,value", [("statefulset", "store"), ("daemonset", "agentd"), ("job_name", "nightly-1"),
                                         ("cronjob", "nightly"), ("service", "shop-svc"), ("deployment", "billing")])
def test_each_kind_of_resource_label_is_understood(started, label, value):
    assert len(post(alert(name="X", **{label: value})).json()["started"]) == 1
    assert any(r["resource"] == value for r in client.get("/api/alerts").json())


def test_a_pod_label_wins_over_a_deployment_label(started):
    post(alert(pod="web-pick", deployment="other-deploy"))
    resources = [r["resource"] for r in client.get("/api/alerts").json()]
    assert "web-pick" in resources and "other-deploy" not in resources


def test_a_resolved_alert_is_marked_resolved(started):
    post(alert(pod="web-resolve"))
    assert post(alert(pod="web-resolve", status="resolved")).json() == {"started": [], "ignored": 0}
    row = next(r for r in client.get("/api/alerts").json() if r["resource"] == "web-resolve")
    assert row["alert_state"] == "resolved"


def test_the_alert_list_links_to_grafana(started):
    post(alert(pod="web-link"))
    row = next(r for r in client.get("/api/alerts").json() if r["resource"] == "web-link")
    assert "var-pod=web-link" in row["graph_url"] and "var-namespace=demo" in row["graph_url"]


# ---- the chat endpoints ---------------------------------------------------------------------------------------

def test_approving_something_that_is_not_waiting_is_a_conflict():
    assert client.post("/api/approve", json={"thread_id": "nope", "approved": True}).status_code == 409


def test_an_unknown_run_is_not_found():
    assert client.get("/api/status/nope").status_code == 404


def test_the_health_check_answers():
    assert client.get("/healthz").json() == {"ok": True}


# ---- small helpers --------------------------------------------------------------------------------------------

def test_untrusted_text_is_cleaned_and_shortened():
    assert main._clean("Pod <b>stuck</b>; rm -rf /`") == "Pod bstuck/b rm -rf /"  # angle brackets, semicolons, backticks removed
    assert main._clean(None) == "" and len(main._clean("x" * 500)) == 200 and len(main._clean("x" * 500, 10)) == 10


def values(**extra):
    return {"request": "fix it", "diagnosis": {"root_cause": "rc", "confidence": "high"}, "plan": {"actions": []}, **extra}


def test_a_turn_is_summarised_by_its_outcome():
    assert "no change proposed" in main._summarize(values())
    rejected = values(plan={"actions": [{"guard": "allowed"}]})
    assert "rejected by the user" in main._summarize(rejected)
    fixed = values(results=["a"], verification={"fixed": True})
    assert "applied 1 action(s); fixed" in main._summarize(fixed)
    not_fixed = values(results=["a", "b"], verification={"fixed": False})
    assert "applied 2 action(s); not fixed" in main._summarize(not_fixed)


def test_summaries_are_kept_short():
    assert len(main._summarize(values(request="r" * 1000, diagnosis={"root_cause": "c" * 1000, "confidence": "high"}))) <= 700
