"""Lightweight, always-on logging for the agent↔backend tool loop.

Surfaces — in the demo backend's console — exactly what the model asked for each
turn and how the system handled it:

  * every tool call the model emits      (the model's choice)
  * every deterministic backend result   (CaseBackend.dispatch handling)
  * every `reply` guard rejection         (reply path re-prompting the model)
  * every successful `reply`               (what the customer actually sees)
  * every fallback                         (degenerate tool call, unknown
                                            text_ids, or hop exhaustion)

Reply handling + fallbacks live in agents.communicator (not simulator.backend),
so both modules import this one to write a single, coherent timeline.

On by default; set AAX6_LOG_TOOLS=0 (or false/no/off) to mute. Deliberately
stdlib-only — importing this never drags in google-genai the way importing
simulator.config would, so it is safe to use from simulator.backend.
"""

import json
import logging
import os
import sys

LOG_TOOLS: bool = (
    os.environ.get("AAX6_LOG_TOOLS", "1").strip().lower()
    not in ("0", "false", "no", "off", "")
)


def _build_logger() -> logging.Logger:
    """Return a dedicated 'aax6.tools' logger with its own stderr handler.

    `propagate=False` + a private handler means our lines print exactly once and
    do not depend on (or duplicate through) uvicorn's root logging config. When
    muted we attach a NullHandler and raise the level so call sites are cheap.
    """
    lg = logging.getLogger("aax6.tools")
    if not lg.handlers:
        if LOG_TOOLS:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter("%(message)s"))
            lg.addHandler(handler)
            lg.setLevel(logging.INFO)
        else:
            lg.addHandler(logging.NullHandler())
            lg.setLevel(logging.CRITICAL + 1)
        lg.propagate = False
    return lg


logger = _build_logger()


def short(obj, limit: int = 300) -> str:
    """Compact one-line repr of tool args/results, truncated for log hygiene."""
    if isinstance(obj, str):
        s = obj
    else:
        try:
            s = json.dumps(obj, ensure_ascii=False)
        except Exception:
            s = str(obj)
    s = s.replace("\n", " ").strip()
    return s if len(s) <= limit else s[:limit] + "…"
