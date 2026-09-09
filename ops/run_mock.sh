#!/usr/bin/env bash
# Mockoon — backend ปลอมที่ตอบ tool call ทุกตัวของทุกบริษัท
#
#   bash /workspace/aax6/run_mock.sh
#
# หยุด:  fuser -k 3001/tcp
set -euo pipefail

ROOT=${ROOT:-/workspace/aax6}
DATA=${DATA:-$ROOT/demo-app/mock/aax6-mock.json}
PORT=${PORT:-3001}
LOG=${LOG:-$ROOT/logs/mock.log}

# node อยู่ใน env ของเรา ไม่ได้อยู่ในระบบ — npm/mockoon-cli ใช้ shebang `env node`
# จึงต้องมี node ใน PATH ก่อน ไม่งั้นได้ "/usr/bin/env: 'node': No such file or directory"
export PATH="$ROOT/env/bin:$PATH"

[ -f "$DATA" ] || { echo "ไม่พบไฟล์ mock: $DATA"; exit 1; }
if ss -tln 2>/dev/null | grep -q ":${PORT} "; then
  echo "พอร์ต ${PORT} มีคนใช้อยู่ — หยุด"; exit 1
fi
mkdir -p "$(dirname "$LOG")"

setsid nohup mockoon-cli start --data "$DATA" --port "$PORT" \
  > "$LOG" 2>&1 < /dev/null &

echo "เริ่มแล้ว pid $! — log: $LOG"
for i in $(seq 1 20); do
  if curl -s -m 3 -X POST "http://127.0.0.1:${PORT}/AEON/record_outcome" \
       -H "Content-Type: application/json" \
       -d '{"tool":"record_outcome","args":{"result":"ptp","reason":"ptp"}}' \
       | grep -q recorded; then
    echo "พร้อมใช้งาน — ทดสอบ AEON/record_outcome ผ่าน"
    exit 0
  fi
  sleep 2
done
echo "ยังไม่ตอบ — ดู $LOG"; tail -10 "$LOG"; exit 1
