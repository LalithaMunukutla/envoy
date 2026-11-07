"""Getting facts into a Personal Context Graph.

1. Interview transcript -> typed facts (Claude in live mode; simple patterns offline), each with the quote as evidence.
2. GitHub username -> public repos as Achievement facts with repo URLs as evidence.
Every ingested fact keeps its source and starts at a conservative tier the person can raise later."""
from __future__ import annotations

import os
import re

import httpx

from ..llm import llm
from .graph import PersonalGraph

TYPES = ["Work", "Goal", "Traction", "Achievement", "Interest", "Availability", "Preference"]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]


async def from_interview(graph: PersonalGraph, transcript: str) -> list[dict]:
    items = None
    if llm.enabled:
        out = await llm.json(
            "Extract facts about the speaker from an onboarding interview into a personal knowledge graph. "
            f"Types: {TYPES}. Only facts the speaker states about themselves. Never infer health, religion, "
            "ethnicity, politics, sexuality or finances beyond what they state for a professional goal. "
            "For each fact give a short label, the exact supporting quote, a tier (public for things they'd put on a profile, "
            "fit otherwise) and optional machine facts (keys like stage, sectors, geography, check_ask, traction, interests).",
            f'Transcript:\n"""{transcript[:6000]}"""\nReturn {{"facts": [{{"type": "...", "label": "...", "quote": "...", '
            f'"tier": "public|fit", "facts": {{}}}}]}}', max_tokens=1200)
        if isinstance(out, dict):
            items = out.get("facts")
    if items is None:  # offline: a few transparent patterns
        items = []
        patterns = [
            (r"\bi (?:love|like|enjoy|am into)\s+([^.,;!]+)", "Interest", "public"),
            (r"\bi work (?:at|for)\s+([^.,;!]+)", "Work", "public"),
            (r"\bi(?: am|'m) (?:a|an)\s+([^.,;!]+)", "Achievement", "public"),
            (r"\b(?:i'm |i am |we're |we are )?(?:raising|looking for)\s+([^.,;!]+)", "Goal", "fit"),
        ]
        for sentence in re.split(r"(?<=[.!?])\s+", transcript):
            for pattern, ntype, tier in patterns:
                m = re.search(pattern, sentence, re.I)
                if m:
                    items.append({"type": ntype, "label": m.group(1).strip().capitalize(), "quote": sentence.strip(), "tier": tier})
    added = []
    for i, it in enumerate(items):
        if it.get("type") not in TYPES or not it.get("label"):
            continue
        nid = f"{it['type'].lower()}_{_slug(it['label'])}"
        attrs = {"facts": it["facts"]} if it.get("facts") else {}
        graph.add_fact({"id": nid, "type": it["type"], "label": it["label"], "tier": it.get("tier", "fit"),
                        "source": "interview", "attrs": attrs},
                       evidence={"id": f"ev_{nid}", "label": f"Interview: \"{it.get('quote', '')[:120]}\"",
                                 "attrs": {"kind": "interview_quote", "ref": it.get("quote", "")}})
        added.append({"id": nid, "type": it["type"], "label": it["label"]})
    graph.save()
    return added


async def from_github(graph: PersonalGraph, username: str) -> list[dict]:
    headers = {"Accept": "application/vnd.github+json"}
    if os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.getenv('GITHUB_TOKEN')}"
    async with httpx.AsyncClient(timeout=10, headers=headers) as client:
        r = await client.get(f"https://api.github.com/users/{username}/repos", params={"sort": "updated", "per_page": 30})
        r.raise_for_status()
    repos = sorted([x for x in r.json() if not x.get("fork")], key=lambda x: -x.get("stargazers_count", 0))[:5]
    added = []
    for repo in repos:
        nid = f"repo_{_slug(repo['name'])}"
        label = f"{repo['name']} ({repo['stargazers_count']} stars)"
        graph.add_fact({"id": nid, "type": "Achievement", "label": label, "tier": "public", "source": "github",
                        "attrs": {"statements": {"open_source": f"{repo['name']} on GitHub has {repo['stargazers_count']} stars"},
                                  "language": repo.get("language"), "description": repo.get("description")}},
                       evidence={"id": f"ev_{nid}", "label": f"GitHub: {repo['full_name']}",
                                 "attrs": {"kind": "repo", "ref": repo["html_url"]}})
        added.append({"id": nid, "type": "Achievement", "label": label})
    graph.save()
    return added
