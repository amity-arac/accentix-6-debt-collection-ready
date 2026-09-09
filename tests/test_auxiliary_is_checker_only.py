"""`auxiliary_templates` ไม่มีผลกับสายที่รันในเดโม — เหลือไว้ให้ตัวตรวจเท่านั้น

คีย์นี้เคยถูกอ้างว่า "ทำให้ beat พูดได้ทุกจังหวะ" แต่วัดจริงแล้วไม่มีด่านไหนอ่านมัน
สิ่งที่มันเคยทำมีสองอย่าง: เพิ่มบรรทัด `- **ตามบริบท** — …` ในดัชนีของพรอมป์ และ
กรอง beat ออกจาก hint ตอน reject ทั้งสองอย่างเป็นคำใบ้ ไม่ใช่กลไก

ที่ยังเหลือคือ *ตัวตรวจ*: `_template_refs` ยังนับคีย์นี้เป็น binding ไฟล์ที่ประกาศไว้
จึงไม่ขึ้น warning และสเปคที่จะถูกส่งไปฝั่งเทรน/วัดผลยังพาคีย์นี้ไปได้ตามเดิม

`fallback_fine_state` ถูกนับเป็น binding แทน — `_fallback_reply()` หยิบ beat นั้นจาก
คลังโดยตรง คนเขียนจึงไม่ต้องประกาศซ้ำใน `auxiliary_templates` เพื่อปิด warning
"""
import inspect
import json
import pathlib
import re

import pytest

from demo_v2.server import sessions
from demo_v2.server.flow import flowspec
from demo_v2.server.flow.flowspec import (load_tenant_spec, normalize_catalog,
                                          validate_flow_spec, validate_strict)
from demo_v2.server.flow.flowspec_render import render_instruction

FLOWS = pathlib.Path(__file__).resolve().parents[1] / "data" / "flows"
DOCS = pathlib.Path(__file__).resolve().parents[1] / "demo_v2" / "docs"
SPECS = sorted(FLOWS.glob("*.company.json"))


def _declares_aux(spec: dict) -> list[str]:
    return [t.get("fine_state") for t in
            ((spec.get("auxiliary_templates") or {}).get("allowed") or [])]


def test_at_least_one_shipped_spec_still_declares_it():
    """ถ้าไม่มีไฟล์ไหนประกาศเลย เทสต์ที่เหลือจะผ่านแบบว่างเปล่า"""
    assert any(_declares_aux(load_tenant_spec(p)) for p in SPECS)


@pytest.mark.parametrize("path", SPECS, ids=lambda p: p.name)
def test_prompt_never_mentions_an_aux_only_beat_as_a_group(path):
    """พรอมป์ต้องไม่มีหัวข้อ 'ตามบริบท' — ดัชนีจัดกลุ่มตาม phase กับ faq เท่านั้น"""
    spec = load_tenant_spec(path)
    if not _declares_aux(spec):
        pytest.skip("ไฟล์นี้ไม่ประกาศ auxiliary_templates")
    txt = render_instruction(spec, crm="omit")
    assert "ตามบริบท" not in txt
    groups = re.findall(r"^- \*\*(.+?)\*\* —", txt, re.M)
    assert set(groups) <= {"opening", "main", "close", "faq"}, groups


def test_runtime_helpers_do_not_read_the_key():
    """ด่าน/ตัวช่วยที่ทำงานระหว่างสายต้องไม่อ้างคีย์นี้"""
    for fn in (sessions._resume_beats, sessions._off_flow_beats,
               sessions._beat_states):
        src = inspect.getsource(fn)
        body = src.split('"""')[2] if src.count('"""') >= 2 else src
        assert "auxiliary_templates" not in body, fn.__name__


def test_resume_beats_returns_faq_only():
    spec = load_tenant_spec(FLOWS / "AEON.company.json")
    aux = set(_declares_aux(spec))
    faq = {t["fine_state"] for r in spec["faq_routing"]["routes"]
           for t in r.get("templates", []) if t.get("fine_state")}
    got = sessions._resume_beats(spec)
    assert got == faq
    assert aux - faq, "AEON ต้องมี beat ที่ประกาศใน aux อย่างเดียว ไม่งั้นเทียบไม่ได้"
    assert not (got & (aux - faq))


def test_the_template_the_app_hands_out_does_not_teach_the_key():
    payload = sessions.flow_template()
    assert "auxiliary_templates" not in json.dumps(payload)
    spec = payload["spec"]
    assert validate_strict(spec) == []
    errors, warnings = validate_flow_spec(
        spec, normalize_catalog(payload["catalog"], spec))
    assert errors == [] and warnings == []


def test_checker_still_counts_the_key_as_a_binding():
    """เก็บตัวตรวจไว้: ไฟล์ที่ประกาศ aux ต้องไม่ถูกฟ้องว่าประโยคนั้นไม่มีใครอ้าง"""
    spec = load_tenant_spec(FLOWS / "AEON.company.json")
    wheres = dict((fs, w) for w, fs in flowspec._template_refs(spec))
    aux_only = [b for b in _declares_aux(spec) if wheres.get(b) == "auxiliary"]
    assert aux_only, "AEON มี beat ที่มีเฉพาะ aux เป็นผู้อ้าง"
    _, warnings = validate_flow_spec(spec, normalize_catalog(spec["catalog"], spec))
    assert not [w for w in warnings if any(b in w for b in aux_only)]


def test_fallback_fine_state_counts_as_a_binding():
    spec = {"fallback_fine_state": "faq_repeat", "states": [], "faq_routing": {"routes": []}}
    assert ("fallback", "faq_repeat") in list(flowspec._template_refs(spec))


@pytest.mark.parametrize("name", ["MANUAL.md", "MANUAL.en.md"])
def test_the_manuals_do_not_teach_the_key(name):
    assert "auxiliary_templates" not in (DOCS / name).read_text(encoding="utf-8")
