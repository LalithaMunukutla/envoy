"""The mock connectors shipped today.

Each accepts what a person can actually give us without a login: pasted profile text, a
profile URL, or a handle. Live mode uses the model to extract facts; offline mode uses simple,
transparent patterns. A real OAuth/API connector later implements the same `extract` and drops
into the registry unchanged.
"""
from __future__ import annotations

import os
import re

import httpx

from . import exports
from .base import Connector, FactSpec, slug

ACTIVITY_WORDS = [
    "climbing", "bouldering", "hiking", "running", "cycling", "swimming", "surfing", "skiing",
    "coffee", "dim sum", "ramen", "sushi", "baking", "cooking", "board games", "chess", "gaming",
    "live music", "concerts", "guitar", "piano", "photography", "painting", "reading", "writing",
    "travel", "yoga", "dancing", "football", "basketball", "tennis", "pottery", "gardening",
]


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?\n])\s+", text) if s.strip()]


class LinkedInConnector(Connector):
    name, label, kind, accepts = "linkedin", "LinkedIn", "file", "file"
    placeholder = "Upload your LinkedIn export: the .zip, OR all the .csv files from the unzipped folder, OR your profile PDF"

    async def extract_file(self, filename: str, data: bytes) -> list[FactSpec]:
        return await exports.linkedin_facts(filename, data)

    async def extract(self, payload: str) -> list[FactSpec]:  # pasted text still works
        return await exports.linkedin_facts("pasted.txt", payload.encode("utf-8"))


class PortfolioConnector(Connector):
    name, label, kind = "portfolio", "Portfolio / website", "profile_url"
    placeholder = "Your portfolio or personal site URL (or paste its text)"

    async def extract(self, payload: str) -> list[FactSpec]:
        url = _first_url(payload)
        text = payload
        if url and not re.sub(r"https?://\S+", "", payload).strip():  # URL only -> try to fetch
            text = await _fetch_text(url) or payload
        ref = url or "Portfolio"
        specs = await self.llm_facts(
            "Extract facts from a personal portfolio / website: what the person builds or does, notable "
            "projects or launches, and what they are looking for.",
            text, evidence_ref=ref, evidence_kind="url", evidence_summary="Portfolio site")
        if specs is not None:
            return specs
        out: list[FactSpec] = []
        for s in _split_sentences(text)[:6]:
            if re.search(r"\b(built|launched|created|shipped|designed|founded|made)\b", s, re.I):
                out.append(FactSpec("Achievement", s[:80].strip(), "public",
                                    evidence_ref=ref, evidence_kind="url", evidence_summary="Portfolio site"))
        return out or [FactSpec("Work", "Portfolio", "public",
                               attrs={"one_liner": text.strip()[:160]} if text.strip() else {},
                               evidence_ref=ref, evidence_kind="url", evidence_summary="Portfolio site")]


class InstagramConnector(Connector):
    name, label, kind, accepts = "instagram", "Instagram", "file", "file"
    placeholder = "Upload your Instagram export: the .zip, OR the .json files from the unzipped folder"

    async def extract_file(self, filename: str, data: bytes) -> list[FactSpec]:
        return await exports.instagram_facts(filename, data)

    async def extract(self, payload: str) -> list[FactSpec]:  # pasted bio/captions still work
        return await exports.instagram_facts("pasted.txt", payload.encode("utf-8"))


class GithubConnector(Connector):
    name, label, kind = "github", "GitHub", "handle"
    placeholder = "Your GitHub username"

    async def extract(self, payload: str) -> list[FactSpec]:
        username = _first_handle(payload) or payload.strip()
        headers = {"Accept": "application/vnd.github+json"}
        if os.getenv("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.getenv('GITHUB_TOKEN')}"
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            r = await client.get(f"https://api.github.com/users/{username}/repos",
                                 params={"sort": "updated", "per_page": 30})
            r.raise_for_status()
        repos = sorted([x for x in r.json() if not x.get("fork")],
                       key=lambda x: -x.get("stargazers_count", 0))[:5]
        return [FactSpec(
            "Achievement", f"{repo['name']} ({repo['stargazers_count']} stars)", "public",
            facts={"open_source": repo["name"]},
            statements={"open_source": f"{repo['name']} on GitHub has {repo['stargazers_count']} stars"},
            attrs={"language": repo.get("language"), "description": repo.get("description")},
            evidence_ref=repo["html_url"], evidence_kind="repo", evidence_summary=f"GitHub: {repo['full_name']}")
            for repo in repos]


class InterviewConnector(Connector):
    name, label, kind = "interview", "Interview", "text"
    placeholder = "Paste what the person said in a short onboarding chat"

    async def extract(self, payload: str) -> list[FactSpec]:
        specs = await self.llm_facts(
            "Extract facts about the speaker from an onboarding interview (who they are, what they do, "
            "what they want, interests, availability).",
            payload, evidence_ref=payload[:120], evidence_kind="interview_quote",
            evidence_summary=f'Interview: "{payload.strip()[:80]}"')
        if specs is not None:
            return specs
        out: list[FactSpec] = []
        patterns = [
            (r"\bi (?:love|like|enjoy|am into)\s+([^.,;!]+)", "Interest", "public"),
            (r"\bi work (?:at|for)\s+([^.,;!]+)", "Work", "public"),
            (r"\bi(?: am|'m) (?:a|an)\s+([^.,;!]+)", "Achievement", "public"),
            (r"\b(?:raising|looking for)\s+([^.,;!]+)", "Goal", "fit"),
        ]
        for sentence in _split_sentences(payload):
            for pattern, ntype, tier in patterns:
                m = re.search(pattern, sentence, re.I)
                if m:
                    out.append(FactSpec(ntype, m.group(1).strip().capitalize()[:80], tier,
                                        evidence_ref=sentence.strip(), evidence_kind="interview_quote",
                                        evidence_summary=f'Interview: "{sentence.strip()[:80]}"'))
        return out


# ---- small helpers ----
def _first_url(text: str) -> str | None:
    m = re.search(r"https?://\S+", text or "")
    return m.group(0).rstrip(").,") if m else None


def _first_handle(text: str) -> str | None:
    m = re.search(r"@?([A-Za-z0-9][A-Za-z0-9\-_.]{1,38})", (text or "").strip())
    return m.group(1) if m else None


async def _fetch_text(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True,
                                     headers={"User-Agent": "EnvoyBot/0.1"}) as client:
            r = await client.get(url)
            r.raise_for_status()
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", r.text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()[:6000] or None
    except Exception:
        return None
