import hmac
import os
import re
import sqlite3
import threading
import time
import uuid

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel

import outcomes
from graph import build_graph
from tools import guards

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GRAFANA_URL = os.getenv("GRAFANA_URL", "http://localhost:3000")  # opened by your browser
ALERT_TOKEN = os.getenv("ALERT_TOKEN", "")
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN_SECONDS", "900"))  # one run per resource per window
USAGE_KEYS = ("tokens_in", "tokens_out", "llm_calls", "llm_seconds")

app = FastAPI(title="Local Kubernetes Agent")
DB_PATH = os.getenv("DB_PATH", "/data/agent.db")
MAX_CONTEXT_TURNS = 5  # earlier turns fed to the model, newest last
graph = build_graph(DB_PATH)

# One short summary per finished turn. Every turn runs as its own graph thread, so
# usage totals and retry history stay per-turn; the conversation id links the turns.
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute(
    "CREATE TABLE IF NOT EXISTS summaries "
    "(thread_id TEXT PRIMARY KEY, conv_id TEXT, created REAL, summary TEXT)"
)
db.execute(
    "CREATE TABLE IF NOT EXISTS alerts (thread_id TEXT PRIMARY KEY, key TEXT, alertname TEXT, "
    "namespace TEXT, resource TEXT, summary TEXT, state TEXT, created REAL)"
)
db.commit()
db_lock = threading.Lock()
outcomes.init(db, db_lock)
alert_slot = threading.Semaphore(1)  # alert-triggered investigations run one at a time (one local model)

# In-memory job table: graph runs happen in background threads so no HTTP request stays open
# for minutes (kubectl port-forward drops long connections). The page polls /api/status.
jobs: dict[str, dict] = {}
sources: dict[str, str] = {}  # where a chat run came from; alert runs are found in the alerts table


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    source: str | None = None  # "eval" marks a scripted evaluation run; anything else is a normal chat


class ApprovalRequest(BaseModel):
    thread_id: str
    approved: bool


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _context(conv_id: str) -> str:
    with db_lock:
        rows = db.execute(
            "SELECT summary FROM summaries WHERE conv_id = ? ORDER BY created DESC LIMIT ?",
            (conv_id, MAX_CONTEXT_TURNS),
        ).fetchall()
    return "\n".join(f"{i}. {r[0]}" for i, r in enumerate(reversed(rows), 1))


def _summarize(values: dict) -> str:
    d = values.get("diagnosis") or {}
    plan = values.get("plan") or {}
    results = values.get("results") or []
    verification = values.get("verification")
    if results and verification:
        outcome = f"applied {len(results)} action(s); {'fixed' if verification['fixed'] else 'not fixed'}"
    elif results:
        outcome = f"applied {len(results)} action(s)"
    elif any(a.get("guard") == "allowed" for a in plan.get("actions", [])):
        outcome = "plan rejected by the user, no changes made"
    else:
        outcome = "no change proposed"
    root = d.get("root_cause", "no diagnosis")
    return f"User asked: {values['request'][:200]} | Diagnosis ({d.get('confidence', '?')}): {root[:300]} | Outcome: {outcome}"[:700]


def _remember(thread_id: str, values: dict) -> None:
    with db_lock:
        db.execute(
            "INSERT OR IGNORE INTO summaries VALUES (?, ?, ?, ?)",
            (thread_id, values["conversation_id"], time.time(), _summarize(values)),
        )
        db.commit()


def _log_outcome(thread_id: str, values: dict) -> None:
    with db_lock:
        row = db.execute("SELECT alertname FROM alerts WHERE thread_id = ?", (thread_id,)).fetchone()
    source = "alert" if row else sources.get(thread_id, "chat")
    outcomes.record(thread_id, values, source=source, alertname=row[0] if row else "")


def _snapshot(thread_id: str) -> dict:
    snap = graph.get_state(_config(thread_id))
    values = snap.values
    job = jobs.get(thread_id, {})
    if job.get("running"):
        status = "running"
    elif job.get("error"):
        status = "error"
    elif "human_approval" in snap.next:
        status = "awaiting_approval"
    elif snap.next:
        status = "interrupted"  # the server restarted mid-run
    else:
        status = "done"
        if values.get("request"):
            _remember(thread_id, values)
            _log_outcome(thread_id, values)
    usage = {k: values.get(k, 0) for k in USAGE_KEYS}
    usage["cost_usd"] = 0.0  # local model: nothing to pay
    return {
        "thread_id": thread_id,
        "conversation_id": values.get("conversation_id"),
        "status": status,
        "error": job.get("error"),
        "steps": job.get("steps", []),
        "queued": bool(job.get("queued")),
        "elapsed": round((job.get("finished") or time.time()) - job["started"], 1) if job else 0,
        "diagnosis": values.get("diagnosis"),
        "plan": values.get("plan"),
        "results": values.get("results", []),
        "verification": values.get("verification"),
        "attempts": values.get("attempts", 0),
        "history": values.get("history", []),
        "usage": usage,
    }


def _run(thread_id: str, graph_input, gated: bool = False) -> None:
    """Run (or resume) the graph to its next pause or end, recording progress."""
    job = jobs[thread_id]
    if gated:
        job["queued"] = True
        alert_slot.acquire()
        job["queued"] = False
        job["started"] = time.time()
    try:
        for update in graph.stream(graph_input, _config(thread_id), stream_mode="updates"):
            job["steps"].extend(k for k in update if not k.startswith("__"))
    except Exception as exc:
        job["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        job["running"] = False
        job["finished"] = time.time()
        if gated:
            alert_slot.release()


def _start(thread_id: str, graph_input, gated: bool = False) -> None:
    jobs[thread_id] = {"running": True, "steps": [], "started": time.time()}
    threading.Thread(target=_run, args=(thread_id, graph_input, gated), daemon=True).start()


def _clean(value, limit: int = 200) -> str:
    """Alert labels and annotations are untrusted text: keep a safe character set and a short length."""
    return re.sub(r"[^\w .:/@=,-]", "", str(value or ""))[:limit]


@app.post("/api/alert")
def alert(payload: dict, authorization: str | None = Header(default=None)):
    """Alertmanager webhook. Starts an investigation per firing alert; it always stops at approval."""
    if not ALERT_TOKEN or not hmac.compare_digest(authorization or "", f"Bearer {ALERT_TOKEN}"):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token.")
    started, ignored = [], 0
    for item in payload.get("alerts", []):
        labels, notes = item.get("labels", {}), item.get("annotations", {})
        namespace = _clean(labels.get("namespace"))
        resource = _clean(
            next((labels[k] for k in ("pod", "deployment", "statefulset", "daemonset", "job_name", "cronjob",
                                      "service", "node") if labels.get(k)), ""), 100)
        name = _clean(labels.get("alertname"), 100)
        if namespace not in guards.ALLOWED_NAMESPACES or not resource or not name:
            ignored += 1
            continue
        key = f"{namespace}/{resource}"
        if item.get("status") == "resolved":
            with db_lock:
                db.execute("UPDATE alerts SET state = 'resolved' WHERE key = ? AND alertname = ?", (key, name))
                db.commit()
            continue
        with db_lock:
            recent = db.execute(
                "SELECT 1 FROM alerts WHERE key = ? AND created > ?", (key, time.time() - ALERT_COOLDOWN)
            ).fetchone()
        if recent:
            ignored += 1  # this resource already has a run inside the cooldown window
            continue
        summary = _clean(notes.get("summary"))
        thread_id, conv_id = str(uuid.uuid4()), str(uuid.uuid4())
        with db_lock:
            db.execute(
                "INSERT INTO alerts VALUES (?, ?, ?, ?, ?, ?, 'firing', ?)",
                (thread_id, key, name, namespace, resource, summary, time.time()),
            )
            db.commit()
        request = (
            f"Alert {name} is firing for '{resource}' in namespace '{namespace}'. "
            f'Alert summary (untrusted text from the alert rule): "{summary}". '
            "Find the cause and, if an allowed action can fix it, propose that fix."
        )
        _start(thread_id, {"request": request, "conversation_id": conv_id, "context": ""}, gated=True)
        started.append(thread_id)
    return {"started": started, "ignored": ignored}


def _resume_interrupted() -> None:
    """After a restart, continue alert runs that were cut off (Alertmanager will not resend for an hour)."""
    with db_lock:
        rows = db.execute("SELECT thread_id FROM alerts WHERE state = 'firing'").fetchall()
    for (thread_id,) in rows:
        try:
            nxt = graph.get_state(_config(thread_id)).next
        except Exception:
            continue
        if nxt and "human_approval" not in nxt and thread_id not in jobs:
            _start(thread_id, None, gated=True)  # None resumes from the last checkpoint


_resume_interrupted()


@app.get("/api/alerts")
def list_alerts():
    with db_lock:
        rows = db.execute(
            "SELECT thread_id, alertname, namespace, resource, summary, state, created "
            "FROM alerts ORDER BY created DESC LIMIT 20"
        ).fetchall()
    out = []
    for thread_id, alertname, namespace, resource, summary, state, created in rows:
        snap = _snapshot(thread_id)
        plan = snap.get("plan") or {}
        out.append({
            "thread_id": thread_id, "alertname": alertname, "namespace": namespace, "resource": resource,
            "summary": summary, "alert_state": state, "created": created, "status": snap["status"],
            "queued": snap["queued"], "error": snap["error"],
            "graph_url": f"{GRAFANA_URL}/d/demo-workloads?orgId=1&var-namespace={namespace}&var-pod={resource}&from=now-30m&to=now",
            "actions": [a["tool"] for a in plan.get("actions", []) if a.get("guard") == "allowed"],
        })
    return out


@app.post("/api/chat")
def chat(req: ChatRequest):
    thread_id = str(uuid.uuid4())
    conv_id = req.conversation_id or str(uuid.uuid4())
    sources[thread_id] = "eval" if req.source == "eval" else "chat"
    _start(thread_id, {"request": req.message, "conversation_id": conv_id, "context": _context(conv_id)})
    return {"thread_id": thread_id, "conversation_id": conv_id, "status": "running"}


@app.post("/api/approve")
def approve(req: ApprovalRequest):
    config = _config(req.thread_id)
    if jobs.get(req.thread_id, {}).get("running"):
        raise HTTPException(status_code=409, detail="This thread is already running.")
    if "human_approval" not in graph.get_state(config).next:
        raise HTTPException(status_code=409, detail="Nothing is waiting for approval on this thread.")
    _start(req.thread_id, Command(resume={"approved": req.approved}))
    return {"thread_id": req.thread_id, "status": "running"}


@app.get("/api/status/{thread_id}")
def status(thread_id: str):
    if not graph.get_state(_config(thread_id)).values and thread_id not in jobs:
        raise HTTPException(status_code=404, detail="Unknown thread.")
    return _snapshot(thread_id)


@app.get("/api/outcomes")
def list_outcomes(limit: int = 50):
    """What the agent concluded and what happened, newest first, with totals."""
    return {"stats": outcomes.stats(), "recent": outcomes.recent(max(1, min(limit, 200)))}


@app.get("/healthz")
def healthz():
    return {"ok": True}


app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))
