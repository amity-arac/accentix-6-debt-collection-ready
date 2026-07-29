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
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Protocol

from demo.server import replay, tts
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
    voice_gender: str  # "M" | "F" — which Chirp 3 HD voice speaks this session's replies
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

    def __init__(self, case_id: str, voice_gender: str = "F") -> None:
        self.session_id = uuid.uuid4().hex[:12]
        self.case_id = case_id
        self.voice_gender = voice_gender if voice_gender in ("M", "F") else "F"
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


# Personas created at runtime by the Flow Builder land here (kept out of the
# shipped personas_data.json). Merged into the picker + case lookups.
BUILDER_CASES_FILE = REPO_ROOT / "data" / "test-cases" / "_builder_personas.json"


def _all_cases() -> list[dict[str, Any]]:
    with TEST_CASES_FILE.open(encoding="utf-8") as fh:
        cases = json.load(fh)
    if BUILDER_CASES_FILE.exists():
        try:
            cases = cases + json.loads(BUILDER_CASES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return cases


def _load_test_case(case_id: str) -> dict[str, Any]:
    for case in _all_cases():
        if case.get("id") == case_id:
            return case
    raise KeyError(f"case_id {case_id!r} not found")


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


def list_cases() -> list[dict[str, Any]]:
    """All personas as flat picker rows (shipped pool + any Builder-created)."""
    return [_persona_summary(c) for c in _all_cases()]


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

    def __init__(self, case_id: str, agent: str = DEFAULT_AGENT, voice_gender: str = "F",
                 model: str | None = None) -> None:
        if agent not in VALID_AGENTS:
            raise ValueError(f"agent must be one of {VALID_AGENTS!r}, got {agent!r}")
        self._model_override = model
        self.voice_gender = voice_gender if voice_gender in ("M", "F") else "F"

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
        # Per-turn LLM timing, stashed at the end of each turn for the done line.
        self._last_turn_timing: dict[str, Any] | None = None

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
        # v10 clones a real bot that only verifies by name (never asks for the
        # 4-digit KYC code), so payment_date's identity check is relaxed for v10.
        require_kyc = os.environ.get("AAX6_PROMPT_VERSION", "").strip() not in ("v10", "v11")
        self._backend = self._CaseBackend(
            self.customer_data, v6_active=self._v6_active, require_kyc=require_kyc
        )
        kwargs: dict[str, Any] = {
            "system_prompt": system_prompt,
            "script_db": self._company_scripts,
            "agent_context_data": self.customer_data,
            # v11: render gendered templates to match the user's voice pick, so the
            # spoken TTS voice (M/F) and the Thai particles (ครับ/ค่ะ) agree. No-op
            # on the older non-parameterized catalog.
            "case_id": self.case_id,
            "gender": self.voice_gender,
        }
        if self.agent_name == "qwen":
            base_url = os.environ.get("AAX6_VLLM_BASE_URL")
            model = self._model_override or os.environ.get("AAX6_VLLM_MODEL")
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
                    if name != "reply" and not filler_emitted and FILLER_TEXT:
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
                    if name != "reply" and not filler_emitted and FILLER_TEXT:
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
                        "hop_ms": item.get("hop_ms"),
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
                    reply_text = item.get("text", "")
                    # Server-side TTS prewarm: kick the Chirp synth NOW so it
                    # overlaps NDJSON delivery + the client's prefetch GET (which
                    # joins this in-flight synth via tts.py's per-text lock) rather
                    # than starting cold after the round-trip. Fire-and-forget;
                    # tts.prewarm swallows its own errors. Revert AAX6_TTS_PREWARM_REPLY=0.
                    if reply_text and os.environ.get(
                        "AAX6_TTS_PREWARM_REPLY", "1"
                    ).strip().lower() not in ("0", "false", ""):
                        from services.speech.config import VOICE_BY_GENDER
                        voice_name = VOICE_BY_GENDER.get(self.voice_gender, VOICE_BY_GENDER["F"])
                        asyncio.create_task(tts.prewarm([reply_text], voice_name))
                    yield _emit({
                        "kind": "reply",
                        "text": reply_text,
                        "text_ids": last_reply_args.get("text_ids", []),
                        "dynamic_vars": last_reply_args.get("dynamic_vars", {}),
                    })
                else:
                    yield _emit(item)
        finally:
            result = await task
            # Stash this turn's LLM timing so app.py can attach it to the done
            # line. total_ms = sum of per-hop LLM-call wall-times; llm_hops =
            # number of LLM round-trips (one tool_call hop per call).
            self._last_turn_timing = {
                "llm_ms": round((result or {}).get("total_ms") or 0.0, 1),
                "llm_hops": sum(
                    1 for h in ((result or {}).get("hops") or [])
                    if isinstance(h, dict) and h.get("kind") == "tool_call"
                ),
            }

        self._transcript.append({"user": user_msg, "hops": turn_hops})
        self._turn_count += 1
        if "[TASK_COMPLETED]" in (result.get("text") or ""):
            self.done = True
        if self._turn_count >= MAX_LIVE_TURNS:
            self.done = True


# ---------------------------------------------------------------------------
# Flow-interpreter session (sft_flow_v1 reads a FlowSpec from the prompt)
# ---------------------------------------------------------------------------

# Flow mode is a DEV/testing path, not part of the shipped customer product: it
# drives the flow-interpreter adapter (sft_flow_v1), which reads a FlowSpec +
# catalog from its prompt instead of the per-company v9 playbook. The flow
# logic is vendored self-contained under demo/server/flow/ (ported from the
# aax6 research package — no aax6 dependency), so this path runs wherever the
# demo's own venv runs, leaving the qwen/gemini product paths untouched.
#
# sft_flow_v1 is company-agnostic (it follows whatever FlowSpec it's given), so
# flow mode supports every company that has a (spec, catalog) pair. Each spec
# shares the debt-collection "outbound-remind" structure; the catalog carries
# the company's own templates (name, particles).
# The registry is file-backed so the Flow Builder can add companies at runtime
# (write files + append here) without a code change or redeploy. The built-in
# defaults seed it / act as a fallback if the file is missing.
FLOW_REGISTRY_FILE = REPO_ROOT / "data" / "flows" / "flow_registry.json"
_FLOW_REGISTRY_DEFAULT: dict[str, dict[str, str]] = {
    "AEON": {"spec": "AEON-outbound-remind.json", "catalog": "v10_pre_script_database_parameterized.json"},
    "JAI": {"spec": "JAI-outbound-remind.json", "catalog": "v11_jai_probe_catalog.json"},
    "KS": {"spec": "KS-outbound-remind.json", "catalog": "v11_ks_probe_catalog.json"},
    "AIS": {"spec": "AIS-outbound-remind.json", "catalog": "v11_ais_probe_catalog.json"},
}
FLOW_FALLBACK_COMPANY = "AEON"
FLOW_MODEL = os.environ.get("AAX6_FLOW_MODEL", "sft_flow_v1")
FLOW_MAX_TOOL_LOOPS = 8


def load_flow_registry() -> dict[str, dict[str, str]]:
    """company -> {spec, catalog, [display_name]}. File-backed; falls back to the
    built-in defaults (and merges them in so shipped companies always resolve)."""
    reg = dict(_FLOW_REGISTRY_DEFAULT)
    if FLOW_REGISTRY_FILE.exists():
        try:
            reg.update(json.loads(FLOW_REGISTRY_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return reg


def flow_companies() -> list[str]:
    return list(load_flow_registry())


_CUE_LIBRARY_FILE = REPO_ROOT / "data" / "flows" / "intent_cues.json"


def cue_library() -> dict[str, list[str]]:
    """event name -> suggested cue phrases, derived from the intent taxonomy
    (tools/build_cue_library.py). Powers the editor's "suggest cues" button."""
    if _CUE_LIBRARY_FILE.exists():
        try:
            return json.loads(_CUE_LIBRARY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _flow_paths(company: str) -> "tuple[Any, Any]":
    entry = load_flow_registry()[company]
    return (REPO_ROOT / "data" / "flows" / entry["spec"],
            REPO_ROOT / "data" / "pre-scripts" / entry["catalog"])


def flow_instruction(company: str) -> str:
    """The rendered system instruction for a company's flow — exactly what
    FlowLiveSession feeds the model (render_instruction(spec) + the catalog),
    with [placeholders] intact (filled per-call at runtime)."""
    from demo.server.flow.flowspec_render import render_instruction
    from agents.prescript import build_script_catalog

    company = (company or "").strip().upper()
    if company not in load_flow_registry():
        return ""
    spec_path, cat_path = _flow_paths(company)
    if not spec_path.exists():
        return ""
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    catalog = json.loads(cat_path.read_text(encoding="utf-8")) if cat_path.exists() else []
    return render_instruction(spec) + "\n\n" + build_script_catalog(catalog, compact=True)


def get_flow_spec(company: str) -> dict[str, Any]:
    """Load a company's FlowSpec + the vocab the editor needs (catalog fine_states,
    declared tool names). Returns {} if the company/spec isn't found."""
    company = (company or "").strip().upper()
    if company not in load_flow_registry():
        return {}
    spec_path, cat_path = _flow_paths(company)
    if not spec_path.exists():
        return {}
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    fine_states: list[str] = []
    templates: dict[str, list[str]] = {}
    if cat_path.exists():
        cat = json.loads(cat_path.read_text(encoding="utf-8"))
        fine_states = sorted({e.get("_fine_state", "") for e in cat if e.get("_fine_state")})
        for e in cat:
            fs = e.get("_fine_state")
            if fs and e.get("template"):
                templates.setdefault(fs, []).append(e["template"])
    tools = [d.get("name") for d in spec.get("tools", {}).get("declarations", [])]
    return {"company": company, "spec": spec, "fine_states": fine_states,
            "templates": templates, "tools": tools}


def _sanitize_spec(spec: dict) -> None:
    """Drop references left dangling by editor edits so validation doesn't fail on
    parts the editor can't manage (tools/constraints). Removes transitions/
    constraints/gating that point at a state or tool that no longer exists."""
    states = {s.get("id") for s in spec.get("states", [])}
    tools = {d.get("name") for d in spec.get("tools", {}).get("declarations", [])}
    # transitions → existing states only
    for st in spec.get("states", []):
        st["on"] = [t for t in st.get("on", []) if t.get("to") in states]
        # entry_tools / transition tools → declared tools only
        if "entry_tools" in st:
            st["entry_tools"] = [t for t in st["entry_tools"] if t in tools]
        for t in st["on"]:
            if "tools" in t:
                t["tools"] = [x for x in t["tools"] if x in tools]
    # gating that points at a missing state/tool → strip that key
    for d in spec.get("tools", {}).get("declarations", []):
        g = d.get("gating", {})
        if g.get("required_before_state") and g["required_before_state"] not in states:
            g.pop("required_before_state", None)
        for k in ("must_precede", "requires_prior"):
            if g.get(k) and g[k] not in tools:
                g.pop(k, None)
    # constraints referencing a missing state/tool → drop the constraint
    kept = []
    for c in spec.get("constraints", []):
        refs_state = [c.get("to"), (c.get("on_exceed") or {}).get("to")]
        refs_tool = [c.get("tool"), c.get("first"), c.get("second")]
        if any(s and s not in states for s in refs_state):
            continue
        if any(t and t not in tools for t in refs_tool):
            continue
        kept.append(c)
    spec["constraints"] = kept
    # faq routes: keep (templates bind to catalog, checked separately)


def save_flow_spec(
    company: str, spec: dict, new_templates: list[dict] | None = None
) -> dict[str, Any]:
    """Validate an edited FlowSpec against the company's catalog and write it.
    `new_templates` = [{fine_state, template}] authored in the editor — appended
    to the catalog (new fine_states only) so states can bind brand-new beats.
    No restart needed — FlowLiveSession reads spec + catalog fresh per session."""
    from demo.server.flow.flowspec import validate_flow_spec

    company = (company or "").strip().upper()
    reg = load_flow_registry()
    if company not in reg:
        return {"ok": False, "errors": [f"ไม่รู้จักบริษัท {company}"]}
    spec_path, cat_path = _flow_paths(company)
    catalog = json.loads(cat_path.read_text(encoding="utf-8")) if cat_path.exists() else []

    # Templates authored/edited in the editor. A new fine_state is appended; an
    # existing one UPDATES its (first) catalog entry's text — so the editor can
    # both add beats and rewrite existing lines.
    by_fs: dict[str, dict] = {}
    for e in catalog:
        by_fs.setdefault(e.get("_fine_state"), e)
    next_tid = (max((e.get("text_id", 999) for e in catalog), default=999) + 1)
    added = updated = 0
    for nt in (new_templates or []):
        fs = (nt.get("fine_state") or "").strip()
        text = (nt.get("template") or "").strip()
        if not fs or not text:
            continue
        if fs in by_fs:
            if by_fs[fs].get("template") != text:
                by_fs[fs]["template"] = text
                updated += 1
            continue
        entry = {
            "company": company, "text_id": next_tid, "template": text,
            "_fine_state": fs, "intent_name": fs, "category": "A",
            "state": fs.split("_")[0], "is_closer": False, "is_demand": False,
            "is_acknowledgment": False, "expects_response": True,
        }
        catalog.append(entry)
        by_fs[fs] = entry
        next_tid += 1
        added += 1

    spec["company"] = company
    spec.setdefault("flow_id", f"{company}-outbound-remind")
    spec.setdefault("spec_version", 2)
    _sanitize_spec(spec)  # drop dangling tool/state refs from editor edits
    errs, _ = validate_flow_spec(spec, catalog)
    if errs:
        return {"ok": False, "errors": errs[:10]}
    if added or updated:
        cat_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "company": company, "added_templates": added, "updated_templates": updated}


# --- Flow Builder: author a new company's flow from the UI -------------------

_FLOW_BASE_SPEC = REPO_ROOT / "data" / "flows" / "AEON-outbound-remind.json"
_FLOW_BASE_CATALOG = REPO_ROOT / "data" / "pre-scripts" / "v10_pre_script_database_parameterized.json"


# Human-readable Thai label per beat (what the line does), for the Builder UI.
BEAT_LABELS: dict[str, str] = {
    "greet_verify": "ทักทาย + ยืนยันตัวตน",
    "verify_name": "ยืนยันชื่อซ้ำ",
    "third_party": "ไม่ใช่เจ้าตัวรับสาย",
    "disclose_balance": "แจ้งยอดค้างชำระ",
    "ask_pay_today": "ชวนชำระวันนี้",
    "convince_lost_job": "โน้มน้าว (ตกงาน)",
    "convince_sick": "โน้มน้าว (ป่วย)",
    "convince_other": "โน้มน้าว (อื่นๆ)",
    "probe_hardship": "ถามสาเหตุที่จ่ายไม่ได้",
    "confirm_info": "สรุปข้อตกลง",
    "close": "ปิดสาย",
    "offer_callback": "เสนอโทรกลับ",
    "apology": "ขอโทษ / ติดต่อไม่ได้",
    "faq_caller": 'ตอบ "โทรจากไหน"',
    "ai_disclosure": 'ตอบ "เป็นบอทไหม"',
    "faq_hold": 'ตอบ "รอแป๊บ"',
    "faq_repeat": 'ตอบ "พูดอีกที"',
    "handoff_refuse": 'ตอบ "ขอคุยคนจริง"',
    "faq_scam": 'ตอบ "มิจฉาชีพรึเปล่า"',
    "faq_annoyed": 'ตอบ "รำคาญ / อย่าโทรมา"',
    "offer_channel_only": 'ตอบ "จ่ายที่ไหน / ยังไง"',
    "offer_channel": "เสนอช่องทางชำระ",
    "faq_amount": 'ตอบ "ยอดเท่าไหร่"',
    "faq_due": 'ตอบ "จ่ายเมื่อไหร่"',
    "faq_wrong_name": "เรียกชื่อผิด",
    "faq_mourning": "เจ้าของชื่อเสียชีวิต",
    "faq_faq_referral": "นอกขอบเขต → ให้เบอร์บริษัท",
    "other": "รับทราบกลางๆ (fallback)",
}
BEAT_REQUIRED = {"greet_verify"}


def _base_flow_bindings() -> "tuple[dict, list[str], dict, dict]":
    """(base_spec, ordered fine_states, {fs: hint}, {fs: phase}) from the base flow.
    phase ∈ {opening, main, close, faq, aux}."""
    spec = json.loads(_FLOW_BASE_SPEC.read_text(encoding="utf-8"))
    hint: dict[str, str] = {}
    phase: dict[str, str] = {}
    order: list[str] = []

    def add(fs: str, h: str, ph: str) -> None:
        if fs and fs not in hint:
            hint[fs] = h
            phase[fs] = ph
            order.append(fs)

    for st in spec["states"]:
        for t in st.get("templates", []):
            add(t["fine_state"], f"state:{st['id']}", st.get("phase", "main"))
    for r in spec.get("faq_routing", {}).get("routes", []):
        for t in r.get("templates", []):
            add(t["fine_state"], f"faq:{r.get('intent')} — {r.get('desc','')}", "faq")
    for t in spec.get("auxiliary_templates", {}).get("allowed", []):
        add(t["fine_state"], "auxiliary (ตามบริบท)", "aux")
    return spec, order, hint, phase


def flow_beats() -> list[dict[str, Any]]:
    """Base-flow beats for the Builder: fine_state + phase + Thai label + hint + example."""
    _, order, hint, phase = _base_flow_bindings()
    cat = json.loads(_FLOW_BASE_CATALOG.read_text(encoding="utf-8"))
    ex: dict[str, str] = {}
    for e in cat:
        ex.setdefault(e.get("_fine_state", ""), e.get("template", ""))
    return [{
        "fine_state": fs,
        "phase": phase[fs],
        "label": BEAT_LABELS.get(fs, fs),
        "required": fs in BEAT_REQUIRED,
        "hint": hint[fs],
        "example": ex.get(fs, ""),
    } for fs in order]


def _strip_unbound(spec: dict, keep: set[str]) -> None:
    """Drop template bindings whose fine_state isn't in `keep` (so the spec stays
    valid when the author leaves some beats blank)."""
    for st in spec["states"]:
        st["templates"] = [t for t in st.get("templates", []) if t.get("fine_state") in keep]
    for r in spec.get("faq_routing", {}).get("routes", []):
        r["templates"] = [t for t in r.get("templates", []) if t.get("fine_state") in keep]
    aux = spec.get("auxiliary_templates", {})
    if "allowed" in aux:
        aux["allowed"] = [t for t in aux["allowed"] if t.get("fine_state") in keep]


def _demo_persona(company: str, display_name: str, agent_name: str) -> dict[str, Any]:
    cid = f"TC-{company}-BUILD-001"
    cd = {
        "customer_name": "คุณสมมติ ทดสอบระบบ",
        "loan_type": "สินเชื่อ",
        "total_amount_due": 30000,
        "minimum_payment_due": 3000,
        "due_date": "2026-05-15 (Thursday)",
        "due_status": "overdue",
        "customer_phone": "081-234-5678",
        "msisdn": "081-234-5678",
        "last_4_digits": "1234",
        "case_status": "normal",
        "case_status_note": None,
        "company_name": display_name,
        "agent_name": agent_name,
    }
    return {
        "id": cid, "topic": f"{display_name} — flow demo persona", "eval_track": "Track_A",
        "patience": 3, "was_flipped": False, "customer_data": cd,
        "user_system_prompt": (
            f"<persona>ลูกค้าของ{display_name} ที่มียอดค้างชำระ</persona>\n"
            "<situation>รับสายจากเจ้าหน้าที่ติดตามหนี้</situation>\n"
            "<constraints>คุยตามธรรมชาติ</constraints>"
        ),
    }


def create_flow_company(
    company: str, display_name: str, agent_name: str, templates: dict[str, str],
    custom: list[dict] | None = None,
) -> dict[str, Any]:
    """Author a new flow company from Builder input. Writes catalog + spec, appends
    the registry + a demo persona. `custom` = [{fine_state, phase, template}] extra
    beats the author added; each is written to the catalog AND bound into a state
    of its phase. Returns {ok, case_id} or {ok:False, errors:[...]}."""
    from demo.server.flow.flowspec import validate_flow_spec

    company = (company or "").strip().upper()
    display_name = (display_name or "").strip()
    agent_name = (agent_name or "").strip() or display_name
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,11}", company):
        return {"ok": False, "errors": ["company code ต้องเป็น A-Z/0-9 (ขึ้นต้นด้วยตัวอักษร) 2–12 ตัว"]}
    if company in load_flow_registry():
        return {"ok": False, "errors": [f"บริษัท {company} มีอยู่แล้ว"]}
    if not display_name:
        return {"ok": False, "errors": ["ต้องระบุชื่อบริษัท (display name)"]}

    # keep only beats the author filled in
    filled = {fs: t.strip() for fs, t in (templates or {}).items() if t and t.strip()}
    if "greet_verify" not in filled:
        return {"ok": False, "errors": ["ต้องมีอย่างน้อย greet_verify (ประโยคเปิดสาย)"]}

    _, order, _, _ = _base_flow_bindings()
    catalog, tid = [], 1000
    for fs in order:
        if fs in filled:
            catalog.append({
                "company": company, "text_id": tid, "template": filled[fs],
                "_fine_state": fs, "intent_name": fs, "category": "A",
                "state": fs.split("_")[0], "is_closer": False, "is_demand": False,
                "is_acknowledgment": False, "expects_response": True,
            })
            tid += 1

    # Custom beats: add to catalog + remember for binding into the spec below.
    seen_fs = {e["_fine_state"] for e in catalog}
    to_bind: list[tuple[str, str]] = []  # (fine_state, phase)
    for c in (custom or []):
        fs = (c.get("fine_state") or "").strip()
        text = (c.get("template") or "").strip()
        phase = (c.get("phase") or "main").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", fs) or not text or fs in seen_fs:
            continue
        catalog.append({
            "company": company, "text_id": tid, "template": text,
            "_fine_state": fs, "intent_name": fs, "category": "A",
            "state": fs.split("_")[0], "is_closer": False, "is_demand": False,
            "is_acknowledgment": False, "expects_response": True,
        })
        seen_fs.add(fs)
        tid += 1
        to_bind.append((fs, phase))

    spec = json.loads(_FLOW_BASE_SPEC.read_text(encoding="utf-8"))
    spec["company"] = company
    spec["flow_id"] = f"{company}-outbound-remind"
    spec["description"] = f"Flow Builder — {display_name} outbound-remind (adapted from AEON base)."
    keep = set(filled) | {fs for fs, _ in to_bind}
    _strip_unbound(spec, keep)
    # Bind each custom beat into the first state of its phase (fallback: first state).
    for fs, phase in to_bind:
        st = next((s for s in spec["states"] if s.get("phase") == phase), None) or spec["states"][0]
        st.setdefault("templates", []).append({"fine_state": fs})

    errs, _ = validate_flow_spec(spec, catalog)
    if errs:
        return {"ok": False, "errors": errs[:8]}

    spec_name = f"{company}-outbound-remind.json"
    catalog_name = f"{company.lower()}_builder_catalog.json"
    (REPO_ROOT / "data" / "flows" / spec_name).write_text(
        json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    (REPO_ROOT / "data" / "pre-scripts" / catalog_name).write_text(
        json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")

    reg = load_flow_registry()
    reg[company] = {"spec": spec_name, "catalog": catalog_name, "display_name": display_name}
    FLOW_REGISTRY_FILE.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")

    persona = _demo_persona(company, display_name, agent_name)
    existing = []
    if BUILDER_CASES_FILE.exists():
        try:
            existing = json.loads(BUILDER_CASES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = []
    existing = [c for c in existing if c.get("id") != persona["id"]] + [persona]
    BUILDER_CASES_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=1), encoding="utf-8")

    return {"ok": True, "company": company, "case_id": persona["id"], "beats": len(catalog)}


def _flow_reply_schema(valid_text_ids: list[int]) -> dict:
    """Reply tool schema (enum = catalog ids) — inlined copy of
    aax6.training.prepare_flow_data._reply_schema so flow mode doesn't pull in
    that module's heavy training-time import chain (flowgen / trajectory_converter)."""
    return {
        "type": "function",
        "function": {
            "name": "reply",
            "description": "Reply to the customer with pre-approved script template(s).",
            "parameters": {
                "type": "object",
                "properties": {
                    "text_ids": {
                        "type": "array",
                        "items": {"type": "integer", "enum": sorted(valid_text_ids)},
                        "description": "text_id(s) of the chosen script template(s), in speaking order.",
                    },
                    "dynamic_vars": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                            "required": ["name", "value"],
                        },
                        "description": "List of {name, value} pairs to fill DYNAMIC placeholders in the chosen text_ids.",
                    },
                },
                "required": ["text_ids"],
            },
        },
    }


def _flow_vllm_chat(base_url: str, payload: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    resp = json.load(urllib.request.urlopen(req, timeout=timeout))
    return resp["choices"][0]["message"]


class FlowLiveSession:
    """Live flow-interpreter session: sft_flow_v1 × an AEON FlowSpec × the human
    caller. Ports aax6.simulation.flow_sim.run_conversation into the demo's
    streaming Session protocol, byte-consistent with training. The greeting is
    seeded from the spec's initial state and emitted by aiter_opening (the demo
    is outbound — the bot greets first)."""

    mode = "live"
    agent_name = "flow"

    def __init__(self, case_id: str, voice_gender: str = "F", model: str | None = None) -> None:
        self._model_override = model
        # Lazy imports — flow logic + its heavier deps load only when flow mode
        # is actually used, keeping the default replay/live paths cheap.
        from demo.server.flow.flowspec import build_tool_schemas
        from demo.server.flow.flowspec_render import render_instruction
        from demo.server.flow.spec_backend import SpecBackend
        from agents.prescript import build_script_catalog, fill_template
        from simulator.config import COMPANY_NAMES, COMPANY_AGENT_NAMES, COMPANY_PHONES
        from simulator import datetime_utils

        self.session_id = uuid.uuid4().hex[:12]
        self.voice_gender = voice_gender if voice_gender in ("M", "F") else "F"
        self._fill_template = fill_template
        self._SpecBackend = SpecBackend

        # Resolve to a persona whose company has a FlowSpec; else fall back to
        # the first persona of the fallback company.
        self.case_id = self._resolve_flow_case(case_id)
        self._case = _load_test_case(self.case_id)
        self._company = self.case_id.split("-")[1]

        cd = dict(self._case["customer_data"])
        cd.setdefault("company_phone", COMPANY_PHONES.get(self._company))
        cd.setdefault("company_name", COMPANY_NAMES.get(self._company))
        cd.setdefault("agent_name", COMPANY_AGENT_NAMES.get(self._company))
        cd.setdefault("today", datetime_utils.today_iso())
        self.customer_data = cd

        entry = load_flow_registry()[self._company]
        self._spec = json.loads(
            (REPO_ROOT / "data" / "flows" / entry["spec"]).read_text(encoding="utf-8"))
        self._catalog = json.loads(
            (REPO_ROOT / "data" / "pre-scripts" / entry["catalog"]).read_text(encoding="utf-8"))
        self._by_id = {e["text_id"]: e for e in self._catalog}

        system = self._fill_template(render_instruction(self._spec), cd, gender=self.voice_gender)
        system += "\n\n" + build_script_catalog(self._catalog, compact=True)
        self._system = system
        self._tools = build_tool_schemas(self._spec) + [
            _flow_reply_schema([e["text_id"] for e in self._catalog])
        ]

        self._base_url = os.environ.get("AAX6_VLLM_BASE_URL", "http://localhost:8000/v1")
        self._model = self._model_override or FLOW_MODEL
        self._turn_count = 0
        self.done = False
        self._last_turn_timing: dict[str, Any] | None = None
        self._transcript: list[dict[str, Any]] = []
        self._init_agent()

    # ---- public ----

    async def aiter_opening(self) -> AsyncIterator[dict[str, Any]]:
        """Bot-first outbound greeting: emit the spec-seeded opener. No LLM call."""
        if self._greeted:
            return
        self._greeted = True
        hops = self._greeting_hops()
        self._transcript.append({"user": "", "hops": hops})
        self._last_turn_timing = {"llm_ms": 0.0, "llm_hops": 0}
        for h in hops:
            yield h

    async def aiter_turn(self, user_msg: str) -> AsyncIterator[dict[str, Any]]:
        if self.done:
            return
        # If the caller speaks before the opening was fired, seed the greeting
        # into history first (keeps the trained greeting→customer→agent order).
        # _greeting_hops() appends the anchor to self._messages as a side effect;
        # the returned hops are dropped since the opening bubble was skipped.
        if not self._greeted:
            self._greeted = True
            self._greeting_hops()
        async for hop in self._aiter_run(user_msg):
            yield hop

    def reset_pointer(self) -> None:
        self._turn_count = 0
        self.done = False
        self._transcript = []
        self._init_agent()

    async def prewarm(self) -> None:  # protocol parity; flow mode skips prewarm
        return

    # ---- helpers ----

    @staticmethod
    def _resolve_flow_case(case_id: str) -> str:
        """Honor the requested persona if its company has a FlowSpec; otherwise
        fall back to the first persona of the fallback company."""
        registry = load_flow_registry()
        cases = _all_cases()
        ids = {c.get("id") for c in cases}
        parts = case_id.split("-")
        if case_id in ids and len(parts) > 1 and parts[1] in registry:
            return case_id
        for c in cases:
            cid = c.get("id", "")
            if cid.split("-")[1:2] == [FLOW_FALLBACK_COMPANY]:
                return cid
        raise KeyError(f"no {FLOW_FALLBACK_COMPANY} persona found")

    def _init_agent(self) -> None:
        self._backend = self._SpecBackend(
            {k: v for k, v in self.customer_data.items() if not str(k).startswith("_")},
            self._spec,
        )
        self._messages: list[dict[str, Any]] = [{"role": "system", "content": self._system}]
        self._greeted = False

    def _greeting_hops(self) -> list[dict[str, Any]]:
        spec, cd = self._spec, self.customer_data
        init = next(st for st in spec["states"] if st.get("initial"))
        greet_fs = init["templates"][0]["fine_state"]
        cands = [e for e in self._catalog if e.get("_fine_state") == greet_fs]
        entry = cands[0] if cands else self._catalog[0]
        text = self._fill_template(entry["template"], cd, gender=self.voice_gender)
        args = {"text_ids": [entry["text_id"]], "dynamic_vars": []}
        self._messages.append({
            "role": "assistant", "content": text,
            "tool_calls": [{"id": "call_seed_greeting", "type": "function",
                            "function": {"name": "reply",
                                         "arguments": json.dumps(args, ensure_ascii=False)}}],
        })
        return [
            {"kind": "tool_call", "name": "reply", "args": args},
            {"kind": "reply", "text": text, "text_ids": [entry["text_id"]], "dynamic_vars": {}},
        ]

    def _render_reply(self, args: dict) -> tuple[list[int], str, dict]:
        """Resolve reply text_ids → rendered Thai; tolerant of the qwen3_xml
        parser handing back stringified args (mirrors flow_sim.render_reply)."""
        ids = args.get("text_ids", [])
        if isinstance(ids, str):
            try:
                ids = json.loads(ids)
            except json.JSONDecodeError:
                ids = [x for x in ids.replace("[", " ").replace("]", " ").replace(",", " ").split()
                       if x.isdigit()]
        if isinstance(ids, int):
            ids = [ids]
        dyn = args.get("dynamic_vars") or []
        if isinstance(dyn, str):
            try:
                dyn = json.loads(dyn)
            except json.JSONDecodeError:
                dyn = []
        if isinstance(dyn, list):
            dyn = {d.get("name"): d.get("value") for d in dyn if isinstance(d, dict)}
        seen: set[int] = set()
        good, texts = [], []
        for tid in ids:
            e = self._by_id.get(int(tid)) if str(tid).lstrip("-").isdigit() else None
            if e is None or int(tid) in seen:
                continue
            seen.add(int(tid))
            good.append(int(tid))
            texts.append(self._fill_template(
                e["template"], self.customer_data, dynamic_vars=dyn, gender=self.voice_gender))
        return good, " ".join(texts), dyn if isinstance(dyn, dict) else {}

    async def _aiter_run(self, user_msg: str) -> AsyncIterator[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        _SENTINEL = object()
        turn_hops: list[dict[str, Any]] = []

        def push(hop: dict[str, Any]) -> None:
            turn_hops.append(hop)
            loop.call_soon_threadsafe(queue.put_nowait, hop)

        def blocking() -> tuple[str, float, int]:
            llm_ms, llm_hops = 0.0, 0
            agent_text = ""
            try:
                self._messages.append({"role": "user", "content": user_msg})
                for _loop in range(FLOW_MAX_TOOL_LOOPS):
                    t0 = time.perf_counter()
                    msg = _flow_vllm_chat(self._base_url, {
                        "model": self._model, "messages": self._messages,
                        "tools": self._tools, "temperature": 0.0, "max_tokens": 400,
                    })
                    llm_ms += (time.perf_counter() - t0) * 1000.0
                    llm_hops += 1
                    tcs = msg.get("tool_calls") or []
                    if not tcs:
                        agent_text = (msg.get("content") or "").strip()
                        if agent_text:
                            push({"kind": "reply", "text": agent_text, "text_ids": [], "dynamic_vars": {}})
                        break
                    tc = tcs[0]
                    fn = tc["function"]
                    raw_args = fn.get("arguments")
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                    if fn["name"] == "reply":
                        ids, text, dyn = self._render_reply(args)
                        clean_args = {"text_ids": ids, "dynamic_vars": args.get("dynamic_vars") or []}
                        self._messages.append({
                            "role": "assistant", "content": text,
                            "tool_calls": [{"id": tc.get("id", "call_x"), "type": "function",
                                            "function": {"name": "reply",
                                                         "arguments": json.dumps(clean_args, ensure_ascii=False)}}],
                        })
                        push({"kind": "tool_call", "name": "reply", "args": clean_args})
                        push({"kind": "reply", "text": text, "text_ids": ids, "dynamic_vars": dyn})
                        agent_text = text
                        break
                    result = self._backend.dispatch(fn["name"], args)
                    self._messages.append({
                        "role": "assistant",
                        "tool_calls": [{"id": tc.get("id", "call_x"), "type": "function",
                                        "function": {"name": fn["name"],
                                                     "arguments": json.dumps(args, ensure_ascii=False)}}],
                    })
                    self._messages.append({"role": "tool", "tool_call_id": tc.get("id", "call_x"),
                                           "content": json.dumps(result, ensure_ascii=False)})
                    push({"kind": "tool_call", "name": fn["name"], "args": args})
                    push({"kind": "tool_result", "name": fn["name"], "result": result})
                return agent_text, llm_ms, llm_hops
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

        task = asyncio.create_task(asyncio.to_thread(blocking))
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                yield item
        finally:
            agent_text, llm_ms, llm_hops = await task

        self._transcript.append({"user": user_msg, "hops": turn_hops})
        self._last_turn_timing = {"llm_ms": round(llm_ms, 1), "llm_hops": llm_hops}
        self._turn_count += 1
        if "[TASK_COMPLETED]" in (agent_text or ""):
            self.done = True
        if self._turn_count >= MAX_LIVE_TURNS:
            self.done = True


# ---------------------------------------------------------------------------
# Save trajectory
# ---------------------------------------------------------------------------


def build_trajectory_case(session: "LiveSession", comment: str = "") -> dict[str, Any]:
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
        "comment": comment,
        "session_id": session.session_id,
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "agent_messages": getattr(session._agent, "history", []),
    }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build(
    case_id: str,
    mode: str,
    agent: str = DEFAULT_AGENT,
    voice_gender: str = "F",
    flow: bool = False,
    model: str | None = None,
) -> Session:
    if flow:  # flow-interpreter is always live — it can't replay
        return FlowLiveSession(case_id, voice_gender=voice_gender, model=model)
    if mode == "live":
        return LiveSession(case_id, agent=agent, voice_gender=voice_gender, model=model)
    return ReplaySession(case_id, voice_gender=voice_gender)


def served_models() -> dict[str, list[str]]:
    """Qwen checkpoints currently served by vLLM, split into flow vs pre-flow
    (base). Powers the demo's model-version picker. Empty on any error."""
    base_url = os.environ.get("AAX6_VLLM_BASE_URL", "http://localhost:8000/v1")
    try:
        import urllib.request
        with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=5) as r:
            ids = [m["id"] for m in json.load(r).get("data", [])]
    except Exception:
        return {"base": [], "flow": []}
    # Only the `sft_flow_*` adapters are aligned with the current FlowSpec/editor.
    # sft_v10/v11 are older flow-pipeline models that predate it — exclude them
    # from the picker (they don't fit the current specs). The raw base is the
    # prescript (qwen) engine's model.
    flow = sorted(i for i in ids if i.lower().startswith("sft_flow"))
    base = [i for i in ids if not i.lower().startswith("sft")]
    return {"base": base, "flow": flow}
