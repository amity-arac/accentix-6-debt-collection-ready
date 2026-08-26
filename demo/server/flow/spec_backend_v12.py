# VENDORED from R&D repo (accentix-6-debt-collector) for sft_v12 — DO NOT hand-edit.
# Logic must stay byte-identical to src/aax6/core/spec_backend.py. Only imports are adapted.
# v11.2/sft_v11 path does NOT use this module (it keeps flow/flowspec*.py + spec_backend.py).

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
from demo.server.flow.flowspec_v12 import declared_tools, flow_meta


class SpecBackend:
    def __init__(self, customer_data: dict, spec: dict) -> None:
        self.spec = spec
        self._decls = declared_tools(spec)
        self._meta = flow_meta(spec)
        self._case = CaseBackend(
            customer_data,
            v6_active=True,
            require_kyc=spec["tools"].get("require_kyc", True),
        )
        # Per-conversation call log — the raw material for gating checks,
        # state-summary injection, and the GRPO reward.
        self.call_log: list[dict] = []
        # Reply-side state (the backend never sees replies — the session layer
        # reports them via note_reply so state_summary can count pay-asks and
        # disclosure).
        self._pay_asks = 0
        self._disclosed = False

    @property
    def customer_data(self) -> dict:
        return self._case.customer_data

    def dispatch(self, name: str, args: dict) -> dict:
        decl = self._decls.get(name)
        if decl is None:
            result = {"error": "unknown_tool", "name": name,
                      "valid_tools": sorted(self._decls)}
        elif (decl.get("impl") == "record_outcome"
              and decl.get("gating", {}).get("max_successful_calls") == 1
              and args.get("result") in self._outcome_results_recorded()):
            # spec gating: ผลสายบันทึกครั้งเดียว — result ซ้ำเดิมถูก reject
            # (corrective re-stamp ที่ result ต่าง เช่น refused→ptp ยังผ่านได้)
            result = {
                "recorded": False,
                "reason": "outcome_already_recorded",
                "hint": (f"ผลสาย '{args.get('result')}' ถูกบันทึกไปแล้ว — ห้ามบันทึกซ้ำ "
                         "กล่าวปิดสายด้วย reply ได้เลย (บันทึกใหม่ได้เฉพาะกรณีผลเปลี่ยน "
                         "เช่น ลูกค้ากลับใจยอมชำระ → ptp)"),
            }
        else:
            impl = decl.get("impl", "generic")
            if impl == "generic":
                result = self._dispatch_generic(decl, args or {})
            elif impl == "get_current_datetime":
                # flow-mode ขยาย lookup: วันที่ของทุก weekday ล่วงหน้า 2 สัปดาห์
                # + สิ้นเดือน — model copy exact string ได้ (เสาร์หน้า/สิ้นเดือน)
                # โดยไม่ต้องคำนวณเอง (CaseBackend เดิมไม่แตะ — กัน demo v10/v11 เพี้ยน)
                result = self._extended_datetime_table()
            else:
                arg_map = decl.get("arg_map", {})
                mapped = {arg_map.get(k, k): v for k, v in (args or {}).items()}
                # arg ที่ declare optional + default: เติมให้ก่อนส่ง CaseBackend
                # (v11.2: channel optional — จ่ายวันนี้ไม่ถามช่องทาง → default "other")
                for arg, meta in decl.get("args", {}).items():
                    key = arg_map.get(arg, arg)
                    if meta.get("default") is not None and not mapped.get(key):
                        mapped[key] = meta["default"]
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

    def note_reply(self, fine_states) -> None:
        """Session layer reports each sent reply's template groups so the
        tracker can count pay-asks / disclosure (moves the counting burden
        off the model — see state_summary)."""
        fss = set(fine_states)
        if self._meta["disclose_fs"] and self._meta["disclose_fs"] in fss:
            self._disclosed = True
            self._pay_asks += 1  # disclose_ask turn = คำขอชำระครั้งที่ 1
        elif fss & set(self._meta["pay_ask_fs"]):
            self._pay_asks += 1

    @staticmethod
    def _extended_datetime_table() -> dict:
        import calendar

        from simulator import datetime_utils
        table = datetime_utils.datetime_lookup_table()
        sim = datetime_utils.simulation_date()
        this_week, next_week = {}, {}
        for i in range(1, 15):
            iso = datetime_utils.relative_iso(i)
            wd = iso.split("(")[1].rstrip(")").lower()
            if wd not in this_week:
                this_week[wd] = iso
            elif wd not in next_week:
                next_week[wd] = iso
        table["upcoming"] = this_week      # weekday → วันที่ครั้งถัดไป (ภายใน 7 วัน)
        table["following_week"] = next_week
        eom = calendar.monthrange(sim.year, sim.month)[1] - sim.day
        if eom > 0:
            table["end_of_month"] = datetime_utils.relative_iso(eom)
        return table

    def _outcome_results_recorded(self) -> set:
        return {
            c["args"].get("result") for c in self.call_log
            if self._meta["tool_impls"].get(c["tool"]) == "record_outcome"
            and not c["result"].get("error") and c["result"].get("recorded") is not False
        }

    def outcome_stamped(self) -> bool:
        return any(
            self._meta["tool_impls"].get(c["tool"]) == "record_outcome"
            and not c["result"].get("error") and c["result"].get("recorded") is not False
            for c in self.call_log
        )

    # --- reply-gate (constraint enforce layer "session") -------------------

    # `SENSITIVE_SLOTS`, `verification_reached()` and `blocked_reply_ids()` were
    # removed with the reply-gate that called them: a hardcoded list of one domain's
    # slots, and an inference about what "verified" means, are policy — and the app
    # supplies mechanisms, not policy.

    def auto_outcome(self) -> dict | None:
        """Deterministic wrap-up: ถ้าจบสายแล้ว model ยังไม่ stamp ผลสาย
        session เรียกอันนี้เพื่อ derive ผลจาก call_log (logic เดียวกับ
        real_to_flow.repair_missing_outcome) แล้ว dispatch ให้เลย —
        การันตี CRM completeness 100% เหนือพฤติกรรม model (ชั้นเดียวกับ
        reply-gate/dup-gate). คืน result dict หรือ None ถ้า stamp แล้ว"""
        if self.outcome_stamped():
            return None
        names = sorted(n for n, i in self._meta["tool_impls"].items()
                       if i == "record_outcome")
        if not names:
            return None
        paid = any(self._meta["tool_impls"].get(c["tool"]) == "payment_date"
                   and c["result"].get("recorded") is True for c in self.call_log)
        cb = any(self._meta["tool_impls"].get(c["tool"]) == "callback_datetime"
                 and c["result"].get("recorded") is True for c in self.call_log)
        if paid:
            args = {"result": "ptp", "reason": "ptp", "remark": ""}
        elif cb:
            args = {"result": "tcb", "reason": "callback", "remark": ""}
        else:
            args = {"result": "reached", "reason": "", "remark": ""}
        return self.dispatch(names[0], args)

    def state_summary(self) -> str:
        """Compact deterministic tracker line, appended to every customer turn
        (both at data-gen and at serve — must stay byte-identical). Uses «»
        so it can never collide with [placeholder] tokens."""
        quota = self._meta["max_pay_asks"]
        quota_s = f"{self._pay_asks}/{quota}" if quota is not None else str(self._pay_asks)
        return (f"«สถานะ: ขอชำระ {quota_s}"
                f" · แจ้งยอด:{'แล้ว' if self._disclosed else 'ยัง'}"
                f" · ผลสาย:{'บันทึกแล้ว' if self.outcome_stamped() else 'ยังไม่บันทึก'}»")


__all__ = ["SpecBackend"]
