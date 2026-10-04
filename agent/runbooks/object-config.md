# A workload, ConfigMap, Service or CronJob has a wrong field

Symptoms: pods fail because of a wrong image, port, probe, environment value or resource limit; a CronJob never runs;
a ConfigMap holds a wrong value.

1. Read the object with get_object (kind Deployment, StatefulSet, DaemonSet, Job, CronJob, Service or ConfigMap) and compare
   the field with what the events, logs and the pod state say it should be.
2. OOMKilled: the memory limit is below what the process uses. Check usage with get_metric_history (memory_mib, memory_limit_mib)
   and raise the limit with patch_resource on the owning workload.
3. Probe failures (Unhealthy events): compare the probe path and port with the port the container listens on.
4. ImagePullBackOff after a change: roll back; with no earlier revision, patch the image to the correct tag if the logs or events name it.
5. A ConfigMap value is wrong: patch_resource on the ConfigMap, then restart_deployment so the pods read it.
6. A CronJob is suspended or has a wrong schedule: patch_resource {"spec": {"suspend": false}} or the schedule.
