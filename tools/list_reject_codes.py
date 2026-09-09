#!/usr/bin/env python3
"""Every code the app can refuse with, read out of the source.

Three documents each hand-typed this list and each got it wrong in a different
way. Run this instead of typing it again:

    PYTHONPATH=. python3 tools/list_reject_codes.py
"""
import pathlib, re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def reply_gate() -> list[str]:
    """Codes raised inside the `reply` branch of `_aiter_run` — before speaking."""
    src = (ROOT / "demo_v2/server/sessions.py").read_text(encoding="utf-8").split("\n")
    a = next(i for i, l in enumerate(src) if 'if fn["name"] == "reply":' in l)
    b = next(i for i, l in enumerate(src) if i > a and "_emit_filler(fn" in l)
    out: list[str] = []
    for i in range(a, b):
        # ด่านบางตัวเลือกรหัสไว้ในตัวแปรก่อน (`_reason = "off_flow_beat"`) แล้วค่อยส่ง
        # `"reason": _reason` — จับเฉพาะรูปแบบตรงจะได้ไม่ครบ และรายการนี้คือแหล่งอ้างอิง
        # ที่เอกสารบอกให้เชื่อแทนการพิมพ์เอง
        for m in re.finditer(r'"reason": "([a-z_]+)"|_reason = "([a-z_]+)"', src[i]):
            code = m.group(1) or m.group(2)
            if code not in out:
                out.append(code)
    return out


def tool_gate() -> tuple[list[str], list[str]]:
    """Codes raised before a tool runs — literal ones, and ones built from a
    tool's name (`no_<tool>`, `call_<tool>_first`, `<tool>_already_recorded`).

    Scanning every code-shaped f-string, not just the first argument of each
    `_reject(` call: one of them is a conditional expression carrying two codes
    (`{name}_already_recorded` / `{name}_call_limit_reached`) and matching the
    call site only ever found the first.
    """
    fixed, templated = set(), set()
    for name in ("flow/spec_backend.py", "flow/spec_gate.py"):
        src = (ROOT / "demo_v2/server" / name).read_text(encoding="utf-8")
        fixed |= set(re.findall(r'"error": "([a-z_]+)"', src))
        fixed |= set(re.findall(r'_reject\(\s*\n?\s*"([a-z_]+)"', src))
        templated |= set(re.findall(r'f"((?:\{[a-z_]+\}|[a-z_])+)"', src))
    # `{name}` is the tool being called; `{req}` / `{other}` the one it depends on
    # `{name}` is the tool being called, `{req}`/`{other}` one it depends on,
    # `{arg}` an argument of it (that one comes from the `generic` stub path).
    templated = {t.replace("{name}", "<tool>").replace("{req}", "<tool>")
                  .replace("{other}", "<tool>").replace("{arg}", "<arg>")
                 for t in templated}
    templated -= fixed
    return sorted(fixed), sorted(templated)


if __name__ == "__main__":
    rg = reply_gate()
    fixed, templated = tool_gate()
    print(f"before speaking — reply gate ({len(rg)}), in the order the code checks them:")
    for i, c in enumerate(rg, 1):
        print(f"  {i}. {c}")
    print(f"\nbefore a tool runs — tool gate ({len(fixed)} fixed + {len(templated)} name-derived):")
    for c in fixed:
        print(f"  · {c}")
    for c in templated:
        print(f"  · {c}   (built from the tool's name)")
