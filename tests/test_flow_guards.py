"""ด่านที่อ่านกติกาจากสเปคล้วน — ต้องทำงานกับทุก tenant และเงียบเมื่อสเปคไม่ได้ขอ

ทุกเคสในไฟล์นี้ยิงกับสเปคจริงใน data/flows/*.company.json (ไม่ mock) เพราะสิ่งที่
ต้องกันคือ "โค้ดแอบรู้จักโดเมนของ AEON" ซึ่งจะไม่โผล่ถ้าทดสอบด้วยสเปคที่แต่งเอง
"""
import glob
import json
import re
import sys
import pathlib

import pytest

from demo_v2.lib.datetime_utils import resolve_spoken_date
from demo_v2.server.sessions import (_closing_beats, _is_verified, _off_flow_beats,
                                     _recover_toolcalls, _verify_gate)

FLOWS = sorted(glob.glob(str(pathlib.Path(__file__).resolve().parents[1] /
                             "data" / "flows" / "*.company.json")))
SPECS = {pathlib.Path(f).name.replace(".company.json", ""): json.load(open(f, encoding="utf-8"))
         for f in FLOWS}


def _initial(spec):
    return next((st["id"] for st in spec.get("states", []) if st.get("initial")), None)


@pytest.mark.parametrize("name", sorted(SPECS))
def test_guards_never_block_a_faq_answer(name):
    """ตอบ FAQ แล้วกลับเข้า flow ต้องพูดได้จากทุกจุด — ไม่งั้นสายจะตอบคำถามลูกค้าไม่ได้"""
    spec = SPECS[name]
    init = _initial(spec)
    if init is None:
        pytest.skip("spec ไม่มี initial state")
    for route in (spec.get("faq_routing") or {}).get("routes", []):
        if route.get("then") != "resume":
            continue
        beats = {t.get("fine_state") for t in (route.get("templates") or [])
                 if isinstance(t, dict) and t.get("fine_state")}
        assert _off_flow_beats(spec, {init}, beats) == [], f"{name}: {route.get('intent')}"


@pytest.mark.parametrize("name", sorted(SPECS))
def test_closing_set_is_never_empty(name):
    """ทุกสเปคต้องมีจังหวะปิดสายอย่างน้อยหนึ่ง — ด่านปิดสายจะได้ไม่กันจนจบไม่ได้"""
    spec = SPECS[name]
    if not any(st.get("terminal") for st in spec.get("states", [])):
        pytest.skip("spec ไม่มี terminal state")
    assert _closing_beats(spec)


def test_closing_set_narrows_to_the_recorded_result():
    """result ที่โมเดลส่งมาเองต้องเลือกชุดคำลาให้แคบลง ไม่ใช่คืนทุกจังหวะปิด"""
    spec = SPECS["AEON"]
    assert _closing_beats(spec, "unreachable") < _closing_beats(spec)


@pytest.mark.parametrize("name", sorted(SPECS))
def test_verify_gate_needs_both_halves(name):
    """ประกาศ verify_required แต่ไม่มี tool ปลดล็อก = ต้องไม่มี gate (ไม่งั้นสายค้างถาวร)"""
    spec = SPECS[name]
    need, unlock = _verify_gate(spec)
    assert bool(need) == bool(unlock), f"{name}: need={sorted(need)} unlock={sorted(unlock)}"


def test_verify_reads_the_api_answer_the_spec_declares():
    """ยืนยันตัวตนตัดสินจากคำตอบ API ตาม verified_when — ไม่ผูกชื่อ tool/ชื่อ field"""
    assert _is_verified({"kyc": {"field": "verified", "equals": True}},
                        [{"tool": "kyc", "result": {"verified": True}}])
    assert _is_verified({"kyc": {"field": "kyc_status", "equals": "passed"}},
                        [{"tool": "kyc", "result": {"data": {"kyc_status": "passed"}}}])
    assert _is_verified({"lookup": {"any_success": True}},
                        [{"tool": "lookup", "result": {"balance": 100}}])
    assert not _is_verified({"kyc": {"any_success": True}},
                            [{"tool": "kyc", "result": {"error": "identity_mismatch"}}])
    assert not _is_verified({"kyc": {"any_success": True}}, [])


@pytest.mark.parametrize("raw,want_name", [
    ('<tool_call>{"name": "reply", "arguments": {"text_ids": [1], "dynamic_vars": [5400]}</tool_call>', "reply"),
    ('<tool_call>{"name": "reply", "arguments": {"text_ids": [1], "dynamic_vars": [slot_name]}}</tool_call>', "reply"),
    ('<tool_call>{"name": "record_outcome", "arguments": {"result": "tcb", "reason": "กฎว่า "เลื่อน" ไม่ใช่"}}</tool_call>', "record_outcome"),
    ('<tool_call>{"name": "record_outcome", "arguments": {"result": "tcb", "reason": "โดนตัด', "record_outcome"),
    ('<tool_call>{"name": "reply", "arguments": {"text_ids": [1], "dynamic_vars": []}}</tool_call>', "reply"),
])
def test_broken_tool_call_json_is_recovered(raw, want_name):
    out = _recover_toolcalls(raw)
    assert out and out[0]["function"]["name"] == want_name


def test_placeholder_name_never_becomes_a_spoken_value():
    """ชื่อ placeholder ที่หลุดมาใน dynamic_vars ต้องถูกตัด ไม่ใช่อ่านออกเสียงให้ลูกค้าฟัง"""
    out = _recover_toolcalls(
        '<tool_call>{"name": "reply", "arguments": {"text_ids": [1], "dynamic_vars": [new_slot]}}</tool_call>')
    assert json.loads(out[0]["function"]["arguments"])["dynamic_vars"] == []


@pytest.mark.parametrize("said,offset_sign", [("พรุ่งนี้", 1), ("มะรืน", 1), ("สิ้นเดือน", 1),
                                              ("วันที่ 15 เดือนหน้า", 1), ("เสาร์หน้า", 1)])
def test_spoken_dates_resolve_into_the_future(said, offset_sign):
    import datetime as dt
    got = resolve_spoken_date(said)
    assert got is not None and (got - dt.date.today()).days * offset_sign > 0


def test_iso_dates_are_left_alone():
    assert resolve_spoken_date("2026-06-15") is None
    assert resolve_spoken_date("เร็วๆ นี้") is None


def test_verify_declaration_halves_are_linted():
    """ประกาศครึ่งเดียวต้องเป็น ERROR ตอน lint — ไม่ใช่ผ่านแล้วเงียบตอนรัน"""
    from demo_v2.server.flow.flowspec import validate_strict
    base = json.loads(json.dumps(SPECS["AEON"]))
    for d in base["tools"]["declarations"]:
        d.pop("provides", None)
        d.pop("verified_when", None)
    only_provides = json.loads(json.dumps(base))
    only_provides["tools"]["declarations"][0]["provides"] = "verified"
    assert any("verified_when" in e for e in validate_strict(only_provides))

    only_when = json.loads(json.dumps(base))
    only_when["tools"]["declarations"][0]["verified_when"] = {"any_success": True}
    assert any("provides" in e for e in validate_strict(only_when))

    wrong_shape = json.loads(json.dumps(base))
    wrong_shape["tools"]["declarations"][0].update(
        {"provides": "verified", "verified_when": {"equals": True}})
    assert any("verified_when" in e for e in validate_strict(wrong_shape))


@pytest.mark.parametrize("name", sorted(SPECS))
def test_shipped_specs_lint_clean(name):
    from demo_v2.server.flow.flowspec import (normalize_catalog, resolve_catalog,
                                              validate_flow_spec, validate_strict)
    spec = SPECS[name]
    catalog = normalize_catalog(resolve_catalog(spec), spec)
    errors, _warnings = validate_flow_spec(spec, catalog)
    assert errors == []
    assert validate_strict(spec, catalog) == []


# --- ด่านเลขไม่มีในคลัง -------------------------------------------------------
# ก่อนมีด่านนี้ text_id ที่โมเดลแต่งขึ้นเองถูกทิ้งเงียบใน _render_reply แล้วสายก็เดิน
# ต่อด้วย fallback — ลูกค้าได้ยินประโยคที่ฟังดูปกติแต่ผิดเรื่อง และโมเดลไม่เคยรู้ว่า
# เลือกผิด วัดบน AMT (บริษัทนอกชุดเทรน) เจอ 4 ใน 6 เคสตกเพราะเหตุนี้ล้วน

class _Cat:
    """session ปลอมเท่าที่ _render_reply ใช้ — คลังจริงจากสเปคของ tenant"""
    def __init__(self, spec, entries):
        self._catalog = entries
        self._by_id = {e["text_id"]: e for e in entries}
        self._canon_id_to_fs = None
        self._fs_to_local = {}
        self._spec = spec
        self._cur_states = set()
        self.customer_data = {}
        self.voice_gender = "female"

    _fill_template = staticmethod(lambda tpl, *a, **k: tpl)


def _catalog_of(spec):
    out = []
    def walk(node):
        if isinstance(node, dict):
            fs = node.get("_fine_state") or node.get("fine_state")
            if isinstance(node.get("text_id"), int) and fs:
                out.append({"text_id": node["text_id"], "_fine_state": fs,
                            "template": node.get("template", "")})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(spec)
    return out


@pytest.mark.parametrize("name", sorted(SPECS))
def test_unknown_text_id_is_reported_not_swallowed(name):
    """id ที่ไม่มีในคลังต้องถูกคืนออกมาให้ด่าน reject ไม่ใช่หายไปเงียบๆ"""
    from demo_v2.server.sessions import FlowLiveSession as FlowSession
    entries = _catalog_of(SPECS[name])
    if not entries:
        pytest.skip("สเปคไม่มี template ที่มี text_id")
    sess = _Cat(SPECS[name], entries)
    real = entries[0]["text_id"]
    fake = 1222 if 1222 not in sess._by_id else 4111
    ids, text, dyn, unknown = FlowSession._render_reply(sess, {"text_ids": [real, fake]})
    assert ids == [real], f"{name}: id ที่มีจริงต้องยังพูดได้"
    assert unknown == [fake], f"{name}: id ปลอมต้องถูกรายงาน ไม่ใช่ทิ้งเงียบ"


@pytest.mark.parametrize("name", sorted(SPECS))
def test_reachable_ids_hint_only_names_real_ids(name):
    """hint ตอน reject ต้องเสนอเฉพาะ id ที่มีอยู่จริงในคลังของบริษัทนั้น"""
    from demo_v2.server.sessions import FlowLiveSession as FlowSession
    entries = _catalog_of(SPECS[name])
    if not entries:
        pytest.skip("สเปคไม่มี template ที่มี text_id")
    sess = _Cat(SPECS[name], entries)
    hint = FlowSession._reachable_ids(sess)
    for chunk in filter(None, (c.strip() for c in hint.split(","))):
        tid = chunk.split(" ")[0]
        assert int(tid) in sess._by_id, f"{name}: hint เสนอ id ที่ไม่มีจริง ({tid})"


# --- พรอมป์แบบ spread_cache -------------------------------------------------
# ย้าย CRM ไปท้ายสุด + คลี่ text_id ให้กระจาย เพื่อให้ทุกสายของบริษัทเดียวกันใช้
# prefix cache ร่วมกันได้ (วัดบน gold 43: พรอมป์ที่เหมือนกัน 1.5% → 82.4%)

@pytest.mark.parametrize("name", sorted(SPECS))
def test_crm_moves_to_the_end_without_losing_content(name):
    """crm="omit" ต้องตัด CRM ออกครบ และ render_crm_block ต้องคืนของเดิมเป๊ะ"""
    from demo_v2.server.flow.flowspec_render import render_crm_block, render_instruction
    spec = dict(SPECS[name])
    spec.setdefault("company", name)
    if not spec.get("crm_fields"):
        pytest.skip("สเปคไม่มี crm_fields")
    inline = render_instruction(spec, crm="inline")
    omit = render_instruction(spec, crm="omit")
    block = render_crm_block(spec)
    assert "CRM Snapshot" in inline and "CRM Snapshot" not in omit, name
    # เนื้อหารวมเท่าเดิม แค่ย้ายที่
    assert sorted((omit + "\n\n" + block).split()) == sorted(inline.split()), name
    for field in spec["crm_fields"]:
        assert "{%s}" % field in block, f"{name}: ขาด {field}"


@pytest.mark.parametrize("name", sorted(SPECS))
def test_spread_ids_are_stable_unique_and_unguessable(name):
    """คลี่เลขแล้วต้อง (1) ไม่ชนกัน (2) ได้เลขเดิมทุกครั้ง (3) ไม่เรียงต่อกันเป็นบล็อก"""
    from demo_v2.server.sessions import spread_text_ids
    entries = _catalog_of(SPECS[name])
    if len(entries) < 3:
        pytest.skip("คลังเล็กเกินกว่าจะวัดการกระจาย")
    a = spread_text_ids(entries)
    b = spread_text_ids(entries)
    ids = [e["text_id"] for e in a]
    assert [e["text_id"] for e in b] == ids, f"{name}: เรียกสองครั้งได้คนละเลข"
    assert len(set(ids)) == len(ids), f"{name}: เลขชนกัน"
    assert all(1000 <= i <= 9999 for i in ids), f"{name}: เลขหลุดช่วง"
    # ของเดิมยังอยู่ครบ แค่เปลี่ยนเลข
    assert [e["_fine_state"] for e in a] == [e["_fine_state"] for e in entries], name
    # เดาต่อไม่ได้: คู่ที่ติดกันในคลังต้องไม่ห่างกัน 1 เกินครึ่ง
    adjacent = sum(1 for x, y in zip(ids, ids[1:]) if abs(x - y) == 1)
    assert adjacent <= len(ids) // 2, f"{name}: ยังเรียงต่อกันเป็นบล็อก ({adjacent}/{len(ids)-1})"


def test_remap_rewrites_rule_ids_simultaneously_and_leaves_other_numbers_alone():
    """กฎอ้าง text_id ตรง ๆ — คลี่เลขแล้วต้องแก้ในกฎด้วย และห้ามแตะตัวเลขอื่น

    เคสสลับ (1018→1020, 1020→1018) จับการแทนแบบไล่ทีละตัว ซึ่งจะพัง: แทน 1018 เป็น
    1020 ก่อน แล้วรอบถัดไปแทน 1020 (ที่เพิ่งเขียน) กลับเป็น 1018"""
    from demo_v2.server.sessions import remap_ids_in_text
    m = {1018: 1020, 1020: 1018}
    assert remap_ids_in_text("ใช้ 1018 ก่อน 1020", m) == "ใช้ 1020 ก่อน 1018"
    # ตัวเลขที่ไม่ใช่ id ต้องไม่ถูกแตะ แม้จะมีเลข id ซ่อนอยู่ข้างใน
    keep = "ยอด 45000 บาท เบอร์ 02-035-6666 ปี 2026 บัตร 10188 เลข 11018"
    assert remap_ids_in_text(keep, {1018: 4271}) == keep
    assert remap_ids_in_text("(1018)", {1018: 4271}) == "(4271)"
    assert remap_ids_in_text("1020/1021/1023", {1020: 5000, 1023: 6000}) == "5000/1021/6000"


@pytest.mark.parametrize("name", sorted(SPECS))
def test_spread_leaves_no_stale_id_in_the_rules(name):
    """หลังคลี่เลข ต้องไม่มี id เดิมหลงเหลือในตัวคำสั่ง — ไม่งั้นพรอมป์สั่งขัดกับคลัง"""
    from demo_v2.server.flow.flowspec_render import render_instruction
    from demo_v2.server.sessions import remap_ids_in_text, spread_text_ids
    spec = dict(SPECS[name]); spec.setdefault("company", name)
    entries = _catalog_of(SPECS[name])
    if not entries:
        pytest.skip("สเปคไม่มี template ที่มี text_id")
    spread = spread_text_ids(entries)
    mapping = {o["text_id"]: n["text_id"] for o, n in zip(entries, spread)
               if o["text_id"] != n["text_id"]}
    instr = remap_ids_in_text(render_instruction(spec, crm="omit"), mapping)
    new_ids = {e["text_id"] for e in spread}
    stale = [o for o in mapping if re.search(r"(?<![0-9])%d(?![0-9])" % o, instr)
             and o not in new_ids]
    assert not stale, f"{name}: กฎยังอ้าง id เดิม {stale[:5]}"


@pytest.mark.parametrize("name", sorted(SPECS))
def test_spread_preserves_rank_and_leaves_no_adjacent_pair(name):
    """คลี่เลขต้องคงอันดับ (เดิมน้อยกว่า → ใหม่น้อยกว่า) และไม่มีคู่ที่ห่างกัน 1

    อันดับ: วัดแล้วว่าโมเดลใช้ข้อมูลนี้ — เวอร์ชันที่เหวี่ยงอันดับทิ้งทำให้มันเปิดสาย
    ด้วยการแจ้งยอดโดยไม่ยืนยันตัวตน ส่วนช่องว่าง: ถ้าเลขติดกันก็เดาตัวถัดไปได้"""
    from demo_v2.server.sessions import spread_text_ids
    entries = _catalog_of(SPECS[name])
    if len(entries) < 3:
        pytest.skip("คลังเล็กเกินไป")
    pairs = [(int(o["text_id"]), n["text_id"]) for o, n in zip(entries, spread_text_ids(entries))]
    ranked = sorted(pairs)
    assert [n for _, n in ranked] == sorted(n for _, n in pairs), f"{name}: อันดับเพี้ยน"
    new = sorted({n for _, n in pairs})
    assert not [1 for a, b in zip(new, new[1:]) if b - a == 1], f"{name}: ยังมีเลขติดกัน"
    assert all(1000 <= n <= 9999 for n in new), f"{name}: หลุดช่วง"


@pytest.mark.parametrize("name", sorted(SPECS))
def test_beat_names_never_collide_with_tool_names(name):
    """แอปแปลง 'tool ที่ชื่อตรงกับ beat' เป็น reply — ชื่อจึงต้องไม่ชนกับ tool จริง

    ถ้าสเปคไหนตั้งชื่อ beat ซ้ำกับ tool ของตัวเอง การกู้จะไปกลืน tool call จริง"""
    spec = SPECS[name]
    beats = {e["_fine_state"] for e in _catalog_of(spec)}
    tools = {d.get("name") for d in (spec.get("tools") or {}).get("declarations", [])}
    assert not (beats & tools), f"{name}: ชื่อชนกัน {sorted(beats & tools)}"


def test_mock_date_window_is_not_stale():
    """mock ที่ตรึงหน้าต่างวันที่ไว้ต้องยังครอบวันนี้ ไม่งั้น SHOP จะปฏิเสธทุกวันที่

    Mockoon เลือก response ด้วย regex ที่ template ไม่ได้ gen_mockoon.py จึงกาง
    due_date + max_extend_days เป็นลิสต์วันที่ตอน generate ถ้าไม่ regenerate ลิสต์จะ
    ค้างในอดีต ทุกวันที่ลูกค้าเสนอหล่นไป response default (`in_range: false`) แล้ว
    เจ้าหน้าที่พูด date_too_far ทั้งที่ยังไม่เกิน — เกิดจริง ค้าง 10 วันโดยไม่มีใครรู้
    เพราะมันดูเหมือนโมเดลตัดสินผิด ไม่เหมือนระบบพัง
    """
    import datetime
    import subprocess
    root = pathlib.Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, str(root / "tools" / "check_mock_fresh.py")],
                       capture_output=True, text=True, cwd=root)
    assert r.returncode == 0, (
        "mock ค้าง (วันนี้ %s) — รัน `python3 tools/gen_mockoon.py` แล้วรีสตาร์ต mockoon\n%s"
        % (datetime.date.today(), r.stdout))


@pytest.mark.parametrize("name", sorted(SPECS))
def test_instruction_pane_matches_the_prompt_scheme(name):
    """ปุ่มอ่าน instruction ต้องประกอบด้วยวิธีเดียวกับที่ป้อนโมเดล

    ก่อนแก้: ปุ่มเรนเดอร์ CRM ไว้หัวและใช้ text_id ของสเปค ขณะที่โมเดลได้รับ CRM ท้าย
    และคลังที่คลี่เลขแล้ว — คนที่เปิดอ่านเพื่อหาสาเหตุว่าทำไมเจ้าหน้าที่พูดประโยคนั้น
    จึงอ่านพรอมป์ที่เจ้าหน้าที่ไม่เคยเห็น
    """
    from demo_v2.server import sessions as S
    if name not in S.load_flow_registry():
        pytest.skip("ไม่ได้อยู่ใน registry")
    txt = S.flow_instruction(name)
    assert txt, f"{name}: ปุ่มไม่คืนอะไร"
    if S._PROMPT_CRM_LAST and "CRM Snapshot" in txt:
        head, _, tail = txt.partition("CRM Snapshot")
        assert "Available Pre-Scripts" in head or "text_id" in head, \
            f"{name}: CRM ต้องอยู่หลังคลัง"
        assert len(tail) < len(head), f"{name}: CRM ไม่ได้อยู่ท้ายสุด"
    if S._PROMPT_SPREAD:
        spec = dict(SPECS[name]); spec.setdefault("company", name)
        entries = _catalog_of(spec)
        if entries:
            spread = {e["text_id"] for e in S.spread_text_ids(entries)}
            orig = {e["text_id"] for e in entries}
            shown = {int(x) for x in re.findall(r"(?<![0-9])(\d{4})(?![0-9])", txt)}
            # id ของสเปคที่ถูกคลี่ไปแล้วต้องไม่โผล่ในหน้าอ่าน
            leaked = (orig - spread) & shown
            assert not leaked, f"{name}: หน้าอ่านยังโชว์ id เดิม {sorted(leaked)[:5]}"


def _shipped_tenants() -> set | None:
    """tenant ที่ ship มากับ repo (git ติดตามอยู่) — None ถ้าตอบไม่ได้

    เอกสารพูดถึงบริษัทที่มากับระบบ ส่วนบริษัทที่ผู้ใช้สร้างเองจากหน้า Upload เป็นของ
    เครื่องนั้น ไม่ใช่ของเอกสาร ก่อนมีตัวกรองนี้ การกด Upload สร้างบริษัทเล่นหนึ่งตัว
    ทำให้เทสต์เอกสารแดงทันที ซึ่งอ่านเหมือนสเปคที่เพิ่งเขียนผิด
    """
    import subprocess
    try:
        out = subprocess.run(["git", "ls-files", "data/flows"],
                             cwd=pathlib.Path(__file__).resolve().parents[1],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return {pathlib.Path(line).name.replace(".company.json", "")
            for line in out.stdout.split() if line.endswith(".company.json")}


def test_manual_chain_list_matches_the_code():
    """คู่มือเขียนรายชื่อ chain state ไว้ — ต้องตรงกับ is_chain_state() เสมอ

    รายการนี้ผมเขียนผิดสองครั้งจากการนับ template เอง (นับว่า convince เป็น chain
    ทั้งที่ template มี when_event = ตัวเลือกตามเหตุการณ์) เทสต์นี้กันไม่ให้เอกสาร
    หลุดจากโค้ดเงียบ ๆ อีก · เทียบทีละบรรทัดของ tenant เพราะ state ชื่อเดียวกันเป็น chain
    ในบริษัทหนึ่งแต่ไม่เป็นในอีกบริษัทได้ (`disclose_ask` เป็น chain ของ SKL ไม่ใช่ของ AEON)
    """
    from demo_v2.server.flow.flowspec import is_chain_state
    manual = (pathlib.Path(__file__).resolve().parents[1] / "demo_v2" / "docs" / "MANUAL.md").read_text(encoding="utf-8")
    # หาบล็อกจากเนื้อหา (มีชื่อ tenant อยู่ในนั้น) ไม่ใช่จากประโยคนำ — คู่มือถูกเกลาคำ
    # ได้ตลอด และเทสต์นี้มีหน้าที่ตรวจว่ารายชื่อ chain ตรงกับโค้ด ไม่ใช่ตรึงถ้อยคำ
    fences = re.findall(r"```[a-z]*\n(.*?)```", manual, re.S)
    cands = [f for f in fences if sum(1 for n in SPECS if n in f) >= 3]
    assert len(cands) == 1, f"หาบล็อกรายชื่อ chain ไม่เจอ/เจอหลายอัน ({len(cands)})"
    block = cands[0]
    listed: dict = {}
    for line in block.strip().split("\n"):
        # คอลัมน์ tenant กับคอลัมน์ id คั่นด้วยช่องว่างตั้งแต่สองตัว
        parts = re.split(r"\s{2,}", line.strip(), maxsplit=1)
        if not parts or not parts[0]:
            continue
        head, rest = parts[0], (parts[1] if len(parts) > 1 else "")
        names = [x for x in head.replace("/", " ").split() if x]
        ids = [] if "ไม่มี" in rest else [x for x in rest.replace("·", " ").split() if x]
        for n in names:
            listed[n] = ids
    shipped = _shipped_tenants()
    for name, spec in SPECS.items():
        if name == "_TEMPLATE":
            continue
        if shipped is not None and name not in shipped:
            continue                     # บริษัทที่สร้างเองในแอป ไม่ใช่ของเอกสาร
        chains = sorted(s["id"] for s in spec.get("states", []) if is_chain_state(s))
        assert name in listed, f"{name}: คู่มือไม่ได้ลิสต์ tenant นี้"
        assert sorted(listed[name]) == chains, \
            f"{name}: คู่มือว่า {sorted(listed[name])} แต่โค้ดว่า {chains}"


# --- ทุกสำนวนต้องพูดออกไปได้ ไม่ใช่แค่สำนวนที่โมเดลชอบหยิบ --------------------
# beat หนึ่งมีได้หลายสำนวน (AEON `ask_pay_today` มี 6) และแต่ละสำนวนอ้าง slot ไม่เหมือนกัน
# สำนวนที่อ้าง slot ที่ไม่มีใครเติมได้จะพูด `[bracket]` ออกไมค์ — แต่โผล่แค่ตอนโมเดลหยิบ
# มันขึ้นมา จึงดูเหมือนเป็นความสุ่ม เจอจริง: AEONLITE ประกาศ company_phone ใน crm_fields
# และ 10 สำนวนพูดถึงมัน แต่ persona ไม่มีค่านั้น

def _mock_init_rows() -> dict:
    """CRM ที่ mock ตอบให้แต่ละบริษัท — ใช้เป็นข้อมูลจริงที่สุดที่เทสต์เข้าถึงได้"""
    root = pathlib.Path(__file__).resolve().parents[1]
    doc = json.loads((root / "mock" / "aax6-mock.json").read_text(encoding="utf-8"))
    out: dict = {}

    def walk(node):
        if isinstance(node, dict):
            if node.get("endpoint") == ":company/init":
                for r in node.get("responses") or []:
                    comp = next((x.get("value") for x in (r.get("rules") or [])
                                 if x.get("modifier") == "company"), None)
                    if comp and comp not in out:
                        try:
                            out[comp] = json.loads(r.get("body") or "{}")
                        except json.JSONDecodeError:
                            pass
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc)
    return out


@pytest.mark.parametrize("name", sorted(SPECS))
def test_every_wording_can_be_spoken(name):
    """เรนเดอร์ทุกสำนวนในคลังด้วย CRM จริงของบริษัทนั้น — ต้องไม่มี slot ค้าง

    slot ที่ tool คืนมาตอนรันไม่นับ (ประกาศไว้ใน `returns`/`args` ของ tool) — ซึ่งเป็น
    เหตุผลที่ tool ต้องประกาศ `returns` ให้ครบ ไม่งั้นแยก "slot ที่ถูกต้อง" ออกจาก
    "slot ที่พิมพ์ผิด" ไม่ได้เลย
    """
    import contextlib
    import io
    from demo_v2.lib.prescript import fill_template
    from demo_v2.server.flow.flowspec import normalize_catalog, resolve_catalog

    if name == "_TEMPLATE":
        pytest.skip("ไฟล์ตัวอย่าง ไม่มี persona/mock ของตัวเอง")
    spec = dict(SPECS[name]); spec.setdefault("company", name)
    catalog = normalize_catalog(resolve_catalog(spec), spec)
    if not catalog:
        pytest.skip("ไม่มีคลัง")
    known = set()
    for t in (spec.get("tools") or {}).get("declarations", []):
        known |= set((t.get("returns") or {}).keys())
        known |= set((t.get("args") or {}).keys())
    # CRM ประกอบแบบเดียวกับที่ session ทำ: persona เป็น seed แล้ว session_init ทับ
    # (sessions.py: `fetch_context(seed=self.customer_data)` แล้ว `customer_data.update`)
    # เดิมอ่านจาก mock ที่เดียว บริษัทที่สร้างจากหน้า Upload จึงดูเหมือน CRM ว่างเปล่า
    # ทั้งที่ข้อมูลอยู่ใน persona ของมัน — mock ยังไม่ถูก regenerate เท่านั้น
    from demo_v2.server import sessions as _s
    crm: dict = {}
    try:
        crm.update(_s.case_customer_data(f"TC-{name}-BUILD-001"))
    except Exception:      # noqa: BLE001 — ไม่มี persona ของตัวเองก็ไม่เป็นไร
        pass
    _s._resolve_offset_dates(crm)
    crm.setdefault("today", "2026-01-01 (Thursday)")   # session เติมให้ทุกสาย
    crm.update(_mock_init_rows().get(name) or {})
    leaked: dict = {}
    with contextlib.redirect_stderr(io.StringIO()):
        for e in catalog:
            out = fill_template(e["template"], crm, gender="F")
            for ph in re.findall(r"[\[\{]([a-z_0-9]+)[\]\}]", out):
                if ph not in known:
                    leaked.setdefault(ph, []).append(e["text_id"])
    assert not leaked, (
        "%s: สำนวนพูด slot ที่เติมไม่ได้ %s — เติมค่าใน persona/mock หรือประกาศใน tool.returns"
        % (name, {k: v[:4] for k, v in leaked.items()}))


def test_spec_locked_matches_the_code():
    """SPEC_LOCKED.md ต้องตรงกับค่าคงที่ในโค้ด — เอกสารเล่มนี้บอกว่าตัวเอง LOCKED และคน
    ใช้มันเป็นแหล่งอ้างอิงตอนเขียนสเปคใหม่ พอคลาดจากโค้ด คนที่ทำตามจะเจอ error ที่อ่าน
    ไม่รู้เรื่อง (เจอสองจุดในวันเดียว: `events` รูปผิด → renderer พังเป็น AttributeError ·
    `enforce` บอก backend เลิกใช้แต่ validator ยังรับ)"""
    import subprocess
    root = pathlib.Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, str(root / "tools" / "check_doc_matches_code.py")],
                       capture_output=True, text=True, cwd=root)
    assert r.returncode == 0, r.stdout


def test_flow_id_default_is_the_filename_and_says_so_in_both_docs():
    """`flow_id` เคยถูกแต่งด้วยสามสูตรสองแบบ (`-outbound-call` ที่ flowspec ·
    `-outbound-remind` สองที่ใน sessions) สเปคเดียวจึงได้ id ต่างกันตามประตูที่เข้ามา
    และ "remind" เป็นคำของงานทวงหนี้ในแอปที่รับทั้งคลินิกและร้านค้า ตอนนี้เหลือสูตรเดียว
    คือชื่อไฟล์ — เทสต์นี้กันไม่ให้ใครแต่งคำต่อท้ายกลับมา และกันเอกสารคลาดจากโค้ด"""
    import json
    import pathlib as _p
    import tempfile

    from demo_v2.server.flow.flowspec import load_tenant_spec

    with tempfile.TemporaryDirectory() as d:
        f = _p.Path(d) / "ZZ.company.json"
        f.write_text(json.dumps({"events": {}, "states": [], "tools": {},
                                 "faq_routing": {"routes": []}, "constraints": []}),
                     encoding="utf-8")
        spec = load_tenant_spec(f)
    assert spec["flow_id"] == "ZZ", spec["flow_id"]
    assert spec["company"] == "ZZ"

    src = _p.Path(__file__).resolve().parents[1] / "demo_v2"
    for py in src.rglob("*.py"):
        if "__pycache__" in str(py):
            continue
        body = py.read_text(encoding="utf-8")
        for line in body.split("\n"):
            if "flow_id" in line and "-outbound-" in line and not line.strip().startswith(("#", "`")):
                raise AssertionError(f"{py.name}: แต่งคำต่อท้าย flow_id กลับมา — {line.strip()}")

    docs = _p.Path(__file__).resolve().parents[1] / "demo_v2" / "docs"
    for name in ("SPEC_LOCKED.md", "SPEC_LOCKED.en.md"):
        text = (docs / name).read_text(encoding="utf-8")
        line = next(l for l in text.split("\n") if "`flow_id`" in l and "setdefault" in l)
        assert "-outbound-call" not in line, f"{name}: เอกสารยังบอกสูตรเดิม — {line.strip()}"
