# อ่านโค้ด demo_v2

เอกสารนี้ไม่ใช่ API reference — เป็นแผนที่สำหรับคนที่เพิ่งเปิดโปรเจกต์
และอยากรู้ว่า **จะเริ่มอ่านตรงไหน แล้วเรื่องหนึ่งเรื่องวิ่งผ่านไฟล์ไหนบ้าง**

รูปแบบไฟล์ผู้เช่าอยู่ที่ [SPEC_LOCKED.md](SPEC_LOCKED.md) ·
วิธีสร้าง flow ใหม่อยู่ที่ [FLOW_WALKTHROUGH.md](FLOW_WALKTHROUGH.md)

---

## อ่านแค่ 4 ไฟล์ก็เข้าใจทั้งระบบ

```
demo_v2/server/
  app.py               567   HTTP + streaming — บางมาก แทบไม่มี logic
  sessions.py        2,115   หัวใจ — FlowLiveSession อยู่ที่นี่
  flow/flowspec.py     672   โหลด + ตรวจ spec, สร้าง tool schema
  flow/spec_backend.py 270   ยิง tool จริง

  flow/spec_gate.py    194   ← อ่านตอนสนใจว่าอะไรกันการเขียนผิด
  flow/flowspec_render.py 317 ← อ่านตอนสนใจว่า prompt หน้าตายังไง
  flow/session_init.py 232   ← อ่านตอนสนใจว่า CRM มาจากไหน

  tts.py · stt_ws.py   739   เสียง — แยกขาดจาก logic ของสาย
lib/                   699   prescript (เติม slot) · datetime_utils — vendored
services/speech/     1,030   TTS/STT engine
```

**80% ของสิ่งที่ต้องเข้าใจอยู่ใน `sessions.py`** ที่เหลือเป็นบริการรอบข้าง

---

## เส้นทางของหนึ่งสาย

### เปิด session — `app.py:323 → sessions.build() → FlowLiveSession.__init__`

`__init__` ยาว ~230 บรรทัด ทำ 6 อย่างตามลำดับ อ่านไล่จากบนลงล่างได้เลย

```
1  โหลด spec ของบริษัท          _flow_spec_path() → load_tenant_spec()
2  โหลด catalog                 _read_catalog()  — inline อยู่ใน spec
3  ยิง CRM                      session_init.fetch_context()          :1108
                                → merge ลง self.customer_data
                                → ล้ม + มี on_failure = พูดแล้วปิดสาย
                                → ล้ม + ไม่มี on_failure = ไม่เปิด session
4  สร้าง system prompt          render_instruction(spec)
                                + build_script_catalog(catalog)
                                + เติมค่า CRM ลงไป                     :1165
5  สร้าง backend                SpecBackend(customer_data, spec)
6  สร้าง tool schema            build_tool_schemas(spec)
```

จบขั้นนี้ `self._messages` มี system message หนึ่งอัน พร้อมคุย

> `instruction_version` ที่ยังรับเป็นพารามิเตอร์อยู่ **ถูกเพิกเฉย** — ไฟล์
> `{stem}__{version}.json` ไม่มีแล้ว หนึ่งบริษัท = หนึ่งไฟล์

### ประโยคแรก — `_greeting_hops()` `:1373`

**ไม่ได้เรียกโมเดล** แอปหยิบ `initial: true` state → beat แรก → ประโยคจาก catalog
แล้วยัดเข้า `self._messages` เป็นเทิร์นของ assistant
เพื่อให้ลำดับ greeting → customer → agent ตรงกับตอนเทรน

### เทิร์นถัดไป — `_aiter_run()` `:1472`

นี่คือฟังก์ชันที่ต้องอ่านให้เข้าใจ ทุกอย่างเกิดในนี้

```python
for _loop in range(FLOW_MAX_TOOL_LOOPS):        # :1511  (= 8)
    resp = _flow_vllm_chat(...)                 # :1010  ยิง vLLM
    for tc in tool_calls:
        if tc.name == "reply":
            ids, text, dyn = self._render_reply(args)   # :1392  เติม slot
            # ── ด่านที่ 1 ────────────────────────────
            #   empty_slot · date_format_invalid
            #   missing_required_tools            :1592
            #   incomplete_chain                  :1720
            #   too_many_beats
            # ไม่ผ่าน → push tool_result กลับเข้า messages → continue
            push({"kind": "reply", ...})         # ผ่าน → ลูกค้าได้ยิน
        else:
            result = self._backend.dispatch(...)  # :1795  ── ด่านที่ 2 อยู่ในนี้
            push({"kind": "tool_result", ...})

if not agent_text:                               # :1844
    ids, text = self._fallback_reply()           # ลูปหมดแล้วยังไม่พูด → ห้ามเงียบ
```

**สิ่งที่ต้องเข้าใจข้อเดียว:** คำปฏิเสธไม่ได้ throw และไม่ได้ break —
มันถูก append เข้า `self._messages` ในรูปเดียวกับ tool result ปกติ
โมเดลจึงเห็นมันในรอบถัดไปของ `for _loop` เดียวกัน แล้วแก้เอง

---

## โครงข้อมูล 3 อย่าง

### 1 · spec — dict ที่โหลดจาก `<CODE>.company.json`

ไม่มี class ไม่มี dataclass — เป็น dict ดิบ อ่านด้วย `.get()` ทั้งระบบ
ตัวที่แตะบ่อย: `spec["states"]` · `spec["catalog"]` · `spec["tools"]["declarations"]`

`load_tenant_spec(path)` เติม `company`/`flow_id` จากชื่อไฟล์ให้ (`flowspec.py`)

### 2 · catalog — list ของประโยค

```python
{"text_id": 9004, "_fine_state": "confirm_new_date", "template": "รับทราบค่ะ ... [new_date] ..."}
```

โมเดลอ้างด้วย `text_id` · โค้ดอ้างด้วย `_fine_state` · `normalize_catalog()` เติม
field ที่อนุมานได้ (`state`, `category`) ให้ตอนโหลด

### 3 · hop — สิ่งที่ stream ออกไปหา UI

มี 4 ชนิดเท่านั้น

```python
{"kind": "reply",       "text": ..., "text_ids": [...], "dynamic_vars": {...}}
{"kind": "tool_call",   "name": ..., "args": {...}}
{"kind": "tool_result", "name": ..., "result": {...}}
{"kind": "warning",     "text": ...}
```

`app.py:172 _stream_turn` แปลงเป็น NDJSON บรรทัดละ hop ·
`frontend/src/components/Bubble.tsx` เป็นตัวเดียวที่ตัดสินว่าแต่ละชนิดวาดยังไง

เพิ่ม hop ชนิดใหม่ = ต้องแก้สองที่นี้เสมอ

---

## `flow/` แต่ละไฟล์เป็นเจ้าของอะไร

| ไฟล์ | เป็นเจ้าของ | จุดเข้า |
|---|---|---|
| `flowspec.py` | **รูปแบบ** — key ที่อนุญาต, validate, สร้าง tool schema | `load_tenant_spec` · `validate_strict` · `build_tool_schemas` |
| `flowspec_render.py` | **prompt** — spec → คำสั่งภาษาไทยที่โมเดลอ่าน | `render_instruction(spec)` |
| `spec_backend.py` | **การเรียก tool** — ตรวจ args แล้วยิง HTTP | `dispatch(name, args)` |
| `spec_gate.py` | **ด่านที่ 2** — นับ/เรียงลำดับ/เทียบค่า | `check(name, args, call_log)` |
| `session_init.py` | **CRM** — ยิงครั้งเดียวตอนเปิดสาย, แทน token, flatten | `fetch_context(spec, seed)` |

`spec_gate.check()` ถูกเรียกจาก `spec_backend.dispatch()` — ไม่ได้ถูกเรียกตรงจากที่อื่น

---

## invariant ที่โค้ดถือไว้ให้ (ไม่มีเขียนไว้ที่ไหน อ่านโค้ดอย่างเดียวอาจพลาด)

**หนึ่ง state = หนึ่งเทิร์น** — `templates` ทุกตัวใน state เดียวกันต้องพูดในเทิร์นเดียว
ยกเว้นมี `when_event` ซึ่งแปลว่าเป็นทางเลือก · `is_chain_state()` ใน `flowspec.py` คือนิยามเดียวของเรื่องนี้
ทั้ง prompt และด่านตรวจอ่านจากฟังก์ชันเดียวกัน

**`entry_tools` รันก่อนพูด** — ไม่ใช่ตอนเข้า state แต่ตอน *กำลังจะพูด beat ที่สังกัด state นั้น*
เพราะแอปไม่ได้ติดตาม state ปัจจุบัน — มันย้อนจาก beat ที่โมเดลเลือกกลับไปหา state

**`customer_data` เป็น dict เดียวที่ share กัน** — `FlowLiveSession` กับ `SpecBackend`
ถือ reference เดียวกัน ไม่ได้ copy ผลของ tool ที่ `_merge_context()` เขียนลงไป
จึงไปโผล่ในประโยคถัดไปทันที **ถ้าเผลอ copy จะพังเงียบ**

**ปฏิเสธแล้วไม่ break** — ทั้งสองด่าน append เข้า `_messages` แล้ว `continue`
ไม่มี exception ไม่มี early return

**เพดานต่างกัน** — ด่านที่ 1 นับ `_step_nudges < 2` แล้วปล่อยผ่าน (ห้ามค้างสาย)
ด่านที่ 2 ไม่มีเพดาน (เขียนผิดแล้วเอาคืนไม่ได้)

---

## อยากแก้เรื่องนี้ ไปที่ไหน

| อยากทำอะไร | ไฟล์ |
|---|---|
| เพิ่ม/แก้ key ใน spec | `flow/flowspec.py` — `TOP_KEYS`, `STATE_KEYS`, … แล้ว `validate_strict` |
| เปลี่ยนหน้าตา prompt | `flow/flowspec_render.py` — `render_instruction()` |
| เพิ่มด่านตอนเรียก tool | `flow/spec_gate.py` — `check()` |
| เพิ่มด่านก่อนพูด | `sessions.py` `_aiter_run` ช่วง `:1560–1760` |
| เปลี่ยนวิธียิง LLM | `sessions.py` `_flow_vllm_chat()` `:1010` |
| เพิ่ม endpoint | `app.py` แล้วบอก `frontend/src/api.ts` |
| เปลี่ยนการเติม slot | `lib/prescript.py` — `fill_template()` |
| เรื่องเสียง | `tts.py` · `stt_ws.py` · `services/speech/` |

---

## ที่ไม่ต้องอ่าน

```
services/speech/*      engine ของ STT/TTS — แตะเฉพาะตอนแก้เรื่องเสียง
lib/datetime_utils.py  แปลงวันไทย/canonical
frontend/src/data/     ข้อมูลนิ่ง
```

และของที่ **ไม่มีแล้ว** ใน demo_v2 — ถ้าเจอชื่อพวกนี้ในเอกสารเก่า แปลว่าเอกสารนั้นเก่ากว่าโค้ด

```
ReplaySession · LiveSession · flow/*_v12.py · _is_v12 · demo/server/prescript.py
```

---

## รันในเครื่อง

```bash
PYTHONPATH=. uvicorn demo_v2.server.app:app --host 127.0.0.1 --port 4100
```

ต้องมี `AAX6_VLLM_BASE_URLS` (โมเดล) และ `AAX6_API_BASE` (mock CRM)
ส่วนเสียงต้องมี `GOOGLE_APPLICATION_CREDENTIALS` — ไม่มีก็รันได้ แค่ไม่มีเสียง

ยืนยันว่าไม่พังหลังแก้:

```bash
PYTHONPATH=. python3 tools/eval_demo.py --model grpo400 --out /tmp/x.json
```

⚠️ รันครั้งเดียวไม่พอ — config เดียวกันเคยแกว่ง 6 จาก 45 ให้รัน ≥3 ครั้งแล้วดูช่วง
