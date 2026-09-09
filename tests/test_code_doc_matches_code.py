"""`demo_v2/docs/CODE.md` ต้องอ้างของที่มีอยู่จริง — ไม่ใช่ของที่เคยมี

เอกสารอธิบายโค้ดชุดก่อน (`CODE_MAP.md` · `FLOW_WALKTHROUGH.md` · `DIAGRAMS.md`) ถูกยุบ
ทิ้งไปเพราะ "ล้าหลังโค้ดไปแล้วตอนที่มีคนอ่าน" เทสต์นี้มีไว้ให้ฉบับใหม่ไม่ซ้ำรอยนั้น:
ทุกไฟล์ ฟังก์ชัน ค่าคงที่ และรหัสด่านที่เอกสารเอ่ยชื่อ ต้องหาได้ในซอร์สวันนี้

ตัวเลขบรรทัดในหัวข้อ B1 ยอมให้คลาด 10% — เพื่อบอกว่าแฟ้มไหนหนัก ไม่ใช่เพื่อความเป๊ะ
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = ROOT / "demo_v2" / "docs" / "CODE.md"
TEXT = DOC.read_text(encoding="utf-8")
SRC = "\n".join(p.read_text(encoding="utf-8")
                for p in sorted((ROOT / "demo_v2").rglob("*.py"))
                if "__pycache__" not in str(p))

# ชื่อที่ไม่ได้อยู่ในซอร์สโดยตรง: ชื่อรูปแบบ (`<ชื่อ>_…`), คีย์ของสเปค, ตัวอย่างข้อมูล
SKIP = {
    "reply", "spec", "catalog", "hop", "kind", "text_id", "_fine_state", "fine_state",
    "template", "hint", "gating", "url", "impl", "http", "generic", "initial", "true",
    "company", "if", "for", "break", "continue", "dict", "list", "amount",
}


def _backticked(pattern: str) -> set[str]:
    return set(re.findall(pattern, TEXT))


def test_the_doc_exists_and_has_both_parts():
    assert "# A · While demoing" in TEXT
    assert "# B · Handing over the code" in TEXT


@pytest.mark.parametrize("path", sorted(_backticked(r"([a-z_0-9/]+\.py)")))
def test_every_file_the_doc_names_exists(path):
    name = path.split("/")[-1]
    assert list((ROOT / "demo_v2").rglob(name)) or (ROOT / path).exists(), path


@pytest.mark.parametrize("sym", sorted(
    s for s in _backticked(r"`([A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)?)\(\)`")
    if s.split(".")[-1] not in SKIP))
def test_every_function_the_doc_names_exists(sym):
    """`f()` และ `Class.method()` — เทียบด้วยชื่อสุดท้าย"""
    assert f"def {sym.split('.')[-1]}(" in SRC, sym


# ชื่อเอกสาร/รูปแบบชื่อไฟล์ ไม่ใช่ค่าคงที่ในโค้ด
NOT_CONSTANTS = {"SPEC_LOCKED", "MANUAL", "CODE", "SERVING", "README", "CRM", "NDJSON",
                 "HTTP", "API"}


@pytest.mark.parametrize("const", sorted(
    (_backticked(r"`([A-Z][A-Z_0-9]{3,})`") | _backticked(r"`(FLOW_MAX_TOOL_LOOPS) = \d+`"))
    - NOT_CONSTANTS))
def test_every_constant_the_doc_names_exists(const):
    """ค่าคงที่ระดับโมดูล หรือชื่อ env var ที่โค้ดอ่านเป็นสตริง — ต้องมีอยู่จริงทั้งสองแบบ"""
    assert (re.search(rf"^{const}\b", SRC, re.M)
            or f"{const}(" in SRC
            or f'"{const}"' in SRC), const


@pytest.mark.parametrize("attr", sorted(
    s for s in _backticked(r"`(_[a-z][a-z_0-9]+)`") if s not in SKIP))
def test_every_attribute_the_doc_names_exists(attr):
    assert attr in SRC, attr


def test_the_reply_gate_table_lists_exactly_the_codes_the_code_emits():
    src = (ROOT / "demo_v2/server/sessions.py").read_text(encoding="utf-8")
    # สองรูป: ใส่ตรงใน dict (`"reason": "x"`) และผูกกับตัวแปรก่อน (`_reason = "x"`)
    # รูปที่สองเคยหลุด — `closing_reply_required` จึงไม่อยู่ในตารางตั้งแต่ฉบับแรก
    emitted = set(re.findall(r'"reason": "([a-z_]+)"', src)) | \
              set(re.findall(r'_reason\s*=\s*"([a-z_]+)"', src))
    # ยอมให้จัดคอลัมน์ด้วยช่องว่างได้ — เทสต์ตัวนี้ตรวจข้อเท็จจริง ไม่ใช่การจัดรูป
    listed = set(re.findall(r"^\|\s*`([a-z_]+)`\s*\|", TEXT, re.M))
    assert emitted <= listed, f"เอกสารยังไม่พูดถึง: {sorted(emitted - listed)}"


def test_every_tool_gate_code_in_the_doc_is_produced_somewhere():
    gate = (ROOT / "demo_v2/server/flow/spec_gate.py").read_text(encoding="utf-8")
    backend = (ROOT / "demo_v2/server/flow/spec_backend.py").read_text(encoding="utf-8")
    for code in ("call_already_closed", "commitment_mismatch", "missing_required_args",
                 "http_error", "http_no_url"):
        assert code in gate + backend, code
    # รหัสที่ประกอบจากชื่อเครื่องมือ — เอกสารเขียนเป็นรูปแบบ ต้องมีที่มาในโค้ด
    assert '_already_recorded"' in gate and 'f"no_{req}"' in gate


def test_the_line_counts_are_still_roughly_right():
    off = []
    for name, claimed in re.findall(r"^demo_v2/(\S+\.py)\s+(\d+)", TEXT, re.M) + \
                         re.findall(r"^(server/\S+\.py|lib/\S+\.py)\s+(\d+)", TEXT, re.M):
        p = ROOT / "demo_v2" / name
        if not p.exists():
            continue
        real = len(p.read_text(encoding="utf-8").splitlines())
        if abs(real - int(claimed)) > max(20, real * 0.10):
            off.append(f"{name}: เอกสารว่า {claimed} จริง {real}")
    assert not off, off


def test_the_docs_point_at_each_other():
    for other in ("MANUAL.md", "SPEC_LOCKED.md", "SERVING.md", "README.md"):
        assert other in TEXT, other


def test_the_sequence_diagrams_are_well_formed():
    """ผัง mermaid ต้องปิดบล็อกครบและไม่อ้าง participant ที่ไม่ได้ประกาศ

    ผังที่ syntax ผิดจะไม่ขึ้นเลยตอน render และไม่มีอะไรเตือน — เห็นเป็นกรอบว่างเปล่า
    """
    blocks = re.findall(r"```mermaid\n(.*?)```", TEXT, re.S)
    assert len(blocks) >= 2, "ควรมีผังลำดับทั้งการเปิดสายและการเดินหนึ่งเทิร์น"
    for i, b in enumerate(blocks, 1):
        lines = [l.rstrip() for l in b.split("\n") if l.strip()]
        assert lines[0].strip() == "sequenceDiagram", f"#{i}: {lines[0]}"
        opens = sum(1 for l in lines if re.match(r"\s*(loop|alt|opt|par|rect)\b", l))
        ends = sum(1 for l in lines if re.match(r"\s*end\s*$", l))
        assert opens == ends, f"#{i}: เปิด {opens} ปิด {ends}"
        declared = set(re.findall(r"participant (\w+) as ", b))
        used = (set(re.findall(r"^\s*(\w+)\s*(?:->>|-->>)", b, re.M))
                | set(re.findall(r"(?:->>|-->>)\s*(\w+)\s*:", b))
                | {x.strip() for n in re.findall(r"Note (?:over|right of|left of) ([\w, ]+)", b)
                   for x in n.split(",")})
        assert used <= declared, f"#{i}: ไม่ได้ประกาศ {sorted(used - declared)}"
        assert declared <= used, f"#{i}: ประกาศแล้วไม่ใช้ {sorted(declared - used)}"

        # `<CODE>` ในข้อความทำให้ผัง B2 ไม่ขึ้นเลย — mermaid เรนเดอร์ข้อความเป็น HTML
        # จึงอ่าน `<...>` เป็นแท็ก และ label ของ alt/opt ไม่ได้เรนเดอร์เป็น HTML ด้วย
        # การใส่ &lt; จึงโชว์ตัวอักษรดิบ ⇒ ห้ามมี < > ในข้อความ ยกเว้น <br/>
        arrow = re.compile(r"^\s*\w+\s*(?:->>|-->>|->|-->)\s*\w+\s*:\s*(.*)$")
        label = re.compile(r"^\s*(?:alt|else|opt|loop|par|and|rect)\b\s*(.*)$")
        note = re.compile(r"^\s*Note\s+(?:over|right of|left of)\s+[\w, ]+:\s*(.*)$")
        for line in lines:
            for pat in (arrow, label, note):
                m = pat.match(line)
                if not m:
                    continue
                text = m.group(1).replace("<br/>", "")
                assert "<" not in text and ">" not in text, f"#{i}: {line.strip()}"
                # `;` ปิดคำสั่งของ mermaid — ข้อความจะถูกตัดตรงนั้นและส่วนที่เหลือ
                # กลายเป็นคำสั่งขยะ ทำให้ผังทั้งผังไม่ขึ้น (นี่คือเหตุที่ผัง B2 พัง)
                assert ";" not in text, f"#{i}: มี ; ในข้อความ — {line.strip()}"
                break


def test_no_thai_prose_is_left_in_demo_v2_comments():
    """คอมเมนต์/docstring ในโค้ดเป็นภาษาอังกฤษ — ยกเว้นข้อความไทยที่เป็น *ตัวอย่างข้อมูล*

    ชุดส่งมอบไปถึงคนที่ไม่ได้อ่านภาษาไทย คำอธิบายจึงต้องเป็นอังกฤษ แต่ประโยคที่ระบบ
    พูดจริง ชื่อในทะเบียนลูกค้า และคำลงท้าย (ครับ/ค่ะ) ต้องคงเป็นไทย เพราะนั่นคือ
    เนื้อหาที่คอมเมนต์กำลังอธิบาย ⇒ วัดด้วยสัดส่วน: ไทยเกินหนึ่งในสามของตัวอักษร
    ทั้งหมดในคอมเมนต์นั้น = เป็นเนื้อความ ไม่ใช่ตัวอย่าง
    """
    import ast as _ast
    import io as _io
    import tokenize as _tok

    thai = re.compile(r"[฀-๿]")
    quoted = re.compile(r"\"[^\"]*\"|'[^']*'|`[^`]*`|«[^»]*»")

    def prose(text: str) -> bool:
        """ไทยที่อยู่ *นอก* เครื่องหมายคำพูดคือคำอธิบาย · ในเครื่องหมายคำพูดคือตัวอย่างข้อมูล"""
        bare = quoted.sub(" ", text)
        letters = [c for c in bare if c.isalpha()]
        return bool(letters) and sum(1 for c in letters if thai.match(c)) / len(letters) >= 0.35

    offenders = []
    for path in sorted((ROOT / "demo_v2").rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        src = path.read_text(encoding="utf-8")
        for t in _tok.generate_tokens(_io.StringIO(src).readline):
            if t.type == _tok.COMMENT and prose(t.string):
                offenders.append(f"{path.name}:{t.start[0]}")
        for node in _ast.walk(_ast.parse(src)):
            if isinstance(node, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                if prose(_ast.get_docstring(node) or ""):
                    offenders.append(f"{path.name}:{node.lineno} ({getattr(node, 'name', '<module>')})")
    assert not offenders, offenders


def test_no_checkpoint_name_of_ours_is_baked_into_the_deliverable():
    """ไม่มีชื่อ checkpoint ของเครื่องเราเป็นค่าปริยาย และไม่อ้างว่าโมเดลถูกเทรนด้วย RL

    ชื่อ `grpo540`/`grpo400` เคยเป็นค่าปริยายทั้งใน serve_aax6.sh · FLOW_MODEL ·
    SERVING.md · tools — บนเครื่องคนอื่นชื่อนั้นไม่มีอยู่ แล้ว vLLM ตอบ 404 ทำให้เทิร์น
    จบเงียบ · และโมเดลที่ส่งมอบเป็น SFT ล้วน คำอธิบายที่เขียนว่า RL/GRPO จึงผิด
    """
    banned = ("grpo540", "grpo400", "RL-trained", "GRPO reward")
    roots = [ROOT / "demo_v2", ROOT / "tools", ROOT / "README.md"]
    offenders = []
    for root in roots:
        files = [root] if root.is_file() else [
            p for p in root.rglob("*")
            if p.is_file() and p.suffix in (".py", ".sh", ".md", ".ts", ".tsx", ".sample")
            and "__pycache__" not in str(p) and "node_modules" not in str(p)]
        for p in files:
            text = p.read_text(encoding="utf-8", errors="ignore")
            for word in banned:
                if word in text:
                    offenders.append(f"{p.relative_to(ROOT)}: {word}")
    assert not offenders, offenders
