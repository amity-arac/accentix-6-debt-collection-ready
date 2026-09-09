# คู่มือการสร้าง Tenant ใหม่บน Accentix

เอกสารฉบับนี้อธิบายขั้นตอนการสร้างบริษัทใหม่ (Tenant) บน Accentix โดยกำหนดให้ **หนึ่งบริษัทมีหนึ่งไฟล์ JSON** เป็นข้อมูลหลักของบริษัท

การอัปโหลดไฟล์ JSON จะเป็นกระบวนการสร้างบริษัท และการลบบริษัทผ่าน UI จะเป็นกระบวนการลบบริษัท โดยแอปพลิเคชันไม่มี business logic ที่ผูกกับบริษัทใดบริษัทหนึ่งโดยเฉพาะ

## ข้อกำหนดเบื้องต้น

สิ่งที่จำเป็นสำหรับการดำเนินการมีดังนี้

* เว็บเบราว์เซอร์
* URL ของเว็บแอปพลิเคชัน
* ไม่จำเป็นต้องติดตั้งซอฟต์แวร์เพิ่มเติม
* ไม่จำเป็นต้องเข้าถึง source code ของระบบ
* API ของบริษัท จำนวนหนึ่ง endpoint ต่อหนึ่ง tool ตามที่ประกาศไว้ใน specification
* ข้อมูลลูกค้าสำหรับการทดลอง สามารถกำหนดผ่านไฟล์อัปโหลดได้ โดยยังไม่จำเป็นต้องเชื่อมต่อ CRM จริง

เอกสารประกอบระบบมี 2 ฉบับ ได้แก่

| เอกสาร                                                  | รายละเอียด                                                             |
| ------------------------------------------------------- | ---------------------------------------------------------------------- |
| **MANUAL.md** (เอกสารฉบับนี้) · [English](MANUAL.en.md) | แนวทางการจัดทำ specification ของบริษัทและการนำขึ้นทดลองบนเว็บ          |
| [SPEC_LOCKED.md](SPEC_LOCKED.md)                        | รายละเอียดของ key ทั้งหมดใน `<CODE>.company.json` ใช้เป็นเอกสารอ้างอิง |
| [SERVING.md](SERVING.md)                                | การเสิร์ฟโมเดลด้วย vLLM ให้แอปเรียกใช้                                 |
| [CODE.md](CODE.md) *(อังกฤษ)*                           | โครงสร้างของแอป — ส่วน A สำหรับผู้สาธิต · ส่วน B สำหรับผู้รับช่วงโค้ด        |

เอกสารฉบับนี้แบ่งออกเป็น 2 ส่วน

1. การจัดทำ `<CODE>.company.json` ตาม 7 ขั้นตอน
2. การใช้งาน Demo App รวมถึงการอัปโหลด การทดลองสนทนา และการวิเคราะห์สาเหตุเมื่อ Agent ตอบไม่ถูกต้อง

> **แพลตฟอร์มจัดเตรียมกลไก ส่วน Tenant เป็นผู้กำหนดนโยบาย**
> กฎเฉพาะของบริษัทควรกำหนดไว้ใน `constraints` ซึ่งจะถูกนำไปใช้เป็นข้อความใน prompt หรือกำหนดให้ API ของบริษัทเป็นผู้ปฏิเสธคำขอเมื่อไม่เป็นไปตามเงื่อนไข
> กฎที่ไม่มี mechanism รองรับจะไม่สามารถบังคับใช้ได้จริง

---

# 1. การจัดทำ `<CODE>.company.json`

เริ่มต้นโดยเลือก **Upload → Download template** ในหน้าเว็บ เพื่อดาวน์โหลดโครงสร้าง JSON ที่มี key ครบถ้วนตามรูปแบบที่แอปรองรับ จากนั้นกรอกข้อมูลตาม 7 ขั้นตอนต่อไปนี้

ชื่อไฟล์เป็นตัวระบุ identity ของบริษัท จึงไม่จำเป็นต้องประกาศ `flow_id` และ `spec_version` ด้วยตนเอง

ส่วน `company` ให้กำหนดไว้ภายใน `spec` เนื่องจากใช้เป็นรหัสบริษัทขณะอัปโหลด

## Key ที่จำเป็น

มี key ที่จำเป็นทั้งหมด 5 รายการ ได้แก่

* `events`
* `states`
* `tools`
* `faq_routing`
* `constraints`

หากขาด key รายการใดรายการหนึ่ง การตรวจสอบจะไม่ผ่าน

หากไม่มี FAQ สามารถกำหนด `faq_routing` เป็น `{"routes": []}` และหากไม่มีข้อจำกัดเพิ่มเติม สามารถกำหนด `constraints` เป็น `[]` ได้ แต่ยังคงต้องมี key ดังกล่าว

`catalog` ไม่ใช่ key บังคับ อย่างไรก็ตาม หากไม่มี `catalog` Agent จะไม่มีข้อความสำหรับใช้ในการสนทนา

```text
1. events        กิจกรรมหรือการตอบสนองที่ลูกค้าสามารถดำเนินการได้ และรูปแบบการสิ้นสุดของสาย
2. states        ผังการทำงาน ระบุจังหวะการสนทนา การเรียก tool การเปลี่ยน state และการสิ้นสุดสาย
3. tools         ข้อมูลที่ระบบต้องเป็นผู้ตอบ ไม่ควรให้โมเดลคาดเดา
4. catalog       ประโยคสำหรับทุก beat ที่ถูกอ้างถึงในผัง
5. faq_routing   การจัดการคำถามแทรกระหว่าง flow และการกลับเข้าสู่ flow เดิม
6. crm/session   แหล่งข้อมูลลูกค้า ข้อมูลที่โมเดลสามารถเข้าถึงได้ และพฤติกรรมเมื่อ API ไม่ตอบสนอง
7. constraints   กฎของบริษัท โดยควรกำหนดเฉพาะกฎที่จำเป็นและเพิ่มทีละข้อพร้อมประเมินผล
```

## ข้อมูลระบุตัวตนของ Agent

ก่อนเริ่มกำหนด `events` ควรพิจารณา 3 field ที่ใช้ระบุบทบาทและวัตถุประสงค์ของ Agent ได้แก่

| field          | ตำแหน่งการใช้งาน                                    | ค่าเริ่มต้นหากไม่กำหนด                          |
| -------------- | --------------------------------------------------- | ----------------------------------------------- |
| `display_name` | ชื่อที่แสดงใน UI และใช้แทน `[company]`              | ใช้ชื่อไฟล์                                     |
| `agent_role`   | บรรทัดแรกของ prompt ใช้ระบุตัวตนของ Agent           | `"เจ้าหน้าที่ติดตามทวงถามหนี้ของบริษัท <CODE>"` |
| `goal`         | บรรทัดถัดจาก `agent_role` ใช้ระบุวัตถุประสงค์ของสาย | ไม่แสดงหัวข้อนี้                                |

ค่าเริ่มต้นของ `agent_role` ถูกกำหนดไว้สำหรับ flow งานติดตามหนี้โดยเฉพาะ ดังนั้น **Tenant ที่ไม่ได้ดำเนินงานด้านติดตามหนี้ควรกำหนด `agent_role` อย่างชัดเจน** มิฉะนั้น prompt จะระบุบทบาทของ Agent เป็นเจ้าหน้าที่ติดตามหนี้

ควรกำหนด `states` ก่อน `catalog` เสมอ เนื่องจากผังการทำงานเป็นตัวกำหนดว่าต้องมีข้อความประเภทใด ไม่ใช่การสร้างข้อความก่อนแล้วจึงกำหนดตำแหน่งการใช้งาน

---

## ขั้นที่ 1 — `events`

เริ่มจากการระบุรูปแบบการตอบสนองของลูกค้าและรูปแบบการสิ้นสุดของสาย

ทุกชื่อ event ที่ถูกอ้างถึงใน `states[].on[].event` หรือ `constraints[].event` ต้องประกาศไว้ใน `events` มิฉะนั้นการตรวจสอบจะไม่ผ่าน

```jsonc
"events": {
  "confirms_return": {
    "desc": "รับทราบ จะคืนตามกำหนด",
    "cues": ["ได้ครับ", "คืนตามกำหนด", "จะไปคืน", "รับทราบ"]
  },
  "request_extend": {
    "desc": "ขอต่ออายุการยืม",
    "cues": ["ขอต่อ", "ต่ออายุได้ไหม", "ขอยืมต่อ", "เลื่อนคืนได้ไหม"]
  },
  "gives_date": {
    "desc": "ระบุวันที่ต้องการคืน",
    "cues": ["วันที่ 20", "ศุกร์หน้า", "สัปดาห์หน้า", "สิ้นเดือน"]
  },
  "no_input": {
    "desc": "ไม่ตอบ / เงียบ",
    "cues": ["…"]
  }
}
```

รูปแบบของ event ต้องเป็น object ที่มี `desc` และ `cues` ไม่ใช่ string หากกำหนดเป็น string การตรวจสอบ `validate_strict` จะไม่ผ่าน และระบบจะแจ้งรูปแบบที่ถูกต้อง

`cues` ใช้เป็นตัวอย่างประกอบความหมายของ event สำหรับโมเดล โดยจะถูกนำไปแสดงใน prompt และตัดเหลือ 4 รายการแรก เช่น

```text
confirms_return (รับทราบ จะคืนตามกำหนด — เช่น ได้ครับ, คืนตามกำหนด, จะไปคืน, รับทราบ)
→ close_confirmed
```

สามารถไม่กำหนด `cues` ได้ ในกรณีดังกล่าว prompt จะแสดงเฉพาะ `desc` และโมเดลจะต้องตีความ event จากคำอธิบายดังกล่าว

---

## ขั้นที่ 2 — `states`

`states` เป็นโครงสร้างหลักของ flow ใช้กำหนดจังหวะการสนทนา เครื่องมือที่ต้องเรียก และการเปลี่ยน state

```jsonc
{
  "id": "greet",
  "phase": "opening",
  "initial": true,
  "templates": [{"fine_state": "greet_remind"}],
  "entry_tools": ["check_account_status"],
  "on": [
    {"event": "confirms_pay", "to": "close_confirmed"},
    {"event": "reschedule_request", "to": "ask_new_date"}
  ],
  "verify_required": true,
  "outcome": {"args": {"result": "confirmed"}},
  "terminal": false,
  "max_visits": 2
}
```

### `outcome.args`

`outcome.args` คือ argument ที่ tool สำหรับปิดสายจะได้รับ โดยต้องใช้ชื่อ argument ตามที่ประกาศไว้ใน tool

ตัวอย่างเช่น หาก tool สำหรับการนัดหมายประกาศ `save_appointment(status, …)` ค่าใน `outcome.args` ต้องใช้ชื่อ `status`

argument ตัวแรกที่ tool ประกาศถือเป็น "ผลสาย" และใช้เชื่อมโยงกับข้อความปิดสายให้สอดคล้องกับผลที่ถูกบันทึก

แต่ละ terminal state ต้องกำหนดค่าผลลัพธ์ของตนเอง ไม่ควรกำหนด outcome เดียวสำหรับทุกเส้นทาง

`outcome` ต้องอยู่ใน terminal state หาก terminal state ไม่มี `outcome` สายจะสิ้นสุดโดยไม่มีข้อมูลผลลัพธ์สำหรับบันทึกลง CRM

นอกจากนี้ terminal state ทุกตัวควรมี tool สำหรับปิดสายอยู่ใน `entry_tools` เนื่องจากหากไม่มี tool ดังกล่าว สายจะสิ้นสุดโดยไม่มีการบันทึกผล และไม่มีตัวตรวจสอบเฉพาะสำหรับกรณีนี้

### `verify_required`

`verify_required: true` จะทำงานได้ต่อเมื่อมี tool ที่ประกาศ `provides: "verified"` และกำหนด `verified_when` เพื่อระบุวิธีอ่านผลลัพธ์จาก API

```jsonc
"states": [
  {
    "id": "disclose",
    "verify_required": true
  }
],
"tools": {
  "declarations": [
    {
      "name": "verify_identity",
      "provides": "verified",
      "verified_when": {
        "field": "match",
        "equals": true
      }
    }
  ]
}
```

หากขาดองค์ประกอบใดองค์ประกอบหนึ่ง `_verify_gate()` จะไม่สามารถสร้าง verification gate ได้ ส่งผลให้ state ดังกล่าวไม่ถูกบล็อกและไม่มีคำเตือน

---

## ขั้นที่ 3 — `tools`

หลักการสำคัญคือควรให้ **ระบบเป็นผู้ให้ข้อมูลที่เป็นข้อเท็จจริง** และให้โมเดลเป็นผู้ตีความหรือดำเนินการตามผลลัพธ์ของระบบ

ข้อมูล เช่น วันที่ จำนวนเงิน เพดาน สิทธิ์ และยอดต่าง ๆ ควรได้รับจาก tool แทนการให้โมเดลคำนวณหรือคาดเดาเอง

`tools` ต้องเป็น object และ declaration แต่ละรายการต้องอยู่ภายใน `tools.declarations`

```jsonc
"tools": {
  "declarations": [
    {
      "...": "..."
    }
  ]
}
```

### Tool สำหรับปิดสาย

ต้องมี tool สำหรับปิดสายอย่างน้อยหนึ่งรายการ โดยประกาศ

```jsonc
"gating": {
  "max_successful_calls": 1,
  "required_at": "end_of_call"
}
```

สามารถมี closing tool ได้เพียงหนึ่งรายการต่อ specification และระบบไม่มีค่าเริ่มต้นให้โดยอัตโนมัติ

หาก specification มี state ที่กำหนด `outcome` แต่ไม่มี closing tool ระบบอาจผ่านการ validate แต่ไม่สามารถประกอบ prompt ได้ และจะเกิดข้อผิดพลาด `ValueError: spec declares no closing tool`

ตัวอย่าง:

```jsonc
{
  "name": "record_call_result",
  "impl": "http",
  "url": "https://api.yourcompany.co.th/aax/record_call_result",
  "method": "POST",
  "headers": {
    "Authorization": "Bearer YOUR_TOKEN"
  },
  "timeout": 8,
  "desc": "บันทึกผลสาย",
  "args": {
    "result": {
      "type": "string",
      "enum": ["confirmed", "handoff"]
    }
  },
  "returns": {
    "saved": {
      "type": "boolean"
    }
  },
  "gating": {
    "max_successful_calls": 1,
    "required_at": "end_of_call"
  }
}
```

### Tool ต้องเชื่อมต่อกับ Endpoint ของ Tenant

ทุก declaration ต้องกำหนด

```json
"impl": "http"
```

และระบุ `url` ของ endpoint ที่บริษัทเป็นผู้ดูแล

Tenant มีหน้าที่จัดเตรียม endpoint ให้ครบตามจำนวน tool ที่ประกาศไว้ ระบบไม่มี API กลางสำหรับใช้แทน endpoint ของ Tenant

ไม่ควรนำ `{API_BASE}` ที่ปรากฏใน specification ตัวอย่างของระบบมาใช้ เนื่องจากเป็น environment variable ของเซิร์ฟเวอร์ที่รันระบบ

ใน specification ของ Tenant ควรระบุ URL แบบเต็มของ endpoint

| key       | รายละเอียด                                                                            |
| --------- | ------------------------------------------------------------------------------------- |
| `url`     | URL แบบเต็มของ endpoint เช่น `https://…`                                              |
| `method`  | ค่าเริ่มต้นคือ `POST`                                                                 |
| `headers` | ใช้สำหรับ token หรือ API key และสามารถแทนค่าจาก CRM ได้ เช่นเดียวกับ `url` และ `body` |
| `timeout` | หน่วยเป็นวินาที ค่าเริ่มต้น 8 วินาที การตอบสนองเกินเวลาถือเป็นการปฏิเสธ               |
| `body`    | ไม่บังคับ หากไม่กำหนด ระบบจะใช้โครงสร้างมาตรฐาน                                       |

มี `impl: "generic"` สำหรับการร่าง specification ในช่วงที่ endpoint ยังไม่พร้อมใช้งาน แต่ไม่เหมาะสำหรับ specification ที่นำไปใช้งานจริง เนื่องจากไม่มีการบันทึกข้อมูลไปยังระบบของ Tenant

### สัญญาการสื่อสารระหว่างระบบกับ Endpoint

#### ① Request

เมื่อไม่ได้กำหนด `body` ระบบจะส่ง request ในรูปแบบมาตรฐานเดียวกันสำหรับทุก tool

```jsonc
POST https://api.yourcompany.co.th/aax/record_call_result

{
  "tool": "record_call_result",
  "args": {
    "result": "confirmed"
  },
  "ref": {
    "msisdn": "081-234-5678",
    "customer_phone": "…",
    "last_4_digits": "…",
    "case_id": "…"
  }
}
```

สามารถใช้ handler เดียวและแยกประเภทของคำขอด้วย field `tool` ได้

`ref` จะประกอบด้วยเฉพาะ key ที่มีข้อมูลอยู่ใน CRM ของสายดังกล่าว

หาก endpoint มี request schema เฉพาะของบริษัท สามารถกำหนด `body` เพื่อระบุรูปแบบที่ต้องการได้

#### ② Response

Endpoint ต้องตอบกลับเป็น JSON object โดย JSON ที่ตอบกลับจะถูกใช้เป็นผลลัพธ์ของ tool โดยตรง และถูก merge เข้า `customer_data` เพื่อให้ข้อความถัดไปสามารถอ้างอิงข้อมูลดังกล่าวได้

ข้อมูลที่ซ้อนกันสามารถอ้างอิงได้ทั้งแบบชื่อเต็ม เช่น `appointment.doctor` และชื่อ field ระดับล่าง เช่น `doctor`

ดังนั้นควรประกาศ field ที่ endpoint สามารถคืนกลับมาได้ทั้งหมดไว้ใน `returns`

| Response                               | ผลที่โมเดลได้รับ                                                                         |
| -------------------------------------- | ---------------------------------------------------------------------------------------- |
| `200` + JSON object                    | JSON object ทั้งชุดและข้อมูลที่ถูก flatten แล้ว                                          |
| `200` + plain text เช่น `OK`           | `{"recorded": true, "response": "OK"}` ถือว่าสำเร็จ แต่ไม่มีข้อมูลให้ข้อความถัดไปอ้างอิง |
| non-2xx / timeout / connection failure | `{"error": "http_error", "detail": "http_404: …"}` ซึ่งจะถูกส่งกลับให้โมเดลอ่าน          |

#### ③ การปฏิเสธคำขอ

Endpoint สามารถปฏิเสธคำขอโดยคืน field ชื่อ `error`

ระบบจะตีความ `error` เป็นสัญญาณการปฏิเสธในลักษณะเดียวกับ gate ภายใน และโมเดลจะได้รับเหตุผลดังกล่าวเพื่อดำเนินการใหม่ในเทิร์นเดิม โดยผู้รับสายจะไม่ได้ยินข้อความของรอบที่ถูกปฏิเสธ

```jsonc
{
  "error": "date_out_of_range",
  "valid_dates": [
    "2026-09-12 (Saturday)",
    "…"
  ]
}
```

การบังคับใช้กฎของบริษัทจึงควรดำเนินการใน endpoint ที่สามารถปฏิเสธคำขอได้จริง ส่วน `constraints` เป็นข้อกำหนดที่ส่งไปยังโมเดลและไม่ใช่ enforcement mechanism โดยตรง

ตัวอย่าง tool:

```jsonc
{
  "name": "check_new_date",
  "impl": "http",
  "url": "https://api.yourcompany.co.th/aax/check_new_date",
  "method": "POST",
  "desc": "ตรวจสอบว่าวันที่ลูกค้าระบุอยู่ในเกณฑ์ของร้านหรือไม่ โดยระบบเป็นผู้ตัดสิน",
  "args": {
    "date": {
      "type": "string",
      "format": "YYYY-MM-DD (Weekday)"
    }
  },
  "returns": {
    "in_range": {
      "type": "boolean"
    },
    "valid_dates": {
      "type": "array"
    }
  },
  "gating": {
    "after_event": "gives_date",
    "must_precede": ["record_call_result"]
  }
}
```

`returns` ไม่ได้มีไว้สำหรับเอกสารเท่านั้น แต่เป็นข้อมูลที่ระบบนำไป merge เข้า `customer_data` เพื่อใช้ในข้อความถัดไป

### `one_of_from`

หาก argument ของ tool ต้องเลือกจากค่าที่ระบบเสนอ ควรระบุ `one_of_from`

```jsonc
"new_date": {
  "type": "string",
  "optional": true,
  "required_when": {
    "arg": "result",
    "equals": "rescheduled"
  },
  "one_of_from": {
    "tool": "check_new_date",
    "field": "valid_dates"
  }
}
```

ในกรณีนี้ `new_date` ต้องเป็นหนึ่งในค่าที่ `check_new_date.valid_dates` เคยเสนอ

อย่างไรก็ตาม `one_of_from` จะตรวจสอบได้เฉพาะเมื่อ tool ต้นทางถูกเรียกและมีรายการค่าที่เสนอ หากไม่มีการเรียก tool ต้นทาง การตรวจสอบดังกล่าวจะไม่มีค่ามาเปรียบเทียบ

ดังนั้นกรณีที่ต้องการบังคับให้เรียก tool ก่อน ต้องใช้ `gating` ร่วมด้วย โดยทั่วไปควรกำหนดทั้ง

* `one_of_from` เพื่อป้องกันค่าที่ไม่ได้มาจากระบบ
* `gating` เพื่อป้องกันการข้ามขั้นตอน

---

## ขั้นที่ 4 — `catalog`

`catalog` เป็นคลังประโยคที่ใช้ในแต่ละ beat

```jsonc
{
  "text_id": 2341,
  "_fine_state": "greet_remind",
  "template": "สวัสดี{suffix} ร้านค้าโทรมาแจ้งเตือนงวดของคุณ [customer_name] ที่ครบกำหนด [due_date]{suffix}"
}
```

ใน `template` สามารถใช้รูปแบบต่อไปนี้ตามลำดับการ resolve

```text
{suffix} {q_suffix} {pronoun}
{{if field}}…{{else}}…{{/if}}
[field]
{field}
```

`[field]` และ `{field}` ให้ผลเหมือนกัน โดย `{field}` มีการลด prefix ที่ซ้ำ เช่น `"คุณ นายเอกชัย"` จะถูกลดเหลือ `"นายเอกชัย"`

รูปแบบของ placeholder มีรายละเอียดเพิ่มเติมเกี่ยวกับ mapping การลดระดับเป็นภาษาไทย และการตรวจสอบวันที่ใน [SPEC_LOCKED §3](SPEC_LOCKED.md)

ควรเลือกใช้รูปแบบ `[field]` หรือ `{field}` ให้เป็นรูปแบบเดียวกันภายในไฟล์ใหม่หนึ่งไฟล์ แม้ว่าระบบปัจจุบันจะรองรับทั้งสองรูปแบบ

### `text_id` และ `_fine_state`

ควรแยกความหมายของสอง field นี้อย่างชัดเจน

|            | `_fine_state` (beat)                                          | `text_id`                                     |
| ---------- | ------------------------------------------------------------- | --------------------------------------------- |
| ความหมาย   | ระบุว่าจะพูดเรื่องใด                                          | ระบุว่าจะใช้สำนวนใด                           |
| หน่วย      | จังหวะหนึ่งใน flow                                            | ประโยคหนึ่งประโยค                             |
| จำนวน      | เช่น AEON มี 40 beats                                         | เช่น AEON มี 58 ประโยค                        |
| การอ้างถึง | `states`, `faq_routing`, `fallback_fine_state`, `constraints`  | โมเดลเมื่อเรียก `reply(text_ids=[…])`         |
| ความคงทน   | เป็นชื่อเชิงโครงสร้าง                                         | เป็น identifier ของประโยคและอาจเปลี่ยนแปลงได้ |

ตัวอย่างการทำงาน

```text
โมเดลสั่ง reply(text_ids=[1047])
        ↓
_by_id[1047]
        ↓
beat "close"
        ↓
state เจ้าของ beat ผ่าน _beat_states()
        ↓
ตรวจสอบ entry_tools ของ state
        ↓
อนุญาตให้พูด
```

การแยกสองระดับนี้ทำให้สามารถเพิ่มหรือเปลี่ยนสำนวนโดยไม่ต้องแก้ flow หากเจตนาของ beat ยังคงเดิม

`text_id` ไม่ควรถูกใช้เป็น identifier ที่มีความหมายทางธุรกิจ เนื่องจากระบบสามารถสร้างเลขใหม่ในแต่ละสายทดลองและในข้อมูลสำหรับการฝึกได้

เมื่อเขียนกฎหรือ hint ควรอ้างถึง beat และสามารถระบุ `text_id` ประกอบเพื่อช่วยในการอ่าน แต่ไม่ควรใช้เลขเป็นตัวอ้างอิงหลัก

### ความสัมพันธ์ระหว่าง `states` และ `catalog`

```text
states[]                                  catalog[]._fine_state + text_id
─────────────────────────────────         ─────────────────────────────────
state "disclose_ask"
  verify_required: true
  templates:
    { any_of: [
        "disclose_balance" ───→ 1018   "ขออนุญาตแจ้งยอด…"
        "ask_pay_today"    ───→ 1020   "ตามที่ท่านได้นัดชำระ…"
      ]
      note: "ประโยคเดียวแจ้งยอด          1021   "ถ้าเป็นยอดขั้นต่ำ สะดวกวันนี้เลยมั้ย"
             และถามพร้อมกัน"              1023   "ชำระยอดขั้นต่ำภายในวันนี้ก่อนได้ไหม"
                                          1028   "ไม่ทราบว่าสะดวกชำระภายในวันนี้มั้ย"
  on:                                     1039   "รอบบิล ครบกำหนด [due_date]…"
    agrees_to_pay → ptp_capture           1066   "ชำระยอดขั้นต่ำภายในวันนี้ก่อนได้มั้ย"
    already_paid → close_paid
```

ผังจะอ้างถึง beat ส่วน catalog จะเชื่อม beat เข้ากับประโยคที่สามารถใช้ได้

### Chain State

ตัวบ่งชี้ว่า state เป็น chain หรือไม่คือ `when_event` ไม่ใช่จำนวน template

| รูปแบบ `templates`                                       | ความหมาย                                      |
| -------------------------------------------------------- | --------------------------------------------- |
| มี 1 entry                                               | ไม่ใช่ chain                                  |
| มี ≥2 entry และมีอย่างน้อยหนึ่ง entry ที่มี `when_event` | ตัวเลือกตาม event                             |
| มี ≥2 entry และไม่มี entry ใดมี `when_event`             | chain — ใช้ครบทุก entry ในเทิร์นเดียว         |
| มี `any_of` ภายในหนึ่ง entry                             | เป็นการเลือกภายใน entry และไม่เกี่ยวกับ chain |

ตัวอย่าง chain

```jsonc
"templates": [
  {"fine_state": "close"},
  {"fine_state": "apology"}
]
```

ตัวอย่างตัวเลือกตาม event

```jsonc
"templates": [
  {
    "fine_state": "convince_lost_job",
    "when_event": "hardship_lost_job"
  },
  {
    "fine_state": "convince_sick",
    "when_event": "hardship_sick"
  },
  {
    "when_event": "hardship_other",
    "any_of": ["convince_other", "convince_pay"]
  },
  {
    "fine_state": "probe_hardship",
    "optional": true,
    "counts_as": false
  }
]
```

Chain state ที่มีอยู่ใน tenant ปัจจุบัน ได้แก่

```text
AEON / AEONLITE   close_unreachable · close_new_phone
KBANK             ptp_capture · close_unreachable
SKL               disclose_ask · ptp_capture · close_confirm_info
AMT / SHOP / LIB  ไม่มี
```

หากกำหนด `when_event` ไม่ครบใน state ที่ควรเป็นตัวเลือก โมเดลอาจได้รับคำสั่งให้พูดหลายทางเลือกในเทิร์นเดียวกัน

ในทางกลับกัน หากละเว้น template ที่ควรเป็นส่วนหนึ่งของ chain โมเดลอาจพูดไม่ครบและถูกตรวจพบด้วย `incomplete_chain`

### หลักการกำหนด beat และ text variation

ใช้หลักเกณฑ์ต่อไปนี้

```text
ผลต่อ flow เหมือนกัน ต่างกันเฉพาะสำนวน
→ เพิ่ม text_id

ผลต่อ flow แตกต่างกัน
→ เพิ่ม fine_state
```

ตัวอย่างเช่น `faq_due` ของ LIB สามารถมี 3 สำนวน ได้แก่ แบบสั้น แบบปกติ และแบบละเอียดพร้อมค่าปรับ แต่ยังคงเป็น beat เดียว เนื่องจากทั้งสามสำนวนมีผลต่อ flow เหมือนกัน

ในทางกลับกัน `confirm_new_due` และ `date_too_far` ต้องเป็นคนละ beat เนื่องจากปลายทางของ flow แตกต่างกัน

> `_fine_state` = เจตนาและตำแหน่งใน flow
> `text_id` = รูปประโยคที่ใช้สื่อสารภายในเจตนาเดียวกัน

### รูปแบบชื่อที่ต้องใช้

```jsonc
"catalog": [
  {
    "text_id": 2341,
    "_fine_state": "greet_remind"
  }
]

"states": [
  {
    "templates": [
      {
        "fine_state": "greet_remind"
      }
    ]
  }
]
```

ใน `catalog` ต้องใช้ `_fine_state` ซึ่งมี `_` นำหน้า ส่วนใน `states` ต้องใช้ `fine_state` โดยไม่มี `_`

การสลับชื่ออาจทำให้ entry ผ่านการตรวจสอบบางส่วนแต่ไม่สามารถเชื่อมกับ catalog ได้อย่างถูกต้อง โดย `validate_strict` จะตรวจสอบและแจ้งรูปแบบที่ไม่ถูกต้อง

---

## ขั้นที่ 5 — `faq_routing`

`faq_routing` ใช้สำหรับจัดการคำถามที่เกิดขึ้นระหว่าง flow โดยตอบคำถามแล้วกลับเข้าสู่ state เดิม

```jsonc
"faq_routing": {
  "routes": [
    {
      "intent": "hours",
      "desc": "ถามเวลาเปิด-ปิดร้าน",
      "templates": [
        {
          "fine_state": "faq_hours"
        }
      ],
      "then": "resume"
    }
  ]
}
```

| key               | ความหมาย                                                                                                         |
| ----------------- | ---------------------------------------------------------------------------------------------------------------- |
| `intent`          | ชื่อ route ที่ใช้ใน prompt                                                                                       |
| `desc`            | คำอธิบายว่า route ใช้ในกรณีใด หากไม่กำหนดจะเป็น `""`                                                             |
| `templates`       | beat สำหรับตอบคำถาม ซึ่งต้องมีประโยคอยู่ใน `catalog`                                                             |
| `then`            | `"resume"` เพื่อกลับเข้าสู่ flow เดิม หรือกำหนด `{"terminal": true, "outcome": {"args": {...}}}` เพื่อสิ้นสุดสาย |
| `verify_required` | ใช้กำหนดให้ต้องยืนยันตัวตนก่อนตอบในกรณีที่คำตอบเปิดเผยข้อมูลลูกค้า                                               |

`faq_routing` เป็น key ที่จำเป็น แม้ไม่มี FAQ ให้กำหนดเป็น

```json
{
  "routes": []
}
```

FAQ beat ไม่ได้สังกัด state ใดโดยตรง ดังนั้นไม่ควรตรวจสอบจาก state graph เพียงอย่างเดียว

ในการตรวจ orphan ต้องพิจารณา beat ที่ถูกอ้างถึงใน

* `states`
* `faq_routing.routes`
* `fallback_fine_state`

---

## ขั้นที่ 6 — CRM และ Session

ตัวอย่างการกำหนด CRM:

```jsonc
"crm_fields": [
  "today",
  "customer_name",
  "book_title",
  "due_date",
  "fine_per_day",
  "library_phone"
],
"crm_labels": {
  "book_title": "ชื่อหนังสือ",
  "fine_per_day": "ค่าปรับต่อวัน"
},
"session_init": {
  "url": "https://api.yourcompany.co.th/aax/init?msisdn={msisdn}",
  "method": "GET",
  "timeout": 8,
  "headers": {
    "Authorization": "Bearer YOUR_TOKEN"
  },
  "on_failure": {
    "fine_state": "close_handoff",
    "outcome": {
      "args": {
        "result": "handoff",
        "reason": "ระบบไม่ตอบ ให้เจ้าหน้าที่ติดต่อกลับ"
      }
    }
  }
}
```

`crm_fields` ระบุ field ที่โมเดลสามารถมองเห็นได้ หากไม่ประกาศ field ใด โมเดลจะไม่สามารถเข้าถึง field นั้น

`crm_labels` ใช้กำหนดชื่อภาษาไทยที่แสดงใน CRM Snapshot หากไม่กำหนด ระบบจะใช้ชื่อ field

`session_init` จะถูกเรียกหนึ่งครั้งเมื่อเริ่มสาย

หากประกาศ `session_init` ต้องกำหนด `on_failure` ด้วย หากไม่มี `on_failure` ระบบจะไม่สามารถเปิดสายได้อย่างถูกต้อง

วันที่ที่ใช้ใน persona ไม่ควรกำหนดเป็นวันที่ตายตัว ควรใช้ `due_date_offset_days: 1` เพื่อให้แอปพลิเคชันและ mock คำนวณจากวันที่ปัจจุบัน

---

## ขั้นที่ 7 — `constraints`

`constraints` ใช้กำหนดข้อกำหนดที่ส่งไปยังโมเดล

```jsonc
"constraints": [
  {
    "id": "one_template_per_turn",
    "type": "max_templates_per_reply",
    "max": 1,
    "desc": "พูดหนึ่งจังหวะต่อเทิร์น",
    "enforce": ["prompt"]
  },
  {
    "id": "greeting_once",
    "type": "once_per_call",
    "template_fine_states": ["greet_remind"],
    "desc": "ทักทายแจ้งกำหนดคืนครั้งเดียวต่อสาย",
    "enforce": ["prompt"]
  },
  {
    "id": "system_counts_days",
    "enforce": ["prompt"],
    "desc": "ห้ามนับวันเอง — ลูกค้าบอกวันแล้วต้องเรียก check_extend แล้วอ่าน in_range"
  }
]
```

`enforce` ต้องเป็น list และมีค่าเดียวคือ `prompt` ซึ่งนำ `desc` ไปแสดงในส่วน
"หลักการ (⛔ กฎสูงสุด)" · constraint ที่ไม่มี `prompt` จะไม่ถูกใส่ใน prompt เลย

`backend` และ `reward` ไม่รองรับแล้ว และ `validate_strict` จะปฏิเสธ

การบังคับใช้ใน runtime ควรดำเนินการผ่าน `gating` ของ tool

`desc` เป็นข้อมูลหลักที่ส่งไปยังโมเดล ดังนั้น `constraints` ควรประกอบด้วยกฎที่จำเป็นต่อการทำงาน และควรเพิ่มทีละข้อพร้อมประเมินผล เพื่อหลีกเลี่ยงการกำหนดกฎจำนวนมากจนทำให้โมเดลตีความรวมกันเป็นกฎที่กว้างเกินไป

---

# ตัวอย่างเอกสารที่มีขนาดเล็กที่สุดและสามารถรันได้

ตัวอย่างต่อไปนี้เป็นบริษัทตัวอย่างสำหรับร้านกาแฟที่โทรแจ้งลูกค้าว่าสินค้าพร้อมรับ ประกอบด้วย 3 state, 2 tools, 5 ประโยค และ 1 FAQ route

ไฟล์นี้สามารถบันทึกเป็น `.json` และอัปโหลดผ่านหน้า Upload ของ Demo App ได้

| ชั้นนอกสุด                    | รายละเอียด                                                          |
| ----------------------------- | ------------------------------------------------------------------- |
| `display_name` · `agent_name` | ชื่อบริษัทและชื่อ Agent ที่แสดงใน UI                                |
| `crm`                         | ข้อมูลลูกค้าสำหรับสายทดลอง โดยวันที่ใช้ `<field>_offset_days`       |
| `spec`                        | specification ของบริษัทตาม 7 ขั้นตอน โดย `company` อยู่ภายในส่วนนี้ |
| `catalog`                     | ประโยคทั้งหมด โดย `text_id` สามารถละเว้นได้และระบบจะกำหนดให้        |

ไฟล์ตัวอย่างนี้ออกแบบให้ผ่าน

* `validate_strict`
* `validate_flow_spec`
* การสร้างบริษัทผ่านเส้นทางอัปโหลดจริง
* การประกอบ prompt

นอกจากนี้ยังมี test (`tests/test_manual_example_runs.py`) ที่นำตัวอย่างนี้ไปสร้างบริษัทจริงและลบออกเมื่อทดสอบเสร็จ หาก implementation เปลี่ยนจนตัวอย่างไม่สามารถอัปโหลดได้ การทดสอบจะล้มเหลวและช่วยตรวจพบความไม่สอดคล้องระหว่างเอกสารกับระบบ

<!-- MINIMAL-EXAMPLE -->

```json
{
  "display_name": "ร้านกาแฟตัวอย่าง",
  "agent_name": "น้องกาแฟ",
  "crm": {
    "customer_name": "สมชาย ใจดี",
    "order_no": "A-1042",
    "ready_date_offset_days": 1
  },
  "spec": {
    "company": "CAFE",
    "agent_role": "พนักงานร้านกาแฟตัวอย่าง",
    "goal": "แจ้งว่าของถึงแล้ว → ถามว่าจะมารับไหม → บันทึกผล",
    "crm_fields": ["today", "customer_name", "order_no", "ready_date"],
    "crm_labels": {
      "order_no": "เลขที่ออเดอร์",
      "ready_date": "วันที่ของถึง"
    },
    "events": {
      "confirms_pickup": {
        "desc": "รับทราบ จะมารับ",
        "cues": ["ได้ครับ", "จะไปรับ", "รับทราบ"]
      },
      "asks_hours": {
        "desc": "ถามเวลาเปิด-ปิดร้าน",
        "cues": ["เปิดกี่โมง", "ปิดกี่โมง"]
      },
      "cannot_come": {
        "desc": "มารับไม่ได้",
        "cues": ["ไม่ว่าง", "ให้โทรมาใหม่"]
      },
      "no_input": {
        "desc": "เงียบ ไม่ตอบ",
        "cues": ["…", "(เงียบ)"]
      }
    },
    "tools": {
      "declarations": [
        {
          "name": "check_order",
          "impl": "http",
          "method": "POST",
          "url": "https://api.yourcompany.co.th/aax/check_order",
          "headers": {
            "Authorization": "Bearer YOUR_TOKEN"
          },
          "desc": "ถามระบบว่าออเดอร์นี้พร้อมรับวันไหน — ห้ามตอบจากความจำ",
          "args": {},
          "returns": {
            "ready_date": {
              "type": "string",
              "format": "YYYY-MM-DD (Weekday)"
            }
          }
        },
        {
          "name": "record_call_result",
          "impl": "http",
          "method": "POST",
          "url": "https://api.yourcompany.co.th/aax/record_call_result",
          "headers": {
            "Authorization": "Bearer YOUR_TOKEN"
          },
          "desc": "บันทึกผลสาย",
          "args": {
            "result": {
              "type": "string",
              "enum": ["confirmed", "handoff"]
            }
          },
          "returns": {
            "saved": {
              "type": "boolean"
            }
          },
          "gating": {
            "max_successful_calls": 1,
            "required_at": "end_of_call"
          }
        }
      ]
    },
    "states": [
      {
        "id": "greet",
        "phase": "opening",
        "initial": true,
        "max_visits": 2,
        "templates": [
          {
            "fine_state": "greet_pickup"
          }
        ],
        "entry_tools": ["check_order"],
        "on": [
          {
            "event": "confirms_pickup",
            "to": "close_confirmed"
          },
          {
            "event": "cannot_come",
            "to": "close_handoff"
          },
          {
            "event": "no_input",
            "to": "close_handoff"
          }
        ]
      },
      {
        "id": "close_confirmed",
        "phase": "close",
        "terminal": true,
        "templates": [
          {
            "fine_state": "close_thanks"
          }
        ],
        "entry_tools": ["record_call_result"],
        "outcome": {
          "args": {
            "result": "confirmed"
          },
          "reasons": ["confirmed"],
          "desc": "รับปากจะมารับ"
        }
      },
      {
        "id": "close_handoff",
        "phase": "close",
        "terminal": true,
        "templates": [
          {
            "fine_state": "close_handoff"
          }
        ],
        "entry_tools": ["record_call_result"],
        "outcome": {
          "args": {
            "result": "handoff"
          },
          "reasons": ["handoff"],
          "desc": "ให้พนักงานติดต่อกลับ"
        }
      }
    ],
    "faq_routing": {
      "routes": [
        {
          "intent": "hours",
          "desc": "ถามเวลาเปิด-ปิดร้าน",
          "templates": [
            {
              "fine_state": "faq_hours"
            }
          ],
          "then": "resume"
        }
      ]
    },
    "fallback_fine_state": "faq_repeat",
    "constraints": [
      {
        "id": "one_template_per_turn",
        "type": "max_templates_per_reply",
        "max": 1,
        "desc": "พูดหนึ่งจังหวะต่อเทิร์น",
        "enforce": ["prompt"]
      },
      {
        "id": "greeting_once",
        "type": "once_per_call",
        "template_fine_states": ["greet_pickup"],
        "desc": "ทักทายแจ้งออเดอร์ครั้งเดียวต่อสาย",
        "enforce": ["prompt"]
      },
      {
        "id": "system_owns_the_date",
        "enforce": ["prompt"],
        "desc": "ห้ามบอกวันที่ของถึงจากความจำ — ต้องอ่านจาก ready_date ที่ check_order คืนมา"
      }
    ]
  },
  "catalog": [
    {
      "text_id": 9001,
      "_fine_state": "greet_pickup",
      "template": "สวัสดี{suffix} ร้านกาแฟโทรแจ้งคุณ [customer_name] ว่าออเดอร์ [order_no] พร้อมรับได้ตั้งแต่ [ready_date] ไม่ทราบว่าสะดวกมารับไหม{q_suffix}"
    },
    {
      "text_id": 9002,
      "_fine_state": "faq_hours",
      "template": "ร้านเปิดทุกวัน 08:00 ถึง 20:00 {suffix}"
    },
    {
      "text_id": 9003,
      "_fine_state": "faq_repeat",
      "template": "ขออภัย{suffix} ขออนุญาตแจ้งอีกครั้งนะ{suffix}"
    },
    {
      "text_id": 9004,
      "_fine_state": "close_thanks",
      "template": "ขอบคุณ{suffix} ทางร้านรอรับคุณ [customer_name] ในวัน [ready_date] {suffix}"
    },
    {
      "text_id": 9005,
      "_fine_state": "close_handoff",
      "template": "รับทราบ{suffix} เดี๋ยวพนักงานติดต่อกลับอีกครั้ง{suffix}"
    }
  ]
}
```

ตัวอย่างนี้ไม่มี `session_init` เนื่องจากยังไม่ได้เชื่อมต่อ API จริง ข้อมูล CRM จึงมาจาก `crm` ในไฟล์อัปโหลด

เมื่อเชื่อมต่อระบบจริงแล้ว ควรประกาศ `session_init` ตามขั้นที่ 6 และสามารถยกเลิกการใช้ `crm` สำหรับข้อมูลทดลองได้

ตัวอย่างนี้ยังไม่ได้กำหนดองค์ประกอบต่อไปนี้ เนื่องจากไม่จำเป็นสำหรับ flow ขั้นพื้นฐาน

* การยืนยันตัวตน (`verify_required` + `provides: "verified"`)
* chain state
* `when_event`
* `one_of_from`
* หลายสำนวนต่อ beat

องค์ประกอบเหล่านี้ควรเพิ่มเมื่อมีความต้องการใช้งานจริงตามข้อกำหนดของ flow

---

# 2. การใช้งาน Demo App

หลังจากจัดทำไฟล์ specification แล้ว การนำ specification เข้าสู่ระบบ การทดลองสนทนา การตรวจสอบ prompt การวิเคราะห์ผลลัพธ์ และการลบบริษัท สามารถดำเนินการผ่าน Demo App ได้ทั้งหมด โดยไม่จำเป็นต้องเข้าถึงไฟล์บนเซิร์ฟเวอร์

## การอัปโหลด Specification

```text
เปิด Demo App → Upload

① Download template
   ดาวน์โหลด JSON เปล่าที่มี key ครบตามรูปแบบที่ระบบรองรับ

② กรอกข้อมูล
   กรอกข้อมูลตาม specification หรือเริ่มจากตัวอย่างใน §1

③ Upload
   นำไฟล์ JSON เข้าสู่ระบบ
```

ระบบจะตรวจสอบ specification ตาม validation ที่กำหนด และแสดงข้อผิดพลาดเป็นรายการจนกว่าจะสามารถสร้างบริษัทได้สำเร็จ

เมื่อ validation ผ่าน ระบบจะเขียน specification ลงทะเบียนบริษัท และสร้างสายทดลองโดยไม่ต้อง restart ระบบ

บริษัทสามารถเลือกเพื่อเริ่มสนทนา และสามารถลบผ่าน UI เดียวกันได้

การลบผ่าน

```text
DELETE /api/flow/company/<CODE>
```

จะลบไฟล์ specification และสายทดลองของบริษัทนั้น

บริษัทที่มาพร้อมระบบก็สามารถลบได้เช่นเดียวกัน เนื่องจากแต่ละ Tenant มีไฟล์ specification เป็นของตนเองและไม่มี business logic ที่ใช้ร่วมกัน

---

## แหล่งข้อมูล CRM สำหรับสายทดลอง

ข้อมูลใน `crm` ของไฟล์อัปโหลดเป็นแหล่งข้อมูลสำหรับสายทดลอง

สายทดลองที่แอปสร้างขึ้น (`_demo_persona`) จะไม่มี CRM โดยอัตโนมัติ เนื่องจากข้อมูลลูกค้าเป็นข้อมูลของ Tenant

แหล่งข้อมูล CRM สำหรับสายทดลองมี 2 รูปแบบ

| แหล่งข้อมูล         | ใช้ในกรณี                                      |
| ------------------- | ---------------------------------------------- |
| `crm` ในไฟล์อัปโหลด | ยังไม่ได้เชื่อมต่อ CRM และต้องการทดลองผ่านเว็บ |
| `session_init`      | เชื่อมต่อ API จริงของ Tenant แล้ว              |

หากไม่มีทั้งสองแหล่งข้อมูล ประโยคที่อ้างถึง field จะไม่สามารถเติมค่าได้ และอาจปรากฏ `[customer_name]` ในข้อความที่ส่งออก

ระบบจะตรวจสอบกรณีดังกล่าวในขั้นตอนการอัปโหลดและรายงานผ่าน `unfillable_placeholders`

หากมี `session_init` แต่ API ไม่ตอบสนองหรือคืน `{}` ระบบจะใช้ `on_failure` ตามที่ specification กำหนด และจะไม่ใช้ข้อมูลเก่าที่อาจยังคงอยู่ในหน่วยความจำเพื่อเปิดสาย

---

# โครงสร้างหน้าจอ

Demo App แบ่งการทำงานหลักออกเป็น 3 ส่วน

```text
เลือกบริษัท → เลือกโหมด → Playground

บริษัทที่มีอยู่ในระบบ
+
ปุ่มสร้าง / ลบ

โหมด:
· Playground
· อ่าน instruction
```

### Playground

ใช้สำหรับทดลองสนทนากับ Agent ในลักษณะเดียวกับการโทรจริง

### อ่าน instruction

ใช้แสดง prompt ที่ Agent ได้รับ โดย prompt ถูกประกอบด้วยกลไกเดียวกับที่ session ใช้จริง และ render จาก specification ปัจจุบัน

จึงเหมาะสำหรับตรวจสอบว่า flow, rules และ catalog ถูกนำไปใช้ตามที่กำหนดหรือไม่

---

# วิธีสร้างบริษัท

มี 2 วิธีหลัก

|                         | **＋ New company**               | **New / Upload**                                          |
| ----------------------- | ------------------------------- | --------------------------------------------------------- |
| วิธีกรอก                | ฟอร์มแยกตาม beat                | ไฟล์ JSON ทั้งฉบับ                                        |
| Flow                    | ใช้ flow ตั้งต้นของ AEON remind | กำหนด flow เองทั้งหมด                                     |
| เพิ่ม beat              | ได้ โดยผูกกับ phase ที่เลือก    | ได้                                                       |
| tools / events / gating | ใช้ค่าตาม flow ตั้งต้น          | กำหนดเอง                                                  |
| เหมาะสำหรับ             | การปรับสำนวนบน flow เดิม        | flow ประเภทอื่น เช่น นัดหมาย เตือนคืนสินค้า หรือแจ้งเตือน |

หากงานของ Tenant ไม่ใช่งานติดตามหนี้ ควรใช้วิธี **Upload** เนื่องจากวิธีสร้างผ่านฟอร์มจะใช้โครงสร้าง flow ตั้งต้นของงานติดตามหนี้

---

# การตั้งค่าก่อนเริ่มสาย

| รายการ      | รายละเอียด                                                                   |
| ----------- | ---------------------------------------------------------------------------- |
| **Model**   | `Qwen` หรือ `Gemini`                                                         |
| **version** | checkpoint ของ vLLM ซึ่งแสดงเมื่อเลือก Qwen และมีการอัปเดตรายการทุก 8 วินาที |
| **Voice**   | `Female`, `Male` หรือ `Text`                                                 |
| **Start**   | เริ่มสาย โดยค่าการตั้งค่าจะถูกล็อกเมื่อเริ่มสาย                              |

`Text` ไม่สังเคราะห์เสียง จึงเหมาะสำหรับการทดสอบที่ต้องการลดเวลาในการตอบสนอง

เมื่อเริ่มสาย ตัวเลือก Model, version และ Voice จะถูกซ่อน และสามารถเปลี่ยนได้หลังจาก reset สายเท่านั้น

---

# การควบคุมระหว่างสนทนา

| การควบคุม | รายละเอียด                                                                |
| --------- | ------------------------------------------------------------------------- |
| ไมค์      | ปุ่มไมค์หรือ `Space` สำหรับเปิด/ปิดเสียง                                  |
| `Esc`     | ข้ามเสียงที่กำลังพูดหรือขัดจังหวะ                                         |
| `P`       | pause / resume                                                            |
| `R`       | reset สาย โดยมีหน้าจอยืนยัน                                               |
| `K`       | แสดง/ซ่อนแผงข้อมูลลูกค้า                                                  |
| `?`       | แสดงรายการ keyboard shortcuts                                             |
| Latency   | แสดง `VAD`, `STT`, `LLM`, `TTS` ต่อเทิร์น และรายละเอียดเพิ่มเติมเมื่อขยาย |
| Save      | บันทึกบทสนทนาเพื่อใช้เป็น replay/eval case                                |

หากไมโครโฟนถูกบล็อก ระบบจะแสดงวิธีเปิดสิทธิ์ตามเบราว์เซอร์ และมีปุ่ม **Type instead** สำหรับป้อนข้อความแทน

STT จะใช้ Chirp ผ่าน WebSocket เมื่อเบราว์เซอร์รองรับ หากไม่รองรับหรือไม่สามารถเชื่อมต่อได้ ระบบจะ fallback ไปยัง Web Speech API ของเบราว์เซอร์

`Save` จะบันทึกข้อมูลลง

```text
data/demo-saved-trajectory/<dd-mm-yy>/
```

โดยสามารถเพิ่มหมายเหตุ เช่น `"agent ข้าม KYC"` เพื่อใช้ในการประเมินหรือ replay ในภายหลัง

---

# การวิเคราะห์ข้อความในแต่ละเทิร์น

ทุกเหตุการณ์ภายในหนึ่งเทิร์นจะแสดงเป็นฟองข้อความตามลำดับที่เกิดขึ้นจริง

| ฟอง           | รายละเอียด                                          | จุดตรวจสอบ                                                           |
| ------------- | --------------------------------------------------- | -------------------------------------------------------------------- |
| `user`        | ข้อความที่ส่งเข้าระบบ พร้อมไอคอนไมค์เมื่อมาจากเสียง | ตรวจสอบความถูกต้องของ ASR                                            |
| `reply`       | ประโยคที่ Agent พูด พร้อม `text_id`                 | ตรวจสอบการเลือกสำนวนและ placeholder                                  |
| `tool_call`   | ชื่อ tool และ arguments                             | ตรวจสอบ tool ที่เรียกและค่าที่ส่ง                                    |
| `tool_result` | ผลลัพธ์จาก tool และ JSON เมื่อขยาย                  | ตรวจสอบ response และข้อมูลที่ merge เข้า `customer_data`             |
| `warning`     | การแจ้งเตือนจาก gate                                | ตรวจสอบ `missing_tools`, `missing_beats`, `empty_slots` และปัญหาอื่น |

`warning` และ `tool_result` เป็นข้อมูลสำคัญสำหรับการวิเคราะห์ปัญหา

เมื่อ gate ปฏิเสธคำขอ ระบบจะไม่ส่งข้อความที่ถูกปฏิเสธไปยังผู้รับสาย แต่จะให้โมเดลดำเนินการใหม่ภายในเทิร์นเดิม

ดังนั้นควรตรวจสอบทั้ง `warning` และ `tool_result` เพื่อระบุว่าระบบมีการปฏิเสธกี่ครั้งและเกิดจากสาเหตุใด

---

# CRM Snapshot

แผงข้อมูลลูกค้าด้านซ้ายจะแสดงข้อมูลตาม `crm_fields` และ label จาก `crm_labels`

หาก field เป็นค่าว่างหรือแสดงเป็นขีด หมายความว่าโมเดลไม่มีข้อมูลของ field นั้น

ก่อนเริ่มสายสามารถเปิดแผงดังกล่าวเพื่อเปลี่ยน persona ของบริษัทได้ โดยจะแสดงเฉพาะข้อมูลของบริษัทที่เลือก

---

# ตารางวิเคราะห์ปัญหา

| อาการ                           | ความหมาย                                                       | จุดที่ควรแก้ไข                                   |
| ------------------------------- | -------------------------------------------------------------- | ------------------------------------------------ |
| `[customer_name]` ปรากฏในประโยค | ไม่มี field ดังกล่าวอยู่ใน `customer_data`                     | `crm` หรือ `session_init`                        |
| `ยังไม่มีค่าให้พูด: …`          | field มีอยู่แต่ไม่มีค่า                                        | ตรวจสอบว่า tool ได้บันทึกค่าที่ลูกค้าระบุหรือไม่ |
| `missing_required_tools`        | beat อยู่ใน state ที่ `entry_tools` ไม่ครบ                     | `entry_tools` ของ state ที่เป็นเจ้าของ beat      |
| `incomplete_chain`              | chain state พูด template ไม่ครบ                                | ตรวจสอบ `templates` และ `when_event`             |
| `unknown_text_id`               | โมเดลอ้าง `text_id` ที่ไม่มีใน catalog                         | ตรวจสอบ rule หรือ hint ที่อ้างเลขโดยตรง          |
| `value_not_offered`             | argument ไม่ตรงกับค่าที่ระบบเสนอ                               | ตรวจสอบ `one_of_from` และ `gating`               |
| `verify_required`               | beat ที่ต้องยืนยันตัวตนถูกพูดก่อน verification                 | เพิ่ม tool ที่มี `provides: "verified"`          |
| วันที่ไม่ถูกต้อง                | โมเดลคำนวณวันที่เอง                                            | ให้ tool เป็นผู้คืนวันที่                        |
| ประโยคเดิมถูกพูดซ้ำ             | ไม่มีกฎป้องกันการพูดซ้ำ                                        | เพิ่ม `once_per_call`                            |
| การตอบช้าหรือเงียบ              | ระบบอาจวนผ่าน rejection หลายรอบจนถึง `FLOW_MAX_TOOL_LOOPS = 8` | ตรวจสอบ `warning` และสาเหตุของ rejection         |

รหัสที่ปรากฏจริงในฟอง `warning` และ `tool_result` เป็นสิ่งที่ควรใช้เป็นหลัก และตารางด้านบนครอบรหัสที่พบบ่อยไว้แล้ว

รายการรหัสทั้งหมด (ก่อนเรียก tool และระหว่างเรียก tool) อ่านได้จาก source code ด้วยคำสั่งต่อไปนี้ ซึ่งเป็นคำสั่งของฝั่งที่ดูแลระบบ ไม่ใช่ของ Tenant

```bash
PYTHONPATH=. python3 tools/list_reject_codes.py
```

---

# จุดตรวจสอบของระบบ

ควรตรวจสอบระบบใน 4 ขั้นตอนต่อไปนี้

| ขั้นตอน            | สิ่งที่ระบบตรวจสอบ                                       |
| ------------------ | -------------------------------------------------------- |
| ① Upload           | key, beat, event, state และ `to` ที่อ้างถึงต้องถูกต้อง   |
| ② หลัง Upload      | `unfillable_placeholders` แสดง field ที่ไม่มีแหล่งข้อมูล |
| ③ อ่าน instruction | ตรวจสอบ prompt ฉบับที่โมเดลได้รับจริง                    |
| ④ ระหว่างสนทนา     | ตรวจสอบ rejection, warning และผลลัพธ์จาก endpoint        |

การมี `unfillable_placeholders` ไม่ได้ทำให้การสร้างบริษัทล้มเหลวเสมอไป แต่ควรแก้ไขก่อนเริ่มการสนทนาจริง เพื่อป้องกัน placeholder ปรากฏต่อผู้รับสาย

---

# การทดสอบ Flow

ควรทดสอบอย่างน้อย 4 เส้นทาง

```text
① เส้นทางปกติ
   ลูกค้าตอบรับ → ปิดสาย → บันทึกผลสำเร็จ

② การเลื่อนหรือเจรจา
   ลูกค้าไม่ตอบรับทันทีและเข้าสู่เส้นทางทางเลือก

③ การปฏิเสธ
   ลูกค้าปฏิเสธและ flow สามารถสิ้นสุดได้โดยไม่เกิด loop

④ คำถามแทรกระหว่างสาย
   ตอบคำถาม → กลับเข้าสู่ state เดิมผ่าน faq_routing
```

สำหรับทุกเส้นทางควรตรวจสอบ 3 ประเด็น

1. Agent พูดถูกจังหวะหรือไม่
2. มี placeholder ปรากฏในข้อความหรือไม่
3. ผลลัพธ์ที่บันทึกตรงกับสิ่งที่เกิดขึ้นในการสนทนาหรือไม่

---

# การตรวจสอบ Endpoint ก่อนทดสอบผ่านเว็บ

เนื่องจากทุก tool เป็น endpoint ของ Tenant ควรทดสอบ endpoint โดยตรงก่อนนำ specification ไปทดสอบใน Demo App เพื่อแยกปัญหาระหว่าง specification และ endpoint

ตัวอย่าง:

```bash
curl -sS -X POST https://api.yourcompany.co.th/aax/record_call_result \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"tool":"record_call_result",
       "args":{"result":"confirmed"},
       "ref":{"msisdn":"081-234-5678"}}'
```

ควรตรวจสอบอย่างน้อย 3 ประเด็น

1. Response เป็น JSON object หรือไม่
2. Field ที่คืนมาตรงกับ `returns` หรือไม่
3. Response ใช้เวลาน้อยกว่า `timeout` หรือไม่

เมื่อทดสอบในเว็บ ให้ขยาย `tool_result` แล้วเปรียบเทียบกับผลจาก `curl`

หากผลลัพธ์แตกต่างกัน ควรตรวจสอบ `headers`, `body` และ `url` ใน specification ก่อนพิจารณาว่า flow มีปัญหา

กรณี endpoint ตอบ `200` เป็น plain text เช่น `OK` ระบบจะถือว่า request สำเร็จ แม้จะไม่มีข้อมูลให้ข้อความถัดไปอ้างอิง จึงควรตรวจสอบ response โดยตรงด้วย `curl`

---

# ขอบเขตของการตรวจสอบผ่านเบราว์เซอร์

`/api/health` สามารถตอบ `ok` ได้แม้ว่าโมเดลปลายทางจะไม่พร้อมใช้งาน ดังนั้นการตรวจสอบ health endpoint เพียงอย่างเดียวไม่เพียงพอ

ควรทดสอบการสนทนาจริงอย่างน้อยหนึ่งเทิร์น

หากระบบไม่ตอบสนองหรือเกิดข้อผิดพลาดของ stream ในระหว่างการสนทนา ปัญหาดังกล่าวอยู่ในส่วนของระบบที่ให้บริการโมเดล ไม่ใช่ validation ของ Tenant specification
