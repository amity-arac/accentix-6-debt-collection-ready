#!/usr/bin/env bash
# Serve two models at once so the demo can offer both — the 9B SFT and the 27B SFT
# (W4A16).
#
#   bash demo_v2/ops/serve_both.sh
#
# The demo reads the list from /v1/models of every endpoint in AAX6_VLLM_BASE_URLS
# itself (see _model_endpoints in demo_v2/server/sessions.py), so adding a model needs
# no code change — serve it and add its URL.
#
# Stop with:  fuser -k 8001/tcp 8002/tcp
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

M9=${M9:-/workspace/aax6/models/sft9b_v3}
M27=${M27:-/workspace/aax6/models/sft27b_w4a16}
N9=${N9:-sft-9b-v3}
N27=${N27:-sft-27b-w4a16}
P9=${P9:-8001}
P27=${P27:-8002}

for pair in "$M9|$N9" "$M27|$N27"; do
  p="${pair%%|*}"
  [ -d "$p" ] || { echo "no model at $p — set M9= or M27= correctly first"; exit 1; }
done

echo "=== 1/2 serving $N9 on port $P9 ==="
MODEL="$M9" NAME="$N9" PORT="$P9" LOG=/workspace/aax6/logs/vllm_9b.log bash "$HERE/serve_aax6.sh"

echo
echo "=== 2/2 serving $N27 on port $P27 ==="
# The second one reserves out of what is LEFT after the first: serve_aax6.sh works GMU
# out from memory.free on its own, so there is no GMU to set here.
MODEL="$M27" NAME="$N27" PORT="$P27" LOG=/workspace/aax6/logs/vllm_27b.log bash "$HERE/serve_aax6.sh"

echo
echo "=== ready ==="
echo "point the demo at both:"
echo
echo "  export AAX6_VLLM_BASE_URLS=http://127.0.0.1:${P9}/v1,http://127.0.0.1:${P27}/v1"
echo
echo "then start the demo as usual — the version dropdown will list $N9 and $N27"
