#!/usr/bin/env python3
"""SPEC_LOCKED.md ต้องตรงกับค่าคงที่ในโค้ด — ไม่ใช่ตรงกับความจำของคนเขียน

เอกสารเล่มนี้บอกว่าตัวเอง LOCKED และคนใช้มันเป็นแหล่งอ้างอิงตอนเขียนสเปคใหม่ พอมันคลาด
จากโค้ด คนที่ทำตามจะเจอ error ที่อ่านไม่รู้เรื่อง — เจอมาแล้วสองจุดในวันเดียว:

  · `events` เขียนว่า `{ชื่อ: คำอธิบาย}` แต่โค้ดต้องการ `{ชื่อ: {desc, cues}}`
    ทำตามเอกสารแล้ว renderer พังเป็น AttributeError ตอน build session
  · `enforce` หัวข้อว่า "3 ค่า" ตารางลิสต์ 2 เนื้อหาบอก `backend` เลิกใช้ แต่ validator
    ยังรับ `backend` อยู่ — ประกาศแล้วเข้าใจผิดว่ามีอะไรบังคับให้

รัน: PYTHONPATH=. python3 tools/check_doc_matches_code.py   (exit 1 = เอกสารคลาด)
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from demo_v2.server.flow import flowspec as F  # noqa: E402
from demo_v2.server.flow import flowspec_render as R  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

# เอกสารอ้างอิงมีสองภาษา และต้องตรงกับโค้ด **ทั้งคู่** — ฉบับแปลคือที่ที่ข้อเท็จจริง
# หลุดง่ายที่สุด เพราะคนแก้ฉบับต้นทางแล้วไม่ได้แก้อีกฉบับ
DOCS = {
    # เอกสารอ้างอิงมีสองภาษา ต้องตรงกับโค้ด **ทั้งคู่** — ฉบับแปลคือที่ที่ข้อเท็จจริง
    # หลุดง่ายที่สุด เพราะคนแก้ฉบับต้นทางแล้วไม่ได้แก้อีกฉบับ
    #
    # `anchor` = หัวข้อ/ข้อความที่นำหน้าตารางของเรื่องนั้น · ตัวตรวจนับ **จำนวนแถว**
    # ของตารางถัดจาก anchor แล้วเทียบกับความยาวของชุดค่าคงที่จริง — ไม่ยึดตัวเลขใน
    # ถ้อยคำ เพราะเอกสารถูกเกลาคำได้ตลอด (รอบก่อนเปลี่ยน "2 ค่า" เป็น "สองค่า"
    # แล้วตัวตรวจที่อ่านตัวเลขก็แดงทันทีทั้งที่เนื้อหาถูก)
    "SPEC_LOCKED.md": {
        "tables": {"TEMPLATE_KEYS": r"^#+ .*`templates\[\]`",
                   "CONSTRAINT_TYPES": r"^#+ .*Constraint types"},
        "api_base_warning": r"tenant",
    },
    "SPEC_LOCKED.en.md": {
        "tables": {"TEMPLATE_KEYS": r"^#+ .*`templates\[\]`",
                   "CONSTRAINT_TYPES": r"^#+ .*Constraint types"},
        "api_base_warning": r"tenant",
    },
}


def _table_after(doc: str, anchor: str) -> int | None:
    """จำนวนแถวข้อมูลของตารางแรกที่อยู่หลัง anchor (ไม่นับหัวตารางและเส้นคั่น)"""
    m = re.search(anchor, doc, re.M)
    if not m:
        return None
    rows, seen = 0, False
    for line in doc[m.end():].split("\n"):
        s = line.strip()
        if s.startswith("|"):
            seen = True
            if re.match(r"^\|[\s:|-]+\|?$", s) or rows == 0 and not re.match(r"^\|[\s:|-]+", s) and not seen:
                continue
            rows += 1
        elif seen and s and not s.startswith("|"):
            break
    # แถวแรกคือหัวตาราง เส้นคั่นถูกข้ามไปแล้ว
    return max(rows - 1, 0) if rows else None


def _fence_after(doc: str, anchor: str) -> list[str] | None:
    """บรรทัดในบล็อกโค้ดแรกที่อยู่หลัง anchor — ใช้อ่านลิสต์ค่าที่เอกสารประกาศ"""
    m = re.search(anchor, doc, re.M)
    if not m:
        return None
    fence = re.search(r"```[a-z]*\n(.*?)```", doc[m.end():], re.S)
    if not fence:
        return None
    return [l.strip() for l in fence.group(1).split("\n") if l.strip()]


def _bad_table_rows(doc: str):
    """แถวตารางที่จำนวนคอลัมน์ไม่ตรงหัวตาราง (นับ `\\|` เป็นตัวอักษร ไม่ใช่ตัวคั่น)"""
    cols, infence = None, False
    for i, line in enumerate(doc.split("\n"), 1):
        s = line.strip()
        if s.startswith("```"):
            infence = not infence
        if infence:
            continue
        if not s.startswith("|"):
            cols = None
            continue
        n = len(re.sub(r"\\\|", "x", s).strip("|").split("|"))
        if cols is None:
            cols = n
        elif re.match(r"^\|[\s:|-]+\|?$", s):
            continue
        elif n != cols:
            yield i, n, cols


def main() -> int:
    bad: list[str] = []
    for name, pats in DOCS.items():
        bad += check(name, (ROOT / "demo_v2" / "docs" / name).read_text(encoding="utf-8"), pats)
    if not bad:
        print("SPEC_LOCKED ตรงกับโค้ด (ทั้งไทยและอังกฤษ)")
        return 0
    print("SPEC_LOCKED คลาดจากโค้ด %d จุด:" % len(bad))
    for b in bad:
        print("  -", b)
    return 1


def check(name: str, doc: str, pats: dict) -> list[str]:
    bad: list[str] = []

    # ทุก key ที่โค้ดยอมรับ ต้องถูกเอ่ยถึงในเอกสาร — หาแบบมีขอบคำในเอกสารดิบ ไม่บังคับว่า
    # ต้องอยู่ในเครื่องหมายโค้ด เพราะเอกสารเขียนรวมกลุ่มได้ เช่น `{field, equals}`
    for const in ("TOP_KEYS", "STATE_KEYS", "TEMPLATE_KEYS", "CATALOG_KEYS",
                  "CONSTRAINT_KEYS", "GATING_KEYS", "TRANSITION_KEYS",
                  "VERIFIED_WHEN_KEYS", "OUTCOME_KEYS", "CONSTRAINT_TYPES",
                  "KNOWN_IMPLS"):
        for k in getattr(F, const, ()):
            if not re.search(r"(?<![0-9A-Za-z_])%s(?![0-9A-Za-z_])" % re.escape(k), doc):
                bad.append(f"{name} · {const}: `{k}` ไม่ถูกเอ่ยถึงในเอกสาร")

    # กลไกใน template string ต้องถูกเอ่ยถึง — คนเขียน tenant แตะส่วนนี้บ่อยที่สุด และ
    # ทั้ง {q_suffix} {pronoun} {{if}} เคยไม่มีในเอกสารเลยทั้งที่ทุก tenant ใช้อยู่
    from demo_v2.lib import prescript as PS
    for tok in ("{suffix}", "{q_suffix}", "{pronoun}", "{{if", "{{else}}"):
        if tok not in doc:
            bad.append(f"{name} · template: {tok} ไม่ถูกเอ่ยถึงในเอกสาร")
    for const in ("SYSTEM_PLACEHOLDERS", "DYNAMIC_PLACEHOLDERS",
                  "DATE_PLACEHOLDERS", "TIME_PLACEHOLDERS"):
        if const not in doc and not re.search(r"\b%d\b" % len(getattr(PS, const)), doc):
            bad.append(f"{name} · template: ไม่ได้บอกจำนวนของ {const} ({len(getattr(PS, const))})")

    # จำนวนแถวของตาราง ต้องเท่ากับความยาวของชุดค่าคงที่จริง
    for const, anchor in pats["tables"].items():
        rows = _table_after(doc, anchor)
        if rows is None:
            bad.append(f"{name} · ไม่พบตารางของ {const} (anchor: {anchor})")
        elif rows != len(getattr(F, const)):
            bad.append(f"{name} · {const}: ตารางมี {rows} แถว โค้ดมี {len(getattr(F, const))}")

    # `enforce` — จำนวนแถวในตารางต้องเท่ากับที่ validator ยอมจริง
    src = (pathlib.Path(F.__file__)).read_text(encoding="utf-8")
    m = re.search(r"layers <= \{([^}]*)\}", src)
    allowed = sorted(re.findall(r'"([a-z_]+)"', m.group(1))) if m else []
    rows = _table_after(doc, r"^#+ .*`enforce`")
    if not allowed:
        bad.append(f"{name} · อ่านค่า enforce ที่ validator ยอมไม่ได้")
    elif rows is None:
        bad.append(f"{name} · ไม่พบตารางของ `enforce`")
    elif rows != len(allowed):
        bad.append(f"{name} · enforce: ตารางมี {rows} แถว · validator ยอม {allowed}")

    # ลิสต์ key ในบล็อกโค้ดต้องตรงกับชุดจริง **ทั้งสองทาง** — ตัวตรวจเดิมดูแค่ทางเดียว
    # (ทุก key ในโค้ดต้องถูกเอ่ยถึง) จึงปล่อยให้เอกสารลิสต์ `source_ref` เป็น key ของ
    # constraint อยู่นาน ทั้งที่ `CONSTRAINT_KEYS` ไม่มี ⇒ ใครเขียนตามจะถูกปฏิเสธ
    listed_ck = _fence_after(doc, r"^#+ .*`CONSTRAINT_KEYS`")
    if listed_ck is None:
        bad.append(f"{name} · ไม่พบบล็อกที่ลิสต์ `CONSTRAINT_KEYS`")
    else:
        real = set(F.CONSTRAINT_KEYS)
        for k in sorted(set(listed_ck) - real):
            bad.append(f"{name} · CONSTRAINT_KEYS: เอกสารลิสต์ `{k}` ซึ่งโค้ดไม่รับ")
        for k in sorted(real - set(listed_ck)):
            bad.append(f"{name} · CONSTRAINT_KEYS: โค้ดรับ `{k}` แต่เอกสารไม่ได้ลิสต์")

    # ทุกตารางต้องมีจำนวนคอลัมน์เท่าหัวตาราง — `|` ที่ไม่ escape ทำให้แถวเลื่อนทั้งแถว
    # และเรนเดอร์ผิดเงียบ ๆ (เจอ 4 แถวในรอบเกลาคำครั้งล่าสุด)
    for ln, got, want in _bad_table_rows(doc):
        bad.append(f"{name} · บรรทัด {ln}: แถวตารางมี {got} คอลัมน์ หัวตารางมี {want} "
                   f"(ถ้าต้องใช้ `|` ในเซลล์ ให้ escape เป็น \\|)")

    # ค่าของ `phase` — เอกสารเคยเขียน `open` ทั้งที่ renderer วน `opening` ⇒ state ที่
    # เขียนตามเอกสารจะหายจากผังในพรอมป์เงียบ ๆ (ไม่มี validator กันข้อนี้)
    rsrc = (pathlib.Path(R.__file__)).read_text(encoding="utf-8")
    m = re.search(r'for phase in \(([^)]*)\):', rsrc)
    phases = re.findall(r'"([a-z_]+)"', m.group(1)) if m else []
    # ฟังก์ชันที่เอกสารอ้างว่าเป็นเจ้าของลูป ต้องเป็นตัวที่มีลูปจริง — เคยเขียนว่า
    # `_render_state` ซึ่งมีอยู่จริงแต่ไม่ได้วน phase (ตัววนคือ `render_instruction`)
    # ⇒ การเช็คแค่ว่า "ชื่อฟังก์ชันมีอยู่" จับข้อนี้ไม่ได้
    owner, cur = None, None
    for line in rsrc.split("\n"):
        mm = re.match(r"def ([a-z_]+)", line)
        if mm:
            cur = mm.group(1)
        if "for phase in (" in line and owner is None:
            owner = cur
    listed = _fence_after(doc, r"^#+ .*`phase`")
    if not phases:
        bad.append(f"{name} · อ่านค่า phase ที่ renderer รู้จักไม่ได้")
    elif listed is None:
        bad.append(f"{name} · ไม่พบบล็อกที่ลิสต์ค่าของ `phase`")
    else:
        if sorted(listed) != sorted(phases):
            bad.append(f"{name} · phase: เอกสารลิสต์ {listed} · renderer รองรับ {phases}")
        if owner and f"{owner}()" not in doc:
            bad.append(f"{name} · phase: ควรอ้างฟังก์ชันที่วน phase จริง คือ `{owner}()`")

    # รูปของ session_init ที่เอกสารสรุปไว้ ต้องครอบ key ที่โค้ดอ่านจริง
    ssrc = (pathlib.Path(F.__file__).parent / "session_init.py").read_text(encoding="utf-8")
    si_code = set(re.findall(r'cfg\.get\("([a-z_]+)"', ssrc)) | {"on_failure"}
    m = re.search(r"`session_init`\s*\|\s*`\{([^}]*)\}`", doc)
    if not m:
        bad.append(f"{name} · ไม่พบแถว `session_init`")
    else:
        listed = set(re.findall(r"[a-z_]+", m.group(1)))
        for k in sorted(si_code - listed):
            bad.append(f"{name} · session_init: โค้ดอ่าน `{k}` แต่เอกสารไม่ได้สรุปไว้")

    # `{API_BASE}` ต้องมาพร้อมคำเตือนว่าเป็นของฝั่งโฮสต์ ไม่ใช่ของ tenant
    if "{API_BASE}" in doc and not re.search(pats["api_base_warning"], doc):
        bad.append(f"{name} · `{{API_BASE}}`: ไม่ได้เตือนว่า tenant ต้องใส่ URL ของตัวเอง")

    # ตัวเลขที่เอกสารอ้างถึงชุดค่าคงที่ ต้องตรงกับความยาวจริง (§12 เคยค้างที่ 22/13/16)
    for const, n in re.findall(r"`([A-Z_]+)` \((\d+)\)", doc):
        real = getattr(F, const, None)
        if real is not None and int(n) != len(real):
            bad.append(f"{name} · {const}: เอกสารว่า {n} โค้ดว่า {len(real)}")

    # path ที่เอกสารอ้าง ต้องมีจริง **และ** ต้องเป็นแอปที่รันจริง — รีโปยังมีสำเนาเก่า
    # `demo/` อยู่ ⇒ การเช็คแค่ว่า "ไฟล์มีอยู่" ผ่านได้ทั้งที่ชี้ผิดแอป (README สั่งรัน
    # `demo_v2.server.app`) ซึ่งเคยเกิดขึ้นจริงกับ 8 path ในเอกสารนี้
    for path in sorted(set(re.findall(r"`([\w./\-]+\.py)`", doc))):
        if path.startswith("demo/"):
            bad.append(f"{name} · path `{path}` ชี้สำเนาเก่า — แอปที่รันคือ "
                       f"`demo_v2/{path[len('demo/'):]}`")
        elif "/" in path and not (ROOT / path).exists():
            bad.append(f"{name} · path `{path}` ไม่มีอยู่จริง")

    return bad


if __name__ == "__main__":
    sys.exit(main())
