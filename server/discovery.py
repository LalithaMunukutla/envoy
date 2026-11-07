"""Find who to talk to.

The user picks what they want from a session (an intent: raise, invest, make friends, meet at an
event). Discovery scans the network for people whose own intent fits, previews the match from the
user's side (fit on public info + real common ground), and ranks them. The user then starts the
right conversation with whoever they choose; the pack is implied by the intent pair.

Only the requester's own shareable facts and each counterpart's PUBLIC profile are used here, so a
preview never leaks anything a real pitch wouldn't.
"""
from __future__ import annotations

from .agents.fit import score_fit
from .intent import INTENTS, get_intents
from .knowledge.graph import get_graph, list_people, tiers_upto
from .matching import common_ground
from .packs import get_pack


def complements(my_intent: str) -> list[str]:
    """Intents on the other side that make a session with `my_intent` work."""
    v = INTENTS[my_intent]
    if v["role"] == "any":          # symmetric pack: the other person wants the same thing
        return [my_intent]
    return [k for k, vv in INTENTS.items() if vv["pack"] == v["pack"] and vv["role"] != v["role"]]


def discover(me_id: str, my_intent: str) -> dict:
    if my_intent not in INTENTS:
        raise KeyError(f"Unknown intent '{my_intent}'")
    me = get_graph(me_id)
    pack_id = INTENTS[my_intent]["pack"]
    pack = get_pack(pack_id)
    wanted = set(complements(my_intent))

    my_tiers = tiers_upto(me.mandate(pack_id)["shareable_tier"])
    my_facts, _ = me.facts(my_tiers, pack.shareable_types)

    matches = []
    for info in list_people(include_demo=True):          # the whole network, not just my own agents
        if info["id"] == me_id:
            continue
        them = get_graph(info["id"])
        if not (wanted & set(get_intents(them))):        # their declared intent must fit
            continue
        their_prefs = them.public_profile(pack_id)["prefs"]
        cg = common_ground(pack, my_facts, their_prefs, my_graph=me, their_graph=them)
        fit = score_fit(pack, my_facts, their_prefs)
        fit_preview = fit.score * fit.coverage
        score = round(0.6 * cg["score"] + 0.4 * fit_preview, 2)
        matches.append({
            "id": them.id, "name": them.name, "headline": them.headline,
            "pack": pack_id, "score": score, "points": cg["points"],
            "sample": bool(them.meta.get("demo")),
            "why": cg["points"][0] if cg["points"] else (them.headline or "Open to connecting"),
        })
    matches.sort(key=lambda m: -m["score"])
    return {"intent": my_intent, "pack": pack_id, "pack_title": pack.title, "matches": matches}
