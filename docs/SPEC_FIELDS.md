# `<CODE>.company.json` — ทุก field ที่ใส่ได้ และใครอ่านมัน

reference ของทุก key ที่ spec รับได้ พร้อมบอกว่า *ใครอ่าน* และ *ใส่แล้วเกิดอะไร*
เพราะ field ที่ไม่มีใครอ่านคือ field ที่คุณเสียเวลาเขียนเปล่า

เอกสารพี่น้อง: [NEW_COMPANY.md](NEW_COMPANY.md) สอนกรอกทีละขั้น · [SPEC_FORMAT.md](SPEC_FORMAT.md) สรุปรูปแบบ

**validator ปฏิเสธ key ที่ไม่อยู่ในรายการนี้** — พิมพ์ผิดจะได้ error ไม่ใช่ถูกเมินเงียบๆ

---

## prompt ที่โมเดลเห็น มี **2 ส่วน** — สำคัญที่สุดในเอกสารนี้

```
ส่วนที่ 1  instruction     ← render จาก role/goal/crm_fields/states/events/tools/constraints/faq_routing
ส่วนที่ 2  catalog block   ← render จาก catalog  (ประโยคทั้งหมด จัดกลุ่มและติดป้าย)
```

**เคยพลาดมาแล้ว:** ถอด `state`/`intent_name`/`category` ออกจาก catalog เพราะเห็นว่า
`render_instruction` ไม่เปลี่ยน → **catalog block เปลี่ยน 138 บรรทัด → AEON 17 → 5/23**

⇒ ตรวจว่า field ไหน "ไม่มีใครอ่าน" **ต้องเทียบ prompt ทั้งสองส่วน** ไม่ใช่ grep

---

## 3 กลุ่มที่ต้องแยกให้ออก

| กลุ่ม | ใครอ่าน | ผลถ้าเขียนผิด |
|---|---|---|
| **โครงสร้าง** — `states`, `templates`, `on`, `events` | render + gate ของแอป | flow เดินผิดทาง / ไปไม่ถึง |
| **สัญญากับ API** — `tools`, `args`, `gating`, `returns` | executor + mock generator | tool call ถูกปฏิเสธ / mock ไม่ตรง |
| **ข้อความ** — `constraints[].desc`, `states[].note`, `desc` ต่างๆ | **โมเดลอ่านเป็นภาษา** | โมเดลทำตามที่เขียน ผิดก็ทำผิดตาม |

กลุ่มที่ 3 อันตรายที่สุด — เคยเขียน `"ปิดด้วย close (text 1052)"` โดย 1052 คือ `close_paid`
⇒ agent ปิดสายด้วย *"ขอบคุณสำหรับการชำระเงิน"* ให้ลูกค้าที่ยังไม่จ่าย **นั่นไม่ใช่โมเดลมั่ว มันทำตาม spec เป๊ะ**

---

## ระดับบนสุด — 22 key

### บังคับ 5

| key | ค่า | ใครอ่าน |
|---|---|---|
| `events` | `{ชื่อ: {desc, cues[]}}` | render ลง prompt · transition อ้างถึง |
| `tools` | `{declarations: [...]}` | schema ที่โมเดลเห็น + executor |
| `states` | `[...]` | flow ทั้งหมด |
| `faq_routing` | `{note, routes[]}` | คำถามแทรก |
| `constraints` | `[...]` | กฎ |

### ตัวตน

| key | หมายเหตุ |
|---|---|
| `flow_id` | `AEON-outbound-remind` — **ต้องใส่** ฝั่งเทรนใช้ระบุบริษัท และชื่อไฟล์ไม่ได้บอก "kind" |
| `company` | **ไม่ต้องใส่** — loader ดึงจากชื่อไฟล์ (`AEON.company.json` → `AEON`) ใส่ก็ได้ถ้าต่างจากชื่อไฟล์ (เช่น `_TEMPLATE` → `YOURCO`) |
| `display_name` | ชื่อที่โชว์ใน UI · ไม่ใส่ = ใช้รหัสบริษัท |

### บุคลิกของ agent — render ขึ้นหัว prompt

`role` · `agent_role` · `goal` · `legal_note`

### ข้อมูลลูกค้า

| key | ใครอ่าน |
|---|---|
| `crm_fields` | รายชื่อ field ที่จะโชว์ในบล็อก "ข้อมูลลูกค้า" (ดูหัวข้อถัดไป) |
| `crm_labels` | ชื่อไทยของ field · ไม่ใส่ = โชว์ชื่อ field ดิบ |
| `session_init` | `{url, method, timeout, note}` — ยิงครั้งเดียวตอนเปิดสาย |

### ประโยคและกฎเสริม

| key | ใครอ่าน |
|---|---|
| `catalog` | **ประโยคทั้งหมด (list)** — agent พูดได้แค่ในนี้ |
| `auxiliary_templates` | `{allowed: [{fine_state}]}` — beat ที่ใช้ได้ "ตามบริบท" นอก flow |
| `fallback_fine_state` | ประโยคที่ใช้เมื่อไม่รู้จะพูดอะไร · ไม่ใส่ = `faq_repeat` |
| `compliance` | `{verify_fine_states, disclose_fine_states, close_fine_states}` — **ฝั่งเทรนบังคับต้องมี `close_fine_states`** |

**ไม่มี `outcomes` แล้ว** — ผลสายที่ flow ทำได้ มาจาก `states[].outcome` และ FAQ route ที่ terminal
flow ที่ไม่มี state ไหนประกาศ = ไม่มีผลสาย ไม่ต้องประดิษฐ์ · และ **tool ปิดสายก็ไม่บังคับ**
(`required_at: end_of_call` มีได้ไม่เกิน 1 ตัว จะไม่มีเลยก็ได้)

### รับไว้เพื่อความเข้ากันได้ย้อนหลัง

`spec_version` · `company` · `catalog_inline` · `outcomes` — ไฟล์รูปเก่ายังโหลดได้ แต่ไม่ต้องเขียนใหม่

---

## `crm_fields` / `crm_labels` — เข้าใจให้ตรง

**มันคือ "รายการข้อมูลลูกค้าที่จะโชว์ให้ agent อ่านใน prompt"** ไม่ใช่ slot ที่ประโยคพูด

```json
"crm_fields": ["today", "customer_name", "shop_name", "due_date", "amount", "max_extend_days"],
"crm_labels": { "today": "วันนี้", "customer_name": "ชื่อลูกค้า",
                "due_date": "วันครบกำหนดเดิม", "amount": "ยอดค่างวด" }
```

ผลใน prompt:
```
## ข้อมูลลูกค้า (CRM Snapshot)
- **วันนี้:** {today}
- **ชื่อลูกค้า:** {customer_name}
- **วันครบกำหนดเดิม:** {due_date}
- **ยอดค่างวด:** {amount}
```

| ใส่อะไร | ได้อะไร |
|---|---|
| ทั้ง `crm_fields` + `crm_labels` | `- **วันนี้:** {today}` |
| แต่ `crm_fields` | `- **today:** {today}` ← ชื่อ field ดิบ |
| ไม่ใส่ `crm_fields` | บล็อกว่าง — **agent ไม่เห็นข้อมูลลูกค้าเลย** |

### ค่ามาจาก 3 ที่

```
① session_init API   GET {API_BASE}/<CO>/init  →  key ที่คืนมา = ชื่อ placeholder ตรงๆ
                     { "customer_name": "สมชาย ใจดี", "due_date": "2026-08-27 (Thursday)", … }
② persona            data/test-cases/_builder_personas.json — ใช้ตอนเล่นใน playground
③ แอปเติมให้         `today` ตัวเดียว
```

### เพิ่ม field ใหม่ 1 ตัว ต้องทำ 3 อย่าง

```
1. เพิ่มชื่อใน crm_fields
2. เพิ่มคำแปลใน crm_labels
3. ให้ session_init (หรือ persona) คืน key ชื่อเดียวกันมา
```
ขาดข้อ 3 ⇒ prompt โชว์ `{customer_phone}` เปล่าๆ · `lint_spec.py` เตือนด้วย `unfillable_placeholder`

> **ไม่ต้องอยู่ใน `crm_fields` ก็ใช้เป็น `[slot]` ในประโยคได้** — `crm_fields` มีผลแค่
> "โชว์ในบล็อกข้อมูลลูกค้าไหม" · เช่น `company_phone` ของ SHOP ไม่ได้อยู่ใน `crm_fields`
> แต่ประโยคยังพูดได้

> **ทำไม derive จากประโยคไม่ได้** — ทดสอบแล้ว: `today`/`due_status`/`doctor_schedule`
> ประกาศไว้แต่ไม่มีประโยคไหนพูด (agent ต้องรู้เพื่อ *คิด*) ส่วน `new_slot`/`valid_dates`
> ประโยคพูดแต่มาจาก **tool response** ⇒ เป็นการตัดสินใจของคนเขียน ไม่ใช่ผลพลอยได้

---

## `states[]` — 13 key

```json
{ "id": "ptp_capture", "phase": "close", "terminal": true,
  "templates": [{"fine_state": "close"}],
  "entry_tools": ["get_current_datetime", "record_verbal_commitment", "record_outcome"],
  "outcome": {"result": "ptp", "reasons": ["ptp", "minimum"]},
  "note": "เรียก get_current_datetime ก่อนเสมอ …",
  "on": [{"event": "refuses", "to": "close_refused"}] }
```

| key | ใครอ่าน / เกิดอะไร |
|---|---|
| `id` ★ | ชื่อ state · `on[].to` อ้างถึง |
| `phase` | `opening` \| `main` \| `close` — จัดหัวข้อใน prompt + สีใน diagram |
| `initial` | `true` — **มีได้ state เดียว** |
| `terminal` | `true` — สายจบที่นี่ แอปหยุดรับเทิร์นต่อ |
| `templates` | ประโยคที่พูดได้ที่ state นี้ |
| `on` | ทางแยกออกจาก state นี้ |
| `entry_tools` | **tool ที่ต้องเรียกก่อนพูด** · gate บังคับจริง · state ปิดสายต้องมี tool ปิดสายในนี้ |
| `outcome` | `{result, reasons[], note, inferred}` |
| `note` | **โมเดลอ่าน** |
| `max_visits` | เข้า state นี้ได้กี่ครั้ง · render เป็นข้อความ |
| `counts_as` | นับ state นี้เป็นอย่างอื่นสำหรับ constraint ที่นับจำนวน (เช่น `pay_ask`) |
| `spec_note` · `inferred` | เมทาดาทา ไม่ render |

### `templates[]` — ตรรกะสำคัญที่สุดในไฟล์

```json
[{"fine_state":"a"}, {"fine_state":"b"}]                    chain — พูดทั้ง a และ b ในเทิร์นเดียว
[{"fine_state":"a","when_event":"x"}, {"...":"when_event":"y"}]   ทางเลือกตาม event
[{"any_of":["a","b"]}]                                      ขั้นเดียว พูด a หรือ b ก็ได้
```
**กฎ: หลาย template + ไม่มี `when_event` เลย = chain** · มี `when_event` แม้ตัวเดียว = ทางเลือก

key: `fine_state` · `any_of` · `when_event` · `optional` · `note` · `inferred` · `counts_as`

### `on[]`

`event` ★ (ต้องมีใน `events`) · `to` ★ (ต้องเป็น state ที่มีจริง) · `tools` · `note` · `inferred` · `spec_note`

---

## `tools.declarations[]` — 9 key

```json
{ "name": "save_appointment", "impl": "http",
  "url": "{API_BASE}/AMT/save_appointment", "method": "POST",
  "desc": "บันทึกสถานะนัดหมาย — เรียกก่อนปิดสายเสมอ",
  "args":    { "status": {...}, "new_slot": {...} },
  "returns": { "recorded": {"type":"boolean"} },
  "gating":  { "required_at": "end_of_call", "max_successful_calls": 1 },
  "mock":    { "rules": [...], "default": {...} } }
```

| key | ใครอ่าน |
|---|---|
| `name` ★ | ชื่อที่โมเดลเรียก · **แอปห้ามมีลอจิกผูกกับชื่อนี้** |
| `impl` ★ | **มีแค่ `http` กับ `generic`** |
| `url` · `method` | ปลายทาง · `{API_BASE}` มาจาก env |
| `desc` | **โมเดลอ่าน** |
| `args` | schema ที่โมเดลเห็น + ตัวตรวจของแอป |
| `returns` | mock generator สร้าง response จากตรงนี้ · `one_of_from` เช็คได้ว่าชี้ field จริง · ไม่มี = กล่องดำ |
| `gating` | เมื่อไหร่เรียกได้/ต้องเรียก |
| `mock` | response ปลอมสำหรับ playground |

### `args.<name>`

| key | ผล |
|---|---|
| `type` | `string` \| `number` |
| `desc` | **โมเดลอ่าน** |
| `optional` | `true` = ไม่บังคับ |
| `enum` | จำกัดค่า |
| `format` | `"YYYY-MM-DD (Weekday)"` → เปิดการตรวจรูปแบบวันที่ |
| `required_when` | `{arg, equals}` — บังคับเฉพาะเมื่อ arg อื่นเป็นค่าหนึ่ง |
| `one_of_from` | `{tool, field}` — ค่าต้องอยู่ในสิ่งที่ tool นั้นเพิ่งคืนมา |

> **เขียนเงื่อนไขใน `desc` ไม่มีผล** — `"บังคับเมื่อ status=rescheduled"` ระบบอ่านไม่ออก
> ต้องใช้ `required_when` · เคยพลาด: บังคับตลอด (เคสอื่นพัง) หรือไม่บังคับเลย (บันทึกค่าว่างแล้วปิดสาย)

### `gating` — อันไหนบังคับจริง

```
✅ required_at: "end_of_call"     ต้องมี tool เดียวที่ประกาศ = closer ของ flow
✅ max_successful_calls · max_calls_per_conversation
✅ requires_prior · must_precede · args_must_match_commitment
❌ after_event · required_before · required_before_state
   → prompt-level เท่านั้น: ปลายสายเป็นคนจริง แอปเดา event ไม่ได้ เดาผิดจะบล็อกสายที่ถูก
```

### `mock`

```json
"mock": {
  "rules":   [{"when": {"arg":"date", "matches":"^2026-0[89]-"},
               "body": {"in_range": true, "valid_dates": ["{{body 'args.date'}}"]},
               "label": "in range"}],
  "default": {"in_range": false, "valid_dates": []}
}
```
`gen_mockoon.py` แปลง `rules` เป็น Mockoon rule (จับก่อน `default`) · body รองรับ Handlebars
(`{{body 'args.x'}}`, `{{now 'yyyy-MM-dd'}}`, `{{dateTimeShift …}}`)
**ใช้ทดสอบสาขาที่ API เป็นตัวตัดสิน** — วัดแล้วโมเดลเลือกประโยคตาม `in_range` ได้ถูก

---

## `constraints[]` — 2 แบบ

```json
{ "id":"disclose_once", "type":"once_per_call", "template_fine_state":"disclose_balance",
  "enforce":["prompt","reward"], "desc":"แจ้งยอดพูดครั้งเดียวต่อสาย" }   ← มี type = ระบบบังคับ

{ "enforce":["prompt","reward"], "desc":"ความเงียบไม่ใช่การตกลง …" }      ← ไม่มี type = เขียนลง prompt
```

`type` 10 ตัว: `max_occurrences` · `once_per_call` · `repeat_only_on` · `forbid_after_event` ·
`no_repeat_answered_request` · `immediate_transition_on` · `max_templates_per_reply` ·
`resume_after_interrupt` · `require_tool_before_end` · `tool_pair`

> `outcome_precondition` **เลิกใช้แล้ว** — ผูกกับแนวคิด "ผลสาย" ที่ไม่บังคับอีกต่อไป และไม่เคยมีโค้ดอ่าน

`enforce` ∈ `prompt` · `reward` · `backend` — **เป็นการประกาศเจตนา ไม่ใช่สวิตช์**
(`session` ถูกเลิกใช้: แอปให้กลไก ไม่บังคับนโยบายของบริษัทใดบริษัทหนึ่ง)
ใส่ `reward` ไม่ได้ทำให้ reward บังคับเอง

key เสริม: `counts` · `max` · `on_exceed{to}` · `event` · `to` · `when` · `tool` · `first` ·
`second` · `template_fine_state(s)` · `args_must_match` · `exceptions` · `inverted` ·
`reject` · `require_instead` · `source_ref`
(`source_ref`, `require_instead` ยังไม่มีโค้ดอ่าน)

### เขียนกฎยังไงให้ได้ผล

```
✅ ประโยคไหนถูก + ห้ามใช้อะไรแทน + เพราะอะไร      → ย้ายข้ามบริษัทได้เลย
❌ สอนเป็นขั้นตอนที่ลงท้ายด้วย "ปิดสายทันที"      → คะแนนตก + agent เริ่มบันทึกคำมั่นปลอม
```
**เติมทีละ 1-2 ข้อแล้ววัด** — เติม 6 ข้อรวดเดียวเคยทำให้ agent บันทึก PTP ให้ลูกค้าที่บอกว่าไม่มีเงิน

---

## `catalog` — เขียน 3 field พอ

```json
"catalog": [
  { "text_id": 9001, "_fine_state": "greet_remind",
    "template": "สวัสดีค่ะ คุณ[customer_name] ทางร้าน[shop_name] …" }
]
```

| key | ผล |
|---|---|
| `text_id` ★ | **ห้ามซ้ำ ห้ามเปลี่ยนทีหลัง** — เปลี่ยนแล้วกฎที่อ้างเลขนี้จะชี้ผิด |
| `_fine_state` ★ | ชื่อจังหวะที่ state เรียกใช้ · **หลาย text_id ต่อหนึ่ง `_fine_state` ได้** = สำนวนต่างกัน |
| `template` ★ | ข้อความ · `[field]` = slot · `{{if x}}…{{else}}…{{/if}}` = เงื่อนไข |
| `state` · `intent_name` · `category` | **โมเดลอ่านผ่าน catalog block** — จัดกลุ่มหัวข้อ / ป้าย `[A]`,`[B]` / ชื่อในวงเล็บ · ไฟล์เก่ามีอยู่ ให้คงไว้ · ไฟล์ใหม่ไม่ต้องเขียน ระบบ derive ให้ |
| `hint` | โน้ตให้คนที่แก้ไฟล์ ไม่ถึงโมเดล |

**ตัดออกแล้ว** — `is_closer` · `is_demand` · `is_acknowledgment` · `expects_response`
(Builder เคยเขียนลงไป ไม่มีใครอ่าน · ยืนยัน prompt ทั้งสองส่วนเท่าเดิม)

---

## `events` · `faq_routing` · `outcomes`

```json
"events": {
  "confirms_pay": { "desc": "ลูกค้ายืนยันจะชำระตามวันเดิม",
                    "cues": ["ได้ค่ะ พรุ่งนี้โอนให้", "รับทราบครับ", "โอเคค่ะ"] } }
```
`desc` + `cues` **render ลงตรงจุดที่ transition ใช้ event นั้น** ⇒ ใส่ cue ที่ลูกค้าพูดจริงเยอะๆ

> ถ้า flow มีทางแยกตอนลูกค้าเงียบ **ต้องมี `no_input`** พร้อม cue `"..."`, `"(เงียบ)"`
> **แต่ cue อย่างเดียวไม่พอ** — วัดแล้วโมเดลยังตีความว่าเงียบ = ตกลง ต้องมีกฎห้ามใน `constraints` ด้วย
> (ใส่กฎแล้ว การบันทึก PTP ปลอมลดจาก 7/12 เหลือ 1/12)

```json
"faq_routing": { "routes": [
  {"intent":"cost", "templates":[{"fine_state":"faq_cost"}], "then":"resume"},
  {"intent":"wrong_name", "templates":[{"fine_state":"faq_wrong_name"}],
   "then":{"outcome":{"result":"tin","reasons":["wrong_name"]}, "terminal":true}} ]}
```

> **`faq_routing` render เป็นตาราง · `constraints` render เป็นคำสั่งมีหมายเลข**
> วัดแล้ว route ที่มีแต่ในตารางโมเดลมักไม่ใช้ — beat สำคัญควรมีกฎย้ำใน `constraints` ด้วย

```json
"outcomes": { "required_at_close": true,
  "results": { "ptp": {"reasons":["ptp","minimum"], "desc":"รับปากจะชำระ"} } }
```
**ไม่มีชุดสำเร็จรูป** คิดคำของธุรกิจคุณเอง · `result` ที่ state ประกาศต้องอยู่ในนี้

---

## key ที่เลิกใช้ — validator บอกว่าใช้อะไรแทน

| เลิกใช้ | ใช้อะไรแทน |
|---|---|
| `compose` · `render_all_templates` | หลาย template ใน state เดียว (ไม่มี `when_event`) = chain อยู่แล้ว |
| `group` | ใช้ `when_event` แยกทางเลือก / ไม่ใส่ = chain |
| `template_mode` | อนุมานจาก `when_event` |
| `catalog: "__inline__"` + `catalog_inline` | `catalog` เป็น list ตรงๆ |
| `company` · `spec_version` | loader ดึงจากชื่อไฟล์ / อนุมาน |
| `description` · `sources` · `test_cases` · `instruction_version` | ไม่มีใครอ่าน |

---

## ตรวจก่อนใช้

```bash
PYTHONPATH=. python3 .claude/skills/new-company/scripts/lint_spec.py data/flows/<CODE>.company.json
PYTHONPATH=. python3 .claude/skills/new-company/scripts/spec_to_mermaid.py data/flows/<CODE>.company.json
PYTHONPATH=. python3 .claude/skills/new-company/scripts/smoke_company.py <CODE> --case TC-<CODE>-BUILD-001 --say "…"
```

lint รัน **ทั้งสอง validator** (`validate_strict` ล็อกชื่อ key · `validate_flow_spec` ตรวจว่ารันได้)
บวกเช็คที่ทั้งสองจับไม่ได้:
```
name_id_mismatch          กฎเขียนชื่อ beat คู่กับ text_id ของ beat อื่น
terminal_records_nothing  state ปิดสายไม่มี tool ปิดสายใน entry_tools
unreachable_state         state ที่เดินไปไม่ถึง
sentence_never_used       ประโยคที่ไม่มี state หรือ FAQ route ไหนเรียก
one_of_from_unknown_field one_of_from ชี้ field ที่ tool ไม่ได้ประกาศใน returns
condition_only_in_prose   เงื่อนไขเขียนแต่ใน desc
unfillable_placeholder    [slot] ที่ไม่มีใครเติม
```

---

## ตัวอย่างจริง (ขนาดหลังเก็บกวาด)

```
SHOP.company.json       12 key · 12 KB   ← สร้างใหม่ ไม่มีขยะสะสม อ่านง่ายสุด
_TEMPLATE.company.json  14 key · 37 KB   ← ไฟล์เปล่าสำหรับเริ่ม
AEON.company.json       15 key · 60 KB   ← เต็มรูปแบบ 14 state · FAQ 16 route · 38 กฎ
KBANK / SKL             16 key · 31 KB
AMT.company.json        17 key · 23 KB   ← งานนัดหมาย
```
