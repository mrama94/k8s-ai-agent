# Service has no endpoints

Symptoms: requests to a Service fail or time out, endpoints_ready is 0.

1. Run get_service and compare the selector with pods_matching_selector.
2. No matching pods usually means a label typo in the selector or the pods. Compare with the labels from list_pods.
3. Matching pods that are not ready point to a failing readiness probe. Use describe_pod and get_events.
4. Check that the Service target_port equals the containerPort the app listens on.
5. For traffic through an Ingress, use list_ingresses and confirm the backend Service name and port.
