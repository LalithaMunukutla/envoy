"""Envoy API + static UI.  Run:  uvicorn server.main:app --reload"""
from __future__ import annotations

import asyncio
import json

import yaml
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, connectors, discovery, intent
from .broker import TERMINAL, manager
from .knowledge.graph import create_person, get_graph, list_people
from .llm import llm
from .packs import all_packs

app = FastAPI(title="Envoy", version="0.1.0")


def scenarios() -> list[dict]:
    return yaml.safe_load((config.DATA_DIR / "scenarios.yaml").read_text())


class StartBody(BaseModel):
    scenario: str | None = None
    pack: str | None = None
    requester: str | None = None
    receiver: str | None = None


class ApproveBody(BaseModel):
    role: str
    approve: bool


class InterviewBody(BaseModel):
    transcript: str


class GithubBody(BaseModel):
    username: str


class ConnectBody(BaseModel):
    payload: str


class IntentsBody(BaseModel):
    intents: list[str]


class CreateUserBody(BaseModel):
    name: str
    headline: str = ""


@app.get("/api/health")
def health():
    return {"ok": True, "mode": llm.mode, "models": {"smart": config.MODEL_SMART, "fast": config.MODEL_FAST} if llm.enabled else None}


@app.get("/api/packs")
def packs():
    return [p.model_dump() for p in all_packs().values()]


@app.get("/api/scenarios")
def get_scenarios():
    return scenarios()


@app.get("/api/people")
def people(include_demo: bool = False):
    return list_people(include_demo=include_demo)


@app.post("/api/users")
def create_user(body: CreateUserBody):
    if not body.name.strip():
        raise HTTPException(400, "A name is required")
    g = create_person(body.name, body.headline)
    return {"id": g.id, "name": g.name, "headline": g.headline, "meta": g.meta}


@app.get("/api/people/{pid}/graph")
def person_graph(pid: str):
    try:
        return get_graph(pid).view()
    except FileNotFoundError:
        raise HTTPException(404, f"No person '{pid}'")


@app.get("/api/connectors")
def get_connectors():
    return connectors.list_connectors()


@app.post("/api/people/{pid}/connect/{source}")
async def connect_source(pid: str, source: str, body: ConnectBody):
    try:
        added = await connectors.ingest(get_graph(pid), source, body.payload)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"{source} ingestion failed: {exc}")
    return {"source": source, "added": added, "mode": llm.mode}


@app.post("/api/people/{pid}/upload/{source}")
async def upload_source(pid: str, source: str, files: list[UploadFile] = File(...)):
    if source not in connectors.REGISTRY:
        raise HTTPException(404, f"Unknown connector '{source}'")
    graph = get_graph(pid)
    added: list[dict] = []
    errors: list[str] = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        if len(data) > 60 * 1024 * 1024:
            errors.append(f"{f.filename}: too large (60 MB max)")
            continue
        try:
            added += await connectors.ingest_file(graph, source, f.filename or source, data)
        except Exception as exc:
            errors.append(f"{f.filename}: {exc}")
    if not added and errors:
        raise HTTPException(502, "; ".join(errors))
    return {"source": source, "files": [f.filename for f in files], "added": added,
            "errors": errors, "mode": llm.mode}


# Back-compat aliases for the original ingestion endpoints (now routed through connectors).
@app.post("/api/people/{pid}/ingest/interview")
async def ingest_interview(pid: str, body: InterviewBody):
    added = await connectors.ingest(get_graph(pid), "interview", body.transcript)
    return {"added": added, "mode": llm.mode}


@app.post("/api/people/{pid}/ingest/github")
async def ingest_github(pid: str, body: GithubBody):
    try:
        return {"added": await connectors.ingest(get_graph(pid), "github", body.username)}
    except Exception as exc:
        raise HTTPException(502, f"GitHub lookup failed: {exc}")


@app.get("/api/intents")
def get_all_intents():
    return intent.list_intents()


@app.get("/api/people/{pid}/intents")
def person_intents(pid: str):
    return {"intents": intent.get_intents(get_graph(pid))}


@app.put("/api/people/{pid}/intents")
def set_person_intents(pid: str, body: IntentsBody):
    return {"intents": intent.set_intents(get_graph(pid), body.intents)}


@app.get("/api/match")
def match(requester: str, receiver: str):
    try:
        req, rec = get_graph(requester), get_graph(receiver)
    except FileNotFoundError:
        raise HTTPException(404, "Unknown person")
    return {"candidates": intent.candidate_packs(req, rec)}


@app.get("/api/people/{pid}/matches")
def person_matches(pid: str, intent: str):
    try:
        return discovery.discover(pid, intent)
    except FileNotFoundError:
        raise HTTPException(404, f"No person '{pid}'")
    except KeyError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/sessions")
async def start_session(body: StartBody):
    if body.scenario:
        sc = next((s for s in scenarios() if s["id"] == body.scenario), None)
        if not sc:
            raise HTTPException(404, f"No scenario '{body.scenario}'")
        pack, req, rec = sc["pack"], sc["requester"], sc["receiver"]
    else:
        pack, req, rec = body.pack, body.requester, body.receiver
        if req and rec and not pack:  # no pack given: pick one from the pair's declared intents
            try:
                suggestion = intent.suggest_pack(get_graph(req), get_graph(rec))
            except FileNotFoundError:
                raise HTTPException(404, "Unknown person")
            if not suggestion:
                raise HTTPException(409, "No shared intent between these two people; set intents or give a pack.")
            pack = suggestion["pack"]
    if not (pack and req and rec):
        raise HTTPException(400, "Give a scenario id, pack + requester + receiver, or requester + receiver with matching intents")
    try:
        s = manager.start(pack, req, rec, body.scenario)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc))
    return s.info()


@app.get("/api/sessions/{sid}/events")
async def session_events(sid: str, request: Request):
    try:
        s = manager.get(sid)
    except KeyError:
        raise HTTPException(404, "No such session")

    async def stream():
        i = 0
        while True:
            if await request.is_disconnected():
                return
            while i < len(s.events):
                yield f"data: {json.dumps(s.events[i], default=str)}\n\n"
                i += 1
            if s.state in TERMINAL and i >= len(s.events) and (s.task is None or s.task.done()):
                yield "event: done\ndata: {}\n\n"
                return
            async with s.cond:
                try:
                    await asyncio.wait_for(s.cond.wait(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/sessions/{sid}/approve")
async def approve(sid: str, body: ApproveBody):
    try:
        s = manager.get(sid)
        await s.approve(body.role, body.approve)
    except KeyError:
        raise HTTPException(404, "No such session")
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return s.info()


@app.get("/api/sessions/{sid}/invite.ics")
def invite(sid: str):
    s = manager.get(sid)
    if s.state != "booked":
        raise HTTPException(409, "Nothing booked yet")
    return PlainTextResponse(s.ics(), media_type="text/calendar",
                             headers={"Content-Disposition": f'attachment; filename="envoy-{sid}.ics"'})


@app.get("/")
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=config.FRONTEND_DIR), name="static")
