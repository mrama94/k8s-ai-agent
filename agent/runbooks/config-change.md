# Config changed but pods still behave the old way

Symptoms: someone changed a ConfigMap or Secret, but running pods still use the old values.

1. Environment variables from a ConfigMap or Secret are read only when a container starts. Running pods never see the change.
2. Confirm the pods started before the change with describe_pod and get_deployment.
3. A rolling restart of the Deployment makes new pods read the current values.
4. Mounted ConfigMap files update on their own after a delay, but the application may still need a restart to reload them.
