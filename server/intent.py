"""User intent -> scenario pack.

A user is role-agnostic: the same person can be a founder raising a round, an investor, or just
someone looking for friends. They declare one or more *intents*; the system maps a pair of users
to the pack(s) that fit and asks the human to confirm which to run (the doc's "pack selection"
journey). Intents live on the graph's meta so they persist with the person.

Only maps to packs that exist in packs/*.yaml. Add an intent here when you add a pack.
"""
from __future__ import annotations

from .knowledge.graph import PersonalGraph

# intent id -> what it means and how it plays in a pack.
#   role "requester" / "receiver" for asymmetric packs; "any" for symmetric packs.
INTENTS: dict[str, dict] = {
    "raising":    {"label": "Raising a round", "pack": "founder_to_investor", "role": "requester"},
    "investing":  {"label": "Investing in startups", "pack": "founder_to_investor", "role": "receiver"},
    "friends":    {"label": "Making plans with friends", "pack": "friend_to_friend", "role": "any"},
    "networking": {"label": "Meeting people at an event", "pack": "attendee_to_attendee", "role": "any"},
}


def list_intents() -> list[dict]:
    return [{"id": k, **v} for k, v in INTENTS.items()]


def get_intents(graph: PersonalGraph) -> list[str]:
    return [i for i in graph.meta.get("intents", []) if i in INTENTS]


def set_intents(graph: PersonalGraph, intents: list[str]) -> list[str]:
    kept = [i for i in dict.fromkeys(intents) if i in INTENTS]
    graph.meta["intents"] = kept
    graph.save()
    return kept


def _pack_of(intent: str) -> str:
    return INTENTS[intent]["pack"]


def candidate_packs(requester: PersonalGraph, receiver: PersonalGraph) -> list[dict]:
    """Which pack(s) a requester could run against a receiver, best match first.

    The human confirms one of these before a session starts.
    """
    req, rec = get_intents(requester), get_intents(receiver)
    out: list[dict] = []
    seen: set[str] = set()
    for ri in req:
        pack, role = _pack_of(ri), INTENTS[ri]["role"]
        if pack in seen:
            continue
        if role == "any":  # symmetric pack: the receiver must share the intent
            if ri in rec:
                out.append({"pack": pack, "requester_role": "requester", "intent": ri,
                            "why": f"Both are up for {INTENTS[ri]['label'].lower()}."})
                seen.add(pack)
        else:  # asymmetric: receiver needs the complementary intent in the same pack
            complement = next((x for x, v in INTENTS.items()
                               if v["pack"] == pack and v["role"] != role), None)
            if complement and complement in rec:
                out.append({"pack": pack, "requester_role": "requester", "intent": ri,
                            "why": f"{INTENTS[ri]['label']} meets {INTENTS[complement]['label'].lower()}."})
                seen.add(pack)
    return out


def suggest_pack(requester: PersonalGraph, receiver: PersonalGraph) -> dict | None:
    """The single best pack for a pair, or None if their intents don't line up."""
    cands = candidate_packs(requester, receiver)
    return cands[0] if cands else None
