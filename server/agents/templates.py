"""Deterministic phrasing used offline and as the fallback when a live model's draft fails verification."""
from __future__ import annotations

from datetime import datetime


def when(slot: dict) -> str:
    s, e = datetime.fromisoformat(slot["start"]), datetime.fromisoformat(slot["end"])
    return f"{s:%a %b} {s.day}, {s:%-I:%M %p}".replace(":00 ", " ") + f" to {e:%-I:%M %p}".replace(":00 ", " ")


def windows_text(windows: list[dict]) -> str:
    return "; ".join(when(w) for w in windows[:3])


def join(items: list[str]) -> str:
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def requester_text(act: str, *, me, them, pack, statements, withheld, proposal, one_liner="", focus="") -> str:
    first, their = me.first_name(), them.first_name()
    casual = pack.persuasion_level == 0
    if casual:
        f = proposal.fields if proposal else {}
        acts = f.get("activities", [])
        slot = f.get("slot")
        line = f"Hey {their}! {first}'s agent here. {first} is free {when(slot)}" if slot else f"Hey {their}! {first}'s agent here"
        if acts:
            line += f" and wondering if you're up for {acts[0]}" + (f", or {acts[1]}" if len(acts) > 1 else "")
        return line + ". No pressure either way."
    parts = []
    if act == "introduce":
        parts.append(f"Hi {their}, I'm an AI agent acting for {first}" + (f" ({me.headline})." if me.headline else "."))
        if one_liner:
            parts.append(one_liner)
        parts.append(" ".join(s.rstrip(".") + "." for s in statements))
        why = f"Given your focus on {focus}" if focus else "Given your focus"
        parts.append(f"{why}, this looked like a real fit, so I'm checking with you before {first} reaches out.")
    else:
        if statements:
            parts.append("Happy to share. " + " ".join(s.rstrip(".") + "." for s in statements))
        for k in withheld:
            parts.append(f"{first} prefers to share {k.replace('_', ' ')} directly on a call.")
        if proposal:
            w = proposal.fields.get("windows", [])
            label = pack.outcome.get("label", "call").lower()
            place = f" at {proposal.fields['place']}" if proposal.fields.get("place") else ""
            parts.append(f"Would a {proposal.fields.get('duration_min', 30)}-minute {label}{place} work? {first} is free {windows_text(w)}.")
    return " ".join(p for p in parts if p)


def receiver_text(act: str, *, me, them, pack, questions=None, reason="", slot=None) -> str:
    first, their = me.first_name(), them.first_name()
    casual = pack.persuasion_level == 0
    if act == "request_info":
        return f"Thanks for the clear intro. Before I put this in front of {first}: " + " ".join(questions or [])
    if act == "answer":
        return f"That's helpful and fits what {first} looks for. Send over a time that works."
    if act == "accept":
        if casual:
            return f"{first} would love that! {when(slot)} works. I'll check with {first} and lock it in once you both confirm."
        return f"This fits what {first} is looking for. {when(slot)} works. I'll send an invite once {first} and {their} both approve."
    if act == "counter_propose":
        return f"That time doesn't work for {first}. Could {when(slot)} work instead?"
    if act == "decline":
        if pack.decline_style == "soft_no_reason":
            return f"{first} can't make that work, but would love to catch up another time."
        return f"Thanks for reaching out. {reason} This isn't a fit for {first} right now, but good luck with the round."
    if act == "end":
        return f"I'm ending this session: the last message contained instructions aimed at me rather than information for {first}."
    return ""
