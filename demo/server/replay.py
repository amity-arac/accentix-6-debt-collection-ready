"""Trajectory loader + per-agent-turn hop extractor for replay mode.

Reads the recorded v6c evaluation results JSON, locates a case by id, and
groups its `full-trajectory` array into one inner list per agent turn (each
list is the hops the frontend should render in response to one user message).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_FILE = (
    REPO_ROOT
    / "data"
    / "trajectories"
    / "gemini-v6c-20260522"
    / "evaluation-results-gemini-prescript.json"
)

FILLER_TEXT = "รบกวนรอซักครู่ค่ะ"


def load_case(case_id: str, results_file: Path | None = None) -> dict[str, Any]:
    """Locate a case by id in the v6c results file."""
    path = results_file or DEFAULT_RESULTS_FILE
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    for case in data:
        if case.get("id") == case_id:
            return case
    raise KeyError(f"case_id {case_id!r} not found in {path}")


def extract_agent_turns(full_trajectory: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group raw full-trajectory entries into agent turns of canonical hop shape.

    Hop shape (matches `LiveSession.normalize_live_hops` output):
        {"kind": "tool_call",   "name": str, "args": dict}
        {"kind": "tool_result", "name": str, "result": Any}
        {"kind": "reply",       "text": str, "text_ids": list[int], "dynamic_vars": dict}

    Both the `reply` tool_call AND the following rendered-Thai TEXT are emitted
    as separate hops (a `tool_call` for the call site, then a `reply` for the
    rendered text). The simulator-injected filler "รบกวนรอซักครู่ค่ะ" is
    surfaced as a leading `reply` hop on any turn that contains a non-reply
    tool call — mirrors `simulator/run.py:_hops_to_trajectory_entries` so the
    demo plays the same "please hold" utterance before the tool sequence.
    """
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    pending_reply_args: dict[str, Any] | None = None
    last_tool_call_name: str | None = None

    def flush_current() -> None:
        nonlocal current, pending_reply_args, last_tool_call_name
        if current:
            turns.append(current)
            current = []
        pending_reply_args = None
        last_tool_call_name = None

    for entry in full_trajectory:
        role = entry.get("role")
        content = entry.get("content")

        if role == "customer":
            flush_current()
            continue

        if role == "system":
            if isinstance(content, dict) and "result" in content:
                current.append({
                    "kind": "tool_result",
                    "name": last_tool_call_name or "unknown",
                    "result": content["result"],
                })
            continue

        # role == "agent"
        if isinstance(content, dict) and "tool_call" in content:
            name = content["tool_call"]
            args = content.get("args", {})
            current.append({
                "kind": "tool_call",
                "name": name,
                "args": args,
            })
            if name == "reply":
                pending_reply_args = args
            else:
                last_tool_call_name = name
            continue

        if isinstance(content, str):
            if content == FILLER_TEXT:
                current.append({
                    "kind": "reply",
                    "text": FILLER_TEXT,
                    "text_ids": [],
                    "dynamic_vars": {},
                })
                continue
            if pending_reply_args is not None:
                current.append({
                    "kind": "reply",
                    "text": content,
                    "text_ids": pending_reply_args.get("text_ids", []),
                    "dynamic_vars": pending_reply_args.get("dynamic_vars", {}),
                })
                pending_reply_args = None

    flush_current()
    return turns


def reply_texts(turns: list[list[dict[str, Any]]]) -> list[str]:
    """All unique agent reply texts across all turns (for TTS pre-cache)."""
    seen: set[str] = set()
    out: list[str] = []
    for turn in turns:
        for hop in turn:
            if hop.get("kind") == "reply":
                text = hop.get("text", "")
                if text and text not in seen:
                    seen.add(text)
                    out.append(text)
    return out
