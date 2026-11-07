"""Receiver agent: protects its human's time. Per turn: analyze -> check claims and evidence ->
score fit against private priorities -> decide (ask, accept, counter, decline) -> phrase."""
from __future__ import annotations

from datetime import datetime, timedelta

from ..knowledge.graph import PersonalGraph
from ..llm import llm
from ..schemas import AgentMessage, Pack, Proposal, Sender
from . import templates, verifier
from .fit import best_overlap, score_fit
from .requester import Emit, analyze

ALL_TIERS = {"public", "fit", "approval", "private"}


class ReceiverAgent:
    role = "receiver"

    def __init__(self, me: PersonalGraph, them: PersonalGraph, pack: Pack, session_id: str, emit: Emit):
        self.me, self.them, self.pack, self.sid, self.emit = me, them, pack, session_id, emit
        self.prefs = me.prefs(pack.id, ALL_TIERS)
        self.mandate = me.mandate(pack.id)
        self.windows = self.prefs.get("availability", [])
        self.known: dict = {}
        self.asked: set[str] = set()
        self.pending: Proposal | None = None
        self.turn = 0
        self.last_fit = None
        self.interest_history: list[float] = []

    async def think(self, step: str, detail: str, data=None):
        await self.emit("thinking", role=self.role, step=step, detail=detail, data=data)

    def _reply(self, act, text, proposal=None, requested=None) -> AgentMessage:
        return AgentMessage(session_id=self.sid, pack=self.pack.id, turn=0, act=act, text=text,
                            proposal=proposal, requested=requested or [],
                            sender=Sender(agent_id=f"agent:{self.me.id}", role="receiver", acting_for=self.me.name))

    def choose_slot(self, proposal: Proposal) -> tuple[str, dict | None, int]:
        dur = min(int(proposal.fields.get("duration_min", 30)), int(self.mandate.get("max_meeting_min", 60)))
        offered = [proposal.fields["slot"]] if proposal.fields.get("slot") else proposal.fields.get("windows", [])
        minutes, slot = best_overlap(offered, self.windows)
        if slot and minutes >= dur:
            s = datetime.fromisoformat(slot["start"])
            return "accept", {"start": slot["start"], "end": (s + timedelta(minutes=dur)).isoformat(timespec="minutes")}, dur
        if self.windows:
            s = datetime.fromisoformat(self.windows[0]["start"])
            return "counter_propose", {"start": self.windows[0]["start"], "end": (s + timedelta(minutes=dur)).isoformat(timespec="minutes")}, dur
        return "decline", None, dur

    async def step(self, msg: AgentMessage) -> AgentMessage:
        self.turn += 1
        await analyze(self.role, msg, self.emit)

        # 1. Claims and evidence
        bundle = {e.id for e in msg.evidence}
        for c in msg.claims:
            c.verified = bool(c.evidence and c.evidence in bundle)
            if c.key:
                self.known[c.key] = c.value
        problems = [p for p in verifier.check(msg.text, msg.claims, msg.proposal, self.pack) if "Figure" in p or "pressure" in p]
        verified = sum(1 for c in msg.claims if c.verified)
        await self.emit("checks", role=self.role, turn=msg.turn, verified=verified, total=len(msg.claims),
                        unverified=[c.statement for c in msg.claims if not c.verified], problems=problems)
        await self.think("Evidence check", f"{verified} of {len(msg.claims)} claims carry evidence"
                         + (f"; flagged: {problems[0]}" if problems else "."))

        # 2. Fit against private priorities
        fit = score_fit(self.pack, self.known, self.prefs)
        self.last_fit = fit
        interest = round(1 + 4 * fit.score * fit.coverage - (0.5 if problems else 0), 1)
        self.interest_history.append(max(1.0, interest))
        await self.emit("fit", role=self.role, label="Fit against private priorities", fit=fit.as_dict())
        await self.emit("score", role=self.role, turn=msg.turn, interest=self.interest_history[-1],
                        history=self.interest_history)
        if msg.proposal:
            self.pending = msg.proposal

        # 3. Decide
        first = self.me.first_name()
        if fit.veto:
            await self.think("Decision policy", f"Decline: {fit.veto}.")
            return self._reply("decline", templates.receiver_text("decline", me=self.me, them=self.them, pack=self.pack,
                                                                  reason=f"{first} doesn't invest in this area."))
        if fit.coverage >= self.pack.min_coverage and fit.score < self.pack.fit_threshold:
            reason = f"{first} focuses on a different {templates.join([f.lower() for f in fit.failed])}."
            await self.think("Decision policy", f"Decline: mismatch on {', '.join(fit.failed)} (score {fit.score:.2f}).")
            return self._reply("decline", templates.receiver_text("decline", me=self.me, them=self.them, pack=self.pack, reason=reason))

        missing = [(c.fact, c.ask) for c in fit.missing if c.fact not in self.asked]
        diligence = [(k, self.pack.diligence[k]) for k in self.pack.diligence
                     if k not in self.known and k not in self.asked] if self.pack.persuasion_level >= 2 else []
        # Ask ONE thing per turn (the most decision-relevant), so diligence reads as a real exchange.
        to_ask = (missing + diligence)[:1]
        if to_ask:
            keys = [k for k, _ in to_ask]
            self.asked.update(keys)
            ack = f"Noted: {', '.join(c.statement for c in msg.claims if c.verified)}. " if any(c.verified for c in msg.claims) else ""
            await self.think("Question generator", f"Interest {self.interest_history[-1]}/5. Asking about {keys[0]}: "
                             "it's the answer that would move the decision most right now.")
            return self._reply("request_info", ack + templates.receiver_text("request_info", me=self.me, them=self.them,
                                                                             pack=self.pack, questions=[q for _, q in to_ask]),
                               requested=keys)

        if self.pending:
            act, slot, dur = self.choose_slot(self.pending)
            if act == "decline":
                await self.think("Decision policy", "Fits, but no shared time.")
                return self._reply("decline", templates.receiver_text("decline", me=self.me, them=self.them, pack=self.pack,
                                                                      reason=f"{first} has no free time soon."))
            fields = {**self.pending.fields, "slot": slot, "duration_min": dur}
            fields.pop("windows", None)
            await self.think("Decision policy", f"{'Accept' if act == 'accept' else 'Counter-propose'}: fit {fit.score:.2f}, "
                             f"{templates.when(slot)} is free on {first}'s calendar. Waiting for both humans to approve.")
            return self._reply(act, templates.receiver_text(act, me=self.me, them=self.them, pack=self.pack, slot=slot),
                               proposal=Proposal(type=self.pending.type, fields=fields))

        await self.think("Decision policy", "Good fit so far; inviting a concrete proposal.")
        return self._reply("answer", templates.receiver_text("answer", me=self.me, them=self.them, pack=self.pack))

    async def summary(self) -> str:
        fit = self.last_fit
        base = (f"{self.them.name} ({self.them.headline}). Fit {fit.score:.0%} on "
                f"{', '.join(r['label'].lower() for r in fit.rows if r['status'] == 'match')}." if fit else self.them.name)
        if llm.enabled:
            out = await llm.json("Write a two-sentence briefing for a busy person about a meeting request their AI agent screened. "
                                 "Plain, factual, no hype.", f"Facts: {base}. Known: {self.known}. Return {{\"summary\": \"...\"}}",
                                 fast=True, max_tokens=200)
            if isinstance(out, dict) and out.get("summary"):
                return out["summary"]
        return base
