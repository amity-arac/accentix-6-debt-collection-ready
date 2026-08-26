# Known issues — จากการไล่เทสเคส AEON (v11)

รายการที่ยังหลุด พบตอนไล่ test cases. ยังไม่แก้ — บันทึกไว้ก่อน.
(ทุกข้อควรทำครบ 4 บริษัท: AEON / JAI / KS / AIS — ไฟล์ที่อ้างเป็นของ AEON เป็นตัวอย่าง)

---

## #1 — ถามซ้ำ "ชำระวันที่เท่าไร / ผ่านช่องทางไหน" ทั้งที่ลูกค้าตอบสะดวกจ่ายขั้นต่ำวันนี้แล้ว

> **สถานะ (2026-07-29): แก้ 2 ชั้น — channel optional + instruction v11.1.**
>
> **ชั้น 1 — `channel` optional ทั้ง path:**
> - `record_verbal_commitment` + `payment_date`: `channel` optional (backend + tool schema ทั้ง OpenAI/Gemini). ลูกค้าไม่ระบุ → tool ผ่านได้เลย. ลูกค้าระบุ (เคสจ่ายแล้ว) → validate + ต้อง match.
> - **คง `offer_channel`/`ask_channel` ไว้ใน aux** โดยตั้งใจ — เคสจ่ายแล้วยังต้องถามได้.
>
> **ชั้น 2 — instruction v11.1 (spec `2.1`) เพราะชั้น 1 อย่างเดียว โมเดล SFT ยังถามซ้ำจากความเคยชิน:**
> - เพิ่ม **หลักการ ข้อ 1 (กฎสูงสุด)** ใน AEON FlowSpec: "รับปากจ่ายขั้นต่ำวันนี้ → date=วันนี้ ห้ามถามวัน/ช่องทางซ้ำ → record_verbal_commitment → payment_date → confirm_info+close ทันที"
> - + transition note (`disclose_ask --agrees_to_pay--> ptp_capture`) + sharpened `ptp_capture.note` + อัปเดต backend rule ให้ channel optional
> - deploy + demo restart แล้ว (pod `/workspace`, spec render สดต่อ session)
> - ⚠️ ยังเป็น prompt lever — **adherence ไม่การันตี 100%** (โมเดล SFT บนพฤติกรรมเดิม). ถ้ายังหลุด: ทางชัวร์คือ GRPO หรือ deterministic controller (บังคับ date=today + ปิด aux channel เมื่อ event=agrees_to_pay+จ่ายวันนี้)
> - **ยังไม่ replicate ไป JAI/KS/AIS** — ทำ AEON ก่อนตาม focus v11; ถ้าเวิร์คค่อยขยาย


**อาการ:** ถาม "สะดวกชำระขั้นต่ำวันนี้ไหม" → ลูกค้า "สะดวก" → บอทถามต่อ "ชำระวันที่เท่าไร ผ่านช่องทางไหน" (ไม่จำเป็น — ไม่มี tool ไหนบันทึก channel เป็นผลลัพธ์)

**Root cause (2 ชั้น):**
1. Tool `record_verbal_commitment(amount, date, channel)` — `channel` เป็น **required arg** (ไม่มี default) → โมเดลต้องถามช่องทางเพื่อเติมค่า; `date` ก็ต้องเติม → ถาม "วันที่เท่าไร" แม้ลูกค้าบอก "วันนี้"
   - `simulator/backend.py:135`
   - tool_schema ใน communicator ทั้ง 2 ตัว (`agents/communicator.py`)
2. `auxiliary_templates.allowed` มี `offer_channel` (1025 "ชำระวันที่เท่าไหร่ ผ่านช่องทางไหน") + `ask_channel` (1115) → โมเดลหยิบมาพูดได้อิสระ
   - `data/flows/AEON-outbound-remind.json` → `auxiliary_templates.allowed`

**ควรเป็น:** agrees_to_pay → ptp_capture → `record_verbal_commitment` + `payment_date` (date=วันนี้อัตโนมัติ) → confirm_info (1109) → close. ไม่ถามช่องทาง

**Fix candidate:** (a) ทำ `channel` เป็น optional (`channel: str = ""`) ใน `record_verbal_commitment` เหมือน `payment_date`; (b) เอา `offer_channel`/`ask_channel` ออกจาก aux allowed
**⚠️ ระวัง:** เคย revert การเอา offer_channel ออกมาแล้วครั้งนึงเพราะโมเดลเคยพึ่งมันแล้วพ่น garbage — ต้องเทสซ้ำหลังเอาออก

---

## #2 — ตอบ "ครับ/ค่ะ" เฉยๆ ไม่ควรนับเป็นยืนยันตัวตน (ต้อง "ใช่ครับ/ใช่ค่ะ")

> **สถานะ (2026-07-29): ตัดสินใจปล่อยไว้ก่อน** (แก้ cue อย่างเดียวไม่ชัวร์ — ต้องเทรน หรือใส่ code guard; ยังไม่ทำ)


**อาการ:** ถาม "คุณ X ถูกต้องหรือไม่?" → ลูกค้าตอบ "ครับ" (แค่รับสาย) → บอทนับเป็นยืนยันตัวตนแล้วเดินหน้าแจ้งยอดเลย

**Root cause:** `events.name_confirmed.cues` หลวมเกินไป — มี bare `'ครับ','ค่ะ','Okay','Right','โอเคค่ะ','ได้ครับ'` ปนกับ affirmative จริง (`'ใช่','พูดอยู่'`)
- `data/flows/AEON-outbound-remind.json` → `events.name_confirmed.cues`

**ควรเป็น:** ต้อง affirmative ต่อคำถาม → `'ใช่','ใช่ครับ','ใช่ค่ะ','ผม/ดิฉันเอง','พูดสายอยู่','กำลังเรียนสาย'`. bare "ครับ/ค่ะ" → ควรไป `verify_retry` (ถามย้ำ) ไม่ใช่ผ่าน

**Fix candidate:** ตัด `'ครับ','ค่ะ','Okay','Right','โอเคค่ะ','ได้ครับ'` ออกจาก name_confirmed cues (คงไว้เฉพาะ affirmative ชัดเจน)
**หมายเหตุ:** cue เป็น hint ให้โมเดล — adherence จริงขึ้นกับ training ด้วย แต่นี่คือ lever ที่ถูก

---

## #3 — callback ไม่มี tool call บันทึกผล (result code)

**อาการ:** ลูกค้าขอเลื่อน/ให้โทรกลับ → บอทเรียก `callback_datetime` จองเวลา แต่ไม่มี tool ไหน stamp ผลลัพธ์ของสาย (result=tcb)

**Root cause:** `callback_close.entry_tools = ['callback_datetime']` เท่านั้น — `callback_datetime` แค่จองเวลา ไม่ได้บันทึก result_code. outcome `tcb` ใน spec เป็น `"inferred": true` (อนุมานจาก flow). มี tool `record_outcome(result, reason)` อยู่แต่ **ไม่มี state ไหนเรียกเลย** — ทุก terminal อนุมาน outcome หมด
- `simulator/backend.py:223` (`record_outcome` มีอยู่แต่ไม่ถูกใช้)
- `data/flows/AEON-outbound-remind.json` → state `callback_close`

**ควรเป็น:** callback_close เรียก `record_outcome(result="tcb", reason="callback")` เพื่อให้มี tool call บันทึกผลจริง (เหมือน ptp_capture ที่มี record_verbal_commitment)

**Fix candidate:** เพิ่ม `record_outcome` เข้า `callback_close.entry_tools` (พิจารณาทำกับ close_paid/close_refused/close_unreachable ให้ครบด้วย); เอา `"inferred": true` ออกเมื่อบันทึกจริง
