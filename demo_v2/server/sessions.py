"""Session objects the demo streams from.

One kind: `FlowLiveSession` — a tenant's `<CODE>.company.json` drives the call.
The spec supplies the instruction, the sentence catalog, the tools and the rules;
this module runs the turn loop and the two guards around it (before speaking,
before writing). See demo_v2/docs/MANUAL.md.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import re
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Protocol


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
    """Every persona: the shipped pool plus whatever the Builder wrote. A missing or
        unreadable Builder file is ignored rather than fatal — one bad upload should not
        take the picker down.

    """
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


# ---------------------------------------------------------------------------
# Flow-interpreter session (sft_flow_v1 reads a FlowSpec from the prompt)
# ---------------------------------------------------------------------------

# The registry is derived from the tenant files present, so a company uploaded at
# runtime works with no code change or redeploy.
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
# Which checkpoint to ask vLLM for. NO default checkpoint name is baked in: a name
# from our own host (one lived here) is wrong on anyone else's machine, and it
# 404s at vLLM in a way that ends the turn with no reply. Left empty, the app asks the
# endpoint what it is actually serving — see `default_served_model()`.
FLOW_MODEL = os.environ.get("AAX6_FLOW_MODEL", "").strip()

# How the prompt is laid out so vLLM can reuse its prefix cache —
# spread_cache (default) | cache | spread | legacy
#
# Share of the prompt that is identical between two calls of the same company
# (measured in tokens over the 43 gold cases, 2026-09-06). The gold score is the
# same under all three:
#     legacy 1.5%   ·   cache 43.3%   ·   spread_cache 82.4%
#
# `cache`  moves the CRM block to the very end. The CRM block is the only part of
#          the instruction that differs between calls (the only place holding
#          `{field}`), and it used to sit around token 130 — so the prompt diverged
#          almost immediately and everything after it (every rule, the whole
#          template store) could not be shared at all.
# `spread` scatters the text_ids so they cannot be guessed in sequence, which brings
#          the template store into the shared part too.
#
# `spread` needs three things to be in place; drop any one of them and the call
# breaks immediately. Each was measured in this app, same script, same server
# (2026-09-07), three runs each:
#   1. Remap the ids inside the rules as well. Rules cite ids as literal numbers
#      ("disclose the balance with disclose_balance (1018)"), so changing only the
#      catalog makes the prompt contradict it — the model trusts the rule and speaks
#      an id the catalog does not have.
#   2. Preserve the ordering of the ids. A version that threw the order away (pure
#      hash) made the model open the call by disclosing the balance without verifying
#      identity — a KYC breach, 3 runs out of 3. The order is information the model
#      is actually using.
#   3. Accept "a beat name called as a tool". Once the numbers change, the model
#      starts calling beats by name (convince_other), which used to fall through as
#      unknown_tool and end the turn in silence.
# With all three in place, spread_cache speaks the same beat as legacy on every
# turn, 3 runs out of 3.
FLOW_PROMPT_SCHEME = os.environ.get("AAX6_DEMO_PROMPT", "spread_cache").strip().lower()
_PROMPT_SPREAD = FLOW_PROMPT_SCHEME in ("spread", "spread_cache")
_PROMPT_CRM_LAST = FLOW_PROMPT_SCHEME in ("cache", "spread_cache")


def remap_ids_in_text(text: str, mapping: dict) -> str:
    """Replace old text_ids with their new numbers in the instruction text — rules cite
    ids as bare numbers ("disclose the balance with disclose_balance (1018)"), so
    scattering the catalog without fixing them makes the prompt contradict itself and
    the model speaks an id the catalog lacks (measured: the call broke on turn 1, every
    time).

    One pass, not one id at a time, or a new number gets replaced again as another id's
    old one. Only whole-word numbers present in the catalog match, so amounts and phone
    numbers are untouched — and it must run before the CRM block with real values is
    appended, which a too-broad regex once overwrote.
    """
    if not mapping:
        return text
    pat = re.compile(r"(?<![0-9])(" + "|".join(str(k) for k in sorted(mapping, reverse=True))
                     + r")(?![0-9])")
    return pat.sub(lambda m: str(mapping[int(m.group(1))]), text)


def spread_text_ids(catalog: list[dict], preserve_order: bool = True) -> list[dict]:
    """Scatter the text_ids so they cannot be guessed in sequence, while staying stable
    (the same old id always maps to the same new number).

    A pure function of the id, no randomness, so every call of one company gets the same
    numbers and the prefix cache can be shared. Only the in-memory copy changes.

    `preserve_order=True` keeps the ordering while leaving irregular gaps of 7–96.
    Measured: a version that threw the ordering away (pure hash) made the model disclose
    the balance without verifying identity, 3 runs of 3 — the order is information it
    uses, not just a label.
    """
    if not preserve_order:
        used: set[int] = set()
        out = []
        for e in catalog:
            sid = int(e["text_id"])
            tid = 1000 + (sid * 7919 + 104729) % 9000
            while tid in used:
                tid = 1000 + (tid - 1000 + 1) % 9000
            used.add(tid)
            out.append({**e, "text_id": tid})
        return out
    cur, newv = 1000, {}
    for sid in sorted({int(e["text_id"]) for e in catalog}):
        cur += 7 + (sid * 7919 + 104729) % 90
        newv[sid] = cur
    return [{**e, "text_id": newv[int(e["text_id"])]} for e in catalog]


FLOW_MAX_TOOL_LOOPS = 8


def default_served_model() -> str:
    """The model to use when the caller did not pick one (the picker had not loaded
    when Start was clicked, or it sent "").

    It MUST be one vLLM is actually serving: a name that is not served 404s and the
    turn ends with no reply reaching the caller. So the endpoint is asked what it has,
    and `AAX6_FLOW_MODEL` only expresses a preference among those. With nothing served
    this returns "" and the 404 upstream says so plainly.
    """
    served = list(_model_endpoints().keys())
    if FLOW_MODEL and FLOW_MODEL in served:
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
            continue                     # a file starting with `_` is not a tenant
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
    """Tenant codes, derived from the files present. Dropping in a
        `<CODE>.company.json` registers a company; deleting the file removes it.

    """
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


def _read_catalog(spec: "dict | None") -> list[dict]:
    """Templates of a company — they live inside its spec, one company one file.

    The case "the templates are in a separate file named by the registry" used to be
    handled here too, but `load_flow_registry()` never put a `catalog` key into an entry,
    so that path was never reached (and would have raised KeyError if it had been). It
    went with the collapse of catalog shapes in `resolve_catalog`."""
    if not spec:
        return []
    from demo_v2.server.flow.flowspec import resolve_catalog
    try:
        return resolve_catalog(spec)
    except ValueError:
        return []


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
    """The rendered system instruction for a company's flow — assembled the SAME way
    FlowLiveSession assembles it, with [placeholders] intact (filled per-call).

    It has to follow AAX6_DEMO_PROMPT. When it did not, the reading pane showed the
    CRM at section 2 and the spec's own text_ids while the model was being fed the
    CRM last and a renumbered catalog — so anyone checking why the agent said a line
    was reading a prompt the agent never saw."""
    from demo_v2.server.flow.flowspec_render import render_crm_block, render_instruction
    from demo_v2.lib.prescript import build_template_block

    company = (company or "").strip().upper()
    if company not in load_flow_registry():
        return ""
    spec_path = _flow_spec_path(company)
    if not spec_path.exists():
        return ""
    spec = load_tenant_spec(spec_path)
    from demo_v2.server.flow.flowspec import normalize_catalog
    catalog = normalize_catalog(_read_catalog(spec), spec)
    remap: dict = {}
    if _PROMPT_SPREAD:
        _before = [e["text_id"] for e in catalog]
        catalog = spread_text_ids(catalog)
        remap = {o: n for o, n in zip(_before, (e["text_id"] for e in catalog)) if o != n}
    body = render_instruction(spec, crm="omit" if _PROMPT_CRM_LAST else "inline")
    body += "\n\n" + build_template_block(catalog)
    if remap:
        body = remap_ids_in_text(body, remap)
    # Voice gender is rendered into the prompt the model receives ({suffix} → the
    # particle), so it has to be rendered here too or the two texts differ for no
    # reason. Customer data is passed as {} to keep the [placeholder] tokens intact.
    from demo_v2.lib.prescript import fill_template as _ft
    out = _ft(body, {}, gender="F")
    if _PROMPT_CRM_LAST:
        out += "\n\n" + render_crm_block(spec)
    return out


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
    catalog = _read_catalog(spec)

    # fine_state → (state, phase) from the spec, plus the beat order in the flow
    fs_state: dict[str, tuple[str, str]] = {}
    states_out: list[dict[str, Any]] = []
    for st in spec.get("states", []):
        beats = [t.get("fine_state") for t in st.get("templates", []) if t.get("fine_state")]
        for b in beats:
            fs_state.setdefault(b, (st.get("id", ""), st.get("phase", "")))
        states_out.append({"state": st.get("id", ""), "phase": st.get("phase", ""),
                           "note": st.get("note", ""), "beats": beats})
    # faq_routing: a route holds templates:[{fine_state}] — counts as bound, in the
    # "faq" group
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
    # auxiliary_templates — the demo's runtime no longer reads this key, but the spec
    # checker still counts it as a binding (so a declared beat does not raise the
    # "referred to by nobody" warning). This view IS the checker, so it shows what the
    # checker sees: declared, but no edge reaches it.
    # (It used to read the key as a list when the real shape is {allowed:[…]}, which
    # produced a phantom beat named "allowed".)
    aux_beats = []
    for a in ((spec.get("auxiliary_templates") or {}).get("allowed") or []):
        fsx = a.get("fine_state") if isinstance(a, dict) else a
        if isinstance(fsx, str):
            fs_state.setdefault(fsx, ("aux", "aux")); aux_beats.append(fsx)
    if aux_beats:
        states_out.append({"state": "aux", "phase": "aux",
                           "note": "ประกาศใน auxiliary_templates — ไม่มี state/FAQ ไหนพาไปถึง",
                           "beats": aux_beats})

    entries = []
    for e in catalog:
        fs = e.get("_fine_state") or ""
        st, ph = fs_state.get(fs, ("", ""))
        entries.append({"text_id": e.get("text_id"), "fine_state": fs, "template": e.get("template", ""),
                        "state": st, "phase": ph, "bound": bool(st),
                        "intent_name": e.get("intent_name", "")})
    entries.sort(key=lambda x: (not x["bound"], str(x["state"]), str(x["text_id"])))
    return {"company": company, "display_name": reg[company].get("display_name", company),
            "version": instruction_version or flow_versions(company).get("default", ""),
            "spec_file": spec_path.name, "states": states_out, "entries": entries,
            "counts": {"templates": len(entries), "bound": sum(1 for x in entries if x["bound"]),
                       "fine_states": len({x["fine_state"] for x in entries if x["fine_state"]})}}


def get_flow_spec(company: str) -> dict[str, Any]:
    """Load a company's FlowSpec + the vocab the editor needs (catalog fine_states,
    declared tool names). Returns {} if the company/spec isn't found."""
    company = (company or "").strip().upper()
    if company not in load_flow_registry():
        return {}
    spec_path = _flow_spec_path(company)
    if not spec_path.exists():
        return {}
    spec = load_tenant_spec(spec_path)
    fine_states: list[str] = []
    templates: dict[str, list[str]] = {}
    cat = _read_catalog(spec)
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
    spec_path = _flow_spec_path(company)
    spec_now = load_tenant_spec(spec_path) if spec_path.exists() else None
    catalog = _read_catalog(spec_now)

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
            "_fine_state": fs, "intent_name": fs,
            "state": fs.split("_")[0], "is_closer": False, "is_demand": False,
            "is_acknowledgment": False, "expects_response": True,
        }
        catalog.append(entry)
        by_fs[fs] = entry
        next_tid += 1
        added += 1

    spec["company"] = company
    spec.setdefault("flow_id", company)   # the filename is the identity
                                          # (flowspec.load_tenant_spec)
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
    # The templates live in the same file as the spec (`catalog` is a list). This
    # line used to write them to a separate catalog file named by the registry, which
    # no longer exists.
    if added or updated:
        spec["catalog"] = catalog
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "company": company, "added_templates": added, "updated_templates": updated}


# --- creating a company from the JSON a user uploads --------------------------

def _demo_persona(company: str, display_name: str, agent_name: str) -> dict[str, Any]:
    """The playground's stand-in caller for a company that has just been uploaded.

    It carries no CRM row. It used to invent one — a 30,000-baht loan, a minimum payment,
    an overdue status — so a clinic uploading an appointment flow got a patient in debt
    collection. What the account holds is the tenant's to say (`session_init` fetches it,
    `crm_fields` decides what the model may see); what stays here is only what identifies
    the request and the two names the instruction renders.
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


def _register_demo_persona(company: str, display_name: str, agent_name: str,
                           spec: dict, catalog: list[dict],
                           crm: dict | None = None) -> dict[str, Any]:
    """Write the new company's demo caller, then report what its sentences cannot fill.

    Both create paths ended with this block, byte for byte, so a fix to either the
    persona or the audit had to be made twice and once was not.

    `crm` is the author's own CRM row (raw-upload only). `_demo_persona` deliberately
    carries none — inventing a customer's numbers is how a clinic's flow once opened by
    quoting a 30,000-baht loan — so this is the one way to give the demo a caller with
    data, short of pointing `session_init` at a real API.
    """
    persona = _demo_persona(company, display_name, agent_name)
    if isinstance(crm, dict):
        # Keys the persona owns (msisdn, the two rendered names) stay unless named here.
        # `_`-prefixed keys are the template's own notes to the author (`_hint`),
        # not fields — merging one would put it in the CRM snapshot the model reads.
        persona["customer_data"] = {
            **persona["customer_data"],
            **{k: v for k, v in crm.items()
               if not k.startswith("_") and v not in ("", None)}}
    existing = []
    if BUILDER_CASES_FILE.exists():
        try:
            existing = json.loads(BUILDER_CASES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = []
    existing = [c for c in existing if c.get("id") != persona["id"]] + [persona]
    BUILDER_CASES_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=1) + "\n",
                                  encoding="utf-8")

    # Say, at authoring time, which tokens nothing can fill. The session already
    # audits this and logs a warning, but by then the company exists and the author
    # is gone: a template that names a CRM field this company has no `session_init`
    # for is spoken with the bracket still in it. Naming them in the create response
    # is the only moment the person who wrote the template is still looking.
    from demo_v2.server.flow import session_init as _si
    # Audit what a session will actually hold: `<field>_offset_days` becomes `<field>`
    # at session start, so auditing the row as written would report every date field
    # as a leak and send the author chasing one that never happens.
    audit_ctx = dict(persona.get("customer_data") or {})
    _resolve_offset_dates(audit_ctx)
    if (spec.get("session_init") or {}).get("url"):
        # A declared `session_init` answers for the fields the tenant promises; nothing
        # here can read that API, so those names are not this app's to call unfillable.
        # The session audits the real response at call time and logs what is missing.
        audit_ctx.update({f: "" for f in (spec.get("crm_fields") or [])})
    leaks = _si.audit_placeholders([e.get("template", "") for e in catalog], audit_ctx)
    out = {"ok": True, "company": company, "case_id": persona["id"], "beats": len(catalog)}
    if leaks:
        out["unfillable_placeholders"] = leaks
        out["warning"] = ("ประโยคอ้างถึงข้อมูลที่บริษัทนี้ยังไม่มี — จะถูกพูดออกไปทั้งวงเล็บ: "
                          + ", ".join(f"[{x}]" for x in leaks))
    return out


def create_flow_company_raw(
    spec: dict, catalog: list[dict],
    display_name: str = "", agent_name: str = "", crm: dict | None = None,
) -> dict[str, Any]:
    """Author a new flow company from a RAW FlowSpec + catalog JSON — the JSON-editor
    path (no AEON clone, no prefill). The company code is read from ``spec['company']``.
    Validates via ``validate_flow_spec`` and, only when it passes, writes the spec +
    catalog, appends the registry, and writes a demo persona. Returns {ok, company,
    case_id, beats} or {ok:False, errors:[...]}.

    ``crm`` is the author's CRM row for the demo caller — the in-app way to give a new
    company data to speak without standing up its API first. Dates go in it as
    ``<field>_offset_days`` so the row cannot go stale.
    """
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
    flow_id = str(spec.get("flow_id") or company)
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

    return _register_demo_persona(company, display_name, agent_name, spec, catalog, crm)


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


def _repair_tool_json(raw: str) -> dict | None:
    """Repair a tool_call whose JSON the model wrote badly — returns a dict, or None if
    it cannot be repaired.

    Four failure modes measured on the gold/silver sets (2026-09-05). Each one lost the
    whole turn: the parser discarded the call and the system answered "no text_ids given",
    which was untrue, so a model at temperature 0 resent the same thing until the quota
    ran out.
      · unbalanced braces        {"text_ids": [1], "dynamic_vars": [5400]}
      · bare identifier in array "dynamic_vars": [available_dates_text]
      · cut off mid-string       (hit max_tokens: no } and no closing tag)
      · nested quotes            a reason copied from rule text that contains "..."
    Nothing here is tied to a domain or a company — it is pure JSON syntax repair."""
    s = (raw or "").strip()
    if not s:
        return None
    # 1) escape a `"` that sits inside a string body (what follows is not , : } ],
    #    so the string has not closed)
    out, in_str, esc, n = [], False, False, len(s)
    for i, ch in enumerate(s):
        if esc:
            out.append(ch); esc = False; continue
        if ch == "\\":
            out.append(ch); esc = in_str; continue
        if ch == '"':
            if not in_str:
                in_str = True; out.append(ch)
            else:
                j = i + 1
                while j < n and s[j] in " \t\n":
                    j += 1
                if j >= n or s[j] in ',:}]':
                    in_str = False; out.append(ch)
                else:
                    out.append('\\"')
            continue
        out.append(ch)
    s = "".join(out)
    # 2) quote a bare identifier standing in an array's value position
    def _q(m):
        w = m.group(2)
        return m.group(0) if w in ("true", "false", "null") else m.group(1) + '"' + w + '"' + m.group(3)
    s = re.sub(r"([\[,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*[\],])", _q, s)
    # 3) close whatever strings and brackets are still open, per the real stack
    stack, in_str, esc = [], False, False
    for ch in s:
        if esc:
            esc = False
        elif ch == "\\":
            esc = in_str
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]" and stack:
                stack.pop()
    if in_str:
        s += '"'
    try:
        d = json.loads(s + "".join(reversed(stack)))
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict):
        return None
    # A placeholder NAME that arrived as a "value" in dynamic_vars is not something
    # anyone said — drop it and let the template fall back, rather than reading a
    # variable name out loud.
    args = d.get("arguments")
    if isinstance(args, dict) and isinstance(args.get("dynamic_vars"), list):
        args["dynamic_vars"] = [v for v in args["dynamic_vars"]
                                if not (isinstance(v, str) and re.fullmatch(r"[a-z_][a-z0-9_]*", v))]
    return d


def _verify_gate(spec: dict) -> tuple[set, dict]:
    """(beats that need verification before they can be spoken, the unlock) — read
    entirely from what the spec declares.

    The spec says two things and the code knows nothing about any domain:
      · a state or FAQ route with `verify_required: true` → its beats are the sensitive
        ones
      · a tool with `provides: "verified"` is the unlock, together with `verified_when`
        describing how to read the API's answer ({"field": ..., "equals": ...} or
        {"any_success": true})
    If the spec asks for verification but no tool can unlock it, this returns "no gate" —
    otherwise the call would be blocked forever (AEONLITE declares verify_required while
    its only tool is the closer)."""
    def walk(node, acc):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "fine_state" and isinstance(v, str):
                    acc.add(v)
                elif k == "any_of" and isinstance(v, list):
                    acc.update(x for x in v if isinstance(x, str))
                else:
                    walk(v, acc)
        elif isinstance(node, list):
            for x in node:
                walk(x, acc)
    need = set()
    for st in (spec.get("states") or []):
        if st.get("verify_required"):
            walk(st.get("templates"), need)
            walk(st.get("chain"), need)
    for r in ((spec.get("faq_routing") or {}).get("routes") or []):
        if r.get("verify_required"):
            walk(r.get("templates"), need)
    unlock = {d["name"]: (d.get("verified_when") or {"any_success": True})
              for d in ((spec.get("tools") or {}).get("declarations") or [])
              if d.get("provides") == "verified"}
    return (need, unlock) if (need and unlock) else (set(), {})


def _is_verified(unlock: dict, call_log: list) -> bool:
    """Has the customer been verified? Decided from the API's answer, read the way the
    spec says to read it.

    Nothing is inferred from what was said or from an event label (a real call has none),
    and nothing is tied to a particular tool or field name."""
    for rec in call_log or []:
        rule = unlock.get(rec.get("tool"))
        if rule is None:
            continue
        res = rec.get("result")
        if not isinstance(res, dict) or res.get("error"):
            continue
        if rule.get("any_success"):
            return True
        field, want = rule.get("field"), rule.get("equals")
        val = res.get(field) if field else None
        if val is None and isinstance(res.get("data"), dict):
            val = res["data"].get(field)
        if val is None:
            continue
        if isinstance(want, bool):
            if str(val).strip().lower() in ("true", "1", "yes") if not isinstance(val, bool) else val:
                return bool(want)
        elif str(val).strip() == str(want).strip():
            return True
    return False


def _beat_states(spec: dict) -> dict:
    """beat → the states that declare it (chain and any_of included) — used to track how
    far along the app is."""
    out: dict = {}
    def walk(node, acc):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "fine_state" and isinstance(v, str):
                    acc.add(v)
                elif k == "any_of" and isinstance(v, list):
                    acc.update(x for x in v if isinstance(x, str))
                else:
                    walk(v, acc)
        elif isinstance(node, list):
            for x in node:
                walk(x, acc)
    for st in spec.get("states") or []:
        acc = set()
        walk(st.get("templates"), acc)
        walk(st.get("chain"), acc)
        for b in acc:
            out.setdefault(b, set()).add(st["id"])
    return out


def _resume_beats(spec: dict) -> set:
    """Beats speakable from anywhere: a FAQ that answers and returns to the flow.

    `auxiliary_templates` used to be counted here as well. It is not any more: that key is
    not tied to any edge, so it cannot say whether a beat is reachable from the current
    state. The spec checker still reads it (counting it as a binding); the demo's runtime
    does not."""
    out = set()
    for r in ((spec.get("faq_routing") or {}).get("routes") or []):
        for t in (r.get("templates") or []):
            if isinstance(t, dict):
                if t.get("fine_state"):
                    out.add(t["fine_state"])
                out.update(x for x in (t.get("any_of") or []) if isinstance(x, str))
    return out


def _off_flow_beats(spec: dict, cur_states: set, beats: set) -> list:
    """Beats unreachable from the state the app is standing in, by the edges the spec
    declares itself.

    The app knows where it is from the beat it just spoke — no event label on the customer
    is needed (a real call has none). Measured over 581 silver calls: what it caught was a
    genuine jump across the flow every time, e.g. closing as refused before payment was
    ever asked for, or referring without ever stating the minimum."""
    if not cur_states:
        return []
    b2s = _beat_states(spec)
    by_id = {st["id"]: st for st in (spec.get("states") or [])}
    allowed = set(cur_states)
    for sid in cur_states:
        for e in (by_id.get(sid, {}).get("on") or []):
            if e.get("to"):
                allowed.add(e["to"])
    resume = _resume_beats(spec)
    return [b for b in sorted(beats)
            if b not in resume and b in b2s and not (b2s[b] & allowed)]


def _closing_beats(spec: dict, result: str | None = None) -> set:
    """The correct closing templates once a result is recorded — narrowed to the result
    the model sent itself.

    Covers both terminal states and FAQ routes that end the call (those declaring
    then.outcome). With no result matching what was declared, it falls back to "every
    closing beat".

    Comparison goes through `outcome_result`, because the argument that carries the result
    belongs to each tool (`result` for collections, `status` for appointments) and is not a
    fixed name."""
    from demo_v2.server.flow.flowspec import outcome_result as _outcome_result
    def walk(node, acc):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "fine_state" and isinstance(v, str):
                    acc.add(v)
                elif k == "any_of" and isinstance(v, list):
                    acc.update(x for x in v if isinstance(x, str))
                else:
                    walk(v, acc)
        elif isinstance(node, list):
            for x in node:
                walk(x, acc)
    exact, allt = set(), set()
    for st in (spec.get("states") or []):
        if not st.get("terminal"):
            continue
        b = set()
        walk(st.get("templates"), b)
        walk(st.get("chain"), b)
        allt |= b
        if result and _outcome_result(spec, st.get("outcome")) == result:
            exact |= b
    for r in ((spec.get("faq_routing") or {}).get("routes") or []):
        then = r.get("then")
        if not isinstance(then, dict) or not then.get("outcome"):
            continue
        b = set()
        walk(r.get("templates"), b)
        allt |= b
        if result and _outcome_result(spec, then.get("outcome")) == result:
            exact |= b
    return exact or allt


def _recover_toolcalls(content: str) -> list[dict]:
    """Some adapters emit tool calls as literal <tool_call>{...}</tool_call> text
    that vLLM's parser misses. Recover them into the OpenAI tool_calls shape."""
    out = []
    blocks = [m.group(1) for m in _TOOLCALL_JSON_RE.finditer(content or "")]
    if not blocks and "<tool_call>" in (content or ""):
        # A call cut off mid-flight: no trailing }, no closing tag, so
        # _TOOLCALL_JSON_RE cannot match it at all
        m = re.search(r"<tool_call>\s*(\{.*?)(?:</tool_call>|$)", content, re.S)
        if m:
            blocks = [m.group(1)]
    for block in blocks:
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            obj = _repair_tool_json(block)
            if obj is None:
                continue
        name = obj.get("name")
        args = obj.get("arguments", obj.get("parameters", {}))
        # Same reason as the parsed path below: the chat template iterates the
        # arguments, so anything that is not an object poisons every later hop of the
        # turn. A string is kept only if it really holds one — `"{...}"` is how the
        # OpenAI shape carries arguments and must survive.
        if isinstance(args, str):
            try:
                args = args if isinstance(json.loads(args), dict) else {}
            except (ValueError, TypeError):
                args = {}
        elif not isinstance(args, dict):
            args = {}
        if name:
            out.append({"id": "call_rec", "type": "function", "function": {
                "name": name,
                "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)}})
    return out


def _strip_toolcall_markup(content: str) -> str:
    """Drop any leaked tool-call XML so it never reaches the customer/TTS."""
    return _TOOLCALL_TAG_RE.sub("", _TOOLCALL_JSON_RE.sub("", content or "")).strip()


def _flow_vllm_chat(base_url: str, payload: dict, timeout: int = 180) -> dict:
    """One chat-completions call to vLLM, returning the assistant message.

        Thinking is off by default because that is how the model was trained, and the
        tools parameter is always sent — Qwen3.5 will not emit a tool call without it.
        Transport errors are raised rather than swallowed: a turn that cannot reach the
        model has nothing useful to say, and the caller surfaces that.

    """
    # Thinking OFF explicitly: the chat template turns it ON when `enable_thinking`
    # is undefined, so silence was never 'the default'. Measured with it on: ~2x the
    # tokens, duplicated beats, and the eval harness has always sent False.
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
        from demo_v2.lib.prescript import DateFormatError, build_template_block, fill_template
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

        spec_path = _flow_spec_path(self._company, self._instruction_version)
        self._spec = load_tenant_spec(spec_path)
        # A catalog only has to declare _fine_state + template; text_id, company,
        # state and intent_name are derived from the spec that binds it.
        from demo_v2.server.flow.flowspec import normalize_catalog, resolve_catalog
        self._catalog = normalize_catalog(resolve_catalog(self._spec), self._spec)
        self._id_remap: dict = {}
        if _PROMPT_SPREAD:
            _before = [e["text_id"] for e in self._catalog]
            self._catalog = spread_text_ids(self._catalog)
            self._id_remap = {o: n for o, n in zip(_before, (e["text_id"] for e in self._catalog))
                              if o != n}
        self._by_id = {e["text_id"]: e for e in self._catalog}

        # --- spec-declared session-init API -----------------------------------
        # If the spec declares `session_init`, that call runs HERE (before turn 1) and
        # its response becomes the render context. The repo persona stays as the seed:
        # it supplies the request's own tokens ({msisdn}/{case_ref}).
        from demo_v2.server.flow import session_init
        from demo_v2.lib import prescript as _prescript

        self.init_result = session_init.fetch_context(self._spec, seed=self.customer_data)
        if self.init_result["data"]:
            self.customer_data.update(self.init_result["data"])
        # A session_init that did not answer leaves the seed in place, and the seed is a
        # fixture — measured: with the API down the agent still read out 45,000/4,500
        # from `_builder_personas.json`. So the call does not proceed on stale context;
        # `session_init.on_failure` names what to say instead, and a spec without it
        # gets no sentence invented for it (the session refuses to open).
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

        _crm_mode = "omit" if _PROMPT_CRM_LAST else "inline"
        _instr = render_instruction(self._spec, crm=_crm_mode)
        # Values stay as [placeholder] here and are read from the CRM block at the end
        # instead: rules cite customer values too, so substituting them made the prompt
        # diverge mid-instruction. Mamba's align-mode prefix cache is all-or-nothing
        # (the tail must be under one ~528-token block), so the hit rate was 0% with
        # 43% of the text shared.
        _body = _instr
        # Full text, not the compact listing. Compact prints only the id and the
        # beat name, so N wordings of one beat arrive as N lines that differ by
        # a number and nothing else — the model is asked to pick a variant it
        # cannot see. (AEON's `verify_name` has 7; `ask_pay_today` has 9.) The
        # training prompt shows every template in full, so this is also what the
        # model was trained to read.
        _body += "\n\n" + build_template_block(self._catalog)
        # Ids are cited as numbers both in the rules ("disclose the balance with
        # disclose_balance (1018)") and in each template's hint ("interchangeable with
        # 8520"), so the remap runs once over the whole text, not just the instruction
        # body. It has to happen BEFORE the CRM block with real values is appended, or
        # it would overwrite the customer's own numbers.
        if self._id_remap:
            _body = remap_ids_in_text(_body, self._id_remap)
        system = self._fill_template(_body, {} if _PROMPT_CRM_LAST else cd,
                                     gender=self.voice_gender)
        if _crm_mode == "omit":
            # The CRM block goes after the templates — everything above this line is
            # identical for every call of this company
            from demo_v2.server.flow.flowspec_render import render_crm_block
            system += "\n\n" + self._fill_template(render_crm_block(self._spec), cd,
                                                   gender=self.voice_gender)
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
        # A part-spoken chain is the #1 failure the gold eval sees (KBANK: `close`
        # alone where `confirm_info → close` was ordered), so the app holds such a
        # reply back and asks for the whole chain.
        from demo_v2.server.flow.flowspec import is_chain_state as _is_chain
        # A beat can belong to SEVERAL chain states, so keep every candidate chain per
        # beat and let the gate pick the one the reply fits. A single-owner map
        # overwrote `close` with the last state and demanded `apology` on a PTP close.
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
        # Where the app is in the flow — tracked from the beats it spoke itself, not
        # from a label put on the customer
        _init = next((st["id"] for st in (self._spec.get("states") or []) if st.get("initial")), None)
        self._cur_states = {_init} if _init else set()
        self._verify_need, self._verify_unlock = _verify_gate(self._spec)
        self._recorded_result: str | None = None   # the result the model has already
                                                   # recorded, if any

        # the actual model for API calls — MUST resolve to something served, or every
        # turn 404s at vLLM with no reply reaching the caller (see default_served_model).
        self._model = self._model_override or default_served_model()
        # multi-instance aware: route to the endpoint actually serving this model
        # (one checkpoint per port); falls back to the default endpoint.
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
        # This is the ONLY place the app calls the closing tool itself: the CRM did
        # not answer, so there is no conversation for the model to judge. The args come
        # from the spec through the same normalizer the prompt uses.
        from demo_v2.server.flow.flowspec import closing_tool as _closing_tool_decl
        from demo_v2.server.flow.flowspec import outcome_args as _outcome_args
        outcome = cfg.get("outcome") or {}
        _decl = _closing_tool_decl(self._spec)
        closer = (_decl or {}).get("name")
        args = _outcome_args(self._spec, outcome)
        if closer and args:
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
        """One customer turn in, a stream of hops out.

        Thin — the work is in `_aiter_run`. What lives here is the ordering guarantee:
        if the caller speaks before the opening was streamed, the greeting is seeded
        into history first, so the model always sees greeting → customer → agent, the
        order it trained on.
        """
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
        """Start the call over on the same case: clear the transcript, rebuild the
        agent from the spec. The CRM row fetched at construction is restored —
        restarting the conversation is not a reason to hit the tenant's API again,
        but it is every reason to forget what the last call wrote into that row.

        Everything reset here is per-conversation. Clearing the transcript alone is
        not enough: the flow's position lives in four more attributes, and with
        `_recorded_result` still stamped the first reply of the NEW call is refused
        by the closing gate below ("บันทึกผล ptp แล้ว, now say that result's
        farewell"), so the model hangs up on the identity turn. Measured on AEON,
        answering the identity check with "ครับ": 3/3 calls ended there before this,
        0/3 after.
        """
        self._turn_count = 0
        self.done = False
        self._transcript = []
        # `SpecBackend._merge_context` writes each successful tool answer back into
        # customer_data in place, on purpose, so a re-checked balance is what the next
        # template speaks. That makes the row conversation state too.
        if self._crm_snapshot is not None:
            self.customer_data = dict(self._crm_snapshot)
        self._recorded_result = None
        self._step_nudges = 0
        self._off_catalog_replies = 0
        _init = next((st["id"] for st in (self._spec.get("states") or [])
                      if st.get("initial")), None)
        self._cur_states = {_init} if _init else set()
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
        # The row as the tenant gave it, kept so `reset_pointer` can hand the next
        # call the same starting facts without asking the API again. Taken once, at
        # construction — a later reset must not re-snapshot what the last call wrote.
        if getattr(self, "_crm_snapshot", None) is None:
            self._crm_snapshot: dict | None = dict(self.customer_data)
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

    def _render_reply(self, args: dict) -> tuple[list[int], str, dict, list]:
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
        # Ids the model spoke that are not in the catalog. They used to be dropped in
        # silence, so a whole sentence disappeared with nobody the wiser (the caller
        # heard the fallback instead). Return them so the caller can reject.
        unknown: list = []
        for tid in ids:
            if not str(tid).lstrip("-").isdigit():
                continue
            tid_i = int(tid)
            e = self._by_id.get(tid_i)
            if e is None:
                unknown.append(tid_i)
                continue
            if e["text_id"] in seen:
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
        return good, " ".join(texts), dyn if isinstance(dyn, dict) else {}, unknown

    def _reachable_ids(self, cap: int = 6) -> str:
        """Example ids of beats reachable from the current state — used as the hint on a
        reject.

        Read from the spec and the task's own catalog only; no list of beats is baked into
        this file. If the spec declares no edges (cur_states is empty) this returns nothing
        rather than guessing on the spec's behalf."""
        b2i: dict = {}
        for e in self._catalog:
            b2i.setdefault(e["_fine_state"], []).append(e["text_id"])
        cand = sorted(b2i)
        if self._cur_states:
            off = set(_off_flow_beats(self._spec, self._cur_states, set(cand)))
            cand = [b for b in cand if b not in off]
        if not cand:
            return ""
        return ", ".join("%s (%s)" % (b2i[b][0], b) for b in cand[:cap])

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

        # "Please hold" bubble while a tool call is in flight. Off by default (the
        # users did not want it); AAX6_FLOW_FILLER=1 turns it on. Client-stream only —
        # never into self._messages, which stays byte-identical.
        _filler_on = os.environ.get("AAX6_FLOW_FILLER", "0").strip().lower() not in ("0", "false", "")
        _filler_state = {"bubble": False}

        def _emit_filler(tool_name: str) -> None:
            if not _filler_on or _filler_state["bubble"]:
                return
            _filler_state["bubble"] = True
            # Neutral wording: the old line said the agent was *recording*, which is
            # a lie in front of a lookup. "Please hold" is true of a read and a write.
            text = self._fill_template("รบกวนรอสักครู่นะ{q_suffix}",
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
                    try:
                        args = (json.loads(raw_args) if isinstance(raw_args, str)
                                else (raw_args or {}))
                    except (ValueError, TypeError):
                        # The tool loop's `try` has only a `finally`, so a decode error
                        # here left the turn as an unhandled exception mid-stream.
                        args = {}
                    # A tool call is stored back into `_messages` and re-rendered on
                    # every later hop of the same turn, and the chat template does
                    # `tool_call.arguments|items` — so anything that is not an object
                    # kills the REST of the turn with a 400 ("Can only get item pairs
                    # from a mapping"), not just the hop that produced it. The parser
                    # hands back a bare list often enough to matter (`_render_reply`
                    # already tolerates stringified args on the way out; this is the
                    # way in). Verified against the served model: `[1908]` → 400,
                    # `{}` → 200.
                    if not isinstance(args, dict):
                        args = {}
                    # The model sometimes calls a BEAT as if it were a tool
                    # (convince_other, say) instead of replying with that beat's
                    # text_id. The name is the intent, and it is clear enough to act
                    # on. It used to fall through as unknown_tool and the turn ended in
                    # silence — the caller heard nothing. (The gold harness's env
                    # already accepted a beat name in place of an id; the app did not.)
                    # Only names that really are a fine_state in THIS call's catalog are
                    # converted, and they must not collide with a tool name the spec
                    # declares — no list of beats is baked into this file.
                    if (fn["name"] != "reply" and fn["name"] not in getattr(self._backend, "_decls", {})
                            and fn["name"] in {e["_fine_state"] for e in self._catalog}):
                        _fs = fn["name"]
                        _ids = [e["text_id"] for e in self._catalog if e["_fine_state"] == _fs]
                        push({"kind": "warning",
                              "text": f"เรียก {_fs} เป็นเครื่องมือ — ตีความเป็น reply ของจังหวะนั้น"})
                        fn = {"name": "reply"}
                        args = {"text_ids": _ids[:1], "dynamic_vars": []}
                    if fn["name"] == "reply":
                        # A reply gate that judged 'verified' itself used to sit here: the list of
                        # sensitive slots was one domain's, and the verdict was a guess. A tenant that
                        # needs the rule declares it (`verify_required` + a tool that provides it).
                        try:
                            ids, text, dyn, _unknown_ids = self._render_reply(args)
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
                            dyn, _unknown_ids = {}, []

                        # The unknown-id gate: the model speaks a text_id that does not
                        # exist. It used to be dropped in silence and fall through to
                        # the fallback, so the caller heard a sentence that sounded fine
                        # but was about the wrong thing, and the model never learned it
                        # had picked wrong. This gate reads only the task's own catalog
                        # (no company-specific knowledge) and follows the same shape as
                        # the others: reject, hint, count against the quota.
                        if _unknown_ids and self._step_nudges < 2:
                            self._step_nudges += 1
                            _ok_ids = self._reachable_ids()
                            _hint = ("text_id %s ไม่มีในคลังของบริษัทนี้ — เลือกจากคลังที่ให้ไว้เท่านั้น "
                                     "ห้ามแต่งเลขเอง%s"
                                     % (", ".join(str(u) for u in _unknown_ids),
                                        (" จังหวะที่ไปได้ตอนนี้: " + _ok_ids) if _ok_ids else ""))
                            self._messages.append({"role": "assistant", "tool_calls": [{
                                "id": tc.get("id", "call_x"), "type": "function",
                                "function": {"name": "reply",
                                             "arguments": json.dumps(args, ensure_ascii=False, default=str)}}]})
                            self._messages.append({"role": "tool", "tool_call_id": tc.get("id", "call_x"),
                                                   "content": json.dumps({"sent": False,
                                                                          "reason": "unknown_text_id",
                                                                          "unknown_text_ids": _unknown_ids,
                                                                          "hint": _hint}, ensure_ascii=False)})
                            push({"kind": "tool_call", "name": "reply", "args": args})
                            push({"kind": "tool_result", "name": "reply",
                                  "result": {"sent": False, "reason": "unknown_text_id",
                                             "unknown_text_ids": _unknown_ids}})
                            continue
                        if not ids and not self._looks_sayable(text):  # reply [] → safe fallback
                            ids, text = self._fallback_reply()

                        # The verification gate: a beat whose state sets
                        # verify_required cannot be spoken until the tool declaring
                        # provides:"verified" answers in the way verified_when
                        # describes. No list of sensitive slots lives in the code, and
                        # nothing is inferred from what the customer said or is
                        # labelled as.
                        _will0 = {self._by_id[t]["_fine_state"] for t in ids if t in self._by_id}
                        _need = sorted(_will0 & self._verify_need)
                        if (_need and self._verify_unlock
                                and not _is_verified(self._verify_unlock, self._backend.call_log)
                                and self._step_nudges < 2):
                            self._step_nudges += 1
                            _hint = ("ยังไม่ได้ยืนยันตัวตนลูกค้า — ห้ามพูด %s ก่อน "
                                     "ให้เรียก %s ให้สำเร็จก่อน แล้วค่อยตอบ"
                                     % (", ".join(_need), " หรือ ".join(sorted(self._verify_unlock))))
                            self._messages.append({"role": "assistant", "tool_calls": [{
                                "id": tc.get("id", "call_x"), "type": "function",
                                "function": {"name": "reply",
                                             "arguments": json.dumps(args, ensure_ascii=False, default=str)}}]})
                            self._messages.append({"role": "tool", "tool_call_id": tc.get("id", "call_x"),
                                                   "content": json.dumps({"sent": False,
                                                                          "reason": "verify_required",
                                                                          "blocked_beats": _need,
                                                                          "hint": _hint}, ensure_ascii=False)})
                            push({"kind": "tool_call", "name": "reply", "args": args})
                            push({"kind": "tool_result", "name": "reply",
                                  "result": {"sent": False, "reason": "verify_required",
                                             "blocked_beats": _need}})
                            continue

                        # An `off_flow_beat` gate used to sit here (removed 2026-09-07): the app's
                        # idea of where it stands is too weak to refuse speech over — one beat lives
                        # in several states, so cur_states is a union that widens all call long. What
                        # it caught is still caught by `missing_required_tools` and
                        # `closing_reply_required`, which read what happened instead of guessing.
                        _will_beats = {self._by_id[t]["_fine_state"] for t in ids if t in self._by_id}
                        _closing_need: set = set()
                        if self._recorded_result:
                            # The result is stamped: the next line has to be that
                            # result's farewell
                            _cl = _closing_beats(self._spec, self._recorded_result)
                            if _cl and _will_beats and not (_will_beats & _cl):
                                _closing_need = _cl
                        if _closing_need and self._step_nudges < 2:
                            self._step_nudges += 1
                            _b2i: dict = {}
                            for _e in self._catalog:
                                _b2i.setdefault(_e["_fine_state"], []).append(_e["text_id"])
                            _sugg = ", ".join("%s (%s)" % (_b2i.get(b, ["?"])[0], b)
                                              for b in sorted(_closing_need)[:3])
                            _reason = "closing_reply_required"
                            _hint = ("บันทึกผล %s แล้ว — เหลือแค่พูดประโยคปิดสายของผลนั้น: "
                                     "เรียก reply(text_ids=[...]) ด้วย %s"
                                     % (self._recorded_result, _sugg))
                            self._messages.append({"role": "assistant", "tool_calls": [{
                                "id": tc.get("id", "call_x"), "type": "function",
                                "function": {"name": "reply",
                                             "arguments": json.dumps(args, ensure_ascii=False, default=str)}}]})
                            self._messages.append({"role": "tool", "tool_call_id": tc.get("id", "call_x"),
                                                   "content": json.dumps({"sent": False, "reason": _reason,
                                                                          "hint": _hint}, ensure_ascii=False)})
                            push({"kind": "tool_call", "name": "reply", "args": args})
                            push({"kind": "tool_result", "name": "reply",
                                  "result": {"sent": False, "reason": _reason}})
                            continue

                        # Step completeness: the beat about to be spoken belongs to a state
                        # whose spec declares entry_tools — have they actually been called?
                        # Reject with a hint and let the model retry in this same loop.
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
                        _new_states = set().union(*([_beat_states(self._spec).get(
                            self._by_id[t]["_fine_state"], set()) for t in ids if t in self._by_id] or [set()]))
                        if _new_states:
                            self._cur_states = _new_states   # a FAQ has no state of its
                                                             # own → stay where we are
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
                    # Remember the result that was successfully recorded, so the
                    # closing gate knows which farewell is the right one. What counts
                    # as a call result comes from the enum the spec declares, not from
                    # an argument name.
                    if isinstance(result, dict) and not result.get("error"):
                        _decl = next((d for d in (self._spec.get("tools") or {}).get("declarations", [])
                                      if d.get("name") == fn["name"]), None)
                        if _decl and (_decl.get("gating") or {}).get("required_at") == "end_of_call":
                            _enums = {str(v) for a, meta in (_decl.get("args") or {}).items()
                                      for v in ((meta or {}).get("enum") or [])}
                            _got = next((str(v) for v in (args or {}).values() if str(v) in _enums), None)
                            if _got:
                                self._recorded_result = _got
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
                # The tool loop can run out without the model ever calling reply, and a turn
                # with no speech is dead air on a live call. Say the fallback instead — which
                # sentence comes from the spec (`fallback_fine_state`).
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


def build_trajectory_case(session: "FlowLiveSession", comment: str = "") -> dict[str, Any]:
    """Assemble a saved-trajectory case from a session's recorded transcript.

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
    """Build the session for one call.

        There is one kind now: a tenant's spec drives it. `mode`, `agent` and `flow` are
        still accepted because the frontend sends `?flow=1` and older links carry `mode`,
        but nothing branches on them — the replay and pre-script lineages they used to
        select are gone.

    """
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
            "catalog: ใส่แค่ _fine_state + template พอ — text_id/company/state/intent_name ระบบเติมให้เอง",
            "beat เดียวใส่ได้หลายบรรทัด = หลายสำนวน (เช่น close 2 บรรทัดข้างล่าง) โมเดลเลือกเองว่าจะพูดสำนวนไหน",
            "templates ใน 1 state: มีหลายอันแบบไม่มี when_event = พูดต่อกันในเทิร์นเดียว (chain) / มี when_event = เลือกอันเดียวตาม event",
            "agent_role = agent เป็นใคร (บรรทัดแรกของ prompt) — ไม่ใส่ prompt จะบอกโมเดลว่าเป็น "
            "เจ้าหน้าที่ทวงหนี้ (ค่าเริ่มต้นของงานทวงหนี้) · น้ำเสียงคุมที่ประโยคใน catalog ไม่ใช่ที่ prompt "
            "เพราะโมเดลพูดได้เฉพาะ template ในคลังคำต่อคำ",
            "crm = ข้อมูลลูกค้าของสายเดโม — ต้องครอบ crm_fields ให้ครบ ไม่งั้นประโยคจะพูด [ชื่อ field] ออกไปจริง · "
            "วันที่ใส่เป็น <field>_offset_days (จำนวนวันจากวันนี้) ห้ามเขียนวันที่ตายตัว · "
            "ถ้าประกาศ session_init ให้ดึงจาก API จริงแล้ว ไม่ต้องใส่ crm",
            "tools = HTTP webhook: แต่ละ tool ยิง POST ไป url พร้อม body (แทน {customer_name} {amount} ...).",
            "faq_routing = ลูกค้าถามแทรกกลางสาย → ตอบด้วย template ไหน แล้วกลับเข้า flow เดิม",
            "constraints = กฎของบริษัท: ใส่ type ถ้าอยากให้ระบบบังคับจริง / ไม่ใส่ type แต่ใส่ desc = กฎที่เขียนลง prompt ให้โมเดลอ่าน",
        ],
        "spec": {
            "company": "YOURCO",
            "agent_role": "เจ้าหน้าที่ของบริษัท YOURCO",
            "crm_fields": ["customer_name", "your_field_1", "your_field_2"],
            "events": {
                "name_confirmed": {"desc": "ลูกค้ายืนยันตัวตน", "cues": ["ใช่", "ครับ", "ค่ะ"]},
                "asks_caller": {"desc": "ลูกค้าถามว่าโทรจากที่ไหน", "cues": ["ใครโทรมา", "ที่ไหน"]},
            },
            "tools": {"declarations": [
                {"name": "record_outcome", "impl": "http", "desc": "บันทึกผลสาย",
                 "url": "https://api.yourcompany.co.th/aax/record_outcome", "method": "POST",
                 # The first argument is the call result — each closing state's
                 # `outcome.args` sends its value here. Declare an enum and gate 2
                 # rejects anything outside the list on its own.
                 "args": {"result": {"type": "string", "enum": ["reached", "unresolved"]},
                          "reason": {"type": "string", "optional": True}},
                 "returns": {"saved": {"type": "boolean"}},
                 "gating": {"required_at": "end_of_call"}},
                {"name": "notify_crm", "impl": "http", "desc": "ตัวอย่าง webhook — ไม่ต้องประกาศ body ระบบส่ง {tool,args,ref} ให้เอง",
                 "url": "https://api.yourcompany.co.th/aax/notify", "method": "POST", "args": {}},
            ]},
            "states": [
                {"id": "greet", "phase": "opening", "initial": True,
                 "templates": [{"fine_state": "greet_verify"}],
                 "on": [{"event": "name_confirmed", "to": "close"}]},
                {"id": "close", "phase": "close", "terminal": True,
                 "templates": [{"fine_state": "close"}], "entry_tools": ["record_outcome"],
                 # Current shape: `args` = the closing tool's arguments, written with
                 # its real argument names
                 "outcome": {"args": {"result": "reached"}, "reasons": ["done"],
                             "desc": "จบสาย"}},
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
                 "template_fine_states": ["close"], "enforce": ["prompt"],
                 "desc": "ประโยคปิดสายพูดครั้งเดียวต่อสาย"},
                {"enforce": ["prompt"],
                 "desc": "ตัวอย่างกฎแบบข้อความ (ไม่มี type) — จะถูกเขียนลง instruction ให้โมเดลอ่าน แต่ระบบไม่บังคับเชิงกลไก"},
            ],
        },
        "crm": {
            "customer_name": "สมชาย ใจดี",
            "your_field_1": "…",
            "your_field_2": "…",
            "_hint": "ทุกชื่อใน crm_fields ต้องมีค่าที่นี่ · วันที่ใส่เป็น <field>_offset_days: 1",
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
                json.dumps(kept, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            removed.append(f"personas x{len(cases) - len(kept)}")

    return {"ok": True, "company": company, "removed": removed}


def _vllm_base_urls() -> list[str]:
    """vLLM endpoints to query. AAX6_VLLM_BASE_URLS = comma-separated list (multi-model:
    one checkpoint per port); falls back to the single AAX6_VLLM_BASE_URL."""
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
    # Every checkpoint lives under the "qwen" picker (base list). The engine is
    # routed by the flow/company selection, so the picker just lists served versions.
    sft = sorted(i for i in ids if i.lower().startswith("sft"))
    base_only = sorted(i for i in ids if not i.lower().startswith("sft"))
    return {"base": base_only + sft, "flow": [i for i in sft if "flow" in i.lower()]}
