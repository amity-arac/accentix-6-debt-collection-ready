"""SpecBackend: the FlowSpec-driven executor.

Two things happen in this app: **call an API, or reply.** Every tool a spec declares
is `impl: "http"` — a call to the deployment's own backend — so no business rule is
duplicated here where it could drift from the customer's system of record. The app
contributes only what a phone call needs and an API cannot know:

- **SpecGate** (flow/spec_gate.py) enforces the spec's declared per-call rules
  before the request goes out (call caps, ordering, args matching a prior
  commitment). They are conversation invariants, and also the error signals the
  model was trained to read.
- The API's JSON response is returned **flat, as-is**, including an `error` field:
  the observation is exactly what the backend said.
- `session_init` (flow/session_init.py) fetches the call's context once, before
  turn 1, so every reply template is filled from live data.

`impl: "generic"` survives for a tool whose API does not exist yet. Any other impl
is rejected with a hint, so a stale spec fails loudly.
"""
from __future__ import annotations
from demo_v2.lib import lang as _L

def _gen_id(prefix: str) -> str:
    """Reference id for a recorded write. Two lines, vendored rather than importing
    `simulator.backend`, which drags the whole pre-flow backend in for this."""
    import secrets
    return f"{prefix}-{secrets.token_hex(3).upper()}"
from demo_v2.server.flow.flowspec import declared_tools
from demo_v2.lib.datetime_utils import resolve_spoken_date
from demo_v2.server.flow.spec_gate import SpecGate

import re as _re

_DATE_ISO_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}")


class SpecBackend:
    def __init__(self, customer_data: dict, spec: dict) -> None:
        self.spec = spec
        self._decls = declared_tools(spec)
        # The customer's row, verbatim — no domain wrapper, so this executor carries no
        # company's idea of what a call contains. NOT a copy: the session hands in its
        # own render context and `_merge_context` writes each answer back into it, so a
        # re-checked balance is what the next template speaks.
        self.customer_data = customer_data

        # Per-conversation call log — the raw material for gating checks, state-summary
        # injection, and (on the training side, not here) trajectory scoring.
        self.call_log: list[dict] = []
        # spec-driven gate (call caps / ordering / arg-match) — reads the spec's own
        # tools[].gating + constraints, so any company's spec is enforced with no
        # per-company code. See flow/spec_gate.py for why this matters (train parity).
        self._gate = SpecGate(spec)

    def pending_obligations(self) -> list[str]:
        """Tools the spec requires before the call ends that haven't succeeded yet."""
        return self._gate.pending_obligations(self.call_log)

    def dispatch(self, name: str, args: dict) -> dict:
        """Run one tool call and return what the model should see.

        The only way a tool reaches the tenant's API. Four checks run first, in this order,
        each returning rather than raising:

          unknown_tool           the spec does not declare it
          missing_required_args  an arg without `optional` arrived empty
          value_not_offered      `one_of_from` — a value the owning tool never returned
          SpecGate.check()       counts, ordering, argument matching (spec_gate.py)

        Either way the return is a flat dict; a rejection carries `error` and a message
        beginning "Error: <code>", the shape the model saw in training. Wrapping the API's
        payload under a key would show it a shape it has never seen.
        """
        decl = self._decls.get(name)
        if decl is None:
            result = {"error": "unknown_tool", "name": name,
                      "valid_tools": sorted(self._decls)}
        else:
            # An argument declared with a date format also accepts the words the
            # customer said ("tomorrow", "end of the month") and the code turns them
            # into ISO. The model only copies what it heard; the calendar arithmetic
            # stays here (measured: model-computed dates 62% right, copied from a
            # table 92%). A value already in ISO is untouched.
            args = dict(args or {})
            for _a, _meta in (decl.get("args") or {}).items():
                if not str((_meta or {}).get("format", "")).startswith("YYYY-MM-DD"):
                    continue
                _v = str(args.get(_a, "") or "").strip()
                if not _v or _DATE_ISO_RE.match(_v):
                    continue
                _hit = resolve_spoken_date(_v)
                if _hit is not None:
                    args[_a] = _hit.isoformat()
            # A required arg must arrive with a value: accepting "" let a save record
            # nothing, the call was stamped closed on that empty write, and the retry
            # carrying the real date was refused `call_already_closed`. Checked before
            # the gate so the model is told what is missing, not that the call is over.
            missing = [a for a, spec_arg in (decl.get("args") or {}).items()
                       if not (spec_arg or {}).get("optional")
                       and str((args or {}).get(a, "")).strip() == ""]
            if missing:
                err = {"error": "missing_required_args", "tool": name, "args": missing,
                       "message": _L.tool_err("missing_required_args", tool=name,
                                              args=", ".join(missing))}
                self.call_log.append({"tool": name, "args": args, "result": err})
                return err
            bad = self._not_offered(decl, args or {})
            if bad is not None:
                self.call_log.append({"tool": name, "args": args, "result": bad})
                return bad
            # Spec-declared gating (call caps, ordering, arg-match) — see spec_gate.py
            # for why the rejection shape has to stay as the model saw it in training.
            gate_err = self._gate.check(name, args or {}, self.call_log)
            if gate_err is not None:
                self.call_log.append({"tool": name, "args": args, "result": gate_err})
                return gate_err
            # TWO things happen in this app: call an API, or reply. Every tool a
            # spec declares is an HTTP call to the deployment's own backend — no
            # business logic lives here, so nothing can silently disagree with the
            # customer's system of record. `generic` remains only for a tool with a
            # canned answer (a spec being drafted before its API exists).
            impl = decl.get("impl", "http")
            if impl == "http":
                result = self._dispatch_http(decl, args or {})
            elif impl == "generic":
                result = self._dispatch_generic(decl, args or {})
            else:
                result = {"error": "impl_not_supported", "impl": impl,
                          "hint": f'tool {name}: use impl "http" with a "url" '
                                  f'(or "generic" for a canned stub)'}
        self.call_log.append({"tool": name, "args": args, "result": result})
        self._merge_context(result)
        return result

    def _not_offered(self, decl: dict, args: dict) -> dict | None:
        """Reject an arg whose value was never offered by the tool that owns the set.

        An arg can declare `one_of_from: {tool, field}` — "valid values are what that tool
        last returned under that field". Without it the agent booked a Thursday the doctor
        was not on duty, and sent "" when the requested day was not in the list; both were
        recorded as real bookings. `required_when` covers the other half: an arg optional in
        general but mandatory for one value of a sibling (a reschedule needs a date, a
        confirmation does not) — `optional: true` alone let the empty reschedule through.
        """
        for a, spec_arg in (decl.get("args") or {}).items():
            spec_arg = spec_arg or {}
            val = str(args.get(a, "") or "").strip()
            req = spec_arg.get("required_when") or {}
            if req and not val:
                sib = str(args.get(req.get("arg"), "") or "").strip()
                if sib == req.get("equals"):
                    return self._reject(
                        "missing_required_args", tool=decl["name"], args=[a],
                        message=("Error: missing_required_args — "
                                 + decl["name"] + " "
                                 + _L.tool_err("value_required_when", arg=a,
                                               when=req["arg"])
                                 + str(req["equals"])))
            src = spec_arg.get("one_of_from") or {}
            if not (src and val):
                continue
            offered = self._last_result(src.get("tool"), src.get("field"))
            if offered and val not in offered:
                return self._reject(
                    "value_not_offered", tool=decl["name"], arg=a, got=val,
                    valid_values=offered,
                    message=(_L.tool_err("value_not_offered", arg=a, val=val)
                             + _L.tool_err("value_not_offered2", tool=src["tool"])
                             + ", ".join(offered)))
        return None

    def _last_result(self, tool: str | None, field: str | None) -> list[str]:
        """Values that `tool` returned under `field` on its most recent success."""
        if not (tool and field):
            return []
        for rec in reversed(self.call_log):
            if rec.get("tool") != tool:
                continue
            res = rec.get("result")
            if isinstance(res, dict) and not res.get("error"):
                got = res.get(field)
                return [str(x) for x in got] if isinstance(got, list) else []
        return []

    @staticmethod
    def _reject(code: str, **extra) -> dict:
        return {"error": code, "recorded": False, **extra}

    def _merge_context(self, result: dict) -> None:
        """A successful tool response updates the render context, in place.

        The API is the system of record, so its latest answer must be what the agent SAYS,
        not just something the model read in the transcript — without this a re-check
        returning 99999 was still spoken as the older 45000.

        Only successes merge: an error payload carries diagnostic keys that are not facts
        about the customer, and one template away from being spoken. Flattened the same way
        session_init flattens, so a nested response needs no configuration.
        """
        if not isinstance(result, dict) or result.get("error"):
            return
        from demo_v2.server.flow.session_init import flatten

        fresh = {k: v for k, v in flatten(result).items() if v is not None}
        # Bookkeeping about the call itself, not facts about the customer.
        for noise in ("recorded", "verified", "ok", "success"):
            fresh.pop(noise, None)
        self.customer_data.update(fresh)

    def _dispatch_http(self, decl: dict, args: dict) -> dict:
        """User-defined webhook tool: call decl['url'] (POST by default) with
        decl['body'] as the request body, substituting {tokens} from customer_data +
        the call's own args. The parsed JSON response becomes the observation. Any
        transport/HTTP error comes back as {error: ...} so the agent sees a real
        failure branch rather than a crashed turn.

        Shares the HTTP primitive with the session-init fetch (flow/session_init.py)
        so a spec's webhook tools and its context call behave identically —
        same token substitution, same timeout guard, same error shape."""
        from demo_v2.server.flow.session_init import http_json, substitute

        url = decl.get("url")
        if not url:
            return {"error": "http_no_url", "name": decl.get("name")}
        import os as _os
        ctx = {**self.customer_data, **(args or {})}
        ctx.setdefault("API_BASE", _os.getenv("AAX6_API_BASE", "http://127.0.0.1:3001"))
        # The tenant's own code, so a spec can write `{API_BASE}/{company}/<tool>` and
        # stay correct after it is copied. The Builder clones a base spec verbatim, so
        # without this every company it created kept the base's hardcoded path segment
        # and posted its writes to the template tenant.
        ctx.setdefault("company", self.spec.get("company"))
        # Default body = the call itself: the tool's own name, the args the model
        # supplied, and the identifiers an API needs to find the record. So a spec
        # declares nothing but `url` and the API receives everything — a `body`
        # template stays available for an endpoint with a fixed contract.
        body = decl.get("body")
        if body in (None, ""):
            body = {
                "tool": decl.get("name"),
                "args": args or {},
                "ref": {k: self.customer_data.get(k)
                        for k in ("case_id", "msisdn", "customer_phone", "last_4_digits")
                        if self.customer_data.get(k) is not None},
            }
        payload, error = http_json(
            substitute(url, ctx),
            method=str(decl.get("method") or "POST"),
            headers=substitute(decl.get("headers") or {}, ctx),
            body=substitute(body, ctx),
            timeout=float(decl.get("timeout", 8)),
        )
        if error:
            return {"error": "http_error", "detail": error}
        # The API's payload IS the observation — returned flat, not wrapped. The
        # policy was trained on flat tool results (`{"status": …}`, `{"error":
        # "date_format_invalid"}`), so nesting it under a "response" key would show
        # the model a shape it has never seen. An `error` field in the payload
        # therefore reads as the same rejection signal it learned from.
        if isinstance(payload, dict):
            return payload
        return {"recorded": True, "response": payload}

    def _dispatch_generic(self, decl: dict, args: dict) -> dict:
        for arg, meta in decl.get("args", {}).items():
            val = args.get(arg)
            if not meta.get("optional") and val in (None, ""):
                return {"recorded": False, "reason": "missing_required_arg", "missing": arg}
            if meta.get("enum") and val is not None and val not in meta["enum"]:
                return {"recorded": False, "reason": f"{arg}_invalid",
                        "valid": sorted(meta["enum"])}
        for cond in decl.get("reject_if", []):
            crm = cond.get("crm", {})
            if crm and all(self.customer_data.get(k) == v for k, v in crm.items()):
                return {"recorded": False, "reason": cond.get("reason", "rejected")}
        response = dict(decl.get("response", {"recorded": True}))
        if decl.get("id_prefix"):
            response["id"] = _gen_id(decl["id_prefix"])
        return response

    # --- conversation-state summary (for prompt injection / reward) ---

    def successful_calls(self, name: str) -> int:
        return sum(
            1 for c in self.call_log
            if c["tool"] == name and not c["result"].get("error")
            and c["result"].get("recorded") is not False
        )


__all__ = ["SpecBackend"]
