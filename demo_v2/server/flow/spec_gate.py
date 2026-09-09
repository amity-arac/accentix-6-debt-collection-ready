"""SpecGate — generic, spec-driven tool gating.

WHY THIS EXISTS (train/serve parity + generalization):

The model shipped here is a supervised fine-tune whose training trajectories were
recorded in a tool environment that REJECTS an invalid call with a specific error
token (`Error: outcome_already_recorded`, `Error: no_verbal_commitment`, …). Those
tokens are in the data it imitates, so the recovery — "I already did this → stop and
reply" — is a pattern it learned to produce after seeing one. A backend that
silently accepts every call never shows it the token, and it can loop: observed,
record_outcome called 8× in one turn until the tool-loop cap. That looping is a
measurement, and it is what this module prevents.

The gating is read FROM THE SPEC, per tool — no tool names, no company names, no
domain assumptions here:

    record_outcome:            gating.max_successful_calls: 1
    check_account_status:      gating.max_calls_per_conversation: 1
    payment_date:              gating.requires_prior: record_verbal_commitment
                               gating.args_must_match: ["amount","date","channel"]
    get_current_datetime:      gating.must_precede: record_verbal_commitment
    save_appointment  (AMT):   gating.required_at: end_of_call

A rejection is a dict carrying the training error token and the "Error: …" phrasing,
so the model sees the signal it knows:

    {"error": "outcome_already_recorded", "message": "Error: … — …",
     "recorded": False, "hint": "…"}

NOT enforced here, deliberately:
  * `after_event` / `required_before_state` — a live caller carries no reliable event
    tag, so enforcing on a guess would block legitimate calls. Prompt-level only.
  * `required_at: end_of_call` — an obligation before hanging up, not a reason to
    reject. Exposed through `pending_obligations()`.
"""
from __future__ import annotations

from typing import Any


def _num(v: Any) -> str | None:
    """Normalize a numeric-ish arg so "500" == 500 == 500.0 == "500.00" compare equal."""
    try:
        return f"{float(str(v).replace(',', '').strip()):g}"
    except (TypeError, ValueError):
        return None if v is None else str(v).strip()


def _ok(result: Any) -> bool:
    """Did a logged tool call succeed? (mirrors SpecBackend.successful_calls)"""
    if not isinstance(result, dict):
        return bool(result)
    return not result.get("error") and result.get("recorded") is not False


class SpecGate:
    """Enforces a FlowSpec's declared per-tool gating against the live call log."""

    def __init__(self, spec: dict) -> None:
        self._spec = spec or {}
        decls = (self._spec.get("tools") or {}).get("declarations") or []
        self._gating: dict[str, dict] = {
            d["name"]: (d.get("gating") or {}) for d in decls if d.get("name")
        }

    # ---------- helpers over the call log ----------
    @staticmethod
    def _calls(call_log: list[dict], name: str) -> list[dict]:
        return [c for c in call_log if c.get("tool") == name]

    @classmethod
    def _successful(cls, call_log: list[dict], name: str) -> list[dict]:
        return [c for c in cls._calls(call_log, name) if _ok(c.get("result"))]

    def _closed_by(self, call_log: list[dict]) -> str | None:
        """The tool that already closed this call, if any. Spec-driven: a tool declared
        `gating.required_at: end_of_call` IS the end of the call by definition, so once
        it succeeds nothing further may be written (observed failure: the model recorded
        `refused`, then recorded a 4500 commitment the customer never agreed to)."""
        for name, g in self._gating.items():
            if g.get("required_at") == "end_of_call" and self._successful(call_log, name):
                return name
        return None

    # ---------- the gate ----------
    def check(self, name: str, args: dict, call_log: list[dict]) -> dict | None:
        """Return a rejection dict if this call violates the spec's declared gating,
        else None (caller proceeds to the side-effect). Tools with no declared gating
        are never blocked — absence of a rule means "no rule", not "deny"."""
        g = self._gating.get(name) or {}
        args = args or {}

        # 0. the call is already closed → no tool may run after it (`reply` never
        # reaches dispatch, so the closing utterance is unaffected).
        closer = self._closed_by(call_log)
        if closer is not None:
            return self._reject(
                "call_already_closed",
                f"สายนี้ปิดแล้วด้วย {closer} — ห้ามเรียกเครื่องมือเพิ่ม ให้ปิดสายด้วยบทพูดปิดเท่านั้น",
                closed_by=closer,
            )

        # 1. call-count caps -------------------------------------------------
        # max_successful_calls counts only calls that actually took effect (a rejected
        # attempt must not burn the quota); max_calls_per_conversation counts attempts.
        cap_ok = g.get("max_successful_calls")
        if isinstance(cap_ok, int) and len(self._successful(call_log, name)) >= cap_ok:
            return self._reject(
                f"{name}_already_recorded" if cap_ok == 1 else f"{name}_call_limit_reached",
                f"เรียก {name} ได้ {cap_ok} ครั้งต่อสาย (ทำไปแล้ว) — "
                "ขั้นตอนนี้เสร็จแล้ว ให้ตอบลูกค้าต่อ ห้ามเรียกซ้ำ",
            )
        cap_all = g.get("max_calls_per_conversation")
        if isinstance(cap_all, int) and len(self._calls(call_log, name)) >= cap_all:
            return self._reject(
                "already_checked" if cap_all == 1 else f"{name}_call_limit_reached",
                f"เรียก {name} ได้ {cap_all} ครั้งต่อสาย (ทำไปแล้ว) — ใช้ข้อมูลที่ได้มาแล้ว",
            )

        # 2. ordering: this tool needs another tool to have run first --------
        prior = g.get("requires_prior")
        prereqs = [prior] if isinstance(prior, str) else list(prior or [])
        for req in prereqs:
            if req and not self._successful(call_log, req):
                return self._reject(
                    f"no_{req}",
                    f"ต้องเรียก {req} ให้สำเร็จก่อน แล้วจึงเรียก {name}",
                )
        # the mirror form: another tool declares must_precede: <this tool>
        for other, og in self._gating.items():
            mp = og.get("must_precede")
            targets = [mp] if isinstance(mp, str) else list(mp or [])
            if name in targets and not self._successful(call_log, other):
                return self._reject(
                    f"call_{other}_first",
                    f"ต้องเรียก {other} ก่อนเรียก {name} (ตามลำดับที่ spec กำหนด)",
                )

        # 3. args must match what a prior tool recorded ----------------------
        # e.g. payment_date.args_must_match_commitment → its amount/date/channel must
        # equal the record_verbal_commitment call that gated it (spec decides which).
        # Which arguments have to agree is the tenant's business, so the spec names
        # them. The list used to be ("amount","date","channel") in this file — a debt
        # collector's fields, which a clinic could not use for its own booking.
        match_args = g.get("args_must_match")
        if match_args and prereqs:
            src = next((p for p in prereqs if self._successful(call_log, p)), None)
            if src:
                prior_args = self._successful(call_log, src)[-1].get("args") or {}
                mismatch = []
                for key in match_args:
                    want, got = prior_args.get(key), args.get(key)
                    if want in (None, "") or got in (None, ""):
                        continue      # only compare what BOTH sides actually supplied
                    if _num(want) != _num(got):
                        mismatch.append(key)
                if mismatch:
                    return self._reject(
                        "commitment_mismatch",
                        f"{', '.join(mismatch)} ไม่ตรงกับที่บันทึกไว้ใน {src} — "
                        "ต้องใช้ค่าเดียวกับที่ลูกค้าตกลง",
                        expected={k: prior_args.get(k) for k in match_args
                                  if prior_args.get(k)},
                    )
        return None

    def pending_obligations(self, call_log: list[dict]) -> list[str]:
        """Tools the spec says must happen before the call ends (`required_at:
        end_of_call`) that have not succeeded yet — for a closing check / UI warning.
        Spec-driven: works for AEON's record_outcome and AMT's save_appointment alike."""
        return [
            name for name, g in self._gating.items()
            if g.get("required_at") == "end_of_call" and not self._successful(call_log, name)
        ]

    @staticmethod
    def _reject(code: str, thai_hint: str, **extra: Any) -> dict:
        """Rejection payload carrying the training-time signal (`Error: <code>`) inside
        the dict shape the serving pipeline uses."""
        return {
            "error": code,
            "message": f"Error: {code} — {thai_hint}",
            "recorded": False,
            "hint": thai_hint,
            **extra,
        }


__all__ = ["SpecGate"]
