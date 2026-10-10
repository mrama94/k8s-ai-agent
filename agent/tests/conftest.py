"""Test setup: the agent modules connect to a cluster when imported (k8s_client), so a stand-in module is installed
first. Tests then set return values on the mocks, and nothing here talks to a real cluster, Ollama or Prometheus."""
import os
import sys
import tempfile
import types
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "agent.db")
os.environ["ALERT_TOKEN"] = "test-token"
os.environ["ALLOWED_NAMESPACES"] = "demo"

_fake = types.ModuleType("k8s_client")
for _name in ("core", "apps", "batch", "networking", "custom"):
    setattr(_fake, _name, MagicMock(name=_name))
sys.modules["k8s_client"] = _fake


@pytest.fixture(autouse=True)
def fresh_cluster():
    """Every test starts with untouched mocks."""
    for name in ("core", "apps", "batch", "networking", "custom"):
        getattr(_fake, name).reset_mock(return_value=True, side_effect=True)
    return _fake


def items(*objs):
    return NS(items=list(objs))


def pod(name="p", namespace="demo", phase="Running", ready=True, owner=True, selector=None, waiting=None,
        terminating=False):
    state = NS(waiting=NS(reason=waiting) if waiting else None)
    return NS(
        metadata=NS(name=name, namespace=namespace, labels={}, owner_references=[NS(kind="ReplicaSet")] if owner else None,
                    deletion_timestamp="now" if terminating else None),
        spec=NS(node_selector=selector, node_name="agent-lab-worker"),
        status=NS(phase=phase, container_statuses=[NS(ready=ready, state=state, restart_count=0)]),
    )
