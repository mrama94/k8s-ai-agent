# Code walkthrough

A guided tour of the repository for someone who knows what the project does and wants to know where things are.
Read it top to bottom once, then use the maps as a reference. Line numbers are approximate; search for the function
name instead.

## 1. The repository in one picture

Only about 1,700 lines are Python. The rest is configuration and documentation.

| Folder or file | What it is | Read it? |
|---|---|---|
| `agent/` | The Python agent. This is the actual program. | Yes, start here |
| `apps/agent/` | Kubernetes manifests that run the agent (Deployment, RBAC, volume, Service) | Later; `rbac.yaml` matters |
| `platform/`, `bootstrap/`, `demo/` | Configuration for Prometheus, Grafana, Loki, Argo CD, and the demo apps | Skim |
| `lab`, `scripts/` | The start/stop command and helper scripts | When you need them |
| `.github/workflows/` | CI checks and the image build | When you need them |
| `*.md`, `docs/` | Documentation | Reference |

## 2. Inside `agent/`

```
agent/
  main.py            web API, background jobs, alert webhook, conversation summaries   (284 lines)
  graph.py           the five-step flow, the prompts, plan checks, verification        (482 lines)
  llm.py             the connection to Ollama                                           (17 lines)
  k8s_client.py      Kubernetes API clients (core, apps, batch)                         (10 lines)
  usage_tracker.py   counts model calls and tokens                                      (15 lines)
  tools/
    read.py          the 12 read-only tools the model may call                          (339 lines)
    runbooks.py      the search_runbooks tool                                           (27 lines)
    guards.py        the safety rules                                                   (76 lines)
    write.py         the 7 actions that change the cluster                              (81 lines)
    patching.py      parse, preview and apply generic object edits                      (155 lines)
  runbooks/          troubleshooting guides in Markdown (read by search_runbooks)
  static/index.html  the chat and alerts page                                           (194 lines)
  tests/             170 tests
```

## 3. The one idea that explains the design

The model is split from the hands.

- **The model** can only read (12 tools) and write a plan as data. It never holds a write tool.
- **Code** checks the plan against the rules (`guards.py`), shows it to a person, applies it only after approval
  (`write.py`), and then checks whether the problem is gone (`verify_fix` in `graph.py`).

Almost every file exists to support that split. When you read a function, ask which side it is on.

## 4. Reading order

Read in this order. It goes from the smallest and most important file outward.

### Step 1: `tools/guards.py` (the safety rules)

One function, `check(action)`, and one rule: deny by default. It raises `GuardError` unless every rule passes.

- `ALLOWED_ACTIONS` lists the seven action names. Anything else is refused.
- `ALLOWED_NAMESPACES` (default `demo`) limits where changes can happen. `label_node` is the one action without a namespace.
- Per-action rules, in order of the `if tool == ...` blocks:
  - `label_node`: only the `type` key, never a control-plane node, and only if a pod in `demo` has a `nodeSelector` asking for that exact label.
  - `rollback_deployment`: an earlier revision must exist.
  - `scale_deployment`: replicas must be an integer from 1 to 5. The `isinstance(replicas, bool)` part is there because `True` counts as `1` in Python.
  - `delete_pod`: the pod must have a controller, or deleting it removes it for good.
  - `patch_resource` / `delete_resource`: the kind must be known, delete is limited to `DELETE_KINDS`, the object must exist, and the patch must parse.

`check` is called twice for every action: when the plan is made, and again just before applying.

### Step 2: `tools/write.py` (the seven actions)

Each action is a few lines that call the Kubernetes API. The important part is at the bottom.

- `WRITE_TOOLS` maps action names to functions.
- `execute(action)` runs one approved action. It calls `guards.check` first, even though approval already happened. A refusal comes back as the text `BLOCKED by guard: ...`; an API error comes back as `FAILED: ...`. These strings are what `verify_fix` later looks for.

### Step 3: `tools/patching.py` (generic object edits)

This file handles `patch_resource` and `delete_resource`, which cover Deployments, StatefulSets, DaemonSets, Jobs, CronJobs, Services and ConfigMaps.

- `KINDS` maps each kind to its API client and method names. Adding a kind starts here.
- `parse_patch(raw, target)` turns the model's JSON string into a checked dict. It is deliberately lenient about two slips a 7B model makes: stray closing braces at the end (`_load`), and repeating `apiVersion`, `kind` and `metadata.name` when they match the target (they change nothing, so they are dropped).
- The checks that follow are strict: size limit, only `spec`, `data`, `binaryData` and label/annotation metadata, and `_walk` refuses any forbidden field anywhere in the patch (`hostNetwork`, `privileged`, `securityContext`, `serviceAccountName`, and so on) and Service types `LoadBalancer`, `NodePort` and `ExternalName`.
- `preview(...)` asks the real API for a dry run (`dry_run="All"`), compares before and after, and returns lines like `spec.selector.app: storefrnt -> storefront`. This is what the person sees before approving.
- `apply(...)` and `delete(...)` do the real change.

### Step 4: `tools/read.py` and `tools/runbooks.py` (what the model can see)

Twelve functions marked `@tool`. Read two or three and skim the rest; they follow one pattern: call the API, keep only the useful fields, return short JSON.

- `_clip` caps output at 4,000 characters and `_j` writes compact JSON. A small model gets lost in long output.
- `get_pod_logs` falls back to Loki for pods that no longer exist. `search_logs` and `get_metric_history` call Loki and Prometheus over HTTP.
- `READ_TOOLS` (bottom of the file) is the list handed to the model. `READ_TOOL_MAP` looks a tool up by name.
- `search_runbooks` scores the Markdown files in `runbooks/` by shared words and returns the best two.

### Step 5: `graph.py` (the agent itself)

Read it in four parts.

**Part A: prompts and data shapes (top of the file).** Three prompts drive three model calls: `INVESTIGATE_PROMPT`, `CONCLUDE_PROMPT` and `PLAN_PROMPT`. Then come the typed shapes the model must return: `Diagnosis`, `Action`, `ActionArgs` and `Plan`. `AgentState` is the data carried from step to step; fields marked `Annotated[..., operator.add]` accumulate instead of being overwritten.

**Part B: model helpers.**
- `_invoke` retries once if an Ollama call fails.
- `_structured(schema, ...)` makes a call that must return the given shape.
- `_investigate` is the agent loop: call the model, run the tools it asks for, repeat up to `MAX_TOOL_ROUNDS` (12). `_trim_old_outputs` shrinks old tool results to save context.

**Part C: the five nodes, in flow order.**

| Node | What it does |
|---|---|
| `investigate` | Runs the loop, then asks the model to turn its notes into a `Diagnosis` (root cause, confidence, evidence). |
| `propose` | If confidence is `low`, returns an empty plan. Otherwise asks the model for a `Plan`, repairs it (below), runs `guards.check` on each action, and for patches adds the dry-run `preview`. Each action gets `guard: allowed` or `guard: blocked: ...`. |
| `human_approval` | Calls `interrupt(...)`, which pauses the run and saves its state. If no action is allowed, it skips the pause. |
| `apply_fix` | Runs `execute` for each allowed action. It runs only after approval. |
| `verify_fix` | Waits and polls the live cluster for up to 90 seconds with `_cluster_health`, and records whether the problem is fixed. The model plays no part in this. |

**The repair helpers in `propose`.** These exist because a 7B model slips:
- `_missing_args` lists required arguments the model left out (using `REQUIRED_ARGS`), and fills in the namespace when only one is allowed.
- `_infer_name` fills in a missing object name when only one object matches.
- `_object_facts` and `_node_facts` put the real object and node names into the prompt so the model copies them instead of guessing.
- If arguments are still missing, `propose` asks the model once more, naming exactly what is missing.

**Part D: wiring.** `build_graph` connects the nodes. The two routing functions decide the loops:
- `route_after_approval`: approved goes to `apply_fix`; otherwise the run ends.
- `route_after_verify`: fixed, or `MAX_ATTEMPTS` (2) reached, ends the run; otherwise it goes back to `investigate` with a note of what failed (`history`).

The graph uses a SQLite checkpointer, which is what lets a run pause for approval and survive a restart.

### Step 6: `main.py` (the web layer)

| Endpoint | What it does |
|---|---|
| `POST /api/chat` | Starts a run in a background thread and returns a `thread_id` at once. |
| `GET /api/status/{id}` | Returns the run's state: `running`, `awaiting_approval`, `done`, `error` or `interrupted`, plus diagnosis, plan and usage. The page polls this every couple of seconds. |
| `POST /api/approve` | Resumes the paused run with the person's decision. |
| `POST /api/alert` | The Alertmanager webhook. Checks the bearer token, cleans untrusted text, applies a 15-minute cooldown per resource, and starts a gated run. |
| `GET /api/alerts` | The alert list for the page. |
| `GET /healthz` | Liveness check. |

Pieces worth knowing:
- `_start` / `_run` run the graph on a background thread and record progress in the `jobs` dict. The graph never blocks an HTTP request.
- `alert_slot` is a semaphore with one slot, so only one alert-driven run uses the model at a time. Others show as queued.
- `_snapshot` builds the status response from the graph's saved state.
- `_remember` and `_context` store one short summary per finished run and feed the last five into the next message. That is the whole conversation memory.
- `_resume_interrupted` restarts alert runs that were cut off by a restart.
- `_clean` strips untrusted alert text down to a safe character set.

### Step 7: the small files

- `llm.py` builds the Ollama client. It sets `temperature=0`, a reply-length cap (`NUM_PREDICT`) and a timeout. The cap and timeout exist because a runaway reply blocks Ollama's single request slot.
- `k8s_client.py` loads the in-cluster Kubernetes config (or your kubeconfig locally) and exposes `core`, `apps` and `batch`.
- `usage_tracker.py` adds up tokens and model time per run.
- `static/index.html` is the whole front end. `post` and `guarded` start a run and poll `/api/status`; `render` draws the diagnosis and plan with the Approve and Reject buttons; `refreshAlerts` polls `/api/alerts` every five seconds. It writes model output as plain text.

## 5. One request, end to end

A chat message about a broken Service:

1. The page posts to `/api/chat` (`main.py`). `_start` creates a thread id and a background thread, and the response returns immediately.
2. The thread runs `graph.stream(...)`. `investigate` loops through read tools (`read.py`) and writes a `Diagnosis`.
3. `propose` asks for a `Plan`, repairs it, and checks it with `guards.check`. For a patch it builds the dry-run `preview` (`patching.py`).
4. `human_approval` pauses. `_snapshot` now reports `awaiting_approval`, and the page shows the plan and preview.
5. The person clicks Approve. The page posts to `/api/approve`, and `_start` resumes the graph with `Command(resume=...)`.
6. `apply_fix` calls `execute` (`write.py`). `execute` runs `guards.check` again, then the real API call.
7. `verify_fix` polls `_cluster_health` until the cluster is healthy or 90 seconds pass, and the page shows the result.

## 6. Tests

`agent/tests/` mirrors the code. A fake `k8s_client` in `conftest.py` means no cluster is needed.

| Test file | Covers |
|---|---|
| `test_guards.py` | Every guard rule, including the `replicas: True` case |
| `test_patching.py` | Patch parsing, forbidden fields, preview |
| `test_graph.py` | Plan repair, name inference, verification, routing |
| `test_main.py` | Endpoints, alert token and cooldown, summaries |
| `test_read_tools.py` | Tool output shape and truncation |
| `test_rbac.py` | Pins the agent's RBAC to exactly the permissions it needs |

Run them from the repo root with `cd agent && python -m pytest`. The tests are a good second way to read the code: each one states a rule in a single sentence.

## 7. Outside `agent/`

- `apps/agent/rbac.yaml` is the hard limit. Even if the code were wrong, the cluster refuses anything not listed here.
- `apps/agent/deployment.yaml` runs the agent (environment variables such as `ALLOWED_NAMESPACES` are set there).
- `platform/apps/*.yaml` are Argo CD Applications, one per platform component.
- `demo/base/` is the healthy `storefront`; `demo/scenarios/` holds 13 broken workloads for testing.
- `lab` is the single command for start, stop and destroy. `bootstrap/bootstrap.sh` does the real work for `lab up`.
- `.github/workflows/ci.yml` runs the checks; `agent-image.yml` builds the image and commits the new tag.

## 8. Where to change things

| You want to... | Start in |
|---|---|
| Allow a new kind of object | `patching.py` (`KINDS`), then `apps/agent/rbac.yaml`, then `test_rbac.py` |
| Add a new safety rule | `guards.py` and `test_guards.py` |
| Add a read tool | `read.py` (add to `READ_TOOLS`), mention it in a runbook, `test_read_tools.py` |
| Add a new action | `write.py` (`WRITE_TOOLS`), `guards.py`, `PLAN_PROMPT` and `REQUIRED_ARGS` in `graph.py`, and RBAC |
| Change what the model is told | the three prompts at the top of `graph.py` |
| Change how a run is judged fixed | `_cluster_health` in `graph.py` |
| Add an alert rule | `platform/prometheus/values.yaml` |
| Change the page | `agent/static/index.html` |
