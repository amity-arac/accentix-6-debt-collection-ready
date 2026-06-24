#!/usr/bin/env bash
# Serve the fine-tuned Qwen debt-collection agent (sft_v2_2) for the live demo.
#
# Brings up an OpenAI-compatible vLLM endpoint at :8000 serving the base model
# Qwen/Qwen3.5-9B with the sft_v2_2 LoRA adapter applied. The demo backend talks
# to this endpoint (AAX6_VLLM_BASE_URL=http://localhost:8000/v1).
#
# Requirements (see README):
#   - NVIDIA GPU, ~40 GB+ VRAM (9B + LoRA + KV cache)
#   - vLLM installed:  pip install vllm==0.19.0
#   - Hugging Face access — the base model auto-downloads on first run (~18 GB)
#   - The adapter present at checkpoints/$MODEL_NAME/ (git-LFS; run `git lfs pull`)
#
# The vLLM REQUEST model name == the served LoRA module ($MODEL_NAME, default
# sft_v2_2) — that is what the demo must send (AAX6_VLLM_MODEL) so the adapter is
# applied on top of base.
set -uo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"
# Which LoRA adapter to serve. Defaults to sft_v2_2 (v9 teacher: honest-AI
# disclosure + transfer_to_human_agent escalation). Override to serve a different
# adapter:  AAX6_VLLM_MODEL=sft_v2 bash scripts/serve_qwen.sh
# The served LoRA module name == $MODEL_NAME, exactly what the backend must send
# (AAX6_VLLM_MODEL) so the adapter is applied on top of the base model.
MODEL_NAME="${AAX6_VLLM_MODEL:-sft_v2_2}"
ADAPTER="checkpoints/$MODEL_NAME"
# Fraction of GPU memory vLLM may use. vLLM's default (0.9) sizes the KV cache so
# aggressively that on a ~96 GB card (e.g. H20) the CUDA-graph pool overshoots its
# own estimate and the engine OOMs at the sampler warmup, AFTER loading weights and
# capturing graphs (so it looks like a late crash, not a config error). 0.70 leaves
# ample headroom and is still far more KV cache than the single-session demo needs.
# Override for bigger/smaller GPUs:  GPU_MEM_UTIL=0.85 bash scripts/serve_qwen.sh
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.70}"

# flash-attn's compiled CUDA extension needs CXXABI_1.3.15, but the conda python
# binds the older SYSTEM libstdc++ (GCC 12, max CXXABI_1.3.13) at startup, so vLLM
# dies importing flash_attn during model load ("CXXABI_1.3.15 not found"). Force-load
# the env's newer libstdc++, which has the symbol. MUST be `export`ed — `exec` passes
# only exported vars to the vLLM process, so a bare assignment is silently ignored.
# Path is conda-env-specific; only preload if it actually exists. On a plain venv
# host (the README setup) it won't, and it isn't needed unless flash-attn was built
# against an old libstdc++. Override with LIBSTDCPP=/path/to/libstdc++.so.6 if needed.
LIBSTDCPP="${LIBSTDCPP:-/root/miniconda3/envs/aax6/lib/libstdc++.so.6}"
[ -f "$LIBSTDCPP" ] && export LD_PRELOAD="$LIBSTDCPP"

if [ ! -f "$ADAPTER/adapter_config.json" ] || [ ! -f "$ADAPTER/adapter_model.safetensors" ]; then
  echo "ERROR: LoRA adapter not found at $ADAPTER/"
  echo "       After cloning, fetch the LFS files:  git lfs pull"
  exit 1
fi

echo "Serving Qwen/Qwen3.5-9B + LoRA '$MODEL_NAME' on :$PORT"
echo "  -> request model name to use from the backend: $MODEL_NAME"
echo "  -> gpu-memory-utilization: $GPU_MEM_UTIL (override with GPU_MEM_UTIL=...)"
echo "  -> LD_PRELOAD: ${LD_PRELOAD:-<none>}"
exec python3 -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen3.5-9B --trust-remote-code --gdn-prefill-backend triton \
  --host 0.0.0.0 --port "$PORT" --dtype bfloat16 --max-model-len 32768 \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml \
  --default-chat-template-kwargs '{"enable_thinking": false}' \
  --enable-lora --max-lora-rank 32 --lora-modules "$MODEL_NAME=$ADAPTER" \
  --gpu-memory-utilization "$GPU_MEM_UTIL"
