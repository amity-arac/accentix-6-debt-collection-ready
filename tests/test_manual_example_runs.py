"""เอกสารตัวอย่างในคู่มือต้องอัปโหลดผ่านเส้นทางจริงของแอปได้ — ไม่ใช่แค่หน้าตาถูก

เหตุที่มีเทสต์นี้: คู่มือเคยสอนครบทุกหัวข้อแต่ไม่เคยโชว์ไฟล์ครบใบเดียว คนที่เขียนตาม
เส้นทางหลักจึงได้ไฟล์ที่พังสามจุด (`tools` ไม่ได้ห่อด้วย `declarations` · ไม่มี
`faq_routing` ซึ่งเป็น key จำเป็น · ไม่มี tool ที่ `required_at: end_of_call`) โดยสองข้อ
แรกไม่ปรากฏในเอกสารเลย ตัวอย่างที่ไม่มีเทสต์ค้ำก็จะกลับไปเป็นอย่างนั้นอีก

เทสต์ตัวสุดท้ายสร้างบริษัทจริงผ่าน `create_flow_company_raw` แล้วลบทิ้ง — เพราะ
"validate ผ่าน" ไม่ได้แปลว่า "อัปโหลดได้": สเปคที่ไม่มี tool ปิดสาย validate ผ่านสะอาด
แล้วไปตายตอนประกอบพรอมป์

ทั้งสองฉบับ (ไทย/อังกฤษ) ต้องมีบล็อกเดียวกันเป๊ะ — ถ้าแก้ฉบับเดียว เทสต์นี้แดง
"""
import contextlib
import io
import json
import pathlib
import re

import pytest

from demo_v2.lib.prescript import fill_template
from demo_v2.server import sessions
from demo_v2.server.flow.flowspec import (normalize_catalog, validate_flow_spec,
                                          validate_strict)
from demo_v2.server.flow.flowspec_render import render_instruction

DOCS = pathlib.Path(__file__).resolve().parents[1] / "demo_v2" / "docs"
MARKER = "<!-- MINIMAL-EXAMPLE -->"
OUTER_KEYS = ("display_name", "agent_name", "crm", "spec", "catalog")
REQUIRED_TOP = ("events", "states", "tools", "faq_routing", "constraints")
# ชื่อไฟล์คือ identity — เอกสารอัปโหลดไม่ควรสอนให้เขียนสองตัวนี้
# `role`/`legal_note` ถูกถอดออกจาก format — ตัวอย่างต้องไม่สอนให้เขียน
FILENAME_OWNED = ("flow_id", "spec_version", "role", "legal_note")
# field ที่ session เติมให้เองทุกสาย (sessions.py: cd.setdefault("today", …))
SESSION_SUPPLIED = ("today",)


def _block(name: str) -> str:
    text = (DOCS / name).read_text(encoding="utf-8")
    assert text.count(MARKER) == 1, f"{name}: ต้องมี {MARKER} หนึ่งที่"
    after = text.split(MARKER, 1)[1]
    # อนุญาตให้มีบรรทัดว่างคั่นระหว่าง marker กับ fence — เอกสารถูกจัดรูปใหม่ได้
    m = re.search(r"\s*```json\n(.*?)\n```", after, re.S)
    assert m, f"{name}: ไม่พบบล็อก ```json หลัง {MARKER}"
    return m.group(1)


@pytest.fixture(scope="module")
def doc() -> dict:
    return json.loads(_block("MANUAL.md"))


@pytest.fixture(scope="module")
def spec(doc) -> dict:
    return doc["spec"]


def _context(doc: dict) -> dict:
    """CRM ที่สายเดโมจะถืออยู่จริง — offset ถูกแปลงเป็นวันที่แล้ว"""
    ctx = {k: v for k, v in doc["crm"].items() if not k.startswith("_")}
    sessions._resolve_offset_dates(ctx)
    return {**{f: "2026-01-01 (Thursday)" for f in SESSION_SUPPLIED}, **ctx}


def test_both_manuals_carry_the_same_example():
    assert _block("MANUAL.md") == _block("MANUAL.en.md")


def test_example_has_the_shape_the_upload_accepts(doc):
    missing = [k for k in OUTER_KEYS if k not in doc]
    assert not missing, f"เอกสารอัปโหลดขาด key ชั้นนอก: {missing}"
    assert isinstance(doc["catalog"], list) and doc["catalog"]
    assert doc["spec"].get("company"), "spec.company คือรหัสบริษัทตอนอัปโหลด"


def test_example_declares_every_required_key(spec):
    missing = [k for k in REQUIRED_TOP if k not in spec]
    assert not missing, f"ตัวอย่างขาด key จำเป็น: {missing}"


def test_example_lets_the_filename_own_the_identity(spec):
    present = [k for k in FILENAME_OWNED if k in spec]
    assert not present, f"ไม่ควรเขียน {present} — ชื่อไฟล์/แอปเป็นตัวกำหนด"


def test_example_passes_both_validators(doc, spec):
    catalog = normalize_catalog(doc["catalog"], spec)
    assert validate_strict(spec, catalog) == []
    errors, warnings = validate_flow_spec(spec, catalog)
    assert errors == []
    assert warnings == []


def test_example_renders_a_prompt(spec):
    """ประกอบพรอมป์ได้ และไม่ตกไปที่ identity เริ่มต้นของงานทวงหนี้"""
    text = render_instruction(spec)
    assert spec["agent_role"] in text
    assert "ทวงถามหนี้" not in text, (
        "tenant ที่ไม่ใช่งานทวงหนี้ต้องประกาศ agent_role")


def test_example_declares_exactly_one_closing_tool(spec):
    decls = spec["tools"]["declarations"]
    closers = [d["name"] for d in decls
               if (d.get("gating") or {}).get("required_at") == "end_of_call"]
    assert len(closers) == 1, f"ต้องมี tool ปิดสายตัวเดียว ได้ {closers}"
    # ทุก state ที่บันทึกผล ต้องเรียกตัวนั้นก่อนปิด ไม่งั้นจบสายโดยไม่บันทึก
    for st in spec["states"]:
        if st.get("outcome"):
            assert closers[0] in (st.get("entry_tools") or []), (
                f"state {st['id']} มี outcome แต่ไม่ได้เรียก {closers[0]}")


def test_crm_block_fills_every_sentence(doc, spec):
    """ทุกสำนวนต้องเติมได้จากบล็อก `crm` — slot ที่ tool คืนมาตอนรันไม่นับ

    ตัวอย่างนี้ไม่มี `session_init` ⇒ `crm` เป็นแหล่งเดียว ถ้ามันไม่ครบ agent จะพูด
    `[customer_name]` ออกไปถึงผู้รับสายจริง
    """
    known = set(SESSION_SUPPLIED)
    for d in spec["tools"]["declarations"]:
        known |= set((d.get("returns") or {}).keys())
        known |= set((d.get("args") or {}).keys())
    ctx = _context(doc)
    leaked: dict = {}
    with contextlib.redirect_stderr(io.StringIO()):
        for e in normalize_catalog(doc["catalog"], spec):
            out = fill_template(e["template"], ctx, gender="F")
            for ph in re.findall(r"[\[\{]([a-z_0-9]+)[\]\}]", out):
                if ph not in known:
                    leaked.setdefault(ph, []).append(e["text_id"])
    assert not leaked, f"slot ค้าง: {leaked}"


def test_example_uploads_through_the_real_app_path(doc):
    """สร้างบริษัทจริงด้วยเส้นทางเดียวกับหน้า Upload แล้วลบทิ้ง

    validate ผ่านไม่ได้แปลว่าอัปโหลดได้ — สเปคที่ไม่มี tool ปิดสาย validate สะอาด
    แล้วไปตายตอน render พรอมป์ เทสต์นี้จึงเดินเส้นทางจริงทั้งเส้น
    """
    code = str(doc["spec"]["company"]).upper()
    if code in sessions.load_flow_registry():
        pytest.skip(f"{code} มีอยู่แล้วในเครื่องนี้")
    res = None
    try:
        res = sessions.create_flow_company_raw(
            json.loads(json.dumps(doc["spec"])), json.loads(json.dumps(doc["catalog"])),
            doc["display_name"], doc["agent_name"], doc["crm"])
        assert res.get("ok"), f"อัปโหลดไม่ผ่าน: {res.get('errors')}"
        assert "unfillable_placeholders" not in res, res.get("warning")
        assert code in sessions.load_flow_registry()
        assert sessions.flow_instruction(code)
    finally:
        if res and res.get("ok"):
            assert sessions.delete_flow_company(code).get("ok")
    assert code not in sessions.load_flow_registry()

def test_the_example_points_at_a_url_the_reader_controls(doc):
    """ผู้อ่านไม่มีเซิร์ฟเวอร์ของระบบ — `{API_BASE}` เป็น env ฝั่งโฮสต์ ใช้ในสเปคของ
    tenant ไม่ได้ และทุก tool ต้องเป็น http ที่เขาโฮสต์เอง (generic เขียนอะไรลงระบบ
    ของเขาไม่ได้ จึงส่งมอบด้วยไม่ได้)
    """
    for d in doc["spec"]["tools"]["declarations"]:
        assert d.get("impl") == "http", f"{d['name']}: ต้องเป็น impl http"
        url = d.get("url", "")
        assert url.startswith("https://"), f"{d['name']}: ต้องเป็น URL เต็มของ tenant"
        assert "{API_BASE}" not in url, f"{d['name']}: {{API_BASE}} เป็น env ของฝั่งโฮสต์"
