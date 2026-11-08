# Envoy: your agent, their agent

**A system design document**

Envoy is an agent-to-agent protocol for asking for someone's time. One person's
agent makes an honest, evidence-backed request to another person's agent; a
meeting happens only when both sides fit, both agents agree, and both humans
approve. The same engine runs every relationship type through *scenario packs*
(`packs/*.yaml`), which encode the rules of that relationship.

This document describes the architecture, the pitch loop that the agents run
every turn, the research the architecture draws from, and the end-to-end
operational behaviours: data ingestion through connectors, discovery, session
lifecycle, approval and booking, evaluation, and how to wire your own Google
Calendar into the system.

---

## 1. Problem and principles

### 1.1 The problem

Scheduling a meeting between two people with real asymmetric incentives is
expensive to do well. The founder needs the investor's time; the investor
guards their calendar against pitches that do not fit. The recruiter wants the
candidate to pass the first-round bar; the candidate wants the recruiter to
see their strongest signal. Today this costs a dozen emails, a handful of
calendar links, and in many cases a meeting that should never have been
booked.

Giving each side an agent sounds appealing, but naively letting two LLM agents
negotiate is dangerous. Two issues dominate:

1. **Bluffing pays if the environment does not punish it.** Unverifiable
   claims tilt the outcome toward whoever lies more boldly (PieArena [1]).
2. **Prompt injection breaks role.** One side can embed instructions in a
   "message" and hijack the other side's decision-making.

Envoy's design is a direct response to both. Code — not the LLM — decides
whether a pack fits, whether a claim is backed, whether a counter-strategy
is allowed, and whether a figure in a draft message is one we are allowed to
state. The LLM's job is strictly to phrase what the planner has chosen.

### 1.2 Principles

- **Code decides, the model phrases.** Deterministic scoring and
  pack-enforced rules govern every load-bearing decision (fit, accept, walk
  away, disclosure tier). An LLM call never flips these.
- **Honest by construction.** A message may only contain figures that appear
  in the claims it carries, and may not use any pack's banned pressure
  phrases. The verifier runs on every outgoing draft.
- **Everything through the broker.** Agents never call each other directly.
  Every message crosses the broker, which validates the schema, scans for
  injection, enforces the pack, and logs the event.
- **Both humans approve.** No meeting is booked unless both people, not
  their agents, say yes.
- **Offline-first.** The demo runs with no API key: decisions are
  deterministic and phrasing uses templates. Enabling an LLM only widens the
  range of phrasings, it never changes the outcome space.

---

## 2. Architecture overview

### 2.1 Components

```
                             +---------------------+
                             |      Frontend       |
                             |   (ES module UI)    |
                             +---------+-----------+
                                       |  HTTP + SSE
                             +---------v-----------+
                             |   FastAPI service   |
                             |   (server/main.py)  |
                             +-----+---------+-----+
                                   |         |
                   discovery, intent, connectors, sessions
                                   |         |
     +-----------------+   +-------v-----+   +----v------------+
     |  Personal       |   |  Broker     |   |  Store          |
     |  Context Graph  |   |  (session)  |   |  (SQLite log +  |
     |  (networkx)     |   |             |   |   agent memory) |
     +---------+-------+   +-----+-------+   +-----------------+
               |                 |
     +---------+----+   +--------+-----------+
     |  Connectors  |   |  Two agent         |
     |  (LinkedIn,  |   |  pipelines:        |
     |   GitHub,    |   |                    |
     |   Portfolio, |   |   requester.py     |
     |   Instagram, |   |   receiver.py      |
     |   Google     |   |                    |
     |   Calendar,  |   |  share: fit.py,    |
     |   Interview) |   |   verifier.py,     |
     +--------------+   |   firewall.py,     |
                        |   persuasion.py    |
                        +--------------------+
```

### 2.2 Data model

- **Personal Context Graph** (`server/knowledge/graph.py`): one typed,
  directed multigraph per person, persisted as JSON. Every fact is a node
  with a **type**, a **disclosure tier** (`public` / `fit` / `approval` /
  `private`), a **source** (`seed` / `linkedin` / `github` / `interview` /
  `google_calendar` / …), and **evidence** attached via a `SUPPORTED_BY`
  edge to a typed `Evidence` node. Edges carry types: `HAS`, `FOUNDED`,
  `KNOWS`, `PARTNER_AT`, etc.

- **Scenario pack** (`packs/*.yaml`): per-relationship rules. A pack
  declares its **persuasion level** (0 coordinate-only, 1 propose with
  reasons, 2 make the case), the **outcome** type (meeting / hangout /
  session / call), the **fit criteria** with weights and comparison kinds
  (`member` / `overlap` / `in_range` / `slot_overlap`), **vetoes**,
  negotiable **issues** (duration, format, agenda, slot), **diligence**
  questions, **shareable node types**, **tone budget**, **decline style**,
  and **banned pressure phrases**.

- **Session** (`server/broker.py`): a bounded, auditable transaction
  between two agents under one pack. Holds the transcript, the agreed
  proposal (if any), each human's approval vote, and the full event log.

### 2.3 Request lifecycle

1. The user in the UI picks their agent and an intent ("Raising a round").
2. `/api/people/{id}/matches?intent=raising` runs **discovery**: scans the
   network for people whose declared intent fits, previews fit on public
   info only, scores common ground, and ranks candidates.
3. The user hits **Start conversation** on a match. A session is created and
   both agents begin running the pipeline below.
4. The browser opens a Server-Sent Events stream from
   `/api/sessions/{id}/events` and renders every event live.
5. On agreement, the UI shows the approval bar with a briefing per side.
6. When both humans approve, the state becomes `booked` and `/invite.ics`
   serves the calendar invite.

---

## 3. The pitch loop

Each turn runs the same five-step pipeline in each agent. This is the heart
of the system and the place where the referenced research matters most.

### 3.1 Analyzer (what did they just do)

- Classifies the incoming message's **act** (`introduce` / `question` /
  `request_info` / `answer` / `propose` / `counter_propose` / `accept` /
  `decline` / `end`).
- Detects **resistance type** against the taxonomy from Persuasion Games [2]:
  `counterargument`, `source_derogation`, `inoculation`, `reactance`,
  `selective_exposure`, `information_seeking`, `in_group_identity`,
  `self_esteem`, `avoidance`, `none`.
- Rule-based classifier runs always (`server/persuasion.py:classify_resistance`);
  an LLM refines in live mode.
- Updates the **shared-intentionality state tracker** (PieArena [1]):
  what topics they focus on, which claims we have made, which they have
  asked about, interest trajectory, concessions offered.

### 3.2 Strategist (what to do about it)

For level-2 packs the planner calls `persuasion.plan_pitch`:

| Resistance | Counter-strategy (chosen by code) |
|---|---|
| counterargument | concede the narrow point, reframe with evidence |
| source_derogation | cite third-party or customer evidence |
| inoculation | acknowledge the known concern, then differentiate |
| reactance | reduce the ask and give a choice |
| selective_exposure | surface an unaddressed strength |
| information_seeking | answer with evidence |
| in_group_identity | lead with common ground |
| self_esteem | affirm their expertise, then ask |
| avoidance | shrink the commitment (async Q&A, short call) |
| none | continue the pitch |

The planner picks the single strongest unmade claim (highest-weight
unanswered fit criterion), decides what to ask for (full meeting or a
shrunk commitment under avoidance / reactance), and returns a structured
**PitchIntent** — the direct analogue of CICERO's intent object [3].

### 3.3 Generator (phrase it)

The generator receives the PitchIntent plus the facts it is allowed to
state. It generates N candidate messages. The LLM's prompt includes:

- the pack tone, title, and persuasion level,
- the chosen move and counter-strategy,
- the specific claims we may cite (with their evidence summaries),
- the objection to counter (quoted),
- the acknowledgment and concession to weave in.

Every candidate falls back to the deterministic template if the live call
fails. This is the **intent → candidates → filter** pattern CICERO used
with a trained model, implemented here with prompting [3].

### 3.4 Verifier (never lie, never pressure, never too long)

`server/agents/verifier.py` rejects any draft that:

1. Contains a figure (number, dollar amount, percent) that is not present
   in the claims the message carries. The regex-based detector normalises
   tokens (`$50k`, `40%`, `2.5M`) and checks each against the allowed set
   built from the claim statements and the proposal fields.
2. Uses one of the pack's banned pressure phrases ("last chance",
   "act now", "closing tomorrow", etc.).
3. Exceeds the pack's word budget (1.6 × `tone.max_words`).

If no candidate passes the verifier, the template fallback is sent.

### 3.5 Receiver pushback (the pitch, not just the schedule)

For level-2 packs the receiver no longer accepts on the first good fit. It
runs an additional step before the accept gate (`persuasion.craft_objection`):

1. If any numeric claim has no backing evidence → challenge it.
2. Otherwise, if traction has been shared but reads light → ask how it
   converts into ARR / adoption.
3. Otherwise → ask "why now".

Only one objection per session. The founder must counter with an
evidence-backed answer; `objection_resolved` is marked once that lands.
The acceptance gate then opens.

The accept gate (level ≥ 2): `fit AND diligence_covered AND
objection_resolved AND interest ≥ 4.0 / 5`.

### 3.6 Per-turn interest score (PMIYC)

The receiver emits a **1-to-5 interest score** after every message:

```
interest = 1 + 4 · fit.score · fit.coverage - 0.5·(verifier_problems ? 1 : 0)
         + 0.5·(objection_resolved ? 1 : 0)
```

This is the signal that PMIYC [4] identified as the right telemetry for a
persuasion loop: a per-turn, visible-on-stream agreement score. The UI
plots it as a sparkline on the receiver's desk.

---

## 4. Firewall: treating the other agent's text as data

`server/agents/firewall.py` runs on every incoming message **before** the
receiving agent looks at it. Two layers:

1. **Pattern layer** (always): a small set of regexes catches the common
   prompt-injection shapes — "ignore your previous instructions",
   "disregard your mandate", "system prompt", "you are now", "mark this as
   a perfect fit", "reveal your calendar", "override mode".
2. **Model layer** (live mode only): a second-opinion LLM call framed as a
   security filter, told explicitly that the text below came from another
   agent and is data, not instructions.

If either layer flags, the broker emits a `flag` event and transitions the
session to `flagged`. No further message reaches the receiver.

The adversarial scenario (`rex` → `dana`) exercises this path.

---

## 5. Fit scoring: deterministic, pack-driven

`server/agents/fit.py` is pure arithmetic. For each fit criterion in the
pack:

- **`member`**: the fact must be in the counterpart's accepted set
  (e.g. founder's stage ∈ investor's stages).
- **`overlap`**: the fact and the counterpart's preference must share at
  least one element (e.g. sectors).
- **`in_range`**: a numeric value must lie inside a range
  (e.g. $250k check against the fund's $100k–$750k range).
- **`slot_overlap`**: time intervals must overlap for at least N minutes.

The score is the weighted mean over scored criteria; coverage is the
weighted share of criteria that could be scored. A **veto** (any match on
the `vetoes` list) is non-negotiable. The LLM never participates in this
calculation.

---

## 6. Common ground and discovery

Before the first message the requester computes **common ground**
(`server/matching.py`):

- shared values on every `overlap` and `member` criterion,
- shared interests,
- mutual free time (longest overlapping availability window),
- mutual connections (people both know).

This is used in two places:

1. **Discovery ranking.** `GET /api/people/{id}/matches?intent=…` scores
   each candidate as `0.6·common_ground + 0.4·fit_preview`, where
   `fit_preview = fit.score · fit.coverage` on public info only, and sorts
   by that.
2. **Opening message.** The generator weaves 1–3 shared terms into the
   intro ("Given your focus on `immersive tech, legaltech`, this looked
   like a real fit"). Not invented — they are terms both sides have on
   their graphs.

---

## 7. Memory and reflection (Generative Agents)

The broker calls `store.remember` at the end of every session. Each agent
writes one durable memory tagged with the counterpart's id and the pack's
id. `store.recall` scores stored memories by **recency × relevance ×
importance** — the Generative Agents retrieval model [5]:

```
score = 0.995^(hours_since) + importance/10 + |tags ∩ query_tags| / |query_tags|
```

On the next session with the same counterpart, the `prepare` step pulls
the top memory and places it on the requester's desk under "Memory".

---

## 8. Connectors: pluggable sources for the Personal Context Graph

A connector turns one external source into typed, evidence-backed facts.
Every connector implements:

```python
class Connector:
    name: str; label: str; kind: str; accepts: str  # "text" | "handle" | "file"
    async def extract(self, payload: str) -> list[FactSpec]: ...
    async def extract_file(self, filename: str, data: bytes) -> list[FactSpec]: ...
```

Shipped connectors:

| Connector | Accepts | Produces |
|---|---|---|
| `linkedin` | ZIP, CSVs, or PDF | Work (positions), Achievement (education, certs, projects), Interest (skills), all with LinkedIn export as evidence |
| `portfolio` | URL or pasted text | Work, Achievement, Goal |
| `instagram` | ZIP or pasted bio | Interest (topics, captions) |
| `github` | username | Achievement (top repos with star counts) |
| `google_calendar` | `.ics` file or pasted ICS | Availability (free windows for the next ~10 weekdays) |
| `interview` | transcript | Any typed fact the speaker states |

Every fact a connector emits records:

- its **source** (`linkedin`, `google_calendar`, …),
- an **Evidence** node (the ref back to the source: a URL, a repo, the
  interview quote, or "Google Calendar export"),
- a conservative **tier** (`public` for things on a profile, `fit`
  otherwise), which the user can raise in the UI.

`apply()` merges intelligently: a matching node is updated, not duplicated;
interests gain `weight +1` on each new sighting; a provenance count is
recorded per source.

---

## 9. Evaluation

`scripts/run_eval.py` runs every scenario in `data/scenarios.yaml`
end-to-end against the deterministic engine, auto-approves agreed meetings,
and prints the scorecard. Current result:

```
scenario               expected   actual         turns  unbacked  flags
investor_fit           booked     booked             8         0      0
investor_misfit        declined   declined           2         0      0
investor_injection     flagged    flagged            1         0      1
friends_plan           booked     booked             2         0      0
friends_busy           declined   declined           2         0      0
attendees_match        booked     booked             4         0      0

Correct outcomes: 6/6   False agreements: 0   Unbacked figures: 0
```

The gates that must stay green:

- **Correct outcomes = 6/6**: both booking and declining must be right.
- **False agreements = 0**: a session must never book when the expected
  outcome is a decline. (TERMS-Bench style [6].)
- **Unbacked figures = 0**: the verifier must never let a figure through
  that no claim backs.

`tests/test_platform.py` and `tests/test_persuasion.py` cover the broker,
connectors, discovery, intent mapping, common ground, firewall, and the
pitch loop.

---

## 10. Observability

Every session emits a stream of typed events over SSE. The UI renders each.

| Event | Emitted by | Meaning |
|---|---|---|
| `session` | broker | pack, scenario, counterpart identities |
| `handshake` | broker | both sides disclosed as AI |
| `thinking` | both | a reasoning step (Analyzer / Planner / Strategist / Verifier / Decision policy / …) |
| `fit` | both | fit result (score, coverage, rows, veto) |
| `common_ground` | requester | shared points, score |
| `pitch` | requester | strategist step: resistance addressed, strategy, claim keys, concession |
| `message` | broker | validated AgentMessage + firewall flags |
| `checks` | receiver | per-turn evidence tally and problems |
| `score` | receiver | 1-to-5 interest score and history |
| `objection` | receiver | an archetype objection raised, and when resolved |
| `state` | broker | FSM transition (handshake / discovery / qa / proposal / approval / booked / declined / flagged / timed_out / not_contacted) |
| `approval` | broker | agreement reached, waiting for humans |
| `approval_vote` | broker | one human voted |
| `booked` | broker | both humans approved; slot + ICS URL |
| `reflection` | broker | summary written to each agent's memory |

---

## 11. How to connect your Google Calendar

Envoy ships with a Google Calendar connector that reads a standard `.ics`
export and emits free-window Availability nodes into your graph. Steps:

1. **Export your calendar from Google.**
   1. Open [Google Calendar](https://calendar.google.com/) in a browser.
   2. Click the gear icon → **Settings**.
   3. In the left sidebar, choose **Import & Export**.
   4. Under **Export**, click **Export**. This downloads a ZIP named
      `<your-email>.ical.zip` containing one `.ics` file per calendar you
      own.
   5. Unzip. You will see files like `your.email@gmail.com.ics`.

2. **Open the Knowledge graphs tab in Envoy.**
   Start the app (`uvicorn server.main:app --reload`) and go to
   `http://127.0.0.1:8000`. Switch to the **Knowledge graphs** tab.

3. **Pick (or create) your agent.**
   Select yourself from "Your agents" on the left. If you have not
   created an agent yet, fill in the "Full name" field and click
   **Create agent**.

4. **Upload the `.ics` file.**
   In the right-hand **Connect sources** panel, find **Google Calendar**.
   Click **Choose Files**, select the `.ics` file you exported (or
   several, if you have multiple calendars), and click
   **Upload Google Calendar**.

5. **What gets added to your graph.**
   The connector parses every `VEVENT` block as a **busy** interval. It
   then subtracts those busy intervals from a working week (Monday–Friday,
   09:00–18:00 local) over the next ~10 weekdays and emits each resulting
   free slice of 30+ minutes as an `Availability` node, tiered `fit`, with
   "Google Calendar export" as its evidence. These are what the fit
   scorer compares against the counterpart's windows to find a mutual
   slot.

6. **Alternative: paste raw ICS.**
   The same connector also accepts pasted ICS text; the UI's text-mode
   input works too if you just want to paste a schedule.

7. **Verify.**
   The newly added Availability nodes appear on the graph as orbiting
   satellites under the center node. Click one to see its tier, source,
   and evidence (the Google Calendar export).

**Privacy note.** The ICS file stays on your machine; nothing is uploaded
beyond the HTTP request that your browser makes to the local server. The
connector only reads `DTSTART` and `DTEND` on each event — it does not
read `SUMMARY`, `DESCRIPTION`, or any attendee information.

**Live-fetch (future).** An OAuth / Google Calendar API connector would
drop into the same `Connector` contract without any change above the
registry.

---

## 12. Operational notes

- **Offline mode**: leave `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` empty.
  `/api/health` reports `mode: mock`. The decisions do not change, only
  the phrasing.
- **Live mode**: set either `ANTHROPIC_API_KEY` or a
  `OPENAI_BASE_URL` + `OPENAI_API_KEY` pair. Model ids are configurable
  through `ENVOY_MODEL_SMART` and `ENVOY_MODEL_FAST`.
- **Pace**: `ENVOY_PACE` (seconds between pipeline steps) makes the
  session watchable on stage. Set `0` for tests and the evaluation.
- **Audit log**: every event is written to SQLite (`envoy.db`). The
  session SSE replays from the beginning so a late-joining viewer sees
  the whole story.

---

## 13. References

1. **PieArena** — Zhu et al., *PieArena: Frontier Language Agents Achieve
   MBA-Level Negotiation Performance and Reveal Novel Behavioral
   Differences*. The source of the **shared-intentionality state tracker**
   plus the **lie-rate ↔ value-captured** finding that motivates
   "honest by construction".

2. **Persuasion Games** (TCS / IIT) — Tyagi et al., *Persuasion Games
   Using Large Language Models*. The source of the four-agent
   **analyzer → strategist → generator → fact-checker** chain, the nine
   **resistance types**, and the mapping to counter-strategies.

3. **CICERO** — Meta FAIR, *Human-level play in the game of Diplomacy by
   combining language models with strategic reasoning*
   (*Science*, 2022). The source of the **intent object** that separates
   planning from phrasing, and the **candidates → filter** pattern.

4. **PMIYC (Persuade Me If You Can)** — UIUC, *PMIYC: Benchmarking LLM
   Persuasion in Agent-to-Agent Dialogue*. The source of the **per-turn
   agreement score** and the finding that **most persuasive effect
   happens in the first two turns**, which is why Envoy's opening message
   leads with the strongest claim.

5. **Generative Agents** — Park et al., *Generative Agents: Interactive
   Simulacra of Human Behavior*, Stanford, 2023. The source of the
   **memory stream** retrieval model (recency + relevance + importance).

6. **TERMS-Bench** — Stanford, *TERMS-Bench: Diagnosing LLM Negotiation
   Agents*. The source of the **false-agreement rate** metric — a session
   must never book when no deal is possible — which Envoy's evaluation
   tracks directly.

7. **LLM-Deliberation** (CISPA) — *LLM-Deliberation: Evaluating LLMs with
   Interactive Multi-Agent Negotiation Games*. The source of the
   **incentive slot** idea for red-teaming the founder agent against a
   sabotaging investor.

8. **A2A negotiation risk** (Stanford / MIT, NeurIPS 2025) — pairs two
   negotiation agents with two auxiliary models around a core negotiation
   loop. Informs Envoy's broker-as-auxiliary architecture and the
   authorisation envelope on every proposal.

9. **AgenticPay** (Berkeley) — grounds dialogue in structured actions.
   Envoy's structured `accept` handshake and the ICS invite are the
   action-grounding at the end of the loop.

10. **MERIT / AgoraBench** (KAIST) — in-context feedback between rounds
    improves negotiation outcomes without training. The receiver's
    `objection → resolved` loop echoes this feedback pattern inside a
    single session.

11. **MIT AI Negotiation Competition** (PNAS) — the winning entry
    combined classical negotiation preparation (goals, BATNA, likely
    objections) with LLM phrasing. Envoy's `terms()` on the Personal
    Context Graph carries exactly this preparation.

---

## 14. Open questions and next steps

- **Wire up the deeper negotiation engine.** `server/negotiation/{strategy,
  terms, guard}.py` and `server/agents/negotiator.py` define a multi-issue
  bargaining layer with concession schedules and a guard that enforces each
  side's limits. These modules are complete but not yet imported by the
  agents. Wiring them in adds structured bargaining over duration, format,
  agenda, and slot on top of the pitch loop.
- **Scripted adversarial investors** (TERMS-Bench style). Add scenarios
  whose hidden ground truth says "no deal possible" and assert that the
  founder agent walks away rather than forces a booking.
- **Google Calendar live-fetch.** OAuth + Calendar API, replacing the ICS
  upload but keeping the same connector contract.
- **Two-machine demo.** Run two Envoy servers and relay `AgentMessage`
  JSON over HTTP between their brokers, so the "agent-to-agent" claim is
  physical.
- **Neo4j or Postgres storage** behind the Personal Context Graph,
  keeping the current interface.
