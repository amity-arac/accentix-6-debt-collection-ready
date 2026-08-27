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
import logging
import os
import re
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Protocol

from demo_v2.server import tts


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
# Spoken by the UI while a tool call is in flight. It used to live in the replay
# module, which owned nothing else once replay mode was dropped.
FILLER_TEXT = ("" if os.environ.get("AAX6_PROMPT_VERSION", "").strip() in ("v10", "v11")
               else "รบกวนรอซักครู่ค่ะ")


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


def case_customer_data(case_id: str) -> dict[str, Any]:
    """A case's raw `customer_data`. Public so the demo's mock-CRM route can serve
    the shipped fixtures without reaching into a private loader."""
    return dict(_load_test_case(case_id).get("customer_data") or {})


# Keys that describe the persona rather than the caller — everything else in
# `customer_data` is the tenant's CRM row and is shown as-is. The picker used to lift a
# fixed list (loan_type, total_amount_due, minimum_payment_due, due_status, …), which is
# one domain's schema: a clinic's `doctor_name` was simply not displayed, and every
# tenant carried those column names whether they meant anything or not.
_NON_CRM_KEYS = frozenset({"msisdn", "case_ref"})


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
    # Resolve the same way a session does, or the picker lists a date the agent will
    # never say: personas store `<field>_offset_days`, so the raw row has no `due_date`
    # at all and the column read empty while the call quoted a real one.
    cd = dict(cd)
    _resolve_offset_dates(cd)
    for field, value in cd.items():
        if field in _NON_CRM_KEYS or field.endswith("_offset_days"):
            continue
        row.setdefault(field, value)
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
from demo_v2.server.flow.flowspec import load_tenant_spec  # noqa: E402

FLOW_DIR = REPO_ROOT / "data" / "flows"
# The one artifact a tenant supplies. The suffix IS the contract: a file named this
# way, holding a spec this app can validate, is a working outbound agent.
TENANT_SUFFIX = ".company.json"
# flow_registry.json is the only source of companies. There is no seeded default:
# a shipped fallback company means an app that still half-works with no data, and
# what it half-works as is whoever was hardcoded here.
_FLOW_REGISTRY_DEFAULT: dict[str, dict[str, str]] = {}
FLOW_FALLBACK_COMPANY = os.environ.get("AAX6_FLOW_COMPANY", "")
# Static fallback name (env override), used only if NOTHING is being served (error path).
FLOW_MODEL = os.environ.get("AAX6_FLOW_MODEL", "grpo540")
FLOW_MAX_TOOL_LOOPS = 8


def _default_served_model() -> str:
    """The model to use when the caller didn't pick one (frontend hasn't loaded the
    picker yet, or sent ""). MUST be one actually served — a stale hardcoded name here
    (e.g. an old checkpoint id) 404s at vLLM and the turn silently fails with no reply.
    Prefers AAX6_FLOW_MODEL if it's actually being served; else the first served model;
    else AAX6_FLOW_MODEL as a last resort (will surface a clear 404 upstream)."""
    served = list(_model_endpoints().keys())
    if FLOW_MODEL in served:
        return FLOW_MODEL
    return served[0] if served else FLOW_MODEL


def load_flow_registry() -> dict[str, dict[str, str]]:
    """company code -> {spec, display_name}, DERIVED from the tenant files present.

    One tenant = one `<CODE>.company.json` in data/flows. Onboarding is dropping that
    file in; off-boarding is deleting it. There is no index to keep in step, which is
    the failure this replaces: a spec could be present and invisible, or listed and
    missing, and the app would start either way. The tenant's own `company` field and
    `display_name` come from inside its file, so the platform stores nothing about it.
    """
    out: dict[str, dict[str, str]] = {}
    for f in sorted(FLOW_DIR.glob("*" + TENANT_SUFFIX)):
        if f.name.startswith("_"):
            continue                     # `_TEMPLATE.company.json` is the blank, not a tenant
        try:
            spec = load_tenant_spec(f)
        except (json.JSONDecodeError, OSError):
            log.warning("flow registry: skipping unreadable tenant file %s", f.name)
            continue
        code = str(spec.get("company") or f.name[: -len(TENANT_SUFFIX)]).strip()
        if not code:
            continue
        out[code] = {"spec": f.name,
                     "display_name": spec.get("display_name") or spec.get("company") or code}
    return out


def _write_tenant(company: str, spec: dict, catalog: list[dict],
                  display_name: str = "") -> Path:
    """Persist one tenant as one file: `<CODE>.company.json`, catalog inlined.

    Both authoring paths end here, so there is exactly one on-disk shape to validate,
    read, back up, hand to a tenant, or accept back from one. `display_name` travels
    inside the spec because it describes the tenant, and the platform keeps no
    per-tenant record of its own.
    """
    body = {k: v for k, v in spec.items() if k not in ("catalog", "catalog_inline",
                                                      "company", "spec_version")}
    if display_name:
        body["display_name"] = display_name
    # `catalog` IS the list, and `company` is not repeated — the filename carries it.
    # Writing the older two-key shape here meant a file this app produced did not match
    # the one it documents, and round-tripping an export through upload changed it.
    body["catalog"] = catalog
    f = FLOW_DIR / f"{company}{TENANT_SUFFIX}"
    f.write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return f


def flow_companies() -> list[str]:
    return list(load_flow_registry())


def flow_companies_meta() -> list[dict[str, Any]]:
    """Company code + label + whether it may be deleted. Deletability is decided
    HERE, next to `delete_flow_company`, so the UI never has to keep its own copy
    of the rule (and never offers a delete the server will refuse)."""
    out = []
    for code, entry in load_flow_registry().items():
        # The customer panel used to render a fixed set of debt columns (loan type,
        # balance, minimum payment, last 4, due status). A shop's row has none of them,
        # so every slot showed a dash while the agent quoted real values. The spec
        # already names its own fields and their Thai labels — send those and let the
        # panel show what this tenant actually has.
        labels: dict[str, str] = {}
        fields: list[str] = []
        try:
            spec = load_tenant_spec(REPO_ROOT / "data" / "flows" / entry["spec"])
            labels = spec.get("crm_labels") or {}
            fields = list(spec.get("crm_fields") or [])
        except Exception:      # noqa: BLE001 — a listing must not die on one bad spec
            pass
        out.append({"company": code,
                    "display_name": entry.get("display_name") or code,
                    # every tenant owns its own file, so every tenant can be removed
                    "deletable": True,
                    "crm_fields": fields, "crm_labels": labels})
    return out


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


def _resolve_offset_dates(cd: dict) -> None:
    """`<field>_offset_days: N` → `<field>` = today+N, resolved per session.

    A persona that stores a literal date is right on the day it was written and wrong
    every day after — the appointment reminder told the patient about a visit three
    months in the past. `due_offset_days` already existed for one field; this is the
    same rule for any of them, so a tenant can anchor whatever its templates speak.
    """
    from demo_v2.lib import datetime_utils as _du
    for key in [k for k in cd if k.endswith("_offset_days")]:
        n = cd.get(key)
        if n is None:
            continue
        try:
            cd[key[: -len("_offset_days")]] = _du.future_date(int(n))
        except (TypeError, ValueError):
            continue
        # The offset is how a date is authored, not a fact about the customer. Left in
        # the row it showed up in the panel beside the date it produced, and in the
        # render context the templates read. Measured to change nothing either way.
        cd.pop(key, None)



def _flow_spec_path(company: str, instruction_version: str | None = None) -> "Any":
    """The FlowSpec file for a company. A company is ONE file.

    There used to be versioned overrides beside it (`{stem}__{version}.json`), and the
    canonical file naming an `instruction_version` redirected here — AEON's did, so
    every edit to the canonical AEON spec was read by nobody while `__v11.2` was
    served. No override file exists any more, so the lookup only ever fell through to
    the canonical file while still reporting a version that pointed nowhere. The
    parameter stays so old callers keep working; it is ignored.
    """
    return REPO_ROOT / "data" / "flows" / load_flow_registry()[company]["spec"]


def _read_catalog(spec: "dict | None", cat_path) -> list[dict]:
    """Templates for a company, from wherever that company keeps them: inside the
    spec (single-file) or in the catalog file the registry names (split)."""
    if spec:
        try:
            from demo_v2.server.flow.flowspec import resolve_catalog
            return resolve_catalog(spec)
        except (ValueError, KeyError, OSError):
            pass
    if cat_path is not None and cat_path.exists():
        return json.loads(cat_path.read_text(encoding="utf-8"))
    return []


def _flow_paths(company: str) -> "tuple[Any, Any]":
    """(spec path, catalog path). The catalog path is None for a single-file
    company — its templates live inside the spec."""
    entry = load_flow_registry()[company]
    cat = entry.get("catalog")
    return (REPO_ROOT / "data" / "flows" / entry["spec"],
            (REPO_ROOT / "data" / "pre-scripts" / cat) if cat else None)


def flow_versions(company: str) -> "dict[str, Any]":
    """Available instruction versions for a company's flow + the default.

    The canonical spec (registry) supplies the default (its `instruction_version`
    field, else 'latest'); each `{stem}__{ver}.json` override adds a selectable
    version. Powers the demo's instruction-version picker (A/B v11 vs v11.1)."""
    company = (company or "").strip().upper()
    reg = load_flow_registry()
    if company not in reg:
        return {"versions": [], "default": ""}
    base = REPO_ROOT / "data" / "flows" / reg[company]["spec"]
    default = "latest"
    if base.exists():
        try:
            default = json.loads(base.read_text(encoding="utf-8")).get("instruction_version") or "latest"
        except (json.JSONDecodeError, OSError):
            pass
    versions = {default}
    for p in base.parent.glob(base.stem + "__*.json"):
        ver = p.stem[len(base.stem) + 2:]  # strip "{stem}__"
        if ver:
            versions.add(ver)
    # newest-looking last→first: plain sort is fine for v11 < v11.1
    return {"versions": sorted(versions), "default": default}


def flow_instruction(company: str) -> str:
    """The rendered system instruction for a company's flow — exactly what
    FlowLiveSession feeds the model (render_instruction(spec) + the catalog),
    with [placeholders] intact (filled per-call at runtime)."""
    from demo_v2.server.flow.flowspec_render import render_instruction
    from demo_v2.lib.prescript import build_script_catalog

    company = (company or "").strip().upper()
    if company not in load_flow_registry():
        return ""
    spec_path, cat_path = _flow_paths(company)
    if not spec_path.exists():
        return ""
    spec = load_tenant_spec(spec_path)
    catalog = _read_catalog(spec, cat_path)
    from demo_v2.server.flow.flowspec import normalize_catalog
    return (render_instruction(spec) + "\n\n"
            + build_script_catalog(normalize_catalog(catalog, spec), compact=False))


def flow_prescripts(company: str, instruction_version: str | None = None) -> dict[str, Any]:
    """ทุก pre-script ของบริษัท (สำหรับหน้าอ่าน pre-script ข้างๆ playground).
    default = catalog + spec เดียวกับที่ FlowLiveSession ใช้ (sft_v11 + instruction v14.1)
    คืน: {company, version, states:[{state, phase, beats:[...]}], entries:[{text_id, fine_state,
          template, phase, bound}], counts}"""
    company = (company or "").strip().upper()
    reg = load_flow_registry()
    if company not in reg:
        return {}
    spec_path = _flow_spec_path(company, instruction_version)
    if not spec_path.exists():
        return {}
    spec = load_tenant_spec(spec_path)
    # catalog ที่ spec เวอร์ชันนั้นประกาศไว้ (v14.1 → v14_aeon_flow_catalog) — อ่านเพื่อแสดงเท่านั้น
    # `catalog` is the inline list of templates now; it was a path to a catalog file
    # in the older shape, and this still joined it onto REPO_ROOT — a TypeError that
    # made /api/flow/spec and /api/flow/prescripts 500 for every company.
    cat_path = None
    if isinstance(spec.get("catalog"), str):
        p_spec = REPO_ROOT / spec["catalog"]
        if p_spec.exists(): cat_path = p_spec
    if cat_path is None:
        _, cat_path = _flow_paths(company)
    catalog = _read_catalog(spec, cat_path)

    # fine_state → (state, phase) จาก spec + ลำดับของ beat ใน flow
    fs_state: dict[str, tuple[str, str]] = {}
    states_out: list[dict[str, Any]] = []
    for st in spec.get("states", []):
        beats = [t.get("fine_state") for t in st.get("templates", []) if t.get("fine_state")]
        for b in beats:
            fs_state.setdefault(b, (st.get("id", ""), st.get("phase", "")))
        states_out.append({"state": st.get("id", ""), "phase": st.get("phase", ""),
                           "note": st.get("note", ""), "beats": beats})
    # faq_routing: route มี templates:[{fine_state}] — นับเป็น bound (กลุ่ม "faq")
    faq_beats = []
    for r in (spec.get("faq_routing", {}) or {}).get("routes", []):
        for t in (r.get("templates") or []):
            fsx = t.get("fine_state") if isinstance(t, dict) else t
            if isinstance(fsx, str):
                fs_state.setdefault(fsx, ("faq", "faq")); faq_beats.append(fsx)
    if faq_beats:
        states_out.append({"state": "faq", "phase": "faq",
                           "note": "คำถามแทรกจากลูกค้า — ตอบแล้วกลับเข้า flow เดิม",
                           "beats": faq_beats})
    # auxiliary_templates (ถ้ามี) — ใช้ได้ตามบริบท ไม่ผูก state
    aux_beats = []
    for a in (spec.get("auxiliary_templates", []) or []):
        fsx = a.get("fine_state") if isinstance(a, dict) else a
        if isinstance(fsx, str):
            fs_state.setdefault(fsx, ("aux", "aux")); aux_beats.append(fsx)
    if aux_beats:
        states_out.append({"state": "aux", "phase": "aux",
                           "note": "ใช้ได้ตามบริบท (ไม่ผูก state)", "beats": aux_beats})

    entries = []
    for e in catalog:
        fs = e.get("_fine_state") or ""
        st, ph = fs_state.get(fs, ("", ""))
        entries.append({"text_id": e.get("text_id"), "fine_state": fs, "template": e.get("template", ""),
                        "state": st, "phase": ph, "bound": bool(st),
                        "intent_name": e.get("intent_name", ""), "category": e.get("category", "")})
    entries.sort(key=lambda x: (not x["bound"], str(x["state"]), str(x["text_id"])))
    return {"company": company, "display_name": reg[company].get("display_name", company),
            "version": instruction_version or flow_versions(company).get("default", ""),
            "catalog_file": cat_path.name if cat_path else "",
            "spec_file": spec_path.name, "states": states_out, "entries": entries,
            "counts": {"templates": len(entries), "bound": sum(1 for x in entries if x["bound"]),
                       "fine_states": len({x["fine_state"] for x in entries if x["fine_state"]})}}


def get_flow_spec(company: str) -> dict[str, Any]:
    """Load a company's FlowSpec + the vocab the editor needs (catalog fine_states,
    declared tool names). Returns {} if the company/spec isn't found."""
    company = (company or "").strip().upper()
    if company not in load_flow_registry():
        return {}
    spec_path, cat_path = _flow_paths(company)
    if not spec_path.exists():
        return {}
    spec = load_tenant_spec(spec_path)
    # `_flow_paths` returns None for the catalog when the spec carries it inline,
    # which is every spec now — this used to call .exists() on that None.
    fine_states: list[str] = []
    templates: dict[str, list[str]] = {}
    cat = _read_catalog(spec, cat_path)
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
    from demo_v2.server.flow.flowspec import (normalize_catalog, validate_flow_spec,
                                           validate_strict)

    company = (company or "").strip().upper()
    reg = load_flow_registry()
    if company not in reg:
        return {"ok": False, "errors": [f"ไม่รู้จักบริษัท {company}"]}
    spec_path, cat_path = _flow_paths(company)
    spec_now = load_tenant_spec(spec_path) if spec_path.exists() else None
    catalog = _read_catalog(spec_now, cat_path)

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
    from demo_v2.server.flow.flowspec import normalize_catalog
    # Freeze the derived fields AT CREATION and store those. The author may omit
    # text_id; assigning it once here means the ids never move again, instead of
    # being recomputed on every load where a later edit could shift them.
    catalog = normalize_catalog(catalog, spec)
    errs, _ = validate_flow_spec(spec, catalog)
    # The key-level lock runs on the same call: a spec that validates structurally
    # but carries a retired or misspelled key is rejected HERE, at the moment it
    # would be written, rather than being accepted and doing nothing at runtime.
    errs = errs + validate_strict(spec, catalog)
    if errs:
        return {"ok": False, "errors": errs[:10]}
    if added or updated:
        cat_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "company": company, "added_templates": added, "updated_templates": updated}


# --- Flow Builder: author a new company's flow from the UI -------------------

# The Builder starts a new company from a base flow. That base is DATA — a template
# file the deployment owns and can replace — never a live company's spec. Pointing
# this at a shipped customer's flow meant "author a new company" silently began as a
# copy of that customer's states, tools and outcome codes, and the app had to name
# them to do it. Both paths are overridable so a deployment with a different domain
# supplies its own starting point.
_FLOW_BASE_SPEC = Path(os.environ.get("AAX6_FLOW_BASE_SPEC", "")) if os.environ.get(
    "AAX6_FLOW_BASE_SPEC") else FLOW_DIR / "_TEMPLATE.company.json"
_FLOW_BASE_CATALOG = Path(os.environ.get("AAX6_FLOW_BASE_CATALOG", "")) if os.environ.get(
    "AAX6_FLOW_BASE_CATALOG") else REPO_ROOT / "data" / "pre-scripts" / "v10_pre_script_database_parameterized.json"


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
    spec = load_tenant_spec(_FLOW_BASE_SPEC)
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
    """The playground's stand-in caller for a company that has just been uploaded.

    It carries no CRM row. It used to invent one — a 30,000-baht loan, a minimum
    payment, an overdue status, and a prompt telling the simulated customer it was
    "รับสายจากเจ้าหน้าที่ติดตามหนี้" — so a clinic that uploaded an appointment flow got
    a patient in debt collection, and every template that speaks a real field read a
    number this file made up. Whatever the caller's account holds is the tenant's to
    say: the spec's `session_init` fetches it, and `crm_fields` decides what the model
    is allowed to see. What stays here is only what identifies the request
    (`msisdn`) and the two names the instruction renders.
    """
    return {
        "id": f"TC-{company}-BUILD-001",
        "topic": f"{display_name} — flow demo persona",
        "eval_track": "Track_A", "patience": 3, "was_flipped": False,
        "customer_data": {
            "msisdn": "081-234-5678",
            "company_name": display_name,
            "agent_name": agent_name,
        },
        "user_system_prompt": (
            f"<persona>ผู้รับสายจาก{display_name}</persona>\n"
            "<situation>รับสายเข้ามาโดยไม่ได้นัดไว้ล่วงหน้า</situation>\n"
            "<constraints>คุยตามธรรมชาติ ตอบตามที่ตัวเองรู้</constraints>"
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
    from demo_v2.server.flow.flowspec import (normalize_catalog, validate_flow_spec,
                                           validate_strict)

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

    spec = load_tenant_spec(_FLOW_BASE_SPEC)
    spec["company"] = company
    # keep the base's own flow kind (…-outbound-remind, …-outbound-appointment) so a
    # replaced base does not still label everything a reminder call
    kind = "-".join(str(spec.get("flow_id", "flow")).split("-")[1:]) or "flow"
    spec["flow_id"] = f"{company}-{kind}"
    spec["description"] = f"Flow Builder — {display_name} {kind} (from {_FLOW_BASE_SPEC.name})."
    keep = set(filled) | {fs for fs, _ in to_bind}
    _strip_unbound(spec, keep)
    # Bind each custom beat into the first state of its phase (fallback: first state).
    for fs, phase in to_bind:
        st = next((s for s in spec["states"] if s.get("phase") == phase), None) or spec["states"][0]
        st.setdefault("templates", []).append({"fine_state": fs})

    # Freeze the derived fields AT CREATION and store those. The author may omit
    # text_id; assigning it once here means the ids never move again, instead of
    # being recomputed on every load where a later edit could shift them.
    catalog = normalize_catalog(catalog, spec)
    errs, _ = validate_flow_spec(spec, catalog)
    # The key-level lock runs on the same call: a spec that validates structurally
    # but carries a retired or misspelled key is rejected HERE, at the moment it
    # would be written, rather than being accepted and doing nothing at runtime.
    errs = errs + validate_strict(spec, catalog)
    if errs:
        return {"ok": False, "errors": errs[:8]}

    # ONE file, the same one the JSON-editor path writes. The Builder used to emit a
    # spec plus a separate catalog plus an index entry — three artifacts that could
    # disagree, and two of them the tenant never saw.
    _write_tenant(company, spec, catalog, display_name)

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


def create_flow_company_raw(
    spec: dict, catalog: list[dict],
    display_name: str = "", agent_name: str = "",
) -> dict[str, Any]:
    """Author a new flow company from a RAW FlowSpec + catalog JSON — the JSON-editor
    path (no AEON clone, no prefill). The company code is read from ``spec['company']``.
    Validates via ``validate_flow_spec`` and, only when it passes, writes the spec +
    catalog, appends the registry, and writes a demo persona. Returns {ok, company,
    case_id, beats} or {ok:False, errors:[...]}."""
    from demo_v2.server.flow.flowspec import (normalize_catalog, validate_flow_spec,
                                           validate_strict)

    if not isinstance(spec, dict):
        return {"ok": False, "errors": ["spec ต้องเป็น JSON object"]}
    if not isinstance(catalog, list):
        return {"ok": False, "errors": ["catalog ต้องเป็น JSON array"]}

    # A tenant file no longer has to repeat its own code — the filename carries it, and
    # the files this app writes leave it out. An upload may still name it (the blank
    # template does), and `display_name` is the fallback so an export of a shipped
    # company can be sent straight back without hand-editing.
    company = str(spec.get("company") or spec.get("flow_id", "").split("-")[0]
                  or display_name).strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,11}", company):
        return {"ok": False, "errors": [
            "ระบุรหัสบริษัทไม่ได้ — ใส่ `company` หรือ `flow_id` ใน spec "
            "(A-Z/0-9 ขึ้นต้นด้วยตัวอักษร 2–12 ตัว)"]}
    if company in load_flow_registry():
        return {"ok": False, "errors": [f"บริษัท {company} มีอยู่แล้ว"]}

    # keep the spec's own company/flow_id consistent with the code
    spec["company"] = company
    flow_id = str(spec.get("flow_id") or f"{company}-outbound-remind")
    spec["flow_id"] = flow_id

    # Freeze the derived fields AT CREATION and store those. The author may omit
    # text_id; assigning it once here means the ids never move again, instead of
    # being recomputed on every load where a later edit could shift them.
    catalog = normalize_catalog(catalog, spec)
    errs, _ = validate_flow_spec(spec, catalog)
    # The key-level lock runs on the same call: a spec that validates structurally
    # but carries a retired or misspelled key is rejected HERE, at the moment it
    # would be written, rather than being accepted and doing nothing at runtime.
    errs = errs + validate_strict(spec, catalog)
    if errs:
        return {"ok": False, "errors": errs[:8]}

    display_name = (display_name or "").strip() or company
    agent_name = (agent_name or "").strip() or display_name

    # One company, one file: the templates go INSIDE the spec. Two files could only
    # ever drift apart, and the training side already reads this layout
    # (`resolve_catalog` inline-first) — so a spec authored here can be handed
    # straight to it. The four shipped companies keep their split layout; both are
    # read the same way.
    _write_tenant(company, spec, catalog, display_name)

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


_TOOLCALL_JSON_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_TOOLCALL_TAG_RE = re.compile(r"</?tool_call>")


def _recover_toolcalls(content: str) -> list[dict]:
    """Some adapters emit tool calls as literal <tool_call>{...}</tool_call> text
    that vLLM's parser misses. Recover them into the OpenAI tool_calls shape."""
    out = []
    for m in _TOOLCALL_JSON_RE.finditer(content or ""):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        name = obj.get("name")
        args = obj.get("arguments", obj.get("parameters", {}))
        if name:
            out.append({"id": "call_rec", "type": "function", "function": {
                "name": name,
                "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)}})
    return out


def _strip_toolcall_markup(content: str) -> str:
    """Drop any leaked tool-call XML so it never reaches the customer/TTS."""
    return _TOOLCALL_TAG_RE.sub("", _TOOLCALL_JSON_RE.sub("", content or "")).strip()


def _flow_vllm_chat(base_url: str, payload: dict, timeout: int = 180) -> dict:
    # Thinking OFF, explicitly. The chat template turns it ON whenever
    # `enable_thinking` is undefined, so saying nothing was not "the default" —
    # it was a choice nobody made. The eval harness has always sent False, and the
    # model was trained that way, so serving with it on was an eval/serve split.
    # Measured with it on: the model spends ~2x the tokens, duplicates beats,
    # closed a postpone request as a PTP and then looped `callback_datetime` seven
    # times. It also writes its reasoning into `content`, and a turn that returns
    # content without a tool call is spoken to the customer verbatim.
    payload = {**payload, "chat_template_kwargs":
               {**(payload.get("chat_template_kwargs") or {}), "enable_thinking": False}}
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

    def __init__(self, case_id: str, voice_gender: str = "F", model: str | None = None,
                 instruction_version: str | None = None) -> None:
        self._model_override = model
        # `instruction_version` is accepted so old callers keep working, and ignored:
        # the versioned override files it selected (`{stem}__{version}.json`) are gone
        # and a company is one file.
        self._instruction_version = instruction_version
        # Lazy imports — flow logic + its heavier deps load only when flow mode
        # is actually used, keeping the default replay/live paths cheap.
        from demo_v2.server.flow.flowspec import build_tool_schemas
        from demo_v2.server.flow.flowspec_render import render_instruction
        from demo_v2.server.flow.spec_backend import SpecBackend
        from demo_v2.lib.prescript import DateFormatError, build_script_catalog, fill_template
        from demo_v2.lib import datetime_utils

        self.session_id = uuid.uuid4().hex[:12]
        self.voice_gender = voice_gender if voice_gender in ("M", "F") else "F"
        self._fill_template = fill_template
        self._DateFormatError = DateFormatError
        self._SpecBackend = SpecBackend

        # Resolve to a persona whose company has a FlowSpec; else fall back to
        # the first persona of the fallback company.
        self.case_id = self._resolve_flow_case(case_id)
        self._case = _load_test_case(self.case_id)
        self._company = self.case_id.split("-")[1]

        cd = dict(self._case["customer_data"])
        # A tenant's own name, phone and agent name come from its persona row and its
        # `session_init` response — never from a table in here. The table this replaced
        # held four debt companies, three of them retired, so a new tenant that forgot
        # `company_phone` either got another company's number (if the codes happened to
        # match) or a silent None that the template then spoke as an empty bracket.
        # Leaving the field missing is the better failure: the placeholder audit names it.
        cd.setdefault("today", datetime_utils.today_iso())
        _resolve_offset_dates(cd)      # <field>_offset_days → live date
        self.customer_data = cd

        entry = load_flow_registry()[self._company]
        spec_path = _flow_spec_path(self._company, self._instruction_version)
        self._spec = load_tenant_spec(spec_path)
        # A catalog only has to declare _fine_state + template; text_id, company,
        # state, intent_name and category are derived from the spec that binds it.
        # `resolve_catalog` takes the templates from the spec itself when it carries
        # `catalog_inline` (one company = one file), and from the named file
        # otherwise — the same two layouts the training repo reads.
        from demo_v2.server.flow.flowspec import normalize_catalog, resolve_catalog
        try:
            raw_catalog = resolve_catalog(self._spec)
        except (ValueError, KeyError, OSError):
            # spec names no catalog of its own → the registry entry does
            raw_catalog = json.loads(
                (REPO_ROOT / "data" / "pre-scripts" / entry["catalog"]).read_text(
                    encoding="utf-8"))
        self._catalog = normalize_catalog(raw_catalog, self._spec)
        self._by_id = {e["text_id"]: e for e in self._catalog}

        # --- spec-declared session-init API -----------------------------------
        # A production deployment knows the caller through its own CRM, not through
        # a persona shipped in this repo. If the spec declares `session_init`, that
        # one call runs HERE (before turn 1) and its response becomes the render
        # context, so every template is ready by the time the agent speaks. The
        # repo persona stays as the seed: it supplies the request's own tokens
        # ({msisdn}/{case_ref}) and remains the fallback if the CRM is unreachable
        # — a live call must not die on someone else's timeout.
        from demo_v2.server.flow import session_init
        from demo_v2.lib import prescript as _prescript

        self.init_result = session_init.fetch_context(self._spec, seed=self.customer_data)
        if self.init_result["data"]:
            self.customer_data.update(self.init_result["data"])
        # A declared session_init that did not answer leaves the seed in place, and the
        # seed is a fixture in this repo. Speaking it means telling a real caller a
        # balance this deployment made up — measured: with the API down the agent still
        # said 45,000/4,500, the numbers in `_builder_personas.json`. So the call does
        # not proceed on stale context. What it says instead is the tenant's to choose:
        # `session_init.on_failure` names the beat and, optionally, the result to record.
        # A spec that declares session_init without on_failure gets no sentence invented
        # for it — the session refuses to open and the caller is told why.
        self.init_failed = bool(self.init_result["declared"]) and not self.init_result["ok"]
        self.init_on_failure = ((self._spec.get("session_init") or {}).get("on_failure")
                                if self.init_failed else None)
        if self.init_failed and not self.init_on_failure:
            raise RuntimeError(
                f"{self._company}: session_init ไม่ตอบ ({self.init_result['error']}) และ spec "
                "ไม่ได้ประกาศ session_init.on_failure — ไม่เปิดสายด้วยข้อมูลค้างในเครื่อง")
        # No flag is derived here. Whether a date is "still upcoming" is a fact about
        # the tenant's own account, and `due_upcoming` was this file deciding it by
        # comparing two of that tenant's fields — a clinic has no due date to compare.
        # A template that branches on a flag gets the flag from `session_init`.

        # Say plainly which placeholders nothing can fill — those are the tokens the
        # agent would speak literally. `known` = names other layers resolve, so this
        # reports only genuinely unresolvable ones.
        self.unresolved_placeholders = session_init.audit_placeholders(
            [e.get("template", "") for e in self._catalog], self.customer_data)
        if self.unresolved_placeholders:
            logging.getLogger("demo.server.sessions").warning(
                "%s: %d placeholder(s) unfillable, will leak if used: %s",
                self._company, len(self.unresolved_placeholders),
                self.unresolved_placeholders)

        # --- flow-company text_id remap guard (memorizer models only) ---
        # A builder-created company renumbers its catalog (1000+). A *memorizer*
        # model (sft_v11/sft_v2_2, trained on AEON's FIXED ids) only speaks the
        # canonical AEON text_id vocabulary, so a raw lookup on a builder company
        # collides (its 1018=disclose_balance resolves to the local 1018=
        # handoff_refuse) and it speaks the wrong line. Remap through the stable
        # fine_state namespace: canonical id → fine_state → local entry, dropping
        # any canonical id whose fine_state the company lacks.
        #
        # The flow-interpreter models (sft_flow_*) are trained on per-flow RANDOM
        # ids + a shuffled in-prompt catalog (prepare_flow_data), so they READ the
        # catalog and already emit this company's own ids — remapping their output
        # through AEON would corrupt it. Guard OFF for them: direct lookup.
        self._canon_id_to_fs: dict[int, str] = {}
        self._fs_to_local: dict[str, dict] = {}
        # A model that reads the catalog printed in its own prompt answers with THIS
        # company's ids, so nothing needs translating. The removed branch existed for
        # one retired lineage (sft_v11 / sft_v2_2) that had memorized a single
        # company's fixed numbering, and it worked by loading that company's catalog
        # by name from inside this app. Serving such a model again is a data problem
        # — publish its numbering as that company's catalog — not a reason for the
        # app to know a company exists.

        system = self._fill_template(render_instruction(self._spec), cd, gender=self.voice_gender)
        # Full text, not the compact listing. Compact prints only the id and the
        # beat name, so N wordings of one beat arrive as N lines that differ by
        # a number and nothing else — the model is asked to pick a variant it
        # cannot see. (AEON's `verify_name` has 7; `ask_pay_today` has 9.) The
        # training prompt shows every template in full, so this is also what the
        # model was trained to read.
        system += "\n\n" + build_script_catalog(self._catalog, compact=False)
        self._system = system
        self._tools = build_tool_schemas(self._spec) + [
            _flow_reply_schema([e["text_id"] for e in self._catalog])
        ]
        # instruction-grounded step-completeness: fine_state -> [required tool names]
        # from each state's own entry_tools (spec: "must be called before this state's
        # reply", e.g. AEON ptp_capture requires get_current_datetime+record_verbal_
        # commitment+payment_date before its "close" reply). Session-wide call_log =
        # each of these tools is a once-per-call step, so "called at least once" = done.
        self._fine_state_requires: dict[str, list[str]] = {}
        for st in self._spec.get("states", []):
            et = st.get("entry_tools")
            if not et:
                continue
            for t in st.get("templates", []):
                for fs in ([t["fine_state"]] if t.get("fine_state") else t.get("any_of") or []):
                    self._fine_state_requires[fs] = et
        # chain obligation: fine_state -> ordered required steps (each a set of
        # acceptable beats) of the chain state it belongs to. The instruction now
        # tells the model "พูดต่อกันในเทิร์นเดียว (chain) ตามลำดับ" for these states; a
        # reply that voices only part of the chain is the #1 failure the gold eval
        # sees (KBANK: `close` alone where `confirm_info → close` was ordered), so the
        # app holds such a reply back and asks for the full chain — the same way it
        # already holds back a reply whose entry_tools were skipped.
        from demo_v2.server.flow.flowspec import is_chain_state as _is_chain
        # A beat can belong to SEVERAL chain states (`close` is in both
        # `confirm_info → close` and `close → apology`), so keep every candidate
        # chain per beat; the gate then judges the reply against the chain it fits
        # best. A single-owner map silently overwrote `close` with the last state
        # and demanded `apology` on a PTP close.
        # A beat that ALSO stands alone in some non-chain state cannot be used to
        # demand a chain: the same beat means "this state's whole turn" there. AEON's
        # `close` is both the entire reply of `ptp_capture` and the first step of
        # `close_unreachable`'s `close → apology`, so indexing the chain under the beat
        # made every ordinary promise-to-pay close report "missing apology" and get
        # rejected — an apology to a customer who had just agreed to pay.
        _solo_capable = {
            fs
            for st in self._spec.get("states", [])
            if not _is_chain(st)
            for t in st.get("templates", [])
            for fs in ([t["fine_state"]] if t.get("fine_state") else t.get("any_of") or [])
        }
        self._chain_steps: dict[str, list[list[set]]] = {}
        for st in self._spec.get("states", []):
            if not _is_chain(st):
                continue
            steps = [set([t["fine_state"]] if t.get("fine_state") else t.get("any_of") or [])
                     for t in st.get("templates", []) if not t.get("optional")]
            for step in steps:
                for fs in step:
                    if fs in _solo_capable:
                        continue
                    self._chain_steps.setdefault(fs, []).append(steps)
        # The mirror of the chain rule: a state that is NOT a chain speaks ONE beat.
        # `max_templates_per_reply` used to say this in `constraints`, with the chain
        # pairs repeated by hand as its exceptions; that copy was removed as duplicated
        # data — correctly, but nothing then enforced the half it also carried. Derive
        # it from the states instead: beats that belong only to non-chain states may
        # not be combined with each other. (AEON's disclose_ask is the case that
        # exposed this: its two beats each say the amount AND ask, so voicing both
        # states the balance twice and asks twice in one breath.)
        self._solo_beats: dict[str, str] = {}
        for st in self._spec.get("states", []):
            if _is_chain(st):
                continue
            for t in st.get("templates", []):
                for fs in ([t["fine_state"]] if t.get("fine_state") else t.get("any_of") or []):
                    if fs not in self._chain_steps:
                        self._solo_beats[fs] = st["id"]
        # A call ends when the agent SPEAKS a closing line, not when it stamps the
        # outcome. The spec already marks which states are terminal; nothing read it.
        # Without this the session runs on after the close: the backend refuses every
        # further tool with `call_already_closed`, the model retries it to the hop
        # limit saying nothing, and then reaches for the only line that always fits —
        # the greeting — so the caller is greeted again after being told goodbye.
        self._terminal_beats = {
            b for st in self._spec.get("states", []) if st.get("terminal")
            for t in st.get("templates", [])
            for b in ([t["fine_state"]] if t.get("fine_state") else t.get("any_of") or [])
        }
        self._step_nudges = 0   # per-session cap on self-correction retries (avoid loop burn)

        # the actual model for API calls — MUST resolve to something served, or every
        # turn 404s at vLLM with no reply reaching the caller (see _default_served_model).
        self._model = self._model_override or _default_served_model()
        # multi-instance aware: route to the endpoint actually serving this model
        # (grpo540→:8000, grpo400→:8002); falls back to the default endpoint.
        self._base_url = endpoint_for_model(self._model)
        self._turn_count = 0
        self.done = False
        self._last_turn_timing: dict[str, Any] | None = None
        self._transcript: list[dict[str, Any]] = []
        self._init_agent()

    # ---- public ----

    def _init_failure_hops(self, cfg: dict) -> "list[dict[str, Any]]":
        """What the caller hears when `session_init` did not answer."""
        hops: list[dict[str, Any]] = [{
            "kind": "warning",
            "text": f"session_init ไม่ตอบ ({self.init_result.get('error')}) — ปิดสายตามที่ spec กำหนด",
        }]
        outcome = cfg.get("outcome") or {}
        closer = next((d["name"] for d in (self._spec.get("tools") or {}).get("declarations", [])
                       if (d.get("gating") or {}).get("required_at") == "end_of_call"), None)
        if closer and outcome.get("result"):
            args = {"result": outcome["result"]}
            if outcome.get("reason"):
                args["reason"] = outcome["reason"]
            result = self._backend.dispatch(closer, args)
            hops.append({"kind": "tool_call", "name": closer, "args": args})
            hops.append({"kind": "tool_result", "name": closer, "result": result})
        entry = next((e for e in self._catalog
                      if e.get("_fine_state") == cfg.get("fine_state")), None)
        if entry:
            text = self._fill_template(entry["template"], self.customer_data,
                                       gender=self.voice_gender)
            hops.append({"kind": "reply", "text": text,
                         "text_ids": [entry["text_id"]], "dynamic_vars": {}})
        return hops

    async def aiter_opening(self) -> AsyncIterator[dict[str, Any]]:
        """Bot-first outbound greeting: emit the spec-seeded opener. No LLM call."""
        if self._greeted:
            return
        self._greeted = True
        if self.init_on_failure:
            # The CRM did not answer. Say the tenant's own line for that, record the
            # result if it named one, and end — rather than greet with a balance that
            # came from a fixture. Mechanism, not policy: every tenant needs the call
            # to end when the system behind it is down; which sentence and which
            # disposition are theirs.
            for hop in self._init_failure_hops(self.init_on_failure):
                yield hop
            self.done = True
            return
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
        """Honor the requested persona if its company has a FlowSpec; otherwise fall
        back to the first persona of any registered company. Which company that is
        follows from the registry, not from a name compiled into the app — the
        fallback used to be one company, so a deployment that never registered it
        raised `no AEON persona found` while holding perfectly good personas."""
        registry = load_flow_registry()
        cases = _all_cases()
        ids = {c.get("id") for c in cases}
        parts = case_id.split("-")
        if case_id in ids and len(parts) > 1 and parts[1] in registry:
            return case_id
        preferred = [FLOW_FALLBACK_COMPANY] if FLOW_FALLBACK_COMPANY else []
        for company in preferred + sorted(registry):
            for c in cases:
                if c.get("id", "").split("-")[1:2] == [company]:
                    return c["id"]
        raise KeyError(
            f"no persona found for any registered company ({', '.join(sorted(registry)) or 'none'})")

    def _init_agent(self) -> None:
        # ONE context object, shared with the backend — deliberately not a copy. A
        # tool's response updates the render context in place (SpecBackend.dispatch),
        # so what the API just said is what the next template speaks. With a copy
        # here a re-checked balance stayed invisible: the tool returned 99999 and the
        # agent read the session-init snapshot of 45000 aloud.
        self.customer_data = {k: v for k, v in self.customer_data.items()
                              if not str(k).startswith("_")}
        self._backend = self._SpecBackend(self.customer_data, self._spec)
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
            if not str(tid).lstrip("-").isdigit():
                continue
            tid_i = int(tid)
            if self._canon_id_to_fs:
                # Builder company: the model's id is canonical (AEON) — resolve it
                # through fine_state to this company's template. No raw fallback:
                # a direct hit would be a renumber collision, and a canonical id
                # whose fine_state the company lacks is intentionally dropped.
                fs = self._canon_id_to_fs.get(tid_i)
                e = self._fs_to_local.get(fs) if fs else None
            else:
                e = self._by_id.get(tid_i)
            if e is None or e["text_id"] in seen:
                continue
            seen.add(e["text_id"])
            good.append(e["text_id"])
            # strict_dates: a date the MODEL supplies must be canonical, or the
            # agent reads "2026-06-02 (Tuesday)" aloud instead of a Thai date. The
            # flow path never enabled this, so every [promised_date]/[callback_date]
            # would have been spoken as an ISO string. Malformed → DateFormatError,
            # which the caller turns into a reject+retry.
            texts.append(self._fill_template(
                e["template"], self.customer_data, dynamic_vars=dyn,
                strict_dates=True, gender=self.voice_gender))
        return good, " ".join(texts), dyn if isinstance(dyn, dict) else {}

    def _fallback_reply(self) -> "tuple[list[int], str]":
        """A safe on-catalog line for when the model returns an empty/garbage reply
        (no valid text_ids, or free text with no Thai) — so the bot never speaks a
        blank bubble or a leaked token like 'parameter'."""
        # v12: once the call outcome is stamped, a blank/garbage reply means the
        # model stumbled on the CLOSING line — recover with a close, not a
        # "ขอแจ้งอีกครั้ง" (faq_repeat), which reads wrong after a completed PTP.
        outcome_done = getattr(self._backend, "outcome_stamped", None)
        # Which beat to fall back on is the company's choice, not this file's: a
        # spec may name one via `fallback_fine_state`. Default `faq_repeat` keeps
        # every existing company's behaviour unchanged.
        want = (self._spec or {}).get("fallback_fine_state", "faq_repeat")
        e = next((x for x in self._catalog if x.get("_fine_state") == want), None)
        if e:
            return [e["text_id"]], self._fill_template(e["template"], self.customer_data, gender=self.voice_gender)
        # Nothing in the catalog can carry a re-ask. Speaking invented Thai here
        # breaks the one rule the instruction gives the model ("ห้ามสร้างข้อความอิสระ")
        # and the line is unreviewable — no company wrote it. Count it so an
        # under-populated catalog shows up as a number instead of as a bot that
        # apologises every turn.
        self._off_catalog_replies = getattr(self, "_off_catalog_replies", 0) + 1
        print(f"[flow] off-catalog fallback #{self._off_catalog_replies} "
              f"({getattr(self, '_company', '?')}: catalog has no '{want}' beat)", flush=True)
        return [], self._fill_template(
            "ขออภัย{suffix} รบกวนคุณลูกค้าแจ้งอีกครั้งได้ไหม{q_suffix}",
            self.customer_data, gender=self.voice_gender)

    @staticmethod
    def _looks_sayable(text: str) -> bool:
        return bool(text.strip()) and any("฀" <= c <= "๿" for c in text)

    async def _aiter_run(self, user_msg: str) -> AsyncIterator[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        _SENTINEL = object()
        turn_hops: list[dict[str, Any]] = []

        def push(hop: dict[str, Any]) -> None:
            turn_hops.append(hop)
            loop.call_soon_threadsafe(queue.put_nowait, hop)

        # UX filler for the silent data-record chain only. "Data record" = the
        # tools that actually persist customer info to CRM (a PTP commitment, a
        # callback, a phone update) — NOT get_current_datetime (a read) and NOT
        # record_outcome alone (a call-result stamp; a plain refusal/close must
        # never say "เรียบร้อย"). On the first such tool: one "ขออนุญาตบันทึก…"
        # bubble; the closing reply is prefixed "เรียบร้อยค่ะ " ONLY IF a write
        # actually SUCCEEDED (recorded, no error). Client-stream only — never
        # into self._messages (byte-identity). Disable AAX6_FLOW_FILLER=0.
        _WRITE_TOOLS = {"record_verbal_commitment", "payment_date",
                        "callback_datetime", "update_phone"}
        _filler_on = os.environ.get("AAX6_FLOW_FILLER", "1").strip().lower() not in ("0", "false", "")
        _filler_state = {"bubble": False, "saved": False}

        def _emit_filler(tool_name: str) -> None:
            if not _filler_on or tool_name not in _WRITE_TOOLS or _filler_state["bubble"]:
                return
            _filler_state["bubble"] = True
            text = self._fill_template("ขออนุญาตบันทึกข้อมูลสักครู่นะ{q_suffix}",
                                       self.customer_data, gender=self.voice_gender)
            push({"kind": "reply", "text": text, "text_ids": [], "dynamic_vars": {},
                  "filler": True})

        def blocking() -> tuple[str, float, int]:
            llm_ms, llm_hops = 0.0, 0
            _closed_retries = 0
            agent_text = ""
            try:
                content = user_msg
                self._messages.append({"role": "user", "content": content})
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
                        content = (msg.get("content") or "").strip()
                        # Recover tool calls the parser missed (leaked as <tool_call> text).
                        tcs = _recover_toolcalls(content) if "<tool_call>" in content else []
                        if not tcs:
                            agent_text = _strip_toolcall_markup(content)
                            if self._looks_sayable(agent_text):
                                push({"kind": "reply", "text": agent_text, "text_ids": [], "dynamic_vars": {}})
                            else:  # empty / non-Thai garbage (e.g. leaked "parameter") → safe fallback
                                fb_ids, agent_text = self._fallback_reply()
                                push({"kind": "reply", "text": agent_text, "text_ids": fb_ids, "dynamic_vars": {}})
                            break
                    tc = tcs[0]
                    fn = tc["function"]
                    raw_args = fn.get("arguments")
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                    if fn["name"] == "reply":
                        # A reply-gate that blocked "sensitive" lines until the app
                        # judged the customer verified used to sit here. It was the app
                        # enforcing one domain's compliance rule: the list of sensitive
                        # slots was a debt collector's, and "verified" was a guess the
                        # app made from whichever tool happened to declare an
                        # `after_event`. A tenant that needs the rule states it in its
                        # own `constraints` (enforce: prompt/reward); a tenant that does
                        # not should not be carrying the machinery.
 # model picks again in the same tool loop
                        try:
                            ids, text, dyn = self._render_reply(args)
                        except self._DateFormatError as e:
                            # strict_dates is on for model-supplied dates (see
                            # _render_reply): a malformed one is agent-fixable, so
                            # reject and let it retry rather than speak the raw
                            # string. Same shape as the guards above.
                            push({"kind": "warning",
                                  "text": f"รูปแบบวันที่ไม่ถูกต้อง: {e}"})
                            if self._step_nudges < 2:
                                self._step_nudges += 1
                                self._messages.append({"role": "assistant", "tool_calls": [{
                                    "id": tc.get("id", "call_x"), "type": "function",
                                    "function": {"name": "reply",
                                                 "arguments": json.dumps(args, ensure_ascii=False, default=str)}}]})
                                self._messages.append({"role": "tool", "tool_call_id": tc.get("id", "call_x"),
                                                       "content": json.dumps({
                                                           "sent": False, "reason": "date_format_invalid",
                                                           "detail": str(e),
                                                           "hint": "ส่ง dynamic_vars วันที่เป็น YYYY-MM-DD (Weekday) "
                                                                   "เช่น 2026-06-02 (Tuesday)"},
                                                           ensure_ascii=False)})
                                push({"kind": "tool_call", "name": "reply", "args": args})
                                push({"kind": "tool_result", "name": "reply",
                                      "result": {"sent": False, "reason": "date_format_invalid"}})
                                continue
                            ids, text = self._fallback_reply()
                            dyn = {}
                        if not ids and not self._looks_sayable(text):  # reply [] → safe fallback
                            ids, text = self._fallback_reply()

                        # instruction-grounded step-completeness gate: the beat(s) about to
                        # be spoken belong to a state whose spec declares entry_tools — has
                        # the model actually called them yet? (e.g. AEON's ptp_capture needs
                        # get_current_datetime+record_verbal_commitment+payment_date before
                        # its "close" reply.) Same pattern as the KYC reply-gate above:
                        # reject with a hint, let the model retry in the same loop, and
                        # surface it to the FE so a missed step is visible, not silent.
                        _reply_fs = {self._by_id[t]["_fine_state"] for t in ids if t in self._by_id}
                        _missing: list[str] = []
                        _rejected: dict[str, Any] = {}
                        for _fs in _reply_fs:
                            for _tool in self._fine_state_requires.get(_fs, []):
                                if _tool in _missing or _tool in _rejected:
                                    continue
                                if self._backend.successful_calls(_tool) == 0:
                                    _missing.append(_tool)
                                    continue
                                # A prior SUCCESS is not enough. Observed: the model
                                # closed an appointment as "confirmed" on turn 1, the
                                # patient then asked to cancel, the cancel write was
                                # rejected `call_already_closed` — and the agent still
                                # said "บันทึกยกเลิกนัด…เรียบร้อยแล้ว". Never let a reply
                                # claim a write the backend refused; hand the model the
                                # actual error instead.
                                _last = next((c for c in reversed(self._backend.call_log)
                                              if c.get("tool") == _tool), None)
                                _res = (_last or {}).get("result") or {}
                                if isinstance(_res, dict) and _res.get("error"):
                                    # …unless the refused call was a REDUNDANT REPEAT of
                                    # one that already succeeded with the same arguments.
                                    # Then the write the reply is about did happen, and
                                    # blocking creates a livelock: the reply is the right
                                    # next move, the app refuses it, the model re-calls the
                                    # tool, the backend refuses that too, forever. Args must
                                    # match — a second call that changes the outcome (the
                                    # confirmed→cancelled case above) is a different
                                    # request and stays blocked.
                                    _same_ok = any(
                                        c.get("tool") == _tool
                                        and c.get("args") == (_last or {}).get("args")
                                        and not (isinstance(c.get("result"), dict)
                                                 and c["result"].get("error"))
                                        for c in self._backend.call_log)
                                    if not _same_ok:
                                        _rejected[_tool] = _res.get("error")
                        if _rejected:
                            warn_text = ("ตอบไม่ได้ — เครื่องมือถูกปฏิเสธ: "
                                         + ", ".join(f"{k} ({v})" for k, v in _rejected.items()))
                            push({"kind": "warning", "text": warn_text,
                                  "rejected_tools": _rejected})
                            if self._step_nudges < 2:
                                self._step_nudges += 1
                                self._messages.append({"role": "assistant", "tool_calls": [{
                                    "id": tc.get("id", "call_x"), "type": "function",
                                    "function": {"name": "reply",
                                                 "arguments": json.dumps(args, ensure_ascii=False, default=str)}}]})
                                self._messages.append({"role": "tool", "tool_call_id": tc.get("id", "call_x"),
                                                       "content": json.dumps({
                                                           "sent": False, "reason": "tool_call_rejected",
                                                           "rejected": _rejected,
                                                           "hint": "การบันทึกล่าสุดไม่สำเร็จ — ห้ามบอกลูกค้าว่าบันทึกเรียบร้อย "
                                                                   "ให้แก้ไขการเรียกเครื่องมือ หรือตอบตามสถานะจริง"},
                                                           ensure_ascii=False)})
                                push({"kind": "tool_call", "name": "reply", "args": args})
                                push({"kind": "tool_result", "name": "reply",
                                      "result": {"sent": False, "reason": "tool_call_rejected",
                                                 "rejected": _rejected}})
                                continue
                        # A slot the sentence needs but the context has EMPTY. The
                        # backend echoes what it recorded, so `new_slot: ""` means the
                        # model called save_appointment without the date the patient
                        # just gave. fill_template then substitutes "" and the line
                        # goes out as "บันทึกเลื่อนนัด...เป็น  เรียบร้อยแล้ว" — a blank
                        # where the date belongs, which reads as fine until you listen.
                        # Rejecting sends the model back to supply it.
                        from demo_v2.lib.prescript import _placeholder_names as _slots
                        _blank: list[str] = []
                        for _tid in ids:
                            _e = self._by_id.get(_tid)
                            if not _e:
                                continue
                            for _n in _slots(_e.get("template", "")):
                                _v = self.customer_data.get(_n)
                                if _n in self.customer_data and (_v is None or str(_v).strip() == ""):
                                    _blank.append(_n)
                        if _blank and self._step_nudges < 2:
                            push({"kind": "warning",
                                  "text": f"ยังไม่มีค่าให้พูด: {', '.join(sorted(set(_blank)))}",
                                  "empty_slots": sorted(set(_blank))})
                            self._step_nudges += 1
                            self._messages.append({"role": "assistant", "tool_calls": [{
                                "id": tc.get("id", "call_x"), "type": "function",
                                "function": {"name": "reply",
                                             "arguments": json.dumps(args, ensure_ascii=False, default=str)}}]})
                            self._messages.append({"role": "tool", "tool_call_id": tc.get("id", "call_x"),
                                                   "content": json.dumps({
                                                       "sent": False, "reason": "empty_slot",
                                                       "slots": sorted(set(_blank)),
                                                       "hint": "ประโยคนี้ต้องพูดค่าที่ยังว่างอยู่ — "
                                                               "เรียก tool ที่บันทึกค่านั้นพร้อมค่าที่ลูกค้าบอก "
                                                               "แล้วค่อยตอบ"},
                                                       ensure_ascii=False)})
                            push({"kind": "tool_call", "name": "reply", "args": args})
                            push({"kind": "tool_result", "name": "reply",
                                  "result": {"sent": False, "reason": "empty_slot",
                                             "slots": sorted(set(_blank))}})
                            continue

                        # one beat per reply for a non-chain state (see _solo_beats)
                        _solo = {self._solo_beats[f] for f in _reply_fs if f in self._solo_beats}
                        if len(_reply_fs) > 1 and len(_solo) == 1 and \
                                all(f in self._solo_beats for f in _reply_fs) and self._step_nudges < 2:
                            _st_id = next(iter(_solo))
                            push({"kind": "warning",
                                  "text": f"state {_st_id} ให้พูดบีตเดียว แต่พูด {len(_reply_fs)} บีต: "
                                          f"{', '.join(sorted(_reply_fs))}",
                                  "beats": sorted(_reply_fs)})
                            self._step_nudges += 1
                            self._messages.append({"role": "assistant", "tool_calls": [{
                                "id": tc.get("id", "call_x"), "type": "function",
                                "function": {"name": "reply",
                                             "arguments": json.dumps(args, ensure_ascii=False, default=str)}}]})
                            self._messages.append({"role": "tool", "tool_call_id": tc.get("id", "call_x"),
                                                   "content": json.dumps({
                                                       "sent": False, "reason": "too_many_beats",
                                                       "state": _st_id, "beats": sorted(_reply_fs),
                                                       "hint": f"state {_st_id} เลือกพูดได้บีตเดียว "
                                                               "ส่ง text_id เดียวที่เหมาะที่สุด"},
                                                       ensure_ascii=False)})
                            push({"kind": "tool_call", "name": "reply", "args": args})
                            push({"kind": "tool_result", "name": "reply",
                                  "result": {"sent": False, "reason": "too_many_beats",
                                             "beats": sorted(_reply_fs)}})
                            continue

                        # Which chain does this reply belong to? Among every chain any
                        # spoken beat is part of, take the one the reply covers most;
                        # if that chain is fully covered the reply is complete.
                        _chain_missing: list[str] = []
                        _order = ""
                        _cands = [c for _fs in _reply_fs for c in self._chain_steps.get(_fs, [])]
                        if _cands:
                            _best = max(_cands, key=lambda c: sum(1 for st_ in c if st_ & _reply_fs))
                            _chain_missing = ["/".join(sorted(st_)) for st_ in _best if not (st_ & _reply_fs)]
                            _order = " → ".join("/".join(sorted(x)) for x in _best)
                        if _chain_missing and self._step_nudges < 2:
                            push({"kind": "warning",
                                  "text": f"พูดไม่ครบ chain: ขาด {', '.join(_chain_missing)} (ต้องพูด {_order} ในเทิร์นเดียว)",
                                  "missing_beats": _chain_missing})
                            self._step_nudges += 1
                            self._messages.append({"role": "assistant", "tool_calls": [{
                                "id": tc.get("id", "call_x"), "type": "function",
                                "function": {"name": "reply",
                                             "arguments": json.dumps(args, ensure_ascii=False, default=str)}}]})
                            self._messages.append({"role": "tool", "tool_call_id": tc.get("id", "call_x"),
                                                   "content": json.dumps({
                                                       "sent": False, "reason": "incomplete_chain",
                                                       "missing_beats": _chain_missing,
                                                       "hint": f"state นี้ต้องพูดต่อกันในเทิร์นเดียว: {_order} — "
                                                               "ส่ง text_ids ของทุกขั้นในครั้งเดียว เรียงตามลำดับ"},
                                                       ensure_ascii=False)})
                            push({"kind": "tool_call", "name": "reply", "args": args})
                            push({"kind": "tool_result", "name": "reply",
                                  "result": {"sent": False, "reason": "incomplete_chain",
                                             "missing_beats": _chain_missing}})
                            continue
                        if _missing:
                            warn_text = f"ลืมเรียก tool ก่อนตอบ: {', '.join(_missing)}"
                            push({"kind": "warning", "text": warn_text, "missing_tools": _missing})
                            if self._step_nudges < 2:
                                # give the model a chance to self-correct: tell it exactly
                                # what it skipped, then let it retry within the same turn
                                # instead of sending the incomplete reply to the customer.
                                self._step_nudges += 1
                                self._messages.append({"role": "assistant", "tool_calls": [{
                                    "id": tc.get("id", "call_x"), "type": "function",
                                    "function": {"name": "reply",
                                                 "arguments": json.dumps(args, ensure_ascii=False, default=str)}}]})
                                self._messages.append({"role": "tool", "tool_call_id": tc.get("id", "call_x"),
                                                       "content": json.dumps({
                                                           "sent": False, "reason": "missing_required_tools",
                                                           "missing_tools": _missing,
                                                           "hint": f"คุณลืมเรียกเครื่องมือ: {', '.join(_missing)} "
                                                                   "ตามขั้นตอนที่ระบุ — เรียกเครื่องมือเหล่านี้ให้ครบก่อน "
                                                                   "แล้วค่อยตอบลูกค้า ห้ามตอบก่อนทำครบ"},
                                                           ensure_ascii=False)})
                                push({"kind": "tool_call", "name": "reply", "args": args})
                                push({"kind": "tool_result", "name": "reply",
                                      "result": {"sent": False, "reason": "missing_required_tools",
                                                 "missing_tools": _missing}})
                                continue  # model retries in the same tool loop
                            # already nudged twice — let it through rather than stall the
                            # call forever; the FE warning above still records the miss.
                        clean_args = {"text_ids": ids, "dynamic_vars": args.get("dynamic_vars") or []}
                        self._messages.append({
                            "role": "assistant", "content": text,
                            "tool_calls": [{"id": tc.get("id", "call_x"), "type": "function",
                                            "function": {"name": "reply",
                                                         "arguments": json.dumps(clean_args, ensure_ascii=False)}}],
                        })
                        push({"kind": "tool_call", "name": "reply", "args": clean_args})
                        # after a record chain, prefix the closing reply with a
                        # "เรียบร้อยค่ะ" acknowledgement (client display only —
                        # keep self._messages content raw for byte-identity)
                        display_text = text
                        # prefix "เรียบร้อยค่ะ" only on a genuine CLOSING reply
                        # after a save — never on a repeat/fallback/other reply
                        # (e.g. the model closing with 1076 faq_repeat by mistake)
                        # (_reply_fs already computed above by the step-completeness gate)
                        _CLOSE_FS = {"close", "offer_callback"}
                        push({"kind": "reply", "text": display_text, "text_ids": ids, "dynamic_vars": dyn})
                        agent_text = text
                        # the closing line was just spoken -> the call is over
                        _spoken = {self._by_id[t]["_fine_state"] for t in ids if t in self._by_id}
                        if _spoken & self._terminal_beats:
                            self.done = True
                        break
                    _emit_filler(fn["name"])
                    result = self._backend.dispatch(fn["name"], args)
                    # The call is already closed and the model keeps re-sending the
                    # same write instead of speaking. Left alone it burns every hop
                    # in the turn, says nothing at all, and then reaches for whatever
                    # line always fits — the greeting — so the caller is greeted again
                    # after goodbye. Two refusals is enough: say the closing line and
                    # end the call, which is what the terminal state asks for anyway.
                    if isinstance(result, dict) and result.get("error") == "call_already_closed":
                        _closed_retries += 1
                        if _closed_retries >= 2:
                            _close = next(
                                (e for e in self._catalog
                                 if e["_fine_state"] in self._terminal_beats), None)
                            if _close:
                                ids = [_close["text_id"]]
                                text = self._fill_template(_close["template"],
                                                           self.customer_data,
                                                           gender=self.voice_gender)
                            else:
                                ids, text = self._fallback_reply()
                            push({"kind": "warning",
                                  "text": "สายปิดไปแล้ว — ปิดบทสนทนา"})
                            push({"kind": "reply", "text": text, "text_ids": ids,
                                  "dynamic_vars": {}})
                            agent_text = text
                            self.done = True
                            break
                    if (fn["name"] in _WRITE_TOOLS and not result.get("error")
                            and result.get("recorded") is not False):
                        _filler_state["saved"] = True   # a real write succeeded
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
                # The tool loop can run out (FLOW_MAX_TOOL_LOOPS) without the model ever
                # calling `reply` — observed on SHOP: it called check_new_date with an
                # empty `date` eight times, every one correctly rejected, and the turn
                # then returned with nothing said. The caller is a live person, so a
                # turn that produces no speech is dead air on the line, which is worse
                # than any sentence the catalog holds. Say the fallback instead.
                # A mechanism, not a policy: every tenant wants the agent to answer when
                # spoken to, and which sentence that is comes from the spec
                # (`fallback_fine_state`, else the catalog's re-ask beat).
                if not agent_text:
                    ids, text = self._fallback_reply()
                    push({"kind": "warning",
                          "text": "จบเทิร์นโดยไม่ได้ตอบ (tool loop หมด) — ใช้ประโยคสำรอง"})
                    push({"kind": "reply", "text": text, "text_ids": ids,
                          "dynamic_vars": {}})
                    self._messages.append({"role": "assistant", "content": text})
                    agent_text = text
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
        # DEBUG: per-turn trace (user msg → ordered tools/replies) to spot ordering
        # bugs like disclose-before-verify. Remove when done debugging.
        _seq = []
        for _h in turn_hops:
            if _h.get("kind") == "tool_call" and _h.get("name") != "reply":
                _seq.append(_h["name"])
            elif _h.get("kind") == "reply":
                _seq.append("reply" + str(_h.get("text_ids")))
        print(f"[flow-turn] user={user_msg!r} -> {' | '.join(_seq)}", flush=True)
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
    instruction_version: str | None = None,
) -> Session:
    # One kind of session: a spec drives the call. `mode` and `agent` are accepted so
    # existing callers and query strings keep working.
    return FlowLiveSession(case_id, voice_gender=voice_gender, model=model,
                           instruction_version=instruction_version)


def flow_template() -> dict:
    """A blank, documented FlowSpec + catalog skeleton for authoring a NEW company —
    the 'download template' payload. The user fills it and re-uploads (→ create_flow_company_raw).
    Tools are HTTP webhooks (impl:"http"): every tool call POSTs {url} with {body}, so a new
    company wires its own CRM/booking API. {placeholders} are filled from customer_data + args."""
    return {
        "_readme": [
            "กรอกไฟล์นี้แล้ว upload กลับเพื่อสร้างบริษัทใหม่ในเดโม",
            "spec.company = รหัสบริษัท (A-Z/0-9, 2-12 ตัว). ทุก fine_state ที่อ้างใน states ต้องมีใน catalog.",
            "catalog: ใส่แค่ _fine_state + template พอ — text_id/company/state/intent_name/category ระบบเติมให้เอง",
            "beat เดียวใส่ได้หลายบรรทัด = หลายสำนวน (เช่น close 2 บรรทัดข้างล่าง) โมเดลเลือกเองว่าจะพูดสำนวนไหน",
            "templates ใน 1 state: มีหลายอันแบบไม่มี when_event = พูดต่อกันในเทิร์นเดียว (chain) / มี when_event = เลือกอันเดียวตาม event",
            "outcomes.results = โค้ดผลสายของบริษัทนี้ (debt: ptp/refused/unreachable/reached/tcb/tin · หรือกำหนดเอง).",
            "tools = HTTP webhook: แต่ละ tool ยิง POST ไป url พร้อม body (แทน {customer_name} {amount} ...).",
            "faq_routing = ลูกค้าถามแทรกกลางสาย → ตอบด้วย template ไหน แล้วกลับเข้า flow เดิม",
            "constraints = กฎของบริษัท: ใส่ type ถ้าอยากให้ระบบบังคับจริง / ไม่ใส่ type แต่ใส่ desc = กฎที่เขียนลง prompt ให้โมเดลอ่าน",
        ],
        "spec": {
            "spec_version": 2,
            "flow_id": "YOURCO-outbound-call",
            "company": "YOURCO",
            "description": "",
            "crm_fields": ["customer_name", "your_field_1", "your_field_2"],
            "events": {
                "name_confirmed": {"desc": "ลูกค้ายืนยันตัวตน", "cues": ["ใช่", "ครับ", "ค่ะ"]},
                "asks_caller": {"desc": "ลูกค้าถามว่าโทรจากที่ไหน", "cues": ["ใครโทรมา", "ที่ไหน"]},
            },
            "tools": {"declarations": [
                {"name": "record_outcome", "impl": "http", "desc": "บันทึกผลสาย",
                 "url": "{API_BASE}/YOURCO/record_outcome", "method": "POST", "args": {},
                 "gating": {"required_at": "end_of_call"}},
                {"name": "notify_crm", "impl": "http", "desc": "ตัวอย่าง webhook — ไม่ต้องใส่ body ก็ได้ ระบบส่ง {tool,args,ref} ให้เอง",
                 "url": "https://api.example.com/hook", "method": "POST", "args": {}},
            ]},
            "states": [
                {"id": "greet", "phase": "opening", "initial": True,
                 "templates": [{"fine_state": "greet_verify"}],
                 "on": [{"event": "name_confirmed", "to": "close"}]},
                {"id": "close", "phase": "close", "terminal": True,
                 "templates": [{"fine_state": "close"}], "entry_tools": ["record_outcome"],
                 "outcome": {"result": "reached", "reason": "done"}},
            ],
            "faq_routing": {
                "_hint": "ลูกค้าถามแทรก → ตอบด้วย templates ที่ระบุ แล้วกลับเข้า state เดิมต่อ",
                "routes": [
                    {"intent": "asks_caller",
                     "desc": "ถามว่าโทรจากไหน → บอกชื่อบริษัทแล้วทำ flow ต่อ",
                     "templates": [{"fine_state": "faq_caller"}],
                     "then": "resume"},
                ],
            },
            "constraints": [
                {"id": "outcome_once", "type": "once_per_call",
                 "template_fine_state": "close", "enforce": ["prompt", "reward"],
                 "desc": "ประโยคปิดสายพูดครั้งเดียวต่อสาย"},
                {"enforce": ["prompt"],
                 "desc": "ตัวอย่างกฎแบบข้อความ (ไม่มี type) — จะถูกเขียนลง instruction ให้โมเดลอ่าน แต่ระบบไม่บังคับเชิงกลไก"},
            ],
            "outcomes": {"required_at_close": True,
                         "results": {"reached": {"reasons": ["done"], "desc": "จบสาย"}}},
        },
        "catalog": [
            {"_fine_state": "greet_verify",
             "template": "สวัสดีค่ะ ติดต่อจากบริษัท ... เรียนสายกับคุณ {customer_name} ใช่ไหมคะ"},
            {"_fine_state": "close", "template": "ขอบคุณค่ะ สวัสดีค่ะ"},
            {"_fine_state": "close", "template": "ขอบคุณมากค่ะ สวัสดีค่ะ"},
            {"_fine_state": "faq_caller", "template": "ดิฉันติดต่อจากบริษัท ... ค่ะ"},
        ],
    }


def delete_flow_company(company: str) -> dict[str, Any]:
    """Off-board a tenant: remove its `<CODE>.company.json` and the demo personas
    written with it.

    Every tenant owns exactly one file, so there is nothing to check for sharing and
    no index to prune — the previous version had to refuse four companies by name
    because they pointed at curated catalogs other companies also used. A tenant that
    supplied its own spec can always take it back.
    """
    company = (company or "").strip().upper()
    if not company:
        return {"ok": False, "errors": ["ต้องระบุรหัสบริษัท"]}
    if company not in load_flow_registry():
        return {"ok": False, "errors": [f"ไม่พบบริษัท {company}"]}

    removed: list[str] = []
    f = FLOW_DIR / f"{company}{TENANT_SUFFIX}"
    if f.exists():
        f.unlink()
        removed.append(f"data/flows/{f.name}")

    if BUILDER_CASES_FILE.exists():
        try:
            cases = json.loads(BUILDER_CASES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cases = []

        # A persona carries its company in its ID (`TC-<COMPANY>-BUILD-001`) — the
        # same derivation `list_cases` and the sessions use. There is no `company`
        # field on the row, so filtering on one silently kept every persona and the
        # deleted company kept showing up in the picker.
        def _company_of(case_id: str) -> str:
            parts = str(case_id).split("-")
            return parts[1].upper() if len(parts) > 1 else ""

        kept = [c for c in cases if _company_of(c.get("id", "")) != company]
        if len(kept) != len(cases):
            BUILDER_CASES_FILE.write_text(
                json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")
            removed.append(f"personas x{len(cases) - len(kept)}")

    return {"ok": True, "company": company, "removed": removed}


def _vllm_base_urls() -> list[str]:
    """vLLM endpoints to query. AAX6_VLLM_BASE_URLS = comma-separated list (multi-model:
    grpo540 on :8000, grpo400 on :8002); falls back to the single AAX6_VLLM_BASE_URL."""
    multi = os.environ.get("AAX6_VLLM_BASE_URLS", "").strip()
    if multi:
        return [u.strip() for u in multi.split(",") if u.strip()]
    return [os.environ.get("AAX6_VLLM_BASE_URL", "http://localhost:8000/v1")]


def _model_endpoints() -> dict[str, str]:
    """{served_model_id: base_url} across ALL configured vLLM endpoints — so a picked
    version routes to the instance actually serving it (multi-instance A100)."""
    import urllib.request
    out: dict[str, str] = {}
    for url in _vllm_base_urls():
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/models", timeout=5) as r:
                for m in json.load(r).get("data", []):
                    out.setdefault(m["id"], url)
        except Exception:
            continue
    return out


def endpoint_for_model(model: str | None) -> str:
    """The base_url serving `model` (multi-instance aware); default = first configured."""
    if model:
        ep = _model_endpoints().get(model)
        if ep:
            return ep
    return _vllm_base_urls()[0]


def served_models() -> dict[str, list[str]]:
    """Qwen checkpoints currently served by vLLM (aggregated across all endpoints),
    split into flow vs pre-flow (base). Powers the demo's model-version picker."""
    ids = list(_model_endpoints().keys())
    if not ids:
        return {"base": [], "flow": []}
    # GRPO/SFT checkpoints all live under the "qwen" picker (base list). The engine is
    # routed by the flow/company selection, so the picker just lists served versions.
    sft = sorted(i for i in ids if i.lower().startswith("sft"))
    base_only = sorted(i for i in ids if not i.lower().startswith("sft"))
    return {"base": base_only + sft, "flow": [i for i in sft if "flow" in i.lower()]}
