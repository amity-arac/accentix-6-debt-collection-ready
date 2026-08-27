"""Spec-declared session-init API — fetch the call's context before turn 1.

WHY THIS EXISTS

Until now a flow company's render context came from a persona JSON shipped in the
repo. That is fine for a canned demo and useless for production: a real deployment
knows the customer only through its own CRM. And when a field is absent, every
template that references it speaks the raw token at the customer — observed on AMT:

    "คุณมีนัดพบ [doctor_name] วันที่ [appointment_date]"

So a spec may declare ONE call to make at session start, whose response becomes the
render context. It is declared exactly like a webhook tool — url, method, headers,
body — and NOTHING else:

    "session_init": {
      "url": "https://crm.example.com/api/case/{case_ref}",
      "method": "POST",                        # default GET
      "headers": {"Authorization": "Bearer …"},
      "body": {"phone": "{msisdn}"},           # {tokens} filled from the seed
      "timeout": 8
    }

**No field mapping.** Whatever the API returns IS the context, the same way a tool
call's response goes straight back as the observation. A field named `doctor_name`
in the response fills `[doctor_name]` in a template — the API's own contract is the
only contract. Nested payloads still work: objects are flattened, so
`{"appointment": {"doctor": "…"}}` is reachable as both `[appointment.doctor]` and
`[doctor]`. That keeps a legacy CRM usable without asking anyone to write a mapping
table, while a purpose-built endpoint just returns the names its templates use.

FAILURE IS NOT FATAL. A live call must not die because a CRM timed out: the fetch
returns `ok=False` with the reason, the caller keeps whatever seed context it had,
and the UI can show it. Silent partial context is the thing to avoid, not a slow
CRM — hence `audit_placeholders()`, which names exactly which placeholders would
still be spoken literally.
"""
from __future__ import annotations

import json as _json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("demo.server.flow.session_init")

_TOKEN_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
# Catalogs use BOTH bracket styles — AEON writes {customer_name}, KBANK/AMT write
# [customer_name] — and fill_template substitutes both. An audit that scanned only
# square brackets therefore reported a clean bill for every curly-brace catalog,
# which is the loudest way to be useless: it was silent about exactly the company
# whose templates it could not see.
_PLACEHOLDER_RE = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]+)\]|\{([A-Za-z_][A-Za-z0-9_]+)\}")
# Resolved by render_gender() from the caller's voice, never from the CRM record.
_GENDER_TOKENS = frozenset({"suffix", "q_suffix", "pronoun"})


def _resolve(name: str, ctx: dict) -> str | None:
    """A {token}'s value: the call context first, then — for ALL-CAPS names only —
    the process environment. That is what lets a spec write
    `{API_BASE}/AEON/record_outcome` or an `Authorization: Bearer {CRM_TOKEN}`
    header and have staging vs production differ by env alone, with no spec edit.
    Restricted to ALL-CAPS so a lowercase data field can never be answered by a
    stray environment variable of the same name.
    """
    if ctx.get(name) is not None:
        return str(ctx[name])
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
        env = os.environ.get(name)
        if env:
            return env
    return None


def substitute(blob: Any, ctx: dict) -> Any:
    """Fill {token} references from ctx (see `_resolve`), recursing dicts/lists.

    A token with no value anywhere is left intact rather than blanked — a URL that
    silently loses its id is far harder to debug than one that 404s visibly.
    """
    if isinstance(blob, str):
        return _TOKEN_RE.sub(
            lambda m: _resolve(m.group(1), ctx) or m.group(0),
            blob,
        )
    if isinstance(blob, dict):
        return {k: substitute(v, ctx) for k, v in blob.items()}
    if isinstance(blob, list):
        return [substitute(v, ctx) for v in blob]
    return blob


def http_json(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    body: Any = None,
    timeout: float = 8.0,
) -> tuple[Any, str | None]:
    """One JSON HTTP call. Returns (parsed_or_text, error_code_or_None).

    Every transport/HTTP failure comes back as an error string rather than an
    exception, because both callers (session init, webhook tool) must surface the
    failure to the model or UI instead of crashing the turn.
    """
    data = None
    if body not in (None, ""):
        data = (body if isinstance(body, str)
                else _json.dumps(body, ensure_ascii=False)).encode("utf-8")
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    try:
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
        try:
            return _json.loads(text), None
        except (ValueError, TypeError):
            return text[:2000], None
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001 — best-effort diagnostics only
            pass
        return None, f"http_{e.code}: {detail}"
    except Exception as e:  # noqa: BLE001 — surface any transport error, never raise
        return None, f"http_error: {str(e)[:200]}"


def flatten(payload: Any, prefix: str = "") -> dict:
    """Response → context, pass-through. Scalars keep their own key; nested objects
    are also exposed by dotted path AND by bare leaf name.

    The bare name is what makes a mapping table unnecessary — a CRM returning
    {"appointment": {"doctor": "…"}} fills `[doctor]` with no config. First writer
    wins on a leaf-name collision, so a top-level field is never shadowed by
    something buried deeper; the dotted form stays available to disambiguate.
    Lists are passed through whole (a template can't speak a list, but a webhook
    tool or the model may still want it).
    """
    out: dict = {}
    if not isinstance(payload, dict):
        return out
    for key, value in payload.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            nested = flatten(value, f"{path}.")
            for nk, nv in nested.items():
                out.setdefault(nk, nv)
            continue
        out.setdefault(path, value)
        if prefix and key not in out:      # bare leaf name, if nothing claimed it
            out[key] = value
    return out


def fetch_context(spec: dict, seed: dict | None = None) -> dict:
    """Run the spec's `session_init` call and return what it contributes.

    Result keys:
      declared -- False when the spec has no session_init (nothing ran)
      ok       -- the call succeeded and produced at least one field
      data     -- {field: value} to merge into the render context
      error    -- failure reason, if any
    """
    cfg = (spec or {}).get("session_init") or {}
    if not cfg or not cfg.get("url"):
        return {"declared": False, "ok": False, "data": {}, "error": None}

    ctx = dict(seed or {})
    ctx.setdefault("API_BASE", os.getenv("AAX6_API_BASE", "http://127.0.0.1:3001"))
    # Let a spec point at this very server for demos without hardcoding a host.
    ctx.setdefault("BASE_URL", os.getenv("AAX6_DEMO_SELF_URL", "http://127.0.0.1:4100"))

    url = substitute(cfg["url"], ctx)
    payload, error = http_json(
        url,
        method=cfg.get("method", "GET"),
        headers=substitute(cfg.get("headers") or {}, ctx),
        body=substitute(cfg.get("body"), ctx),
        timeout=float(cfg.get("timeout", 8)),
    )
    if error:
        logger.warning("session_init %s failed: %s", url, error)
        return {"declared": True, "ok": False, "data": {}, "error": error}
    if not isinstance(payload, dict):
        return {"declared": True, "ok": False, "data": {},
                "error": "response_not_an_object"}

    data = {k: v for k, v in flatten(payload).items() if v is not None}
    return {"declared": True, "ok": bool(data), "data": data, "error": None}


def resolvable(context: dict) -> set[str]:
    """Placeholder names `fill_template` can actually resolve for this context.

    Not simply "every registry name": a SYSTEM placeholder resolves only when the
    field it maps to HAS a value. `[company_phone]` is in the registry yet still
    reached a customer's ear because the context carried no company_phone — so an
    audit that trusts the registry alone reports a clean bill while the agent
    reads tokens aloud. DYNAMIC names are genuinely covered: the model supplies
    them, and `fill_template` degrades them to a safe Thai phrase if it doesn't.
    """
    from agents.prescript import DYNAMIC_PLACEHOLDERS, SYSTEM_PLACEHOLDERS

    have = {k for k, v in (context or {}).items() if v is not None}
    names = set(have) | set(DYNAMIC_PLACEHOLDERS)
    names |= {p for p, field in SYSTEM_PLACEHOLDERS.items() if field in have}
    return names


def audit_placeholders(templates: list[str], context: dict,
                       known: set[str] | None = None) -> list[str]:
    """Placeholders the catalog uses that nothing can fill — i.e. the tokens that
    WILL be spoken literally if those templates are chosen. `known` overrides the
    computed set (see `resolvable`) for a caller that knows better."""
    used: set[str] = set()
    for t in templates:
        # Drop {{if field}}…{{else}}…{{/if}} blocks first: their control words sit
        # inside doubled braces, so a plain {name} scan reports "else" as a missing
        # CRM field. Pass 1 of fill_template resolves these before any substitution.
        text = re.sub(r"\{\{[^{}]*\}\}", "", str(t or ""))
        for square, curly in _PLACEHOLDER_RE.findall(text):
            used.add(square or curly)
    used -= _GENDER_TOKENS
    return sorted(used - (known if known is not None else resolvable(context)))


__all__ = ["fetch_context", "audit_placeholders", "http_json", "substitute", "flatten"]
