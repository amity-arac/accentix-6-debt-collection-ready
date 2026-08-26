#!/usr/bin/env python3
"""Demo-faithful batch driver for PIPA — runs N personas through the EXACT demo
FlowLiveSession (sft_v11 + v11.2 FlowSpec + compact catalog + SpecBackend), so the
trajectories are byte-identical in construction to what the web demo produces.

Why not flow_sim: flow_sim (main repo) uses full-text render_catalog + main-repo
prescript (.format breaks on {curly}) + state_summary injection — a DIFFERENT
prompt/loop than the demo. This driver reuses FlowLiveSession, guaranteeing parity.

Output shape matches simulation.flow_sim.run_conversation so run_pipa can score it.

Run (on the pod, with vLLM serving sft_v11 + .env GOOGLE_API_KEY loaded):
    cd /workspace/accentix-6-debt-collection-ready
    PYTHONPATH=. python tools/pipa_flow_driver.py \
        --personas /workspace/accentix-6-debt-collector/tmp/results/personas_v10_300.json \
        --model sft_v11 --parallel 4 --limit 3 \
        -o /workspace/accentix-6-debt-collector/tmp/results/flow_v112_personas307.json
"""
import argparse, asyncio, json, os, sys

os.environ.setdefault("AAX6_V6_ACTIVE", "1")
MAX_TURNS = 16


def _load_env(repo):
    for name in (".env",):
        p = os.path.join(repo, name)
        if not os.path.exists(p):
            continue
        for ln in open(p):
            ln = ln.strip()
            if ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class GeminiCustomer:
    """Minimal debtor sim — mirrors simulation.customer: persona system prompt,
    ends the call by emitting [TASK_COMPLETED]."""
    MODEL = os.environ.get("AAX6_CUSTOMER_MODEL", "gemini-3.1-flash-lite-preview")

    def __init__(self, persona_prompt: str):
        from google import genai
        self._genai = genai
        self._client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        self._sys = (persona_prompt or "") + (
            "\n\nคุณคือลูกหนี้/ผู้รับสายในบทสนทนาโทรทวงหนี้ ตอบสั้นเป็นธรรมชาติแบบคุยโทรศัพท์ ตอบเป็นภาษาไทย.\n"
            "**อย่าเพิ่งจบบทสนทนา** — เจรจากับเจ้าหน้าที่ต่อไปตามบทบาทของคุณ (ต่อรอง/อธิบายเหตุผล/ถามรายละเอียด) "
            "อย่างน้อยหลายรอบ. ใส่ [TASK_COMPLETED] **เฉพาะเมื่อบทสนทนาจบจริงๆ เท่านั้น**: เช่น ตกลงนัดจ่าย/จ่ายแล้ว, "
            "ปฏิเสธเด็ดขาดหลังถูกโน้มน้าวแล้ว, หรือวางสายชัดเจน. ถ้าเจ้าหน้าที่ยังคุยอยู่และยังไม่ได้ข้อสรุป **ห้ามใส่** [TASK_COMPLETED]")
        self._history: list = []

    def reply(self, agent_text: str):
        from google.genai import types
        self._history.append({"role": "user", "parts": [{"text": agent_text}]})
        try:
            r = self._client.models.generate_content(
                model=self.MODEL,
                contents=self._history,
                config=types.GenerateContentConfig(
                    system_instruction=self._sys, temperature=0.7, max_output_tokens=200),
            )
            txt = (r.text or "").strip()
        except Exception as e:
            txt = "[TASK_COMPLETED]"
            print(f"  [customer error] {e}", file=sys.stderr)
        self._history.append({"role": "model", "parts": [{"text": txt}]})
        done = "[TASK_COMPLETED]" in txt
        return txt.replace("[TASK_COMPLETED]", "").strip(), done


def _hops_from_session_stream(stream_hops: list) -> list:
    """Map FlowLiveSession hop kinds -> flow_sim.run_conversation trajectory shape."""
    out = []
    for h in stream_hops:
        kind = h.get("kind")
        if kind == "tool_call":
            out.append({"role": "agent", "content": {"tool_call": h.get("name"), "args": h.get("args", {})}})
        elif kind == "reply":
            if not h.get("text") or h.get("filler"):  # UX filler is not agent speech
                continue
            out.append({"role": "agent", "content": h.get("text")})
        elif kind == "tool_result":
            out.append({"role": "system", "content": {"result": h.get("result")}})
    return out


async def _drain(aiter):
    got = []
    async for h in aiter:
        got.append(h)
    return got


async def run_one(pid: str, model: str, sem: asyncio.Semaphore):
    import demo.server.sessions as S
    async with sem:
        try:
            sess = S.FlowLiveSession(case_id=pid, model=model)
        except Exception as e:
            return {"id": pid, "full-trajectory": [], "_violations": ["init_error:%s" % e], "_meta": {}}
        persona_prompt = sess._case.get("user_system_prompt", "")
        cust = GeminiCustomer(persona_prompt)
        hops: list = []
        # opening (greeting)
        op = await _drain(sess.aiter_opening())
        hops += _hops_from_session_stream(op)
        agent_text = next((h["text"] for h in reversed(op) if h.get("kind") == "reply" and h.get("text")), "")
        violations = []
        for _turn_idx in range(MAX_TURNS):
            ctext, done = cust.reply(agent_text)
            hops.append({"role": "customer", "content": ctext})
            # let the agent respond at least once before honoring an end signal,
            # so wrong-number / early-refusal convos still capture the agent's close.
            if done and _turn_idx > 0:
                break
            try:
                turn_hops = await _drain(sess.aiter_turn(ctext))
            except Exception as e:
                import traceback; traceback.print_exc()
                violations.append("turn_error:%s" % e)
                break
            hops += _hops_from_session_stream(turn_hops)
            agent_text = next((h["text"] for h in reversed(turn_hops) if h.get("kind") == "reply" and h.get("text")), "")
            if getattr(sess, "done", False):
                break
            if not agent_text:
                violations.append("empty_agent_turn")
                break
        return {
            "id": pid, "flow_id": getattr(sess, "_company", "AEON") + "-flow",
            "customer_data": {k: v for k, v in sess.customer_data.items() if not str(k).startswith("_")},
            "agent_gender": sess.voice_gender,
            "scenario": sess._case.get("topic", "persona"),
            "full-trajectory": hops,
            "_violations": violations,
            "_meta": {"model": model, "turns": len([h for h in hops if h["role"] == "customer"])},
        }


async def main_async(args):
    personas = json.loads(open(args.personas, encoding="utf-8").read())
    if args.limit:
        personas = personas[: args.limit]
    ids = [p["id"] for p in personas]

    # monkeypatch the demo's case loader to serve our personas
    import demo.server.sessions as S
    pmap = {p["id"]: p for p in personas}
    _orig = S._load_test_case
    def _patched(cid):
        p = pmap.get(cid)
        if p is None:
            return _orig(cid)
        return {"id": cid, "customer_data": p["customer_data"],
                "user_system_prompt": p.get("user_system_prompt", ""),
                "topic": p.get("topic", "persona")}
    S._load_test_case = _patched
    S.FlowLiveSession._resolve_flow_case = staticmethod(lambda cid: cid)

    sem = asyncio.Semaphore(args.parallel)
    results = [None] * len(ids)

    async def _wrap(i, pid):
        results[i] = await run_one(pid, args.model, sem)
        done = sum(1 for r in results if r is not None)
        t = results[i]
        print(f"[{done}/{len(ids)}] {pid} hops={len(t['full-trajectory'])} viol={t['_violations']}")

    await asyncio.gather(*[_wrap(i, pid) for i, pid in enumerate(ids)])
    json.dump(results, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"wrote {len(results)} conversations -> {args.out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--personas", required=True)
    ap.add_argument("--model", default="sft_v11")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("-o", "--out", default="tmp/flow_v112_driver.json")
    args = ap.parse_args()
    _load_env(os.getcwd())
    if not os.environ.get("GOOGLE_API_KEY"):
        raise SystemExit("GOOGLE_API_KEY not set (need .env for the customer sim)")
    os.environ.setdefault("AAX6_VLLM_MODEL", args.model)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
