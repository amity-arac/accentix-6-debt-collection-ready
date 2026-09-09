# การเสิร์ฟโมเดลให้ Demo App

*(คู่มือการเขียนสเปค: [MANUAL.md](MANUAL.md) · รูปแบบไฟล์: [SPEC_LOCKED.md](SPEC_LOCKED.md))*

แอปไม่ได้เรียก API ของผู้ให้บริการภายนอก แต่คุยกับ **vLLM** ที่ติดตั้งบนเครื่องที่มี GPU
ผ่านโพรโทคอลแบบเดียวกับ OpenAI ⇒ ก่อนเปิดแอปต้องมีโมเดลเสิร์ฟอยู่ก่อนเสมอ

```
เครื่อง GPU                              แอป
┌──────────────────────┐                ┌──────────────────────┐
│ vLLM  :8001          │◀───────────────│ AAX6_VLLM_BASE_URL   │
│   <NAME> (9B)        │   /v1/…        │ demo_v2.server.app   │
└──────────────────────┘                └──────────────────────┘
```

---

# 1 · ของที่ต้องมี

| | |
| --- | --- |
| GPU | A100 40GB ขึ้นไป · โมเดล 9B แบบ bfloat16 ใช้ราว 18 GB ที่เหลือเป็น KV cache |
| น้ำหนักโมเดล | ไดเรกทอรีของ checkpoint หรือ LoRA adapter ที่ได้รับมา — ดูข้อ 3 ว่าเป็นรูปไหนและต้องทำอะไร |
| environment | Python 3.11 พร้อม vLLM ตามรายการเวอร์ชันในข้อ 2 |

ความจุที่วัดได้จริงต่อการ์ดหนึ่งใบ อยู่ในตาราง Capacity ของ [README.md](README.md)

---

# 2 · ติดตั้ง model runner

โมเดลเป็นสถาปัตยกรรม **Qwen3.5 (gated deltanet แบบลูกผสม)** ซึ่งเพิ่งรองรับใน
transformers รุ่นใหม่ ⇒ ชุดเวอร์ชันต่อไปนี้ไม่ใช่คำแนะนำ แต่เป็นเงื่อนไข

```bash
python3 -m venv /workspace/aax6/env
/workspace/aax6/env/bin/pip install "torch==2.10.0" "vllm==0.19.0"

# ต้องดันกลับด้วย --no-deps หลังลง vLLM เสมอ — อ่านคำอธิบายข้างล่างก่อนข้ามขั้นนี้
/workspace/aax6/env/bin/pip install --no-deps \
    "transformers==5.5.0" "huggingface_hub==1.26.1" "tokenizers==0.22.2"
```

### กับดักที่ต้องรู้ก่อน

**`pip install vllm` จะ downgrade `transformers` เป็น 4.57.6** ซึ่งอ่านไฟล์ config ของ
Qwen3.5 ผิดสถาปัตยกรรม (ตีเป็น `qwen3_next` ซึ่งเป็น MoE คนละตัว) ผลคือ **โมเดลขึ้นได้
ตามปกติ ไม่มี error แต่พ่นข้อความที่ไม่มีความหมายออกมา** จึงต้องดัน transformers 5.5.0
กลับทุกครั้ง และตรวจก่อนเสิร์ฟ

```bash
/workspace/aax6/env/bin/python -c "
import torch, vllm, transformers, huggingface_hub as h
print(f'torch {torch.__version__} · vllm {vllm.__version__} · tf {transformers.__version__} · hub {h.__version__}')
assert transformers.__version__.startswith('5.5'), 'transformers ถูก downgrade — โมเดลจะโหลดผิดสถาปัตยกรรม'
"
```

ชุดที่ยืนยันแล้วว่าใช้ได้ — `torch 2.10.0+cu128` · `vllm 0.18.1` หรือ `0.19.x` ·
`transformers 5.5.0` · `huggingface_hub 1.26.1` · `tokenizers 0.22.2`

**เรียก binary ตรง ๆ อย่าพึ่ง shell hook ของ conda** — `serve_aax6.sh` ตั้ง
`LD_LIBRARY_PATH` ให้ชี้ `lib` ของ environment เอง เพราะ `libstdc++` ของระบบมักเก่ากว่า
ที่ส่วนขยาย CUDA ต้องการ (`CXXABI_1.3.15 not found`)

---

# 3 · น้ำหนักโมเดลมาจากไหน

ชุดส่งมอบมีน้ำหนักได้ **สองรูป** และวิธีเสิร์ฟไม่เหมือนกัน ตรวจก่อนว่าที่ได้มาเป็นรูปไหน

```bash
ls <ไดเรกทอรีที่ได้มา>
```

| เห็นอะไร | เป็นรูปไหน | ต้องทำอะไร |
| --- | --- | --- |
| `config.json` + `model-*.safetensors` หลายไฟล์ (รวม ~18 GB) | checkpoint เต็ม | เสิร์ฟได้เลย → ข้อ 4 |
| `adapter_config.json` + `adapter_model.safetensors` (~19 MB) | LoRA adapter | ต้องมี base model ก่อน → ข้อ 3.2 |

`checkpoints/` ใน repo เป็น **adapter ทั้งหมด** (r=32 · α=64 · base `Qwen/Qwen3.5-9B`)
ไฟล์เก็บด้วย git-LFS ⇒ หลัง clone ต้องดึงของจริงลงมาก่อน

```bash
git lfs pull
```

ไม่ทำขั้นนี้จะได้ไฟล์ตัวชี้ขนาดไม่กี่ร้อยไบต์ แล้ว vLLM ล้มตอนโหลด

## 3.1 · checkpoint เต็ม (ส่งมอบแยกจาก repo)

น้ำหนักไม่ได้อยู่ใน git เพราะขนาดเกินไป — โอนแยกทางที่ตกลงกัน · **ตรวจความครบก่อนใช้เสมอ**

```bash
sha256sum -c checksums.txt          # หรือเทียบกับต้นทางทีละไฟล์
du -sh <dir>                        # ต้องใกล้เคียงกับที่ต้นทางแจ้ง
```

เคยมีกรณีที่ไฟล์โอนมาไม่ครบ (sparse file: `du` แสดง 12 GB แต่ `ls` แสดง 18.8 GB) แล้วโมเดล
พ่นข้อความไม่มีความหมาย — และถูกสรุปผิดว่าเป็นปัญหาของ vLLM รุ่นนั้น ⇒ เทียบ checksum
ก่อนโทษ runtime ทุกครั้ง

## 3.2 · adapter + base จาก Hugging Face

base model ไม่ได้อยู่ในชุดส่งมอบ (~18 GB) ต้องดาวน์โหลดเอง

```bash
export HF_HOME=/workspace/hf            # ให้แคชอยู่บนดิสก์ที่มีที่ว่างพอ
/workspace/aax6/env/bin/python -c "from huggingface_hub import snapshot_download; \
    snapshot_download('Qwen/Qwen3.5-9B', local_dir='/workspace/models/Qwen3.5-9B', max_workers=8)"
```

ถ้าเข้า `huggingface.co` ไม่ได้ ต้องขอ base model มาแบบ offline — ไม่มีทางอื่น

จากนั้นเลือกทางใดทางหนึ่ง

**ก) เสิร์ฟ base พร้อม adapter** — ไม่ต้อง merge ไม่กินดิสก์เพิ่ม

```bash
/workspace/aax6/env/bin/python -m vllm.entrypoints.openai.api_server \
  --model /workspace/models/Qwen3.5-9B \
  --trust-remote-code --dtype bfloat16 --gdn-prefill-backend triton \
  --host 127.0.0.1 --port 8001 --max-model-len 24576 \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml \
  --default-chat-template-kwargs '{"enable_thinking": false}' \
  --enable-lora --max-lora-rank 32 \
  --lora-modules sft_v2_3_1-h20=checkpoints/sft_v2_3_1-h20 \
  --gpu-memory-utilization 0.70
```

ชื่อที่ผู้เรียกต้องส่งใน field `model` คือ **ชื่อ lora-module** (ด้านซ้ายของ `=`) ไม่ใช่ชื่อ base

⚠️ ทางนี้ใช้ `--tool-call-parser qwen3_xml` ซึ่ง **ต่างจาก `qwen3_coder`** ที่ `serve_aax6.sh`
ใช้กับ checkpoint เต็ม ⇒ ต้องทำด่านตรวจข้อ 5 ② ให้ผ่านก่อนใช้งานจริง

**ข) merge เป็น checkpoint เต็มก่อน** — ได้ไดเรกทอรีที่ `serve_aax6.sh` เสิร์ฟได้ตรง ๆ
แต่กินดิสก์เพิ่ม ~18 GB · ต้องมี `peft` กับ `accelerate` ในสภาพแวดล้อมเดียวกับข้อ 2 และ
ต้องคัดลอกไฟล์ tokenizer กับ chat template จาก base ไปไว้ในไดเรกทอรีผลลัพธ์ด้วย ไม่งั้น
vLLM หา chat template ไม่เจอ
---

# 4 · คำสั่งเสิร์ฟ

```bash
bash demo_v2/ops/serve_aax6.sh
```

สคริปต์คำนวณ `--gpu-memory-utilization` ให้เองจากหน่วยความจำที่ **ว่างจริง** ณ ขณะนั้น
จึงใช้ได้แม้การ์ดใบนั้นมีผู้ใช้อื่นอยู่ · ตัวแปรที่กำหนดทับได้

| ตัวแปร | ค่าปริยาย | ความหมาย |
| --- | --- | --- |
| `MODEL` | **ต้องตั้ง** | ไดเรกทอรีของ checkpoint · ไม่ตั้งแล้วสคริปต์ล้มพร้อมบอกวิธี |
| `NAME` | ชื่อโฟลเดอร์ของ `MODEL` | ชื่อที่ผู้เรียกต้องส่งมาใน field `model` |
| `PORT` | `8001` | พอร์ตที่เสิร์ฟ |
| `MAXLEN` | `24576` | ความยาว context สูงสุด |
| `MAXSEQS` | `32` | จำนวน request พร้อมกันสูงสุด |
| `MARGIN` | `1500` | หน่วยความจำ (MiB) ที่กันไว้ ไม่จองจนเต็มพอดี |
| `GMU` | คำนวณเอง | กำหนดสัดส่วนหน่วยความจำเองโดยข้ามการคำนวณ |
| `VLLM` | `/workspace/aax6/env/bin/vllm` | binary ของ environment ที่ติดตั้งไว้ในข้อ 2 |

ธงสองตัวในสคริปต์ที่ห้ามถอด

* `--gdn-prefill-backend triton` — ถอดแล้ว engine จะตายตอนบูต
* `--enable-auto-tool-choice --tool-call-parser …` — ไม่มีแล้วแอปจะไม่ได้ tool call เลย

สคริปต์รอจนกว่า endpoint จะพร้อม (ปกติ 1–3 นาที) แล้วรายงานขนาด KV cache ที่จองได้
หากบูตไม่ขึ้น จะพิมพ์ท้าย log ให้ทันทีแทนที่จะรอจนหมดเวลา

**หยุด**

```bash
fuser -k 8001/tcp
```

จากนั้นตรวจว่า `nvidia-smi` แสดงหน่วยความจำคืนมาจริงก่อนเสิร์ฟตัวใหม่ — กระบวนการลูก
(`VLLM::EngineCore`) อาจยังกอด VRAM อยู่แม้ตัวแม่ตายแล้ว

---

# 5 · ตรวจก่อนเชื่อว่าเสิร์ฟถูก

สามข้อนี้ล้มเหลวได้โดยไม่มี error ⇒ ตรวจทุกครั้งที่ตั้งเครื่องใหม่หรือเปลี่ยนเวอร์ชัน

**① endpoint เห็นโมเดลด้วยชื่อที่ถูก**

```bash
curl -s http://127.0.0.1:8001/v1/models
```

ต้องได้ `"id"` ตรงกับ `NAME` ที่เสิร์ฟ · แอปไม่จำเป็นต้องรู้ชื่อนี้ล่วงหน้า มันถาม endpoint เอง

**② tool call ถูกแปลงจริง** — parser ที่ไม่ตรงกับรูปที่โมเดลพ่นออกมา จะคืน
`tool_calls: []` ทั้งที่ `content` มี `<tool_call>{…}</tool_call>` ครบถ้วน แอปจะเห็นเป็น
"โมเดลไม่ยอมเรียกเครื่องมือ" ทั้งที่ต้นเหตุอยู่ที่ตัวแปลง

```bash
curl -s http://127.0.0.1:8001/v1/chat/completions -H 'content-type: application/json' -d '{
  "model": "<NAME ที่เสิร์ฟ>",
  "messages": [{"role":"user","content":"ยืนยันตัวตนลูกค้าเลข 1234"}],
  "tools": [{"type":"function","function":{"name":"verify_identity",
             "parameters":{"type":"object","properties":{"last_4":{"type":"string"}}}}}]
}' | python3 -m json.tool | grep -A3 '"tool_calls"'
```

ถ้าได้ `[]` แต่ `content` มีข้อความ `<tool_call>` ให้เปลี่ยน `--tool-call-parser`
(รูปแบบที่ใช้ได้ต่างกันตามรุ่นของ vLLM — `qwen3_coder` กับ `hermes` เป็นสองตัวที่พบบ่อย)

**③ ยิงด้วย prompt ยาวเท่าของจริง** — พรอมป์ของ tenant ยาว 12k–24k token
เคยพบอาการที่ prompt สั้นตอบปกติทุกอย่าง แต่ prompt ยาวจริงพ่นอักขระซ้ำ ๆ ออกมา
⇒ **การทดสอบด้วยประโยคสั้นไม่นับ** ใช้พรอมป์จริงของบริษัทหนึ่ง (ดูได้จาก
`/api/flow/instruction?company=AEON` ของแอป) ยิงสัก 3 ครั้ง ถ้าเจอขยะให้ฆ่าแล้วเสิร์ฟใหม่

---

# 6 · ต่อแอปเข้ากับ endpoint

ตั้งค่าใน `demo_v2/.env` (ดู [.env.sample](../.env.sample))

```bash
AAX6_VLLM_BASE_URL=http://127.0.0.1:8001/v1
# AAX6_FLOW_MODEL=              # ไม่ต้องตั้งก็ได้ — แอปหยิบตัวที่ vLLM เสิร์ฟอยู่
                                # ตั้งเมื่อเสิร์ฟหลายตัวแล้วอยากเจาะจงว่าจะใช้ตัวไหน
```

`ops/run_demo.sh` จะไม่ยอมเริ่มถ้า vLLM ยังไม่ขึ้น — ล้มตรงนั้นดีกว่าไปล้มตอนผู้ใช้กดคุย

---

# 7 · เสิร์ฟหลายโมเดลให้เลือกในหน้าเว็บ

```bash
bash demo_v2/ops/serve_both.sh
export AAX6_VLLM_BASE_URLS=http://127.0.0.1:8001/v1,http://127.0.0.1:8002/v1
```

แอปอ่านรายชื่อจาก `/v1/models` ของทุก endpoint ที่ระบุไว้เอง แล้วนำมาแสดงเป็นตัวเลือก
ในแถบควบคุม ⇒ การเพิ่มโมเดลไม่ต้องแก้โค้ด เสิร์ฟเพิ่มแล้วเติม URL ให้ครบเท่านั้น

ตัวที่สองจองหน่วยความจำจากส่วนที่ **เหลือ** หลังตัวแรกจองไปแล้ว เพราะการคำนวณอิงค่าว่างจริง
จึงไม่ต้องกำหนด `GMU` เอง

---

# 8 · เมื่อบูตไม่ขึ้น

| อาการ | สาเหตุ | การแก้ |
| --- | --- | --- |
| โมเดลขึ้นปกติแต่พ่นข้อความไม่มีความหมาย | `transformers` ถูก downgrade ตอนลง vLLM | ดันกลับเป็น 5.5.0 ด้วย `--no-deps` (ข้อ 2) |
| `CXXABI_1.3.15 not found` | `libstdc++` ของระบบเก่ากว่าที่ environment ต้องการ | สคริปต์ตั้ง `LD_LIBRARY_PATH` ให้แล้ว — ตรวจว่า `VLLM` ชี้ binary ที่ถูก |
| `out of memory` ตอน warm-up | ค่าปริยายของ vLLM จองสำหรับ 1024 request พร้อมกัน | ลด `MAXSEQS` (สคริปต์ตั้งไว้ที่ 32) หรือเพิ่ม `MARGIN` |
| `EngineDead` ตอนบูต | ถอด `--gdn-prefill-backend triton` ออก | ใส่กลับ |
| `Engine core initialization failed` | instance เดิมยังกอด VRAM อยู่ | ฆ่า `VLLM::EngineCore` ให้หมด รอ `nvidia-smi` แสดง free จริง |
| `ที่ว่างไม่พอ — หยุด` | หน่วยความจำว่างน้อยกว่า `MARGIN` | รอให้กระบวนการอื่นคืนหน่วยความจำ หรือกำหนด `GMU` เอง |
| `พอร์ต 8001 มีคนใช้อยู่แล้ว` | มี instance ค้างอยู่ | `fuser -k 8001/tcp` แล้วเริ่มใหม่ |
| โหลดโมเดลล้มทันที ไฟล์เล็กผิดปกติ | ยังไม่ได้ `git lfs pull` จึงได้ไฟล์ตัวชี้ | รัน `git lfs pull` แล้วตรวจขนาดไฟล์ |
| `does not appear to have a file named config.json` | ชี้ `MODEL` ไปที่ไดเรกทอรีของ adapter | ใช้ทางข้อ 3.2 หรือ merge ก่อน |

log อยู่ที่ `/workspace/aax6/logs/vllm.log` (กำหนดทับด้วย `LOG=`)

> เมื่อคะแนนหรือพฤติกรรมตกผิดปกติบนเครื่องใหม่ ให้สงสัยการติดตั้งก่อนสงสัยโมเดล
> เคยมีกรณีที่ผลตกจาก 43/43 เหลือ 8–15/43 โดยต้นเหตุคือ tool call ถูกตัวแปลงทิ้ง
> ไม่ใช่คุณภาพของโมเดล และอีกกรณีหนึ่งคือไฟล์น้ำหนักที่คัดลอกมาไม่ครบ
> (`sha256sum` เทียบกับต้นทางก่อนสรุปเสมอ)
