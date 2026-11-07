"""The mock connectors shipped today.

Each accepts what a person can actually give us without a login: pasted profile text, a
profile URL, or a handle. Live mode uses the model to extract facts; offline mode uses simple,
transparent patterns. A real OAuth/API connector later implements the same `extract` and drops
into the registry unchanged.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, time, timedelta

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


class GoogleCalendarConnector(Connector):
    """Google Calendar: ingest an .ics export.

    Google Calendar settings -> Export -> `.ics` file. We parse VEVENT blocks as
    BUSY intervals, subtract them from a working week (default Mon-Fri 9-18 local)
    and emit the resulting FREE windows as Availability nodes. Each node carries
    the calendar export as evidence, so the fit scorer can prove where free time
    came from. Live-fetching via OAuth would drop into this same shape later."""
    name, label, kind, accepts = "google_calendar", "Google Calendar", "file", "file"
    placeholder = ("Upload your .ics export (Google Calendar -> Settings -> "
                   "Import & Export -> Export). Paste raw ICS text also works.")

    async def extract_file(self, filename: str, data: bytes) -> list[FactSpec]:
        return _ics_to_availability(data.decode("utf-8", "ignore"))

    async def extract(self, payload: str) -> list[FactSpec]:
        return _ics_to_availability(payload)


_ICS_DT_LINE = re.compile(r"^(DTSTART|DTEND)(?:;[^:\n]*)?:([^\r\n]+)", re.M)
_ICS_EVENT = re.compile(r"BEGIN:VEVENT(.*?)END:VEVENT", re.S)


def _parse_ics_dt(raw: str) -> datetime | None:
    raw = raw.strip()
    try:
        if "T" in raw:
            return datetime.strptime(raw.rstrip("Z"), "%Y%m%dT%H%M%S")
        if len(raw) == 8 and raw.isdigit():
            return datetime.strptime(raw, "%Y%m%d")
    except Exception:
        return None
    return None


def _parse_events(text: str) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for m in _ICS_EVENT.finditer(text or ""):
        block = m.group(1)
        start = end = None
        for kv in _ICS_DT_LINE.finditer(block):
            key, val = kv.group(1), kv.group(2).strip()
            dt = _parse_ics_dt(val)
            if key == "DTSTART":
                start = dt
            else:
                end = dt
        if start and end and end > start:
            out.append((start, end))
    return out


def _free_windows(busy: list[tuple[datetime, datetime]], base: datetime,
                   days: int = 10, work_start: int = 9, work_end: int = 18) -> list[tuple[datetime, datetime]]:
    """Subtract busy intervals from a working week; return free windows >= 30 min."""
    out: list[tuple[datetime, datetime]] = []
    busy_sorted = sorted(busy)
    for d in range(days):
        day = (base + timedelta(days=d)).date()
        if day.weekday() >= 5:
            continue
        ws = datetime.combine(day, time(work_start))
        we = datetime.combine(day, time(work_end))
        day_busy = sorted([(max(s, ws), min(e, we)) for s, e in busy_sorted if s < we and e > ws])
        cur = ws
        for s, e in day_busy:
            if s > cur:
                out.append((cur, s))
            cur = max(cur, e)
        if cur < we:
            out.append((cur, we))
    return [(s, e) for s, e in out if (e - s).total_seconds() >= 30 * 60][:8]


def _ics_to_availability(text: str) -> list[FactSpec]:
    """Return FactSpecs for the free windows across the next ~10 weekdays."""
    busy = _parse_events(text)
    # Anchor the week either at today or at the first event in the file; this makes
    # a demo-export with far-future dates still produce useful windows.
    base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if busy:
        future = [s for s, _ in busy if s >= base]
        if future:
            base = min(future).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            base = min(s for s, _ in busy).replace(hour=0, minute=0, second=0, microsecond=0)
    windows = _free_windows(busy, base)
    specs: list[FactSpec] = []
    for s, e in windows:
        label = f"{s:%a %b} {s.day}, {s:%H:%M} to {e:%H:%M}"
        specs.append(FactSpec(
            type="Availability", label=label[:80], tier="fit",
            attrs={"start": s.isoformat(timespec="minutes"),
                   "end": e.isoformat(timespec="minutes")},
            evidence_ref="Google Calendar export",
            evidence_kind="calendar",
            evidence_summary=f"{len(busy)} busy blocks parsed; free slot shown"))
    return specs


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
