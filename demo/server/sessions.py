"""Session strategies for the demo backend.

Two implementations behind a shared protocol so the frontend is mode-agnostic:

- `ReplaySession`: replays the recorded v6c trajectory of one case. Zero LLM
  calls, instant, deterministic. The default for stage demos.
- `LiveSession`: drives the agent through the user-selected pre-script
  communicator (Qwen via vLLM by default, Gemini optional) + `CaseBackend(
  v6_active=True)`. Real LLM calls per turn. Higher fidelity but slower and
  requires either a running vLLM endpoint (Qwen) or `GOOGLE_API_KEY` (Gemini).

Both return the same `hops[]` shape:
    {"kind": "tool_call",   "name": str, "args": dict}
    {"kind": "tool_result", "name": str, "result": Any}
    {"kind": "reply",       "text": str, "text_ids": list[int], "dynamic_vars": dict}
"""

from __future__ import annotations

import asyncio
import datetime
import functools
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Protocol

from demo.server import replay
from demo.server.replay import FILLER_TEXT

# Inter-hop delay used in replay mode to make bubble cadence feel agent-like.
REPLAY_HOP_DELAY_SEC = 0.35
# Reply hops render immediately — TTS playback already gates the next bubble
# on the client side.
REPLAY_REPLY_DELAY_SEC = 0.05

REPO_ROOT = Path(__file__).resolve().parents[2]
# Full 152-persona pool (106 train + 46 test). The picker lists every case here
# and `_load_test_case` resolves the chosen id against the same file.
TEST_CASES_FILE = REPO_ROOT / "data" / "test-cases" / "personas_data.json"

MAX_LIVE_TURNS = 30


VALID_AGENTS = ("qwen", "gemini")
DEFAULT_AGENT = "qwen"


class Session(Protocol):
    session_id: str
    customer_data: dict[str, Any]
    mode: str  # "replay" | "live"
    agent_name: str | None  # "qwen" | "gemini" | None (replay)
    done: bool

    def reset_pointer(self) -> None: ...
    def aiter_opening(self) -> AsyncIterator[dict[str, Any]]: ...
    def aiter_turn(self, user_msg: str) -> AsyncIterator[dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


class ReplaySession:
    mode = "replay"
    agent_name: str | None = None

    def __init__(self, case_id: str) -> None:
        self.session_id = uuid.uuid4().hex[:12]
        self.case_id = case_id
        case = replay.load_case(case_id)
        self.customer_data = dict(case.get("customer_data", {}))
        self._turns = replay.extract_agent_turns(case["full-trajectory"])
        self._pointer = 0
        self.done = False

    # ---- public ----

    async def aiter_opening(self) -> AsyncIterator[dict[str, Any]]:
        async for hop in self._aiter_advance():
            yield hop

    async def aiter_turn(self, user_msg: str) -> AsyncIterator[dict[str, Any]]:
        async for hop in self._aiter_advance():
            yield hop

    def reset_pointer(self) -> None:
        self._pointer = 0
        self.done = False

    # ---- helpers ----

    def all_reply_texts(self) -> list[str]:
        return replay.reply_texts(self._turns)

    async def _aiter_advance(self) -> AsyncIterator[dict[str, Any]]:
        if self._pointer >= len(self._turns):
            self.done = True
            return
        hops = self._turns[self._pointer]
        for h in hops:
            delay = REPLAY_REPLY_DELAY_SEC if h.get("kind") == "reply" else REPLAY_HOP_DELAY_SEC
            await asyncio.sleep(delay)
            yield h
        self._pointer += 1
        if self._pointer >= len(self._turns):
            self.done = True


# ---------------------------------------------------------------------------
# Live
# ---------------------------------------------------------------------------


def _load_test_case(case_id: str) -> dict[str, Any]:
    with TEST_CASES_FILE.open(encoding="utf-8") as fh:
        cases = json.load(fh)
    for case in cases:
        if case.get("id") == case_id:
            return case
    raise KeyError(f"case_id {case_id!r} not found in {TEST_CASES_FILE}")


# Display fields lifted verbatim from each case's `customer_data` for the picker.
_CUSTOMER_DISPLAY_FIELDS = (
    "customer_name",
    "loan_type",
    "total_amount_due",
    "minimum_payment_due",
    "due_date",
    "due_status",
    "customer_phone",
    "last_4_digits",
    "case_status",
    "case_status_note",
)


def _extract_tag(usp: str, tag: str) -> str:
    """Return the inner text of <tag>…</tag> from a persona prompt, or ""."""
    m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", usp, re.S)
    return m.group(1).strip() if m else ""


def _persona_summary(case: dict[str, Any]) -> dict[str, Any]:
    """Flatten one test case into the row shape the persona picker consumes.

    Parses the human-facing role-play sections (persona / situation /
    constraints) out of `user_system_prompt`; `<system_rules>` is intentionally
    omitted (internal sim mechanics + the [TASK_COMPLETED] marker).
    """
    case_id = case.get("id", "")
    cd = case.get("customer_data", {}) or {}
    usp = case.get("user_system_prompt", "") or ""
    row: dict[str, Any] = {
        "id": case_id,
        "company": case_id.split("-")[1] if "-" in case_id else "",
        "topic": case.get("topic", ""),
        "eval_track": case.get("eval_track"),
        "patience": case.get("patience"),
        "persona": _extract_tag(usp, "persona"),
        "situation": _extract_tag(usp, "situation"),
        "constraints": _extract_tag(usp, "constraints"),
    }
    for field in _CUSTOMER_DISPLAY_FIELDS:
        row[field] = cd.get(field)
    return row


@functools.lru_cache(maxsize=1)
def list_cases() -> list[dict[str, Any]]:
    """All personas as flat picker rows. Cached — the source file is static."""
    with TEST_CASES_FILE.open(encoding="utf-8") as fh:
        cases = json.load(fh)
    return [_persona_summary(c) for c in cases]


def normalize_live_hops(reply_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert CommunicatorGeminiPreScript.reply() hops into the canonical shape.

    The communicator emits `rendered_text` entries right after their matching
    `tool_call name="reply"`. We convert each `rendered_text` into a `reply`
    hop carrying the text_ids/dynamic_vars of the immediately preceding
    `tool_call name="reply"`, but we KEEP that tool_call hop as well so the
    UI shows both bubbles.
    """
    raw = reply_result.get("hops", [])
    out: list[dict[str, Any]] = []
    last_reply_args: dict[str, Any] = {}
    for h in raw:
        kind = h.get("kind")
        name = h.get("name")
        if kind == "tool_call":
            out.append({"kind": "tool_call", "name": name, "args": h.get("args", {})})
            if name == "reply":
                last_reply_args = h.get("args", {}) or {}
            continue
        if kind == "tool_result":
            out.append({"kind": "tool_result", "name": name, "result": h.get("result")})
            continue
        if kind == "rendered_text":
            out.append({
                "kind": "reply",
                "text": h.get("text", ""),
                "text_ids": last_reply_args.get("text_ids", []),
                "dynamic_vars": last_reply_args.get("dynamic_vars", {}),
            })
            continue
        # passthrough for anything unknown
        out.append(h)
    return out


class LiveSession:
    mode = "live"

    def __init__(self, case_id: str, agent: str = DEFAULT_AGENT) -> None:
        if agent not in VALID_AGENTS:
            raise ValueError(f"agent must be one of {VALID_AGENTS!r}, got {agent!r}")

        # Import lazily so replay-mode users don't pay the cost of pulling in
        # google-genai, vLLM, simulator, etc.
        from simulator.backend import CaseBackend
        from simulator.config import (
            COMPANY_NAMES,
            COMPANY_AGENT_NAMES,
            COMPANY_PHONES,
            PRE_SCRIPT_DB_FILE,
            V6_ACTIVE,
        )
        from simulator import datetime_utils
        from agents.prompt_loader import load_prescript_prompt

        if agent == "qwen":
            from agents.communicator import CommunicatorQwenPreScript as _CommunicatorCls
        else:
            from agents.communicator import CommunicatorGeminiPreScript as _CommunicatorCls

        self._CaseBackend = CaseBackend
        self._CommunicatorCls = _CommunicatorCls
        self._load_prescript_prompt = load_prescript_prompt

        self.session_id = uuid.uuid4().hex[:12]
        self.case_id = case_id
        self.agent_name = agent
        self._case = _load_test_case(case_id)
        self._company = case_id.split("-")[1]
        self._v6_active = V6_ACTIVE

        # Mirror simulator/run.py:271-284 setup
        cd = dict(self._case["customer_data"])
        cd.setdefault("company_phone", COMPANY_PHONES.get(self._company))
        cd.setdefault("company_name", COMPANY_NAMES.get(self._company))
        cd.setdefault("agent_name", COMPANY_AGENT_NAMES.get(self._company))
        if V6_ACTIVE:
            cd.setdefault("today", datetime_utils.today_iso())
        self.customer_data = cd

        # Load v6 catalog (filtered to this company)
        pre_script_path = REPO_ROOT / PRE_SCRIPT_DB_FILE
        full_db = json.loads(pre_script_path.read_text(encoding="utf-8"))
        self._company_scripts = [s for s in full_db if s["company"] == self._company]

        self._turn_count = 0
        self.done = False

        # Build the actual session objects.
        self._init_agent()

    async def aiter_opening(self) -> AsyncIterator[dict[str, Any]]:
        async for hop in self._aiter_run("สวัสดีค่ะ"):
            yield hop

    async def aiter_turn(self, user_msg: str) -> AsyncIterator[dict[str, Any]]:
        if self.done:
            return
        async for hop in self._aiter_run(user_msg):
            yield hop

    def reset_pointer(self) -> None:
        self._turn_count = 0
        self.done = False
        self._init_agent()

    # ---- helpers ----

    def _init_agent(self) -> None:
        base = REPO_ROOT
        # Deliverable: drive the fine-tuned Qwen (sft_v2) under the v9 per-company
        # prompt (set AAX6_PROMPT_VERSION=v9). v9 = the trained v8 base PLUS honest-AI
        # disclosure + the transfer_to_human_agent escalation; it's a strict superset
        # of v8, so the SFT model still gets its real base. (Set v8 for original
        # train-time behavior.) The legacy demo used a curated English replacement
        # prompt ("qwen-demo") as a crutch for *base* Qwen; not needed here, so
        # variant=None.
        variant = None
        system_prompt = self._load_prescript_prompt(
            base, self._company, self.customer_data, prompt_variant=variant,
        )
        self._backend = self._CaseBackend(self.customer_data, v6_active=self._v6_active)
        kwargs: dict[str, Any] = {
            "system_prompt": system_prompt,
            "script_db": self._company_scripts,
            "agent_context_data": self.customer_data,
        }
        if self.agent_name == "qwen":
            base_url = os.environ.get("AAX6_VLLM_BASE_URL")
            model = os.environ.get("AAX6_VLLM_MODEL")
            if base_url:
                kwargs["base_url"] = base_url
            if model:
                kwargs["model"] = model
            # Demo-only: stream tool-call deltas so the filler bubble appears
            # as soon as Qwen emits the tool name, not after the full tool
            # call is generated. See agents/communicator.py:_llm_streamed.
            kwargs["stream_tool_calls"] = True
            # Deliverable: append the FULL v6/v8 catalog (what sft_v2 was trained
            # with) so the agent's option space matches its training.
            kwargs["append_script_catalog"] = True
        self._agent = self._CommunicatorCls(**kwargs)
        # Per-turn record for the save-trajectory feature: each entry is
        # {"user": <customer msg>, "hops": [<normalized hops>]}. Reset here so a
        # reset_pointer() (which re-inits the agent) starts a clean transcript.
        self._transcript: list[dict[str, Any]] = []

    async def prewarm(self) -> None:
        """Populate the LLM's prefix cache so the user's first turn is fast.

        Fire-and-forget background task called by demo/server/app.py right
        after session creation. Qwen-only: vLLM has automatic prefix caching
        that a dummy completion populates. Gemini's caching is implicit and
        per-account, not per-session, so we skip it there.
        """
        if self.agent_name != "qwen":
            return
        prewarm = getattr(self._agent, "prewarm_cache", None)
        if prewarm is None:
            return
        try:
            await asyncio.to_thread(prewarm)
        except Exception:
            pass

    async def _aiter_run(self, user_msg: str) -> AsyncIterator[dict[str, Any]]:
        """Stream hops as Gemini emits them during a single turn.

        Installs an on_hop callback on the agent that pushes raw hops into a
        thread-safe queue. The blocking agent.reply() runs in a thread, and we
        yield normalized hops from the queue.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        _SENTINEL = object()

        def on_hop_cb(raw: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, raw)

        def blocking() -> dict[str, Any]:
            self._agent.on_hop = on_hop_cb
            try:
                return self._agent.reply(user_msg, self._backend)
            finally:
                self._agent.on_hop = None
                loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

        task = asyncio.create_task(asyncio.to_thread(blocking))

        last_reply_args: dict[str, Any] = {}
        filler_emitted = False
        turn_hops: list[dict[str, Any]] = []

        def _emit(hop: dict[str, Any]) -> dict[str, Any]:
            # Record every client-facing hop so the turn can be saved later.
            turn_hops.append(hop)
            return hop

        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                kind = item.get("kind")
                if kind == "tool_call_pending":
                    # Streaming-only signal from the Qwen agent: the tool
                    # name has been decoded from the first delta, but the
                    # full tool_call args aren't ready yet. Fire the filler
                    # bubble immediately so the user sees activity ~500ms-1s
                    # earlier than waiting for the full tool_call hop.
                    name = item.get("name")
                    if name != "reply" and not filler_emitted:
                        filler_emitted = True
                        yield _emit({
                            "kind": "reply",
                            "text": FILLER_TEXT,
                            "text_ids": [],
                            "dynamic_vars": {},
                        })
                    # Pending hop itself is internal — do not forward to client.
                    continue
                if kind == "tool_call":
                    name = item.get("name")
                    # Fallback for non-streaming path: announce filler before
                    # the first non-reply tool fires this turn. In streaming
                    # mode this is a no-op because the tool_call_pending
                    # branch above already set filler_emitted=True.
                    if name != "reply" and not filler_emitted:
                        filler_emitted = True
                        yield _emit({
                            "kind": "reply",
                            "text": FILLER_TEXT,
                            "text_ids": [],
                            "dynamic_vars": {},
                        })
                    yield _emit({
                        "kind": "tool_call",
                        "name": name,
                        "args": item.get("args", {}),
                    })
                    if name == "reply":
                        last_reply_args = item.get("args", {}) or {}
                elif kind == "tool_result":
                    yield _emit({
                        "kind": "tool_result",
                        "name": item.get("name"),
                        "result": item.get("result"),
                    })
                elif kind == "rendered_text":
                    yield _emit({
                        "kind": "reply",
                        "text": item.get("text", ""),
                        "text_ids": last_reply_args.get("text_ids", []),
                        "dynamic_vars": last_reply_args.get("dynamic_vars", {}),
                    })
                else:
                    yield _emit(item)
        finally:
            result = await task

        self._transcript.append({"user": user_msg, "hops": turn_hops})
        self._turn_count += 1
        if "[TASK_COMPLETED]" in (result.get("text") or ""):
            self.done = True
        if self._turn_count >= MAX_LIVE_TURNS:
            self.done = True


# ---------------------------------------------------------------------------
# Save trajectory
# ---------------------------------------------------------------------------


def build_trajectory_case(session: "LiveSession") -> dict[str, Any]:
    """Assemble a saved-trajectory case from a LiveSession's recorded transcript.

    Matches the canonical schema in `data/trajectories/` (so the file is
    replay-/eval-compatible) and embeds the raw agent message history under
    `agent_messages` for debugging. `conversation` is the human-readable
    turn-by-turn (filler excluded); `full-trajectory` interleaves tool
    calls/results with rendered text and customer turns, mirroring the
    simulator's format (built here from the demo's already-normalized hops).
    """
    case = session._case
    conversation: list[dict[str, Any]] = []
    full: list[dict[str, Any]] = []
    for turn in session._transcript:
        user_msg = turn.get("user", "")
        conversation.append({"role": "customer", "content": user_msg})
        full.append({"role": "customer", "content": user_msg})
        for hop in turn.get("hops", []):
            kind = hop.get("kind")
            if kind == "tool_call":
                full.append({
                    "role": "agent",
                    "content": {"tool_call": hop.get("name"), "args": hop.get("args", {})},
                })
            elif kind == "tool_result":
                full.append({"role": "system", "content": {"result": hop.get("result")}})
            elif kind == "reply":
                text = hop.get("text", "")
                full.append({"role": "agent", "content": text})
                if text != FILLER_TEXT:
                    conversation.append({"role": "agent", "content": text})
    return {
        "id": session.case_id,
        "topic": case.get("topic"),
        "patience": case.get("patience"),
        "eval_track": case.get("eval_track"),
        "was_flipped": case.get("was_flipped", False),
        "user_system_prompt": case.get("user_system_prompt"),
        "customer_data": session.customer_data,
        "communicator_mode": f"{session.agent_name}-prescript",
        "conversation": conversation,
        "full-trajectory": full,
        # Demo-specific extras — canonical eval/replay tooling ignores unknown keys.
        "session_id": session.session_id,
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "agent_messages": getattr(session._agent, "history", []),
    }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build(case_id: str, mode: str, agent: str = DEFAULT_AGENT) -> Session:
    if mode == "live":
        return LiveSession(case_id, agent=agent)
    return ReplaySession(case_id)
