import json

import pytest
from kubernetes import client

from tools import patching


def test_valid_patch_is_parsed():
    assert patching.parse_patch('{"spec": {"selector": {"app": "shop"}}}') == {"spec": {"selector": {"app": "shop"}}}
    assert patching.parse_patch({"data": {"MODE": "ok"}}) == {"data": {"MODE": "ok"}}


@pytest.mark.parametrize("raw", ["{oops", "", "[]", "{}", "null", "42"])
def test_malformed_or_empty_patch_is_refused(raw):
    with pytest.raises(patching.PatchError):
        patching.parse_patch(raw)


def test_oversized_patch_is_refused():
    big = {"data": {"k": "x" * (patching.MAX_PATCH_CHARS + 1)}}
    with pytest.raises(patching.PatchError, match="larger"):
        patching.parse_patch(big)


@pytest.mark.parametrize("patch", [{"status": {"x": 1}}, {"kind": "Deployment"}, {"spec": {}, "rules": []}])
def test_only_spec_data_and_metadata_may_change(patch):
    with pytest.raises(patching.PatchError, match="may only change"):
        patching.parse_patch(patch)


def test_metadata_is_limited_to_labels_and_annotations():
    assert patching.parse_patch({"metadata": {"labels": {"a": "b"}}})
    for bad in ({"metadata": {"name": "x"}}, {"metadata": {"finalizers": []}}, {"metadata": {"ownerReferences": []}}):
        with pytest.raises(patching.PatchError, match="labels and annotations"):
            patching.parse_patch(bad)


@pytest.mark.parametrize("field", sorted(patching.FORBIDDEN_KEYS))
def test_every_forbidden_field_is_refused_even_when_deeply_nested(field):
    patch = {"spec": {"template": {"spec": {"containers": [{"name": "c", field: True}]}}}}
    with pytest.raises(patching.PatchError, match=field):
        patching.parse_patch(patch)


@pytest.mark.parametrize("kind", ["LoadBalancer", "NodePort", "ExternalName"])
def test_exposing_a_service_is_refused(kind):
    with pytest.raises(patching.PatchError, match="not allowed"):
        patching.parse_patch({"spec": {"type": kind}})


def test_clusterip_service_type_is_fine():
    assert patching.parse_patch({"spec": {"type": "ClusterIP"}})


class FakeApi:
    """Stands in for a Kubernetes API client: one object before, another after the dry run."""

    def __init__(self, before, after=None, error=None):
        self.before, self.after, self.error, self.calls = before, after, error, []

    def read(self, name, namespace):
        return self.before

    def patch(self, name, namespace, body, **kwargs):
        self.calls.append((body, kwargs))
        if self.error:
            raise self.error
        return self.after

    def delete(self, name, namespace):
        self.calls.append(("delete", name))


@pytest.fixture
def svc_api(monkeypatch):
    def install(api):
        monkeypatch.setitem(patching.KINDS, "Service", (api, "read", "patch", "delete"))
        return api
    return install


def test_preview_lists_exactly_the_changed_fields(svc_api):
    before = {"metadata": {"resourceVersion": "1", "name": "shop"}, "spec": {"selector": {"app": "shopp"}, "ports": [{"name": "http", "port": 80}]}}
    after = {"metadata": {"resourceVersion": "2", "name": "shop"}, "spec": {"selector": {"app": "shop"}, "ports": [{"name": "http", "port": 80}]}}
    api = svc_api(FakeApi(before, after))
    changes = patching.preview("Service", "demo", "shop", {"spec": {"selector": {"app": "shop"}}})
    assert changes == ["spec.selector.app: shopp -> shop"]
    body, kwargs = api.calls[0]
    assert kwargs["dry_run"] == "All" and kwargs["_content_type"] == patching.PATCH_TYPE


def test_preview_names_list_items_by_their_name(svc_api):
    before = {"spec": {"containers": [{"name": "web", "image": "nginx:old"}]}}
    after = {"spec": {"containers": [{"name": "web", "image": "nginx:new"}]}}
    svc_api(FakeApi(before, after))
    assert patching.preview("Service", "demo", "x", {"spec": {}}) == ["spec.containers[web].image: nginx:old -> nginx:new"]


def test_preview_refuses_a_patch_that_changes_nothing(svc_api):
    same = {"spec": {"selector": {"app": "shop"}}}
    svc_api(FakeApi(same, dict(same)))
    with pytest.raises(patching.PatchError, match="changes nothing"):
        patching.preview("Service", "demo", "shop", {"spec": {"selector": {"app": "shop"}}})


def test_preview_reports_the_api_rejection_message(svc_api):
    err = client.ApiException(status=422, reason="Unprocessable Entity")
    err.body = json.dumps({"message": "spec.ports: Invalid value"})
    svc_api(FakeApi({"spec": {}}, error=err))
    with pytest.raises(patching.PatchError, match="rejected the patch in a dry run: spec.ports: Invalid value"):
        patching.preview("Service", "demo", "shop", {"spec": {"ports": []}})


def test_apply_sends_a_real_patch_and_delete_deletes(svc_api):
    api = svc_api(FakeApi({"spec": {}}, {"spec": {}}))
    assert patching.apply("Service", "demo", "shop", {"spec": {"x": 1}}) == "patched Service demo/shop"
    assert "dry_run" not in api.calls[0][1]
    assert patching.delete("Service", "demo", "shop") == "deleted Service demo/shop"


def test_workloads_cannot_be_deleted_but_small_objects_can():
    assert patching.DELETE_KINDS == {"Job", "CronJob", "Service", "ConfigMap"}
    assert {"Deployment", "StatefulSet", "DaemonSet"}.isdisjoint(patching.DELETE_KINDS)


# ---- slips a small model makes, and the limits of forgiving them ----------------------------------------------------

TARGET = ("Service", "demo", "shop")


@pytest.mark.parametrize("raw", [
    '{"spec": {"selector": {"app": "shop"}}}}',      # one stray closing brace: the real slip seen in a live run
    '{"spec": {"selector": {"app": "shop"}}}}}',
    '{"spec": {"selector": {"app": "shop"}}} ]\n',
])
def test_stray_closing_braces_after_a_complete_object_are_forgiven(raw):
    assert patching.parse_patch(raw) == {"spec": {"selector": {"app": "shop"}}}


@pytest.mark.parametrize("raw", [
    '{"spec": {"selector": {"app": "shop"}}} and then some words',
    '{"spec": {"a": 1}} {"spec": {"b": 2}}',
    '{"spec": {"selector": {"app": "shop"}',          # missing braces are not guessed at
    '{"spec": 1}}x',
])
def test_any_other_trailing_text_is_still_refused(raw):
    with pytest.raises(patching.PatchError, match="not valid JSON"):
        patching.parse_patch(raw)


def test_a_manifest_style_patch_loses_only_its_own_identity_fields():
    raw = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "shop", "namespace": "demo"}, "spec": {"x": 1}}
    assert patching.parse_patch(raw, TARGET) == {"spec": {"x": 1}}
    kept = {"metadata": {"name": "shop", "labels": {"tier": "web"}}, "spec": {"x": 1}}
    assert patching.parse_patch(kept, TARGET) == {"metadata": {"labels": {"tier": "web"}}, "spec": {"x": 1}}


def test_identity_fields_are_refused_without_a_target_or_when_they_point_elsewhere():
    raw = {"kind": "Service", "metadata": {"name": "shop"}, "spec": {"x": 1}}
    with pytest.raises(patching.PatchError, match="may only change"):
        patching.parse_patch(raw)
    with pytest.raises(patching.PatchError, match="labels and annotations"):
        patching.parse_patch({"metadata": {"name": "other"}, "spec": {"x": 1}}, TARGET)
    with pytest.raises(patching.PatchError, match="labels and annotations"):
        patching.parse_patch({"metadata": {"namespace": "kube-system"}, "spec": {"x": 1}}, TARGET)
    with pytest.raises(patching.PatchError, match="may only change"):
        patching.parse_patch({"kind": "Deployment", "spec": {"x": 1}}, TARGET)


def test_a_patch_that_is_only_identity_changes_nothing():
    with pytest.raises(patching.PatchError, match="changes nothing"):
        patching.parse_patch({"kind": "Service", "metadata": {"name": "shop"}}, TARGET)


def test_the_target_never_lets_a_forbidden_field_through():
    with pytest.raises(patching.PatchError, match="hostNetwork"):
        patching.parse_patch({"kind": "Service", "spec": {"hostNetwork": True}}, TARGET)
