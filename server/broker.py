"""Session broker: the only path between agents. Validates every message, enforces the pack,
runs the firewall, drives the state machine, holds approvals, and logs everything."""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime
from typing import Any

from . import config, store
from .agents import firewall
from .agents.receiver import ReceiverAgent
from .agents.requester import RequesterAgent
from .knowledge.graph import get_graph
from .packs import get_pack
from .schemas import AgentMessage

TERMINAL = {"booked", "declined", "flagged", "timed_out", "not_contacted"}
LEVEL0_FORBIDDEN_ACTS = {"introduce"}


class Session:
    def __init__(self, pack_id: str, requester_id: str, receiver_id: str, scenario_id: str | None = None,
                 pace: float | None = None):
        self.id = uuid.uuid4().hex[:8]
        self.scenario_id = scenario_id
        self.pack = get_pack(pack_id)
        self.req_graph, self.rec_graph = get_graph(requester_id), get_graph(receiver_id)
        self.pace = config.PACE if pace is None else pace
        self.events: list[dict[str, Any]] = []
        self.cond = asyncio.Condition()
        self.state = "handshake"
        self.turn = 0
        self.transcript: list[AgentMessage] = []
        self.agreed: AgentMessage | None = None
        self.approvals: dict[str, bool | None] = {"requester": None, "receiver": None}
        self.requester = RequesterAgent(self.req_graph, self.rec_graph, self.pack, self.id, self.emit)
        self.receiver = ReceiverAgent(self.rec_graph, self.req_graph, self.pack, self.id, self.emit)
        self.task: asyncio.Task | None = None

    # ---------- events ----------
    async def emit(self, etype: str, **payload: Any) -> None:
        event = {"seq": len(self.events), "ts": time.time(), "type": etype, **payload}
        self.events.append(event)
        store.log_event(self.id, event["seq"], etype, payload)
        async with self.cond:
            self.cond.notify_all()

    async def set_state(self, state: str, reason: str = "") -> None:
        self.state = state
        await self.emit("state", state=state, reason=reason)

    async def pause(self, factor: float = 1.0) -> None:
        if self.pace:
            await asyncio.sleep(self.pace * factor)

    def info(self) -> dict[str, Any]:
        return {"id": self.id, "scenario": self.scenario_id, "pack": self.pack.model_dump(),
                "state": self.state, "approvals": self.approvals,
                "requester": {"id": self.req_graph.id, "name": self.req_graph.name, "headline": self.req_graph.headline},
                "receiver": {"id": self.rec_graph.id, "name": self.rec_graph.name, "headline": self.rec_graph.headline}}

    # ---------- validation ----------
    async def admit(self, msg: AgentMessage) -> bool:
        self.turn += 1
        msg.turn = self.turn
        msg = AgentMessage.model_validate(msg.model_dump())  # schema check at the boundary
        flags = await firewall.scan(msg.text)
        if self.pack.persuasion_level == 0 and msg.act in LEVEL0_FORBIDDEN_ACTS:
            flags.append("Pack escalation: a pitch is not allowed in a level 0 pack")
        if not msg.is_ai:
            flags.append("Agent did not disclose that it is an AI")
        self.transcript.append(msg)
        await self.emit("message", message=msg.model_dump(), flags=flags)
        if flags:
            for f in flags:
                await self.emit("flag", severity="high", role=msg.sender.role, text=f)
            await self.set_state("flagged", f"Blocked a message from {msg.sender.acting_for}'s agent: {flags[0]}.")
            return False
        return True

    # ---------- main loop ----------
    async def run(self) -> None:
        try:
            await self._run()
        except Exception as exc:  # surface errors to the UI instead of hanging
            await self.emit("error", text=f"{type(exc).__name__}: {exc}")
            await self.set_state("declined", "Session stopped because of an internal error.")
        finally:
            await self.reflect()

    async def _run(self) -> None:
        await self.emit("session", **self.info())
        await self.emit("handshake", text=f"Both agents disclosed they are AI, acting for {self.req_graph.name} and "
                        f"{self.rec_graph.name}. Pack agreed: {self.pack.title} (persuasion level {self.pack.persuasion_level}).")
        await self.pause()
        if not await self.requester.prepare():
            await self.set_state("not_contacted", "The requester's agent chose not to reach out after the fit pre-check.")
            return
        await self.set_state("discovery")
        await self.pause()

        incoming: AgentMessage | None = None
        while True:
            # requester turn
            msg = await self.requester.step(incoming)
            if not await self.admit(msg):
                return
            if msg.act == "accept":
                self.agreed = msg
                break
            if msg.act in ("decline", "end"):
                await self.set_state("declined", f"{self.req_graph.first_name()}'s agent withdrew.")
                return
            if msg.proposal:
                await self.set_state("proposal")
            await self.pause(1.4)
            if self.turn >= self.pack.turn_limit:
                await self.set_state("timed_out", f"Reached the pack's limit of {self.pack.turn_limit} turns.")
                return

            # receiver turn
            reply = await self.receiver.step(msg)
            if not await self.admit(reply):
                return
            if reply.act == "accept":
                self.agreed = reply
                break
            if reply.act == "decline":
                await self.set_state("declined", reply.text)
                return
            if reply.act == "request_info" and self.state == "discovery":
                await self.set_state("qa")
            await self.pause(1.4)
            if self.turn >= self.pack.turn_limit:
                await self.set_state("timed_out", f"Reached the pack's limit of {self.pack.turn_limit} turns.")
                return
            incoming = reply

        await self.set_state("approval", "Agents agreed. Waiting for both people to approve.")
        await self.emit("approval", proposal=self.agreed.proposal.model_dump(),
                        summary_for_receiver=await self.receiver.summary(),
                        summary_for_requester=self._requester_summary())

    def _requester_summary(self) -> str:
        claims = [c for m in self.transcript if m.sender.role == "requester" for c in m.claims]
        backed = sum(1 for c in claims if c.evidence)
        asked = sorted(self.receiver.asked)
        return (f"{self.rec_graph.name}'s agent accepted after reviewing {len(claims)} facts ({backed} with evidence)"
                + (f" and asking about {', '.join(asked)}." if asked else "."))

    async def approve(self, role: str, approve: bool) -> None:
        if self.state != "approval":
            raise ValueError(f"Session is '{self.state}', not waiting for approval")
        self.approvals[role] = approve
        who = self.req_graph.name if role == "requester" else self.rec_graph.name
        await self.emit("approval_vote", role=role, approve=approve, who=who)
        if approve is False:
            await self.set_state("declined", f"{who} chose not to meet.")
        elif all(self.approvals.values()):
            await self.set_state("booked", "Both people approved. Invite ready.")
            await self.emit("booked", slot=self.agreed.proposal.fields.get("slot"), ics=f"/api/sessions/{self.id}/invite.ics")
        if self.state in TERMINAL:
            await self.reflect()

    async def reflect(self) -> None:
        if self.state not in TERMINAL or getattr(self, "_reflected", False):
            return
        self._reflected = True
        asked = sorted(self.receiver.asked)
        text = (f"{self.pack.title} session with {self.rec_graph.name} ended {self.state}. "
                + (f"They asked about {', '.join(asked)}." if asked else ""))
        store.remember(self.req_graph.id, text, 6 if self.state == "booked" else 4, [self.rec_graph.id, self.pack.id])
        store.remember(self.rec_graph.id, f"{self.req_graph.name} reached out ({self.pack.title}); outcome {self.state}.",
                       5, [self.req_graph.id, self.pack.id])
        await self.emit("reflection", text=text)

    def ics(self) -> str:
        slot = self.agreed.proposal.fields["slot"]
        fmt = lambda s: datetime.fromisoformat(s).strftime("%Y%m%dT%H%M%S")
        title = f"{self.pack.outcome.get('label', 'Meeting')}: {self.req_graph.name} and {self.rec_graph.name}"
        return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Envoy//EN", "BEGIN:VEVENT",
                            f"UID:{self.id}@envoy", f"DTSTART:{fmt(slot['start'])}", f"DTEND:{fmt(slot['end'])}",
                            f"SUMMARY:{title}", "DESCRIPTION:Arranged by Envoy agents and approved by both people.",
                            "END:VEVENT", "END:VCALENDAR", ""])


class SessionManager:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    def start(self, pack: str, requester: str, receiver: str, scenario: str | None = None, pace: float | None = None) -> Session:
        s = Session(pack, requester, receiver, scenario, pace)
        self.sessions[s.id] = s
        s.task = asyncio.create_task(s.run())
        return s

    def get(self, sid: str) -> Session:
        if sid not in self.sessions:
            raise KeyError(sid)
        return self.sessions[sid]


manager = SessionManager()
