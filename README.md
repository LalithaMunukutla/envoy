# Envoy: your agent, their agent

A general agent-to-agent protocol for asking for someone's time. One person's agent makes an honest, evidence-backed
request to another person's agent; a meeting happens only when both sides fit and both humans approve.
The same engine runs every relationship type through **scenario packs** (`packs/*.yaml`).

## Quick start

```bash
cd ~/Desktop/envoy
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # leave ANTHROPIC_API_KEY empty for offline mode
python scripts/seed_people.py   # (re)writes the demo personas
python scripts/run_eval.py      # runs all scenarios, prints the scorecard
pytest -q                       # tests
uvicorn server.main:app --reload
# open http://127.0.0.1:8000  (API docs at /docs)
```

In the UI: pick a scenario, press **Start session**, watch both desks and the corridor, then approve for both
people to book. The **Knowledge graphs** tab shows each person's graph by disclosure tier and lets you add facts.

```bash
# demo scenarios: investor_fit, investor_misfit, investor_injection, friends_plan, friends_busy, attendees_match
```

Offline mode needs no API key: decisions are deterministic, and phrasing uses templates.
Set `ANTHROPIC_API_KEY` to have Claude write and verify messages, classify replies, screen for injection and summarize.

## How it works

- `server/knowledge/graph.py`: Personal Context Graph (NetworkX, saved as JSON in `data/people/`). Every fact is a typed
  node with a disclosure tier (public, fit, approval, private), a source, and `SUPPORTED_BY` edges to evidence.
- `server/knowledge/ingest.py`: adds facts from an interview transcript (Claude, or simple patterns offline) and from
  GitHub repos.
- `server/agents/fit.py`: deterministic fit scoring from the pack's criteria. Code decides; the model only phrases.
- `server/agents/requester.py`, `receiver.py`: the two pipelines (analyze, plan an intent, generate, verify, act / check evidence,
  score, decide).
- `server/agents/firewall.py`, `verifier.py`: injection screening; no figure may appear unless a claim backs it.
- `server/broker.py`: the only path between agents. Validates, enforces pack rules, runs the state machine, holds approvals.
- `server/store.py`: SQLite audit log and agent memory (relevance, recency, importance).
- `data/scenarios.yaml`: demo scenarios with expected outcomes.

All people and firms in `data/` are fictional.
