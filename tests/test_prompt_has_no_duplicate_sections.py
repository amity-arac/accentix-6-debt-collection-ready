"""พรอมป์ที่โมเดลได้รับ ต้องไม่มีหัวข้อซ้ำและไม่มีหัวข้อว่าง

เกิดจริงกับทุก tenant: `## Available Pre-Scripts` ปรากฏสองครั้งในพรอมป์เดียว โดยมี
เนื้อหาคนละแบบ (ดัชนีชื่อ beat จาก `render_instruction` กับคลังประโยคจาก
`build_script_catalog`) · ต้นเหตุคือตอนสร้าง demo_v2 เอาบล็อกดัชนีมาจากรีโปหลัก
ซึ่งที่นั่นคลังขึ้นหัวว่า `TEMPLATES …:` จึงไม่ชน แต่ demo ใช้ตัวต่อคลังที่ขึ้นหัวว่า
`## Available Pre-Scripts` เหมือนกัน — แต่ละครึ่งถูกในตัวเอง จึงไม่มีใครเห็น

และ SHOP ที่ประกาศ `faq_routing: {"routes": []}` ได้หัวข้อ FAQ เปล่า ๆ ไปในพรอมป์
"""
import collections
import re

import pytest

from demo_v2.server import sessions


def _companies():
    return sorted(sessions.flow_companies())


@pytest.mark.parametrize("company", _companies())
def test_no_section_heading_appears_twice(company):
    text = sessions.flow_instruction(company)
    assert text, f"{company}: ไม่ได้พรอมป์"
    dupes = [h for h, n in collections.Counter(re.findall(r"^#{2,3} .*$", text, re.M)).items()
             if n > 1]
    assert not dupes, f"{company}: หัวข้อซ้ำในพรอมป์เดียว → {dupes}"


@pytest.mark.parametrize("company", _companies())
def test_no_section_is_empty(company):
    """หัวข้อที่ไม่มีเนื้อหาใต้มัน = โทเคนที่ไม่ได้บอกอะไร (และอ่านเหมือนข้อมูลหาย)"""
    text = sessions.flow_instruction(company)
    parts = re.split(r"^(#{2,3} .*)$", text, flags=re.M)
    empty = [parts[i] for i in range(1, len(parts), 2) if not parts[i + 1].strip()]
    assert not empty, f"{company}: หัวข้อว่าง → {empty}"


@pytest.mark.parametrize("company", _companies())
def test_catalog_block_matches_the_training_side_shape(company):
    """คลังประโยคใช้รูปเดียวกับสายเทรน/eval: `TEMPLATES …:` แล้ว ` id [beat]: text`

    รูปนี้ไม่มีหัวข้อ `##` จึงไม่ชนกับดัชนี และเป็นรูปที่โมเดลถูกเทรนมา
    """
    from demo_v2.lib.prescript import TEMPLATES_HEADER
    text = sessions.flow_instruction(company)
    assert TEMPLATES_HEADER in text, f"{company}: ไม่มีบล็อกคลัง"
    body = text.split(TEMPLATES_HEADER, 1)[1]
    rows = [l for l in body.split("\n") if re.match(r"^ \d+ \[[a-z_0-9]+\]", l)]
    assert rows, f"{company}: บล็อกคลังไม่มีบรรทัดรูป ` id [beat]…`"
    # ทุก text_id ในคลังของบริษัทนั้นต้องอยู่ในบล็อก — เคยมีบั๊กที่ตัดทิ้งเงียบ ๆ
    # เพราะจัดกลุ่มด้วยรายชื่อ state ตายตัว
    from demo_v2.server.flow.flowspec import normalize_catalog, resolve_catalog, load_tenant_spec
    spec = load_tenant_spec(sessions._flow_spec_path(company))
    n = len(normalize_catalog(resolve_catalog(spec), spec))
    assert len(rows) == n, f"{company}: คลังมี {n} ประโยค แต่พรอมป์พิมพ์ {len(rows)}"


@pytest.mark.parametrize("company", _companies())
def test_the_chain_rule_is_still_in_the_prompt(company):
    """กติกา chain ย้ายจากหัวบล็อกคลังไปอยู่กับหัวข้อวิธีตอบ — ต้องไม่หายไปเฉย ๆ"""
    from demo_v2.lib.prescript import CHAIN_RULE
    assert CHAIN_RULE in sessions.flow_instruction(company), f"{company}: กติกา chain หาย"
