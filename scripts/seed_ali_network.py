"""Seed a demo network around the real 'ali' agent so the matching story is visible end to end:

  - ali gains the machine facts his LinkedIn export couldn't carry (startup stage/sector/check,
    plus a couple of interests and availability) so he is actually matchable. These are clearly
    sourced 'demo' and can be edited or replaced by a real interview.
  - zara  (investor)  invests in immersive/XR + legaltech across Pakistan/South Asia  -> Ali CONNECTS
  - bilal (friend)    shares interests + overlapping free time                        -> Ali CONNECTS
  - fatima (founder)  agri-hardware, a totally different space                         -> no path

The three counterparts are tagged demo:true, so they stay out of 'Your agents' but show up as
sample people to connect with in discovery.

Run:  python scripts/seed_ali_network.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "people"


def node(id, type, label, tier="fit", source="demo", **attrs):
    return {"id": id, "type": type, "label": label, "tier": tier, "source": source, "attrs": attrs}


def edge(s, t, ty, **extra):
    return {"source": s, "target": t, "type": ty, **extra}


def has_edges(pid, nodes, edges):
    return edges + [edge(pid, n["id"], "HAS") for n in nodes
                    if n["id"] != pid and n["type"] != "Evidence"
                    and not any(e["source"] == pid and e["target"] == n["id"] for e in edges)]


def write(doc):
    (OUT / f"{doc['id']}.json").write_text(json.dumps(doc, indent=2))
    print("wrote", doc["id"], len(doc["nodes"]), "nodes", file=sys.stderr)


# ---------------------------------------------------------------- enrich ali
def enrich_ali():
    path = OUT / "ali.json"
    if not path.exists():
        print("ali.json not found; create the 'Ali' agent and connect LinkedIn first.", file=sys.stderr)
        return
    d = json.loads(path.read_text())
    have = {n["id"] for n in d["nodes"]}
    add_nodes = [
        node("goal_ali_raise", "Goal", "Raising a $250k pre-seed for immersive + legaltech", "fit", "demo",
             facts={"stage": "seed", "sectors": ["immersive tech", "augmented reality", "legaltech"],
                    "geography": "Pakistan", "check_ask": 250000},
             statements={"stage": "Ali's startup is raising a pre-seed round",
                         "sectors": "Ali builds immersive tech (AR/VR) and legaltech",
                         "geography": "Ali's startup is based in Pakistan",
                         "check_ask": "Ali is raising a $250,000 pre-seed round"}),
        node("ev_goal_ali_raise", "Evidence", "Pre-seed one-pager", "fit", "demo",
             kind="doc", ref="https://example.com/ali/one-pager"),
        node("tr_ali_pilots", "Traction", "3 enterprise AR pilots", "fit", "demo",
             facts={"traction": "3 enterprise AR pilots"},
             statements={"traction": "Ali's team is running 3 paid enterprise AR pilots, with 2 converting to annual contracts"}),
        node("ev_tr_ali_pilots", "Evidence", "Pilot agreements (redacted)", "fit", "demo",
             kind="doc", ref="https://example.com/ali/pilots"),
        node("ach_ali_team", "Achievement", "Two-time XR founder; ex-Technical Director", "fit", "demo",
             facts={"team": "two-time XR founder and ex-Technical Director"},
             statements={"team": "Ali is a two-time XR founder and former Technical Director who has shipped AR products on HoloLens and Unity"}),
        node("ev_ach_ali_team", "Evidence", "LinkedIn profile", "public", "demo",
             kind="profile", ref="https://linkedin.com/in/ali"),
        node("int_ali_hiking", "Interest", "Hiking", "public", "demo", facts={"interests": ["hiking"]}),
        node("int_ali_coffee", "Interest", "Coffee", "public", "demo", facts={"interests": ["coffee"]}),
        node("av_ali_wed", "Availability", "Wed Nov 11, 15:00 to 17:00", "fit", "demo",
             start="2026-11-11T15:00", end="2026-11-11T17:00"),
        node("av_ali_sat", "Availability", "Sat Nov 14, 14:00 to 18:00", "public", "demo",
             start="2026-11-14T14:00", end="2026-11-14T18:00"),
    ]
    add_nodes = [n for n in add_nodes if n["id"] not in have]
    if not add_nodes:
        print("ali already enriched", file=sys.stderr)
        return
    d["nodes"] += add_nodes
    added_ids = {n["id"] for n in add_nodes}
    existing = {(e["source"], e["target"], e["type"]) for e in d["edges"]}
    for src, ev in [("goal_ali_raise", "ev_goal_ali_raise"), ("tr_ali_pilots", "ev_tr_ali_pilots"),
                    ("ach_ali_team", "ev_ach_ali_team")]:
        if ev in added_ids and (src, ev, "SUPPORTED_BY") not in existing:
            d["edges"].append(edge(src, ev, "SUPPORTED_BY"))
    d["edges"] += [edge("ali", n["id"], "HAS") for n in add_nodes
                   if n["type"] != "Evidence" and ("ali", n["id"], "HAS") not in existing]
    d.setdefault("meta", {})["intents"] = sorted(set(d["meta"].get("intents", []) + ["raising", "friends"]))
    path.write_text(json.dumps(d, indent=2))
    print(f"enriched ali with {len(add_nodes)} nodes", file=sys.stderr)


# ---------------------------------------------------------------- investor (match)
def build_zara():
    pid = "zara"
    nodes = [
        node(pid, "Person", "Zara Sheikh", "public", role="Partner", city="Dubai"),
        node("firm_indus", "Work", "Indus Ventures", "public",
             one_liner="Indus Ventures backs pre-seed and seed founders in immersive tech, legaltech and applied AI across Pakistan and South Asia."),
        node("prio_zara", "Priority", "Thesis: immersive, legaltech & AI across South Asia", "public",
             pack="founder_to_investor",
             prefs={"stages": ["pre-seed", "seed"],
                    "sectors": ["immersive tech", "augmented reality", "virtual reality", "legaltech", "ai", "devtools"],
                    "check_min": 100000, "check_max": 750000,
                    "geographies": ["pakistan", "lahore, punjab, pakistan", "south asia", "uae", "global"]}),
        node("limit_zara", "Limit", "Avoids crypto, gambling and pure hardware", "public",
             pack="founder_to_investor", prefs={"anti": ["crypto", "gambling", "hardware"]}),
        node("ach_zara_exit", "Achievement", "Ex-founder; sold an AR startup to a global platform", "public",
             statements={"bg": "Zara founded and exited an AR startup before investing"}),
        node("ach_zara_portfolio", "Achievement", "Led 14 immersive-tech seed rounds", "fit",
             statements={"portfolio": "Zara has led 14 immersive-tech seed rounds (ImmersaLearn, LexAI, VRSurgeon)"}),
        node("cap_zara", "Priority", "Three intro calls a week", "private",
             pack="founder_to_investor", prefs={"weekly_calls": 3}),
        node("av_z1", "Availability", "Wed Nov 11, 15:30 to 16:30", "fit",
             start="2026-11-11T15:30", end="2026-11-11T16:30"),
        node("av_z2", "Availability", "Thu Nov 12, 10:00 to 11:00", "fit",
             start="2026-11-12T10:00", end="2026-11-12T11:00"),
        node("mandate_zara", "Mandate", "Gatekeeper mandate", "private",
             pack="founder_to_investor", shareable_tier="public", max_meeting_min=30,
             allowed_proposals=["meeting", "call"]),
        node("ev_indus_site", "Evidence", "Indus Ventures website", "public", kind="url", ref="https://example.com/indus"),
        node("ev_zara_exit", "Evidence", "Acquisition announcement", "public", kind="url", ref="https://example.com/zara/exit"),
    ]
    edges = [edge("zara", "firm_indus", "PARTNER_AT"),
             edge("prio_zara", "ev_indus_site", "SUPPORTED_BY"),
             edge("ach_zara_exit", "ev_zara_exit", "SUPPORTED_BY")]
    write({"id": pid, "name": "Zara Sheikh", "headline": "Partner, Indus Ventures",
           "meta": {"demo": True, "role": "investor", "firm": "Indus Ventures", "intents": ["investing"]},
           "nodes": nodes, "edges": has_edges(pid, nodes, edges)})


# ---------------------------------------------------------------- friend (match)
def build_bilal():
    pid = "bilal"
    nodes = [
        node(pid, "Person", "Bilal Khan", "public", neighborhood="Gulberg, Lahore"),
        node("work_bilal", "Work", "Product Manager at Careem", "public",
             one_liner="Bilal is a product manager who hikes most weekends."),
        node("int_b1", "Interest", "Hiking", "public", facts={"interests": ["hiking"]}),
        node("int_b2", "Interest", "Coffee", "public", facts={"interests": ["coffee"]}),
        node("int_b3", "Interest", "Climbing", "public", facts={"interests": ["climbing"]}),
        node("int_b4", "Interest", "Photography", "public", facts={"interests": ["photography"]}),
        node("av_b1", "Availability", "Sat Nov 14, 13:00 to 17:00", "public",
             start="2026-11-14T13:00", end="2026-11-14T17:00"),
        node("mandate_bilal", "Mandate", "Agent mandate for friends", "private",
             pack="friend_to_friend", shareable_tier="public", max_meeting_min=240,
             allowed_proposals=["hangout"]),
    ]
    edges = [edge("bilal", "ali", "KNOWS", closeness=0.6, last_met="2026-09-20", shared=["hiking"])]
    write({"id": pid, "name": "Bilal Khan", "headline": "Product manager, hikes on weekends",
           "meta": {"demo": True, "role": "friend", "intents": ["friends"]},
           "nodes": nodes, "edges": has_edges(pid, nodes, edges)})


# ---------------------------------------------------------------- founder, different space (no match)
def build_fatima():
    pid = "fatima"
    nodes = [
        node(pid, "Person", "Fatima Noor", "public", role="Founder", city="Faisalabad"),
        node("co_agrisense", "Work", "AgriSense", "public",
             one_liner="AgriSense builds soil-sensor hardware and crop analytics for smallholder farms.",
             facts={"stage": "seed", "sectors": ["agritech", "hardware", "iot"], "geography": "Pakistan"},
             statements={"stage": "AgriSense is raising its seed round",
                         "sectors": "AgriSense is an agritech hardware and IoT company"}),
        node("goal_fatima", "Goal", "Raising a $500k seed round", "fit",
             facts={"check_ask": 500000}, statements={"check_ask": "Fatima is raising a $500,000 seed round"}),
        node("tr_fatima", "Traction", "120 farms piloting the sensors", "fit",
             facts={"traction": "120 farms piloting"},
             statements={"traction": "120 farms are piloting AgriSense sensors"}),
        node("ach_fatima_team", "Achievement", "Team of ex-agronomists and hardware engineers", "fit",
             statements={"team": "Fatima's team are ex-agronomists and hardware engineers"}),
        node("av_f1", "Availability", "Wed Nov 11, 09:00 to 10:00", "fit",
             start="2026-11-11T09:00", end="2026-11-11T10:00"),
        node("mandate_fatima", "Mandate", "Agent mandate for investor outreach", "private",
             pack="founder_to_investor", shareable_tier="fit", max_meeting_min=45,
             allowed_proposals=["meeting", "call"]),
        node("ev_agrisense", "Evidence", "AgriSense pilot report", "fit", kind="doc", ref="https://example.com/agrisense/pilot"),
    ]
    edges = [edge("fatima", "co_agrisense", "FOUNDED"), edge("tr_fatima", "ev_agrisense", "SUPPORTED_BY")]
    write({"id": pid, "name": "Fatima Noor", "headline": "Founder, AgriSense (agri-hardware)",
           "meta": {"demo": True, "role": "founder", "company": "AgriSense", "intents": ["raising"]},
           "nodes": nodes, "edges": has_edges(pid, nodes, edges)})


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    enrich_ali()
    build_zara()
    build_bilal()
    build_fatima()
    print("done", file=sys.stderr)
