#!/usr/bin/env bash
# HTTPS + basic auth ครอบหน้าเว็บ demo
#
# ต้องเป็น https เพราะเบราว์เซอร์ให้เปิดไมค์เฉพาะบน secure context เท่านั้น
# พอร์ต 80/443 ของเครื่องนี้ปิด จึงขอ cert จาก Let's Encrypt ไม่ได้ ใช้ self-signed
# ที่มี IP อยู่ใน SAN แทน — กดข้ามคำเตือนครั้งแรกครั้งเดียว หลังจากนั้นถือเป็น
# secure context เต็มรูปแบบ ไมค์ใช้ได้ปกติ
#
#   bash /workspace/aax6/run_proxy.sh
#
# หยุด:  fuser -k 10000/tcp
set -euo pipefail

ROOT=${ROOT:-/workspace/aax6}
PORT=${PORT:-10000}           # พอร์ตเดียวที่เปิดออกเน็ต
UPSTREAM=${UPSTREAM:-127.0.0.1:4100}
USER=${AUTH_USER:-demoapp}
LOG=${LOG:-$ROOT/logs/proxy.log}
export PATH="$ROOT/env/bin:$PATH"

HASH=$(sed -n 's/^hash=//p' "$ROOT/tls/.auth")
[ -n "$HASH" ] || { echo "ไม่พบรหัสผ่านที่ hash ไว้ใน $ROOT/tls/.auth"; exit 1; }

curl -s -m 5 "http://$UPSTREAM/api/health" >/dev/null \
  || { echo "เว็บที่ $UPSTREAM ยังไม่ขึ้น — รัน run_demo.sh ก่อน"; exit 1; }
if ss -tln 2>/dev/null | grep -q ":${PORT} "; then
  echo "พอร์ต ${PORT} มีคนใช้อยู่ — หยุด"; exit 1
fi
mkdir -p "$(dirname "$LOG")"

cat > "$ROOT/tls/Caddyfile" <<EOF
{
	admin off
	auto_https off
}

:${PORT} {
	tls ${ROOT}/tls/demo.crt ${ROOT}/tls/demo.key

	basic_auth {
		${USER} ${HASH}
	}

	# NDJSON ที่ /api/session กับ /turn เป็น stream — ห้ามให้ proxy ไปกองไว้ก่อนส่ง
	reverse_proxy ${UPSTREAM} {
		flush_interval -1
	}
}
EOF

setsid nohup caddy run --config "$ROOT/tls/Caddyfile" --adapter caddyfile \
  > "$LOG" 2>&1 < /dev/null &

echo "เริ่มแล้ว pid $! — log: $LOG"
for i in $(seq 1 20); do
  code=$(curl -s -k -o /dev/null -w "%{http_code}" -m 3 "https://127.0.0.1:${PORT}/api/health" || true)
  if [ "$code" = "401" ]; then
    ok=$(curl -s -k -u "${USER}:demoapp" -m 5 "https://127.0.0.1:${PORT}/api/flow/companies" || true)
    echo "พร้อมใช้งาน:  https://101.47.153.162:${PORT}"
    echo "  ไม่ใส่รหัส -> 401 ✓   ใส่รหัสแล้ว -> ${ok}"
    exit 0
  fi
  sleep 2
done
echo "ยังไม่ขึ้น — ดู $LOG"; tail -15 "$LOG"; exit 1
