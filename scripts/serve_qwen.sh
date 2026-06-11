#!/usr/bin/env bash
# Serve the fine-tuned Qwen debt-collection agent (sft_v2) for the live demo.
#
# Brings up an OpenAI-compatible vLLM endpoint at :8000 serving the base model
# Qwen/Qwen3.5-9B with the sft_v2 LoRA adapter applied. The demo backend talks
# to this endpoint (AAX6_VLLM_BASE_URL=http://localhost:8000/v1).
#
# Requirements (see README):
#   - NVIDIA GPU, ~40 GB+ VRAM (9B + LoRA + KV cache)
#   - vLLM installed:  pip install vllm==0.19.0
#   - Hugging Face access — the base model auto-downloads on first run (~18 GB)
#   - The sft_v2 adapter present at checkpoints/sft_v2/ (git-LFS; run `git lfs pull`)
#
# The vLLM REQUEST model name is "sft_v2" (the LoRA module) — that is what the
# demo must send (AAX6_VLLM_MODEL=sft_v2) so the adapter is applied on top of base.
set -uo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"
ADAPTER="checkpoints/sft_v2"

if [ ! -f "$ADAPTER/adapter_config.json" ] || [ ! -f "$ADAPTER/adapter_model.safetensors" ]; then
  echo "ERROR: LoRA adapter not found at $ADAPTER/"
  echo "       After cloning, fetch the LFS files:  git lfs pull"
  exit 1
fi

echo "Serving Qwen/Qwen3.5-9B + LoRA 'sft_v2' on :$PORT"
echo "  -> request model name to use from the backend: sft_v2"
exec python3 -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen3.5-9B --trust-remote-code --gdn-prefill-backend triton \
  --host 0.0.0.0 --port "$PORT" --dtype auto --max-model-len 32768 \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml \
  --default-chat-template-kwargs '{"enable_thinking": false}' \
  --enable-lora --max-lora-rank 32 --lora-modules "sft_v2=$ADAPTER"
