import os
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
graph = build_graph(os.getenv("DB_PATH", "/data/agent.db"))


class ChatRequest(BaseModel):
    message: str


class ApprovalRequest(BaseModel):
    thread_id: str
    approved: bool


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _snapshot(thread_id: str) -> dict:
    snap = graph.get_state(_config(thread_id))
    values = snap.values
    usage = {k: values.get(k, 0) for k in USAGE_KEYS}
    usage["cost_usd"] = 0.0  # local model: nothing to pay
    return {
        "thread_id": thread_id,
        "status": "awaiting_approval" if snap.next else "done",
        "diagnosis": values.get("diagnosis"),
        "plan": values.get("plan"),
        "results": values.get("results", []),
        "verification": values.get("verification"),
        "attempts": values.get("attempts", 0),
        "history": values.get("history", []),
        "usage": usage,
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    thread_id = str(uuid.uuid4())
    graph.invoke({"request": req.message}, _config(thread_id))
    return _snapshot(thread_id)


@app.post("/api/approve")
def approve(req: ApprovalRequest):
    config = _config(req.thread_id)
    if not graph.get_state(config).next:
        raise HTTPException(status_code=409, detail="Nothing is waiting for approval on this thread.")
    graph.invoke(Command(resume={"approved": req.approved}), config)
    return _snapshot(req.thread_id)


@app.get("/healthz")
def healthz():
    return {"ok": True}


app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))
