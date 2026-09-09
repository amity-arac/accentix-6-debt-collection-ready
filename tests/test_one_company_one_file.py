"""หนึ่งบริษัท = หนึ่งไฟล์ — ทั้งตอนอ่านและตอนเขียน

รูปคลังประโยคเคยมีสามแบบ: `catalog` เป็น list · `catalog_inline` คู่กับ
`catalog: "__inline__"` · และ `catalog` เป็น *ชื่อไฟล์* ใน `data/pre-scripts/`
สองแบบหลังไม่มีไฟล์ใดใช้แล้ว แต่โค้ดยังแบกทางอ่านทั้งสามและทางเขียนไฟล์คลังแยก

เทสต์ชุดนี้มีเพราะตอนยุบรูป ผมทำ `flow_prescripts` และ `save_flow_spec` พังทั้งคู่
(NameError จาก `cat_path` ที่ไม่มีแล้ว) และเทสต์ 281 ข้อผ่านหมด — ไม่มีข้อไหนเรียก
สองฟังก์ชันนี้เลย
"""
import contextlib
import io
import json
import pathlib

import pytest

from demo_v2.server import sessions
from demo_v2.server.flow.flowspec import TOP_KEYS, load_tenant_spec, resolve_catalog

FLOWS = pathlib.Path(__file__).resolve().parents[1] / "data" / "flows"
COMPANIES = sorted(sessions.load_flow_registry())


@pytest.mark.parametrize("path", sorted(FLOWS.glob("*.company.json")), ids=lambda p: p.name)
def test_every_spec_carries_its_catalog_inline(path):
    spec = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(spec.get("catalog"), list), "`catalog` ต้องเป็น list ในไฟล์เดียวกัน"
    assert "catalog_inline" not in spec
    assert "outcomes" not in spec


def test_the_retired_shapes_are_rejected_not_silently_accepted():
    for bad in ({"catalog": "v10_pre_script_database.json"},
                {"catalog": "__inline__", "catalog_inline": [{"text_id": 1, "_fine_state": "a",
                                                              "template": "x"}]}):
        with pytest.raises(ValueError):
            resolve_catalog(bad)


def test_the_legacy_keys_are_out_of_the_lock():
    assert "catalog_inline" not in TOP_KEYS
    assert "outcomes" not in TOP_KEYS


@pytest.mark.parametrize("company", COMPANIES)
def test_flow_prescripts_answers(company):
    """หน้าตรวจคลังประโยค — พังเงียบมาแล้ว เพราะไม่มีเทสต์เรียกมัน"""
    with contextlib.redirect_stdout(io.StringIO()):
        got = sessions.flow_prescripts(company)
    assert got["spec_file"] == f"{company}.company.json"
    assert "catalog_file" not in got, "หนึ่งบริษัทหนึ่งไฟล์ ไม่มีไฟล์คลังแยกให้รายงาน"
    assert got["counts"]["templates"] > 0
    assert got["entries"] and got["states"]


@pytest.mark.parametrize("company", COMPANIES)
def test_flow_instruction_answers(company):
    with contextlib.redirect_stdout(io.StringIO()):
        txt = sessions.flow_instruction(company)
    assert len(txt) > 500, company


def test_save_flow_spec_writes_a_new_template_into_the_spec_file():
    """คลังต้องเข้าไฟล์สเปค ไม่ใช่ไฟล์แยก — และไฟล์ต้องกลับสภาพเดิมเมื่อจบเทสต์"""
    path = FLOWS / "LIB.company.json"
    before = path.read_text(encoding="utf-8")
    try:
        spec = json.loads(before)
        n0 = len(spec["catalog"])
        with contextlib.redirect_stdout(io.StringIO()):
            res = sessions.save_flow_spec(
                "LIB", spec, [{"fine_state": "zz_probe", "template": "ทดสอบ{suffix}"}])
        assert res.get("ok"), res
        after = json.loads(path.read_text(encoding="utf-8"))
        assert len(after["catalog"]) == n0 + 1
        assert any(e.get("_fine_state") == "zz_probe" for e in after["catalog"])
        stray = list((path.parents[1] / "pre-scripts").glob("LIB*")) \
            if (path.parents[1] / "pre-scripts").exists() else []
        assert not stray, f"มีไฟล์คลังแยกโผล่มา: {stray}"
    finally:
        path.write_text(before, encoding="utf-8")
    assert len(json.loads(path.read_text(encoding="utf-8"))["catalog"]) == n0
