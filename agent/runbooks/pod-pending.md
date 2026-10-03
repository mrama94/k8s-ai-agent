# Pod stuck in Pending (FailedScheduling)

Symptoms: pod phase Pending, Warning event FailedScheduling.

1. Read the event message. It names the cause: node selector or affinity mismatch, untolerated taint, insufficient cpu or memory, or an unbound volume claim.
2. Selector mismatch: compare the pod nodeSelector with the labels from list_nodes.
3. Taint: compare node taints with the pod tolerations.
4. Resources: use describe_node to compare allocatable with requested, and get_resource_quota for namespace limits.
5. Volume: use list_pvcs and check the claim is Bound.

Possible fixes: label a node, scale a deployment, roll back a change that raised requests.
