"""Writes the demo personas to data/people/*.json. All people and firms are fictional.

Replace or extend them with real profiles (with real source URLs) via the UI's ingestion panel
or by editing the JSON. Run:  python scripts/seed_people.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "people"


def n(id, type, label, tier="fit", source="seed", **attrs):
    return {"id": id, "type": type, "label": label, "tier": tier, "source": source, "attrs": attrs}


def e(source, target, type):
    return {"source": source, "target": target, "type": type}


def person(pid, name, headline, meta, nodes, edges):
    has = [e(pid, x["id"], "HAS") for x in nodes if x["id"] != pid and x["type"] != "Evidence"
           and not any(ed["source"] == pid and ed["target"] == x["id"] for ed in edges)]
    return {"id": pid, "name": name, "headline": headline, "meta": meta, "nodes": nodes, "edges": edges + has}


people = []

# ---------------- Founders ----------------
people.append(person(
    "maya", "Maya Chen", "Founder and CEO, Lumen Labs", {"role": "founder", "company": "Lumen Labs"},
    [
        n("maya", "Person", "Maya Chen", "public", role="Founder and CEO", city="San Francisco"),
        n("co_lumen", "Work", "Lumen Labs", "public",
          one_liner="Lumen Labs gives engineering teams a test harness for LLM features, so prompt changes ship with the same confidence as code.",
          facts={"stage": "seed", "sectors": ["devtools", "ai infrastructure"], "geography": "US"},
          statements={"stage": "Lumen Labs is raising its seed round",
                      "sectors": "Lumen Labs builds developer tools and AI infrastructure",
                      "geography": "Lumen Labs is based in San Francisco"}),
        n("goal_raise", "Goal", "Raising a $2.5M seed round", "fit",
          facts={"check_ask": 500000},
          statements={"check_ask": "Maya is looking for a $500,000 check as part of a $2.5M seed round"}),
        n("tr_teams", "Traction", "40 paying teams", "fit",
          facts={"traction": "40 paying teams"},
          statements={"traction": "40 engineering teams pay for Lumen Labs today"}),
        n("tr_revenue", "Traction", "$18k monthly revenue", "approval",
          facts={"revenue": 18000}, statements={"revenue": "Lumen Labs makes $18,000 in monthly revenue"}),
        n("ach_team", "Achievement", "Built Stripe's internal test infrastructure", "fit",
          facts={"team": "ex-Stripe test infrastructure"},
          statements={"team": "Maya and her co-founder built Stripe's internal testing infrastructure for six years"}),
        n("ach_repo", "Achievement", "Open-source eval runner, 2,100 GitHub stars", "public",
          facts={"open_source": "eval runner"},
          statements={"open_source": "Lumen's open-source eval runner has 2,100 GitHub stars"}),
        n("ev_metrics", "Evidence", "Billing dashboard export, Oct 2026", "fit", kind="url", ref="https://example.com/lumen/metrics"),
        n("ev_linkedin", "Evidence", "Founders' LinkedIn profiles", "public", kind="url", ref="https://example.com/lumen/team"),
        n("ev_repo", "Evidence", "GitHub repository", "public", kind="repo", ref="https://github.com/example/lumen-eval"),
        n("ev_deck", "Evidence", "Seed deck, Nov 2026", "fit", kind="doc", ref="https://example.com/lumen/deck"),
        n("av_m1", "Availability", "Tue Nov 10, 10:00 to 12:00", "fit", start="2026-11-10T10:00", end="2026-11-10T12:00"),
        n("av_m2", "Availability", "Wed Nov 11, 14:00 to 17:00", "fit", start="2026-11-11T14:00", end="2026-11-11T17:00"),
        n("mandate_inv", "Mandate", "Agent mandate for investor outreach", "private",
          pack="founder_to_investor", shareable_tier="fit", max_meeting_min=45, allowed_proposals=["meeting", "call"]),
        n("int_climb", "Interest", "Climbing", "private"),
    ],
    [e("maya", "co_lumen", "FOUNDED"), e("tr_teams", "ev_metrics", "SUPPORTED_BY"),
     e("tr_revenue", "ev_metrics", "SUPPORTED_BY"), e("ach_team", "ev_linkedin", "SUPPORTED_BY"),
     e("ach_repo", "ev_repo", "SUPPORTED_BY"), e("goal_raise", "ev_deck", "SUPPORTED_BY"),
     e("co_lumen", "ev_deck", "SUPPORTED_BY")],
))

people.append(person(
    "rex", "Rex Calloway", "Founder, Quickflip AI", {"role": "founder", "company": "Quickflip AI", "adversarial": True},
    [
        n("rex", "Person", "Rex Calloway", "public", role="Founder"),
        n("co_quickflip", "Work", "Quickflip AI", "public",
          one_liner="Quickflip AI resells GPU time to startups.",
          facts={"stage": "seed", "sectors": ["ai infrastructure"], "geography": "US"},
          statements={"stage": "Quickflip AI is raising a seed round",
                      "sectors": "Quickflip AI is an AI infrastructure company",
                      "geography": "Quickflip AI is based in Austin"}),
        n("goal_rex", "Goal", "Raising a seed round", "fit", facts={"check_ask": 400000},
          statements={"check_ask": "Rex is looking for a $400,000 check"}),
        n("av_r1", "Availability", "Wed Nov 11, 15:00 to 16:00", "fit", start="2026-11-11T15:00", end="2026-11-11T16:00"),
        n("mandate_rex", "Mandate", "Agent mandate", "private", pack="*", shareable_tier="fit", max_meeting_min=60),
    ],
    [e("rex", "co_quickflip", "FOUNDED")],
))

# ---------------- Investors ----------------
people.append(person(
    "dana", "Dana Ruiz", "Partner, Northbeam Ventures", {"role": "investor", "firm": "Northbeam Ventures"},
    [
        n("dana", "Person", "Dana Ruiz", "public", role="Partner"),
        n("firm_northbeam", "Work", "Northbeam Ventures", "public",
          one_liner="Seed fund backing developer tools and AI infrastructure."),
        n("prio_dana", "Priority", "Thesis: pre-seed and seed devtools, AI infrastructure, data", "public",
          pack="founder_to_investor",
          prefs={"stages": ["pre-seed", "seed"], "sectors": ["devtools", "ai infrastructure", "data"],
                 "check_min": 250000, "check_max": 1000000, "geographies": ["US", "Canada"]}),
        n("limit_dana", "Limit", "Does not invest in hardware or crypto", "public",
          pack="founder_to_investor", prefs={"anti": ["hardware", "crypto"]}),
        n("cap_dana", "Priority", "Two intro calls a week", "private", pack="founder_to_investor", prefs={"weekly_calls": 2}),
        n("av_d1", "Availability", "Wed Nov 11, 15:00 to 16:30", "fit", start="2026-11-11T15:00", end="2026-11-11T16:30"),
        n("av_d2", "Availability", "Thu Nov 12, 09:00 to 10:00", "fit", start="2026-11-12T09:00", end="2026-11-12T10:00"),
        n("mandate_dana", "Mandate", "Gatekeeper mandate", "private",
          pack="founder_to_investor", shareable_tier="public", max_meeting_min=30, allowed_proposals=["meeting", "call"]),
        n("ev_nb_site", "Evidence", "Northbeam website", "public", kind="url", ref="https://example.com/northbeam"),
    ],
    [e("dana", "firm_northbeam", "PARTNER_AT"), e("prio_dana", "ev_nb_site", "SUPPORTED_BY")],
))

people.append(person(
    "sam", "Sam Okafor", "Partner, Harbor Growth", {"role": "investor", "firm": "Harbor Growth"},
    [
        n("sam", "Person", "Sam Okafor", "public", role="Partner"),
        n("firm_harbor", "Work", "Harbor Growth", "public", one_liner="Growth fund for software companies."),
        n("prio_sam_pub", "Priority", "Public focus: fintech and developer tools in the US", "public",
          pack="founder_to_investor", prefs={"sectors": ["fintech", "devtools"], "geographies": ["US"]}),
        n("prio_sam_fit", "Priority", "Invests at Series B and C, $5M to $20M checks", "fit",
          pack="founder_to_investor",
          prefs={"stages": ["series b", "series c"], "check_min": 5000000, "check_max": 20000000}),
        n("av_s1", "Availability", "Wed Nov 11, 14:00 to 15:00", "fit", start="2026-11-11T14:00", end="2026-11-11T15:00"),
        n("mandate_sam", "Mandate", "Gatekeeper mandate", "private",
          pack="founder_to_investor", shareable_tier="public", max_meeting_min=30),
    ],
    [e("sam", "firm_harbor", "PARTNER_AT")],
))

# ---------------- Friends ----------------
people.append(person(
    "asha", "Asha Rao", "Product designer, lives in the Mission", {"role": "friend"},
    [
        n("asha", "Person", "Asha Rao", "public", neighborhood="Mission, San Francisco"),
        n("int_a1", "Interest", "Climbing", "public"),
        n("int_a2", "Interest", "Dim sum", "public"),
        n("int_a3", "Interest", "Live music", "public"),
        n("av_a1", "Availability", "Sat Nov 14, 14:00 to 18:00", "public", start="2026-11-14T14:00", end="2026-11-14T18:00"),
        n("mandate_asha", "Mandate", "Agent mandate for friends", "private",
          pack="friend_to_friend", shareable_tier="public", max_meeting_min=180, allowed_proposals=["hangout"]),
    ],
    [{"source": "asha", "target": "priya", "type": "KNOWS", "closeness": 0.9, "last_met": "2026-10-03", "shared": ["climbing"]},
     {"source": "asha", "target": "ben", "type": "KNOWS", "closeness": 0.7, "last_met": "2026-08-22", "shared": ["board games"]}],
))

people.append(person(
    "priya", "Priya Natarajan", "Data scientist, climbs on weekends", {"role": "friend"},
    [
        n("priya", "Person", "Priya Natarajan", "public"),
        n("int_p1", "Interest", "Climbing", "public"),
        n("int_p2", "Interest", "Coffee", "public"),
        n("int_p3", "Interest", "Hiking", "public"),
        n("av_p1", "Availability", "Sat Nov 14, 13:00 to 17:00", "public", start="2026-11-14T13:00", end="2026-11-14T17:00"),
        n("mandate_priya", "Mandate", "Agent mandate for friends", "private",
          pack="friend_to_friend", shareable_tier="public", max_meeting_min=180, allowed_proposals=["hangout"]),
    ],
    [{"source": "priya", "target": "asha", "type": "KNOWS", "closeness": 0.9, "last_met": "2026-10-03", "shared": ["climbing"]}],
))

people.append(person(
    "ben", "Ben Adeyemi", "Teacher, board game host", {"role": "friend"},
    [
        n("ben", "Person", "Ben Adeyemi", "public"),
        n("int_b1", "Interest", "Board games", "public"),
        n("int_b2", "Interest", "Climbing", "public"),
        n("av_b1", "Availability", "Tue Nov 10, 18:00 to 21:00", "private", start="2026-11-10T18:00", end="2026-11-10T21:00"),
        n("av_b2", "Availability", "Thu Nov 12, 19:00 to 22:00", "private", start="2026-11-12T19:00", end="2026-11-12T22:00"),
        n("mandate_ben", "Mandate", "Agent mandate for friends", "private",
          pack="friend_to_friend", shareable_tier="public", max_meeting_min=180, allowed_proposals=["hangout"]),
    ],
    [{"source": "ben", "target": "asha", "type": "KNOWS", "closeness": 0.7, "last_met": "2026-08-22", "shared": ["board games"]}],
))

# ---------------- Event attendees ----------------
people.append(person(
    "leo", "Leo Park", "ML engineer building an agent eval tool", {"role": "attendee"},
    [
        n("leo", "Person", "Leo Park", "public", role="ML engineer"),
        n("work_leo", "Work", "Building an open-source agent eval tool", "public",
          one_liner="Leo is building an open-source tool that replays agent runs to find regressions.",
          facts={"topics": ["agents", "evals"], "offers": ["ml engineering", "eval design"]},
          statements={"topics": "Leo works on AI agents and evaluation",
                      "offers": "Leo can help with ML engineering and eval design"}),
        n("goal_leo", "Goal", "Looking for a product designer to team up with", "public",
          facts={"looking_for": ["product design"]}),
        n("av_l1", "Availability", "Today, 15:00 to 17:00", "public", start="2026-11-07T15:00", end="2026-11-07T17:00"),
        n("mandate_leo", "Mandate", "Agent mandate at events", "private",
          pack="attendee_to_attendee", shareable_tier="public", max_meeting_min=30, allowed_proposals=["session"]),
    ],
    [],
))

people.append(person(
    "nia", "Nia Okoro", "Product designer exploring agent UX", {"role": "attendee"},
    [
        n("nia", "Person", "Nia Okoro", "public", role="Product designer"),
        n("work_nia", "Work", "Designing interfaces for AI agents", "public",
          facts={"topics": ["agents", "design"], "offers": ["product design"]}),
        n("prio_nia", "Priority", "Wants to meet ML engineers working on agents", "public",
          pack="attendee_to_attendee", prefs={"topics": ["agents", "design"], "looking_for": ["ml engineering"]}),
        n("av_n1", "Availability", "Today, 16:00 to 16:45", "public", start="2026-11-07T16:00", end="2026-11-07T16:45"),
        n("mandate_nia", "Mandate", "Agent mandate at events", "private",
          pack="attendee_to_attendee", shareable_tier="public", max_meeting_min=30, allowed_proposals=["session"]),
    ],
    [],
))

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for p in people:
        (OUT / f"{p['id']}.json").write_text(json.dumps(p, indent=2))
        print("wrote", p["id"], len(p["nodes"]), "nodes", len(p["edges"]), "edges", file=sys.stderr)
