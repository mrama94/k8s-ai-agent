# CrashLoopBackOff and restarts

Symptoms: container waiting with reason CrashLoopBackOff, restart count rising.

1. Run describe_pod and read last_state: OOMKilled means the memory limit is too low, exit code 1 means the app failed, exit code 137 means it was killed.
2. Run get_previous_logs for the crashed instance. Current logs are often empty.
3. For OOMKilled, compare get_metrics usage with the container memory limit.
4. For failing probes, look for Unhealthy events and check the probe path and port.
5. If crashes began after a rollout, compare revisions with get_deployment and consider a rollback.
