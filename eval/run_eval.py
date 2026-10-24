#!/usr/bin/env python3
"""Run the evaluation scenarios against a running agent and score what it concludes.

For each scenario: remove any leftovers, create the fault, wait for it to show, ask the agent, wait for its plan,
score the diagnosis and the plan, then close the run and clean up. By default the plan is rejected after scoring, so
nothing is changed. With --approve, a plan that scored correct is approved and the run is also scored on whether the
cluster became healthy; a plan that scored wrong is still rejected.

Needs: the agent reachable (./scripts/portforward.sh), kubectl, and the demo namespace. Standard library only.

  python3 eval/run_eval.py --list
  python3 eval/run_eval.py --only A2,G1 --label try
  python3 eval/run_eval.py --approve --label baseline
  python3 eval/run_eval.py --compare eval/baselines/baseline.json eval/results/try.json
  python3 eval/run_eval.py --merge eval/baselines/baseline.json eval/results/rerun.json

Alert-driven runs can start for the same faults while this runs (the alert rules fire after 1 to 2 minutes). They
compete for the one local model, so timings are noisy; the scores are not affected.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from scenarios import SCENARIOS  # noqa: E402
from scoring import allowed_actions, compare, merge, plan_ok, score, summarise  # noqa: E402


def call(base: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.load(res)


def poll(base: str, thread_id: str, stop: set[str], timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    status = {}
    while time.monotonic() < deadline:
        try:
            status = call(base, f"/api/status/{thread_id}")
        except (urllib.error.URLError, OSError):
            time.sleep(3)  # a dropped port-forward heals itself within seconds
            continue
        if status["status"] in stop:
            return status
        time.sleep(3)
    raise TimeoutError(f"run {thread_id} still '{status.get('status')}' after {int(timeout)} s")


def sh(command: str, context: str, quiet: bool = False) -> int:
    """Run a shell command from the repo root, pointing kubectl at the lab cluster."""
    command = re.sub(r"\bkubectl\b", f"kubectl --context {context}", command)
    proc = subprocess.run(["bash", "-c", command], cwd=ROOT, capture_output=True, text=True)
    if proc.returncode and not quiet:
        print(f"    ! {command[:90]}\n      {proc.stderr.strip()[:200]}")
    return proc.returncode


def run_scenario(sc: dict, args) -> dict:
    result = {"id": sc["id"], "title": sc["title"]}
    try:
        for cmd in sc["cleanup"]:
            sh(cmd, args.context, quiet=True)
        for cmd in sc["setup"]:
            if sh(cmd, args.context):
                raise RuntimeError(f"setup failed: {cmd[:80]}")
        time.sleep(sc["settle"])
        started = call(args.agent, "/api/chat", {"message": sc["ask"], "source": "eval"})
        thread_id = started["thread_id"]
        result["thread_id"] = thread_id
        status = poll(args.agent, thread_id, {"awaiting_approval", "done", "error", "interrupted"}, args.timeout)
        if status["status"] in ("error", "interrupted"):
            raise RuntimeError(f"run ended as {status['status']}: {status.get('error')}")
        applied = None
        runnable = bool(allowed_actions(status.get("plan")))
        if status["status"] == "awaiting_approval":
            go = args.approve and plan_ok(status.get("plan"), sc["expect"]["plan"])
            call(args.agent, "/api/approve", {"thread_id": thread_id, "approved": bool(go)})
            after = poll(args.agent, thread_id, {"done", "error"}, args.timeout)
            if go and after["status"] == "done":
                applied = after
        result.update(score(status, sc["expect"], applied))
        d = status.get("diagnosis") or {}
        result["root_cause"] = d.get("root_cause", "")
        result["confidence"] = d.get("confidence", "")
        result["plan"] = [{"tool": a["tool"], **a.get("args", {})} for a in allowed_actions(status.get("plan"))]
        result["runnable_plan"] = runnable
        result["plan_full"] = status.get("plan")
        if applied is not None:
            result["verification"] = (applied.get("verification") or {}).get("evidence", "")[:500]
    except Exception as exc:  # one broken scenario must not stop the others
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        for cmd in sc["cleanup"]:
            sh(cmd, args.context, quiet=True)
    return result


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def show(results: list[dict]) -> None:
    print(f"\n{'id':<4} {'result':<7} {'diag':<5} {'plan':<5} {'safe':<5} {'fixed':<6} {'calls':<6} title")
    for r in results:
        if "error" in r:
            print(f"{r['id']:<4} ERROR   {r['error'][:80]}")
            continue
        c = r["checks"]
        mark = lambda k: "-" if k not in c else ("yes" if c[k] else "NO")  # noqa: E731
        print(f"{r['id']:<4} {'pass' if r['passed'] else 'FAIL':<7} {mark('diagnosis'):<5} {mark('plan'):<5} "
              f"{mark('safe'):<5} {mark('fixed'):<6} {r['llm_calls']:<6} {r['title']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", default="http://localhost:8000", help="agent URL (default %(default)s)")
    ap.add_argument("--context", default="kind-agent-lab", help="kubectl context (default %(default)s)")
    ap.add_argument("--only", help="comma-separated scenario ids, for example A2,G1")
    ap.add_argument("--approve", action="store_true", help="approve plans that scored correct and check the cluster heals")
    ap.add_argument("--label", help="name for the saved result (default: a timestamp)")
    ap.add_argument("--timeout", type=float, default=900, help="seconds to wait for one run (default %(default)s)")
    ap.add_argument("--list", action="store_true", help="list the scenarios and exit")
    ap.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"), help="compare two saved results and exit")
    ap.add_argument("--merge", nargs=2, metavar=("OLD", "NEW"),
                    help="write OLD with the scenarios found in NEW replaced by NEW's results, then exit")
    args = ap.parse_args()

    if args.compare:
        old, new = (json.loads(pathlib.Path(p).read_text()) for p in args.compare)
        print("\n".join(compare(old, new)))
        return 0
    if args.merge:
        old_path, new_path = args.merge
        old, new = (json.loads(pathlib.Path(p).read_text()) for p in (old_path, new_path))
        ids = ", ".join(r["id"] for r in new["results"])
        merged = merge(old, new, f"{ids} re-run on {new.get('when', '')} after a scenario or scoring fix.")
        pathlib.Path(old_path).write_text(json.dumps(merged, indent=2))
        print(f"updated {old_path}: {ids} replaced; now {merged['summary']['passed']}/{merged['summary']['scenarios']} pass")
        return 0
    chosen = [s for s in SCENARIOS if not args.only or s["id"] in args.only.split(",")]
    if args.list:
        for s in SCENARIOS:
            print(f"{s['id']:<4} {s['title']}")
        return 0
    if not chosen:
        print("no scenario matches --only")
        return 2
    try:
        call(args.agent, "/healthz")
    except (urllib.error.URLError, OSError):
        print(f"The agent is not reachable at {args.agent}. Start the port-forwards with ./scripts/portforward.sh.")
        return 2

    results = []
    for sc in chosen:
        print(f"\n[{sc['id']}] {sc['title']}")
        results.append(run_scenario(sc, args))
        r = results[-1]
        print("    " + (r["error"] if "error" in r else ("pass" if r["passed"] else f"FAIL {r['checks']}")))

    show(results)
    summary = summarise(results)
    print(f"\n{summary['passed']}/{summary['scenarios']} scenarios passed; by check: {summary['by_check']}")
    out = HERE / "results" / f"{args.label or time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "label": args.label, "when": time.strftime("%Y-%m-%d %H:%M:%S"), "commit": git_sha(), "agent": args.agent,
        "approved_plans": args.approve, "summary": summary, "results": results,
    }, indent=2))
    print(f"saved {out.relative_to(ROOT)}")
    return 0 if summary["passed"] == summary["scenarios"] else 1


if __name__ == "__main__":
    sys.exit(main())
