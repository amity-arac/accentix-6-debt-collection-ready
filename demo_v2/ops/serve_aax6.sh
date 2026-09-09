#!/usr/bin/env bash
# Serve the model on a card that other people are also using.
#
#   MODEL=/path/to/ckpt bash serve_aax6.sh   # works out gpu-memory-utilization from
#                                            # what is actually free right now
#   MARGIN=3000 … bash serve_aax6.sh         # leave a wider margin (MiB)
#   GMU=0.39 … bash serve_aax6.sh            # set it yourself, skip the calculation
#
# Stop with:  fuser -k 8001/tcp
set -euo pipefail

# No default checkpoint: the name of a model on our host is wrong on anyone else's.
MODEL=${MODEL:?set MODEL= to the checkpoint directory first (e.g. MODEL=/workspace/models/sft9b_v3)}
NAME=${NAME:-$(basename "$MODEL")}
PORT=${PORT:-8001}
LOG=${LOG:-/workspace/aax6/logs/vllm.log}
MAXLEN=${MAXLEN:-24576}
# vllm 0.19 defaults max_num_seqs to 1024, and at sampler warm-up it reserves for 1024
# concurrent requests — an instant OOM on a ~37 GB budget (the neighbour on the same
# card uses 32). The demo takes a handful of calls at once, so 32 is ample and the rest
# of the VRAM goes to the KV cache instead.
MAXSEQS=${MAXSEQS:-32}
MARGIN=${MARGIN:-1500}          # MiB held back, so the reservation is not flush full
VLLM=${VLLM:-/workspace/aax6/env/bin/vllm}   # our own env, not anyone else's
ENV_LIB=${ENV_LIB:-$(dirname "$(dirname "$VLLM")")/lib}

# Calling the binary directly without `conda activate` dies with
#   ImportError: libstdc++.so.6: version `CXXABI_1.3.15' not found
# because the system libstdc++ is older than the env needs. `activate` would put the
# env's lib first on LD_LIBRARY_PATH; we do that here instead, so nothing depends on
# conda's shell hook.
export LD_LIBRARY_PATH="${ENV_LIB}:${LD_LIBRARY_PATH:-}"

if ! "$VLLM" --version >/dev/null 2>&1; then
  echo "cannot run $VLLM — try activating the env that owns it, then rerun"; exit 1
fi

read -r TOTAL FREE < <(nvidia-smi --query-gpu=memory.total,memory.free \
                        --format=csv,noheader,nounits | head -1 | tr -d ',')

# gpu-memory-utilization is the share of the WHOLE card this instance may reserve,
# not the share of what is free — set it above what is actually free and vLLM errors
# out at boot.
if [ -z "${GMU:-}" ]; then
  GMU=$(awk -v f="$FREE" -v t="$TOTAL" -v m="$MARGIN" 'BEGIN{printf "%.2f",(f-m)/t}')
fi

echo "GPU: ${FREE} free / ${TOTAL} total MiB  |  margin ${MARGIN}  ->  --gpu-memory-utilization ${GMU}"
echo "     will reserve = $(awk -v g="$GMU" -v t="$TOTAL" 'BEGIN{printf "%d",g*t}') MiB"

if [ "$(awk -v g="$GMU" 'BEGIN{print (g<=0)}')" = "1" ]; then
  echo "not enough free memory — stopping"; exit 1
fi
if ss -tln 2>/dev/null | grep -q ":${PORT} "; then
  echo "port ${PORT} is already in use — stopping"; exit 1
fi

mkdir -p "$(dirname "$LOG")"

setsid nohup "$VLLM" serve "$MODEL" \
  --served-model-name "$NAME" \
  --host 127.0.0.1 --port "$PORT" \
  --trust-remote-code \
  --dtype bfloat16 \
  --gdn-prefill-backend triton \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --enable-prefix-caching \
  --max-model-len "$MAXLEN" \
  --max-num-seqs "$MAXSEQS" \
  --gpu-memory-utilization "$GMU" \
  > "$LOG" 2>&1 < /dev/null &

echo "started, pid $! — log: $LOG"
echo "waiting until it is ready (usually 1-3 minutes)…"
for i in $(seq 1 90); do
  if curl -s -m 3 "http://127.0.0.1:${PORT}/v1/models" | grep -q "$NAME"; then
    echo "ready"
    grep -aE "model weights take|GPU KV cache size|maximum concurrency" "$LOG" | tail -3
    exit 0
  fi
  if grep -aqE "Error|Traceback|out of memory" "$LOG"; then
    echo "it did not come up — tail of the log:"; tail -20 "$LOG"; exit 1
  fi
  sleep 5
done
echo "past 7 minutes and still not up — see $LOG"; exit 1
