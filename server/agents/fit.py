"""Deterministic, explainable fit scoring driven entirely by the scenario pack.
The LLM never decides fit; it only phrases what this module concludes."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..schemas import Criterion, Pack


def _set(v: Any) -> set[str]:
    if v is None:
        return set()
    items = v if isinstance(v, (list, tuple, set)) else [v]
    return {str(i).strip().lower() for i in items}


def best_overlap(a: list[dict], b: list[dict]) -> tuple[float, dict | None]:
    best, slot = 0.0, None
    for x in a or []:
        for y in b or []:
            s = max(datetime.fromisoformat(x["start"]), datetime.fromisoformat(y["start"]))
            e = min(datetime.fromisoformat(x["end"]), datetime.fromisoformat(y["end"]))
            minutes = (e - s).total_seconds() / 60
            if minutes > best:
                best, slot = minutes, {"start": s.isoformat(timespec="minutes"), "end": e.isoformat(timespec="minutes")}
    return best, slot


def evaluate(c: Criterion, facts: dict, prefs: dict) -> tuple[float | None, str]:
    f = facts.get(c.fact)
    if f is None:
        return None, "not shared yet"
    if c.kind in ("member", "overlap"):
        p = prefs.get(c.pref)
        if p is None:
            return None, "their preference is not public"
        hit = _set(f) & _set(p)
        return (1.0, ", ".join(sorted(hit))) if hit else (0.0, f"{', '.join(sorted(_set(f)))} is outside {', '.join(sorted(_set(p)))}")
    if c.kind == "in_range":
        lo, hi = prefs.get(c.pref_min), prefs.get(c.pref_max)
        if lo is None or hi is None:
            return None, "their range is not public"
        ok = lo <= float(f) <= hi
        return (1.0 if ok else 0.0), f"${float(f):,.0f} vs ${lo:,.0f} to ${hi:,.0f}"
    if c.kind == "slot_overlap":
        p = prefs.get(c.pref)
        if p is None:
            return None, "their calendar is not shared"
        minutes, slot = best_overlap(f, p)
        return (1.0 if minutes >= c.min_minutes else 0.0), (f"{int(minutes)} minutes overlap" if slot else "no overlap")
    return None, "unknown criterion"


@dataclass
class FitResult:
    score: float
    coverage: float
    veto: str | None
    rows: list[dict] = field(default_factory=list)
    missing: list[Criterion] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"score": round(self.score, 2), "coverage": round(self.coverage, 2), "veto": self.veto,
                "rows": self.rows, "missing": [c.name for c in self.missing], "failed": self.failed}


def score_fit(pack: Pack, facts: dict, prefs: dict) -> FitResult:
    total = sum(c.weight for c in pack.fit_criteria) or 1.0
    known_w = got = 0.0
    rows, missing, failed = [], [], []
    for c in pack.fit_criteria:
        s, detail = evaluate(c, facts, prefs)
        status = "unknown" if s is None else ("match" if s >= 1 else "miss")
        rows.append({"name": c.name, "label": c.label, "status": status, "detail": detail, "weight": c.weight})
        if s is None:
            missing.append(c)
        else:
            known_w += c.weight
            got += c.weight * s
            if s < 1:
                failed.append(c.label)
    veto = None
    for v in pack.vetoes:
        s, detail = evaluate(v, facts, prefs)
        if s == 1.0:
            veto = f"{v.label}: {detail}"
    missing.sort(key=lambda c: -c.weight)
    return FitResult(score=(got / known_w) if known_w else 0.0, coverage=known_w / total,
                     veto=veto, rows=rows, missing=missing, failed=failed)
