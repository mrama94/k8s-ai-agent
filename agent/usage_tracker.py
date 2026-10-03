import time

from langchain_core.messages import AIMessage


def new_totals() -> dict:
    return {"tokens_in": 0, "tokens_out": 0, "llm_calls": 0, "llm_seconds": 0.0}


def add(totals: dict, ai: AIMessage, started: float) -> None:
    meta = ai.usage_metadata or {}
    totals["tokens_in"] += meta.get("input_tokens", 0)
    totals["tokens_out"] += meta.get("output_tokens", 0)
    totals["llm_calls"] += 1
    totals["llm_seconds"] += time.monotonic() - started
