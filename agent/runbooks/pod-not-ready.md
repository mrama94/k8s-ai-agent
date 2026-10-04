# One replica not ready while the others are healthy

Symptoms: a Deployment has fewer ready pods than desired, and the unready pod fails its readiness probe while its siblings are fine.

1. Run get_object (kind Deployment) to confirm only some replicas are unready, then list_pods to find which pod.
2. Run describe_pod and get_events on that pod: read the probe failure message and check the restart count.
3. If the container keeps running and the fault is local to this one pod, delete the pod. The controller creates a fresh replacement.
4. If every replica fails the same way, the cause is shared. Look at the rollout, the configuration and the dependencies instead.
