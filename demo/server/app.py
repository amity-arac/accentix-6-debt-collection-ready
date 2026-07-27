"""FastAPI shim for the chat-with-agent demo.

Endpoints:
    GET    /api/session            -- create session, stream session info + opening hops (NDJSON)
    POST   /api/session/{id}/turn  -- stream agent hops for one user message (NDJSON)
    POST   /api/session/{id}/reset -- reset session, stream new session info + opening hops (NDJSON)
    GET    /api/tts                -- Google Chirp 3 HD TTS (raw PCM int16@24k -> Web Audio)
    WS     /api/stt                -- streaming Zipformer STT: browser PCM16@16k in, transcript events out
    GET    /api/health             -- liveness

NDJSON message types:
    {"type": "session", "session_id": str, "mode": str, "case_id": str,
     "agent": "qwen"|"gemini"|None, "customer_data": {...}}
    {"type": "hop", "hop": {...}}
    {"type": "done", "session_done": bool}
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
from pathlib import Path
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

# Load .env from the repo root before any module reads env vars.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# `stt_ws` keeps torch / numpy / websockets imports lazy (inside the handler),
# so importing it here does NOT pull those heavy deps at startup.
from demo.server import replay, sessions, stt_ws, tts  # noqa: E402

logger = logging.getLogger("demo.server")

DEFAULT_CASE_ID = "TC-AEON-AAX-025"
DEFAULT_MODE = "live"
DEFAULT_AGENT = "qwen"

app = FastAPI(title="aax6-demo", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
    # Server-Timing isn't a CORS-safelisted response header, so cross-origin JS
    # can't read it unless it's exposed. The client reads `Server-Timing: cache`
    # off /api/tts to attribute TTS latency (hit vs cold) — see audio.ts.
    expose_headers=["Server-Timing"],
)

# In-memory session store. One process, one demo — no persistence needed.
SESSIONS: dict[str, sessions.Session] = {}

NDJSON_MEDIA = "application/x-ndjson"


def _gcp_creds_present() -> bool:
    return bool(
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GOOGLE_CREDENTIALS_JSON")
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    )


@app.on_event("startup")
async def _prewarm_filler() -> None:
    """Pre-synthesize the fixed "please wait" filler into the TTS cache once per
    process. In live mode the filler is the FIRST audio the caller hears on any
    tool turn (sessions.py relabels tool_call_pending → a spoken reply hop), and
    live mode never prewarms TTS otherwise — so without this its first synth is a
    cold Chirp call (~500ms). A warm cache makes it a hit (~50-100ms). Gated on
    GCP creds (else the synth just 401s) + AAX6_TTS_PREWARM_FILLER (default on).
    Fire-and-forget: never blocks startup, and there is runway before the first
    caller speaks."""
    if os.environ.get("AAX6_TTS_PREWARM_FILLER", "1").strip().lower() in ("0", "false", ""):
        return
    if not _gcp_creds_present():
        return
    asyncio.create_task(tts.prewarm([replay.FILLER_TEXT]))


def _config() -> tuple[str, str, str]:
    mode = (os.environ.get("AAX6_DEMO_MODE") or DEFAULT_MODE).strip().lower()
    if mode not in ("replay", "live"):
        mode = DEFAULT_MODE
    case_id = (os.environ.get("AAX6_DEMO_CASE_ID") or DEFAULT_CASE_ID).strip()
    agent = (os.environ.get("AAX6_DEMO_AGENT") or DEFAULT_AGENT).strip().lower()
    if agent not in sessions.VALID_AGENTS:
        agent = DEFAULT_AGENT
    return mode, case_id, agent


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TurnBody(BaseModel):
    message: str = ""


class SaveBody(BaseModel):
    # Optional tester note attached to the saved trajectory. Defaulted so a
    # body-less POST stays valid.
    comment: str = ""


class FlowCompanyBody(BaseModel):
    company: str = ""
    display_name: str = ""
    agent_name: str = ""
    templates: dict[str, str] = {}
    custom: list[dict] = []


class FlowSpecBody(BaseModel):
    company: str = ""
    spec: dict = {}
    new_templates: list[dict] = []


# ---------------------------------------------------------------------------
# NDJSON helpers
# ---------------------------------------------------------------------------


def _line(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


async def _stream_session_only(session: sessions.Session) -> AsyncIterator[bytes]:
    """Emit session metadata + done, without firing the agent's opening turn.

    The user-facing flow is: click "เริ่มต้น" → session metadata loads → user
    speaks first → first /turn call advances the replay pointer (or invokes
    the live agent) and produces what was previously the opening greeting.
    """
    yield _line({
        "type": "session",
        "session_id": session.session_id,
        "mode": session.mode,
        "case_id": getattr(session, "case_id", None),
        "agent": getattr(session, "agent_name", None),
        "voice_gender": getattr(session, "voice_gender", "F"),
        "customer_data": session.customer_data,
    }).encode("utf-8")
    yield _line({"type": "done", "session_done": session.done}).encode("utf-8")


async def _stream_turn(session: sessions.Session, msg: str) -> AsyncIterator[bytes]:
    if session.done:
        yield _line({"type": "done", "session_done": True}).encode("utf-8")
        return
    async for hop in session.aiter_turn(msg):  # type: ignore[attr-defined]
        yield _line({"type": "hop", "hop": hop}).encode("utf-8")
    # Attach this turn's LLM timing (set by LiveSession._aiter_run). Absent for
    # ReplaySession → llm_ms/llm_hops are null (the UI shows "—").
    timing = getattr(session, "_last_turn_timing", None) or {}
    yield _line({
        "type": "done",
        "session_done": session.done,
        "llm_ms": timing.get("llm_ms"),
        "llm_hops": timing.get("llm_hops"),
    }).encode("utf-8")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/api/cases")
async def list_cases() -> JSONResponse:
    """All available personas as flat picker rows (shipped pool + Builder-created)."""
    return JSONResponse(sessions.list_cases())


@app.get("/api/flow/companies")
async def flow_companies() -> JSONResponse:
    """Company codes that have a FlowSpec (drives the frontend's flow-supported set)."""
    return JSONResponse(sessions.flow_companies())


@app.get("/api/flow/beats")
async def flow_beats() -> JSONResponse:
    """Base-flow beats for the Flow Builder form: [{fine_state, hint, example}]."""
    return JSONResponse(sessions.flow_beats())


@app.get("/api/flow/instruction")
async def flow_instruction(company: str = Query(...)) -> JSONResponse:
    """Rendered system instruction (prompt) for a company's flow — for reading."""
    txt = sessions.flow_instruction(company)
    if not txt:
        raise HTTPException(404, detail=f"no flow instruction for company {company!r}")
    return JSONResponse({"company": company, "instruction": txt})


@app.get("/api/flow/spec")
async def get_flow_spec(company: str = Query(...)) -> JSONResponse:
    """A company's FlowSpec + editor vocab (catalog fine_states, tool names)."""
    result = sessions.get_flow_spec(company)
    if not result:
        raise HTTPException(404, detail=f"no FlowSpec for company {company!r}")
    return JSONResponse(result)


@app.post("/api/flow/spec")
async def save_flow_spec(body: FlowSpecBody) -> JSONResponse:
    """Validate + write an edited FlowSpec (structure editor). No restart needed."""
    try:
        result = sessions.save_flow_spec(body.company, body.spec, body.new_templates)
    except Exception as e:
        logger.exception("flow spec save failed")
        raise HTTPException(500, detail=f"save failed: {e}")
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.post("/api/flow/company")
async def create_flow_company(body: FlowCompanyBody) -> JSONResponse:
    """Author a new flow company (writes catalog+spec, registers, adds a demo
    persona). Returns {ok, case_id} or {ok:False, errors:[...]} (400)."""
    try:
        result = sessions.create_flow_company(
            body.company, body.display_name, body.agent_name, body.templates, body.custom
        )
    except Exception as e:
        logger.exception("flow company create failed")
        raise HTTPException(500, detail=f"create failed: {e}")
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.get("/api/session")
async def create_session(
    agent: str | None = Query(default=None),
    case_id: str | None = Query(default=None),
    gender: str | None = Query(default=None),
    flow: bool = Query(default=False),
) -> StreamingResponse:
    mode, default_case_id, default_agent = _config()
    chosen_case = (case_id or default_case_id).strip()
    chosen_agent = (agent or default_agent).strip().lower()
    if chosen_agent not in sessions.VALID_AGENTS:
        chosen_agent = default_agent
    chosen_gender = (gender or "F").strip().upper()
    if chosen_gender not in ("M", "F"):
        chosen_gender = "F"
    # Flow-interpreter mode is always a live session (it can't replay).
    if flow and mode != "live":
        mode = "live"
    try:
        session = sessions.build(
            chosen_case, mode, agent=chosen_agent, voice_gender=chosen_gender, flow=flow
        )
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:
        logger.exception("session build failed")
        raise HTTPException(500, detail=f"session build failed: {e}")

    SESSIONS[session.session_id] = session

    # Replay-mode optimization: fire-and-forget TTS pre-warm so subsequent
    # /api/tts calls are cache hits. Skip if neither GCP creds nor a project
    # is configured — the synth call would just 401/raise.
    if isinstance(session, sessions.ReplaySession) and _gcp_creds_present():
        from services.speech.config import VOICE_BY_GENDER
        texts = session.all_reply_texts()
        voice_name = VOICE_BY_GENDER.get(session.voice_gender, VOICE_BY_GENDER["F"])
        asyncio.create_task(tts.prewarm(texts, voice_name))

    # Live-mode optimization: fire-and-forget vLLM prefix prewarm so the
    # user's first turn hits a warm KV cache instead of paying full
    # prompt-processing cost (~500ms-1s saved on the first hop).
    if isinstance(session, sessions.LiveSession):
        asyncio.create_task(session.prewarm())

    return StreamingResponse(
        _stream_session_only(session),
        media_type=NDJSON_MEDIA,
    )


async def _stream_opening(session: sessions.Session) -> AsyncIterator[bytes]:
    """Fire the agent's proactive opening greeting (outbound call — the bot
    speaks first). Streams the same hop schema as a normal turn."""
    if session.done:
        yield _line({"type": "done", "session_done": True}).encode("utf-8")
        return
    async for hop in session.aiter_opening():  # type: ignore[attr-defined]
        yield _line({"type": "hop", "hop": hop}).encode("utf-8")
    timing = getattr(session, "_last_turn_timing", None) or {}
    yield _line({
        "type": "done",
        "session_done": session.done,
        "llm_ms": timing.get("llm_ms"),
        "llm_hops": timing.get("llm_hops"),
    }).encode("utf-8")


@app.post("/api/session/{session_id}/opening")
async def session_opening(session_id: str) -> StreamingResponse:
    """Outbound-call opening: the bot greets first, before the caller speaks."""
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(404, detail=f"unknown session_id {session_id!r}")
    return StreamingResponse(
        _stream_opening(session),
        media_type=NDJSON_MEDIA,
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/api/session/{session_id}/turn")
async def advance_turn(session_id: str, body: TurnBody) -> StreamingResponse:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(404, detail=f"unknown session_id {session_id!r}")
    return StreamingResponse(
        _stream_turn(session, body.message),
        media_type=NDJSON_MEDIA,
        headers={
            # Flush each hop the instant it is produced. Without this a reverse
            # proxy (e.g. nginx) may buffer the NDJSON and deliver several hops
            # in one clump, making tool calls look delayed. Matches /api/tts.
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/session/{session_id}/reset")
async def reset_session(session_id: str) -> StreamingResponse:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(404, detail=f"unknown session_id {session_id!r}")
    session.reset_pointer()  # type: ignore[attr-defined]
    return StreamingResponse(
        _stream_session_only(session),
        media_type=NDJSON_MEDIA,
    )


@app.post("/api/session/{session_id}/save")
async def save_trajectory(session_id: str, body: SaveBody = SaveBody()) -> JSONResponse:
    """Persist the live conversation to data/demo-saved-trajectory/<dd-mm-yy>/.

    Writes a JSON list of one canonical case (replay-/eval-compatible) plus the
    raw agent message history. Each call is a fresh timestamped file.
    """
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(404, detail=f"unknown session_id {session_id!r}")
    if not isinstance(session, sessions.LiveSession) or not session._transcript:
        return JSONResponse({"saved": False, "reason": "nothing to save"}, status_code=400)

    case = sessions.build_trajectory_case(session, comment=body.comment.strip())
    now = datetime.datetime.now()
    day = now.strftime("%d-%m-%y")
    out_dir = sessions.REPO_ROOT / "data" / "demo-saved-trajectory" / day
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{session.case_id}-{now.strftime('%H-%M-%S')}.json"
    (out_dir / filename).write_text(
        json.dumps([case], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return JSONResponse({
        "saved": True,
        "path": f"{day}/{filename}",
        "turns": len(session._transcript),
    })


@app.get("/api/tts")
async def tts_stream(
    text: str = Query(..., min_length=1, max_length=4096),
    gender: str = Query(default="F"),
) -> StreamingResponse:
    """Stream raw PCM bytes (headerless int16 LE @ 24 kHz) as they arrive from
    the Chirp 3 HD gRPC streaming synth. The client reads this body with
    `fetch` and schedules each chunk on a Web Audio `AudioContext` (see
    `demo/frontend/src/audio.ts`) — no container demux, no codec decode, so the
    first samples are audible on arrival instead of paying the native `<audio>`
    element's decode-startup floor.

    `gender` ("M"/"F") picks which Chirp 3 HD voice speaks — independent of the
    reply text's own grammatical gender (ครับ/ค่ะ particles)."""
    from services.speech.config import VOICE_BY_GENDER
    voice_name = VOICE_BY_GENDER.get(gender.strip().upper(), VOICE_BY_GENDER["F"])

    async def _gen() -> AsyncIterator[bytes]:
        try:
            async for chunk in tts.stream_synth(text, voice_name):
                yield chunk
        except Exception:
            logger.exception("tts stream failed")
            # Status is already sent; just close. The `<audio>` element
            # will fire `error` if the stream is empty.
            return

    # Whether this text is already synthesized — known up front, so it can ride a
    # header (unlike the measured synth time, which isn't known until the first
    # chunk, after headers flush). Lets the client attribute TTS latency.
    cache_state = "hit" if tts.is_cached(text, voice_name) else "miss"
    return StreamingResponse(
        _gen(),
        media_type=tts.AUDIO_MEDIA_TYPE,
        headers={
            "Cache-Control": "no-store",
            # Some intermediate proxies buffer otherwise.
            "X-Accel-Buffering": "no",
            # Client reads this via PerformanceObserver (serverTiming). Same-origin
            # in dev/prod; Timing-Allow-Origin keeps it readable if ever cross-origin.
            "Server-Timing": f'cache;desc="{cache_state}"',
            "Timing-Allow-Origin": "*",
        },
    )


@app.websocket("/api/stt")
async def stt_ws_endpoint(ws: WebSocket) -> None:
    """Streaming Zipformer speech-to-text. The browser streams PCM16 @ 16 kHz mono
    frames; server-side Silero VAD gates utterances and the customer's streaming
    Zipformer WS server transcribes each. Emits speech_begin / speech_end /
    stt_final events (see demo/server/stt_ws.py). Engines (torch VAD + the
    Zipformer client) load lazily on first connect; if they can't be built we send
    a fatal error and the frontend falls back to the browser Web Speech API."""
    await ws.accept()
    await stt_ws.run_session(ws)


@app.get("/api/health")
async def health() -> dict:
    mode, case_id, agent = _config()
    return {
        "ok": True,
        "mode": mode,
        "case_id": case_id,
        "agent": agent,
        "sessions": len(SESSIONS),
    }
