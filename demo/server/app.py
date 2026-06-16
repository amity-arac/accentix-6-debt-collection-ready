"""FastAPI shim for the chat-with-agent demo.

Endpoints:
    GET    /api/session            -- create session, stream session info + opening hops (NDJSON)
    POST   /api/session/{id}/turn  -- stream agent hops for one user message (NDJSON)
    POST   /api/session/{id}/reset -- reset session, stream new session info + opening hops (NDJSON)
    GET    /api/tts                -- Google Chirp 3 HD TTS (audio/ogg, OGG_OPUS)
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
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

# Load .env from the repo root before any module reads env vars.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from demo.server import sessions, tts  # noqa: E402

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
)

# In-memory session store. One process, one demo — no persistence needed.
SESSIONS: dict[str, sessions.Session] = {}

NDJSON_MEDIA = "application/x-ndjson"


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
        "customer_data": session.customer_data,
    }).encode("utf-8")
    yield _line({"type": "done", "session_done": session.done}).encode("utf-8")


async def _stream_turn(session: sessions.Session, msg: str) -> AsyncIterator[bytes]:
    if session.done:
        yield _line({"type": "done", "session_done": True}).encode("utf-8")
        return
    async for hop in session.aiter_turn(msg):  # type: ignore[attr-defined]
        yield _line({"type": "hop", "hop": hop}).encode("utf-8")
    yield _line({"type": "done", "session_done": session.done}).encode("utf-8")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/api/cases")
async def list_cases() -> JSONResponse:
    """All available personas as flat picker rows (id, company, topic, account
    facts, parsed persona/situation/constraints). Static; cached server-side."""
    return JSONResponse(sessions.list_cases())


@app.get("/api/session")
async def create_session(
    agent: str | None = Query(default=None),
    case_id: str | None = Query(default=None),
) -> StreamingResponse:
    mode, default_case_id, default_agent = _config()
    chosen_case = (case_id or default_case_id).strip()
    chosen_agent = (agent or default_agent).strip().lower()
    if chosen_agent not in sessions.VALID_AGENTS:
        chosen_agent = default_agent
    try:
        session = sessions.build(chosen_case, mode, agent=chosen_agent)
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:
        logger.exception("session build failed")
        raise HTTPException(500, detail=f"session build failed: {e}")

    SESSIONS[session.session_id] = session

    # Replay-mode optimization: fire-and-forget TTS pre-warm so subsequent
    # /api/tts calls are cache hits. Skip if neither GCP creds nor a project
    # is configured — the synth call would just 401/raise.
    if isinstance(session, sessions.ReplaySession) and (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GOOGLE_CREDENTIALS_JSON")
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    ):
        texts = session.all_reply_texts()
        asyncio.create_task(tts.prewarm(texts))

    # Live-mode optimization: fire-and-forget vLLM prefix prewarm so the
    # user's first turn hits a warm KV cache instead of paying full
    # prompt-processing cost (~500ms-1s saved on the first hop).
    if isinstance(session, sessions.LiveSession):
        asyncio.create_task(session.prewarm())

    return StreamingResponse(
        _stream_session_only(session),
        media_type=NDJSON_MEDIA,
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
async def save_trajectory(session_id: str) -> JSONResponse:
    """Persist the live conversation to data/demo-saved-trajectory/<dd-mm-yy>/.

    Writes a JSON list of one canonical case (replay-/eval-compatible) plus the
    raw agent message history. Each call is a fresh timestamped file.
    """
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(404, detail=f"unknown session_id {session_id!r}")
    if not isinstance(session, sessions.LiveSession) or not session._transcript:
        return JSONResponse({"saved": False, "reason": "nothing to save"}, status_code=400)

    case = sessions.build_trajectory_case(session)
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
async def tts_stream(text: str = Query(..., min_length=1, max_length=4096)) -> StreamingResponse:
    """Stream Chirp 3 HD OGG_OPUS bytes as they arrive from the gRPC
    streaming synth. The browser's `<audio src="/api/tts?text=...">` decodes
    progressively; first audio reaches the user a few hundred ms after the
    request instead of waiting for the full clip."""

    async def _gen() -> AsyncIterator[bytes]:
        try:
            async for chunk in tts.stream_synth(text):
                yield chunk
        except Exception:
            logger.exception("tts stream failed")
            # Status is already sent; just close. The `<audio>` element
            # will fire `error` if the stream is empty.
            return

    return StreamingResponse(
        _gen(),
        media_type=tts.AUDIO_MEDIA_TYPE,
        headers={
            "Cache-Control": "no-store",
            # Some intermediate proxies buffer otherwise.
            "X-Accel-Buffering": "no",
        },
    )


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
