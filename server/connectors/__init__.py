"""Connector registry: one place that knows every source and ingests from any of them.

`ingest(graph, source, payload)` runs a connector, writes the facts it found into the person's
graph (each with its evidence), records the connection on the graph's meta, and saves. The API
and UI only ever call this, so adding a source is: write a Connector, register it here.
"""
from __future__ import annotations

import time

from ..knowledge.graph import PersonalGraph
from .base import Connector, apply
from .sources import (GithubConnector, InstagramConnector, InterviewConnector,
                      LinkedInConnector, PortfolioConnector)

REGISTRY: dict[str, Connector] = {
    c.name: c for c in (
        LinkedInConnector(), PortfolioConnector(), InstagramConnector(),
        GithubConnector(), InterviewConnector(),
    )
}


def list_connectors() -> list[dict]:
    return [{"name": c.name, "label": c.label, "kind": c.kind, "accepts": c.accepts,
             "placeholder": c.placeholder} for c in REGISTRY.values()]


def _record(graph: PersonalGraph, source: str, specs: list, ref: str) -> list[dict]:
    ts = time.time()
    added = [a for spec in specs if (a := apply(graph, source, spec, ts=ts))]
    conns = graph.meta.setdefault("connectors", {})
    hist = conns.get(source, {}).get("imports", 0) + 1
    conns[source] = {"at": ts, "added": len(added), "imports": hist, "ref": ref[:120]}
    graph.meta.setdefault("ingest_log", []).append(
        {"source": source, "at": ts, "new": sum(a["status"] == "new" for a in added),
         "updated": sum(a["status"] == "updated" for a in added)})
    graph.save()
    return added


async def ingest(graph: PersonalGraph, source: str, payload: str) -> list[dict]:
    if source not in REGISTRY:
        raise KeyError(f"Unknown connector '{source}'. Options: {', '.join(REGISTRY)}")
    specs = await REGISTRY[source].extract(payload)
    return _record(graph, source, specs, payload.strip() if source != "interview" else "interview")


async def ingest_file(graph: PersonalGraph, source: str, filename: str, data: bytes) -> list[dict]:
    if source not in REGISTRY:
        raise KeyError(f"Unknown connector '{source}'. Options: {', '.join(REGISTRY)}")
    specs = await REGISTRY[source].extract_file(filename, data)
    return _record(graph, source, specs, filename or source)
