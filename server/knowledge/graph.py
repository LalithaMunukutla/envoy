"""Personal Context Graph.

One typed, directed multigraph per person (NetworkX), persisted as JSON. Every fact is a node with:
  - type      Person | Work | Goal | Traction | Achievement | Interest | Availability | Priority | Limit | Mandate | Evidence | Style
  - tier      public | fit | approval | private  (what an agent may disclose, and when)
  - facts     machine-readable key/values the fit scorer understands (stage, sectors, interests, ...)
  - prefs     (Priority/Limit nodes) what this person looks for, scoped to a pack or "*"
  - source    where the fact came from (seed, interview, github, calendar)
Edges: SUPPORTED_BY (fact -> Evidence), KNOWS (person -> person, with closeness), FOUNDED, HAS, ...
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import networkx as nx

from ..config import PEOPLE_DIR

TIER_ORDER = {"public": 0, "fit": 1, "approval": 2, "private": 3}


def tiers_upto(tier: str) -> set[str]:
    return {t for t, i in TIER_ORDER.items() if i <= TIER_ORDER[tier]}


class PersonalGraph:
    def __init__(self, data: dict[str, Any]):
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.headline: str = data.get("headline", "")
        self.meta: dict[str, Any] = data.get("meta", {})
        self.g = nx.MultiDiGraph()
        for n in data.get("nodes", []):
            n = {"tier": "private", "attrs": {}, "source": "seed", **n}
            self.g.add_node(n["id"], **n)
        for e in data.get("edges", []):
            for end in (e["source"], e["target"]):
                if end not in self.g:  # someone this person knows, who has their own graph
                    self.g.add_node(end, id=end, type="Contact", label=end.title(), tier="private",
                                    attrs={}, source="relationship")
            self.g.add_edge(e["source"], e["target"], key=e["type"], **e)

    # ---------- persistence ----------
    @classmethod
    def load(cls, person_id: str) -> "PersonalGraph":
        return cls(json.loads((PEOPLE_DIR / f"{person_id}.json").read_text()))

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "headline": self.headline, "meta": self.meta,
            "nodes": [dict(d) for _, d in self.g.nodes(data=True)],
            "edges": [dict(d) for _, _, d in self.g.edges(data=True)],
        }

    def save(self, path: Path | None = None) -> None:
        (path or PEOPLE_DIR / f"{self.id}.json").write_text(json.dumps(self.to_json(), indent=2))

    # ---------- queries ----------
    def node(self, node_id: str) -> dict[str, Any] | None:
        return dict(self.g.nodes[node_id]) if node_id in self.g else None

    def nodes(self, types: Iterable[str] | None = None, tiers: Iterable[str] | None = None) -> list[dict]:
        types, tiers = set(types or []), set(tiers or [])
        return [
            dict(d) for _, d in self.g.nodes(data=True)
            if (not types or d["type"] in types) and (not tiers or d["tier"] in tiers)
        ]

    def evidence_for(self, node_id: str) -> dict | None:
        for _, target, key in self.g.out_edges(node_id, keys=True):
            if key == "SUPPORTED_BY":
                return self.node(target)
        return None

    def first_name(self) -> str:
        return self.name.split()[0]

    def facts(self, tiers: set[str], types: Iterable[str] | None = None) -> tuple[dict, dict]:
        """Flatten disclosable facts -> (facts, source_node_for_key)."""
        facts: dict[str, Any] = {}
        source: dict[str, str] = {}
        for n in self.nodes(types=types, tiers=tiers):
            for k, v in n["attrs"].get("facts", {}).items():
                facts[k] = v
                source[k] = n["id"]
            if n["type"] == "Interest":
                facts.setdefault("interests", []).append(n["label"].lower())
                source.setdefault("interests", n["id"])
            if n["type"] == "Availability":
                facts.setdefault("availability", []).append(
                    {"start": n["attrs"]["start"], "end": n["attrs"]["end"]})
                source.setdefault("availability", n["id"])
        return facts, source

    def prefs(self, pack_id: str, tiers: set[str]) -> dict[str, Any]:
        """What this person looks for, as seen by someone allowed `tiers`."""
        prefs: dict[str, Any] = {}
        for n in self.nodes(types=["Priority", "Limit"], tiers=tiers):
            if n["attrs"].get("pack", "*") in ("*", pack_id):
                prefs.update(n["attrs"].get("prefs", {}))
        for n in self.nodes(types=["Interest"], tiers=tiers):
            prefs.setdefault("interests", []).append(n["label"].lower())
        for n in self.nodes(types=["Availability"], tiers=tiers):
            prefs.setdefault("availability", []).append({"start": n["attrs"]["start"], "end": n["attrs"]["end"]})
        return prefs

    def mandate(self, pack_id: str) -> dict[str, Any]:
        base = {"shareable_tier": "fit", "max_meeting_min": 60, "allowed_proposals": ["meeting", "call", "hangout", "session"]}
        for n in self.nodes(types=["Mandate"]):
            if n["attrs"].get("pack", "*") in ("*", pack_id):
                base.update({k: v for k, v in n["attrs"].items() if k != "pack"})
        return base

    def terms(self, pack_id: str) -> dict[str, Any]:
        """This person's private bargaining preferences for a pack: per-issue ideal/reserve/
        weight plus the walk-away line. Falls back to indifferent defaults if none are set,
        so the engine works for any graph."""
        base = {"terms": {}, "reservation_utility": 0.5, "concession_beta": 1.0, "batna": ""}
        for node in self.nodes(types=["Terms"]):
            if node["attrs"].get("pack", "*") in ("*", pack_id):
                for k, v in node["attrs"].items():
                    if k != "pack":
                        base[k] = v
        return base

    def relationship(self, other_id: str) -> dict | None:
        if self.g.has_edge(self.id, other_id, key="KNOWS"):
            return dict(self.g.edges[self.id, other_id, "KNOWS"])
        return None

    def public_profile(self, pack_id: str) -> dict[str, Any]:
        """What a counterpart's agent may see before any conversation."""
        pub = {"public"}
        facts, _ = self.facts(pub, ["Person", "Work"])
        return {"id": self.id, "name": self.name, "headline": self.headline,
                "facts": facts, "prefs": self.prefs(pack_id, pub)}

    # ---------- mutation (ingestion) ----------
    def add_fact(self, node: dict[str, Any], evidence: dict[str, Any] | None = None) -> str:
        node = {"tier": "fit", "attrs": {}, "source": "manual", **node}
        self.g.add_node(node["id"], **node)
        if node["type"] not in ("Evidence",) and node["id"] != self.id:
            self.g.add_edge(self.id, node["id"], key="HAS", source=self.id, target=node["id"], type="HAS")
        if evidence:
            evidence = {"type": "Evidence", "tier": node["tier"], "attrs": {}, "source": node["source"], **evidence}
            self.g.add_node(evidence["id"], **evidence)
            self.g.add_edge(node["id"], evidence["id"], key="SUPPORTED_BY",
                            source=node["id"], target=evidence["id"], type="SUPPORTED_BY")
        return node["id"]

    # ---------- view for the UI ----------
    def view(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "headline": self.headline, "meta": self.meta,
            "nodes": [{"id": d["id"], "label": d["label"], "type": d["type"], "tier": d["tier"],
                       "source": d.get("source", "seed"), "attrs": d.get("attrs", {})}
                      for _, d in self.g.nodes(data=True)],
            "edges": [{"source": d["source"], "target": d["target"], "type": d["type"]}
                      for _, _, d in self.g.edges(data=True)],
        }


_cache: dict[str, PersonalGraph] = {}


def get_graph(person_id: str) -> PersonalGraph:
    if person_id not in _cache:
        _cache[person_id] = PersonalGraph.load(person_id)
    return _cache[person_id]


def list_people() -> list[dict[str, Any]]:
    out = []
    for path in sorted(PEOPLE_DIR.glob("*.json")):
        g = get_graph(path.stem)
        out.append({"id": g.id, "name": g.name, "headline": g.headline, "meta": g.meta,
                    "nodes": g.g.number_of_nodes(), "edges": g.g.number_of_edges()})
    return out
