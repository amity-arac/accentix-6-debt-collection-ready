# สร้างบริษัทใหม่ — คู่มือกรอก `<CODE>.company.json`

หนึ่งบริษัท = **หนึ่งไฟล์** วางไว้ที่ `data/flows/<CODE>.company.json`
ระบบอ่านบริษัทจากไฟล์ที่มีอยู่ ไม่มีทะเบียนแยก — **วางไฟล์ = เปิดใช้ · ลบไฟล์ = ปิด**

เอกสารนี้สอน *วิธีกรอก* · ส่วน *รูปแบบที่ล็อกไว้* (key ที่อนุญาต / key ที่เลิกใช้) อยู่ที่
[SPEC_FORMAT.md](SPEC_FORMAT.md)

---

## เริ่มยังไง

```
1. โหลดไฟล์เปล่า        ปุ่ม Upload ในเว็บ → "ดาวน์โหลด template"
                        (หรือ data/flows/_TEMPLATE.company.json)
2. กรอกตามคู่มือนี้
3. อัปโหลดกลับ          ระบบ validate ให้ก่อนบันทึก ถ้าผิดจะบอกว่าผิด key ไหน
4. กด playground        เลือกบริษัทของคุณแล้วคุยได้เลย
```

ระบบจะ **แปลง JSON เป็น instruction ให้เอง** ตอนกด playground — คุณไม่ต้องเขียน prompt

---

## กรอกตามลำดับนี้

ลำดับนี้ไม่ใช่ลำดับ key ในไฟล์ แต่เป็นลำดับที่คิดแล้วไม่ต้องย้อนกลับ

### 1 · ระบุตัวตน

```json
"spec_version": 2,
"company": "YOURCO",
"display_name": "ชื่อที่จะโชว์ในเว็บ",
"flow_id": "YOURCO-outbound-call",
"description": "โทรออกเพื่อ…"
```

`company` ต้องตรงกับชื่อไฟล์ (`YOURCO.company.json`) และเป็นตัวพิมพ์ใหญ่

---

### 2 · `crm_fields` + `crm_labels` — ข้อมูลลูกค้าที่ agent จะเห็น

```json
"crm_fields": ["today", "customer_name", "doctor_name", "appointment_date"],
"crm_labels": { "today": "วันนี้", "customer_name": "ชื่อลูกค้า" }
```

ชื่อ field พวกนี้จะกลายเป็น **placeholder ที่ใช้ในประโยคได้** เช่น `[customer_name]`
`crm_labels` คือคำแปลไทยที่จะโชว์ใน prompt — ไม่ใส่ก็ได้ จะใช้ชื่อ field ดิบแทน

> ต้องมี `today` เสมอถ้าประโยคหรือ tool ของคุณเกี่ยวกับวันที่

---

### 3 · `session_init` — ดึงข้อมูลลูกค้าตอนเริ่มสาย

```json
"session_init": {
  "url": "{API_BASE}/YOURCO/init",
  "method": "GET",
  "timeout": 8
}
```

ยิงครั้งเดียวตอนเปิดสาย **response คือ context ตรงๆ** — key อะไรกลับมา ก็ใช้เป็น
placeholder ชื่อนั้นได้เลย ไม่มีชั้นแปลง

⚠️ **ทุก field ที่ประโยคของคุณอ้างถึง ต้องมาจาก `session_init` หรือจาก tool** ถ้าไม่มี
ระบบจะเตือน `ยังไม่มีค่าให้พูด: <field>` และประโยคจะออกไปพร้อมวงเล็บเปล่า

---

### 4 · `catalog_inline` — ประโยคทั้งหมดที่ agent พูดได้

agent **พูดได้เฉพาะประโยคในนี้** สร้างข้อความเองไม่ได้ เขียนแค่ 2 field:

```json
"catalog": "__inline__",
"catalog_inline": [
  { "text_id": 8501,
    "_fine_state": "greet_remind",
    "template": "สวัสดีค่ะคุณ [customer_name] โรงพยาบาล AMT โทรมาแจ้งเตือนว่าคุณมีนัดพบ [doctor_name] [appointment_date] ค่ะ ไม่ทราบว่ายังสะดวกเข้าตามนัดไหมคะ" }
]
```

| field | คืออะไร |
|---|---|
| `text_id` | เลขประจำประโยค **ห้ามซ้ำ ห้ามเปลี่ยนทีหลัง** |
| `_fine_state` | ชื่อ "จังหวะพูด" ที่ state จะเรียกใช้ |
| `template` | ข้อความจริง ใส่ placeholder ด้วย `[ชื่อ_field]` |

**หนึ่ง `_fine_state` มีได้หลาย `text_id`** = สำนวนต่างกันของจังหวะเดียวกัน ระบบจะให้
agent เลือกเอง

---

### 5 · `events` — เหตุการณ์ฝั่งลูกค้า

```json
"events": {
  "confirms": {
    "desc": "ผู้ป่วยยืนยันจะเข้าตามนัดเดิม",
    "cues": ["สะดวก", "ยืนยัน", "ไปตามนัด", "มาแน่", "ตามเดิม"]
  }
}
```

`desc` + `cues` ถูกพิมพ์ลง prompt ให้ agent อ่าน — **ยิ่งใส่ cue ที่ลูกค้าพูดจริงเยอะ
ยิ่งแม่น**

**ต้องมี `no_input`** ถ้า flow มีทางแยกตอนลูกค้าเงียบ และใส่ cue พวกนี้ด้วย:
```json
"no_input": { "desc": "เงียบ / ตอบไม่เป็นคำพูด", "cues": ["...", "(เงียบ)", "อือ..."] }
```

---

### 6 · `tools` — สิ่งที่ agent ทำกับระบบคุณ

```json
"tools": { "declarations": [
  { "name": "save_appointment",
    "impl": "http",
    "desc": "บันทึกสถานะนัดหมาย — เรียกก่อนปิดสายเสมอ",
    "url": "{API_BASE}/YOURCO/save_appointment",
    "method": "POST",
    "args": {
      "status":   { "type": "string", "enum": ["confirmed", "rescheduled", "cancelled"],
                    "desc": "ผลของสายนี้" },
      "new_slot": { "type": "string", "optional": true,
                    "format": "YYYY-MM-DD (Weekday)",
                    "desc": "วันที่เลื่อนไป",
                    "required_when": { "arg": "status", "equals": "rescheduled" },
                    "one_of_from":   { "tool": "check_doctor_time", "field": "available_dates" } }
    },
    "gating": { "required_at": "end_of_call", "max_successful_calls": 1 } }
]}
```

**`impl` มีแค่ 2 แบบ** — `http` (ยิง url ของคุณ) กับ `generic` (คำตอบสำเร็จรูป ใช้ตอนยัง
ไม่มี API)

**ตัวช่วยใน `args` ที่ควรใช้:**

| key | ทำอะไร |
|---|---|
| `optional: true` | ไม่บังคับ |
| `enum` | จำกัดค่าที่รับได้ |
| `format: "YYYY-MM-DD (Weekday)"` | เปิดการตรวจรูปแบบวันที่ |
| `required_when` | บังคับเฉพาะเมื่อ arg อื่นเป็นค่าหนึ่ง |
| `one_of_from` | ค่าต้องเป็นหนึ่งในสิ่งที่ tool อื่นเพิ่งคืนมา |

**`gating`:**
- `required_at: "end_of_call"` — tool ปิดสาย **ต้องมีตัวเดียวและต้องมี**
- `after_event` — ควรเรียกหลังเหตุการณ์ไหน (บอก agent เฉยๆ ไม่บังคับ)
- `max_successful_calls` — เรียกสำเร็จได้กี่ครั้งต่อสาย

---

### 7 · `states` — เส้นทางของบทสนทนา

```json
{ "id": "greet",
  "phase": "opening",
  "initial": true,
  "templates": [{ "fine_state": "greet_remind" }],
  "note": "แจ้งนัดแล้วถามว่าสะดวกไหม — ห้ามเรียก save_appointment ในเทิร์นแรก",
  "on": [
    { "event": "confirms",           "tools": ["save_appointment"], "to": "confirm_close" },
    { "event": "reschedule_request", "to": "check_slot" },
    { "event": "no_input",           "to": "greet_retry" }
  ] }
```

`phase` ∈ `opening` / `main` / `close` · `initial: true` มีได้ state เดียว

**`templates` อ่านยังไง**

```json
"templates": [{"fine_state": "a"}, {"fine_state": "b"}]        → chain: พูด a แล้ว b ในเทิร์นเดียว
"templates": [{"fine_state": "a", "when_event": "x"},          → ทางเลือก: เลือกตาม event
              {"fine_state": "b", "when_event": "y"}]
"templates": [{"any_of": ["a", "b"]}]                          → ขั้นเดียว พูด a หรือ b ก็ได้
```

**state ปิดสาย** ต้องมี 3 อย่าง:
```json
{ "id": "confirm_close",
  "phase": "close",
  "terminal": true,
  "templates": [{"fine_state": "confirm_visit"}],
  "entry_tools": ["save_appointment"],          ← ต้องมี tool ปิดสาย
  "outcome": { "result": "confirmed", "reasons": ["confirmed"] } }
```

---

### 8 · `outcomes` — ผลสายที่เป็นไปได้

```json
"outcomes": {
  "required_at_close": true,
  "results": {
    "confirmed":   { "reasons": ["confirmed"],   "desc": "ยืนยันเข้าตามนัดเดิม" },
    "rescheduled": { "reasons": ["rescheduled"], "desc": "ขอเลื่อนนัด" }
  }
}
```

**คิดคำของธุรกิจคุณเอง** — ไม่มีชุดสำเร็จรูป งานทวงหนี้ใช้ `ptp/refused/…` งานนัดหมาย
ใช้ `confirmed/rescheduled/…` งานสำรวจใช้ `completed/declined`

`result` ที่ state ประกาศ ต้องอยู่ในลิสต์นี้

---

### 9 · `faq_routing` — ลูกค้าถามแทรก

```json
"faq_routing": { "routes": [
  { "intent": "cost",
    "desc": "ถามค่าใช้จ่าย",
    "templates": [{"fine_state": "faq_cost"}],
    "then": "resume" },
  { "intent": "wrong_name",
    "templates": [{"fine_state": "faq_wrong_name"}],
    "then": { "outcome": {"result": "tin", "reasons": ["wrong_name"]}, "terminal": true } }
]}
```

`then: "resume"` = ตอบแล้วกลับเข้า flow จุดเดิม · `then: {…, terminal: true}` = ตอบแล้วจบสาย

---

### 10 · `constraints` — กฎที่ agent ต้องทำตาม

สองแบบ:

```json
{ "id": "disclose_once", "type": "once_per_call",
  "template_fine_state": "disclose_balance",
  "enforce": ["prompt", "reward"],
  "desc": "แจ้งยอดพูดครั้งเดียวต่อสาย" }        ← มี type = ระบบบังคับจริง

{ "enforce": ["prompt", "reward"],
  "desc": "…" }                                  ← ไม่มี type = เขียนลง prompt ให้อ่าน
```

`type` ที่ใช้ได้: `max_occurrences` · `once_per_call` · `repeat_only_on` ·
`forbid_after_event` · `no_repeat_answered_request` · `immediate_transition_on` ·
`max_templates_per_reply` · `resume_after_interrupt` · `require_tool_before_end` ·
`outcome_precondition` · `tool_pair`

---

## เขียนกฎยังไงให้ได้ผล

จากการวัดจริง กฎที่ได้ผลกับกฎที่ทำให้แย่ลงต่างกันชัดเจน

**✅ รูปแบบที่ได้ผล — "ประโยคไหนถูก + ห้ามใช้อะไรแทน + เพราะอะไร"**

```
`handoff_refuse` (1017) ใช้ได้เฉพาะเมื่อลูกค้าขอคุยกับเจ้าหน้าที่ที่เป็นคน —
ลูกค้าเงียบหรือถามเรื่องอื่น ห้ามตอบด้วย handoff_refuse เพราะไม่ได้ตอบสิ่งที่ถาม
```
กฎรูปนี้ย้ายไปใช้กับบริษัทอื่นได้เลยโดยไม่ต้องแก้

**❌ รูปแบบที่ทำให้แย่ลง — สอนเป็นขั้นตอนที่ลงท้ายด้วย "ปิดสาย"**

```
ลูกค้าถาม X → ตอบ Y แล้วปิดสายทันที บันทึกผลเป็น tcb
```
วัดแล้ว: เติมกฎแบบนี้ 6 ข้อรวดเดียว **คะแนนตก** และ agent เริ่ม*บันทึกคำมั่นที่ลูกค้า
ไม่ได้ให้* — มันเรียนรู้คำว่า "ปิดสาย" แล้วเอาไปใช้ทั่ว flow

**เติมทีละ 1-2 ข้อแล้ววัด** อย่าเติมทีเดียวหลายข้อ

---

## กับดักที่เจอมาแล้ว

### ① ชื่อ beat กับ text_id ต้องตรงกันเสมอ

```
❌ "ปิดด้วย close (text 1052)"      ← 1052 คือ close_paid ไม่ใช่ close
✅ "ปิดด้วย close (text 1047)"
```

เคยเกิดจริง: เปลี่ยนชื่อ beat ในภายหลัง แต่ลืมแก้ข้อความในกฎ **agent ทำตามข้อความ** เลย
ปิดสายด้วย "ขอบคุณสำหรับการชำระเงิน" ให้ลูกค้าที่ยังไม่ได้จ่าย

**เวลาเปลี่ยนชื่อ `_fine_state` ต้องค้นทั้งไฟล์ว่ามีกฎหรือ note ไหนอ้างถึงบ้าง**

### ② state ปิดสายต้องบันทึกผล

ทุก state ที่ `terminal: true` หรือ `phase: "close"` ต้องมี tool ปิดสายใน `entry_tools`
ไม่งั้นสายจบโดยไม่มีการบันทึกอะไรเลย

### ③ ประโยคที่ไม่มี state ไหนเรียก จะไม่ถูกใช้

ยกเว้นประโยคใน `faq_routing` ซึ่งเป็น interrupt ใช้ได้ทุกจังหวะ

### ④ arg ที่บังคับบางกรณี ต้องใช้ `required_when`

เขียนใน `desc` ว่า "บังคับเมื่อ status=rescheduled" **ไม่มีผล** ระบบอ่านไม่ออก
ต้องประกาศเป็น `required_when` — ไม่งั้นจะบังคับตลอด (เคสอื่นพัง) หรือไม่บังคับเลย
(บันทึกค่าว่างแล้วปิดสาย)

### ⑤ ความเงียบไม่ใช่การตกลง

ถ้า flow มีทางแยกตอนลูกค้าเงียบ ให้ใส่ทั้ง cue **และ** กฎ:
```
'...' / '(เงียบ)' = เหตุการณ์ no_input เท่านั้น
ห้ามถือเป็นการตกลง ห้ามบันทึกผลว่าลูกค้ารับปาก
```
ใส่ cue อย่างเดียว **ไม่พอ** — วัดแล้วไม่เปลี่ยนพฤติกรรมเลย ต้องมีกฎห้ามด้วย

### ⑥ วันที่ใน mock/API ต้องสอดคล้องกับ "วันนี้"

ถ้า API คืนวันที่ตายตัวที่เป็นอดีต agent จะแปลง "วันพุธหน้า" ไม่ได้ เพราะคำนวณแล้วได้
คำตอบที่ไม่มีในตัวเลือก

---

## เช็คก่อนอัปโหลด

```
□ ชื่อไฟล์ = <company>.company.json และ company ในไฟล์ตรงกัน
□ text_id ไม่ซ้ำ
□ ทุก _fine_state ที่ state อ้าง มีประโยคใน catalog_inline
□ ทุก event ที่ transition อ้าง มีใน events
□ ทุก state ที่ to ชี้ไป มีจริง
□ มี initial: true หนึ่ง state
□ มี tool ที่ required_at: end_of_call หนึ่งตัว
□ ทุก terminal/close state มี tool นั้นใน entry_tools
□ ทุก outcome.result ที่ state ประกาศ อยู่ใน outcomes.results
□ ทุก placeholder ในประโยค มาจาก crm_fields หรือ tool response
□ กฎที่อ้าง text_id — เลขตรงกับ _fine_state ที่พูดถึงจริง
```

อัปโหลดแล้วระบบตรวจให้อีกชั้น ถ้าผิดจะบอกว่า key ไหนผิดและอะไรมาแทน

---

## ดูตัวอย่างจริง

```
data/flows/AMT.company.json     งานนัดหมาย  — flow สั้น 2 tool อ่านง่ายสุด
data/flows/KBANK.company.json   งานทวงหนี้  — flow ปานกลาง
data/flows/AEON.company.json    งานทวงหนี้  — เต็มรูปแบบ 14 state + FAQ ครบ
data/flows/_TEMPLATE.company.json  ไฟล์เปล่าสำหรับเริ่มต้น
```

แนะนำเปิด `AMT.company.json` ควบคู่กับคู่มือนี้ — สั้นพอที่จะอ่านจบและครบทุกส่วน
