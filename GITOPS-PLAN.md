# GitOps plan

Goal: Git is the single source of truth. Rebuild the cluster with one command, change a value in Git and watch the
cluster follow, and have manual edits to the platform reverted automatically.

## Status

| Phase | State |
|---|---|
| 0 Prepare the repo | Done |
| 1 Argo CD and the platform | Done (2026-10-10): Argo CD installed; Loki, Alloy, Grafana (and its dashboard) and Prometheus adopted with no pod restarts; Helm releases removed; self-heal verified |
| 2 Secrets | Done (2026-10-10): Sealed Secrets controller via Argo CD; the webhook token and Grafana password are encrypted files in git; recovery tooling and key backup in place |
| 3 Agent image and CI | Done (2026-10-10): GitHub Actions builds an arm64 image to a private ghcr.io package and commits the tag; the cluster pulls it with a sealed pull secret; Argo CD deploys apps/agent; dependencies pinned |
| 4 CI checks | Built and passing locally (2026-10-10): 152 unit tests, lint, plain-secret scan, manifest and Helm validation, image build check, tests gate the image build. First run on GitHub pending |
| 5 Demo namespace | Done (2026-10-10): `demo` Argo CD app with manual sync and a healthy `storefront`; drift detected in 3 s and never reverted (tested for ~5 min and ~3 min); agent fixes bring the app back to Synced or stay visible as drift until a person syncs or updates git |

## Decisions

| Question | Choice |
|---|---|
| GitOps tool | **Argo CD**, slimmed (no Dex, no notifications), UI through a port-forward |
| Secrets | **Sealed Secrets** (encrypted files in Git, decrypted in the cluster) |
| Agent image | Built by **GitHub Actions**, pushed to **GitHub Container Registry** (`ghcr.io`) |
| Scope | **Phases 0 to 5.** The agent opening pull requests (phase 6) is left for later. |
| Repo | `github.com/mrama94/k8s-ai-agent` (private, branch `main`) |

## The design problem: the agent edits live objects

The agent fixes things by patching live objects. A GitOps tool treats that as drift and, with auto-heal on, reverts it.
So `demo` is handled differently from everything else:

| Namespace | Argo CD behaviour |
|---|---|
| `ai-agent`, `monitoring` (the platform) | auto-sync, **auto-heal on**, prune on |
| `demo` (what the agent fixes) | auto-sync **off**, auto-heal **off**. Drift shows as `OutOfSync` and nothing is reverted. |

## Target repo layout

```
bootstrap/        kind config, Argo CD install, root app, bootstrap.sh
apps/
  agent/          Kustomize base for the agent (Deployment, RBAC, Service, volume)
platform/         Argo CD Applications for Prometheus, Alertmanager, Grafana, Loki, Alloy, Sealed Secrets
                  (each points at a chart version and a values file)
demo/             the test workloads (manual sync)
agent/            application source code (unchanged)
.github/workflows/  CI: validate, test, build and push the image, bump the tag
```

## Phases

### Phase 0: prepare the repo
- Add `.gitignore`; confirm no secret is committed (none found).
- Restructure into the layout above, keeping history readable (move, do not rewrite).
- Write `bootstrap/bootstrap.sh`: create the kind cluster, install Argo CD, apply the root app.
- **Done when:** `kustomize build` and `helm template` render everything from the new paths.

### Phase 1: Argo CD and the platform
**How the migration works (zero downtime):** each Application renders the same chart and values that Helm used, with
`releaseName` set to the existing release name, so Argo CD adopts the running objects instead of recreating them. Once an
app is Synced and Healthy, the old Helm release record (a Secret) is removed with the objects left in place.
**Repo access:** the repo is private, so Argo CD reads it with a read-only SSH deploy key. The private key lives only in
`~/.config/k8s-agent-lab/argocd-deploy-key` and a cluster Secret. The public key is added to the repo as a deploy key
(read-only) by the repo owner.
- Install Argo CD (pinned version), slimmed; reach the UI on a port-forward.
- Add an Argo CD project that limits which repo and namespaces each app may use.
- Move one component at a time from hand-run Helm to an Argo CD Application: Loki, Alloy, Grafana, then Prometheus.
  For each: uninstall the Helm release, let Argo CD create it, check it is Healthy and Synced.
- **Done when:** `helm list -A` shows nothing in `monitoring`, and all platform apps are Synced and Healthy.
- **Checkpoint:** check Docker and Mac memory before continuing.

### Phase 2: secrets
**Done.** Notes: the chart now lives at `bitnami.github.io/sealed-secrets` (the project moved from `bitnami-labs`). The three
secrets were sealed from their live values, so nothing changed for the running system; the controller adopted the existing
Secrets (annotation `sealedsecrets.bitnami.com/managed`). Deleting a Secret now brings it back from git within a second.
**The sealing key is the one thing git cannot hold.** It is backed up to `~/.config/k8s-agent-lab/sealed-secrets-key.yaml`
(owner-only, outside the repo); `bootstrap.sh` restores it before the controller starts. If it is lost, run
`bootstrap/sealed-secrets/regenerate.sh` (new random values), commit the new sealed files and re-run bootstrap.
The Argo CD repo credential (deploy key) stays out of band on purpose: Argo CD needs it to read git at all.
- Install Sealed Secrets through Argo CD.
- Replace the secrets that were generated by a script (webhook token, Grafana admin password) with sealed files in Git.
- **Done when:** a rebuilt cluster has working secrets with nothing secret in Git.

### Phase 3: the agent and its image
- GitHub Actions: build the agent image, push to `ghcr.io/mrama94/k8s-agent`, commit the new tag into `apps/agent`.
- Switch the Deployment from `k8s-agent:local` to the registry image. The cluster needs to be able to pull it (public
  package, or an image pull secret as a sealed secret).
- Argo CD manages `apps/agent` with auto-heal on.
- **Done when:** a code change pushed to `main` ends up running in the cluster with no `kind load`.

### Phase 4: checks in CI
- On each pull request: YAML lint, `kustomize build`, `helm template`, `kubeconform`, and unit tests for the guards and
  tool input checks.
- **Done when:** a broken manifest fails the pull request.

### Phase 5: the demo namespace
- Add `demo/` as an Argo CD Application with auto-sync and auto-heal off.
- **Done when:** after the agent patches a Service, Argo CD shows that object `OutOfSync`, and nothing is reverted.

### Phase 6 (later, optional): the agent opens pull requests
- Fixes become pull requests on `demo/`; Argo CD applies them after merge. Needs a restricted Git token and a new guard,
  so it gets its own design discussion first.

## How we will know it works

1. Delete the cluster, run `./bootstrap/bootstrap.sh`: everything returns from Git (sealed secrets are decrypted by the controller).
2. Change a Grafana value in Git: the cluster follows within a few minutes with no `helm` command.
3. `kubectl edit` a platform object: Argo CD reverts it.
4. Push an agent code change: CI builds the image, the tag changes in Git, the agent rolls out.
5. Let the agent fix a `demo` problem: Argo CD shows `OutOfSync` and leaves it alone.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Memory: Argo CD plus Sealed Secrets add about 450 to 500 MB on a machine already near its limit | Slim install; memory check after phase 1; Flux is the fallback |
| Short monitoring gaps while each component moves from Helm to Argo CD | Move one at a time; their history is not stored anyway |
| The agent and Argo CD disagree about `demo` | `demo` has auto-heal off (see above) |
| Secrets in a repo | Sealed Secrets only; never commit plain values; revisit if the repo ever becomes public |
| A rebuild needs the internet for charts and images | Pin versions; accept for a lab |

## Things that need your explicit OK when we reach them

- Pushing commits to `origin` (the GitHub repository).
- Adding the GitHub Actions workflow, and any permissions it needs to write to the repo and to `ghcr.io`.
- Making the container package public, or creating an image pull secret.
- Uninstalling each hand-run Helm release during the platform migration.
