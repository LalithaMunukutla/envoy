"""Deep parsers for the files a person can legitimately export about themselves.

LinkedIn: a profile PDF ("Save to PDF") or the official data-export ZIP (CSV archive).
Instagram / other socials: the official "Download your information" ZIP (JSON archive).

These produce many typed, evidence-backed nodes per upload (every position, school, skill,
certification, interest topic, ...), so the graph is genuinely deep. CSV/JSON archives parse
deterministically (works offline); a PDF uses the model for best structure, with a heuristic
section parser as the offline fallback.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from typing import Any

from ..llm import llm
from .base import FACT_TYPES, FactSpec

# ------------------------------------------------------------------ LinkedIn

LI_SECTIONS = ["summary", "about", "experience", "education", "licenses & certifications",
               "licenses and certifications", "certifications", "skills", "projects",
               "honors & awards", "honors-awards", "volunteering", "publications", "languages"]


async def linkedin_facts(filename: str, data: bytes) -> list[FactSpec]:
    name = (filename or "").lower()
    if name.endswith(".zip") or zipfile.is_zipfile(io.BytesIO(data)):
        return _linkedin_from_zip(data)
    if name.endswith(".pdf") or data[:4] == b"%PDF":
        return await _linkedin_from_pdf(data)
    if name.endswith(".csv"):  # a single CSV straight out of the export folder
        return _linkedin_from_csv(name, data.decode("utf-8", "ignore"))
    # pasted text fallback
    return await _linkedin_from_text(data.decode("utf-8", "ignore"))


def _ev(ref: str = "LinkedIn export") -> dict:
    return {"evidence_ref": ref, "evidence_kind": "profile", "evidence_summary": "LinkedIn export"}


def _read_csv(text: str) -> list[dict]:
    """Parse a LinkedIn CSV, skipping the optional 'Notes:' preamble some files carry."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if "," in ln and not ln.lower().startswith("notes")), 0)
    return list(csv.DictReader(lines[start:]))


# Each parser turns the rows of one export CSV into facts. Keyed by the file's basename so the
# same code serves a whole ZIP and a single CSV dropped in from the unzipped folder.
def _li_profile(rows: list[dict]) -> list[FactSpec]:
    out = []
    for r in rows:
        head, summ = (r.get("Headline") or "").strip(), (r.get("Summary") or "").strip()
        geo = (r.get("Geo Location") or r.get("Location") or "").strip()
        inds = (r.get("Industry") or "").strip()
        if head or summ:
            out.append(FactSpec("Work", head or "LinkedIn profile", "public",
                                facts={k: v for k, v in {"geography": geo, "industry": inds}.items() if v},
                                attrs={"one_liner": summ or head}, **_ev()))
    return out


def _li_positions(rows: list[dict]) -> list[FactSpec]:
    out = []
    for r in rows:
        title, co = (r.get("Title") or "").strip(), (r.get("Company Name") or "").strip()
        if not (title or co):
            continue
        span = _span(r.get("Started On"), r.get("Finished On"))
        label = (f"{title} at {co}" if title and co else title or co)
        out.append(FactSpec("Work", label[:80], "public",
                            statements={"role": f"{label}{(' (' + span + ')') if span else ''}"},
                            attrs={"company": co, "title": title, "span": span,
                                   "description": (r.get("Description") or "").strip()[:400]}, **_ev()))
    return out


def _li_education(rows: list[dict]) -> list[FactSpec]:
    out = []
    for r in rows:
        school, degree = (r.get("School Name") or "").strip(), (r.get("Degree Name") or "").strip()
        if not school:
            continue
        out.append(FactSpec("Achievement", f"{degree + ', ' if degree else ''}{school}"[:80], "public",
                            statements={"education": f"Studied {degree or 'at'} {school}".strip()},
                            attrs={"span": _span(r.get("Start Date"), r.get("End Date"))}, **_ev()))
    return out


def _li_certs(rows: list[dict]) -> list[FactSpec]:
    out = []
    for r in rows:
        nm = (r.get("Name") or "").strip()
        if nm:
            out.append(FactSpec("Achievement", nm[:80], "public",
                                statements={"certification": f"Certified: {nm}"},
                                evidence_ref=(r.get("Url") or "LinkedIn export"),
                                evidence_kind="url", evidence_summary="Certification"))
    return out


def _li_projects(rows: list[dict]) -> list[FactSpec]:
    out = []
    for r in rows:
        t = (r.get("Title") or "").strip()
        if t:
            out.append(FactSpec("Achievement", t[:80], "fit",
                                attrs={"description": (r.get("Description") or "").strip()[:400]},
                                evidence_ref=(r.get("Url") or "LinkedIn export"),
                                evidence_kind="url", evidence_summary="Project"))
    return out


def _li_skills(rows: list[dict]) -> list[FactSpec]:
    skills = [s for r in rows if (s := (r.get("Name") or "").strip())][:25]
    if not skills:
        return []
    return [FactSpec("Interest", "Skills", "public",
                     facts={"topics": [s.lower() for s in skills], "skills": skills},
                     statements={"topics": "Skilled in " + ", ".join(skills[:8])}, **_ev())]


LI_CSV = {
    "profile.csv": _li_profile, "positions.csv": _li_positions, "education.csv": _li_education,
    "certifications.csv": _li_certs, "licenses and certifications.csv": _li_certs,
    "projects.csv": _li_projects, "skills.csv": _li_skills,
}


def _linkedin_from_csv(filename: str, text: str) -> list[FactSpec]:
    base = filename.rsplit("/", 1)[-1].lower()
    parser = LI_CSV.get(base)
    if parser:
        return parser(_read_csv(text))
    # unknown CSV: fall back to generic deep extraction on the raw text
    return _linkedin_heuristic(text)


def _linkedin_from_zip(data: bytes) -> list[FactSpec]:
    specs: list[FactSpec] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            base = n.rsplit("/", 1)[-1].lower()
            parser = LI_CSV.get(base)
            if not parser:
                continue
            with z.open(n) as f:
                text = io.TextIOWrapper(f, encoding="utf-8", errors="ignore").read()
            specs += parser(_read_csv(text))
    return specs


async def _linkedin_from_pdf(data: bytes) -> list[FactSpec]:
    text = _pdf_text(data)
    if not text.strip():
        return []
    specs = await _linkedin_from_text(text)
    return specs


async def _linkedin_from_text(text: str) -> list[FactSpec]:
    if llm.enabled:
        out = await llm.json(
            "Extract a DEEP professional profile from LinkedIn text. Return every position, school, "
            "certification, project and the top skills. Be thorough, one item per fact. "
            f"Allowed types: {FACT_TYPES}. Use Work for positions, Achievement for education / "
            "certifications / projects, Goal for what they want next, Interest for skills/topics "
            "(put a list under facts.topics).",
            f'LinkedIn text:\n"""{text[:9000]}"""\n'
            'Return {"facts": [{"type": "...", "label": "...", "tier": "public|fit", "facts": {}, "statement": "..."}]}',
            max_tokens=1800)
        specs = _specs_from_llm(out, _ev())
        if specs:
            return specs
    # offline heuristic: split by known section headers
    return _linkedin_heuristic(text)


def _linkedin_heuristic(text: str) -> list[FactSpec]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    specs, section = [], ""
    for ln in lines:
        low = ln.lower().strip(": ")
        if low in LI_SECTIONS:
            section = "experience" if low == "experience" else \
                      "education" if low == "education" else \
                      "skills" if low == "skills" else \
                      "cert" if "cert" in low or "licens" in low else \
                      "summary" if low in ("summary", "about") else section
            continue
        if len(ln) < 3 or len(ln) > 120:
            continue
        if section == "experience" and not re.match(r"^\d", ln):
            specs.append(FactSpec("Work", ln[:80], "public", **_ev()))
        elif section == "education":
            specs.append(FactSpec("Achievement", ln[:80], "public",
                                  statements={"education": f"Studied at {ln}"}, **_ev()))
        elif section == "cert":
            specs.append(FactSpec("Achievement", ln[:80], "public", **_ev()))
        elif section == "skills":
            specs.append(FactSpec("Interest", ln[:40], "public", facts={"topics": [ln.lower()]}, **_ev()))
    # de-dupe by label, cap
    seen, out = set(), []
    for s in specs:
        if s.label.lower() in seen:
            continue
        seen.add(s.label.lower())
        out.append(s)
    return out[:40]


# ------------------------------------------------------------------ Instagram / socials

async def instagram_facts(filename: str, data: bytes) -> list[FactSpec]:
    if zipfile.is_zipfile(io.BytesIO(data)):
        return _instagram_from_zip(data)
    name = (filename or "").lower()
    if name.endswith(".json"):
        return _instagram_from_json(json.loads(data.decode("utf-8", "ignore")), name)
    # pasted bio/captions
    return _interests_from_text(data.decode("utf-8", "ignore"), "Instagram")


def _instagram_from_zip(data: bytes) -> list[FactSpec]:
    topics: dict[str, int] = {}
    captions: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            if not n.lower().endswith(".json"):
                continue
            low = n.lower()
            try:
                with z.open(n) as f:
                    j = json.load(f)
            except Exception:
                continue
            if any(k in low for k in ("topic", "interest")):
                for v in _string_map_values(j):
                    topics[v] = topics.get(v, 0) + 1
            if any(k in low for k in ("post", "reel", "story", "liked", "saved")):
                captions += _collect_keys(j, ("title", "caption"))
    specs: list[FactSpec] = []
    ref = "Instagram export"
    for topic, cnt in sorted(topics.items(), key=lambda kv: -kv[1]):
        if 2 <= len(topic) <= 40:
            specs.append(FactSpec("Interest", topic.title(), "public",
                                  facts={"interests": [topic.lower()], "topics": [topic.lower()]},
                                  attrs={"mentions": cnt}, evidence_ref=ref, evidence_kind="profile",
                                  evidence_summary="Instagram interests"))
    # derive extra interests from captions
    for s in _interests_from_text(" ".join(captions[:300]), "Instagram"):
        if not any(e.label.lower() == s.label.lower() for e in specs):
            specs.append(s)
    return specs[:60]


def _instagram_from_json(j: Any, name: str) -> list[FactSpec]:
    topics = {v for v in _string_map_values(j)} | set(_collect_keys(j, ("title", "caption")))
    return [FactSpec("Interest", t.title(), "public", facts={"interests": [t.lower()]},
                     evidence_ref="Instagram export", evidence_kind="profile", evidence_summary="Instagram")
            for t in sorted(topics) if 2 <= len(t) <= 40][:60]


# ------------------------------------------------------------------ shared helpers

def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:
        return ""


def _specs_from_llm(out: Any, ev: dict) -> list[FactSpec]:
    if not isinstance(out, dict) or not isinstance(out.get("facts"), list):
        return []
    specs = []
    for it in out["facts"]:
        if not isinstance(it, dict) or it.get("type") not in FACT_TYPES or not it.get("label"):
            continue
        key = next(iter(it.get("facts") or {}), None)
        specs.append(FactSpec(it["type"], str(it["label"])[:80], it.get("tier", "fit"),
                              facts=it.get("facts") or {},
                              statements={key: it["statement"]} if key and it.get("statement") else {}, **ev))
    return specs


def _string_map_values(obj: Any) -> list[str]:
    """Pull human values out of Instagram's `string_map_data` blocks and generic {value: ...}."""
    out: list[str] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            smd = o.get("string_map_data")
            if isinstance(smd, dict):
                for v in smd.values():
                    if isinstance(v, dict) and isinstance(v.get("value"), str):
                        out.append(v["value"].strip())
            if isinstance(o.get("value"), str) and "string_map_data" not in o:
                out.append(o["value"].strip())
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return [v for v in out if v]


def _collect_keys(obj: Any, keys: tuple[str, ...]) -> list[str]:
    out: list[str] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k in keys and isinstance(v, str) and v.strip():
                    out.append(v.strip())
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return out


_ACTIVITY = [
    "climbing", "bouldering", "hiking", "running", "cycling", "swimming", "surfing", "skiing",
    "coffee", "dim sum", "ramen", "sushi", "baking", "cooking", "board games", "chess", "gaming",
    "live music", "concerts", "guitar", "piano", "photography", "painting", "reading", "writing",
    "travel", "yoga", "dancing", "football", "basketball", "tennis", "pottery", "gardening",
    "startups", "design", "ai", "machine learning", "fitness", "fashion", "food", "art", "film",
]


def _interests_from_text(text: str, source: str) -> list[FactSpec]:
    low = (text or "").lower()
    found = [w for w in _ACTIVITY if re.search(rf"\b{re.escape(w)}\b", low)]
    return [FactSpec("Interest", w.title(), "public", facts={"interests": [w]},
                     evidence_ref=source, evidence_kind="profile", evidence_summary=source)
            for w in dict.fromkeys(found)]


def _span(a: str | None, b: str | None) -> str:
    a, b = (a or "").strip(), (b or "").strip()
    if a and b:
        return f"{a} – {b}"
    return a or (f"until {b}" if b else "")
