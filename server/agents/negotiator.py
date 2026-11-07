"""Shared bargaining turn used by both agents. The LLM picks the move (make the case, concede,
counter, accept, walk away); code builds a feasible package and the guard enforces every limit.
Offline, or on any LLM failure, the deterministic strategy decides, so the eval stays reproducible.
"""
from __future__ import annotations

from ..llm import llm
from ..negotiation import guard as G
from ..negotiation import strategy as S
from ..negotiation import terms as NT
from ..schemas import Pack, Plan

MOVES = {"accept", "counter", "concede", "trade", "propose", "decline", "make_case", "ask", "answer", "end"}


def make_ctx(pack: Pack, my_terms: dict, my_windows: list[dict], *, table: dict | None,
             my_last: dict | None, other_windows: list[dict], turn: int) -> S.NegCtx:
    return S.NegCtx(pack=pack, my_terms=my_terms, my_windows=my_windows, table=table, my_last=my_last,
                    other_windows=other_windows, round=max(0, turn - 1), max_rounds=max(1, pack.turn_limit // 2),
                    persuasion_level=pack.persuasion_level)


async def plan_move(ctx: S.NegCtx, *, role: str, emit, allowed_keys: set[str] | None = None,
                    fit_veto: str | None = None, other_declined: bool = False,
                    table_summary: str = "") -> tuple[Plan, list[dict]]:
    """Return a guarded bargaining move plus the interventions code made."""
    det = S.fallback(ctx)              # deterministic baseline (also the package source)
    plan = det
    if llm.enabled and not fit_veto:
        table_u = NT.utility(ctx.pack, ctx.table, ctx.my_terms, ctx.my_windows) if ctx.table else None
        out = await llm.json(
            "You choose the NEXT bargaining move for an AI agent in a scheduling/deal negotiation. "
            "You may: accept, counter, concede, decline. Accept only if their current offer is good enough. "
            "Never pressure, flatter or bluff. Choose only the move; code builds the actual terms within your "
            "person's private limits. Respond with JSON only.",
            f"Persuasion level: {ctx.persuasion_level}. Round {ctx.round + 1} of {ctx.max_rounds}. "
            f"Their current offer: {table_summary or ctx.table}. "
            f"That offer is worth {table_u:.2f} to us (we accept at or above {ctx.my_terms.get('reservation_utility', 0.5)}). "
            f"Our walk-away alternative: {ctx.my_terms.get('batna') or 'none'}. "
            'Return {"move": "accept|counter|concede|decline", "rationale": "one short line"}',
            fast=True, max_tokens=200)
        if isinstance(out, dict) and out.get("move") in MOVES:
            m = out["move"]
            pkg = ctx.table if m == "accept" else det.package
            plan = Plan(move=m, package=pkg or {}, rationale=str(out.get("rationale", ""))[:200])
    guarded, interventions = G.guard(plan, ctx, allowed_keys=allowed_keys,
                                     fit_veto=fit_veto, other_declined=other_declined)
    await emit("thinking", role=role, step="Strategist", detail=guarded.rationale or det.rationale,
               data={"move": guarded.move, "package": guarded.package})
    for iv in interventions:
        await emit("guard", role=role, rule=iv["rule"], detail=iv["detail"])
    return guarded, interventions
