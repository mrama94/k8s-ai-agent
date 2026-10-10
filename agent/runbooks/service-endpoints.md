# Service has no endpoints

Symptoms: requests to a Service fail or time out, endpoints_ready is 0.

1. Run get_service and compare the selector with pods_matching_selector.
2. No matching pods usually means a label typo in the selector or the pods. Compare with the labels from list_pods.
3. Matching pods that are not ready point to a failing readiness probe. Use describe_pod and get_events.
4. Check that the Service target_port equals the containerPort the app listens on.
5. For traffic through an Ingress, say so in the diagnosis: the agent cannot inspect Ingresses, so the backend Service name and port have to be checked by a person.
6. A wrong selector or port is fixed with patch_resource on the Service, for example
   {"spec": {"selector": {"app": "<label the pods really have>"}}}. Use get_object (kind Service) to read the current selector and ports.
