# `<CODE>.company.json` — locked format

**สถานะ: LOCKED** — เอกสารนี้บรรยาย schema ที่ตรึงแล้ว key ที่ไม่อยู่ในนี้ถูก **ปฏิเสธ**
ไม่ใช่ถูกเพิกเฉย (`validate_strict` ใน `demo/server/flow/flowspec.py`) การเพิ่ม/ลบ key
คือการแก้ format ไม่ใช่การแก้ spec

หนึ่งบริษัท = หนึ่งไฟล์ วางไฟล์ = สร้างบริษัท ลบไฟล์ = ลบบริษัท แอปไม่ถือ logic ของ
บริษัทใดเลย **ทุกอย่างที่ agent ทำมาจากไฟล์นี้**

ชื่อไฟล์คือ identity: `AEON.company.json` → `company: "AEON"`,
`flow_id: "AEON-outbound-call"` — เติมโดย `load_tenant_spec` ด้วย `setdefault` **เฉพาะ
เมื่อไฟล์ไม่ได้เขียนไว้เอง** (ถ้าเขียนไว้ ค่าในไฟล์ชนะ — AEON เขียนไว้เป็น
`AEON-outbound-remind`) ไฟล์ใหม่ไม่ต้องใส่ ปล่อยให้ชื่อไฟล์เป็นตัวกำหนด เพราะเป็นที่เดียว
ที่ขัดกันเองไม่ได้ ขึ้นต้นด้วย `_` = template ไม่ขึ้นทะเบียน

---

## หลักที่ตัดสินว่า field ควรมีไหม

> **แพลตฟอร์มให้ "กลไก" · tenant ให้ "นโยบาย"**

กลไก = ทุก tenant ใช้เหมือนกัน และงอกจากโครงสร้างที่ทุก spec มี — ตอบด้วย text_id,
เติม slot, เรียก tool, "template ใน state เดียว = หนึ่งเทิร์น", "`entry_tools` รันก่อนพูด",
สัญญาของ argument

กฎที่มีเฉพาะบางบริษัท **ไม่ใช่เรื่องของแพลตฟอร์ม** แม้จะ parameterise จาก spec ก็ตาม
เพราะชั้นบังคับยังคงมีอยู่กับทุกคน → กฎแบบนั้นเขียนใน `constraints` (ไปอยู่ใน prompt)
หรือให้ API ของ tenant ปฏิเสธเอง

---

## 1 · ภาพรวม 22 key

จำเป็น 5 · ที่เหลือใส่หรือไม่ใส่ก็ได้

| key | ชนิด | ใช้ตอนไหน | ถ้าไม่ใส่ |
|---|---|---|---|
| **`events`** ✱ | `{ชื่อ: คำอธิบาย}` | คำศัพท์ที่ `states[].on[].event` และ `constraints[].event` อ้างได้ | validate ไม่ผ่าน |
| **`tools`** ✱ | `{declarations, …}` | สร้าง tool schema ให้โมเดล + ยิง API จริง | validate ไม่ผ่าน |
| **`states`** ✱ | `[…]` | flow ทั้งหมด — พูดอะไร เรียกอะไร ไปไหนต่อ จบยังไง | validate ไม่ผ่าน |
| **`faq_routing`** ✱ | `{routes: […]}` | คำถามแทรกกลางสาย → ตอบแล้วกลับ flow | validate ไม่ผ่าน (ใส่ `{"routes": []}` ได้) |
| **`constraints`** ✱ | `[…]` | กฎ — เกือบทั้งหมดกลายเป็นข้อความใน prompt | validate ไม่ผ่าน (ใส่ `[]` ได้) |
| `catalog` | `[…]` | ประโยคทุกประโยคที่พูดได้ (text_id ↔ ข้อความ) | agent ไม่มีอะไรพูด |
| `display_name` | `str` | ชื่อที่โชว์ใน UI + แทน `[company]` | ใช้ `company` แทน |
| `role` | `str` | บรรทัดแรกของ prompt — agent เป็นใคร | ข้ามหัวข้อนั้น |
| `agent_role` | `str` | ต่อจาก `role` | ข้าม |
| `goal` | `str` | เป้าหมายของสาย | ข้าม |
| `legal_note` | `str` | ข้อกฎหมายที่ต้องเตือนโมเดล | ข้าม |
| `crm_fields` | `[str]` | field ที่ CRM ส่งมาและโมเดล**ได้เห็น** | โมเดลไม่เห็นข้อมูลลูกค้า |
| `crm_labels` | `{field: ป้ายไทย}` | ป้ายกำกับใน CRM Snapshot | ใช้ชื่อ field ดิบ |
| `session_init` | `{url, method, timeout, on_failure}` | ยิงครั้งเดียวตอนเปิดสาย ดึงข้อมูลลูกค้า | ไม่มี CRM — template ที่อ้าง field จะพูด `[placeholder]` |
| `auxiliary_templates` | `{allowed: […]}` | ประโยคที่พูดได้ทุกที่ ไม่ผูก state | beat นอก state = คำเตือน |
| `fallback_fine_state` | `str` | beat ที่ใช้เมื่อไม่รู้จะพูดอะไร | ไม่มีทางถอย |
| `compliance` | `{verify/disclose/close_fine_states}` | **แอปไม่อ่าน** — ฝั่งเทรนอ่าน | ไม่กระทบ demo |
| `company` `flow_id` `spec_version` | `str` | รับไว้เพื่อไฟล์เก่ายังโหลดได้ | เติมจากชื่อไฟล์ ✅ แนะนำให้ไม่ใส่ |
| `catalog_inline` `outcomes` | — | รูปเก่า ยังโหลดได้ | **อย่าใส่ในไฟล์ใหม่** |

✱ = จำเป็น

---

## 2 · `states` — flow

```json
{
  "id": "disclose_ask",
  "phase": "main",
  "initial": false,
  "terminal": false,
  "templates": [{"fine_state": "disclose_balance"}, {"fine_state": "ask_pay_today"}],
  "entry_tools": ["check_account_status"],
  "on": [{"event": "agrees", "to": "close_ptp", "tools": ["record_verbal_commitment"]}],
  "outcome": {"result": "ptp", "reasons": ["ptp"], "desc": "รับปากจ่าย"},
  "max_visits": 2,
  "note": "…", "spec_note": "…", "inferred": true, "counts_as": false
}
```

| key | ค่า | ความหมายตอนรัน |
|---|---|---|
| `id` ✱ | `str` | ชื่อ state — `on[].to`, `constraints[].to` อ้างถึง |
| `templates` ✱ | `[…]` | **ทุกตัวรวมกัน = หนึ่งเทิร์น** ไม่ใช่ทางเลือก |
| `phase` | `open` `main` `close` … | จัดกลุ่มใน prompt |
| `initial` | `true` | state แรกของสาย — มีได้ตัวเดียว |
| `terminal` | `true` | เข้าแล้วจบสาย |
| `entry_tools` | `[ชื่อ tool]` | เรียก **ก่อนพูด** ทุกครั้งที่เข้า state |
| `on` | `[{event,to,tools?}]` | เจอ event นี้ → ไป state นั้น |
| `outcome` | ดูข้างล่าง | ผลของสายถ้าจบที่ state นี้ |
| `max_visits` | `int` | เข้าซ้ำได้กี่ครั้ง |
| `counts_as` | `false` | ไม่นับเป็นการถามจ่าย (ตัวนับ `pay_ask`) |
| `note` | `str` | เข้า prompt |
| `spec_note` `inferred` | `str` `bool` | บันทึกถึงคนเขียน **ไม่เข้า prompt** |

### `templates[]` — 7 key

| key | ค่า | ความหมาย |
|---|---|---|
| `fine_state` | `str` | beat หนึ่งตัว |
| `any_of` | `[str]` | **ทางเลือก** — พูดตัวไหนก็ผ่าน (ใช้แทน `fine_state`) |
| `when_event` | `str` | ใช้ประโยคนี้เฉพาะเมื่อเกิด event นี้ ⇒ กลุ่มนี้เป็น**ทางเลือก** |
| `optional` | `true` | ไม่พูดก็ได้ |
| `counts_as` | `false` | ไม่นับเป็นการถามจ่าย |
| `note` `inferred` | | บันทึก |

> **กติกาเดียวที่ต้องจำ:** หลาย template ใน state เดียว **ไม่มี** `when_event` = **chain** (ต้องพูดครบในเทิร์นเดียว) · **มี** `when_event` = ทางเลือก
> key เก่า `compose` `group` `template_mode` `render_all_templates` ถูก**ปฏิเสธ**พร้อมข้อความว่าใช้อะไรแทน

### `outcome`

```json
{"result": "ptp", "reasons": ["ptp","minimum"],
 "reason_by_event": {"refuses": "ลูกค้าแจ้งไม่มีเงิน"}, "desc": "…"}
```

`result` = คำที่จะส่งเข้า tool ปิดสาย · **ไม่มีคลังคำสำเร็จรูป** — สายทวงหนี้ใช้
`ptp`/`refused` งานนัดใช้ `confirmed`/`rescheduled` แบบสำรวจใช้ `completed`/`declined`
คลังคำของ flow = ทุก `result` ที่ `states[].outcome` และ `faq_routing` ประกาศไว้ รวมกัน
(`derive_outcomes`) **ไม่มี block `outcomes` แยกอีกแล้ว** — state เป็นตัวกำหนด
ไม่มี state ไหนประกาศ = flow นี้ไม่มีผลลัพธ์ ก็ถูกต้อง

---

## 3 · `catalog` — ประโยค

```json
{"text_id": 1018, "_fine_state": "disclose_balance",
 "template": "ยอดค้างชำระ [amount] บาท ครบกำหนด [due_date] ค่ะ",
 "hint": "ใช้ตอนแจ้งยอดครั้งแรกหลังยืนยันตัวตน",
 "state": "disclose_ask", "intent_name": "…", "category": "A"}
```

| key | จำเป็น | ใช้ทำอะไร |
|---|---|---|
| `text_id` ✱ | ✅ | เลขที่โมเดลเรียก — `reply([1018])` |
| `_fine_state` ✱ | ✅ | beat ที่ประโยคนี้สังกัด (หนึ่ง beat มีได้หลายสำนวน) — **มี `_` นำหน้า** ชื่อไม่มี `_` ใช้ใน `states[].templates` เท่านั้น ใส่ผิดที่ถูกปฏิเสธ |
| `template` ✱ | ✅ | ข้อความจริง `[slot]` เติมตอนรันจาก CRM |
| `hint` | | บอกโมเดลว่าเมื่อไหร่ควรใช้สำนวนนี้ |
| `state` `intent_name` `category` | | **เข้า prompt** — จัดกลุ่มและติดป้าย `[A]`/`[B]` |
| `is_closer` `is_demand` `is_acknowledgment` `expects_response` | | ป้ายพฤติกรรม |
| `company` `note` `desc` `_hint_where` `_example_AEON` | | รับไว้ |
| `_`-นำหน้าอื่นๆ | | ผ่านได้ ไม่มีใครอ่าน |

> ⚠ **ห้ามเขียนชื่อ beat ข้างเลขที่ไม่ใช่ของมัน** — เป็นบั๊กที่แพงที่สุดที่เคยเจอ
> อาการ "โมเดลเลือกประโยคปิดผิด" หลายเดือน คือมันทำถูกตามประโยคที่ค้างอยู่ใน prose
> `lint_spec.py` ตรวจให้

---

## 4 · `tools`

```json
"tools": {
  "declarations": [{
    "name": "check_account_status",
    "desc": "อ่านข้อมูลบัญชี",
    "impl": "http",
    "url": "{API_BASE}/AEON/check_account_status",
    "method": "POST",
    "args": {"last_4_digits": {"type": "string", "optional": true}},
    "returns": {"amount": {"type": "number", "desc": "ยอดค้าง"}},
    "gating": {"max_successful_calls": 1, "required_at": "end_of_call"},
    "mock": {"rules": [{"when": {"arg": "date", "matches": "2026-0[89]"},
                        "body": {"in_range": true}, "label": "อยู่ในเกณฑ์"}],
             "default": {"in_range": false}}
  }],
  "validation": {"date_format": "YYYY-MM-DD (Weekday)", "payment_channels": ["…"]},
  "notes": ["…"],
  "require_kyc": false
}
```

### `declarations[]`

| key | ค่า | ใช้ตอนไหน |
|---|---|---|
| `name` ✱ | `str` | ชื่อที่โมเดลเรียก และที่ `entry_tools`/`gating` อ้าง |
| `desc` ✱ | `str` | คำอธิบายใน tool schema |
| `impl` ✱ | **`http`** \| **`generic`** | `http` = ยิง `url` จริง · `generic` = ตอบจากที่ประกาศไว้ (ยังไม่มี API) — **มีแค่สองค่านี้** |
| `url` | `str` | ใช้เมื่อ `impl: http` — `{API_BASE}` แทนด้วย env `AAX6_API_BASE` |
| `method` | `POST` \| `GET` | ค่าเริ่มต้น `POST` |
| `args` | `{ชื่อ: สัญญา}` | ดูตาราง argument |
| `returns` | `{field: {type, desc}}` | รูปคำตอบ — ทำให้ mock เหมือนจริง, ให้ `one_of_from` ตรวจได้, และบอกคนอ่านว่าโมเดลจะเห็นอะไร |
| `gating` | `{…}` | ดูตาราง gating |
| `mock` | `{rules, default}` | คำตอบปลอมสำหรับ `gen_mockoon.py` |

### สัญญาของ argument

| key | ค่า | บังคับตอนรันไหม |
|---|---|---|
| `type` | `string` `number` `boolean` `array` | เข้า tool schema |
| `optional` | `true` | ไม่ใส่ = **จำเป็น** ขาด → `missing_required_args` ✅ |
| `enum` | `[str]` | เข้า schema |
| `format` | `"YYYY-MM-DD (Weekday)"` `"HH:MM"` | ผิดรูป → `date_format_invalid` ✅ |
| `desc` | `str` | คำอธิบายให้โมเดล |
| `required_when` | `{arg, equals}` | จำเป็น**เมื่อ** arg อื่นเป็นค่านั้น ✅ |
| `one_of_from` | `{tool, field}` | ต้องเป็นค่าที่ tool นั้นเคยคืนมา ไม่งั้น `value_not_offered` ✅ |

> `required_when` + `one_of_from` คือคู่ที่ปิดรูโหว่ "เขียนค่าว่างแล้วปิดสาย" —
> `optional: true` เดี่ยวๆ ยอมให้ `""` ผ่านได้

### `gating`

| key | ค่า | บังคับตอนรันไหม |
|---|---|---|
| `max_successful_calls` | `int` | ✅ เกิน → `<tool>_already_recorded` (cap=1) / `<tool>_call_limit_reached` |
| `max_calls_per_conversation` | `int` | ✅ เกิน → `already_checked` (cap=1) / `<tool>_call_limit_reached` — นับรวมครั้งที่ถูกปฏิเสธ |
| `requires_prior` | `ชื่อ tool` | ✅ ต้องเรียกตัวนั้นสำเร็จก่อน |
| `must_precede` | `ชื่อ tool` / `[…]` | ✅ ต้องมาก่อนตัวนั้น |
| `args_must_match` | `[ชื่อ arg]` | ✅ arg เหล่านี้ต้องตรงกับที่ `requires_prior` บันทึกไว้ — **spec เป็นคนบอกว่า field ไหน** |
| `required_at` | `"end_of_call"` | ✅ ตัวปิดสาย — ประกาศได้ **ตัวเดียว**ต่อ spec |
| `after_event` | `ชื่อ event` | ❌ ขึ้น prompt เท่านั้น |
| `note` | `str` | ❌ ขึ้น prompt เท่านั้น |
| `required_before_state` | `ชื่อ state` | ❌ prompt เท่านั้น |
| `required_before` | `str` | ❌ prompt เท่านั้น |

`args_must_match` ต้องมากับ `requires_prior` — ตัวหลังบอกว่า "ต้องมาหลังใคร"
ตัวแรกบอกว่า "arg ไหนต้องตรงกับ call นั้น"

**10 key นี้เท่านั้น** — พิมพ์ผิดจะถูกปฏิเสธ (`GATING_KEYS`) ไม่ใช่เงียบแล้วไม่ทำงาน

**ลำดับระหว่าง tool ประกาศได้ทางเดียว** — `requires_prior` ที่ตัวที่ต้องพึ่งคนอื่น
ใช้ `must_precede` เฉพาะตอนตัวเดียวต้องมาก่อนหลายตัว (เขียนที่เดียวแทนหลายที่)
`constraints type: tool_pair` เลิกใช้แล้ว — เคยประกาศเส้นเดียวกันซ้ำเป็นทางที่สาม

### `mock`

รูปแบบ `when` มีสองแบบ:

```jsonc
"when": {"arg": "date", "matches": "^2026-09"}                 // regex ตรงๆ
"when": {"arg": "date",                                        // หน้าต่างวันจริง
         "within_days_of": {"field": "due_date", "days_field": "max_extend_days"}}
```

แบบหลังให้ตัว generate คำนวณจาก CRM — `valid_dates` ออกมาเป็นวันจริงทั้งช่วง คำนวณสด
ทุกครั้งที่ถูกเรียก ใช้เมื่อ requirement พูดว่า "ไม่เกิน N วัน" **อย่าเขียน regex เดา** —
ของเดิมใช้ `^2026-0[89]-` แล้วรับวันที่เกินเกณฑ์ไป 26 วัน

⚠️ Mockoon ไม่มี helper เปรียบเทียบ (`lte`/`gt` ทดสอบแล้วไม่ทำงาน) — regex ที่เลือก
response จึงตรึงตอน generate ต้อง regenerate เมื่อข้ามวัน · ตัวบังคับจริงคือ
`one_of_from` ที่ arg ปลายทาง ซึ่งเทียบกับ `valid_dates` ที่คำนวณสด

### วันที่ต้องอิง "วันนี้" เสมอ

persona เก็บวันแบบตายตัวจะถูกแค่วันที่เขียน — เตือนนัดที่ผ่านมาแล้ว 3 เดือน ให้ใช้

```jsonc
"appointment_date_offset_days": 2      // → วันนี้ + 2
"due_date_offset_days": -7             // → วันนี้ - 7 (ค้างชำระ)
```

แอปแปลงตอนเปิด session · `gen_mockoon` แปลงเป็น `dateTimeShift` ให้ API คืนค่าตรงกัน
(`due_offset_days` เป็นชื่อเดิม ยังใช้ได้ ชี้ไป `due_date`)

```json
"mock": {"rules": [{"when": {"arg": "date", "matches": "regex"},
                    "body": {…}, "label": "…"}],
         "default": {…}}
```
`gen_mockoon.py` อ่านไปสร้าง Mockoon env · body ถูก merge ทับบน `_echo(decl)` ซึ่งสะท้อน
argument ที่ส่งมากลับไป — จำเป็นเวลา template ต้องพูดค่าที่ลูกค้าเพิ่งบอก

### สามตัวที่เหลือใน `tools`

| key | สถานะจริง |
|---|---|
| `validation.date_format`, `validation.payment_channels` | ✅ ขึ้น prompt |
| `validation.result_codes` | อ่านโดย**ฝั่งเทรน** (`verl/envs/debt/backend.py`) — demo ไม่อ่าน |
| `notes` | ขึ้น prompt |
| `require_kyc` | ⚠️ อ่านโดยสาย **v12 เท่านั้น** — โมเดลที่เสิร์ฟอยู่ (non-v12) ไม่ใช้ |

---

## 5 · `constraints` — กฎ

```json
{"id": "max_pay_asks", "type": "max_occurrences", "counts": "pay_ask", "max": 2,
 "on_exceed": {"to": "close_refused"}, "enforce": ["prompt","reward"],
 "desc": "ถามจ่ายได้ไม่เกิน 2 ครั้ง …"}
```

`desc` คือหัวใจ — **มันคือกฎที่โมเดลอ่านจริง** `type` เป็นการจัดหมวดที่ตัวตรวจใช้

### `enforce` — 3 ค่า

| ค่า | เกิดอะไรขึ้นจริง |
|---|---|
| `prompt` | `desc` ไปอยู่ในหัวข้อ **หลักการ (⛔ กฎสูงสุด)** |
| `reward` | ป้ายสำหรับฝั่งเทรน **ไม่มีผลตอนรัน** |

`backend` ไม่ใช่ค่าที่ใช้ได้แล้ว — การบังคับตอนรันอยู่ที่ `gating` ของ tool ทั้งหมด
`session` ก็ถูกตัดออก ตั้งแต่ถอด reply-gate

> พูดตรงๆ: `constraints` **ทุกข้อ**คือข้อความใน prompt โมเดลทำตามหรือไม่ทำก็ได้
> ถ้ากฎไหนแตกไม่ได้ ต้องให้ **API ของคุณเอง**ปฏิเสธ อย่าหวังว่าแอปจะกันให้

### 7 `type`

| type | field ที่ใช้คู่กัน | ความหมาย |
|---|---|---|
| `max_occurrences` | `counts` `max` `on_exceed.to` | นับเกินโควตา → ไป state นั้น |
| `once_per_call` | `template_fine_states` | พูดได้ครั้งเดียวต่อสาย |
| `repeat_only_on` | `event` `template_fine_states` | พูดซ้ำได้เฉพาะเมื่อเกิด event นี้ |
| `forbid_after_event` | `event` `template_fine_states` `inverted` | ห้ามหลัง event (`inverted: true` = ห้าม**ก่อน**) |
| `no_repeat_answered_request` | `template_fine_states` | ตอบไปแล้วห้ามตอบซ้ำ |
| `immediate_transition_on` | `event` `to` | เจอ event ปุ๊บ ย้ายทันที |
| `max_templates_per_reply` | `max` | หนึ่งเทิร์นพูดได้กี่ประโยค (chain มาจาก `states` — ไม่มี `exceptions`) |

กฎที่ไม่มี `type` = กฎเชิงข้อความล้วน ใส่ `id` + `desc` + `enforce` พอ — เป็นรูปแบบ
ปกติ ไม่ใช่ของด้อยกว่า `desc` คือสิ่งเดียวที่ไปถึงโมเดล

**key ที่ใส่ใน constraint ได้** (`CONSTRAINT_KEYS`, พิมพ์ผิดถูกปฏิเสธ):
`id` `type` `desc` `enforce` · `event` `max` `counts` `to` `on_exceed`
`template_fine_states` `inverted` · `source_ref` `note` `spec_note` `inferred`
— `source_ref` เก็บที่มา ⚠️ ไม่มีใครอ่าน

**ไม่มี `template_fine_state` เอกพจน์แล้ว** ใช้ `template_fine_states` (list) ตัวเดียว
รูปเดียวรับได้ทั้งหนึ่งและหลาย

**ประกาศทางเดียว** — สามอย่างนี้เลิกใช้เพราะพูดซ้ำสิ่งที่ส่วนอื่นบอกอยู่แล้ว:
`tool_pair` → `gating.requires_prior` · `require_tool_before_end` →
`gating.required_at` · `resume_after_interrupt` → `faq_routing.routes[].then="resume"`
(กฎเดิมยังอยู่ในไฟล์ในรูปกฎเชิงข้อความ — `desc` ไม่ได้หายไปไหน)

> **รูปของกฎสำคัญกว่าจำนวนกฎ** เพิ่มกฎที่ถูกต้องทีละข้อ 6 ข้อพร้อมกัน เคยทำให้คะแนน**ลด**
> และเกิดการรับปากที่ลูกค้าไม่เคยพูด เพราะ 3 ข้อลงท้ายด้วย "แล้วปิดสาย" แล้วโมเดล
> generalise ตอนจบ ไม่ใช่เงื่อนไข
> รูปที่ย้ายข้ามบริษัทได้: **ประโยคที่ถูก + ทางเลือกที่ห้าม + เหตุผลเชิงความหมาย**
> รูปที่ย้ายไม่ได้: ขั้นตอน
> ⚠️ วัดจริง: เขียนกฎเพิ่มให้ชัดขึ้น 29/34/30 · ลบ clause ที่ผิดทิ้งเฉยๆ 36/34/35

---

## 6 · `faq_routing`

```json
{"intent": "amount", "desc": "ถามยอด",
 "templates": [{"fine_state": "faq_amount"}],
 "then": "resume"}
```
`then` = `"resume"` (ตอบแล้วกลับ flow) หรือ
`{"terminal": true, "outcome": {"result": "tcb", "reasons": […]}}` (ตอบแล้วจบสาย)

`outcome` ที่นี่ก็นับเข้าคลังคำผลลัพธ์ของ flow เหมือน `states[].outcome`

⚠️ FAQ beat จงใจไม่สังกัด state ใด — อย่าตัดสินว่า beat "เข้าไม่ถึง" จากกราฟ state
ให้ดูจาก catalog

---

## 7 · CRM

| key | ทำอะไร |
|---|---|
| `session_init.url` | ยิงครั้งเดียวตอนเปิดสาย ได้ dict ของข้อมูลลูกค้า |
| `session_init.on_failure` | API ไม่ตอบ → พูดประโยคนี้แล้วปิดสาย (ดูข้างล่าง) |

**ค้นผู้โทรด้วยตัวระบุ ไม่ใช่คืนแถวเดียวตายตัว** — `{msisdn}` (และ token อื่นใน CRM)
แทนค่าได้ทั้งใน `url`, `headers`, `body`

```jsonc
"url": "{API_BASE}/AEON/init?msisdn={msisdn}"
```

tool ทุกตัวก็ส่ง `ref: {case_id, msisdn, customer_phone, last_4_digits}` ไปกับ body
อัตโนมัติ — API แยกแถวได้โดยโมเดลไม่ต้องพิมพ์อะไร ⇒ เลือกผู้โทรคนไหนในหน้าเว็บ
ก็ได้ข้อมูลของคนนั้นจริง
| `crm_fields` | **whitelist** — เฉพาะ field ในนี้ที่โมเดลได้เห็น |
| `crm_labels` | ป้ายไทยในหัวข้อ `## ข้อมูลลูกค้า (CRM Snapshot)` |

ต้องมีทั้ง `crm_fields` และค่าจริง โมเดลถึงเห็น — ประกาศ label เฉยๆ ไม่พอ
`[slot]` ใน template เติมจาก dict ก้อนเดียวกันนี้

### `session_init.on_failure` — API ไม่ตอบ

```jsonc
"on_failure": {"fine_state": "apology_close",
               "outcome": {"result": "unreachable", "reason": "ระบบไม่ตอบ"}}
```

แอป**ไม่เปิดสายด้วยข้อมูลค้างในเครื่อง** — วัดแล้วว่าเมื่อ API ล่ม agent ยังพูดยอด
45,000/4,500 ซึ่งเป็นตัวเลขจากไฟล์ในเรโป ไม่ใช่ของลูกค้า

เมื่อ `session_init` ประกาศไว้แต่ไม่ตอบ: พูด `fine_state` ที่ระบุ · เรียก tool ปิดสาย
ด้วย `outcome` ที่ระบุ (ถ้ามี) · จบสาย

**spec ที่ประกาศ `session_init` แต่ไม่ประกาศ `on_failure` → เปิด session ไม่ได้เลย**
แอปไม่แต่งประโยคให้ และไม่ยอมพูดข้อมูลเก่า

---

## 8 · แอปกันอะไรให้ (ทั้งหมด — ไม่มีมากกว่านี้)

**ก่อนพูด** — `empty_slot` (slot ไม่มีค่า) · `too_many_beats` · `incomplete_chain`
(chain ไม่ครบ) · `missing_required_tools` (`entry_tools` ยังไม่ครบ)

**ตอนเรียก tool** — `unknown_tool` · `missing_required_args` · `date_format_invalid` ·
`value_not_offered` · `<tool>_already_recorded` · `already_checked` · `commitment_mismatch` ·
`call_already_closed` · `outcome_already_recorded` · `http_error`

**จบเทิร์นต้องมีคำพูดเสมอ** — ถ้าลูปเรียก tool หมดโควตาแล้วโมเดลยังไม่ `reply`
แอปจะพูด `fallback_fine_state` (ค่าเริ่มต้น `faq_repeat`) แทนที่จะเงียบใส่สาย
⇒ **ทุก spec ต้องมี beat นี้** ไม่งั้น lint ขึ้น ERROR `no_fallback_beat`

ทั้งหมดนี้งอกจากโครงสร้างที่ทุก spec มี **ไม่มีอะไรกันตาม "นโยบาย" ของบริษัทไหน**
ไม่มี KYC gate ไม่มีรายชื่อ field อ่อนไหว ไม่มีนิยาม "ยืนยันตัวตนแล้ว"

---

## 9 · เขียนเสร็จต้องรัน

```bash
PYTHONPATH=. python3 .claude/skills/new-company/scripts/lint_spec.py data/flows/<CODE>.company.json
```
**ERROR ต้องเป็น 0** · WARN อ่านทุกบรรทัด ส่วนใหญ่เป็นของจริง — `sentence_never_used`
เคยเจอ beat ที่กฎ prompt สามรอบทำให้โมเดลพูดไม่ได้ ด้วยเหตุผลง่ายๆ ว่าไม่มี state หรือ
FAQ route ไหนเปิดทางให้เลย

```bash
PYTHONPATH=. python3 .claude/skills/new-company/scripts/spec_to_mermaid.py data/flows/<CODE>.company.json
PYTHONPATH=. python3 tools/gen_mockoon.py
PYTHONPATH=. python3 .claude/skills/new-company/scripts/smoke_company.py <CODE> --case TC-<CODE>-BUILD-001 --scenarios s.json
```

⚠️ **ห้ามรายงานคะแนนจากการรันครั้งเดียว** — config เดียวกัน temp 0 เคยแกว่ง 6 จาก 45
รัน ≥3 ครั้ง แล้วรายงานความเสถียรรายเคส

---

## 10 · กรอกตามลำดับนี้

เขียน `states` ก่อน `catalog` เสมอ — flow เป็นตัวบอกว่าต้องมีประโยคอะไรบ้าง

```
1  events          รายการเหตุการณ์ที่ลูกค้าทำได้  (ตกลง / ปฏิเสธ / ขอเลื่อน / เงียบ …)
2  states          ผัง — เริ่มที่ไหน เจอ event ไหนไปไหน จบที่ไหน + outcome ของแต่ละทางจบ
3  tools           อะไรที่ "ระบบ" ต้องตอบ ไม่ใช่ agent เดา
4  catalog         ประโยคของทุก _fine_state ที่ผังอ้างถึง
5  faq_routing     คำถามแทรกที่ไม่อยู่ในผัง
6  crm_*           ข้อมูลลูกค้าที่ประโยคต้องใช้ + session_init
7  constraints     กฎ — น้อยที่สุดที่ใช้ได้ แล้วค่อยวัด
```

**อะไรควรเป็น tool ไม่ใช่กฎใน prompt:** การคำนวณวันที่ · ขีดจำกัด/เกณฑ์ · การเช็คสิทธิ์ ·
การค้นข้อมูล — วัดแล้วว่าโมเดลคำนวณวันที่ไม่แม่น ถ้า requirement บอกกฎ ("เลื่อนได้ไม่เกิน
7 วัน") โดยไม่มีกลไก นั่นคือ **API ที่ยังขาด** ไม่ใช่บรรทัดใน prompt

**วันที่ใน mock ต้องสัมพันธ์กับ "วันนี้"** — ถ้า API คืนวันตายตัวที่เป็นอดีต agent จะแปลง
"พุธหน้า" ออกมาเป็นค่าที่ไม่มีในตัวเลือก

---

## 11 · เช็คก่อนอัปโหลด

```
□ ชื่อไฟล์ = <CODE>.company.json  (ไม่ต้องใส่ company/flow_id/spec_version ในไฟล์)
□ text_id ไม่ซ้ำ
□ ทุก _fine_state ที่ state / faq_routing / auxiliary_templates อ้าง มีประโยคใน catalog
□ ทุก event ที่ transition และ constraint อ้าง มีใน events
□ ทุก state ที่ `to` ชี้ไป มีจริง
□ มี initial: true หนึ่ง state
□ มี tool ที่ required_at: end_of_call อย่างมากหนึ่งตัว (ไม่มีเลยก็ได้)
□ ทุก terminal/close state มี tool นั้นใน entry_tools  ← ไม่งั้นจบสายโดยไม่บันทึกอะไร
□ ทุก placeholder ในประโยค มาจาก crm_fields หรือ field ใน tool `returns`
□ กฎที่อ้าง text_id — เลขตรงกับ _fine_state ที่พูดถึงจริง  ← บั๊กที่แพงที่สุด
□ arg ที่ห้ามว่าง ใช้ required_when + one_of_from ไม่ใช่ optional เฉยๆ
```

อัปโหลดแล้วระบบตรวจอีกชั้น key ผิดจะบอกว่าผิดตรงไหนและอะไรมาแทน

**ไฟล์ตัวอย่าง** — `AMT.company.json` งานนัดหมาย flow สั้น 2 tool อ่านง่ายสุด ·
`SHOP.company.json` 11 key เล็กสุด มี `mock`/`returns` ครบ · `KBANK` ปานกลาง ·
`AEON` เต็มรูปแบบ · `_TEMPLATE.company.json` เริ่มจากศูนย์

---

## 12 · ที่มาของทุกตัวเลขในเอกสารนี้

`demo/server/flow/flowspec.py` — `TOP_KEYS` (22) · `_REQUIRED_TOP_KEYS` (5) ·
`STATE_KEYS` (13) · `TEMPLATE_KEYS` (7) · `TRANSITION_KEYS` (6) · `CATALOG_KEYS` (16) ·
`CONSTRAINT_TYPES` (7) · `CONSTRAINT_KEYS` (14) · `GATING_KEYS` (10) · `KNOWN_IMPLS` (2) · `RETIRED` (4)
`spec_gate.py` (gating ที่บังคับจริง) · `spec_backend.py` (args, `one_of_from`) ·
`flowspec_render.py` (อะไรเข้า prompt) · `sessions.py` (reply gate) ·
`gen_mockoon.py` (`mock`, `returns`)
