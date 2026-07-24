# เพิ่มบริษัทใหม่เข้า Flow Mode

คู่มือ onboard บริษัทใหม่ให้ **flow-interpreter** (`sft_flow_v1`) ทำงานในเดโม โดย
**ไม่ต้องเทรนโมเดลใหม่** — แค่เขียน 2 ไฟล์ (FlowSpec + Catalog) แล้ว register + deploy

> flow-interpreter = โมเดลที่ "อ่าน FlowSpec + Catalog จาก prompt แล้วเดินตาม"
> เทรนบน synthetic flows ที่สุ่มโครง/ชื่อไปเรื่อยๆ จึงตาม flow ที่ไม่เคยเห็นได้

---

## แนวคิด: `_fine_state` คืออะไร

`_fine_state` = **ป้ายชื่อ "จังหวะ" ของบทสนทนา** — บอกว่าประโยคนี้ *ทำหน้าที่อะไร*
(ทักทาย+ยืนยันตัวตน / แจ้งยอด / ตอบ "นี่มิจฉาชีพไหม" / ปิดสาย) **ไม่ใช่ตัวข้อความ**

```
flow state (spec)  ──ผูกด้วย fine_state──►  catalog  ──►  text_id + ข้อความจริง
```

ตอนรัน: โมเดลอยู่ที่ state ไหน → spec บอกว่า fine_state ไหนพูดได้ → catalog แปลงเป็น
text_id → โมเดลเลือก → ระบบ render เป็นภาษาไทย เติม `{placeholder}` อัตโนมัติ

**คุณสมบัติสำคัญ:**
- ชื่อ fine_state เป็น **สตริงอะไรก็ได้** (`greet_verify`, `abc`, `s1`) — ระบบสนแค่
  **spec กับ catalog สะกดตรงกันเป๊ะ** (case + ช่องว่างต้องตรง)
- แนะนำตั้งชื่อสื่อความหมาย: (1) โมเดลเห็นชื่อใน prompt → hint ให้ adherence ดีขึ้น
  (2) คุณ debug ง่ายกว่า
- **เพิ่ม/ลด fine_state ได้อิสระ** ตามบริษัท — ชุด 28 ของ AEON เป็นแค่ convention

**กติกา coverage (validator เช็ค):**
| กรณี | ผล |
|---|---|
| spec ผูก fine_state ที่ catalog **ไม่มี** | ❌ error `template binding unresolved` (flow ตัน) |
| catalog มี fine_state ที่ spec **ไม่ใช้** | ⚠️ warning เฉยๆ (ไม่พัง) |

⇒ **spec-bound ⊆ catalog** เสมอ

---

## ต้องเตรียม 2 ไฟล์

### 1. Catalog — `data/pre-scripts/<company>_catalog.json`
JSON list ของ template แต่ละ entry:
```json
{
  "company": "NEWCO",
  "text_id": 1000,                    // int ไม่ซ้ำในไฟล์
  "template": "สวัสดีค่ะ ...{customer_name}...",   // ข้อความไทย + {placeholder}
  "_fine_state": "greet_verify",      // ⭐ คีย์จับคู่กับ spec
  "intent_name": "opening_greet_verify",
  "category": "A", "state": "opening",
  "is_closer": false, "is_demand": false,
  "is_acknowledgment": false, "expects_response": true
}
```

**Placeholder ใช้ `{ }` แบบเดียว** (รวมคำลงท้ายตามเพศ `{suffix}`/`{q_suffix}`/`{pronoun}`)

**Placeholder ที่ระบบเติมให้อัตโนมัติจาก `customer_data`:**
`{customer_name} {amount} {minimum_payment} {due_date} {company_phone}`
+ custom ต่อบริษัท (เช่น AIS ใช้ `{msisdn}`)
⚠️ placeholder ที่โมเดลต้องส่งเองผ่าน `dynamic_vars` (เช่น `{promise_date}`) ถ้าโมเดล
ไม่ส่ง จะหลุดเป็น literal — เลี่ยงถ้าไม่จำเป็น

**ชื่อบริษัท:** hardcode ในข้อความ (`"บริษัทอิอ้อน"`, `"น้องไอ"`) หรือใช้ `[company_name]`

**เสียง M/F:** ใช้ `{suffix}` (ครับ/ค่ะ) / `{q_suffix}` (ครับ/คะ) / `{pronoun}` (ผม/ดิฉัน)
แบบ catalog AEON ถ้าอยากให้ particle ตรงเสียงที่เลือก — ไม่งั้นเป็น female-only

### 2. FlowSpec — `data/flows/<COMPANY>-outbound-remind.json`
2 ทาง:
- **ง่าย (แนะนำเริ่มต้น):** copy `AEON-outbound-remind.json` เปลี่ยนแค่ `company` + `flow_id`
  → ใช้โครง flow เดิม (states/transitions/tools/faq/outcomes) ถ้า catalog ขาด fine_state
  บางอัน ก็ trim binding นั้นทิ้ง
- **เต็ม (flow-interpreter จริง):** เขียน spec ใหม่ (states/transitions ต่างไป) ถ้า flow
  บริษัทต่างจริง — schema ดู `documentation/flow-spec.md` (repo aax6)

---

## ใช้ไฟล์ skeleton ที่เตรียมไว้

มีตัวตั้งต้นให้แล้ว (copy ไปแก้):
- `data/pre-scripts/_TEMPLATE_company_catalog.json` — **28 entries ครบ fine_state**,
  pre-fill ข้อความ AEON เป็นแนว + field ช่วย `_hint_where` (จังหวะนี้อยู่ตรงไหนของ flow)
  และ `_example_AEON`
- `data/flows/_TEMPLATE-outbound-remind.json` — spec เปล่า (`company: "NEWCO"`)

**ขั้นตอน:**
1. copy 2 ไฟล์ → ตั้งชื่อบริษัท (`NEWCO_catalog.json`, `NEWCO-outbound-remind.json`)
2. **catalog:** แก้ `company`, เขียน `template` ใหม่ในน้ำเสียงบริษัท (ดู `_hint_where` +
   `_example_AEON` ว่าสลอตนี้ต้องพูดอะไร) — อย่างน้อยต้องเปลี่ยนชื่อบริษัทออก;
   ลบ field `_hint_where`/`_example_AEON` ทิ้งได้ (ระบบ ignore key แปลก)
3. **spec:** เปลี่ยน `company` + `flow_id` พอ (โครงใช้ร่วม); ถ้าตัด fine_state ก็ trim binding
4. **validate** (ดูด้านล่าง) ต้อง 0 errors

---

## Register + Deploy

### 3. Register (2 จุด)
**Backend** — [`demo/server/sessions.py`](../demo/server/sessions.py) → `FLOW_REGISTRY`:
```python
FLOW_REGISTRY = {
    "AEON": ("AEON-outbound-remind.json", "v10_pre_script_database_parameterized.json"),
    ...
    "NEWCO": ("NEWCO-outbound-remind.json", "NEWCO_catalog.json"),   # เพิ่มบรรทัดนี้
}
```
**Frontend** — [`demo/frontend/src/App.tsx`](../demo/frontend/src/App.tsx) → `FLOW_COMPANIES`:
```ts
const FLOW_COMPANIES = ["AEON", "JAI", "KS", "AIS", "NEWCO"];   // เพิ่ม
```

### 4. Personas
ต้องมี case `TC-NEWCO-...` ใน `data/test-cases/personas_data.json` (ให้ picker มี
customer_data ของบริษัทนั้น) ไม่งั้น flow จะ fallback เป็น AEON

### 5. Deploy
```bash
git add data/flows/NEWCO-outbound-remind.json data/pre-scripts/NEWCO_catalog.json \
        demo/server/sessions.py demo/frontend/src/App.tsx
git commit -m "feat(demo): add NEWCO to flow mode"
git push origin main
# บน deploy host:
git pull
# restart backend (frontend vite dev จะ HMR reload เอง):
#   kill uvicorn เดิม → bash scripts/serve หรือ run_demo.sh ใน tmux
```
โมเดล `sft_flow_v1` ไม่ต้องแตะ — มัน serve อยู่แล้ว อ่าน spec ใหม่จาก prompt เอง

---

## Validate

```bash
python - <<'PY'
import json
from aax6.core.flowspec import validate_flow_spec   # repo aax6 (PYTHONPATH=src)
# หรือใน deliverable: from demo.server.flow.flowspec import validate_flow_spec
spec = json.load(open("data/flows/NEWCO-outbound-remind.json"))
cat  = json.load(open("data/pre-scripts/NEWCO_catalog.json"))
errs, warns = validate_flow_spec(spec, cat)
print("errors:", len(errs)); [print("  ", e) for e in errs]
print("warnings:", len(warns))
PY
```
**errors ต้องเป็น 0** ก่อน deploy (warnings ปล่อยได้ — แค่ template ส่วนเกิน/unbound)

---

## 28 fine_states ของ flow outbound-remind (อ้างอิง AEON)

| fine_state | จังหวะ |
|---|---|
| `greet_verify` | เปิดสาย + ยืนยันชื่อ |
| `verify_name` | ยืนยันชื่อซ้ำ |
| `third_party` | ไม่ใช่เจ้าตัวรับสาย |
| `disclose_balance` | แจ้งยอดค้าง |
| `ask_pay_today` | ชวนชำระวันนี้ |
| `convince_lost_job` / `convince_sick` / `convince_other` | โน้มน้าวตามเหตุ (ตกงาน/ป่วย/อื่นๆ) |
| `probe_hardship` | ถามสาเหตุที่จ่ายไม่ได้ |
| `confirm_info` | สรุปข้อตกลง |
| `close` | ปิดสาย |
| `offer_callback` | เสนอโทรกลับ |
| `apology` | ขอโทษ/ติดต่อไม่ได้ |
| `faq_caller` | "โทรจากไหน" |
| `ai_disclosure` | "เป็นบอทใช่ไหม" |
| `faq_hold` | "รอแป๊บ" |
| `faq_repeat` | "พูดอีกที" |
| `handoff_refuse` | "ขอคุยคนจริง" |
| `faq_scam` | "มิจฉาชีพรึเปล่า" |
| `faq_annoyed` | "รำคาญ/อย่าโทรมา" |
| `offer_channel_only` / `offer_channel` | "จ่ายที่ไหน/ยังไง" |
| `faq_amount` | "ยอดเท่าไหร่" |
| `faq_due` | "จ่ายเมื่อไหร่" |
| `faq_wrong_name` | เรียกชื่อผิด |
| `faq_mourning` | เจ้าของชื่อเสียชีวิต |
| `faq_faq_referral` | นอกขอบเขต → ให้โทรเบอร์บริษัท |
| `other` | รับทราบกลางๆ (fallback) |

> FAQ routes ตั้ง `then: "resume"` = ตอบแล้ววกกลับ flow เดิม (return-to-flow) →
> ลูกค้าถามแทรก/ออกนอกเรื่องแล้ว agent ดึงกลับเรื่องเดิมได้

---

## Gotchas

- ลืม `_fine_state` สัก entry → spec ผูก template ไม่เจอ
- catalog cover ไม่ครบเท่าที่ spec ผูก → validate error (ต้อง trim spec หรือเติม template)
- `text_id` ซ้ำในไฟล์เดียวกัน
- placeholder ที่โมเดลต้องส่งเอง (`{promise_date}` ฯลฯ) หลุดเป็น literal
- ยิ่ง flow/fine_state ต่างจากที่เทรนมามาก → rough edges มากขึ้น (adherence ~0.28 บน
  flow ที่ไม่เคยเห็น) จังหวะมาตรฐาน (greet/verify/balance/close) แน่นสุด
- **flow mode เป็น experimental** — ยังไม่ผ่าน PIPA gate อย่าตั้งเป็น default ให้ลูกค้า

---

## ทำไมไม่ต้องเทรนใหม่

`sft_flow_v1` เทรนบน 41 synthetic flows ที่สุ่มชื่อ states/fine_states/tools ไปเรื่อยๆ →
เรียนรู้ที่จะ **"อ่าน vocabulary ของ flow จาก prompt แล้วเดินตาม"** ไม่ใช่ท่องจำ flow ใด
flow หนึ่ง เพราะงั้น spec + fine_state ชุดใหม่ที่ไม่เคยเห็น มันก็ตามได้ นี่คือ core
capability ของ flow-interpreter — **onboard บริษัทใหม่ = เขียน spec/catalog ไม่ retrain**
