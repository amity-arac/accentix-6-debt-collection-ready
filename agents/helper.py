"""Centralized cost calculation and retry logic for all models in the pipeline."""

import time


def retry_transient(fn, retries=3, base_delay=2, max_delay=30):
    """Retry a callable on transient API errors (Gemini 500/503/504, OpenAI 429/500/503)."""
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as e:
            msg = str(e)
            is_transient = (
                "503" in msg or "500" in msg or "UNAVAILABLE" in msg
                or "429" in msg or "rate_limit" in msg.lower()
                or "504" in msg or "DEADLINE_EXCEEDED" in msg  # Gemini pro server-side timeouts
            )
            if not is_transient or attempt >= retries:
                raise
            wait = min(base_delay * (2 ** attempt), max_delay)
            print(f"    [retry] attempt {attempt + 1}/{retries}, waiting {wait}s ...")
            time.sleep(wait)


# Pricing registry: model name → pricing config (per 1M tokens, USD)
# Tiered models have low/high prices with a tier boundary (input token count).
PRICING = {
    "gemini-3.1-pro-preview": {
        "input_low": 2.0,
        "input_high": 4.0,
        "cached_low": 0.2,
        "cached_high": 0.4,
        "output_low": 12.0,
        "output_high": 18.0,
        "tier_boundary": 200_000,
    },
    "gemini-3.1-flash-lite-preview": {
        "input_low": 0.25,
        "input_high": 0.25,
        "cached_low": 0.03,
        "cached_high": 0.03,
        "output_low": 1.5,
        "output_high": 1.5,
        "tier_boundary": 200_000,
    },
    "gemini-2.5-flash": {
        "input_low": 0.15,
        "input_high": 0.30,
        "cached_low": 0.0375,
        "cached_high": 0.075,
        "output_low": 0.60,
        "output_high": 3.50,
        "tier_boundary": 200_000,
    },
    "gemini-2.5-flash-lite": {
        "input_low": 0.1,
        "input_high": 0.1,
        "cached_low": 0.0,
        "cached_high": 0.0,
        "output_low": 0.4,
        "output_high": 0.4,
        "tier_boundary": 0,  # flat pricing, no tier
    },
    "gemini-3-flash-preview": {
        "input_low": 0.50,
        "input_high": 0.50,
        "cached_low": 0.05,
        "cached_high": 0.05,
        "output_low": 3.00,
        "output_high": 3.00,
        "tier_boundary": 0,
    },
    "gpt-5.5": {
        "input_low": 5.0,
        "input_high": 5.0,
        "cached_low": 0.50,
        "cached_high": 0.50,
        "output_low": 30.0,
        "output_high": 30.0,
        "tier_boundary": 0,
    },
}


def calculate_cost(model: str, usage: dict) -> float:
    """Calculate API cost for a single LLM call.

    Args:
        model: Model identifier string (e.g. "gemini-3.1-pro-preview").
        usage: Dict with token counts. Expected keys:
            - prompt_token_count: total input tokens
            - cached_content_token_count: cached input tokens (subset of prompt)
            - candidates_token_count: output tokens
            - thoughts_token_count: thinking/reasoning tokens (billed as output)

    Returns:
        Cost in USD.
    """
    # Self-hosted models (Qwen via vLLM) have no API cost
    if model.startswith("Qwen/") or model not in PRICING:
        return 0.0

    pricing = PRICING[model]

    prompt_tokens = usage.get("prompt_token_count", 0) or 0
    cached_tokens = usage.get("cached_content_token_count", 0) or 0
    output_tokens = usage.get("candidates_token_count", 0) or 0
    thoughts_tokens = usage.get("thoughts_token_count", 0) or 0

    non_cached_tokens = prompt_tokens - cached_tokens
    is_high_tier = 0 < pricing["tier_boundary"] < prompt_tokens

    input_price = pricing["input_high"] if is_high_tier else pricing["input_low"]
    cached_price = pricing["cached_high"] if is_high_tier else pricing["cached_low"]
    output_price = pricing["output_high"] if is_high_tier else pricing["output_low"]

    return (
        non_cached_tokens * input_price / 1_000_000
        + cached_tokens * cached_price / 1_000_000
        + (output_tokens + thoughts_tokens) * output_price / 1_000_000
    )
