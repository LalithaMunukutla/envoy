"""Choosing a bargaining move. The LLM picks the move and phrasing; this module also provides a
deterministic concession strategy used offline and whenever the LLM call fails, so the demo always
negotiates for real and the offline eval stays reproducible.

Concession target: T(t) = 1 - (1-R)*(t/T)^(1/beta).
beta < 1 holds out and concedes late (investors); beta > 1 concedes early (friends).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import product
from typing import Any

from ..schemas import Pack, Plan
from . import terms as T


@dataclass
class NegCtx:
    pack: Pack
    my_terms: dict
    my_windows: list[dict]
    table: dict | None = None          # offer currently on the table (from the other side)
    my_last: dict | None = None        # my own previous offer
    other_windows: list[dict] = field(default_factory=list)  # windows the other side has revealed
    round: int = 0
    max_rounds: int = 4
    persuasion_level: int = 2


def concession_target(r_util: float, t: int, total: int, beta: float) -> float:
    total = max(total, 1)
    return 1.0 - (1.0 - r_util) * (min(t, total) / total) ** (1.0 / max(beta, 0.05))


def _slot_candidates(ctx: NegCtx, minutes: int) -> list[dict]:
    """A few concrete slots of `minutes` length that sit inside my windows, preferring overlap
    with any window the other side has shown."""
    out: list[dict] = []
    for w in ctx.my_windows:
        ws, we = T._dt(w["start"]), T._dt(w["end"])
        step = timedelta(minutes=30)
        cur = ws
        while cur + timedelta(minutes=minutes) <= we:
            out.append({"start": cur.isoformat(timespec="minutes"),
                        "end": (cur + timedelta(minutes=minutes)).isoformat(timespec="minutes")})
            cur += step

    def overlaps_other(slot: dict) -> int:
        s, e = T._dt(slot["start"]), T._dt(slot["end"])
        return 1 if any(T._dt(o["start"]) < e and s < T._dt(o["end"]) for o in ctx.other_windows) else 0

    out.sort(key=lambda s: (-overlaps_other(s), s["start"]))
    return out[:6]


def _candidates(ctx: NegCtx) -> list[dict]:
    """Feasible packages I could offer, capped and deterministically ordered."""
    specs = ctx.my_terms.get("terms", {})
    axes, names = [], []
    minutes = 30
    for issue in ctx.pack.issues:
        if issue.kind == "slot":
            continue
        vals = T.feasible_values(issue, specs.get(issue.name))
        if vals:
            names.append(issue.name)
            axes.append(vals)
            if issue.name == "duration_min":
                minutes = int(vals[0])
    packages = []
    combos = list(product(*axes))[:200] if axes else [()]
    slots = [None]
    if any(i.kind == "slot" for i in ctx.pack.issues):
        # use each combo's duration when building slots; approximate with the ideal duration here
        slots = _slot_candidates(ctx, minutes) or [None]
    for combo in combos:
        base = dict(zip(names, combo))
        dur = int(base.get("duration_min", minutes))
        for slot in (_slot_candidates(ctx, dur) or [None]) if slots != [None] else [None]:
            pkg = dict(base)
            if slot is not None:
                pkg["slot"] = slot
            packages.append(pkg)
    # keep only packages feasible for me, best-utility first
    scored = [(T.utility(ctx.pack, p, ctx.my_terms, ctx.my_windows), p) for p in packages]
    scored = [(u, p) for u, p in scored if u is not None]
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:300]]


def _distance_to(table: dict, pkg: dict, pack: Pack) -> float:
    """How far a package is from the table, so a concession moves toward the other side."""
    d = 0.0
    for issue in pack.issues:
        a, b = table.get(issue.name), pkg.get(issue.name)
        if issue.kind == "slot":
            if a and b:
                d += abs((T._dt(a["start"]) - T._dt(b["start"])).total_seconds()) / 3600.0
            elif a or b:
                d += 5
        elif issue.kind == "numeric" and a is not None and b is not None:
            rng = (issue.max or 1) - (issue.min or 0) or 1
            d += abs(float(a) - float(b)) / rng
        elif a != b:
            d += 1
    return d


def fallback(ctx: NegCtx) -> Plan:
    """Deterministic move: accept if the table clears my target, else concede toward it, else walk."""
    r = float(ctx.my_terms.get("reservation_utility", 0.5))
    beta = float(ctx.my_terms.get("concession_beta", 1.0))
    target = concession_target(r, ctx.round, ctx.max_rounds, beta)
    cands = _candidates(ctx)
    if not cands:
        return Plan(move="decline", rationale="No package I could offer clears my own limits.")

    table_u = T.utility(ctx.pack, ctx.table, ctx.my_terms, ctx.my_windows) if ctx.table else None
    if table_u is not None and table_u >= min(target, r) and table_u >= r:
        return Plan(move="accept", package=ctx.table,
                    rationale=f"Their offer is worth {table_u:.2f} to me, at or above my line of {r:.2f}.")

    # Deadline: out of rounds and nothing on the table clears my line -> no deal.
    if ctx.round >= ctx.max_rounds and not (table_u is not None and table_u >= r):
        return Plan(move="decline", rationale="Out of rounds with no offer that clears my limits.")

    # choose my counter: feasible package at/above target, closest to the table
    at_target = [p for p in cands if (T.utility(ctx.pack, p, ctx.my_terms, ctx.my_windows) or 0) >= target]
    pool = at_target or cands  # if nothing meets target yet, offer my best
    if ctx.table:
        pool = sorted(pool, key=lambda p: _distance_to(ctx.table, p, ctx.pack))
    best = pool[0]
    u = T.utility(ctx.pack, best, ctx.my_terms, ctx.my_windows) or 0

    # out of rounds and still nothing both-acceptable: hold at my line, which the other side can refuse
    move = "propose" if ctx.my_last is None and ctx.table is None else "counter"
    if ctx.my_last is not None:
        changed = T.diff(ctx.my_last, best, ctx.pack)
        gained = any((T.utility(ctx.pack, best, ctx.my_terms, ctx.my_windows) or 0) >
                     (T.utility(ctx.pack, ctx.my_last, ctx.my_terms, ctx.my_windows) or 0) for _ in [0])
        if changed and not gained:
            move = "concede"
    return Plan(move=move, package=best,
                rationale=f"Offer worth {u:.2f} to me (target {target:.2f}); moving toward their ask.")
