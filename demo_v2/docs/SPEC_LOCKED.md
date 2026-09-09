# `<CODE>.company.json` — locked format

**สถานะ: LOCKED** — เอกสารนี้กำหนด schema ที่ใช้จริง key ที่ไม่อยู่ใน schema ถูก **ปฏิเสธ**
(`validate_strict` ใน `demo_v2/server/flow/flowspec.py`) การเพิ่มหรือลบ key จึงถือเป็นการแก้
format ไม่ใช่การแก้ spec

หนึ่งบริษัท = หนึ่งไฟล์
วางไฟล์ = สร้างบริษัท
ลบไฟล์ = ลบบริษัท

แอปไม่มี business logic เฉพาะบริษัท ทุกสิ่งที่ agent ทำมาจากไฟล์นี้

ชื่อไฟล์เป็น identity:

* `AEON.company.json` → `company: "AEON"`
* `flow_id` จะถูกเติมเป็น `AEON` (ชื่อไฟล์) ด้วย `setdefault` **เฉพาะเมื่อไฟล์ไม่ได้กำหนดเอง**
* ถ้าไฟล์กำหนดค่าเอง ค่านั้นมีผลเหนือค่า default (`AEON` ที่ ship มาใช้ `AEON-outbound-remind`)
* ไฟล์ใหม่จึงไม่จำเป็นต้องใส่ `company` หรือ `flow_id`
* ไฟล์ที่ขึ้นต้นด้วย `_` ถือเป็น template และไม่ถูกขึ้นทะเบียน

---

## หลักการออกแบบ

> **แพลตฟอร์มให้ "กลไก" · tenant ให้ "นโยบาย"**

**กลไก** คือสิ่งที่ทุก tenant ใช้เหมือนกันและอนุมานได้จากโครงสร้างของ spec เช่น
`text_id`, slot filling, tool call, กติกา template ใน state, `entry_tools` และ argument contract

**นโยบาย** คือกฎที่มีเฉพาะบางบริษัท แม้จะ parameterise ผ่าน spec ได้ก็ยังเป็นกฎของ tenant
จึงควรอยู่ใน `constraints` หรือถูกบังคับโดย API ของ tenant

หลักสำคัญคือ:

* สิ่งที่แอปบังคับได้เหมือนกันทุก tenant → ทำเป็นกลไก
* สิ่งที่เป็น policy เฉพาะ tenant → ทำเป็น `constraints` หรือ API
* อย่าเพิ่ม gate เฉพาะบริษัทลงในแอปเพียงเพราะสามารถ parameterise จาก spec ได้

---

# 1 · ภาพรวม 19 key

จำเป็น 5 key ที่เหลือ optional

| key                                | ชนิด                                                      | ใช้ตอนไหน                                                      | ถ้าไม่ใส่                                       |
| ---------------------------------- | --------------------------------------------------------- | -------------------------------------------------------------- | ----------------------------------------------- |
| **`events`** ✱                     | `{ชื่อ: {desc, cues}}`                                    | event ที่ `states[].on[].event` และ `constraints[].event` อ้าง | validate ไม่ผ่าน                                |
| **`tools`** ✱                      | `{declarations, …}`                                       | สร้าง tool schema และเรียก API                                 | validate ไม่ผ่าน                                |
| **`states`** ✱                     | `[…]`                                                     | flow ทั้งหมด                                                   | validate ไม่ผ่าน                                |
| **`faq_routing`** ✱                | `{routes: […]}`                                           | คำถามแทรกระหว่าง flow                                          | validate ไม่ผ่าน; ใช้ `{"routes":[]}` ได้       |
| **`constraints`** ✱                | `[…]`                                                     | กฎที่ส่งเข้า prompt                                            | validate ไม่ผ่าน; ใช้ `[]` ได้                  |
| `catalog`                          | `[…]`                                                     | คลังประโยค `text_id ↔ template`                                | agent ไม่มีประโยคให้พูด                         |
| `display_name`                     | `str`                                                     | ชื่อใน UI และ `[company]`                                      | ใช้ `company`                                   |
| `agent_role`                       | `str`                                                     | บรรทัดแรกของ prompt                                            | `"เจ้าหน้าที่ติดตามทวงถามหนี้ของบริษัท <CODE>"` |
| `goal`                             | `str`                                                     | เป้าหมายของสาย                                                 | ข้าม                                            |
| `crm_fields`                       | `[str]`                                                   | whitelist ข้อมูล CRM ที่โมเดลเห็น                              | โมเดลไม่เห็นข้อมูลลูกค้า                        |
| `crm_labels`                       | `{field: ป้ายไทย}`                                        | label ใน CRM Snapshot                                          | ใช้ชื่อ field ดิบ                               |
| `session_init`                     | `{url, method, headers, body, timeout, note, on_failure}` | ดึงข้อมูลลูกค้าตอนเปิดสาย                                      | ไม่มี CRM; slot จะเหลือ `[placeholder]`         |
| `fallback_fine_state`              | `str`                                                     | beat ที่พูดเมื่อโมเดลตอบว่าง                                    | ใช้ `faq_repeat`                                |
| `company` `flow_id` `spec_version` | `str`                                                     | compatibility กับไฟล์เก่า                                      | เติมจากชื่อไฟล์ได้                              |
| `auxiliary_templates`              | `{allowed: […]}`                                          | legacy format                                                  | **อย่าใช้ในไฟล์ใหม่**                           |

### key ที่ถูกถอดออก

`validate_strict` ปฏิเสธ key ต่อไปนี้:

| key              | เหตุผล                                                                                                                      |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `role`           | เป็นข้อความอิสระที่ไม่มี runtime mechanism รองรับ ถ้อยคำจริงมาจาก `catalog.template` จึงควบคุมที่ catalog                   |
| `legal_note`     | ไม่มีกลไกตรวจว่าข้อความอ้างกฎหมายถูกต้อง และไม่เคยมี spec ที่ใช้ key นี้จริง กฎที่บังคับได้ควรอยู่ใน `constraints` หรือ API |
| `catalog_inline` | คลังประโยคอยู่ใน `catalog` ของไฟล์เดียวกันเสมอ — รูปที่แยกคลังไปไว้อีกคีย์ (หรืออีกไฟล์) ทำให้ "หนึ่งบริษัทหนึ่งไฟล์" ไม่จริง |
| `outcomes`       | ผลของสายมาจาก `states[].outcome` และ `faq_routing` เท่านั้น บล็อกนี้เป็นสำเนาที่คลาดจากของจริงได้                          |

---

# 2 · `states` — flow

```json
{
  "id": "disclose_ask",
  "phase": "main",
  "initial": false,
  "terminal": false,
  "templates": [
    {"fine_state": "disclose_balance"},
    {"fine_state": "ask_pay_today"}
  ],
  "entry_tools": ["check_account_status"],
  "on": [
    {"event": "agrees", "to": "close_ptp", "tools": ["record_verbal_commitment"]}
  ],
  "outcome": {
    "args": {"result": "ptp"},
    "reasons": ["ptp"],
    "desc": "รับปากจ่าย"
  },
  "max_visits": 2,
  "verify_required": true,
  "note": "…",
  "spec_note": "…",
  "inferred": true,
  "counts_as": false
}
```

| key                    | ค่า                          | ความหมาย                                                             |
| ---------------------- | ---------------------------- | -------------------------------------------------------------------- |
| `id` ✱                 | `str`                        | ชื่อ state ที่ `on[].to` และ `constraints[].to` อ้าง                 |
| `templates` ✱          | `[…]`                        | beat ของ state; หลายตัวที่ไม่มี `when_event` ต้องพูดครบในเทิร์นเดียว |
| `phase`                | `opening` \| `main` \| `close` | ใช้จัดกลุ่มใน prompt (ดูข้างล่าง)                                    |
| `initial`              | `true`                       | state แรก; มีได้ตัวเดียว                                             |
| `terminal`             | `true`                       | เข้าแล้วจบสาย                                                        |
| `entry_tools`          | `[ชื่อ tool]`                | เรียกก่อนพูดทุกครั้งที่เข้า state                                    |
| `on`                   | `[{event,to,tools?}]`        | event → state ปลายทาง และอาจเรียก tool                               |
| `outcome`              | ดู §2.2                      | ผลลัพธ์เมื่อสายจบที่ state นี้                                       |
| `max_visits`           | `int`                        | จำนวนครั้งสูงสุดที่เข้า state                                        |
| `verify_required`      | `true`                       | ต้องผ่าน verification ก่อนพูด beat ใน state                          |
| `counts_as`            | `false`                      | ไม่เพิ่มตัวนับ `pay_ask`                                             |
| `note`                 | `str`                        | เข้า prompt                                                          |
| `spec_note` `inferred` | `str` `bool`                 | metadata สำหรับคนเขียน ไม่เข้า prompt                                |

### `phase` ต้องใช้ค่าที่กำหนด

มีเพียง:

```text
opening
main
close
```

ค่าอื่นอาจผ่าน validation บางด่าน แต่ `render_instruction()` จะแสดงเฉพาะสามค่านี้
ทำให้ state นั้นหายจาก prompt โดยไม่มี warning

ดังนั้น **อย่าใช้ค่าที่ไม่ได้ระบุใน schema แม้ validator จะรับได้**

---

## 2.1 · `templates[]`

มี 7 key:

| key          | ความหมาย                                               |
| ------------ | ------------------------------------------------------ |
| `fine_state` | beat หนึ่งตัว                                          |
| `any_of`     | เลือกพูดหนึ่งตัวจากรายการ                              |
| `when_event` | ใช้ template เมื่อเกิด event นี้; กลุ่มนี้เป็นทางเลือก |
| `optional`   | ไม่พูดก็ได้                                            |
| `counts_as`  | ไม่เพิ่มตัวนับ                                         |
| `note`       | metadata                                               |
| `inferred`   | metadata                                               |

### กติกา chain / choice

> หลาย template ใน state เดียว **ไม่มี `when_event` = chain** ต้องพูดครบในเทิร์นเดียว
> ถ้ามี `when_event` = เป็นทางเลือก

key เก่าต่อไปนี้ถูกปฏิเสธ:

```text
compose
group
template_mode
render_all_templates
```

ใช้ `templates`, `any_of`, `when_event` และ `optional` แทน

---

## 2.2 · `outcome`

```json
{
  "args": {"result": "ptp"},
  "reasons": ["ptp", "minimum"],
  "desc": "…"
}
```

`args` ต้องใช้ **ชื่อ argument จริงของ closing tool**

เช่น tool ประกาศ:

```text
save_appointment(status, new_slot)
```

ให้เขียน:

```json
{"args": {"status": "rescheduled"}}
```

ค่า argument ที่มาจากบทสนทนา เช่น `new_slot` ไม่ต้องใส่ที่นี่ ให้ใช้ `desc` หรือ
`required_when` ของ argument เป็นตัวบอกโมเดล

| key                                             | ความหมาย                             |
| ----------------------------------------------- | ------------------------------------ |
| `args`                                          | arguments ที่ closing tool จะได้รับ  |
| `reasons`                                       | รหัสเหตุผลที่ใช้เป็นคำแนะนำใน prompt |
| `desc`                                          | คำอธิบายผลลัพธ์ให้โมเดล              |
| `note` `spec_note` `inferred` `reason_by_event` | metadata ไม่ถูกอ่านโดย runtime       |

argument ตัวแรกของ closing tool ถือเป็น **ผลสาย** และถูกใช้โดย `derive_outcomes`
และ closing gate

### legacy outcome

รูปเดิม:

```json
{"result": "ptp"}
```

ยังโหลดได้ โดยแปลงเป็น:

```json
{"args": {"<first_tool_arg>": "ptp"}}
```

พร้อมสร้าง `reason` เดี่ยวเมื่อมีค่า

ไม่มี `outcomes` block แยกแล้ว ผลลัพธ์ทั้งหมดของ flow มาจาก:

* `states[].outcome`
* `faq_routing.routes[].then.outcome`

ดังนั้น flow ที่ไม่มี outcome ก็ถือว่าถูกต้อง

### validation

ตอนอัปโหลดตรวจ:

1. arg ต้องประกาศอยู่ใน tool
2. ค่า enum ต้องอยู่ใน enum ของ arg
3. ผลต้องอยู่ใน outcome catalog ของ flow
4. ตรวจครอบคลุม `states[]`, `faq_routing` และ `session_init.on_failure`

---

# 3 · `catalog` — คลังประโยค

```json
{
  "text_id": 1018,
  "_fine_state": "disclose_balance",
  "template": "ยอดค้างชำระ [amount] บาท ครบกำหนด [due_date] ค่ะ",
  "hint": "ใช้ตอนแจ้งยอดครั้งแรกหลังยืนยันตัวตน",
  "state": "disclose_ask",
  "intent_name": "…"
}
```

| key                                                            | จำเป็น | ความหมาย                                 |
| -------------------------------------------------------------- | ------ | ---------------------------------------- |
| `text_id` ✱                                                    | ✅      | ID ที่โมเดลใช้เรียก เช่น `reply([1018])` |
| `_fine_state` ✱                                                | ✅      | beat ที่ประโยคสังกัด                     |
| `template` ✱                                                   | ✅      | ข้อความจริงพร้อม placeholder             |
| `hint`                                                         |        | บอกโมเดลว่าใช้เมื่อไร                    |
| `state`                                                        |        | จัดกลุ่มใน prompt                        |
| `intent_name`                                                  |        | label ของประโยค                          |
| `is_closer` `is_demand` `is_acknowledgment` `expects_response` |        | behavioral labels                        |
| `company` `note` `desc` `_hint_where` `_example_AEON`          |        | รับไว้                                   |
| key อื่นที่ขึ้นต้น `_`                                         |        | ผ่านได้ แต่ไม่มี runtime อ่าน            |

`_fine_state` มี `_` นำหน้าโดยตั้งใจ:

* catalog → `_fine_state`
* states / templates → `fine_state`

ใส่ผิดตำแหน่งจะถูกปฏิเสธ

---

## 3.1 · Placeholder

`fill_template()` resolve ตามลำดับ:

| ลำดับ | รูป                                 | ความหมาย                     |
| ----- | ----------------------------------- | ---------------------------- |
| 1     | `{suffix}` `{q_suffix}` `{pronoun}` | เพศเสียง                     |
| 2     | `{{if field}}…{{else}}…{{/if}}`     | conditional                  |
| 3     | `[field]`                           | data field                   |
| 4     | `{field}`                           | data field เช่นเดียวกับข้อ 3 |
| 5     | —                                   | ยุบคำนำหน้าซ้ำ               |

ตัวอย่าง:

```text
F → สวัสดีค่ะ ไหมคะ ดิฉันขอเรียน
M → สวัสดีครับ ไหมครับ ผมขอเรียน
```

จาก:

```text
สวัสดี{suffix} ไหม{q_suffix} {pronoun}ขอเรียน
```

### `[field]` และ `{field}`

สองรูปแบบให้ผลเหมือนกัน

ไฟล์เดิมใช้ทั้งสองแบบ แต่ไฟล์ใหม่ควร **เลือกสไตล์เดียวต่อไฟล์**
เพื่อให้อ่านง่ายและค้นหา slot ได้ง่าย

เวลาค้นทั้งสองรูปแบบใช้:

```regex
[\[\{]([a-z_0-9]+)[\]\}]
```

### conditional

`{{if field}}` ตรวจ boolean ด้วยค่าความจริง

* `False` → false branch
* ชนิดอื่น → ใช้การมีอยู่ของ field

ทำให้ค่า `0` หรือ `""` ยังคงใช้งานได้ตามชนิดข้อมูล

---

## 3.2 · แหล่ง placeholder

| แหล่ง                         |    จำนวน | พฤติกรรม                                                                      |
| ----------------------------- | -------: | ----------------------------------------------------------------------------- |
| `SYSTEM_PLACEHOLDERS`         |       21 | alias เช่น `[amount]` → `total_amount_due`; ถ้า map ไม่พบจะลองชื่อ field ตรงๆ |
| `DYNAMIC_PLACEHOLDERS`        |        9 | มาจาก `dynamic_vars`; ถ้าไม่มีจะใช้คำไทยกลาง                                  |
| `crm_fields` / `tool.returns` | ตาม spec | field ตรงจากข้อมูล CRM / tool                                                 |

วันที่และเวลาที่มาจากโมเดลถูกตรวจเมื่อ `strict_dates=True`

* `DATE_PLACEHOLDERS`: `callback_date`, `promised_date`
* `TIME_PLACEHOLDERS`: `callback_time`

ค่าต้องเป็น canonical format ก่อน render ไม่เช่นนั้นจะได้ `DateFormatError`
และผู้เรียกแปลงเป็น:

```json
{"sent": false, "reason": "date_format_invalid"}
```

---

## 3.3 · เพิ่มสำนวนหรือเพิ่ม beat?

ถ้าสายเดินเหมือนเดิม → เพิ่ม `text_id` ใต้ `_fine_state` เดิม

ถ้าสายเดินต่างกัน → สร้าง `_fine_state` ใหม่และเพิ่มลงผัง

หลายสำนวนที่ต่างกันแค่การเรียบเรียงคำมักไม่ทำให้ model เลือกต่างกัน
เพราะ temperature 0 และ prompt เหมือนเดิม สิ่งที่ช่วยให้เลือกต่างกันคือ:

* ข้อมูลที่สื่อ
* `hint`
* เงื่อนไขการใช้งาน

### ข้อผิดพลาดสำคัญ

**ห้ามเขียนชื่อ beat ผิดข้าง `text_id`**

นี่เป็นสาเหตุสำคัญของปัญหา model เลือกประโยคผิด โดยเฉพาะประโยคปิดสาย
ระบบตรวจความสอดคล้องนี้ให้ตอนอัปโหลด

---

# 4 · `tools`

```json
"tools": {
  "declarations": [{
    "name": "check_account_status",
    "desc": "อ่านข้อมูลบัญชี",
    "impl": "http",
    "url": "{API_BASE}/AEON/check_account_status",
    "method": "POST",
    "args": {
      "last_4_digits": {
        "type": "string",
        "optional": true
      }
    },
    "returns": {
      "amount": {
        "type": "number",
        "desc": "ยอดค้าง"
      }
    },
    "gating": {
      "max_successful_calls": 1,
      "required_at": "end_of_call"
    },
    "mock": {
      "rules": [{
        "when": {
          "arg": "date",
          "matches": "2026-0[89]"
        },
        "body": {"in_range": true},
        "label": "อยู่ในเกณฑ์"
      }],
      "default": {"in_range": false}
    }
  }],
  "validation": {
    "date_format": "YYYY-MM-DD (Weekday)",
    "payment_channels": ["…"]
  },
  "notes": ["…"]
}
```

## 4.1 · `declarations[]`

| key             | ค่า                                      | ความหมาย                                     |
| --------------- | ---------------------------------------- | -------------------------------------------- |
| `name` ✱        | `str`                                    | ชื่อ tool                                    |
| `desc` ✱        | `str`                                    | คำอธิบายใน schema                            |
| `impl` ✱        | `http` \| `generic`                     | `http` ยิง API; `generic` ตอบจาก declaration |
| `url`           | `str`                                    | ใช้กับ `http`; tenant ต้องใส่ URL ของตัวเอง  |
| `method`        | `POST` \| `GET`                         | default = `POST`                             |
| `args`          | `{ชื่อ: สัญญา}`                          | arguments                                    |
| `returns`       | `{field: {type, desc}}`                  | response schema                              |
| `gating`        | `{…}`                                    | runtime gate                                 |
| `provides`      | `"verified"`                             | ประกาศว่า tool นี้ปลดล็อก verification       |
| `verified_when` | `{field, equals}` \| `{any_success:true}` | เงื่อนไขที่ถือว่ายืนยันสำเร็จ                |
| `mock`          | `{rules, default}`                       | คำตอบสำรองสำหรับ API ปลอมตอนทดสอบ            |

`{API_BASE}` ที่ใช้ในตัวอย่างเป็น env ของ server (`AAX6_API_BASE`)
ไม่ใช่ URL ที่ tenant ต้องใช้

---

## 4.2 · Argument contract

| key             | ค่า                                  | runtime                              |
| --------------- | ------------------------------------ | ------------------------------------ |
| `type`          | `string` `number` `boolean` `array`  | เข้า schema                          |
| `optional`      | `true`                               | ไม่ใส่ = required                    |
| `enum`          | `[str]`                              | จำกัดค่า                             |
| `format`        | `"YYYY-MM-DD (Weekday)"` / `"HH:MM"` | ผิดรูป → `date_format_invalid`       |
| `desc`          | `str`                                | คำอธิบาย                             |
| `required_when` | `{arg, equals}`                      | required เมื่อ arg อื่นมีค่าที่กำหนด |
| `one_of_from`   | `{tool, field}`                      | ต้องเป็นค่าที่ tool นั้นเคยคืน       |

`required_when` + `one_of_from` ใช้ป้องกันกรณี `optional: true` แล้วส่ง `""`
เพื่อหลบ validation

---

## 4.3 · `gating`

มี 10 key:

| key                          | runtime                                        |
| ---------------------------- | ---------------------------------------------- |
| `max_successful_calls`       | จำกัดจำนวน successful calls                    |
| `max_calls_per_conversation` | จำกัดจำนวน calls ต่อสาย รวมครั้งที่ถูกปฏิเสธ   |
| `requires_prior`             | ต้องเรียก tool ที่ระบุก่อน                     |
| `must_precede`               | tool นี้ต้องมาก่อน tool ที่ระบุ                |
| `args_must_match`            | arguments ต้องตรงกับ call ของ `requires_prior` |
| `required_at`                | `"end_of_call"` = closing tool                 |
| `after_event`                | prompt เท่านั้น                                |
| `note`                       | prompt เท่านั้น                                |
| `required_before_state`      | prompt เท่านั้น                                |
| `required_before`            | prompt เท่านั้น                                |

`args_must_match` ต้องใช้คู่กับ `requires_prior`

closing tool (`required_at: "end_of_call"`) ประกาศได้ **หนึ่งตัวต่อ spec**

การกำหนดลำดับใช้:

* `requires_prior` เมื่อ tool ต้องตามหลัง tool ใด tool หนึ่ง
* `must_precede` เมื่อ tool เดียวต้องมาก่อนหลายตัว

`constraints.type: tool_pair` เลิกใช้แล้ว

key อื่นนอก `GATING_KEYS` ถูกปฏิเสธ

---

## 4.4 · `mock`

`when` รองรับสองรูป:

```jsonc
"when": {
  "arg": "date",
  "matches": "^2026-09"
}
```

หรือ:

```jsonc
"when": {
  "arg": "date",
  "within_days_of": {
    "field": "due_date",
    "days_field": "max_extend_days"
  }
}
```

แบบ `within_days_of` ให้ generator คำนวณวันจริงจาก CRM
จึงเหมาะกับ requirement เช่น “ไม่เกิน N วัน”

**อย่าใช้ regex เดาวัน** เพราะ regex แบบตรึงเดือน/ปีทำให้รับวันที่นอกเกณฑ์ได้

Mockoon ไม่มี comparison helper ที่ใช้งานได้ตามต้องการ (`lte` / `gt`)
จึงฝัง response ที่ generator คำนวณไว้

การตรวจจริงควรอยู่ที่ `one_of_from` ซึ่งเทียบกับ `valid_dates` สด

---

## 4.5 · วันที่ที่โมเดลพูด

Argument ที่ประกาศ:

```text
format: "YYYY-MM-DD"
```

รับคำพูดธรรมชาติได้ เช่น:

```text
พรุ่งนี้
มะรืน
สิ้นเดือน
สิ้นเดือนหน้า
เสาร์หน้า
อีก 3 วัน
วันที่ 15 เดือนหน้า
20 มิถุนายน
```

แอปจะแปลงเป็น ISO ก่อนส่ง API

* ISO เดิม → ไม่แก้
* แปลไม่ได้ → ส่งต่อให้ format validation ตัดสิน
* API จะเห็น ISO เสมอ

เหตุผลคือ model 9B แปลงคำพูดเป็นวันที่ได้ไม่แม่น โดยเฉพาะกรณีข้ามเดือน
การแปลงในแอปทำให้ความแม่นเพิ่มจากประมาณ 62% เป็น 92% ในการทดสอบเดิม

---

## 4.6 · วันที่ต้องสัมพันธ์กับวันนี้

อย่า hard-code วันที่ใน persona

ใช้ offset เช่น:

```json
"appointment_date_offset_days": 2,
"due_date_offset_days": -7
```

แอปคำนวณให้ตอนเปิด session

`due_offset_days` เป็นชื่อ legacy และยังชี้ไป `due_date`

---

## 4.7 · key อื่นใน `tools`

| key                           | สถานะ                           |
| ----------------------------- | ------------------------------- |
| `validation.date_format`      | เข้า prompt                     |
| `validation.payment_channels` | เข้า prompt                     |
| `notes`                       | เข้า prompt                     |

---

# 5 · `constraints` — กฎ

ตัวอย่าง:

```json
{
  "id": "max_pay_asks",
  "type": "max_occurrences",
  "counts": "pay_ask",
  "max": 2,
  "on_exceed": {
    "to": "close_refused"
  },
  "enforce": ["prompt"],
  "desc": "ถามจ่ายได้ไม่เกิน 2 ครั้ง …"
}
```

`desc` คือส่วนที่โมเดลอ่านจริง
`type` มีไว้จัดหมวดและช่วย validation

## 5.1 · `enforce`

มีค่าเดียว:

| ค่า      | ผล                                           |
| -------- | -------------------------------------------- |
| `prompt` | `desc` เข้า section **หลักการ (⛔ กฎสูงสุด)** |

constraint ที่ไม่มี `prompt` ใน `enforce` จะไม่ถูกใส่ใน prompt เลย

`backend` และ `reward` ถูกถอดออกและ `validate_strict` ปฏิเสธ
(`reward` เคยเป็นป้ายสำหรับฝั่งเทรน ไม่มีผลตอนรัน)

การบังคับ runtime ต้องใช้ `tools.gating`

`session` ก็ถูกถอดออกพร้อม reply-gate

ส่วน verification ใช้:

```text
state/route.verify_required
+
tool.provides / tool.verified_when
```

ไม่ใช้ `constraints`

> **constraints ทุกข้อเป็นคำสั่งใน prompt** ไม่ใช่ runtime enforcement
> กฎที่ห้ามแตกต้องบังคับที่ API ของ tenant

---

## 5.2 · Constraint types

มี 7 type:

| type                         | fields                                      | ความหมาย                                   |
| ---------------------------- | ------------------------------------------- | ------------------------------------------ |
| `max_occurrences`            | `counts`, `max`, `on_exceed.to`             | เกินจำนวน → state ปลายทาง                  |
| `once_per_call`              | `template_fine_states`                      | พูดได้ครั้งเดียวต่อสาย                     |
| `repeat_only_on`             | `event`, `template_fine_states`             | พูดซ้ำได้เฉพาะเมื่อเกิด event              |
| `forbid_after_event`         | `event`, `template_fine_states`, `inverted` | ห้ามหลัง event; `inverted:true` = ห้ามก่อน |
| `no_repeat_answered_request` | `template_fine_states`                      | ตอบแล้วห้ามตอบซ้ำ                          |
| `immediate_transition_on`    | `event`, `to`                               | เจอ event → ย้ายทันที                      |
| `max_templates_per_reply`    | `max`                                       | จำกัดจำนวนประโยคต่อเทิร์น                  |

Constraint ที่ไม่มี `type` เป็น text-only rule ใช้เพียง:

```text
id
desc
enforce
```

รูปนี้เป็นรูปปกติ ไม่ใช่ fallback

### `CONSTRAINT_KEYS`

มีเฉพาะ:

```text
id
type
desc
enforce
event
max
counts
to
on_exceed
template_fine_states
inverted
note
spec_note
inferred
```

ใช้ `template_fine_states` ซึ่งเป็น list เสมอ
ไม่มี `template_fine_state` เอกพจน์แล้ว

### รูปที่เลิกใช้

| เดิม                      | ใช้แทน                                 |
| ------------------------- | -------------------------------------- |
| `tool_pair`               | `gating.requires_prior`                |
| `require_tool_before_end` | `gating.required_at`                   |
| `resume_after_interrupt`  | `faq_routing.routes[].then = "resume"` |

กฎเดิมยังเก็บเป็น `desc` ได้หากต้องการบอก policy ให้โมเดล

### หลักการเขียน constraint

อย่าเพิ่มกฎจำนวนมากพร้อมกันโดยไม่วัดผล

กฎที่ย้ายข้ามบริษัทได้ดีมักเป็น:

* ประโยคที่ถูก
* ทางเลือกที่ห้าม
* เหตุผลเชิงความหมาย

ส่วน “ขั้นตอน” มักไม่ควรย้ายเป็นกฎทั่วไป

---

# 6 · `faq_routing`

```json
{
  "intent": "amount",
  "desc": "ถามยอด",
  "templates": [
    {"fine_state": "faq_amount"}
  ],
  "verify_required": true,
  "then": "resume"
}
```

`verify_required` ทำงานเหมือนใน state:
ถ้า FAQ เปิดเผยข้อมูลลูกค้า ต้องผ่าน verification ก่อน

`then` มีสองรูป:

```json
"then": "resume"
```

ตอบแล้วกลับ flow

หรือ:

```json
"then": {
  "terminal": true,
  "outcome": {
    "args": {"result": "tcb"},
    "reasons": ["…"]
  }
}
```

ตอบแล้วจบสาย

FAQ outcome นับรวมใน outcome catalog ของ flow เช่นเดียวกับ state outcome

> FAQ beat ไม่จำเป็นต้องสังกัด state
> อย่าตัดสินว่า beat ใช้ไม่ได้จาก state graph ให้ดู catalog และ routing

---

# 7 · CRM

## 7.1 · `session_init`

`session_init.url` ถูกเรียกครั้งเดียวตอนเปิดสายและควรคืน dict ของข้อมูลลูกค้า

ตัวระบุผู้โทร เช่น `{msisdn}` สามารถใช้ใน:

* `url`
* `headers`
* `body`

ตัวอย่างสำหรับ spec ที่ ship:

```json
"url": "{API_BASE}/AEON/init?msisdn={msisdn}"
```

ตัวอย่างสำหรับ tenant:

```json
"url": "https://api.yourcompany.co.th/aax/init?msisdn={msisdn}"
```

tool ทุกตัวจะได้รับ:

```json
"ref": {
  "case_id": "...",
  "msisdn": "...",
  "customer_phone": "...",
  "last_4_digits": "..."
}
```

โดยอัตโนมัติ

agent จึงไม่ต้องส่งตัวระบุเองเพื่อเลือก record

---

## 7.2 · CRM visibility

`crm_fields` เป็น **whitelist** ของ field ที่โมเดลเห็น

`crm_labels` เป็น label ภาษาไทยใน:

```text
## ข้อมูลลูกค้า (CRM Snapshot)
```

ต้องมีทั้ง:

1. field อยู่ใน `crm_fields`
2. API คืนค่าจริง

จึงจะเห็นใน prompt

template slot ใช้ข้อมูลจาก dict เดียวกัน

---

## 7.3 · `session_init.on_failure`

```json
{
  "on_failure": {
    "fine_state": "apology_close",
    "outcome": {
      "args": {
        "result": "unreachable",
        "reason": "ระบบไม่ตอบ"
      }
    }
  }
}
```

ถ้า CRM ไม่ตอบ:

1. พูด `fine_state`
2. เรียก closing tool ด้วย outcome ถ้ามี
3. จบสาย

แอป **ไม่ใช้ข้อมูลค้างในเครื่อง** เป็น fallback

หากประกาศ `session_init` แต่ไม่ประกาศ `on_failure` → เปิด session ไม่ได้
เพราะแอปจะไม่แต่งประโยคและไม่ใช้ข้อมูลเก่า

---

# 8 · `verify_required`

Verification ต้องประกาศ **สองฝั่ง**

### ฝั่งที่หนึ่ง — state / FAQ ระบุว่าต้อง verify

```json
{
  "id": "disclose_ask",
  "verify_required": true
}
```

### ฝั่งที่สอง — tool ระบุวิธีปลดล็อก

แบบอ่าน flag จาก API:

```json
{
  "name": "verify_identity",
  "provides": "verified",
  "verified_when": {
    "field": "verified",
    "equals": true
  }
}
```

หรือ successful call ถือเป็น verification:

```json
{
  "name": "check_account_status",
  "provides": "verified",
  "verified_when": {
    "any_success": true
  }
}
```

`verified_when` มีเพียงสองรูป:

```text
{field, equals}
{any_success: true}
```

แบบ `field` รองรับทั้ง payload ชั้นบนและ `data`

### ต้องมีทั้งสองครึ่ง

ถ้ามี `verify_required` แต่ไม่มี tool ที่ `provides: verified`
จะไม่มี gate จริง

พฤติกรรมนี้ตั้งใจไว้ เพราะ spec ที่ขอ verification โดยไม่มี tool ปลดล็อก
ถ้าอ่านตรงตัวจะบล็อกการเปิดเผยข้อมูลทั้งสาย

---

# 9 · ลำดับการเขียน

เขียน `states` ก่อน `catalog` เสมอ เพราะ flow เป็นตัวกำหนดว่าต้องมี beat ใด

```text
1. events
2. states
3. tools
4. catalog
5. faq_routing
6. crm_* + session_init
7. constraints
```

แนวคิด:

* `events` — ลูกค้าทำอะไรได้บ้าง
* `states` — flow ไปทางไหน
* `tools` — ระบบต้องคำนวณ / ตรวจ / บันทึกอะไร
* `catalog` — agent พูดอะไร
* `faq_routing` — คำถามแทรก
* `crm_*` — ข้อมูลที่ agent ใช้
* `constraints` — policy ที่จำเป็นจริง

### อะไรควรเป็น tool?

สิ่งต่อไปนี้ควรอยู่ใน API/tool ไม่ใช่ prompt:

* การคำนวณวันที่
* limit / eligibility
* การตรวจสิทธิ์
* การค้นข้อมูล

หาก requirement บอกว่า “เลื่อนได้ไม่เกิน 7 วัน”
แต่ยังไม่มี API สำหรับตรวจเงื่อนไขนั้น ให้ถือว่า **ระบบยังขาด tool**
ไม่ใช่เพิ่มบรรทัดใน `constraints`

Mock ที่เกี่ยวกับวันที่ก็ควรสัมพันธ์กับ “วันนี้”

---
