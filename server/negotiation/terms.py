"""Deterministic deal scoring. Code — never the LLM — decides what a package is worth to a
person and whether it clears their walk-away line. One value per issue; a broken hard limit
makes the whole package infeasible (utility None), so no agent can be talked past its limits.

A person's `terms` (from PersonalGraph.terms) look like:
    {"duration_min": {"ideal": 45, "reserve": 20, "weight": 0.35},
     "format":       {"scores": {"video": 1, "in_person": 0.6, "async_qa": 0.2}, "weight": 0.25},
     "slot":         {"weight": 0.25}}
plus reservation_utility (the walk-away line) and concession_beta.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..schemas import Issue, Pack


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def slot_fits(slot: dict | None, windows: list[dict], min_minutes: int = 0) -> bool:
    """True if the slot sits wholly inside one of the person's own availability windows."""
    if not slot or not slot.get("start") or not slot.get("end"):
        return False
    s, e = _dt(slot["start"]), _dt(slot["end"])
    if (e - s).total_seconds() / 60 < min_minutes:
        return False
    return any(_dt(w["start"]) <= s and e <= _dt(w["end"]) for w in windows or [])


def issue_score(issue: Issue, value: Any, spec: dict, windows: list[dict]) -> float | None:
    """Score one issue in [0, 1], or None if the value breaks this person's hard limit."""
    if value is None:
        return None
    if issue.kind == "numeric":
        v = float(value)
        ideal = float(spec.get("ideal", issue.max if issue.max is not None else v))
        reserve = float(spec.get("reserve", issue.min if issue.min is not None else ideal))
        if reserve == ideal:
            return 1.0
        if reserve < ideal:  # person wants a larger value; reserve is the floor
            if v < reserve:
                return None
            return min(1.0, (v - reserve) / (ideal - reserve))
        # reserve > ideal: person wants a smaller value; reserve is the ceiling
        if v > reserve:
            return None
        return min(1.0, (reserve - v) / (reserve - ideal))
    if issue.kind == "choice":
        scores = spec.get("scores", {})
        if not scores:
            return 1.0  # indifferent
        return scores.get(str(value))  # None => option this person won't accept
    if issue.kind == "slot":
        return 1.0 if slot_fits(value, windows) else None
    return 1.0


def utility(pack: Pack, package: dict, terms: dict, windows: list[dict]) -> float | None:
    """Weighted mean of per-issue scores, or None if any scored issue is infeasible."""
    specs = terms.get("terms", {})
    total_w = got = 0.0
    for issue in pack.issues:
        spec = specs.get(issue.name)
        if not spec:
            continue  # this person is indifferent to this issue
        w = float(spec.get("weight", 1.0))
        s = issue_score(issue, package.get(issue.name), spec, windows)
        if s is None:
            return None
        total_w += w
        got += w * s
    return (got / total_w) if total_w else 1.0


def acceptable(pack: Pack, package: dict, terms: dict, windows: list[dict]) -> bool:
    u = utility(pack, package, terms, windows)
    return u is not None and u >= float(terms.get("reservation_utility", 0.5))


def feasible_values(issue: Issue, spec: dict | None) -> list[Any]:
    """Enumerate the values the strategy may offer for a non-slot issue, best-first."""
    spec = spec or {}
    if issue.kind == "numeric":
        lo = int(issue.min if issue.min is not None else 0)
        hi = int(issue.max if issue.max is not None else lo)
        step = int(issue.step or 1)
        vals = list(range(lo, hi + 1, step))
        ideal = spec.get("ideal")
        if ideal is not None:
            vals.sort(key=lambda v: abs(v - float(ideal)))  # best (closest to ideal) first
        return vals
    if issue.kind == "choice":
        scores = spec.get("scores", {})
        opts = list(issue.options)
        if scores:
            opts = [o for o in opts if scores.get(o) is not None]
            opts.sort(key=lambda o: -scores.get(o, 0))
        return opts
    return []


def diff(old: dict | None, new: dict, pack: Pack) -> list[dict]:
    """Which terms changed between two packages, for the UI."""
    old = old or {}
    out = []
    for issue in pack.issues:
        a, b = old.get(issue.name), new.get(issue.name)
        if issue.kind == "slot":
            a, b = (a or {}).get("start"), (b or {}).get("start")
        if a != b and b is not None:
            out.append({"name": issue.name, "label": issue.label, "from": a, "to": b})
    return out
