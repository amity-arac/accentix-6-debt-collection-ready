"""SpecBackend: the FlowSpec-driven executor.

Two things happen in this app: **call an API, or reply.** Every tool a spec declares
is `impl: "http"` — a call to the deployment's own backend — so no business rule is
duplicated here where it could drift from the customer's system of record. The app
contributes only what a phone call needs and an API cannot know:

- **SpecGate** (flow/spec_gate.py) enforces the spec's declared per-call rules
  before the request goes out (call caps, tool ordering, args matching a prior
  commitment). These are conversation invariants, and they are also the error
  signals the RL policy was trained to read.
- The API's JSON response is returned **flat, as-is** — the observation the model
  sees is exactly what the backend said, including an `error` field.
- `session_init` (flow/session_init.py) fetches the call's context once, before
  turn 1, so every reply template is filled from live data.

`impl: "generic"` survives for a tool whose API does not exist yet: it validates
args against the declaration and returns a canned response. Any other impl is
rejected with a hint rather than silently handled, so a stale spec fails loudly.

The dispatch surface stays `dispatch(name, args) -> dict`, so callers are unchanged.
"""
from __future__ import annotations

def _gen_id(prefix: str) -> str:
    """Reference id for a recorded write. Two lines, vendored rather than importing
    `simulator.backend`, which drags the whole pre-flow backend in for this."""
    import secrets
    return f"{prefix}-{secrets.token_hex(3).upper()}"
from demo_v2.server.flow.flowspec import declared_tools
from demo_v2.server.flow.spec_gate import SpecGate


class SpecBackend:
    def __init__(self, customer_data: dict, spec: dict) -> None:
        self.spec = spec
        self._decls = declared_tools(spec)
        # The customer's row, verbatim. This used to be wrapped in CaseBackend — the
        # debt collector's backend — which carried a 4-digit KYC check, a verbal-
        # commitment state machine and that domain's result codes. In the flow path
        # every tool dispatches through the spec, so none of it ran; only its debt
        # assumptions stayed wired in. Holding the row directly keeps this executor
        # free of any one company's idea of what a call contains.
        # NOT a copy: the session hands in its own render context and `_merge_context`
        # writes each tool's answer back into it, so a re-checked balance is what the
        # next template speaks. Copying here silently restores the session-init snapshot.
        self.customer_data = customer_data

        # Per-conversation call log — the raw material for gating checks,
        # state-summary injection, and the GRPO reward.
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

            The only way a tool reaches the tenant's API. Four checks run before the call
            is made, in this order, and each returns instead of raising:

              unknown_tool           the spec does not declare it
              missing_required_args  an arg without `optional` arrived empty
              value_not_offered      `one_of_from` — a value the owning tool never returned
              SpecGate.check()       counts, ordering, argument matching (spec_gate.py)

            The return value is a flat dict either way. A rejection carries `error` and a
            Thai `message` beginning "Error: <code>" — the same shape the training
            environment used, so the policy reads a refusal it already knows. Wrapping the
            API's payload under a key would show the model a shape it has never seen.

        """
        decl = self._decls.get(name)
        if decl is None:
            result = {"error": "unknown_tool", "name": name,
                      "valid_tools": sorted(self._decls)}
        else:
            # spec-declared gating FIRST (call caps, tool ordering, arg-match). This is
            # what restores train/serve parity: the training env rejected these same
            # calls with an error the policy learned to read (e.g. "already recorded →
            # stop and reply"). Purely spec-driven — see flow/spec_gate.py.
            # An arg the spec declares WITHOUT `optional` has to arrive with a value.
            # Accepting `""` let a save go through that recorded nothing: the call was
            # stamped closed on the empty write, the retry that carried the real date
            # was refused as `call_already_closed`, and the sentence that speaks the
            # value went out with a hole in it. Checked before gating so the model is
            # told what is missing instead of being told the call is over.
            missing = [a for a, spec_arg in (decl.get("args") or {}).items()
                       if not (spec_arg or {}).get("optional")
                       and str((args or {}).get(a, "")).strip() == ""]
            if missing:
                err = {"error": "missing_required_args", "tool": name, "args": missing,
                       "message": f"Error: missing_required_args — {name} ต้องมีค่าของ "
                                  f"{', '.join(missing)}"}
                self.call_log.append({"tool": name, "args": args, "result": err})
                return err
            bad = self._not_offered(decl, args or {})
            if bad is not None:
                self.call_log.append({"tool": name, "args": args, "result": bad})
                return bad
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

        An arg can declare `one_of_from: {tool, field}` — "the valid values are what
        that tool last returned under that field". The agent booked appointments the
        clinic never offered: it invented a Thursday the doctor was not on duty, and
        it sent "" when the day the customer asked for was not in the list. Both were
        recorded as real bookings, because nothing compared the value against the set
        the API had already returned. Spec-driven, so any company that offers a
        choice set from its own API gets the same check with no code here.

        `required_when` covers the other half: an arg that is optional in general but
        mandatory for one value of a sibling arg (a reschedule needs a date, a
        confirmation does not) — `optional: true` alone let the empty reschedule
        through.
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
                        message=(f"Error: missing_required_args — {decl['name']} "
                                 f"ต้องมีค่าของ {a} เมื่อ {req['arg']}="
                                 f"{req['equals']}"))
            src = spec_arg.get("one_of_from") or {}
            if not (src and val):
                continue
            offered = self._last_result(src.get("tool"), src.get("field"))
            if offered and val not in offered:
                return self._reject(
                    "value_not_offered", tool=decl["name"], arg=a, got=val,
                    valid_values=offered,
                    message=(f"Error: value_not_offered — {a}={val!r} ไม่ได้อยู่ใน"
                             f"รายการที่ {src['tool']} คืนมา เลือกจาก: "
                             f"{', '.join(offered)}"))
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

        The API is the system of record, so its latest answer must be what the agent
        SAYS — not just something the model saw in the transcript. Without this the
        reply templates keep speaking the session-init snapshot: measured, a
        re-check returning 99999 was read aloud as the older 45000.

        Only successes merge. An error payload carries diagnostic keys (`got`,
        `hint`, `expected`) that are not facts about the customer, and letting them
        into the context would put them one template away from being spoken.
        Flattened the same way session_init flattens, so a nested response works
        without configuration.
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
