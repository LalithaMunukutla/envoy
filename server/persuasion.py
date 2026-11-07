"""Persuasion engine: the pitch loop underneath each agent.

Architecture pulled from four papers:

- Persuasion Games (TCS/IIT) — analyzer -> strategist -> generator -> fact-checker,
  with a fixed resistance taxonomy mapped to counter-strategies.
- CICERO (Meta) — separate planning from phrasing: a structured Intent object the
  generator writes within; filtered candidates.
- PieArena (Yale) — a shared-intentionality state tracker + strategic planner that
  runs each turn before writing.
- PMIYC (UIUC) — per-turn agreement/interest score; the opening carries the weight.

Code decides everything that matters (resistance type, strategy, which claim to
cite, whether to concede); the LLM only phrases what the planner picked. Every
counter must be backed by a claim the verifier can check — no bluffs."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import Claim, Pack

# ----------------------------------------------------------------- taxonomy
# Resistance types from the Persuasion Games paper (TCS/IIT). Each maps to a
# counter-strategy the planner uses to decide the next move.
RESISTANCE_TYPES: dict[str, str] = {
    "counterargument":    "They argued against a specific claim with their own reasoning.",
    "source_derogation":  "They questioned a source we cited.",
    "inoculation":        "They flagged a known concern about this category of ask.",
    "reactance":          "They pushed back on feeling steered.",
    "selective_exposure": "They focused on a familiar angle and ignored the rest.",
    "information_seeking":"They asked for more facts before deciding.",
    "in_group_identity":  "They framed a mismatch of values or fit.",
    "self_esteem":        "They signalled the ask is outside their expertise.",
    "avoidance":          "They stalled or asked to defer.",
    "none":               "No resistance; continue the pitch.",
}

COUNTER_STRATEGY: dict[str, str] = {
    "counterargument":    "concede_narrow_then_reframe_with_evidence",
    "source_derogation":  "cite_third_party_or_customer_evidence",
    "inoculation":        "acknowledge_then_differentiate",
    "reactance":          "reduce_ask_and_give_choice",
    "selective_exposure": "surface_unaddressed_strength",
    "information_seeking":"answer_with_evidence",
    "in_group_identity":  "lead_with_common_ground",
    "self_esteem":        "affirm_their_expertise_then_ask",
    "avoidance":          "shrink_the_commitment",
    "none":               "continue_pitch",
}

# Keyword patterns for the deterministic classifier. The LLM (when live) refines
# via `analyze` upstream; this is the floor, so a model outage never leaves the
# agent stuck.
_PATTERNS: list[tuple[tuple[str, ...], str]] = [
    (("not for us", "outside our", "not in our", "doesn't fit", "wrong fit"), "in_group_identity"),
    (("too early", "come back when", "when you have", "premature", "early for us"), "inoculation"),
    (("maybe later", "ping us", "catch up in", "in a few months", "circle back", "not now"), "avoidance"),
    (("pressure", "pushy", "aggressive", "stop pitching"), "reactance"),
    (("prove", "evidence", "data", "metrics", "numbers", "where's", "what's the", "how many",
      "any customers", "any traction", "show me"), "information_seeking"),
    (("but ", "however", "that's not", "doesn't match", "not convinced", "unclear"), "counterargument"),
    (("source", "who said", "citation", "where did"), "source_derogation"),
]


def classify_resistance(text: str, act: str, requested: list[str] | None = None) -> str:
    """Rule-based resistance classifier. Returns one of RESISTANCE_TYPES."""
    requested = requested or []
    low = (text or "").lower()
    if act == "decline":
        if any(w in low for w in ("not for us", "outside", "focus", "mismatch", "fit")):
            return "in_group_identity"
        if any(w in low for w in ("too early", "premature")):
            return "inoculation"
        return "counterargument"
    if act == "counter_propose":
        return "avoidance"
    if act == "request_info" or requested:
        # information_seeking is the default for a question; a pattern match can override
        # (e.g. "too early for us, but what's the traction?" reads as inoculation).
        for keywords, rtype in _PATTERNS:
            if any(k in low for k in keywords) and rtype != "information_seeking":
                return rtype
        return "information_seeking"
    for keywords, rtype in _PATTERNS:
        if any(k in low for k in keywords):
            return rtype
    return "none"


# ----------------------------------------------------------------- state tracker
@dataclass
class NegotiationState:
    """Shared-intentionality tracker (PieArena): what we believe about them, what
    we've said, what they object to, and the interest trajectory.

    Updated on each incoming message by `observe`; the pitch planner reads it to
    pick the next move. Also lives on the receiver to track its own trajectory.
    """
    role: str = "requester"
    turn: int = 0
    their_focus: list[str] = field(default_factory=list)          # topics they've surfaced
    their_resistance: list[dict] = field(default_factory=list)    # history of resistance events
    claims_made: set[str] = field(default_factory=set)            # fact keys we've stated
    claims_asked: set[str] = field(default_factory=set)           # fact keys they asked about
    evidence_shown: set[str] = field(default_factory=set)
    concessions_offered: list[str] = field(default_factory=list)
    objections_resolved: int = 0
    interest_trajectory: list[float] = field(default_factory=list)
    their_last_act: str = ""

    def record_our_claims(self, claim_keys: list[str], evidence_ids: list[str]) -> None:
        self.claims_made.update(k for k in claim_keys if k)
        self.evidence_shown.update(e for e in evidence_ids if e)

    def observe(self, text: str, act: str, requested: list[str]) -> str:
        """Record an incoming message. Returns the classified resistance type."""
        self.turn += 1
        self.their_last_act = act
        self.claims_asked.update(requested or [])
        rtype = classify_resistance(text, act, requested)
        if rtype != "none":
            self.their_resistance.append({"turn": self.turn, "type": rtype,
                                           "topics": list(requested or []),
                                           "text": (text or "")[:160]})
        for k in requested or []:
            if k not in self.their_focus:
                self.their_focus.append(k)
        return rtype

    def last_resistance(self) -> dict | None:
        return self.their_resistance[-1] if self.their_resistance else None

    def summary(self) -> dict[str, Any]:
        last = self.last_resistance()
        return {
            "turn": self.turn,
            "their_last_act": self.their_last_act,
            "their_focus": list(self.their_focus),
            "last_resistance": last["type"] if last else "none",
            "objections_resolved": self.objections_resolved,
            "claims_made": sorted(self.claims_made),
            "interest_trajectory": list(self.interest_trajectory),
        }


# ----------------------------------------------------------------- pitch intent
@dataclass
class PitchIntent:
    """CICERO-style intent: the planner's contract with the generator. The LLM
    writes only WITHIN this object; the verifier then checks the text against it."""
    move: str = "open_pitch"
    resistance_addressed: str = "none"
    strategy: str = "continue_pitch"
    claim_keys: list[str] = field(default_factory=list)
    counter_to: str = ""                   # short quote of the objection being addressed
    acknowledgment: str = ""                # one-line concession we open with
    ask: str = ""                           # the ask (full meeting, async qa, 15-min call)
    concession: str = ""                    # what we offer to shrink the ask
    withheld: list[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# ----------------------------------------------------------------- pitch planner
def _strongest_unmade_claim(pack: Pack, available_facts: dict, state: NegotiationState,
                             their_focus: list[str]) -> list[str]:
    """Pick the two claim keys most likely to move the decision now.

    Ranking: (1) a fact the counterpart just asked about, (2) a pack fit criterion
    with high weight we haven't made yet, (3) a diligence key (team/traction) we
    haven't shown.
    """
    picks: list[str] = []
    # 1. answer what they just asked
    for k in state.claims_asked:
        if k in available_facts and k not in picks:
            picks.append(k)
    # 2. highest-weight unmade fit criterion
    criteria = sorted(pack.fit_criteria, key=lambda c: -c.weight)
    for c in criteria:
        if c.fact in available_facts and c.fact not in state.claims_made and c.fact not in picks:
            picks.append(c.fact)
    # 3. an unshown diligence point (team/traction/etc.)
    for k in pack.diligence:
        if k in available_facts and k not in state.claims_made and k not in picks:
            picks.append(k)
    return picks[:2]


def _ask_for_this_turn(pack: Pack, state: NegotiationState, resistance: str) -> tuple[str, str]:
    """Return (ask, concession). Shrinks the ask on avoidance / reactance."""
    full = pack.outcome.get("label", "meeting").lower()
    if resistance == "avoidance":
        return ("a 15-minute async Q&A", "No live call — written answers only, you decide after reading.")
    if resistance == "reactance":
        return (full, "Happy to cut the call to 15 minutes or send a deck instead.")
    if resistance == "inoculation":
        return ("a short deep-dive", "Happy to do it async first if a live call feels premature.")
    if resistance == "self_esteem":
        return ("a short review call", "Just your read — no commitment implied.")
    return (full, "")


def plan_pitch(pack: Pack, available_facts: dict, available_claims: dict[str, Claim],
                state: NegotiationState, incoming_text: str = "",
                resistance_type: str = "none",
                common_ground_points: list[str] | None = None) -> PitchIntent:
    """Deterministic pitch planner. Given the state and the last resistance,
    produce the next intent. The generator writes only within it.

    This is the strategist step in Persuasion Games. The counter-strategy comes
    from the taxonomy; the specific claims come from what we can honestly make
    about this person and their pack."""
    strategy = COUNTER_STRATEGY.get(resistance_type, "continue_pitch")
    picks = _strongest_unmade_claim(pack, available_facts, state, state.their_focus)
    ask, concession = _ask_for_this_turn(pack, state, resistance_type)

    # Default move: answer_and_evidence covers both info_seeking and the end of
    # the Q&A ladder, where we also make the ask.
    move = "counter_objection" if resistance_type in {
        "counterargument", "inoculation", "reactance", "source_derogation",
        "selective_exposure", "self_esteem"} else "answer_and_evidence"
    if resistance_type == "none" and state.turn == 0:
        move = "open_pitch"
    if state.turn >= 2 and move == "answer_and_evidence" and not state.claims_asked:
        move = "ask_for_meeting"

    ack = ""
    if resistance_type == "counterargument":
        ack = "Fair point — I'll narrow that and show the backing."
    elif resistance_type == "inoculation":
        ack = "You're right that this pattern often disappoints. Here's what's different."
    elif resistance_type == "avoidance":
        ack = "No pressure on timing."
    elif resistance_type == "reactance":
        ack = "I'll keep it short and factual."
    elif resistance_type == "information_seeking":
        ack = ""  # just answer

    cg_points = common_ground_points or []
    rationale = (f"Resistance '{resistance_type}'. Strategy '{strategy}'. "
                 f"Lead with {', '.join(picks) if picks else 'what matters most'}. "
                 + (f"Common ground: {', '.join(cg_points[:2])}. " if cg_points else "")
                 + (f"Shrinking ask: {concession}" if concession else "Standard ask."))

    return PitchIntent(
        move=move, resistance_addressed=resistance_type, strategy=strategy,
        claim_keys=picks, counter_to=(incoming_text or "")[:160],
        acknowledgment=ack, ask=ask, concession=concession,
        withheld=[], rationale=rationale,
    )


# ----------------------------------------------------------------- receiver objections
# Archetype objections the investor-style receiver uses to push back once before
# accepting. Picked by state, not the model. Each ties to a concrete follow-up key
# so the founder's verifier knows what to back.
_OBJECTIONS = [
    {
        "key": "why_convert",
        "trigger": "light_traction",
        "text": ("Traction is promising but early for a seed check. "
                 "What convinces you these pilots become ARR in the next 12 months?"),
    },
    {
        "key": "why_now",
        "trigger": "stage_gap",
        "text": ("The stage reads a touch early for us. "
                 "What has changed in the last quarter that makes this the right moment?"),
    },
    {
        "key": "why_you",
        "trigger": "team_light",
        "text": ("Market is interesting — convince me this team is the one to build it. "
                 "What have you done before that transfers?"),
    },
    {
        "key": "unverified_claim",
        "trigger": "unbacked",
        "text": ("One flag: a few of those figures don't carry a document yet. "
                 "Point me at something I can check on the strongest number?"),
    },
]


def craft_objection(fit, msg_claims: list, known: dict, raised_so_far: set[str]) -> dict | None:
    """Pick a single objection the receiver should raise before accepting.
    Returns None when nothing warrants pushback.

    Preference: unverified strong claim > light traction > stage gap > team."""
    # 1. Any numeric/figure claim without evidence -> ask for proof.
    unverified = [c for c in msg_claims if not getattr(c, "verified", False) and getattr(c, "statement", "")]
    if unverified and "unverified_claim" not in raised_so_far:
        obj = dict(_OBJECTIONS[3])
        obj["text"] = (f"One flag: '{unverified[0].statement}' doesn't carry a document yet. "
                       f"Point me at something I can check on that?")
        return obj
    # 2. Traction present but modest -> convert challenge.
    traction = str(known.get("traction") or "").lower()
    if traction and "why_convert" not in raised_so_far:
        return dict(_OBJECTIONS[0])
    # 3. Fit is good on paper but we want the "why now".
    if "why_now" not in raised_so_far:
        return dict(_OBJECTIONS[1])
    return None
