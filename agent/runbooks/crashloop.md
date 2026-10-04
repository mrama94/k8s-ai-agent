# CrashLoopBackOff and restarts

Symptoms: container waiting with reason CrashLoopBackOff, restart count rising.

1. Run describe_pod and read last_state: OOMKilled means the memory limit is too low, exit code 1 means the app failed, exit code 137 means it was killed.
2. Run search_logs for the pod (try text=error, then without text) to read what the crashed instance printed, even after it restarted. Current logs are often empty.
3. For OOMKilled, use get_metric_history with memory_mib and memory_limit_mib to compare usage with the limit, and with oom_killed or restarts to see whether it is still happening.
4. For failing probes, look for Unhealthy events and check the probe path and port.
5. If crashes began after a rollout, compare revisions with get_object (kind Deployment) and consider a rollback.
