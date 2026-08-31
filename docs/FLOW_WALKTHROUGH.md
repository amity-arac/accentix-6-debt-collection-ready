# จาก requirement → JSON — เดินทีละขั้นด้วย SHOP

เอกสารนี้ไม่ได้อธิบาย *ว่า key ไหนแปลว่าอะไร* — อันนั้นอยู่ที่
[SPEC_LOCKED.md](SPEC_LOCKED.md) แล้ว

อันนี้ตอบอีกคำถาม: **เวลาได้โจทย์มาหนึ่งข้อ เราคิดยังไง แล้วสิ่งที่คิดกลายเป็น JSON ตรงไหนบ้าง**

ตัวอย่างที่ใช้คือ `SHOP` ซึ่งสร้างจาก requirement จริง และตอนนี้อยู่ในระบบแล้ว —
`data/flows/SHOP.company.json` ทุกโค้ดในเอกสารนี้ตัดมาจากไฟล์นั้นตรงๆ

---

## โจทย์

> ร้านค้าอยากโทรเตือนลูกค้าล่วงหน้า 1 วันก่อนถึงกำหนดชำระ
> ถ้าลูกค้าขอเลื่อน ให้เลื่อนได้ **ไม่เกิน 7 วัน** จากกำหนดเดิม
> ถ้าลูกค้าบอกว่าจ่ายไปแล้ว ให้รับเรื่องไว้ตรวจสอบ
> ถ้าขอคุยคน หรือถามเรื่องนอกเรื่องชำระเงิน ให้ส่งต่อเจ้าหน้าที่

สั้นแค่นี้ ที่เหลือเป็นสิ่งที่เราต้องถามกลับหรือตัดสินใจเอง

---

## ขั้น 1 — ลูกค้าทำอะไรได้บ้าง

เริ่มจากฝั่งลูกค้าเสมอ ไม่ใช่ฝั่งบท เพราะ flow ทั้งอันคือการตอบสนองต่อสิ่งที่ลูกค้าทำ

นับให้ครบว่าสายจบได้กี่แบบ แล้วไล่ย้อนว่าอะไรพาไปที่นั่น

```jsonc
"events": {
  "confirms_pay":       { "desc": "ยืนยันจะชำระตามวันเดิม ไม่มีเงื่อนไข" },
  "reschedule_request": { "desc": "ยังยืนยันจะจ่าย แต่ขอเลื่อนวัน (ไม่ใช่การปฏิเสธหนี้)" },
  "gives_date":         { "desc": "ลูกค้าระบุวันที่จะชำระ" },
  "confirms_new_date":  { "desc": "ยืนยันวันใหม่ที่เสนอไป" },
  "already_paid":       { "desc": "อ้างว่าชำระไปแล้ว — เป็นข้อโต้แย้งเรื่องสถานะ ไม่ใช่การปฏิเสธ" },
  "asks_human":         { "desc": "ขอคุยเจ้าหน้าที่จริง หรือถามนอกขอบเขต flow การชำระ" },
  "no_input":           { "desc": "เงียบ หรือตอบไม่เป็นคำพูดที่ตีความได้" }
}
```

**สองอันที่ requirement ไม่ได้บอก แต่ต้องมี**

- `no_input` — คนเงียบใส่สายเสมอ ถ้าไม่มี event นี้ flow จะไม่มีทางออกให้ความเงียบ
- แยก `reschedule_request` (ขอเลื่อน ยังไม่บอกวัน) ออกจาก `gives_date` (บอกวันมาแล้ว)
  เพราะสองอันนี้พาไปคนละที่ — อันแรกต้องถามวันก่อน อันหลังเช็คได้เลย

`desc` ไม่ได้มีไว้ประดับ — มันเข้าไปอยู่ใน prompt ที่โมเดลอ่าน คำว่า
*"ไม่ใช่การปฏิเสธหนี้"* คือสิ่งที่กันไม่ให้โมเดลตีความคำขอเลื่อนเป็นการปฏิเสธ

---

## ขั้น 2 — วาดผัง

ตอนนี้ค่อยคิดว่าเอเจนต์พูดอะไร แต่ละกล่องคือ **หนึ่งเทิร์นที่เอเจนต์พูด**

```
                    greet ─────────── confirms_pay ────────► close_confirmed
                      │                                       (confirmed)
                      ├──── reschedule_request ──► ask_new_date
                      │                                │
                      ├──── gives_date ───────────►  check_date  ◄── gives_date (วนซ้ำ)
                      │                            [check_new_date]
                      │                                │
                      │                                └── confirms_new_date ──► close_rescheduled
                      │                                                          (rescheduled)
                      ├──── already_paid ─────────────────────► close_paid_claim
                      │                                          (paid_claimed)
                      └──── asks_human / no_input ────────────► close_handoff
                                                                 (handoff)
```

สังเกต **`check_date` วนกลับหาตัวเอง** ด้วย `gives_date` — ลูกค้าเสนอวันเกินเกณฑ์
เอเจนต์บอกว่าเลื่อนได้ไม่เกินกี่วัน ลูกค้าเสนอวันใหม่ ก็เช็คอีกรอบ

กลายเป็น JSON แบบนี้ (ตัดมา 2 state จาก 7)

```jsonc
{
  "id": "greet",
  "initial": true,
  "templates": [{ "fine_state": "greet_remind" }],
  "on": [
    { "event": "confirms_pay",       "to": "close_confirmed" },
    { "event": "reschedule_request", "to": "ask_new_date" },
    { "event": "gives_date",         "to": "check_date" },
    { "event": "already_paid",       "to": "close_paid_claim" },
    { "event": "asks_human",         "to": "close_handoff" },
    { "event": "no_input",           "to": "close_handoff" }
  ]
},
{
  "id": "check_date",
  "entry_tools": ["check_new_date"],
  "templates": [{ "any_of": ["confirm_new_date", "date_too_far"] }],
  "on": [
    { "event": "confirms_new_date", "to": "close_rescheduled" },
    { "event": "gives_date",        "to": "check_date" },
    { "event": "asks_human",        "to": "close_handoff" },
    { "event": "no_input",          "to": "close_handoff" }
  ]
}
```

จุดที่ต้องเข้าใจใน `check_date`

- **`entry_tools`** = เรียกก่อนพูดทุกครั้งที่เข้า state นี้ ไม่ใช่ให้โมเดลเลือกเรียกเอง
- **`any_of`** = สองประโยคนี้เป็น *ทางเลือก* ไม่ใช่ให้พูดทั้งคู่
  เพราะพูด `confirm_new_date` เมื่อวันผ่านเกณฑ์ พูด `date_too_far` เมื่อไม่ผ่าน
  ถ้าเขียนเป็นสองบรรทัดโดยไม่มี `any_of` จะแปลว่าต้องพูดทั้งสองในเทิร์นเดียว

state ที่ปิดสายเขียนแบบนี้

```jsonc
{
  "id": "close_rescheduled",
  "terminal": true,
  "entry_tools": ["record_call_result"],
  "templates": [{ "fine_state": "close_rescheduled" }],
  "outcome": {
    "result": "rescheduled",
    "reasons": ["rescheduled"],
    "desc": "เลื่อนวันชำระและยืนยันวันใหม่แล้ว"
  }
}
```

**ไม่มี block `outcomes` แยกในไฟล์** — ผลลัพธ์ที่ flow นี้มี คือ `result` ของทุก
terminal state รวมกัน ระบบอ่านเอง (`confirmed` · `rescheduled` · `paid_claimed` · `handoff`)

---

## ขั้น 3 — อะไรเป็นงานของ tool ไม่ใช่ของโมเดล

ตรงนี้คือขั้นที่พลาดแล้วเจ็บที่สุด

> *"เลื่อนได้ไม่เกิน 7 วัน"*

ประโยคนี้ **ไม่ใช่กฎที่เขียนลง prompt** — เป็น **API ที่ยังไม่มี**

วัดมาแล้วว่าโมเดลคำนวณวันที่ไม่แม่น ถ้า requirement บอกเกณฑ์แต่ไม่มีกลไกให้ตอบ
แปลว่าเรายังขาดเครื่องมือ ไม่ใช่ขาดคำสั่ง

SHOP จึงมี tool 2 ตัว — หนึ่งตัวตอบคำถามที่ระบบต้องตอบ อีกตัวบันทึกผล

```jsonc
{
  "name": "check_new_date",
  "impl": "http",
  "url": "{API_BASE}/SHOP/check_new_date",
  "args": {
    "date": {
      "type": "string",
      "format": "YYYY-MM-DD (Weekday)",
      "desc": "วันที่ลูกค้าบอก แปลงเป็นรูปแบบ canonical ก่อนส่ง"
    }
  },
  "returns": {
    "in_range":        { "type": "boolean", "desc": "วันที่ขอมาอยู่ในเกณฑ์ไหม" },
    "valid_dates":     { "type": "array",   "desc": "วันที่รับได้ทั้งหมด (canonical)" },
    "max_extend_days": { "type": "number",  "desc": "เลื่อนได้ไม่เกินกี่วัน" }
  },
  "gating": {
    "after_event": "gives_date",
    "note": "เรียกเมื่อลูกค้าระบุวันแล้วเท่านั้น — ถ้ายังไม่ได้วัน ให้ถามด้วย ask_new_date ก่อน"
  }
}
```

`returns` ไม่ได้มีไว้ให้คนอ่าน — มันทำสามอย่าง: ให้ mock สร้างคำตอบที่หน้าตาถูก
ให้ `one_of_from` เช็คได้ และบอกว่าโมเดลจะเห็นอะไรกลับมา

ตัวบันทึกผล

```jsonc
{
  "name": "record_call_result",
  "impl": "http",
  "url": "{API_BASE}/SHOP/record_call_result",
  "args": {
    "result": {
      "type": "string",
      "enum": ["confirmed", "rescheduled", "paid_claimed", "handoff", "unreachable"]
    },
    "new_date": {
      "type": "string",
      "optional": true,
      "format": "YYYY-MM-DD (Weekday)",
      "required_when": { "arg": "result", "equals": "rescheduled" },
      "one_of_from":   { "tool": "check_new_date", "field": "valid_dates" }
    }
  },
  "gating": { "required_at": "end_of_call", "max_successful_calls": 1 }
}
```

สามบรรทัดล่างคือของสำคัญ

| ประกาศ | กันอะไร |
|---|---|
| `required_when` | ถ้า `result=rescheduled` แล้ว `new_date` ว่าง → ปฏิเสธ |
| `one_of_from` | `new_date` ต้องเป็นค่าที่ `check_new_date` เคยคืนมา — โมเดลพิมพ์วันเองไม่ได้ |
| `required_at` + `max_successful_calls` | ตัวปิดสาย เรียกสำเร็จได้ครั้งเดียว |

`optional: true` เดี่ยวๆ **ไม่พอ** — มันยอมให้ `""` ผ่าน เคยมีเคสที่บันทึกสำเร็จโดยไม่มีวัน
แล้วสายถูกปิดไปเลย `required_when` + `one_of_from` คือคู่ที่ปิดรูนั้น

---

## ขั้น 4 — เขียนประโยค

หนึ่ง state อ้างชื่อ beat ตอนนี้ค่อยเขียนว่า beat นั้นพูดว่าอะไร

```jsonc
"catalog": [
  { "text_id": 9001, "_fine_state": "greet_remind",
    "template": "สวัสดีค่ะ คุณ[customer_name] ทางร้าน[shop_name] ติดต่อมาเพื่อแจ้งเตือน..." },
  { "text_id": 9003, "_fine_state": "ask_new_date",
    "template": "ได้ค่ะ ทางร้านขอสอบถามว่าสะดวกชำระวันไหนคะ" },
  { "text_id": 9004, "_fine_state": "confirm_new_date",
    "template": "รับทราบค่ะ ทางร้านขอเลื่อนกำหนดชำระเป็นวันที่ [new_date] นะคะ ยืนยันตามนี้ไหมคะ" },
  { "text_id": 9005, "_fine_state": "date_too_far",
    "template": "ขออภัยค่ะ ทางร้านสามารถเลื่อนกำหนดให้ได้ไม่เกิน [max_extend_days] วันค่ะ" }
]
```

- `_fine_state` มี `_` นำหน้า (ชื่อไม่มี `_` ใช้ใน `states[].templates`)
- `[slot]` เติมตอนรัน — `[customer_name]` มาจาก CRM, `[new_date]` มาจากผลของ tool
- หนึ่ง beat มีได้หลายสำนวน (คนละ `text_id` แต่ `_fine_state` เดียวกัน) ระบบสุ่ม/เลือกให้

ต้องมี beat สำรองด้วย ไม่งั้น lint ไม่ผ่าน

```jsonc
{ "text_id": 9009, "_fine_state": "faq_repeat",
  "template": "ขออภัยค่ะ รบกวนคุณลูกค้าแจ้งอีกครั้งได้ไหมคะ" }
```

```jsonc
"auxiliary_templates": {
  "note": "template ที่ไม่ผูกกับ state — พูดได้จากทุกจุดของสาย",
  "allowed": [{ "fine_state": "faq_repeat", "desc": "ขอให้ลูกค้าพูดอีกครั้ง" }]
}
```

เพราะถ้าโมเดลเรียก tool วนจนหมดโควตาแล้วยังไม่พูดอะไร แอปจะพูดประโยคนี้แทนที่จะเงียบใส่สาย

---

## ขั้น 5 — ข้อมูลลูกค้ามาจากไหน

ไม่มีข้อมูลลูกค้าอยู่ในไฟล์ spec เลย — ประกาศแค่ว่าไปเอาจากไหน และโมเดลเห็นอะไรได้บ้าง

```jsonc
"session_init": {
  "url": "{API_BASE}/SHOP/init?msisdn={msisdn}",
  "method": "GET",
  "timeout": 8,
  "on_failure": {
    "fine_state": "close_handoff",
    "outcome": { "result": "handoff", "reason": "ระบบไม่ตอบ ให้เจ้าหน้าที่ติดต่อกลับ" }
  }
},
"crm_fields": ["today", "customer_name", "shop_name", "due_date", "amount", "max_extend_days"],
"crm_labels": {
  "customer_name":   "ชื่อลูกค้า",
  "shop_name":       "ชื่อร้าน",
  "due_date":        "วันครบกำหนดเดิม",
  "amount":          "ยอดค่างวด",
  "max_extend_days": "เลื่อนได้ไม่เกิน (วัน)"
}
```

- `?msisdn={msisdn}` — ค้นลูกค้าจากเบอร์ที่โทรออก ถ้าไม่ใส่ API จะคืนแถวเดิมทุกสาย
- `crm_fields` เป็น **whitelist** เฉพาะ field ในนี้ที่โมเดลได้เห็น
- `crm_labels` คือป้ายไทยในหัวข้อ `## ข้อมูลลูกค้า` ของ prompt
- `on_failure` — ถ้า API ล่ม **ไม่เปิดสายด้วยข้อมูลค้างในเครื่อง** พูดประโยคนี้แล้ววาง

ข้อสุดท้ายมาจากของจริง: ตอนทดสอบโดยชี้ API ไปพอร์ตที่ปิดอยู่
เอเจนต์ยังทักลูกค้าและบอกยอดหนี้ ซึ่งเป็นตัวเลขจากไฟล์ตัวอย่างในโค้ดเรา

---

## ขั้น 6 — กฎ ให้น้อยที่สุดที่ใช้ได้

`constraints` เกือบทั้งหมดคือ **ข้อความที่โมเดลอ่าน** ไม่ใช่กลไก
`desc` คือของจริง ส่วน `type` เป็นการจัดหมวดให้ตัวตรวจ

```jsonc
"constraints": [
  { "enforce": ["prompt", "reward"],
    "desc": "**ห้ามตัดสินเองว่าวันที่ลูกค้าขอเลื่อนอยู่ในเกณฑ์ไหม** — เรียก `check_new_date(date)` ทุกครั้งที่ลูกค้าระบุวัน แล้วอ่าน `in_range` · in_range=true → พูด confirm_new_date (9004) · false → date_too_far (9005)" },

  { "enforce": ["prompt", "reward"],
    "desc": "**ยืนยันวันใหม่ก่อนบันทึกเสมอ** — หลัง confirm_new_date (9004) ต้องรอให้ลูกค้ายืนยันก่อน ห้ามบันทึกทันที" },

  { "enforce": ["prompt", "reward"],
    "desc": "**ความเงียบไม่ใช่การตกลง** — ลูกค้าตอบ '...' = เหตุการณ์ no_input เท่านั้น ห้ามถือเป็นการยืนยัน" },

  { "id": "max_date_retries", "type": "max_occurrences",
    "counts": "date_too_far", "max": 2,
    "on_exceed": { "to": "close_handoff" },
    "enforce": ["prompt", "reward"],
    "desc": "บอกว่าเลื่อนได้ไม่เกินกี่วันได้สูงสุด 2 ครั้งต่อสาย — ครบแล้วยังเสนอวันเกินเกณฑ์ ให้ส่งต่อเจ้าหน้าที่" }
]
```

**กฎที่ย้ายข้ามบริษัทได้มีรูปเดียว:** *ประโยคที่ถูก + ทางเลือกที่ห้าม + เหตุผลเชิงความหมาย*
กฎที่เขียนเป็นขั้นตอน (“ทำ ก แล้ว ข แล้วปิดสาย”) ย้ายไม่ได้ และวัดแล้วว่า
การใส่กฎถูกต้องหลายข้อพร้อมกันเคยทำให้คะแนน **ลด** เพราะโมเดล generalise ตอนจบ ไม่ใช่เงื่อนไข

> เริ่มจากกฎน้อยที่สุดที่ใช้ได้ แล้วเพิ่มทีละหนึ่งถึงสองข้อ พร้อมวัดผลทุกครั้ง

---

## ไฟล์ที่ได้

SHOP ทั้งอันมี 12 key — เล็กที่สุดในระบบ

| key | มีอะไร |
|---|---|
| `display_name` | `"ร้านค้า (Pre-Due Reminder)"` |
| `events` | 7 |
| `states` | 7 (terminal 4) |
| `catalog` | 9 ประโยค |
| `tools.declarations` | 2 |
| `constraints` | 8 |
| `crm_fields` / `crm_labels` | 6 field |
| `session_init` | 1 endpoint + `on_failure` |
| `auxiliary_templates` | 1 |
| `faq_routing` | `{"routes": []}` — flow นี้ไม่มี FAQ แยก ทุกคำถามนอกเรื่องไปที่ `asks_human` |
| `flow_id` | เติมจากชื่อไฟล์ ไม่ต้องเขียน |

**ไม่มีในไฟล์:** ชื่อบริษัทในโค้ด, ข้อมูลลูกค้า, `outcomes` block, path ของ catalog

---

## ตรวจก่อนอัปโหลด

```bash
PYTHONPATH=. python3 .claude/skills/new-company/scripts/lint_spec.py data/flows/SHOP.company.json
PYTHONPATH=. python3 .claude/skills/new-company/scripts/spec_to_mermaid.py data/flows/SHOP.company.json
PYTHONPATH=. python3 tools/gen_mockoon.py
PYTHONPATH=. python3 .claude/skills/new-company/scripts/smoke_company.py SHOP \
    --case TC-SHOP-BUILD-001 --scenarios scenarios.json
```

`ERROR` ต้องเป็น 0 · `WARN` อ่านทุกบรรทัด ส่วนใหญ่เป็นของจริง

`sentence_never_used` เจอ beat ที่เขียนไว้แต่ไม่มี state หรือ FAQ route ไหนเปิดทางให้พูดเลย —
กฎใน prompt สามรอบก็แก้ไม่ได้ เพราะมันไม่ใช่เรื่องที่โมเดลเลือกผิด

⚠️ **ห้ามรายงานคะแนนจากการรันครั้งเดียว** — config เดียวกัน temp 0 เคยแกว่ง 6 จาก 45

---

## สามอย่างที่ SHOP สอนตอนใช้จริง

**① กฎที่ไม่มีกลไกรองรับ = ไม่มีกฎ**
`mock` ของ `check_new_date` เคยใช้ regex `^2026-0[89]-` แทนการคำนวณจริง
ผลคือวันที่เกินเกณฑ์ไป 26 วัน **ผ่าน** เพราะบังเอิญอยู่ในเดือนที่ regex ครอบ
แก้ด้วยการให้ mock คำนวณจาก CRM จริง

```jsonc
"when": { "arg": "date",
          "within_days_of": { "field": "due_date", "days_field": "max_extend_days" } }
```

**② เขียน note ให้ชัดขึ้น ไม่ได้แก้พฤติกรรม**
`gating.note` เคยเขียนว่า *"เรียกเงียบๆ ก่อนตอบทุกครั้ง"* โมเดลเลยเรียก
`check_new_date(date="")` 8 ครั้งก่อนลูกค้าบอกวัน แก้ข้อความให้ชัดแล้ว **ยังเรียก 8 ครั้งเท่าเดิม**
สิ่งที่แก้ได้จริงคือกลไก (แอปพูดประโยคสำรองแทนที่จะเงียบ) กับข้อมูล
(`due_date` เป็นวันจริง โมเดลเลยไม่ต้องเดา — เหลือเรียกครั้งเดียว)

**③ วันที่ตายตัวใน persona พังเงียบ**
persona เคยเก็บ `due_date: "2026-08-27 (Thursday)"` ตรงๆ ถูกอยู่วันเดียว
ตอนนี้ใช้ `due_date_offset_days: 1` แล้วแอปกับ mock คำนวณจากวันนี้ทั้งคู่

---

## สรุปลำดับ

```
1  events        ลูกค้าทำอะไรได้บ้าง · สายจบได้กี่แบบ
2  states        ผัง — พูดอะไร ไปไหนต่อ จบยังไง (outcome อยู่ที่ terminal state)
3  tools         อะไรที่ "ระบบ" ต้องตอบ ไม่ใช่ agent เดา — พร้อม returns + args contract
4  catalog       ประโยคของทุก beat ที่ผังอ้างถึง + beat สำรอง
5  crm/session   ไปเอาข้อมูลจากไหน · โมเดลเห็นอะไรได้ · API ล่มทำยังไง
6  constraints   กฎ น้อยที่สุดที่ใช้ได้ แล้วค่อยเพิ่มทีละข้อพร้อมวัด
```

เขียน `states` ก่อน `catalog` เสมอ — ผังเป็นตัวบอกว่าต้องมีประโยคอะไรบ้าง
ไม่ใช่เขียนประโยคไว้ก่อนแล้วค่อยหาที่ให้มัน
