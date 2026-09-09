"""เส้นทาง Upload ในหน้า demo — สร้างบริษัทใหม่ได้จบในแอป ไม่ต้องแตะไฟล์บนเซิร์ฟเวอร์

สามข้อที่เทสต์นี้ตรึงไว้ เพราะทั้งสามเคยพลาดเงียบ ๆ:

  ① template ที่แอปแจก ต้องอัปโหลดกลับได้ทันทีโดยไม่ต้องแก้อะไร — เคยแจก `outcomes`
     ซึ่งเป็น key ที่ loader แค่ทนรับ และไม่ตั้ง `agent_role` ทำให้ร้านกาแฟได้พรอมป์ว่า
     ตัวเองเป็นเจ้าหน้าที่ทวงหนี้ใต้ พ.ร.บ. ทวงถามหนี้
  ② บล็อก `crm` ในไฟล์อัปโหลดต้องถึงสายเดโม — เป็นทางเดียวที่ให้ข้อมูลได้โดยไม่ต้องมี
     API จริงหรือไปแก้ persona บนเครื่อง
  ③ ไม่มี CRM ให้ ต้องบอกตอนอัปโหลด — ไม่ใช่ไปโผล่เป็น `[customer_name]` ตอนโทร

ทุกเคสสร้างบริษัทจริงแล้วลบทิ้งใน finally — data/ ต้องไม่มีอะไรค้าง
"""
import json

import pytest

from demo_v2.server import sessions


@pytest.fixture()
def template() -> dict:
    return sessions.flow_template()


def _fresh(code: str):
    if code.upper() in sessions.load_flow_registry():
        pytest.skip(f"{code} มีอยู่แล้วในเครื่องนี้")


def _persona(code: str) -> dict:
    rows = json.loads(sessions.BUILDER_CASES_FILE.read_text(encoding="utf-8"))
    return next(c for c in rows if c["id"] == f"TC-{code.upper()}-BUILD-001")


def test_template_declares_the_keys_the_app_actually_needs(template):
    spec = template["spec"]
    for key in ("events", "states", "tools", "faq_routing", "constraints"):
        assert key in spec, f"template ขาด key จำเป็น {key}"
    assert isinstance(spec["tools"], dict) and "declarations" in spec["tools"]
    closers = [d["name"] for d in spec["tools"]["declarations"]
               if (d.get("gating") or {}).get("required_at") == "end_of_call"]
    assert len(closers) == 1, f"template ต้องมี tool ปิดสายตัวเดียว ได้ {closers}"
    assert spec.get("agent_role"), "ไม่ตั้ง agent_role = พรอมป์ตกไปที่ identity ทวงหนี้"
    for retired in ("outcomes", "spec_version", "role", "legal_note"):
        assert retired not in spec, f"template ไม่ควรสอน key ที่เลิกใช้: {retired}"
    assert isinstance(template.get("crm"), dict) and template["crm"], "ต้องมีบล็อก crm"


def test_the_template_it_hands_out_uploads_back_unchanged(template):
    """ดาวน์โหลด → อัปโหลดกลับทันที ต้องผ่าน และไม่มี placeholder ที่เติมไม่ได้"""
    code = str(template["spec"]["company"]).upper()
    _fresh(code)
    res = None
    try:
        res = sessions.create_flow_company_raw(
            template["spec"], template["catalog"], "ร้านทดสอบ", "น้องทดสอบ",
            template["crm"])
        assert res.get("ok"), res.get("errors")
        assert "unfillable_placeholders" not in res, res.get("warning")
        prompt = sessions.flow_instruction(code)
        assert template["spec"]["agent_role"] in prompt
        assert "ทวงถามหนี้" not in prompt
    finally:
        if res and res.get("ok"):
            sessions.delete_flow_company(code)


def test_uploaded_crm_reaches_the_demo_caller(template):
    code = str(template["spec"]["company"]).upper()
    _fresh(code)
    res = None
    try:
        res = sessions.create_flow_company_raw(
            template["spec"], template["catalog"], "ร้านทดสอบ", "น้องทดสอบ",
            {**template["crm"], "customer_name": "อารีย์ ทดสอบ", "due_date_offset_days": 3})
        assert res.get("ok"), res.get("errors")
        cd = _persona(code)["customer_data"]
        assert cd["customer_name"] == "อารีย์ ทดสอบ"
        assert cd["due_date_offset_days"] == 3, "วันที่ต้องเก็บเป็น offset ไม่ใช่วันตายตัว"
        assert cd["msisdn"], "field ที่ persona เป็นเจ้าของต้องไม่หาย"
        assert "_hint" not in cd, "โน้ตในเทมเพลตต้องไม่กลายเป็น field ใน CRM snapshot"
    finally:
        if res and res.get("ok"):
            sessions.delete_flow_company(code)


def test_a_company_with_no_crm_is_told_at_upload_time(template):
    """ไม่ส่ง crm มา = ประโยคที่อ้าง field จะรั่ว ต้องรู้ตอนอัปโหลด ไม่ใช่ตอนโทร"""
    code = str(template["spec"]["company"]).upper()
    _fresh(code)
    res = None
    try:
        res = sessions.create_flow_company_raw(
            template["spec"], template["catalog"], "ร้านทดสอบ", "น้องทดสอบ")
        assert res.get("ok")
        assert res.get("unfillable_placeholders"), "ต้องบอกว่าเติมชื่อไหนไม่ได้"
        assert "customer_name" in res["unfillable_placeholders"]
        assert res.get("warning")
    finally:
        if res and res.get("ok"):
            sessions.delete_flow_company(code)


def test_a_declared_session_init_answers_for_its_own_fields(template):
    """สเปคที่ประกาศ session_init ไม่ควรถูกหาว่า CRM ขาด — API ตอบให้ตอนรัน"""
    code = str(template["spec"]["company"]).upper()
    _fresh(code)
    spec = json.loads(json.dumps(template["spec"]))
    spec["session_init"] = {
        "url": "{API_BASE}/{company}/init", "method": "GET", "timeout": 8,
        "on_failure": {"fine_state": "close"},
    }
    res = None
    try:
        res = sessions.create_flow_company_raw(spec, template["catalog"],
                                               "ร้านทดสอบ", "น้องทดสอบ")
        assert res.get("ok"), res.get("errors")
        assert "unfillable_placeholders" not in res, res.get("warning")
    finally:
        if res and res.get("ok"):
            sessions.delete_flow_company(code)


def test_delete_leaves_nothing_behind(template):
    code = str(template["spec"]["company"]).upper()
    _fresh(code)
    res = sessions.create_flow_company_raw(template["spec"], template["catalog"],
                                           "ร้านทดสอบ", "น้องทดสอบ", template["crm"])
    assert res.get("ok")
    out = sessions.delete_flow_company(code)
    assert out.get("ok"), out
    assert code not in sessions.load_flow_registry()
    rows = json.loads(sessions.BUILDER_CASES_FILE.read_text(encoding="utf-8"))
    assert not [c for c in rows if c["id"].startswith(f"TC-{code}-")]
