#!/usr/bin/env bash
# เว็บ demo (FastAPI + หน้าเว็บที่ build แล้ว) — เสิร์ฟทั้ง API และ UI จากพอร์ตเดียว
# รัน demo_v2 (ตัวที่ใช้จริง) · ของเดิมเก็บไว้ที่ run_demo_v1.sh
#
#   bash /workspace/aax6/run_demo.sh
#   PORT=4100 bash /workspace/aax6/run_demo.sh
#
# หยุด:  fuser -k 4100/tcp
set -euo pipefail

ROOT=${ROOT:-/workspace/aax6}
APP=${APP:-$ROOT/demo-app}
VENV=${VENV:-$ROOT/demo-venv}
PORT=${PORT:-4100}       # ภายในเท่านั้น — ข้างนอกเข้าผ่าน run_proxy.sh (https + basic auth) ที่ :10000
LOG=${LOG:-$ROOT/logs/demo.log}
VLLM_URL=${VLLM_URL:-http://127.0.0.1:8001/v1}
MOCK_URL=${MOCK_URL:-http://127.0.0.1:3001}

cd "$APP"
export PYTHONPATH=.
export AAX6_DEMO_STATIC="$APP/demo_v2/frontend/dist"
export AAX6_DEMO_MODE=live
export AAX6_DEMO_AGENT=qwen
export AAX6_VLLM_BASE_URL="$VLLM_URL"
export AAX6_VLLM_BASE_URLS="$VLLM_URL"
export AAX6_API_BASE="$MOCK_URL"
export AAX6_V6_ACTIVE=1

# Google TTS อ่านไฟล์นี้ ถ้าไม่มีเสียงจะเงียบไปเฉยๆ (เสียงหาย แต่แชตยังใช้ได้)
export GOOGLE_APPLICATION_CREDENTIALS="$ROOT/adc.json"
export GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT:-nord-star-non-prod}

mkdir -p "$(dirname "$LOG")"

# ตรวจของที่ต้องพร้อมก่อน — ล้มตรงนี้ดีกว่าไปล้มตอนผู้ใช้กดคุย
curl -s -m 5 "$VLLM_URL/models" | grep -q '"id"' \
  || { echo "vLLM ที่ $VLLM_URL ยังไม่ขึ้น — รัน demo_v2/ops/serve_aax6.sh ก่อน"; exit 1; }
curl -s -m 5 -o /dev/null -w "" "$MOCK_URL/AEON/init" 2>/dev/null \
  || echo "เตือน: mock ที่ $MOCK_URL ไม่ตอบ — tool call จะ error (เปิด Mockoon หรือ tunnel เข้ามา)"
[ -d "$AAX6_DEMO_STATIC" ] || { echo "ไม่พบหน้าเว็บที่ build แล้ว: $AAX6_DEMO_STATIC"; exit 1; }
if ss -tln 2>/dev/null | grep -q ":${PORT} "; then
  echo "พอร์ต ${PORT} มีคนใช้อยู่ — หยุด"; exit 1
fi

setsid nohup "$VENV/bin/python" -m uvicorn demo_v2.server.app:app \
  --host 127.0.0.1 --port "$PORT" > "$LOG" 2>&1 < /dev/null &

echo "เริ่มแล้ว pid $! — log: $LOG"
for i in $(seq 1 30); do
  if curl -s -m 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    echo "พร้อมใช้งาน (ภายใน):  http://127.0.0.1:${PORT}  — เปิดให้ข้างนอกด้วย run_proxy.sh"
    curl -s -m 5 "http://127.0.0.1:${PORT}/api/flow/companies"
    echo
    exit 0
  fi
  sleep 2
done
echo "ยังไม่ขึ้น — ดู $LOG"; tail -15 "$LOG"; exit 1
