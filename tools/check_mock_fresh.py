#!/usr/bin/env python3
"""mock/aax6-mock.json มีหน้าต่างวันที่ตรึงไว้ตอน generate — เตือนเมื่อมันค้าง

`check_new_date` ของ SHOP ตัดสินว่าวันที่ลูกค้าขอเลื่อนอยู่ในเกณฑ์ไหม Mockoon เลือก
response ด้วย regex ที่ template ไม่ได้ gen_mockoon.py จึงกาง due_date + max_extend_days
เป็นลิสต์วันที่ ณ ตอน generate แล้วฝังลงไฟล์

ผลถ้าไม่ regenerate: ลิสต์ค้างอยู่ในอดีต ทุกวันที่ลูกค้าเสนอจะหล่นไป response `default`
ที่ตอบ `in_range: false` ⇒ เจ้าหน้าที่พูด `date_too_far` ("เลื่อนได้ไม่เกิน 7 วัน")
ทั้งที่วันที่ยังไม่เกิน — เกิดขึ้นจริง ค้าง 10 วัน (2026-08-28 → 2026-09-07) และดูเหมือน
โมเดลโง่ ทั้งที่มันเชื่อฟังสเปคที่บอกว่า "การนับวันเป็นหน้าที่ของระบบ ห้ามตัดสินเอง"

รัน: PYTHONPATH=. python3 tools/check_mock_fresh.py   (exit 1 = ต้อง regenerate)
"""
import datetime
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MOCK = ROOT / "mock" / "aax6-mock.json"
# ลิสต์วันที่ที่ถูกกางไว้ในกฎ — ขึ้นต้นด้วยปีสี่หลักคั่นด้วย |
WINDOW = re.compile(r"\^\((\d{4}-\d{2}-\d{2}(?:\|\d{4}-\d{2}-\d{2})+)\)")


def stale_windows(doc: dict, today: datetime.date) -> list:
    out = []

    def walk(node, route=""):
        if isinstance(node, dict):
            route = node.get("endpoint", route)
            for resp in node.get("responses") or []:
                for rule in resp.get("rules") or []:
                    m = WINDOW.match(str(rule.get("value") or ""))
                    if not m:
                        continue
                    days = [datetime.date.fromisoformat(x) for x in m.group(1).split("|")]
                    if today > max(days):
                        out.append((route, resp.get("label"), min(days), max(days)))
            for v in node.values():
                walk(v, route)
        elif isinstance(node, list):
            for v in node:
                walk(v, route)

    walk(doc)
    return out


def main() -> int:
    if not MOCK.exists():
        print("ไม่พบ %s" % MOCK)
        return 1
    today = datetime.date.today()
    stale = stale_windows(json.loads(MOCK.read_text(encoding="utf-8")), today)
    if not stale:
        print("mock ยังสด (วันนี้ %s)" % today)
        return 0
    print("mock ค้าง — วันนี้ %s แต่หน้าต่างวันที่ในกฎจบไปแล้ว:" % today)
    for route, label, lo, hi in stale:
        print("  %s | %s | %s..%s" % (route, label, lo, hi))
    print("\nแก้ด้วย: python3 tools/gen_mockoon.py  แล้วรีสตาร์ต mockoon")
    return 1


if __name__ == "__main__":
    sys.exit(main())
