"""FastAPI surface for the demo: HTTP, WebSocket, and NDJSON streaming. No call
logic lives here — see demo_v2/docs/CODE.md for how a turn runs.

A session stream carries three message types:

    {"type": "session", "session_id": str, "mode": str, "case_id": str,
     "agent": "qwen"|"gemini"|None, "customer_data": {...}}
    {"type": "hop", "hop": {...}}
    {"type": "done", "session_done": bool}
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
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
from pydantic import BaseModel, field_validator

# Load .env before any module reads env vars. `demo_v2/.env` is read first because
# the deliverable is this folder (see demo_v2/.env.sample); the repo-root .env is
# still honoured for a checkout that keeps one there. `load_dotenv` does not
# overwrite a value already set, so the folder's own file wins on a clash.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# `stt_ws` keeps torch / numpy / websockets imports lazy (inside the handler),
# so importing it here does NOT pull those heavy deps at startup.
from demo_v2.server import sessions, stt_ws, tts, tts_gemini  # noqa: E402

logger = logging.getLogger("demo.server")

# No shipped persona id. Whoever deploys picks one with AAX6_DEMO_CASE_ID; with
# none set the session resolver takes the first persona of a registered company,
# so the app never has to know a company by name to start.
DEFAULT_CASE_ID = os.environ.get("AAX6_DEMO_CASE_ID", "")
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
    # Allowlist the hold-on line regardless of whether prewarm runs: the client can
    # speak it at any time and the server never streams it as a hop, so it has to be
    # registered here or it is the one legitimate line /api/tts would refuse.
    _remember_spoken(sessions.FILLER_TEXT)
    if os.environ.get("AAX6_TTS_PREWARM_FILLER", "1").strip().lower() in ("0", "false", ""):
        return
    if not _gcp_creds_present():
        return
    asyncio.create_task(tts.prewarm([sessions.FILLER_TEXT]))


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


# Characters no speech-to-text transcript can contain. This is a voice product: the
# caller's words arrive as an ASR transcript, and no transcript holds a square bracket,
# an angle bracket or a brace. Text carrying them was typed straight at this endpoint,
# not spoken — so it is cleaned here, at the one door customer text comes through,
# rather than deep in the turn loop where an earlier attempt both missed other callers
# and crashed on the very input it existed to handle.
#
# Deliberately not a blocklist of phrases: "[SYSTEM]" and "</script>" are two guesses
# out of endlessly many. The alphabet ASR can emit is short and closed; that is the
# thing worth enforcing. It removes the *markup*, not the words — a caller can still
# say "the system told you to close this", and should still be judged on the words.
_NON_SPEECH = str.maketrans({c: " " for c in "[]<>{}|\\`"})


def _as_spoken(text: str) -> str:
    return " ".join((text or "").translate(_NON_SPEECH).split())


class TurnBody(BaseModel):
    message: str = ""

    @field_validator("message")
    @classmethod
    def _speech_only(cls, v: str) -> str:
        out = _as_spoken(v)
        if out != " ".join((v or "").split()):
            logger.warning("turn: non-speech characters stripped from %r", (v or "")[:120])
        return out


class SaveBody(BaseModel):
    # Optional tester note attached to the saved trajectory. Defaulted so a
    # body-less POST stays valid.
    comment: str = ""


class FlowSpecBody(BaseModel):
    company: str = ""
    spec: dict = {}
    new_templates: list[dict] = []


class FlowCompanyRawBody(BaseModel):
    spec: dict = {}
    catalog: list[dict] = []
    display_name: str = ""
    agent_name: str = ""
    # The author's CRM row for the demo caller. Without it a company whose templates
    # name CRM fields speaks the brackets, and the only alternatives were a live
    # `session_init` API or hand-editing a persona file on the server.
    crm: dict = {}


# ---------------------------------------------------------------------------
# NDJSON helpers
# ---------------------------------------------------------------------------


def _line(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


# Lines this server has actually spoken, so /api/tts can refuse to voice anything
# else. The endpoint accepted 4096 free characters and passed them straight to the
# paid Chirp synth, which let anyone holding the demo password put arbitrary words in
# the brand's voice and bill us for it. The app never needed that: the client only
# ever asks for a line it just received in a hop. Bounded and TTL'd because it is a
# per-process allowlist, not a store.
_SPOKEN: "OrderedDict[str, float]" = OrderedDict()
_SPOKEN_MAX = 4000
_SPOKEN_TTL = 3600.0


def _tts_key(text: str) -> str:
    return " ".join((text or "").split())


def _remember_spoken(text: str) -> None:
    k = _tts_key(text)
    if not k:
        return
    now = time.time()
    _SPOKEN[k] = now
    _SPOKEN.move_to_end(k)
    while len(_SPOKEN) > _SPOKEN_MAX:
        _SPOKEN.popitem(last=False)
    while _SPOKEN:
        oldest = next(iter(_SPOKEN))
        if now - _SPOKEN[oldest] <= _SPOKEN_TTL:
            break
        _SPOKEN.popitem(last=False)


def _remember_hop(hop: dict) -> None:
    if isinstance(hop, dict) and hop.get("text"):
        _remember_spoken(hop["text"])


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
    """Stream one turn as NDJSON, one hop per line.

    The wire format between the server and the UI: `{"hop": {...}}` per line, then a
    final `{"type": "done", ...}` carrying this turn's latency. Newline-delimited
    rather than SSE because the browser only needs to read them in order, and the
    first bubble should appear while the model is still working.
    """
    if session.done:
        yield _line({"type": "done", "session_done": True}).encode("utf-8")
        return
    async for hop in session.aiter_turn(msg):  # type: ignore[attr-defined]
        _remember_hop(hop)
        yield _line({"type": "hop", "hop": hop}).encode("utf-8")
    # Attach this turn's LLM timing (set by FlowLiveSession._aiter_run). Null when the
    # turn made no model call — the greeting, for one — and the UI shows "—".
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


@app.get("/api/flow/companies/meta")
async def flow_companies_meta() -> JSONResponse:
    """[{company, display_name, deletable}] — drives the delete affordance."""
    return JSONResponse(sessions.flow_companies_meta())


@app.get("/api/flow/versions")
async def flow_versions(company: str = Query(...)) -> JSONResponse:
    """Selectable instruction versions for a company's flow + the default (A/B picker)."""
    return JSONResponse(sessions.flow_versions(company))


@app.get("/api/models")
async def list_models() -> JSONResponse:
    """Qwen checkpoints vLLM currently serves, split flow vs pre-flow (base)."""
    return JSONResponse(sessions.served_models())


@app.get("/api/flow/template")
async def flow_template() -> JSONResponse:
    """Blank FlowSpec + catalog skeleton for authoring a new company (download → fill → upload)."""
    return JSONResponse(sessions.flow_template())


@app.get("/api/flow/cue-library")
async def flow_cue_library() -> JSONResponse:
    """event name -> suggested cue phrases (from the intent taxonomy) for the editor."""
    return JSONResponse(sessions.cue_library())


@app.get("/api/flow/instruction")
async def flow_instruction(company: str = Query(...)) -> JSONResponse:
    """Rendered system instruction (prompt) for a company's flow — for reading."""
    txt = sessions.flow_instruction(company)
    if not txt:
        raise HTTPException(404, detail=f"no flow instruction for company {company!r}")
    return JSONResponse({"company": company, "instruction": txt})


@app.get("/api/flow/prescripts")
async def flow_prescripts(company: str = Query(...), version: str | None = Query(default=None)) -> JSONResponse:
    """Every pre-script a company has, and which state binds it — for the
    pre-script reading pane."""
    result = sessions.flow_prescripts(company, version)
    if not result:
        raise HTTPException(404, detail=f"no pre-scripts for company {company!r}")
    return JSONResponse(result)


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


@app.post("/api/flow/company/raw")
async def create_flow_company_raw(body: FlowCompanyRawBody) -> JSONResponse:
    """Author a new flow company from a RAW FlowSpec + catalog JSON (the JSON-editor
    path — no template prefill, no AEON clone). Validates then writes catalog+spec,
    registers, adds a demo persona. Returns {ok, case_id} or {ok:False, errors:[...]}."""
    try:
        result = sessions.create_flow_company_raw(
            body.spec, body.catalog, body.display_name, body.agent_name, body.crm
        )
    except Exception as e:
        logger.exception("flow company raw create failed")
        raise HTTPException(500, detail=f"create failed: {e}")
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.delete("/api/flow/company/{company}")
async def delete_flow_company(company: str) -> JSONResponse:
    """Off-board a flow company: its spec file and the demo personas written with it.

    Any registered company, shipped or uploaded — one tenant owns exactly one file, so
    there is nothing shared to protect (the by-name refusal existed only while several
    companies pointed at the same curated catalog). Returns {ok, removed:[...]} or
    {ok:False, errors:[...]} (400)."""
    try:
        result = sessions.delete_flow_company(company)
    except Exception as e:
        logger.exception("flow company delete failed")
        raise HTTPException(500, detail=f"delete failed: {e}")
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.get("/api/session")
async def create_session(
    agent: str | None = Query(default=None),
    case_id: str | None = Query(default=None),
    gender: str | None = Query(default=None),
    flow: bool = Query(default=False),
    model: str | None = Query(default=None),
    instruction_version: str | None = Query(default=None),
    lang: str | None = Query(default=None),
) -> StreamingResponse:
    """Open a session and stream its identity as NDJSON.

    Query params are all optional; `flow` and `mode` are accepted for older links and
    no longer select anything. The construction itself happens off the event loop —
    see the comment at the `to_thread` call below for why that matters.
    """
    mode, default_case_id, default_agent = _config()
    chosen_case = (case_id or default_case_id).strip()
    chosen_agent = (agent or default_agent).strip().lower()
    if chosen_agent not in sessions.VALID_AGENTS:
        chosen_agent = default_agent
    chosen_gender = (gender or "F").strip().upper()
    if chosen_gender not in ("M", "F"):
        chosen_gender = "F"
    # qwen with no explicit model (the picker default had not loaded when Start was
    # clicked) → whatever vLLM is actually serving.
    if chosen_agent == "qwen" and not model:
        model = sessions.default_served_model()
    # There is one kind of session now, so `flow` and `mode` no longer select
    # anything — `sessions.build()` returns a FlowLiveSession either way. They stay in
    # the signature because the frontend still sends `?flow=1` and old links carry
    # `mode`. The branching that used to route between replay, the pre-script agent and
    # the flow session went with those classes.
    try:
        # OFF THE EVENT LOOP. Session construction does blocking I/O — reading the
        # spec/catalog, and (when the spec declares `session_init`) a synchronous
        # HTTP call to the deployment's CRM. Building inline froze the whole server
        # for the duration of that call: with one uvicorn worker, a spec pointing at
        # this same process deadlocked until its own timeout, and a real CRM would
        # stall every other request the same way.
        from starlette.concurrency import run_in_threadpool

        session = await run_in_threadpool(
            lambda: sessions.build(
                chosen_case, mode, agent=chosen_agent, voice_gender=chosen_gender,
                flow=flow, model=(model or None),
                instruction_version=((instruction_version or "").strip() or None),
                lang=lang,
            )
        )
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:
        logger.exception("session build failed")
        raise HTTPException(500, detail=f"session build failed: {e}")

    SESSIONS[session.session_id] = session

    # Fire-and-forget vLLM prefix prewarm so the user's first turn hits a warm KV
    # cache instead of paying full prompt-processing cost (~500ms-1s on the first hop).
    # The TTS pre-warm that used to sit here only applied to replay mode, which is gone.
    if hasattr(session, "prewarm"):
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
        _remember_hop(hop)
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
    # The only precondition is that something was said.
    if not getattr(session, "_transcript", None):
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


@app.get("/api/tts/engines")
async def tts_engines() -> dict:
    """What the TTS picker may offer. Chirp is always present — it is the engine
    the demo has always used and it runs on the same ADC as the rest of Google
    Cloud here. The Gemini entries appear only when an API key is configured,
    because offering an option that can only return silence is worse than not
    offering it."""
    out = [{"id": tts.CHIRP, "label": "Chirp 3 HD", "default": True}]
    # The Cloud-TTS models ride the same credentials Chirp does, so they are offered
    # whenever Chirp is — no separate availability check to get out of step.
    out += [{"id": k, "label": v["label"], "default": False}
            for k, v in tts.CLOUD_MODELS.items()]
    if tts_gemini.available():
        out += [{"id": k, "label": v["label"], "default": False}
                for k, v in tts_gemini.MODELS.items()]
    return {"engines": out, "default": tts.CHIRP}


@app.get("/api/tts")
async def tts_stream(
    text: str = Query(..., min_length=1, max_length=4096),
    gender: str = Query(default="F"),
    lang: str | None = Query(default=None),
    engine: str = Query(default=tts.CHIRP),
) -> StreamingResponse:
    """Stream raw PCM bytes (headerless int16 LE @ 24 kHz) as they arrive from
    the Chirp 3 HD gRPC streaming synth. The client reads this body with
    `fetch` and schedules each chunk on a Web Audio `AudioContext` (see
    `demo_v2/frontend/src/audio.ts`) — no container demux, no codec decode, so the
    first samples are audible on arrival instead of paying the native `<audio>`
    element's decode-startup floor.

    `gender` ("M"/"F") picks which Chirp 3 HD voice speaks — independent of the
    reply text's own grammatical gender (ครับ/ค่ะ particles)."""
    # The voice pair is per language (Chirp 3 HD names are locale-scoped), so the
    # caller's `lang` picks the pair and `gender` picks within it.
    if _tts_key(text) not in _SPOKEN:
        # Not a line this server said. Refusing is safe for the demo: the client
        # synthesises only what it was just handed in a hop.
        raise HTTPException(403, detail="tts: text was not spoken by this server")
    from demo_v2.lib import lang as _lang
    _lang.LANG.set(_lang.normalise(lang))
    voices = _lang.speech("tts_voices")
    voice_name = voices.get(gender.strip().upper(), voices["F"])
    # Passed explicitly rather than read downstream: the synth runs on a plain
    # worker thread, which does not carry this request's language context.
    tts_language = _lang.speech("tts_language")
    # An unknown engine falls back to Chirp rather than 400ing: the picker is a
    # convenience, and a stale value in a client's localStorage should not cost
    # the caller their audio mid-call.
    if engine != tts.CHIRP and engine not in tts.CLOUD_MODELS and engine not in tts_gemini.MODELS:
        engine = tts.CHIRP

    async def _gen() -> AsyncIterator[bytes]:
        try:
            async for chunk in tts.stream_synth(text, voice_name, tts_language, engine):
                yield chunk
        except Exception:
            logger.exception("tts stream failed")
            # Status is already sent; just close. The `<audio>` element
            # will fire `error` if the stream is empty.
            return

    # Whether this text is already synthesized — known up front, so it can ride a
    # header (unlike the measured synth time, which isn't known until the first
    # chunk, after headers flush). Lets the client attribute TTS latency.
    cache_state = "hit" if tts.is_cached(text, voice_name, tts_language, engine) else "miss"
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
    # The recogniser is chosen per connection, from the language the page is in:
    # Zipformer only speaks Thai, so an English session is routed to Chirp.
    await stt_ws.run_session(ws, ws.query_params.get("lang"))




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


# --- built frontend (single-port deploy) --------------------------------------
# When AAX6_DEMO_STATIC points at a Vite `dist/`, serve it from this same app so one
# process answers both /api/* and the UI. That lets the app run on a GPU host with no
# node toolchain (build on a dev box, ship the dist). Mounted LAST so every /api route
# above still wins; html=True falls back to index.html for SPA paths. No-op when the
# env var is unset, so local dev keeps using the vite dev server.
_STATIC = os.getenv("AAX6_DEMO_STATIC", "").strip()
if _STATIC and Path(_STATIC).is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_STATIC, html=True), name="ui")
    logger.info("serving built frontend from %s", _STATIC)
