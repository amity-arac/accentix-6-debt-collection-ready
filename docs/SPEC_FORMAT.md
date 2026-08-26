# FlowSpec + catalog — รูปแบบที่ล็อกแล้ว

หนึ่งบริษัท = **1 ไฟล์** และ **1 บรรทัดใน registry**

```
data/flows/<FLOW_ID>.json       spec + catalog_inline — ผัง, tool, กฎ, บทพูด อยู่ด้วยกัน
data/flows/flow_registry.json   {"NEWCO": {"spec": "NEWCO-outbound-remind.json", "display_name": "..."}}
```

บริษัทที่สร้างใหม่ทุกตัวเป็นแบบนี้ — `catalog: "__inline__"` + `catalog_inline: [...]`

**แบบแยก 2 ไฟล์ยังอ่านได้** (AEON/KBANK/SKL/AMT ที่มากับระบบยังเป็นแบบนี้ เพราะ catalog ใช้ร่วมกับข้อมูลอื่น):

```
data/flows/<FLOW_ID>.json           spec
data/pre-scripts/<name>.json        catalog   ← registry ชี้ด้วย key "catalog"
```

`resolve_catalog()` เป็นที่เดียวที่ตัดสินว่าอ่านจากไหน — **โค้ดฝั่งเทรนใช้ฟังก์ชันชื่อเดียวกัน ตรรกะเดียวกัน**
⇒ spec ที่เขียนที่นี่ยกไปให้ pipeline เทรนอ่านได้เลย

บังคับด้วย `validate_strict()` ใน `demo/server/flow/flowspec.py` — เรียกทุกครั้งที่สร้าง/แก้/อัปโหลดบริษัท
**key ที่ไม่อยู่ในเอกสารนี้ = error ไม่ใช่ถูกมองข้าม**

---

## catalog — เขียน 2 field พอ

```json
[
  {"_fine_state": "greet_verify", "template": "สวัสดีค่ะ ... คุณ {customer_name} ใช่ไหมคะ"},
  {"_fine_state": "close",        "template": "ขอบคุณค่ะ สวัสดีค่ะ"},
  {"_fine_state": "close",        "template": "ขอบคุณมากค่ะ สวัสดีค่ะ"}
]
```

| field | บังคับ | หมายเหตุ |
|---|---|---|
| `_fine_state` | ✅ | ชื่อ beat — **กาวเส้นเดียวที่เชื่อม spec กับ catalog** |
| `template` | ✅ | ข้อความ `{slot}` เติมจาก CRM/ผล tool อัตโนมัติ |
| `text_id` | — | ไม่ใส่ก็ได้ ระบบแจกตอนสร้างแล้วตรึงลงไฟล์ |
| `company` `state` `intent_name` `category` | — | ระบบเดาจาก spec (`normalize_catalog`) |
| `_อะไรก็ได้` | — | metadata (`_synthetic`, `_real_count`) ไม่มีใครอ่านตอนรัน |

**หลายบรรทัดชื่อเดียวกัน = หลายสำนวน** โมเดลเลือกด้วย `text_id` ว่าจะพูดแบบไหน

**ทำไมต้องมีทั้ง `_fine_state` และ `text_id`** — ตอนสร้างข้อมูลเทรน ระบบ**สับ `text_id` ใหม่ทุก task**
(`greet_verify` = 7176 / 4728 / 6483 / 3690 ใน 4 task แรก) เพื่อบังคับให้โมเดลอ่าน catalog ไม่ใช่ท่องเลข
`_fine_state` คือชื่อเดียวที่รอดจากการสับ ⇒ spec / กฎ / reward / eval อ้างชื่อนี้ ไม่อ้างเลข

---

## spec — states

```json
{ "id": "disclose_ask", "phase": "main", "counts_as": "pay_ask",
  "templates": [{"fine_state": "disclose_balance"}, {"fine_state": "ask_pay_today"}],
  "entry_tools": ["check_account_status"],
  "on": [{"event": "agrees_to_pay", "to": "ptp_capture"}] }
```

key ที่ใช้ได้: `id` `phase` `initial` `terminal` `templates` `on` `entry_tools` `outcome`
`note` `spec_note` `counts_as` `max_visits` `inferred`

### templates — chain หรือ ทางเลือก ตัดสินจาก `when_event`

```json
"templates": [
  {"fine_state": "disclose_balance"},                              // ไม่มี when_event…
  {"fine_state": "ask_pay_today"}                                  // …หลายอัน = พูดต่อกันในเทิร์นเดียว
]

"templates": [
  {"fine_state": "convince_lost_job", "when_event": "hardship_lost_job"},   // มี when_event
  {"fine_state": "convince_sick",     "when_event": "hardship_sick"},       // = เลือกอันเดียวตาม event
  {"fine_state": "probe_hardship",    "optional": true}
]

"templates": [{"any_of": ["close", "close_paid"]}]                 // ขั้นเดียว รับได้หลาย beat
```

key ที่ใช้ได้: `fine_state` `any_of` `when_event` `optional` `note` `inferred`
(ต้องมี `fine_state` หรือ `any_of` อย่างน้อยหนึ่ง)

**เลิกใช้แล้ว — validator จะปฏิเสธ:** `compose` · `group` · `render_all_templates` · `template_mode`
ทั้งสี่ตัวเคยเข้ารหัสเรื่อง chain ด้วยมือ ตอนนี้ `is_chain_state()` อ่านจาก `templates` ตัวเดียว
ใช้นิยามเดียวกันทั้ง renderer / walker / reward / eval

---

## spec — constraints

เก็บเฉพาะกฎที่ต้อง **นับหรือจำข้ามทั้งสาย** — สิ่งที่ผังบอกไม่ได้เพราะผังรู้แค่ว่าตอนนี้อยู่ช่องไหน

| type | ใช้ทำอะไร |
|---|---|
| `max_occurrences` | นับ state ที่ติดป้าย `counts_as` ทั้งสาย เกินแล้วบังคับย้าย (กฎห้ามทวงเกิน 2 ครั้ง) |
| `once_per_call` | beat นี้พูดได้ครั้งเดียวต่อสาย |
| `repeat_only_on` | พูดซ้ำได้เฉพาะตอน event นี้ |
| `no_repeat_answered_request` | ห้ามถามซ้ำสิ่งที่ลูกค้าตอบไปแล้ว |
| `outcome_precondition` | ห้ามบันทึกผลบางแบบถ้าเงื่อนไข CRM/tool ไม่เข้า |
| `immediate_transition_on` | event นี้ตัดไป state นี้ทันทีจากทุกที่ |
| `tool_pair` | ลำดับ + `args_must_match` (ส่วนลำดับซ้ำกับ `must_precede` แต่ args ไม่ซ้ำ) |
| *(ไม่มี type)* | กฎแบบข้อความ ต้องมี `desc` — ถูก render ลง instruction ให้โมเดลอ่าน |

**ตัดออกไปแล้ว 5 ตัวเพราะซ้ำ** (`tools/drop_dup_constraints.py`)

| ตัดออก | ที่ที่พูดเรื่องเดียวกันอยู่แล้ว |
|---|---|
| `max_templates_per_reply` + `exceptions` | `states[].templates` — `exceptions` คือรายชื่อ chain ที่ลอกมา **เคยขัดกันจริงจนทำ eval ตัดสินผิด** |
| `resume_after_interrupt` | `faq_routing.routes[].then = "resume"` |
| `require_tool_before_end` | `tools[].gating.required_at = "end_of_call"` |
| `forbid_after_event` | ผัง state ไม่ย้อนกลับไป state นั้นอยู่แล้ว |

---

## spec — tools / faq_routing / outcomes

```json
"tools": {"declarations": [
  {"name": "record_outcome", "impl": "http", "desc": "บันทึกผลสาย",
   "url": "{API_BASE}/YOURCO/record_outcome", "method": "POST", "args": {},
   "gating": {"required_at": "end_of_call"}}
]}

"faq_routing": {"routes": [
  {"intent": "asks_caller", "desc": "ถามว่าโทรจากไหน",
   "templates": [{"fine_state": "faq_caller"}], "then": "resume"}
]}

"outcomes": {"required_at_close": true,
             "results": {"ptp": {"reasons": ["ptp", "minimum"], "desc": "รับปากชำระ"}}}
```

`impl: "http"` = ยิง POST ไป `url` ทุก tool — backend มี 2 อย่างเท่านั้น: **เรียก API** กับ **reply**
บริษัทที่ mock ไม่มีกฎเฉพาะ จะได้ generic 200 กลับ (`ok/ref/tool/company`) ⇒ บริษัทใหม่ใช้ได้ทันที

---

## ตรวจก่อนใช้

```bash
python3 -c "
import json,sys; sys.path.insert(0,'.')
from demo.server.flow.flowspec import validate_flow_spec, validate_strict, normalize_catalog
s=json.load(open('data/flows/KBANK-outbound-remind.json'))
c=normalize_catalog(json.load(open('data/pre-scripts/kbank_catalog.json')), s)
print(validate_flow_spec(s,c)[0] + validate_strict(s,c) or 'ผ่าน')"
```

`python3 tools/drop_dup_constraints.py --dry-run` — ดูว่ามี constraint ซ้ำโผล่กลับมาไหม
