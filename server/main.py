"""Envoy API + static UI.  Run:  uvicorn server.main:app --reload"""
from __future__ import annotations

import asyncio
import json

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .broker import TERMINAL, manager
from .knowledge import ingest
from .knowledge.graph import get_graph, list_people
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
def people():
    return list_people()


@app.get("/api/people/{pid}/graph")
def person_graph(pid: str):
    try:
        return get_graph(pid).view()
    except FileNotFoundError:
        raise HTTPException(404, f"No person '{pid}'")


@app.post("/api/people/{pid}/ingest/interview")
async def ingest_interview(pid: str, body: InterviewBody):
    added = await ingest.from_interview(get_graph(pid), body.transcript)
    return {"added": added, "mode": llm.mode}


@app.post("/api/people/{pid}/ingest/github")
async def ingest_github(pid: str, body: GithubBody):
    try:
        return {"added": await ingest.from_github(get_graph(pid), body.username)}
    except Exception as exc:
        raise HTTPException(502, f"GitHub lookup failed: {exc}")


@app.post("/api/sessions")
async def start_session(body: StartBody):
    if body.scenario:
        sc = next((s for s in scenarios() if s["id"] == body.scenario), None)
        if not sc:
            raise HTTPException(404, f"No scenario '{body.scenario}'")
        pack, req, rec = sc["pack"], sc["requester"], sc["receiver"]
    else:
        pack, req, rec = body.pack, body.requester, body.receiver
    if not (pack and req and rec):
        raise HTTPException(400, "Give a scenario id, or pack + requester + receiver")
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
