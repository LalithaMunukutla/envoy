"""Tests for the multi-user layer: create-user, connectors + deep export parsing,
intelligent merge, intent -> pack matching, and common ground."""
import asyncio
import io
import json
import os
import zipfile

os.environ["ENVOY_PACE"] = "0"
os.environ["ENVOY_MODE"] = "mock"
os.environ.setdefault("ENVOY_DB", "/tmp/envoy_pytest.db")

from fastapi.testclient import TestClient  # noqa: E402

from server import connectors, intent  # noqa: E402
from server.connectors import exports  # noqa: E402
from server.connectors.base import FactSpec, apply  # noqa: E402
from server.connectors.sources import InstagramConnector  # noqa: E402
from server.knowledge.graph import PersonalGraph, get_graph, tiers_upto  # noqa: E402
from server.main import app  # noqa: E402
from server.matching import common_ground  # noqa: E402
from server.packs import get_pack  # noqa: E402


def _linkedin_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Profile.csv", "First Name,Last Name,Headline,Summary,Industry,Geo Location\n"
                   "Ali,Ahmad,Founder,Building AI dev tools,Software,San Francisco\n")
        z.writestr("Positions.csv", "Company Name,Title,Description,Location,Started On,Finished On\n"
                   "Lumen Labs,Founder,LLM test harness,SF,Jan 2024,\nStripe,Engineer,Infra,SF,2018,2023\n")
        z.writestr("Education.csv", "School Name,Start Date,End Date,Degree Name,Notes\nMIT,2014,2018,BS CS,\n")
        z.writestr("Skills.csv", "Name\nPython\nMachine Learning\n")
    return buf.getvalue()


def _instagram_zip() -> bytes:
    buf = io.BytesIO()
    topics = {"topics_your_topics": [{"string_map_data": {"Name": {"value": "Rock Climbing"}}},
                                     {"string_map_data": {"Name": {"value": "Startups"}}}]}
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("preferences/your_topics.json", json.dumps(topics))
        z.writestr("content/posts_1.json", json.dumps([{"media": [{"title": "hiking and coffee"}]}]))
    return buf.getvalue()


# ---------- connectors + deep parsing ----------
def test_registry_has_all_sources():
    assert set(connectors.REGISTRY) == {"linkedin", "portfolio", "instagram", "github", "interview"}


def test_linkedin_zip_deep_parse():
    specs = asyncio.run(exports.linkedin_facts("export.zip", _linkedin_zip()))
    labels = {s.label for s in specs}
    assert "Founder at Lumen Labs" in labels and "Engineer at Stripe" in labels
    assert any("MIT" in s.label for s in specs)       # education
    assert any(s.type == "Interest" for s in specs)   # skills -> topics
    assert all(s.evidence_ref for s in specs)         # every fact keeps evidence


def test_linkedin_single_csv_from_unzipped_folder():
    # a user who unzipped the export can upload one CSV at a time
    csv_bytes = b"Company Name,Title,Description,Location,Started On,Finished On\nLumen Labs,Founder,x,SF,2024,\n"
    specs = asyncio.run(exports.linkedin_facts("Positions.csv", csv_bytes))
    assert any(s.label == "Founder at Lumen Labs" for s in specs)


def test_instagram_zip_topics_and_captions():
    specs = asyncio.run(exports.instagram_facts("ig.zip", _instagram_zip()))
    labels = {s.label.lower() for s in specs}
    assert "rock climbing" in labels and "startups" in labels  # from topics
    assert "hiking" in labels and "coffee" in labels           # derived from captions


def test_instagram_text_interests_offline():
    specs = asyncio.run(InstagramConnector().extract("weekends: climbing, dim sum and live music"))
    assert {"climbing", "dim sum", "live music"} <= {s.label.lower() for s in specs}


def test_merge_strengthens_interest_without_duplicating():
    g = PersonalGraph({"id": "tmp", "name": "T", "meta": {},
                       "nodes": [{"id": "tmp", "type": "Person", "label": "T", "tier": "public", "attrs": {}}],
                       "edges": []})
    spec = FactSpec("Interest", "Climbing", "public", facts={"interests": ["climbing"]}, evidence_ref="ig")
    a1 = apply(g, "instagram", spec)
    a2 = apply(g, "instagram", spec)
    assert a1["status"] == "new" and a2["status"] == "updated"
    assert g.node("interest_climbing")["attrs"]["weight"] == 2.0  # repeated signal strengthens


# ---------- intent -> pack ----------
def test_candidate_packs_match_and_mismatch():
    maya, dana, ben = get_graph("maya"), get_graph("dana"), get_graph("ben")
    assert intent.candidate_packs(maya, dana)[0]["pack"] == "founder_to_investor"
    assert intent.candidate_packs(maya, ben) == []


def test_symmetric_pack_needs_shared_intent():
    asha, priya = get_graph("asha"), get_graph("priya")
    assert intent.candidate_packs(asha, priya)[0]["pack"] == "friend_to_friend"


# ---------- common ground ----------
def test_common_ground_finds_real_overlap():
    pack = get_pack("founder_to_investor")
    maya, dana = get_graph("maya"), get_graph("dana")
    facts, _ = maya.facts(tiers_upto("fit"), pack.shareable_types)
    prefs = dana.public_profile(pack.id)["prefs"]
    cg = common_ground(pack, facts, prefs, my_graph=maya, their_graph=dana)
    assert cg["overlaps"] and cg["score"] > 0
    assert all("shared shared" not in p.lower() for p in cg["points"])


# ---------- API ----------
def test_demo_personas_hidden_from_app():
    with TestClient(app) as client:
        ids = {p["id"] for p in client.get("/api/people").json()}
        assert "maya" not in ids  # fixtures exist but are hidden from the live app


def test_create_user_and_deep_upload_round_trip():
    with TestClient(app) as client:
        pid = client.post("/api/users", json={"name": "Test Builder"}).json()["id"]
        try:
            assert pid in {p["id"] for p in client.get("/api/people").json()}
            r = client.post(f"/api/people/{pid}/upload/linkedin",
                            files=[("files", ("export.zip", _linkedin_zip(), "application/zip"))]).json()
            assert len(r["added"]) >= 4 and all(a["status"] == "new" for a in r["added"])
            # multi-file: upload two individual CSVs at once, like an unzipped folder
            r_multi = client.post(f"/api/people/{pid}/upload/linkedin", files=[
                ("files", ("Skills.csv", b"Name\nRust\nKubernetes\n", "text/csv")),
                ("files", ("Education.csv", b"School Name,Degree Name,Start Date,End Date\nStanford,MS,2019,2021\n", "text/csv")),
            ]).json()
            assert any("Stanford" in a["label"] for a in r_multi["added"])
            # re-upload the zip merges rather than duplicates
            r2 = client.post(f"/api/people/{pid}/upload/linkedin",
                             files=[("files", ("export.zip", _linkedin_zip(), "application/zip"))]).json()
            assert all(a["status"] == "updated" for a in r2["added"])
            g = client.get(f"/api/people/{pid}/graph").json()
            assert g["meta"]["ingest_log"][-1]["updated"] >= 4
        finally:
            os.remove(f"data/people/{pid}.json")


def test_discovery_ranks_matches_by_intent():
    with TestClient(app) as client:
        pid = client.post("/api/users", json={"name": "Disco Test"}).json()["id"]
        try:
            r = client.get(f"/api/people/{pid}/matches", params={"intent": "raising"}).json()
            ids = {m["id"] for m in r["matches"]}
            assert {"dana", "sam"} <= ids      # investors are the complement of a founder raising
            assert "maya" not in ids           # another founder is not a match
            assert r["pack"] == "founder_to_investor"
            friends = client.get(f"/api/people/{pid}/matches", params={"intent": "friends"}).json()
            assert {"asha", "priya", "ben"} <= {m["id"] for m in friends["matches"]}
        finally:
            os.remove(f"data/people/{pid}.json")


def test_platform_endpoints():
    with TestClient(app) as client:
        assert len(client.get("/api/connectors").json()) == 5
        assert any(i["id"] == "raising" for i in client.get("/api/intents").json())
        cands = client.get("/api/match", params={"requester": "maya", "receiver": "dana"}).json()["candidates"]
        assert cands and cands[0]["pack"] == "founder_to_investor"
        info = client.post("/api/sessions", json={"requester": "maya", "receiver": "dana"}).json()
        assert info["pack"]["id"] == "founder_to_investor"
