from kubernetes import client, config

try:
    config.load_incluster_config()
except config.ConfigException:
    config.load_kube_config()

core = client.CoreV1Api()
apps = client.AppsV1Api()
networking = client.NetworkingV1Api()
batch = client.BatchV1Api()
custom = client.CustomObjectsApi()
