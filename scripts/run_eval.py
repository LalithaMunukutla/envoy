"""Runs every scenario in data/scenarios.yaml, auto-approves agreed meetings, and prints the scorecard.
    python scripts/run_eval.py"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ENVOY_PACE", "0")

import yaml  # noqa: E402

from server.broker import TERMINAL, manager  # noqa: E402
from server.config import DATA_DIR  # noqa: E402


async def run_one(sc: dict) -> dict:
    s = manager.start(sc["pack"], sc["requester"], sc["receiver"], sc["id"], pace=0)
    while s.state not in TERMINAL and s.state != "approval":
        await asyncio.sleep(0.01)
    await s.task
    if s.state == "approval":
        await s.approve("requester", True)
        await s.approve("receiver", True)
    msgs = [e for e in s.events if e["type"] == "message"]
    checks = [e for e in s.events if e["type"] == "checks"]
    return {
        "id": sc["id"], "expected": sc["expected"], "actual": s.state, "turns": len(msgs),
        "unverifiable": sum(len(c["problems"]) for c in checks),
        "flags": sum(1 for e in s.events if e["type"] == "flag"),
        "interest": next((e["history"] for e in reversed(s.events) if e["type"] == "score"), []),
    }


async def main() -> int:
    rows = [await run_one(sc) for sc in yaml.safe_load((DATA_DIR / "scenarios.yaml").read_text())]
    print(f"{'scenario':22} {'expected':10} {'actual':14} turns  unbacked  flags  interest")
    for r in rows:
        ok = "PASS" if r["expected"] == r["actual"] else "FAIL"
        print(f"{r['id']:22} {r['expected']:10} {r['actual']:14} {r['turns']:5}  {r['unverifiable']:8}  {r['flags']:5}  {r['interest']}  {ok}")
    correct = sum(r["expected"] == r["actual"] for r in rows)
    false_agree = sum(r["expected"] != "booked" and r["actual"] == "booked" for r in rows)
    print(f"\nCorrect outcomes: {correct}/{len(rows)}   False agreements: {false_agree}   "
          f"Unbacked figures: {sum(r['unverifiable'] for r in rows)}")
    return 0 if correct == len(rows) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
