"""Tests for the pitch loop: resistance classifier, pitch planner, deeper
investor flow (receiver raises an objection before accepting), and the Google
Calendar ICS parser."""
import asyncio
import os

os.environ["ENVOY_PACE"] = "0"
os.environ["ENVOY_MODE"] = "mock"
os.environ.setdefault("ENVOY_DB", "/tmp/envoy_pytest.db")

from server.broker import manager  # noqa: E402
from server.connectors import sources  # noqa: E402
from server.persuasion import (NegotiationState, classify_resistance,  # noqa: E402
                                craft_objection, plan_pitch)
from server.packs import get_pack  # noqa: E402


def test_classify_resistance_covers_core_patterns():
    assert classify_resistance("That's too early for us right now.", "decline", []) == "inoculation"
    assert classify_resistance("What's your traction? Any paying customers?", "request_info", ["traction"]) == "information_seeking"
    assert classify_resistance("Not for us — outside our focus area.", "decline", []) == "in_group_identity"
    assert classify_resistance("Maybe later, ping us in a few months.", "request_info", []) == "avoidance"
    assert classify_resistance("I'm not convinced — that number doesn't match what I see.", "request_info", []) == "counterargument"
    assert classify_resistance("Sounds great, let's lock a time.", "propose", []) == "none"


def test_plan_pitch_picks_strategy_and_claims():
    pack = get_pack("founder_to_investor")
    facts = {"stage": "seed", "sectors": ["ai", "devtools"], "check_ask": 500000,
             "traction": "40 paying teams"}
    state = NegotiationState(role="requester")
    # They asked about traction, and the tone shows counterargument
    state.observe("Traction is light; what convinces you these convert?", "request_info", ["traction"])
    pitch = plan_pitch(pack, facts, {}, state,
                        incoming_text="Traction is light; what convinces you these convert?",
                        resistance_type="counterargument")
    assert pitch.move == "counter_objection"
    assert pitch.strategy == "concede_narrow_then_reframe_with_evidence"
    assert "traction" in pitch.claim_keys
    assert pitch.acknowledgment  # non-empty for a concession-reframe


def test_plan_pitch_shrinks_ask_on_avoidance():
    pack = get_pack("founder_to_investor")
    state = NegotiationState(role="requester")
    state.observe("Maybe later, ping us in Q2.", "request_info", [])
    pitch = plan_pitch(pack, {"stage": "seed"}, {}, state,
                        incoming_text="Maybe later", resistance_type="avoidance")
    assert "async" in pitch.ask.lower() or "15" in pitch.ask
    assert pitch.concession


def test_craft_objection_targets_unverified_claims_first():
    class _Claim:
        def __init__(self, s, verified=False):
            self.statement = s
            self.verified = verified
    msg_claims = [_Claim("40 paying teams, $50k MRR", verified=False)]
    obj = craft_objection(None, msg_claims, {"traction": "40 paying teams"}, set())
    assert obj and obj["key"] == "unverified_claim"
    # Second call once we've raised that one: moves to the next objection in line.
    obj2 = craft_objection(None, [], {"traction": "40 paying teams"}, {"unverified_claim"})
    assert obj2 and obj2["key"] == "why_convert"


def test_investor_flow_now_raises_an_objection_before_accepting():
    """End-to-end: the maya->dana scenario should still book, but now with a
    receiver objection event and more turns than before the persuasion loop."""
    async def run():
        s = manager.start("founder_to_investor", "maya", "dana", "investor_fit", pace=0)
        await s.task
        if s.state == "approval":
            await s.approve("requester", True)
            await s.approve("receiver", True)
        return s
    s = asyncio.run(run())
    assert s.state == "booked"
    obj_events = [e for e in s.events if e["type"] == "objection"]
    assert any(e.get("status") == "raised" for e in obj_events), \
        "receiver should raise at least one objection on a level-2 pack"
    assert any(e.get("status") == "resolved" for e in obj_events), \
        "founder should resolve the objection with evidence"
    pitch_events = [e for e in s.events if e["type"] == "pitch"]
    assert pitch_events, "requester should emit a pitch strategist step"
    # Every outgoing message still backed (no verifier problems).
    for e in s.events:
        if e["type"] == "checks":
            assert not e["problems"], e["problems"]


def test_level_zero_pack_skips_the_pitch_loop():
    """friend_to_friend is coordinate-only; no objection event should fire."""
    async def run():
        s = manager.start("friend_to_friend", "asha", "priya", "friends_plan", pace=0)
        await s.task
        if s.state == "approval":
            await s.approve("requester", True)
            await s.approve("receiver", True)
        return s
    s = asyncio.run(run())
    assert s.state == "booked"
    objs = [e for e in s.events if e["type"] == "objection"]
    assert not objs


# --------------------------------------------------- Google Calendar
_ICS_SAMPLE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google//Google Calendar//EN
BEGIN:VEVENT
DTSTART:20261109T100000
DTEND:20261109T110000
SUMMARY:Team standup
END:VEVENT
BEGIN:VEVENT
DTSTART:20261109T140000
DTEND:20261109T150000
SUMMARY:1:1 with Dana
END:VEVENT
END:VCALENDAR
"""


def test_gcal_parses_ics_into_free_windows():
    specs = asyncio.run(sources.GoogleCalendarConnector().extract(_ICS_SAMPLE))
    assert specs, "should emit at least one free window"
    # Every spec is an Availability node with evidence
    assert all(s.type == "Availability" for s in specs)
    assert all(s.evidence_ref == "Google Calendar export" for s in specs)
    # Each has structured start/end
    assert all(s.attrs.get("start") and s.attrs.get("end") for s in specs)
    # On the busy day (2026-11-09), the free windows should avoid 10:00-11:00 and 14:00-15:00.
    monday = [s for s in specs if s.attrs["start"].startswith("2026-11-09")]
    assert monday, "monday 2026-11-09 should yield at least one free window"
    for s in monday:
        start_h = int(s.attrs["start"][11:13])
        end_h = int(s.attrs["end"][11:13])
        # Must not span the 10:00 busy block
        assert not (start_h < 10 < end_h), f"free window overlaps busy 10:00: {s.attrs}"


def test_gcal_empty_file_still_emits_a_week():
    specs = asyncio.run(sources.GoogleCalendarConnector().extract("BEGIN:VCALENDAR\nEND:VCALENDAR"))
    # Nothing to subtract -> a whole working week of 9-18 windows
    assert len(specs) >= 5
