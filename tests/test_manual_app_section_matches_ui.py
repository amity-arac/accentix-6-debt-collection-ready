"""หัวข้อ "ใช้ demo app" ในคู่มือ ต้องตรงกับ UI และโค้ดจริง

หัวข้อนี้บรรยายสิ่งที่คนเห็นบนหน้าจอ (คีย์ลัด ชนิดฟอง รหัสที่โผล่ในฟองเตือน ที่เก็บไฟล์
Save) ซึ่งเป็นของที่เปลี่ยนได้เงียบที่สุด — คนแก้ UI ไม่มีเหตุให้เปิดคู่มือ เทสต์นี้เทียบ
ทีละอย่างกับซอร์ส ทั้งสองฉบับต้องตรงพร้อมกัน
"""
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "demo_v2" / "docs"
MANUALS = {name: (DOCS / name).read_text(encoding="utf-8") for name in ("MANUAL.md", "MANUAL.en.md")}
sys.path.insert(0, str(ROOT / "tools"))


def _app_section(text: str) -> str:
    """ตั้งแต่หัวข้อเรื่อง demo app ลงไปจนจบไฟล์ — ตัดส่วนเขียนสเปคออก เพื่อไม่ให้เคส
    ผ่านเพราะคำไปปรากฏในหัวข้ออื่น

    จับหัวข้อแบบหลวม (ระดับใดก็ได้ · ไทยหรืออังกฤษ) เพราะคู่มือถูกเกลาคำและจัดลำดับ
    หัวข้อใหม่ได้ — เทสต์นี้มีหน้าที่ตรวจ *ข้อเท็จจริง* ไม่ใช่ตรึงถ้อยคำของหัวข้อ
    """
    m = re.search(r"^#{1,3} .*[Dd]emo [Aa]pp.*$", text, re.M)
    if not m:
        raise AssertionError("ไม่พบหัวข้อเรื่อง demo app ในคู่มือ")
    return text[m.end():]


@pytest.mark.parametrize("name", sorted(MANUALS))
def test_the_manual_lists_every_keyboard_shortcut(name):
    src = (ROOT / "demo_v2/frontend/src/components/ShortcutsHint.tsx").read_text(encoding="utf-8")
    keys = re.findall(r'\["(\w+)", "', src)
    assert keys, "อ่านคีย์จาก ShortcutsHint ไม่ได้"
    section = _app_section(MANUALS[name])
    missing = [k for k in keys if f"`{k}`" not in section]
    assert not missing, f"{name}: คู่มือไม่ได้บอกคีย์ {missing}"


@pytest.mark.parametrize("name", sorted(MANUALS))
def test_the_manual_lists_every_bubble_kind(name):
    src = (ROOT / "demo_v2/frontend/src/components/Bubble.tsx").read_text(encoding="utf-8")
    kinds = set(re.findall(r'entry\.kind === "(\w+)"', src))
    # tool_result เป็นสาขา else ท้ายฟังก์ชัน จึงไม่มีการเทียบ kind ให้ regex จับ
    kinds.add("tool_result")
    section = _app_section(MANUALS[name])
    missing = [k for k in sorted(kinds)
               if k not in section and k.replace("_", " ") not in section]
    assert not missing, f"{name}: คู่มือไม่ได้อธิบายฟองชนิด {missing}"


@pytest.mark.parametrize("name", sorted(MANUALS))
def test_every_snake_case_name_in_the_section_is_real(name):
    """คำใน backtick ต้องเป็นรหัสปฏิเสธจริง หรือ key จริงของสเปค — ไม่มีอะไรอยู่กลาง ๆ

    เอกสารสามไฟล์เคยพิมพ์รายการรหัสด้วยมือและผิดทั้งสามไฟล์ · คำที่ไม่ใช่ทั้งสองอย่าง
    แทบทุกครั้งคือพิมพ์ผิดหรือชื่อที่เลิกใช้แล้ว
    """
    import list_reject_codes as codes
    from demo_v2.server.flow import flowspec as fs

    reply = set(codes.reply_gate())
    fixed, _derived = codes.tool_gate()
    vocab = reply | set(fixed) | set(fs.CONSTRAINT_TYPES) | set(fs.KNOWN_IMPLS)
    for keys in ("TOP_KEYS", "STATE_KEYS", "TEMPLATE_KEYS", "TRANSITION_KEYS",
                 "CONSTRAINT_KEYS", "GATING_KEYS", "CATALOG_KEYS", "VERIFIED_WHEN_KEYS"):
        vocab |= set(getattr(fs, keys))
    # key ที่ไม่มี frozenset ของตัวเอง (`gating` ใน declaration, `one_of_from` ใน
    # สัญญาของ argument, `on_failure` ใน session_init) — เอาจากสเปคจริงทุกตัว
    # ไม่ใช่พิมพ์รายชื่อไว้ในเทสต์ ซึ่งจะกลายเป็นรายการที่ต้องมาไล่แก้เองอีกที่
    import glob
    import json

    def walk(node, acc):
        if isinstance(node, dict):
            acc.update(node.keys())
            for v in node.values():
                walk(v, acc)
        elif isinstance(node, list):
            for v in node:
                walk(v, acc)

    for f in glob.glob(str(ROOT / "data" / "flows" / "*.company.json")):
        walk(json.loads(pathlib.Path(f).read_text(encoding="utf-8")), vocab)

    # key ที่โค้ดอ่านแต่ยังไม่มีสเปคไหนใช้ (`headers` เป็นตัวอย่าง — tenant ที่ต้องส่ง
    # token ต้องใช้ แต่ไม่มีบริษัทที่ ship มาใช้เลย) อ่านจากตัวที่ dispatch ของจริง
    for src in ("demo_v2/server/flow/spec_backend.py",
                "demo_v2/server/flow/session_init.py"):
        vocab |= set(re.findall(r'\.get\("([a-z_]+)"',
                                (ROOT / src).read_text(encoding="utf-8")))

    # ชนิดฟองในหน้าแชต — ชื่อจริงจาก Bubble.tsx (tool_call / tool_result / warning / …)
    bub = (ROOT / "demo_v2/frontend/src/components/Bubble.tsx").read_text(encoding="utf-8")
    vocab |= set(re.findall(r'entry\.kind === "(\w+)"', bub)) | {"tool_result"}

    # field ในฟอง / คำตอบของ tool ที่ไม่ได้อยู่ในสเปคของใคร
    vocab |= {"missing_tools", "missing_beats", "empty_slots", "customer_data",
              "crm", "agent_name", "unfillable_placeholders", "_demo_persona"}

    section = _app_section(MANUALS[name])
    cited = set(re.findall(r"`([a-z_][a-z_0-9]{5,})`", section))
    unknown = sorted(c for c in cited if c not in vocab)
    assert not unknown, (
        f"{name}: ชื่อพวกนี้ไม่ใช่รหัสปฏิเสธและไม่ใช่ key ของสเปค: {unknown}")


@pytest.mark.parametrize("name", sorted(MANUALS))
def test_the_loop_ceiling_and_save_path_match_the_code(name):
    sessions_src = (ROOT / "demo_v2/server/sessions.py").read_text(encoding="utf-8")
    ceiling = re.search(r"FLOW_MAX_TOOL_LOOPS\s*=\s*(\d+)", sessions_src)
    assert ceiling, "หา FLOW_MAX_TOOL_LOOPS ไม่เจอ"
    app_src = (ROOT / "demo_v2/server/app.py").read_text(encoding="utf-8")
    save_dir = re.search(r"(data/demo-saved-trajectory/[^\s`\"]*)", app_src)
    assert save_dir, "หาที่เก็บไฟล์ save ไม่เจอ"
    section = _app_section(MANUALS[name])
    assert f"FLOW_MAX_TOOL_LOOPS = {ceiling.group(1)}" in section, \
        f"{name}: เพดานลูปในคู่มือไม่ตรงกับโค้ด ({ceiling.group(1)})"
    assert "data/demo-saved-trajectory/" in section, \
        f"{name}: ไม่ได้บอกว่า Save ไปไว้ที่ไหน"


@pytest.mark.parametrize("name", sorted(MANUALS))
def test_the_manual_names_both_create_paths(name):
    """สองทางสร้างบริษัทต่างกันที่ผัง — ถ้าคู่มือพูดถึงทางเดียว คนจะได้ผังทวงหนี้มาโดยไม่รู้"""
    section = _app_section(MANUALS[name])
    assert "New company" in section, f"{name}: ไม่ได้พูดถึงทางฟอร์ม (＋ New company)"
    assert "Upload" in section, f"{name}: ไม่ได้พูดถึงทาง Upload"
    # ทางฟอร์มใช้ผังของ flow ตั้งต้น — ถ้าคู่มือไม่เตือน คนทำงานที่ไม่ใช่ทวงหนี้จะได้ผัง
    # ทวงหนี้มาทั้งชุดโดยไม่รู้ตัว
    assert "AEON" in section, f"{name}: ไม่ได้บอกว่าทางฟอร์มได้ผังของ flow ตั้งต้น"
