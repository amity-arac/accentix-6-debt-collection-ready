"""`outcome` ต้องเรียก tool ปิดสายของ tenant ได้ ไม่ใช่ของงานทวงหนี้ตัวเดียว

เดิมทั้งระบบ hardcode ว่าผลสายอยู่ใน arg ชื่อ `result` และเรนเดอร์เป็น
`closer("<result>", reason: …)` ⇒ AMT ที่ปิดสายด้วย `save_appointment(status, new_slot)`
ถูกพรอมป์สอนให้ส่งค่าไปที่ arg ชื่อ `reason` ซึ่ง tool นั้นไม่มี และ `new_slot` (ตัว
บังคับเมื่อเลื่อนนัด) ไม่เคยปรากฏในบรรทัดปิดสายเลย

รูปใหม่: `outcome.args` = argument ของ tool ปิดสาย เขียนด้วยชื่อ arg จริง · รูปเดิม
(`result`/`reason`) ยังโหลดได้ ถูก normalize เป็น args ให้เอง
"""
import copy
import glob
import json
import pathlib

import pytest

from demo_v2.server.flow.flowspec import (closing_tool, derive_outcomes, outcome_args,
                                          outcome_key, outcome_result, resolve_catalog,
                                          validate_flow_spec, validate_strict)
from demo_v2.server.flow.flowspec_render import render_instruction
from demo_v2.server.sessions import _closing_beats

FLOWS = sorted(glob.glob(str(pathlib.Path(__file__).resolve().parents[1] /
                             "data" / "flows" / "*.company.json")))
SPECS = {pathlib.Path(f).name.replace(".company.json", ""):
         json.load(open(f, encoding="utf-8")) for f in FLOWS}


def _spec(name):
    s = dict(SPECS[name]); s.setdefault("company", name); return s


@pytest.mark.parametrize("name", sorted(SPECS))
def test_outcome_key_is_the_closing_tools_own_first_arg(name):
    spec = _spec(name)
    d = closing_tool(spec)
    assert d, f"{name}: ไม่มี tool ปิดสาย"
    assert outcome_key(spec) == next(iter(d["args"])), \
        f"{name}: ผลสายต้องเป็น arg ตัวแรกของ {d['name']} ไม่ใช่ชื่อที่ hardcode ไว้"


@pytest.mark.parametrize("name", sorted(SPECS))
def test_every_outcome_calls_the_closing_tool_with_declared_args(name):
    """ทุก outcome ต้องส่ง arg ที่ tool ประกาศไว้ และค่าอยู่ใน enum ของ arg นั้น"""
    spec = _spec(name)
    declared = (closing_tool(spec) or {}).get("args") or {}
    for st in spec["states"]:
        if not st.get("outcome"):
            continue
        args = outcome_args(spec, st["outcome"])
        assert args, f"{name}/{st['id']}: outcome ไม่มี args ให้ส่ง"
        for k, v in args.items():
            assert k in declared, f"{name}/{st['id']}: `{k}` ไม่ใช่ arg ของ tool ปิดสาย"
            enum = (declared[k] or {}).get("enum")
            assert not enum or v in enum, f"{name}/{st['id']}: {k}={v!r} นอก enum"


@pytest.mark.parametrize("name", sorted(SPECS))
def test_the_prompt_names_the_real_argument(name):
    """บรรทัด "จบสาย:" ต้องเขียนชื่อ arg จริง — AMT ต้องเห็น status= ไม่ใช่ reason="""
    spec = _spec(name)
    key = outcome_key(spec)
    lines = [l for l in render_instruction(spec).split("\n") if "จบสาย: " in l]
    assert lines, f"{name}: ไม่มีบรรทัดปิดสายในพรอมป์"
    for l in lines:
        assert f"{key}=" in l, f"{name}: บรรทัดนี้ไม่ได้เรียก arg ด้วยชื่อจริง → {l.strip()}"


@pytest.mark.parametrize("name", sorted(SPECS))
def test_closing_gate_still_maps_result_to_its_own_farewell(name):
    """ด่านคำลาต้องยังจับคู่ผล → beat ปิดสายของผลนั้นได้ (ไม่ถอยไปเป็น "ทุก beat")"""
    spec = _spec(name)
    results = sorted(derive_outcomes(spec))
    assert results, f"{name}: ไม่มีผลลัพธ์ให้จับคู่"
    every = _closing_beats(spec, None)
    narrowed = [r for r in results if _closing_beats(spec, r) < every]
    assert narrowed, f"{name}: ไม่มีผลไหนจำกัด beat ปิดสายได้เลย — ด่านคำลาจะไม่กัด"


def test_the_old_shape_still_loads_and_means_the_same():
    """ไฟล์ที่เขียนรูปเดิมไว้ ต้องได้ผลเท่ากับรูปใหม่ทุกจุด"""
    spec = _spec("AMT")
    st = next(x for x in spec["states"] if x.get("outcome"))
    new = copy.deepcopy(st["outcome"])
    old = {k: v for k, v in new.items() if k != "args"}
    old["result"] = new["args"][outcome_key(spec)]          # ← รูปเดิม
    assert outcome_result(spec, old) == outcome_result(spec, new)
    assert outcome_args(spec, old) == {outcome_key(spec): new["args"][outcome_key(spec)]}


def test_an_undeclared_arg_is_refused_at_upload_time():
    spec = copy.deepcopy(_spec("AMT"))
    cat = resolve_catalog(spec)
    spec["states"][-1]["outcome"] = {"args": {"not_an_arg": "x"}}
    errs = validate_strict(spec, cat) + validate_flow_spec(spec, cat)[0]
    assert any("not_an_arg" in e for e in errs), errs


def test_a_value_outside_the_enum_is_refused_at_upload_time():
    spec = copy.deepcopy(_spec("AMT"))
    cat = resolve_catalog(spec)
    key = outcome_key(spec)
    spec["states"][-1]["outcome"] = {"args": {key: "ptp"}}   # คำของงานทวงหนี้
    errs = validate_strict(spec, cat) + validate_flow_spec(spec, cat)[0]
    assert any("enum" in e for e in errs), errs


@pytest.mark.parametrize("doc", ["MANUAL.md", "MANUAL.en.md", "SPEC_LOCKED.md"])
def test_the_docs_teach_the_args_shape(doc):
    """เอกสารต้องไม่สอน `outcome: {"result": …}` อีก — รูปนั้นยังโหลดได้แต่ไม่ใช่รูปที่สอน

    รอบนี้ตกไปหกจุดในสามไฟล์ (ตัวอย่าง state · faq route · session_init.on_failure)
    เพราะแก้ที่ตัวอย่างครบใบแล้วคิดว่าจบ
    """
    import re as _re
    text = (pathlib.Path(__file__).resolve().parents[1] / "demo_v2" / "docs" / doc).read_text(encoding="utf-8")
    # `"outcome"` อาจขึ้นบรรทัดใหม่แล้วค่อยตามด้วย `"args"` (JSON ที่จัดหลายบรรทัด)
    # จึงดูช่วงข้อความถัดไปแทนการเทียบทีละบรรทัด
    stale = []
    for m in _re.finditer(r'"outcome"\s*:', text):
        window = text[m.end(): m.end() + 160]
        if '"args"' not in window:
            stale.append(text[max(0, m.start() - 40): m.end() + 60].replace("\n", "⏎"))
    assert not stale, f"{doc}: ยังสอนรูปเดิม → {stale}"
