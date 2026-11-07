"""Pluggable connector layer.

A connector turns one external source (a LinkedIn profile, a portfolio site, an Instagram
bio, a GitHub account, an interview) into typed facts for a person's Personal Context Graph.
Every connector implements the same tiny contract, so the UI and API treat all sources the
same and a real OAuth/API connector can later replace a mock one without touching anything else.

Honest by construction: every fact a connector emits carries an Evidence reference back to its
source (a URL or handle), and starts at a conservative disclosure tier the person can raise.
Offline (no API key) every connector falls back to deterministic pattern extraction, so the
graph can always be built without a model.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..knowledge.graph import PersonalGraph
from ..llm import llm

# Node types a connector may contribute (must line up with the graph / fit scorer).
FACT_TYPES = ["Person", "Work", "Goal", "Traction", "Achievement", "Interest", "Availability", "Priority"]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40] or "item"


@dataclass
class FactSpec:
    """One fact a connector extracted, plus the evidence that backs it."""
    type: str
    label: str
    tier: str = "fit"
    facts: dict[str, Any] = field(default_factory=dict)       # machine-readable keys the fit scorer reads
    statements: dict[str, str] = field(default_factory=dict)  # key -> human sentence
    attrs: dict[str, Any] = field(default_factory=dict)       # extra node attrs (one_liner, start/end, ...)
    evidence_ref: str = ""                                    # URL / handle / quote
    evidence_kind: str = "url"                                # url | repo | profile | interview_quote
    evidence_summary: str = ""


class Connector(ABC):
    """One external source. A connector is either text-based (`extract`) or file-based
    (`extract_file`), declared by `accepts`."""
    name: str          # stable id, e.g. "linkedin"
    label: str         # human label, e.g. "LinkedIn"
    kind: str          # how the UI should ask for input: "file" | "handle" | "text" | "profile_url"
    accepts: str = "text"  # "text" | "handle" | "file"
    placeholder: str = ""  # hint text for the UI input

    async def extract(self, payload: str) -> list[FactSpec]:
        """Turn a payload (pasted text, a profile URL, or a handle) into facts."""
        raise NotImplementedError(f"{self.name} takes a file upload, not text")

    async def extract_file(self, filename: str, data: bytes) -> list[FactSpec]:
        """Turn an uploaded file (PDF, ZIP export, JSON) into facts."""
        raise NotImplementedError(f"{self.name} takes text, not a file")

    # --- shared helpers for subclasses ---
    async def llm_facts(self, system: str, text: str, *, evidence_ref: str, evidence_kind: str,
                        evidence_summary: str, default_tier: str = "fit") -> list[FactSpec] | None:
        """Extract facts with the model; returns None so the caller can fall back offline."""
        if not (llm.enabled and text.strip()):
            return None
        out = await llm.json(
            system + f" Allowed types: {FACT_TYPES}. Only facts the person states about themselves. "
            "Never infer health, religion, ethnicity, politics, sexuality or finances beyond what is "
            "stated for a professional goal. For each fact give a short label, a tier (public for things "
            "on a public profile, fit otherwise), optional machine `facts` (keys like stage, sectors, "
            "geography, check_ask, topics, offers, looking_for, interests, traction) and an optional "
            "one-line `statement`.",
            f'Source text:\n"""{text[:6000]}"""\n'
            'Return {"facts": [{"type": "...", "label": "...", "tier": "public|fit", '
            '"facts": {}, "statement": "..."}]}',
            max_tokens=1200)
        if not isinstance(out, dict) or not isinstance(out.get("facts"), list):
            return None
        specs: list[FactSpec] = []
        for it in out["facts"]:
            if not isinstance(it, dict) or it.get("type") not in FACT_TYPES or not it.get("label"):
                continue
            key = next(iter(it.get("facts") or {}), None)
            specs.append(FactSpec(
                type=it["type"], label=str(it["label"])[:80], tier=it.get("tier", default_tier),
                facts=it.get("facts") or {},
                statements={key: it["statement"]} if key and it.get("statement") else {},
                evidence_ref=evidence_ref, evidence_kind=evidence_kind, evidence_summary=evidence_summary))
        return specs


def apply(graph: PersonalGraph, source: str, spec: FactSpec, *, ts: float | None = None) -> dict | None:
    """Merge one FactSpec into a graph.

    Intelligent update, not blind append: if a matching node already exists we refresh it
    (merge attrs, add the new source/evidence) instead of duplicating. Interests carry a
    `weight` and `last_seen`, so a signal seen again across uploads gets stronger and a stale
    one can be aged out later. Every node records which source(s) it came from.
    """
    import time as _time
    if spec.type not in FACT_TYPES or not spec.label:
        return None
    ts = ts if ts is not None else _time.time()
    nid = f"{spec.type.lower()}_{slug(spec.label)}"
    existing = graph.node(nid)

    attrs = dict(existing["attrs"]) if existing else {}
    attrs.update(spec.attrs)
    if spec.facts:
        merged = dict(attrs["facts"]) if isinstance(attrs.get("facts"), dict) else {}
        merged.update(spec.facts)
        attrs["facts"] = merged
    if spec.statements:
        merged = dict(attrs["statements"]) if isinstance(attrs.get("statements"), dict) else {}
        merged.update(spec.statements)
        attrs["statements"] = merged

    prov = attrs.get("provenance", {})
    seen = int(prov.get(source, {}).get("count", 0)) + 1
    prov[source] = {"count": seen, "last_seen": ts}
    attrs["provenance"] = prov
    if spec.type == "Interest":  # recency/frequency weight across all uploads
        attrs["weight"] = round(float(attrs.get("weight", 0.0)) + 1.0, 2)
        attrs["last_seen"] = ts

    tier = existing["tier"] if existing else spec.tier
    evidence = None
    if spec.evidence_ref:
        evidence = {"id": f"ev_{nid}_{slug(source)}",
                    "label": spec.evidence_summary or f"{source}: {spec.evidence_ref[:80]}",
                    "attrs": {"kind": spec.evidence_kind, "ref": spec.evidence_ref}}
    graph.add_fact({"id": nid, "type": spec.type, "label": spec.label, "tier": tier, "source": source,
                    "attrs": attrs}, evidence=evidence)
    return {"id": nid, "type": spec.type, "label": spec.label, "tier": tier,
            "status": "updated" if existing else "new"}
