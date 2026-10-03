import os
import sqlite3
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel

from graph import build_graph

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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
db.commit()
db_lock = threading.Lock()

# In-memory job table: graph runs happen in background threads so no HTTP request stays open
# for minutes (kubectl port-forward drops long connections). The page polls /api/status.
jobs: dict[str, dict] = {}


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


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
    usage = {k: values.get(k, 0) for k in USAGE_KEYS}
    usage["cost_usd"] = 0.0  # local model: nothing to pay
    return {
        "thread_id": thread_id,
        "conversation_id": values.get("conversation_id"),
        "status": status,
        "error": job.get("error"),
        "steps": job.get("steps", []),
        "elapsed": round((job.get("finished") or time.time()) - job["started"], 1) if job else 0,
        "diagnosis": values.get("diagnosis"),
        "plan": values.get("plan"),
        "results": values.get("results", []),
        "verification": values.get("verification"),
        "attempts": values.get("attempts", 0),
        "history": values.get("history", []),
        "usage": usage,
    }


def _run(thread_id: str, graph_input) -> None:
    """Run (or resume) the graph to its next pause or end, recording progress."""
    job = jobs[thread_id]
    try:
        for update in graph.stream(graph_input, _config(thread_id), stream_mode="updates"):
            job["steps"].extend(k for k in update if not k.startswith("__"))
    except Exception as exc:
        job["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        job["running"] = False
        job["finished"] = time.time()


def _start(thread_id: str, graph_input) -> None:
    jobs[thread_id] = {"running": True, "steps": [], "started": time.time()}
    threading.Thread(target=_run, args=(thread_id, graph_input), daemon=True).start()


@app.post("/api/chat")
def chat(req: ChatRequest):
    thread_id = str(uuid.uuid4())
    conv_id = req.conversation_id or str(uuid.uuid4())
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


@app.get("/healthz")
def healthz():
    return {"ok": True}


app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))
