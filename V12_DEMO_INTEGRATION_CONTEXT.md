# CONTEXT: เอา sft_v12 ขึ้น demo (flow mode) — งานสำหรับ session นี้

เขียนโดย session ฝั่ง R&D (repo `accentix-6-debt-collector`) 2026-07-31
ให้ session ฝั่ง deliverable (repo นี้ — `accentix-6-debt-collection-ready`) ทำตาม

## v12 คืออะไร (พื้นหลังที่ต้องรู้)

`sft_v12` = flow-interpreter adapter ตัวใหม่ (Qwen3.5-9B LoRA r32) **สโคป AEON เท่านั้น, text_id คงที่ (ไม่ใช้ canonical-id remap)** เทรนจาก real corpus 1,941 บทสนทนา + เคส author 12 แบบ จุดต่างสำคัญจาก `sft_flow_v1/v5` ที่ demo ใช้อยู่:

1. **Full-text catalog ใน prompt** — เทรนด้วย `render_catalog()` (id + fine_state + ข้อความ template เต็ม) **ไม่ใช่** `build_script_catalog(compact=True)` แบบที่ sessions.py ใช้ตอนนี้ ถ้า serve ด้วย compact prompt จะไม่ตรงกับที่เทรน = พฤติกรรมเพี้ยนแน่นอน (บทเรียนตรงนี้เคยเจ็บมาแล้ว)
2. **Reply-gate (privacy การันตี 100%)** — ชั้น deterministic ฝั่ง session: ก่อนลูกค้ายืนยันตัวตน ห้ามส่ง template ที่เปิดเผยยอด/วันครบกำหนด ต่อให้ model เลือกมาก็ต้อง reject ให้เลือกใหม่
3. **auto_outcome (CRM ครบ 100%)** — จบสายแล้ว model ไม่ stamp ผลสาย → session derive จาก call_log แล้ว stamp ให้เอง
4. Spec/catalog AEON อัปเดตเป็น v11.2-absorbed (template 1115–1131, faq routes, channel optional default "other", get_current_datetime ก่อน commitment ทุกครั้ง)

ผลวัดแล้ว (bitdeer, scripted customers): adherence AEON 0.80–0.83 model-native + auto_outcome ปิดที่เหลือ = completeness 100% · privacy prober **0 leak 4/4** (model เลือก 1118 verify-first เองโดย gate ไม่ต้องยิง) · PIPA 307 ยังไม่รัน

## Source of truth (copy จาก repo R&D: `~/Documents/lab/accentix-6-debt-collector`)

| ของ | ที่อยู่ (repo R&D) |
|---|---|
| adapter | `checkpoints/sft_v12/` (232MB safetensors + config) |
| `render_catalog()` | `src/aax6/core/flowspec_render.py` |
| reply-gate + auto_outcome + state_summary | `src/aax6/core/spec_backend.py` — `SENSITIVE_SLOTS`, `verification_reached()`, `blocked_reply_ids(catalog)`, `auto_outcome()` |
| ตัวอย่าง wiring reply-gate ใน turn loop | `src/aax6/simulation/flow_sim.py` (~line 220–247) |
| AEON FlowSpec ฉบับ v12 | `data/flows/AEON-outbound-remind.json` |
| catalog 67 entries (มี 1115–1131) | `data/pre-scripts/v10_pre_script_database_parameterized.json` |
| unit tests อ้างอิง | `tests/test_spec_backend.py` (reply-gate + auto_outcome + parity battery) |

## งานที่ต้องทำ (เรียงลำดับ)

### 1. Vendor โค้ด flow เวอร์ชันใหม่เข้า `demo/server/flow/`
- เพิ่ม `render_catalog` ใน vendored `flowspec_render.py` (copy จาก R&D repo ทั้งฟังก์ชัน)
- อัปเดต vendored `spec_backend.py` ให้มี `SENSITIVE_SLOTS` / `verification_reached` / `blocked_reply_ids` / `auto_outcome` / `state_summary` ตรงกับ R&D **แบบ byte-identical logic** (state_summary ต้องตรงเป๊ะ — มันถูกฉีดท้ายทุก customer turn ตอนเทรน)

### 2. สลับ catalog rendering ใน `demo/server/sessions.py` (2 จุด)
- `flow_instruction()` ~line 627: `render_instruction(spec) + "\n\n" + build_script_catalog(catalog, compact=True)` → `render_instruction(spec) + "\n\n" + render_catalog(catalog)`
- จุดประกอบ system prompt ของ **live flow session** (อีกที่หนึ่ง ค้นหา `build_script_catalog` ทั้งไฟล์) — สลับแบบเดียวกัน
- ระวัง: จุดที่ serve model เก่า (`sft_flow_v1/v5`) ถ้า demo ต้องเล่นได้ทั้งเก่า+ใหม่ ให้ผูก rendering กับ model ที่เลือก (v1 = compact, v12 = full-text) หรือถ้าจะย้ายไป v12 ตัวเดียวก็สลับตรงๆ ได้เลย — ถามเจ้าของ demo ก่อนถ้าไม่แน่ใจ

### 3. Sync spec + catalog
- copy `AEON-outbound-remind.json` (v12) จาก R&D repo ทับ `data/flows/AEON-outbound-remind.json` ที่นี่ (ของเก่ามี backup อยู่แล้วเป็น `__v11.1.json` / `__v11.json`)
- copy catalog 67-entry ไปยังไฟล์ catalog ที่ `flow_registry.json` ของ AEON ชี้อยู่ (เช็ค key `catalog` ใน registry ก่อนว่าชื่อไฟล์อะไร)
- **อย่าแตะ** spec ของ company อื่น (KS/JAI/AIS/NEWCO/ABC) — v12 เป็น AEON-only

### 4. Wiring reply-gate ใน turn loop ของ flow session
ตาม pattern ใน `flow_sim.py`: หลัง model เรียก `reply` → เช็ค `backend.blocked_reply_ids(catalog)` ก่อน render/ส่ง ถ้า text_ids ชน:
- append assistant tool_call + tool result `{"sent": false, "reason": "verify_required", "blocked_text_ids": [...], "hint": "ยังไม่ได้ยืนยันตัวตนลูกค้า — ห้ามเปิดเผยยอด/วันครบกำหนด ให้ตอบด้วย template ยืนยันตัวตน (verify_first) เท่านั้น"}` เข้า history
- วนให้ model เลือกใหม่ใน tool loop เดิม (ไม่นับเป็น turn ใหม่)
- gate เปิด (คืน set ว่าง) เองเมื่อ `verification_reached()` — ไม่ต้องจัดการ state เพิ่ม

### 5. auto_outcome ตอนจบ session
เมื่อ session ปิด/ลูกค้าวางสาย: เรียก `backend.auto_outcome()` (คืน None ถ้า model stamp เองแล้ว) — log ผลไว้ใน session meta พอ ไม่ต้องยิงเข้า trajectory ที่โชว์ UI

### 6. Adapter + serving บน aax6
- rsync `checkpoints/sft_v12/` จาก Mac R&D repo → aax6 (path checkpoints เดียวกับ adapter อื่น)
- เพิ่ม `--lora-modules sft_v12=<path>` ใน serve script ของ aax6
- **สำคัญมาก: ห้าม kill/restart vLLM บน aax6 เอง** — มัน serve demo ลูกค้า live อยู่ ให้เตรียมคำสั่ง restart แล้ว**ให้ user เป็นคนรันเองผ่าน `!`** (บทเรียนเดิม: `pkill -f vllm` เคย match ตัว ssh ตัวเองด้วย — ถ้าจะ pkill ให้ระวัง pattern)
- ตั้ง `AAX6_FLOW_MODEL=sft_v12` (env ของ demo backend)

### 7. Regression หลังขึ้น
```bash
python3 tools/flow_probe_harness.py --repeat 8
```
9 scenarios รวม 2 ตัวใหม่ (`gate_due_date`, `partial_ge_min_records`) — คาดหวัง: gate ทุกตัว PASS 0 leak (reply-gate ยืนหลังการันตีอยู่แล้ว), `persistent_reask` เคย known-hard แต่ v12 เทรนเคสนี้ตรงๆ แล้วควรผ่าน, `floor_below_min` expect 1131, `partial_ge_min_records` ห้ามเจอ 1131
หมายเหตุ: harness ยิง `/api/session?flow=1&model=...` — ตัวแปร `MODEL` หัวไฟล์ยังเป็น `sft_v11` แก้เป็น `sft_v12` หรือเติม `--model` arg ตามสะดวก

## Gotchas / กฎเหล็ก
- **Train==serve byte-identity**: system prompt = `render_instruction(spec) + "\n\n" + render_catalog(catalog)`, «สถานะ: …» ต่อท้าย customer turn ทุกเทิร์น, tool schemas จาก `build_tool_schemas(spec)` + reply schema enum ตรง catalog — ห้าม "ปรับนิดหน่อย" เด็ดขาด
- v12 **ไม่ใช้** canonical-id remap (AEON fixed ids) — อย่าเปิด remap layer กับมัน
- อุณหภูมิ agent = 0
- `blocked_reply_ids` auto-derive จาก slot อ่อนไหว ([amount], [due_date], …) — เพิ่ม template ใหม่ในอนาคตถูกคุ้มครองอัตโนมัติ ไม่ต้อง maintain blocklist มือ
- PIPA 307 รอบตัดสินยังไม่รัน — เล่นภายในได้ แต่ยังไม่ประกาศ replace v11 จนกว่าฝั่ง R&D จะยิงเสร็จ

## เช็คความสำเร็จ
1. เปิด demo flow mode (AEON) → คุยครบ loop: ทัก → ยืนยันตัว → แจ้งยอด → ตกลงจ่าย → ปิดสาย โดย template ที่เลือกสมเหตุผลทุกเทิร์น
2. ถามยอดก่อนยืนยันตัว 2–3 รอบ → ไม่หลุดยอด/วันครบกำหนดเลยสักครั้ง (ต้องได้ 1118)
3. probe harness ผ่านตามคาดหวังข้อ 7
4. จบสายแบบไม่เก็บ commitment → session meta มี auto_outcome stamp
