import re

import pytest
from langchain_core.messages import ToolMessage

import graph
from conftest import NS, items, pod
from graph import Action, ActionArgs, Plan
from tools import guards
from tools.read import READ_TOOLS
from tools.write import WRITE_TOOLS


def act(tool, **args):
    return Action(tool=tool, args=ActionArgs(**args), reason="because")


def plan(*actions):
    return Plan(summary="s", actions=list(actions))


# ---- filling in what the small model leaves out -------------------------------------------------------------

def test_namespace_defaults_to_the_only_allowed_one():
    p = plan(act("restart_deployment", name="web"))
    assert graph._missing_args(p) == []
    assert p.actions[0].args.namespace == "demo"


def test_label_node_does_not_need_a_namespace():
    p = plan(act("label_node", node="w", key="type", value="web"))
    assert graph._missing_args(p) == []
    assert p.actions[0].args.namespace is None


def test_gaps_are_reported_per_action():
    p = plan(act("patch_resource", kind="Service", patch="{}"), act("scale_deployment", name="c"))
    assert graph._missing_args(p) == ["patch_resource lacks name", "scale_deployment lacks replicas"]


@pytest.fixture
def objects(monkeypatch):
    def install(*names_by_kind):
        monkeypatch.setattr(graph, "_objects", lambda ns: [(k, n, None, True) for k, n in names_by_kind])
    return install


def test_the_only_object_of_a_kind_fills_in_the_name(objects):
    objects(("Service", "shop"), ("Deployment", "shop"))
    p = plan(act("patch_resource", kind="Service", patch="{}"))
    assert graph._missing_args(p, "anything") == []
    assert p.actions[0].args.name == "shop"


def test_with_several_objects_the_one_named_in_the_text_is_chosen(objects):
    objects(("Service", "shop"), ("Service", "billing"))
    p = plan(act("patch_resource", kind="Service", patch="{}"))
    graph._missing_args(p, "requests to the shop service are failing")
    assert p.actions[0].args.name == "shop"


@pytest.mark.parametrize("text", ["nothing relevant", "shop and billing are both broken"])
def test_an_ambiguous_name_is_left_missing(objects, text):
    objects(("Service", "shop"), ("Service", "billing"))
    p = plan(act("patch_resource", kind="Service", patch="{}"))
    assert graph._missing_args(p, text) == ["patch_resource lacks name"]


def test_a_name_inside_a_longer_name_does_not_match(objects):
    objects(("Service", "shop"), ("Service", "shop-2"))
    p = plan(act("patch_resource", kind="Service", patch="{}"))
    graph._missing_args(p, "shop-2 is broken")
    assert p.actions[0].args.name == "shop-2"


# ---- the flow -------------------------------------------------------------------------------------------------

def test_routing_after_approval_and_verification(monkeypatch):
    assert graph.route_after_approval({"approved": True}) == "apply_fix"
    assert graph.route_after_approval({"approved": False}) == graph.END
    assert graph.route_after_approval({}) == graph.END
    monkeypatch.setattr(graph, "MAX_ATTEMPTS", 2)
    assert graph.route_after_verify({"verification": {"fixed": True}, "attempts": 1}) == graph.END
    assert graph.route_after_verify({"verification": {"fixed": False}, "attempts": 1}) == "investigate"
    assert graph.route_after_verify({"verification": {"fixed": False}, "attempts": 2}) == graph.END


def test_no_runnable_action_means_no_approval_is_asked():
    state = {"plan": {"actions": [{"tool": "delete_pod", "guard": "blocked: no controller"}]}, "diagnosis": {}}
    assert graph.human_approval(state) == {"approved": False}
    assert graph.human_approval({"plan": {"actions": []}, "diagnosis": {}}) == {"approved": False}


def test_a_low_confidence_diagnosis_proposes_nothing_and_never_calls_the_model(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the model must not be called")
    monkeypatch.setattr(graph, "_structured", boom)
    out = graph.propose({"diagnosis": {"confidence": "low", "root_cause": "?", "evidence": []}, "request": "r"})
    assert out["plan"]["actions"] == []
    assert "too low" in out["plan"]["summary"]


@pytest.fixture
def proposing(monkeypatch):
    monkeypatch.setattr(graph, "_node_facts", lambda: "nodes")
    monkeypatch.setattr(graph, "_object_facts", lambda: "objects")
    calls = []

    def install(*plans):
        queue = list(plans)

        def fake(schema, system, user):
            calls.append(user)
            return queue.pop(0), {"tokens_in": 1, "tokens_out": 1, "llm_calls": 1, "llm_seconds": 0.5}
        monkeypatch.setattr(graph, "_structured", fake)
        return calls
    return install


STATE = {"diagnosis": {"confidence": "high", "root_cause": "selector typo", "evidence": ["e"]}, "request": "fix shop"}


def test_actions_get_a_guard_verdict_and_a_preview(proposing, monkeypatch):
    proposing(plan(act("patch_resource", kind="Service", namespace="demo", name="shop", patch='{"spec": {}}'),
                   act("delete_pod", namespace="kube-system", name="x")))
    monkeypatch.setattr(graph.patching, "preview", lambda *a: ["spec.selector.app: shopp -> shop"])
    monkeypatch.setattr(graph.guards, "check", lambda a: (_ for _ in ()).throw(guards.GuardError("nope")) if a["tool"] == "delete_pod" else None)
    actions = graph.propose(STATE)["plan"]["actions"]
    assert actions[0]["guard"] == "allowed" and actions[0]["preview"] == ["spec.selector.app: shopp -> shop"]
    assert actions[1]["guard"] == "blocked: nope" and "preview" not in actions[1]


def test_a_patch_the_dry_run_rejects_is_shown_as_blocked(proposing, monkeypatch):
    proposing(plan(act("patch_resource", kind="Service", namespace="demo", name="shop", patch='{"spec": {}}')))
    monkeypatch.setattr(graph.guards, "check", lambda a: None)

    def reject(*a):
        raise graph.patching.PatchError("the API rejected the patch in a dry run: bad port")
    monkeypatch.setattr(graph.patching, "preview", reject)
    action = graph.propose(STATE)["plan"]["actions"][0]
    assert action["guard"].startswith("blocked: the API rejected")


def test_a_plan_is_cut_to_the_maximum_number_of_actions(proposing, monkeypatch):
    proposing(plan(*[act("restart_deployment", namespace="demo", name=f"d{i}") for i in range(7)]))
    monkeypatch.setattr(graph.guards, "check", lambda a: None)
    assert len(graph.propose(STATE)["plan"]["actions"]) == guards.MAX_ACTIONS


def test_an_incomplete_plan_is_requested_once_more(proposing, monkeypatch):
    calls = proposing(plan(act("patch_resource", kind="Service", patch="{}")),
                      plan(act("patch_resource", kind="Service", namespace="demo", name="shop", patch='{"spec": {}}')))
    monkeypatch.setattr(graph.guards, "check", lambda a: None)
    monkeypatch.setattr(graph.patching, "preview", lambda *a: ["x"])
    monkeypatch.setattr(graph, "_objects", lambda ns: [("Service", "a", None, True), ("Service", "b", None, True)])
    out = graph.propose(STATE)
    assert len(calls) == 2 and "incomplete" in calls[1]
    assert out["plan"]["actions"][0]["args"]["name"] == "shop"


def test_a_model_that_returns_nothing_gives_an_empty_plan(proposing):
    proposing(None)
    assert graph.propose(STATE)["plan"]["actions"] == []


def test_apply_runs_only_allowed_actions_and_numbers_the_attempt(monkeypatch):
    ran = []
    monkeypatch.setattr(graph, "execute", lambda a: ran.append(a["tool"]) or f"did {a['tool']}")
    state = {"attempts": 1, "plan": {"actions": [{"tool": "delete_pod", "guard": "allowed"}, {"tool": "label_node", "guard": "blocked: x"}]}}
    out = graph.apply_fix(state)
    assert ran == ["delete_pod"] and out["attempts"] == 2
    assert out["results"] == ["[attempt 2] did delete_pod"]


# ---- verification by plain code -------------------------------------------------------------------------------

def healthy_cluster(fc):
    fc.core.list_namespaced_pod.return_value = items(pod("web-1"))
    fc.apps.list_namespaced_deployment.return_value = items(NS(metadata=NS(name="shop"), spec=NS(replicas=2), status=NS(ready_replicas=2)))
    fc.apps.list_namespaced_stateful_set.return_value = items()
    fc.apps.list_namespaced_daemon_set.return_value = items()
    fc.batch.list_namespaced_job.return_value = items()
    fc.core.list_namespaced_service.return_value = items()
    return fc


def test_a_healthy_namespace_is_healthy(fresh_cluster):
    healthy_cluster(fresh_cluster)
    ok, facts = graph._cluster_health([])
    assert ok is True and "pod demo/web-1: phase=Running ready=True" in facts


@pytest.mark.parametrize("bad_pod", [dict(phase="Pending", ready=False), dict(phase="Running", ready=False), dict(phase="Failed", ready=False)])
def test_a_pod_that_is_not_running_and_ready_is_unhealthy(fresh_cluster, bad_pod):
    healthy_cluster(fresh_cluster).core.list_namespaced_pod.return_value = items(pod("a"), pod("b", **bad_pod))
    assert graph._cluster_health([])[0] is False


def test_a_terminating_pod_is_ignored(fresh_cluster):
    healthy_cluster(fresh_cluster).core.list_namespaced_pod.return_value = items(pod("old", phase="Running", ready=False, terminating=True), pod("new"))
    assert graph._cluster_health([])[0] is True


def test_workloads_must_have_all_their_replicas(fresh_cluster):
    fc = healthy_cluster(fresh_cluster)
    fc.apps.list_namespaced_deployment.return_value = items(NS(metadata=NS(name="shop"), spec=NS(replicas=2), status=NS(ready_replicas=1)))
    assert graph._cluster_health([])[0] is False
    healthy_cluster(fc)
    fc.apps.list_namespaced_stateful_set.return_value = items(NS(metadata=NS(name="db"), spec=NS(replicas=3), status=NS(ready_replicas=None)))
    assert graph._cluster_health([])[0] is False
    healthy_cluster(fc)
    fc.apps.list_namespaced_daemon_set.return_value = items(NS(metadata=NS(name="agentd"), status=NS(desired_number_scheduled=2, number_ready=1)))
    assert graph._cluster_health([])[0] is False


def test_a_scaled_to_zero_deployment_counts_as_matching(fresh_cluster):
    healthy_cluster(fresh_cluster).apps.list_namespaced_deployment.return_value = items(NS(metadata=NS(name="c"), spec=NS(replicas=0), status=NS(ready_replicas=None)))
    assert graph._cluster_health([])[0] is True


def test_a_failed_job_is_unhealthy_but_a_finished_one_is_not(fresh_cluster):
    fc = healthy_cluster(fresh_cluster)
    fc.batch.list_namespaced_job.return_value = items(NS(metadata=NS(name="j"), status=NS(failed=2, succeeded=None, active=None)))
    assert graph._cluster_health([])[0] is False
    fc.batch.list_namespaced_job.return_value = items(NS(metadata=NS(name="j"), status=NS(failed=1, succeeded=1, active=None)))
    assert graph._cluster_health([])[0] is True


def service(selector):
    return NS(metadata=NS(name="shop"), spec=NS(selector=selector))


def test_a_service_needs_ready_endpoints(fresh_cluster):
    fc = healthy_cluster(fresh_cluster)
    fc.core.list_namespaced_service.return_value = items(service({"app": "shop"}))
    fc.core.read_namespaced_endpoints.return_value = NS(subsets=None)
    assert graph._cluster_health([])[0] is False
    fc.core.read_namespaced_endpoints.return_value = NS(subsets=[NS(addresses=[NS()])])
    assert graph._cluster_health([])[0] is True


def test_a_service_without_a_selector_is_skipped(fresh_cluster):
    fc = healthy_cluster(fresh_cluster)
    fc.core.list_namespaced_service.return_value = items(service(None))
    assert graph._cluster_health([])[0] is True
    fc.core.read_namespaced_endpoints.assert_not_called()


def test_a_namespace_that_cannot_be_read_is_unhealthy(fresh_cluster):
    healthy_cluster(fresh_cluster).core.list_namespaced_pod.side_effect = RuntimeError("boom")
    ok, facts = graph._cluster_health([])
    assert ok is False and "could not read" in facts


def test_a_labelled_node_is_part_of_the_facts(fresh_cluster):
    healthy_cluster(fresh_cluster).core.read_node.return_value = NS(metadata=NS(labels={"type": "web"}))
    ok, facts = graph._cluster_health([{"tool": "label_node", "args": {"node": "agent-lab-worker"}}])
    assert ok is True and "node agent-lab-worker labels: {'type': 'web'}" in facts


@pytest.fixture
def verifying(monkeypatch):
    monkeypatch.setattr(graph.time, "sleep", lambda s: None)
    monkeypatch.setattr(graph, "VERIFY_POLL", 0)
    calls = []

    def install(health, timeout=0):
        monkeypatch.setattr(graph, "VERIFY_TIMEOUT", timeout)
        queue = list(health)
        monkeypatch.setattr(graph, "_cluster_health", lambda actions: calls.append(1) or queue.pop(0))
        return calls
    return install


VSTATE = {"attempts": 1, "request": "r", "diagnosis": {"root_cause": "rc"},
          "results": ["[attempt 1] labelled node w"], "plan": {"actions": [{"tool": "label_node", "args": {}, "guard": "allowed"}]}}


def test_verification_reports_fixed_when_the_cluster_is_healthy(verifying):
    verifying([(True, "facts")])
    out = graph.verify_fix(VSTATE)
    assert out["verification"]["fixed"] is True and "outcome fixed" in out["history"][0]


def test_a_failed_action_is_not_fixed_and_is_not_polled(verifying):
    calls = verifying([(True, "facts")], timeout=300)
    out = graph.verify_fix({**VSTATE, "results": ["[attempt 1] FAILED: Kubernetes API 404 Not Found"]})
    assert out["verification"]["fixed"] is False and len(calls) == 1
    assert "An action failed" in out["verification"]["evidence"]


def test_an_unhealthy_cluster_is_polled_until_it_recovers(verifying):
    calls = verifying([(False, "a"), (False, "b"), (True, "c")], timeout=300)
    assert graph.verify_fix(VSTATE)["verification"]["fixed"] is True and len(calls) == 3


def test_verification_gives_up_after_the_timeout(verifying):
    verifying([(False, "still pending")])
    out = graph.verify_fix(VSTATE)
    assert out["verification"]["fixed"] is False and "still pending" in out["verification"]["evidence"]


def test_only_the_latest_attempts_results_are_judged(verifying):
    verifying([(True, "f")])
    out = graph.verify_fix({**VSTATE, "attempts": 2, "results": ["[attempt 1] FAILED: old", "[attempt 2] ok"]})
    assert out["verification"]["fixed"] is True


# ---- housekeeping ---------------------------------------------------------------------------------------------

def test_only_the_most_recent_tool_outputs_stay_full_length():
    messages = [ToolMessage(content="x" * 1000, tool_call_id=str(i)) for i in range(5)]
    graph._trim_old_outputs(messages)
    assert [len(m.content) > 400 for m in messages] == [False, False, True, True, True]
    assert "older output trimmed" in messages[0].content


def test_totals_are_merged():
    a = {"tokens_in": 1, "llm_calls": 1}
    graph._merge(a, {"tokens_in": 4, "llm_calls": 2})
    assert a == {"tokens_in": 5, "llm_calls": 3}


def test_prompts_only_name_tools_that_exist():
    names = {t.name for t in READ_TOOLS}
    mentioned = set(re.findall(r"\b(?:get|list|describe|search)_[a-z_]+\b", graph.INVESTIGATE_PROMPT + graph.CONCLUDE_PROMPT))
    assert mentioned and mentioned <= names, mentioned - names


def test_the_action_lists_agree_everywhere():
    assert set(WRITE_TOOLS) == guards.ALLOWED_ACTIONS == set(graph.REQUIRED_ARGS)
    for tool in guards.ALLOWED_ACTIONS:
        assert tool in graph.PLAN_PROMPT, f"{tool} is allowed but the plan prompt never mentions it"
        assert tool in graph.Action.model_fields["tool"].description


def test_there_are_twelve_read_tools_each_with_a_description():
    assert len(READ_TOOLS) == 12
    assert all(len(t.description) > 20 for t in READ_TOOLS)
    assert len({t.name for t in READ_TOOLS}) == 12


def test_the_plan_shows_the_tidied_patch_that_will_really_run(proposing, monkeypatch):
    messy = '{"metadata": {"name": "shop"}, "spec": {"selector": {"app": "storefront"}}}}'
    proposing(plan(act("patch_resource", kind="Service", namespace="demo", name="shop", patch=messy)))
    monkeypatch.setattr(graph.guards, "check", lambda a: None)
    seen = []
    monkeypatch.setattr(graph.patching, "preview", lambda kind, ns, name, patch: seen.append(patch) or ["spec.selector.app: a -> b"])
    action = graph.propose(STATE)["plan"]["actions"][0]
    assert action["args"]["patch"] == '{"spec": {"selector": {"app": "storefront"}}}'
    assert seen == [{"spec": {"selector": {"app": "storefront"}}}]
    assert action["guard"] == "allowed"


# ---- case memory in the prompts -----------------------------------------------------------------------------

def test_similar_cases_reach_the_investigation_and_the_plan(monkeypatch, proposing):
    briefs = []
    monkeypatch.setattr(graph.outcomes, "case_text", lambda request: "Similar cases: A fix worked.")
    monkeypatch.setattr(graph, "_investigate", lambda system, brief: briefs.append(brief) or ("notes", {
        "tokens_in": 0, "tokens_out": 0, "llm_calls": 0, "llm_seconds": 0.0}))
    monkeypatch.setattr(graph, "_structured", lambda *a: (None, {"tokens_in": 0, "tokens_out": 0, "llm_calls": 0, "llm_seconds": 0.0}))
    monkeypatch.setattr(graph, "_object_facts", lambda: "- Service demo/shop")
    out = graph.investigate({"request": "fix shop"})
    assert "Similar cases: A fix worked." in briefs[0] and out["cases"] == "Similar cases: A fix worked."
    calls = proposing(plan())
    graph.propose({**STATE, "cases": out["cases"]})
    assert "Similar cases: A fix worked." in calls[0]


def test_without_similar_cases_the_prompts_are_unchanged(monkeypatch, proposing):
    briefs = []
    monkeypatch.setattr(graph.outcomes, "case_text", lambda request: "")
    monkeypatch.setattr(graph, "_investigate", lambda system, brief: briefs.append(brief) or ("notes", {
        "tokens_in": 0, "tokens_out": 0, "llm_calls": 0, "llm_seconds": 0.0}))
    monkeypatch.setattr(graph, "_structured", lambda *a: (None, {"tokens_in": 0, "tokens_out": 0, "llm_calls": 0, "llm_seconds": 0.0}))
    monkeypatch.setattr(graph, "_object_facts", lambda: "- Service demo/shop")
    out = graph.investigate({"request": "fix shop"})
    assert briefs[0].startswith("fix shop") and "Similar cases" not in briefs[0] and out["cases"] == ""
    calls = proposing(plan())
    graph.propose(STATE)
    assert "Similar cases" not in calls[0]


def test_the_plan_looks_for_similar_cases_using_the_diagnosis(monkeypatch, proposing):
    asked = []
    monkeypatch.setattr(graph.outcomes, "case_text", lambda text: asked.append(text) or "Similar cases: from the diagnosis.")
    calls = proposing(plan())
    graph.propose({**STATE, "cases": "Similar cases: from the question."})
    assert "selector typo" in asked[0] and "fix shop" in asked[0]
    assert "from the diagnosis" in calls[0] and "from the question" not in calls[0]


def test_the_plan_keeps_the_cases_found_earlier_when_the_diagnosis_finds_none(monkeypatch, proposing):
    monkeypatch.setattr(graph.outcomes, "case_text", lambda text: "")
    calls = proposing(plan())
    graph.propose({**STATE, "cases": "Similar cases: from the question."})
    assert "from the question" in calls[0]


def test_the_investigation_is_given_the_exact_object_names(monkeypatch):
    briefs = []
    monkeypatch.setattr(graph.outcomes, "case_text", lambda request: "")
    monkeypatch.setattr(graph, "_object_facts", lambda: "- CronJob demo/nightly (containers: report)")
    monkeypatch.setattr(graph, "_investigate", lambda system, brief: briefs.append(brief) or ("notes", {
        "tokens_in": 0, "tokens_out": 0, "llm_calls": 0, "llm_seconds": 0.0}))
    monkeypatch.setattr(graph, "_structured", lambda *a: (None, {"tokens_in": 0, "tokens_out": 0, "llm_calls": 0, "llm_seconds": 0.0}))
    graph.investigate({"request": "the nightly cronjob never runs"})
    assert "CronJob demo/nightly" in briefs[0] and "exact names" in briefs[0]


BAD_JSON = Plan(summary="s", actions=[act("patch_resource", kind="Deployment", name="web", patch='{"spec": {')])
GOOD_JSON = Plan(summary="s", actions=[act("patch_resource", kind="Deployment", name="web", patch='{"spec": {"replicas": 2}}')])


def test_a_patch_that_is_not_valid_json_is_sent_back_to_the_model_once(monkeypatch, proposing):
    monkeypatch.setattr(graph.patching, "preview", lambda *a: ["spec.replicas: 1 -> 2"])
    monkeypatch.setattr(graph.guards, "check", lambda a: graph.patching.parse_patch(a["args"]["patch"]) and None)
    calls = proposing(BAD_JSON, GOOD_JSON)
    out = graph.propose(STATE)
    assert len(calls) == 2 and "not valid JSON" in calls[1] and "every brace and bracket closed" in calls[1]
    assert [a["guard"] for a in out["plan"]["actions"]] == ["allowed"]


def test_if_the_second_try_is_still_broken_the_first_verdict_stays(monkeypatch, proposing):
    monkeypatch.setattr(graph.guards, "check", lambda a: graph.patching.parse_patch(a["args"]["patch"]) and None)
    calls = proposing(BAD_JSON, BAD_JSON)
    out = graph.propose(STATE)
    assert len(calls) == 2 and out["plan"]["actions"][0]["guard"].startswith("blocked:")


def test_a_valid_plan_is_never_asked_for_twice(monkeypatch, proposing):
    monkeypatch.setattr(graph.patching, "preview", lambda *a: ["x"])
    monkeypatch.setattr(graph.guards, "check", lambda a: None)
    calls = proposing(GOOD_JSON)
    graph.propose(STATE)
    assert len(calls) == 1


def test_the_plan_prompt_limits_rollback_to_deployments_and_does_not_cap_limits_at_the_observed_value():
    assert "rollback_deployment works only on a Deployment" in graph.PLAN_PROMPT
    assert "headroom" in graph.PLAN_PROMPT
