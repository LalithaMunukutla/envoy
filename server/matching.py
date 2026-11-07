"""Common ground between two people, for any pack.

Before pitching, an agent looks for what it genuinely shares with the counterpart: overlapping
interests, sectors or topics, complementary needs (what I offer meets what they want), time both
are free, and people both know. The requester leads with these real points instead of a generic
ask. Code finds them (deterministic, explainable); the model only phrases them.

Everything here uses only what the requester may legitimately see: its own facts and the
counterpart's PUBLIC profile. It never reaches past the disclosure tiers.
"""
from __future__ import annotations

from .agents.fit import _set, best_overlap
from .knowledge.graph import PersonalGraph
from .schemas import Pack


def _known_people(graph: PersonalGraph | None) -> set[str]:
    if graph is None:
        return set()
    return {t for _, t, k in graph.g.edges(keys=True) if k == "KNOWS"}


def common_ground(pack: Pack, my_facts: dict, their_prefs: dict, *,
                  my_graph: PersonalGraph | None = None,
                  their_graph: PersonalGraph | None = None) -> dict:
    """Return shared points plus short human-readable lines for the UI and the pitch."""
    overlaps: list[dict] = []   # category -> shared values, from the pack's overlap/member criteria
    for c in pack.fit_criteria:
        if c.kind not in ("overlap", "member") or c.pref is None:
            continue
        mine, theirs = _set(my_facts.get(c.fact)), _set(their_prefs.get(c.pref))
        hit = sorted(mine & theirs)
        if hit:
            overlaps.append({"label": c.label, "kind": c.kind, "values": hit})

    shared_interests = sorted(_set(my_facts.get("interests")) & _set(their_prefs.get("interests")))

    minutes, slot = best_overlap(my_facts.get("availability") or [], their_prefs.get("availability") or [])
    mutual_time = {"minutes": int(minutes), "slot": slot} if slot else None

    mutual_connections = sorted(_known_people(my_graph) & _known_people(their_graph))

    points: list[str] = []
    for o in overlaps:
        label = o["label"] if o["label"].lower().startswith(("shared", "complementary", "mutual")) \
            else f"Shared {o['label'].lower()}"
        points.append(f"{label}: {', '.join(o['values'])}")
    if shared_interests and not any("interest" in p.lower() for p in points):
        points.append(f"Both into {', '.join(shared_interests)}")
    if mutual_time:
        points.append(f"{mutual_time['minutes']} minutes you are both free")
    if mutual_connections:
        points.append(f"{len(mutual_connections)} mutual connection(s)")

    score = min(1.0, 0.25 * len(overlaps) + 0.15 * bool(shared_interests)
                + 0.1 * bool(mutual_time) + 0.2 * bool(mutual_connections))
    return {"overlaps": overlaps, "shared_interests": shared_interests, "mutual_time": mutual_time,
            "mutual_connections": mutual_connections, "points": points, "score": round(score, 2)}


def phrases(cg: dict, limit: int = 3) -> list[str]:
    """Flat list of shared terms a generator may weave into a message (no times/counts)."""
    words: list[str] = []
    for o in cg.get("overlaps", []):
        words += o["values"]
    words += cg.get("shared_interests", [])
    return list(dict.fromkeys(words))[:limit]
