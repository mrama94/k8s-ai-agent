import pathlib
import re

from langchain_core.tools import tool

RUNBOOK_DIR = pathlib.Path(__file__).resolve().parent.parent / "runbooks"
MAX_RESULTS = 2


def _words(text: str) -> set:
    return set(re.findall(r"[a-z0-9]{3,}", text.lower()))


@tool
def search_runbooks(query: str) -> str:
    """Search the team runbooks for troubleshooting steps. Use the symptom or event reason as the query, for example ImagePullBackOff, FailedScheduling or no endpoints."""
    wanted = _words(query)
    scored = []
    for path in RUNBOOK_DIR.glob("*.md"):
        text = path.read_text()
        score = len(wanted & _words(text)) + 3 * len(wanted & _words(path.stem))
        if score:
            scored.append((score, path.stem, text))
    if not scored:
        return "No matching runbook."
    scored.sort(reverse=True)
    return "\n\n".join(f"[{stem}]\n{text}" for _, stem, text in scored[:MAX_RESULTS])[:4000]
