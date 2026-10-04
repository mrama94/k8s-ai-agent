# ImagePullBackOff and ErrImagePull

Symptoms: container waiting with reason ImagePullBackOff or ErrImagePull.

1. Read the event message: not found means a wrong name or tag, unauthorized means a private registry without credentials.
2. If it began after a change, use get_object (kind Deployment) and compare the images of the current revision and the previous one.
3. A bad tag introduced by the latest revision is fixed by rolling back to the previous revision.
4. Missing registry credentials cannot be fixed by the agent. Report it.
