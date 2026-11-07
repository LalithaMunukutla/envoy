# CLAUDE.md: context for Claude Code

## What this is
Envoy is a hackathon project (Anything Applied Agentic Hackathon, SF, Nov 2026). Agents negotiate meetings on behalf of
people. Requester agent and Receiver agent talk only through a broker. Behavior per relationship comes from scenario
packs (`packs/*.yaml`): founder_to_investor (persuasion level 2), friend_to_friend (level 0), attendee_to_attendee (level 1).

## Commands
- Install: `pip install -r requirements.txt`
- Seed demo data: `python scripts/seed_people.py`
- Scorecard: `python scripts/run_eval.py` (must stay 5/5 correct, 0 false agreements, 0 unbacked figures)
- Tests: `pytest -q`
- Run: `uvicorn server.main:app --reload` then http://127.0.0.1:8000

## Rules for changes
- Code decides, the model phrases. Never let LLM output set fit, decisions or actions. LLM calls must keep a fallback.
- Every factual claim needs evidence; `verifier.check` must pass on every outgoing message.
- Agents never call each other directly; everything goes through `broker.Session.admit`.
- Keep offline mode working (no API key). Run eval + tests after every backend change.
- Personas in `data/` are fictional; do not add real people without their consent.

## API (all JSON)
- GET /api/health -> {mode}
- GET /api/scenarios, /api/packs, /api/people, /api/people/{id}/graph -> {nodes[{id,label,type,tier,source,attrs}], edges[{source,target,type}]}
- POST /api/sessions {scenario} -> session info {id, pack, requester, receiver, state}
- GET /api/sessions/{id}/events  (Server-Sent Events, replays from start)
  event types: session, handshake, thinking{role,step,detail}, fit{role,label,fit{score,coverage,veto,rows[{label,status,detail}]}},
  message{message{sender{role,acting_for},act,text,claims[{statement,evidence,verified}],proposal{type,fields}},flags[]},
  checks{role,turn,verified,total,problems[]}, score{role,turn,interest,history[]}, flag{text}, state{state,reason},
  approval{proposal,summary_for_requester,summary_for_receiver}, approval_vote, booked{slot,ics}, reflection, error
- POST /api/sessions/{id}/approve {role: requester|receiver, approve: bool}
- GET /api/sessions/{id}/invite.ics
- POST /api/people/{id}/ingest/interview {transcript}; POST /api/people/{id}/ingest/github {username}

## UI (built): frontend/index.html, styles.css, app.js
Vanilla ES module served by FastAPI, no build step. "Two desks and a checkpoint": cobalt requester desk, mulberry
receiver desk, letters in the corridor stamped by the broker in brass. Knowledge graphs tab: radial SVG of a person's
graph colored by disclosure tier, plus interview and GitHub ingestion. Keep: no all-caps labels, one motion (letters
arriving), prefers-reduced-motion respected, keyboard focus visible.

## Good next tasks
- More scenarios per pack (counter-proposal path, withheld facts, time-outs) with expected outcomes in data/scenarios.yaml.
- A "build my agent" onboarding flow in the UI: interview -> graph -> review tiers -> set mandate.
- Let people edit a node's tier from the graph inspector (PATCH endpoint + UI).
- Two-machine demo: run two servers and relay AgentMessage JSON over HTTP between brokers.
- Neo4j or Postgres storage behind PersonalGraph's load/save, keeping the same interface.
