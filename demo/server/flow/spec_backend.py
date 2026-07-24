"""SpecBackend: FlowSpec-driven backend interpreter (Step 2b).

Replaces "one hand-coded backend per flow" with one executor that reads a
FlowSpec's tool declarations. Each declaration binds a model-facing tool
`name` to an `impl`:

- builtin impls delegate to the corresponding `CaseBackend` method (all the
  battle-tested behavior — KYC, 3-Element pair, date validation, overdue-PTP,
  busy/voicemail — stays in one place), with optional `arg_map` renaming so
  synthetic flows can expose the same behavior under different tool/arg names;
- `impl: "generic"` executes declaratively: required/enum arg validation from
  the declaration's `args` schema, `reject_if` CRM conditions, and a canned
  `response` (+ generated id) — enough for CRM-ish read/write tools no code
  has ever seen.

The dispatch surface is identical to CaseBackend (`dispatch(name, args) ->
dict`), so the simulator/communicator can swap backends without changes.
"""
from __future__ import annotations

from simulator.backend import CaseBackend, _gen_id
from demo.server.flow.flowspec import declared_tools


class SpecBackend:
    def __init__(self, customer_data: dict, spec: dict) -> None:
        self.spec = spec
        self._decls = declared_tools(spec)
        self._case = CaseBackend(
            customer_data,
            v6_active=True,
            require_kyc=spec["tools"].get("require_kyc", True),
        )
        # Per-conversation call log — the raw material for gating checks,
        # state-summary injection, and the GRPO reward.
        self.call_log: list[dict] = []

    @property
    def customer_data(self) -> dict:
        return self._case.customer_data

    def dispatch(self, name: str, args: dict) -> dict:
        decl = self._decls.get(name)
        if decl is None:
            result = {"error": "unknown_tool", "name": name,
                      "valid_tools": sorted(self._decls)}
        else:
            impl = decl.get("impl", "generic")
            if impl == "generic":
                result = self._dispatch_generic(decl, args or {})
            else:
                arg_map = decl.get("arg_map", {})
                mapped = {arg_map.get(k, k): v for k, v in (args or {}).items()}
                result = self._case.dispatch(impl, mapped)
        self.call_log.append({"tool": name, "args": args, "result": result})
        return result

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
