# Pod stuck in Pending (FailedScheduling)

Symptoms: pod phase Pending, Warning event FailedScheduling.

1. Read the event message. It names the cause: node selector or affinity mismatch, untolerated taint, insufficient cpu or memory, or an unbound volume claim.
2. The control-plane taint node-role.kubernetes.io/control-plane:NoSchedule is normal; ignore it unless the pod must run on the control plane.
3. Selector mismatch: compare the pod nodeSelector with the labels from list_nodes.
4. Taint: compare node taints with the pod tolerations.
5. Resources: use describe_node to compare allocatable with requested, and get_resource_quota for namespace limits.
6. Volume: use list_pvcs and check the claim is Bound.

Possible fixes: label a worker node with the exact key and value of the pod nodeSelector (label_node), scale a deployment, roll back a change that raised requests.
