"""The guard enforces, in code, every limit the LLM must not cross: persuasion level, each
person's walk-away line and reserves, disclosure tiers, and fit vetoes. It validates or repairs
the strategist's move and reports each intervention, so the UI can show code holding the line.
"""
from __future__ import annotations

from ..schemas import Plan
from . import strategy as S
from . import terms as T


def guard(plan: Plan, ctx: S.NegCtx, *, allowed_keys: set[str] | None = None,
          fit_veto: str | None = None, other_declined: bool = False) -> tuple[Plan, list[dict]]:
    """Return a safe move plus the list of interventions code made."""
    out: list[dict] = []
    fb = None

    def fallback() -> Plan:
        nonlocal fb
        if fb is None:
            fb = S.fallback(ctx)
        return fb

    def feasible(pkg: dict | None) -> bool:
        return bool(pkg) and T.utility(ctx.pack, pkg, ctx.my_terms, ctx.my_windows) is not None

    # 1. Fit veto is non-negotiable.
    if fit_veto:
        out.append({"rule": "fit veto", "detail": fit_veto})
        return Plan(move="decline", rationale=fit_veto), out

    # 2. Persuasion level.
    if ctx.persuasion_level == 0 and plan.move == "make_case":
        out.append({"rule": "persuasion level", "detail": "Level 0 is coordinate-only; dropped the pitch."})
        plan = fallback()
    if other_declined and ctx.persuasion_level <= 1 and plan.move not in ("accept", "end", "decline"):
        out.append({"rule": "accept the first no", "detail": f"Level {ctx.persuasion_level}: not pushing after a no."})
        return Plan(move="end", rationale="They said no; this pack accepts the first no."), out

    # 3. Accept only clears above my own walk-away line.
    if plan.move == "accept":
        if not (ctx.table and T.acceptable(ctx.pack, ctx.table, ctx.my_terms, ctx.my_windows)):
            out.append({"rule": "no accept below the line", "detail": "Their offer is below my limits; countering instead."})
            plan = fallback()

    # 4. Any package I put forward must be feasible for me and not under-sell me.
    if plan.move in ("propose", "counter", "concede", "trade"):
        if not feasible(plan.package):
            out.append({"rule": "repair offer", "detail": "LLM offer broke my own limits; used the safe offer."})
            plan = fallback()
        else:
            u = T.utility(ctx.pack, plan.package, ctx.my_terms, ctx.my_windows)
            r = float(ctx.my_terms.get("reservation_utility", 0.5))
            if u is not None and u < r:
                out.append({"rule": "no over-commitment", "detail": f"Offer below my line ({u:.2f}<{r:.2f}); raised it."})
                plan = fallback()

    # 5. Disclosure: only facts at or below my shareable tier leave the desk.
    if allowed_keys is not None and plan.claim_keys:
        kept = [k for k in plan.claim_keys if k in allowed_keys]
        held = [k for k in plan.claim_keys if k not in allowed_keys]
        if held:
            out.append({"rule": "disclosure", "detail": f"Held back {', '.join(held)} (above my sharing tier)."})
        plan.claim_keys = kept

    return plan, out
