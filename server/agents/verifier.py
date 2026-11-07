"""Honest by construction: a candidate message may only contain figures that appear in the claims it carries,
and may not use the pack's banned pressure phrases."""
from __future__ import annotations

import json
import re

from ..schemas import Claim, Pack, Proposal

NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?\s?(?:k|m|%|x|million|thousand)?", re.I)


def _norm(token: str) -> str:
    return re.sub(r"[^\d.kmx%]", "", token.lower().replace("million", "m").replace("thousand", "k"))


def check(text: str, claims: list[Claim], proposal: Proposal | None, pack: Pack) -> list[str]:
    problems = []
    allowed_src = " ".join(c.statement for c in claims) + " " + json.dumps(proposal.fields if proposal else {})
    allowed = {_norm(t) for t in NUM.findall(allowed_src)}
    for token in NUM.findall(text):
        t = _norm(token)
        bare = t.rstrip("kmx%")
        significant = bool(re.search(r"[kmx%$]", token.lower())) or (bare.replace(",", "").replace(".", "").isdigit() and float(bare or 0) >= 100)
        if significant and t not in allowed:
            problems.append(f"Figure '{token.strip()}' is not backed by any claim")
    low = text.lower()
    problems += [f"Uses pressure phrase '{p}'" for p in pack.pressure_terms if p in low]
    limit = int(pack.tone.get("max_words", 120) * 1.6)
    if len(text.split()) > limit:
        problems.append(f"Too long for this pack ({len(text.split())} words)")
    return problems
