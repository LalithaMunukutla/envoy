"""Typed contracts shared by agents, broker and API. The broker refuses anything that does not validate."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Tier = Literal["public", "fit", "approval", "private"]
Act = Literal[
    "introduce", "question", "answer", "request_info", "propose",
    "counter_propose", "accept", "decline", "end",
]
Role = Literal["requester", "receiver"]


class Evidence(BaseModel):
    id: str
    kind: str  # url | repo | calendar | doc | interview_quote
    ref: str
    summary: str = ""


class Claim(BaseModel):
    id: str
    statement: str
    key: str | None = None  # fact key the receiver can score (stage, sectors, ...)
    value: Any = None
    evidence: str | None = None
    verified: bool | None = None


class Proposal(BaseModel):
    type: str  # meeting | call | hangout | session
    fields: dict[str, Any] = Field(default_factory=dict)


class Sender(BaseModel):
    agent_id: str
    role: Role
    acting_for: str


class AgentMessage(BaseModel):
    session_id: str
    pack: str
    turn: int
    sender: Sender
    is_ai: bool = True
    act: Act
    text: str = Field(max_length=2000)
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    requested: list[str] = Field(default_factory=list)
    proposal: Proposal | None = None


class Intent(BaseModel):
    """The planner's contract with the generator: phrase this, add nothing."""
    act: Act
    claim_ids: list[str] = Field(default_factory=list)
    withheld: list[str] = Field(default_factory=list)
    requested: list[str] = Field(default_factory=list)
    proposal: Proposal | None = None
    note: str = ""


Move = Literal[
    "make_case", "ask", "answer", "propose", "counter",
    "concede", "trade", "accept", "decline", "end",
]


class Issue(BaseModel):
    """One negotiable term in a pack's deal (a field of the Proposal package)."""
    name: str
    label: str
    kind: Literal["numeric", "choice", "slot"]
    min: float | None = None
    max: float | None = None
    step: float = 1.0
    options: list[str] = Field(default_factory=list)
    unit: str = ""


class Plan(BaseModel):
    """The strategist's contract with the generator: a bargaining move to phrase.
    The LLM proposes it; the guard validates/repairs it; the writer only phrases it."""
    move: Move = "counter"
    package: dict[str, Any] = Field(default_factory=dict)  # one value per issue
    claim_keys: list[str] = Field(default_factory=list)
    asks: list[str] = Field(default_factory=list)
    rationale: str = ""


class Criterion(BaseModel):
    name: str
    label: str
    kind: Literal["member", "overlap", "in_range", "slot_overlap"]
    weight: float = 1.0
    fact: str
    pref: str | None = None
    pref_min: str | None = None
    pref_max: str | None = None
    min_minutes: int = 60
    ask: str = ""


class Pack(BaseModel):
    id: str
    title: str
    symmetric: bool
    persuasion_level: int = Field(ge=0, le=2)
    outcome: dict[str, Any]
    proposal_defaults: dict[str, Any] = Field(default_factory=dict)
    fit_threshold: float
    min_coverage: float
    turn_limit: int
    issues: list[Issue] = Field(default_factory=list)  # negotiable deal terms
    fit_criteria: list[Criterion]
    vetoes: list[Criterion] = Field(default_factory=list)
    diligence: dict[str, str] = Field(default_factory=dict)  # fact key -> question
    shareable_types: list[str]
    tone: dict[str, Any] = Field(default_factory=dict)
    decline_style: str
    approval: str
    pressure_terms: list[str] = Field(default_factory=list)
