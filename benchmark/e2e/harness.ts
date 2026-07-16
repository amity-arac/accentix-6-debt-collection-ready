/*
 * End-to-end voice-latency benchmark for the AAX6 demo.
 *
 * Drives the REAL demo in a headless Chromium against a deployed (or local) URL,
 * feeds fixed Thai WAV clips through the actual mic pipeline, and harvests the
 * demo's OWN per-turn latency numbers (VAD / STT / LLM / TTS / end-to-end) from
 * the `?bench=1` console telemetry emitted by demo/frontend/src/latency.ts.
 *
 * There are NO models and NO constants here: every number is exactly what the
 * demo's control bar measures during a real session, just automated over N turns
 * and aggregated (p50 / p95 / mean). That is the point — the figures you present
 * are the demo's, captured from the demo, on the deployment you point this at.
 *
 * HOW THE AUDIO GETS IN
 *   We override navigator.mediaDevices.getUserMedia (via addInitScript, before any
 *   page script runs) to return a synthetic MediaStream driven by an
 *   AudioBufferSourceNode -> MediaStreamDestination. The demo's real AudioWorklet
 *   downsamples and streams it over the real WebSocket exactly as a live mic would
 *   — only the sound source is synthetic. A per-turn window.__benchPlayPCM(clip)
 *   plays one clip on demand; between clips the mic is silent, so the server-side
 *   Silero VAD segments each utterance into a turn just like a real call. One
 *   persistent browser session is kept for the whole run so realistic warm/idle
 *   connection state (and thus the real idle-reconnect behaviour) is preserved.
 *
 * PREREQUISITES (read benchmark/e2e/README.md)
 *   - The demo must be running in LIVE mode (AAX6_DEMO_MODE=live) at --url, with
 *     the instrumented frontend deployed (the ?bench=1 telemetry hook must be in
 *     the built bundle).
 *   - Run from a network location representative of your users — the double-hop
 *     RTT to the STT/LLM/TTS backends is part of the real number.
 *
 * USAGE
 *   npm install && npx playwright install chromium
 *   npx tsx harness.ts --url https://<deployed-demo> --turns 20
 *   (see parseArgs below for all flags)
 */

import { chromium, type ConsoleMessage, type Browser } from "playwright";
import { readFileSync, readdirSync, writeFileSync, mkdirSync } from "node:fs";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

// ---------------------------------------------------------------------------
// The record shape emitted by latency.ts (TurnLatency + `complete`).
// ---------------------------------------------------------------------------
type TurnRecord = {
  seq: number;
  viaMic: boolean;
  vadMs: number | null;
  sttMs: number | null;
  llmMs: number | null;
  ttsMs: number | null;
  llmHops: number | null;
  sttPerceivedMs: number | null;
  llmTtftMs: number | null;
  endToEndMs: number | null;
  cache: "hit" | "miss" | null; // TTS cache state (Server-Timing) — verify cold runs
  complete: boolean;
};

type Args = {
  url: string;
  clipsDir: string;
  turns: number;
  warmup: number;
  resetEvery: number;
  gapMs: number;
  turnTimeoutMs: number;
  agent: string | null;
  headed: boolean;
  outDir: string;
};

const HERE = dirname(fileURLToPath(import.meta.url));

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------
function parseArgs(argv: string[]): Args {
  const get = (flag: string): string | undefined => {
    const i = argv.indexOf(flag);
    return i !== -1 && i + 1 < argv.length ? argv[i + 1] : undefined;
  };
  const has = (flag: string): boolean => argv.includes(flag);

  const url = get("--url");
  if (!url) {
    console.error(
      "ERROR: --url is required (the demo URL to drive, e.g. https://your-demo.example).\n" +
        "The demo must be in LIVE mode with the ?bench=1 telemetry hook in its built bundle.",
    );
    process.exit(2);
  }
  return {
    url,
    // Default: the repo's Thai WAV dataset used by scripts/measure_ttfa.py.
    clipsDir: resolve(HERE, get("--clips") ?? "../../scripts/thai-wav-dataset"),
    turns: Number(get("--turns") ?? 20),
    warmup: Number(get("--warmup") ?? 1),
    // Start a fresh conversation every N measured turns. A debt-collection call
    // is only a handful of coherent turns, so one long session drifts
    // out-of-distribution and inflates LLM latency (the agent rambles to its
    // token cap). 0 = never reset (one continuous session). Needs the demo's
    // ?bench=1 __aax6Reset hook in the deployed build.
    resetEvery: Number(get("--reset-every") ?? 8),
    gapMs: Number(get("--gap-ms") ?? 4000),
    turnTimeoutMs: Number(get("--turn-timeout-ms") ?? 45000),
    agent: get("--agent") ?? null, // e.g. "qwen" | "gemini"; null = leave as-is
    headed: has("--headed"),
    outDir: resolve(HERE, get("--out") ?? "./results"),
  };
}

// ---------------------------------------------------------------------------
// WAV -> mono 16-bit PCM (little-endian) + sample rate. Chunk-walks so fmt/data
// order and padding don't matter; downmixes stereo.
// ---------------------------------------------------------------------------
function parseWav(buf: Buffer): { sampleRate: number; pcm: Buffer } {
  if (buf.toString("ascii", 0, 4) !== "RIFF" || buf.toString("ascii", 8, 12) !== "WAVE") {
    throw new Error("not a RIFF/WAVE file");
  }
  let offset = 12;
  let fmt: { format: number; channels: number; sampleRate: number; bits: number } | null = null;
  let data: Buffer | null = null;
  while (offset + 8 <= buf.length) {
    const id = buf.toString("ascii", offset, offset + 4);
    const size = buf.readUInt32LE(offset + 4);
    const body = offset + 8;
    if (id === "fmt ") {
      fmt = {
        format: buf.readUInt16LE(body),
        channels: buf.readUInt16LE(body + 2),
        sampleRate: buf.readUInt32LE(body + 4),
        bits: buf.readUInt16LE(body + 14),
      };
    } else if (id === "data") {
      data = buf.subarray(body, Math.min(body + size, buf.length));
    }
    offset = body + size + (size % 2); // word-aligned chunks
  }
  if (!fmt || !data) throw new Error("missing fmt/data chunk");
  if (fmt.bits !== 16) throw new Error(`only 16-bit PCM supported (got ${fmt.bits}-bit)`);
  if (fmt.format !== 1 && fmt.format !== 0xfffe) {
    throw new Error(`only PCM WAV supported (fmt code ${fmt.format})`);
  }
  if (fmt.channels === 1) return { sampleRate: fmt.sampleRate, pcm: data };
  if (fmt.channels === 2) {
    const n = Math.floor(data.length / 4);
    const out = Buffer.alloc(n * 2);
    for (let i = 0; i < n; i++) {
      const l = data.readInt16LE(i * 4);
      const r = data.readInt16LE(i * 4 + 2);
      out.writeInt16LE((l + r) >> 1, i * 2);
    }
    return { sampleRate: fmt.sampleRate, pcm: out };
  }
  throw new Error(`unsupported channel count ${fmt.channels}`);
}

// ---------------------------------------------------------------------------
// Injected into the page BEFORE its scripts run: a synthetic mic + a per-turn
// clip player. getUserMedia returns a CLONED track each call so the demo's
// requestMicPermission probe (which stops its stream) can't kill the real one.
// ---------------------------------------------------------------------------
const INIT_SCRIPT = `
(() => {
  const md = navigator.mediaDevices;
  if (!md || !md.getUserMedia) return;
  const orig = md.getUserMedia.bind(md);
  let ctx = null, dest = null;
  const ensure = () => {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      ctx = new AC();
      dest = ctx.createMediaStreamDestination();
    }
    return ctx;
  };
  md.getUserMedia = async (constraints) => {
    if (constraints && constraints.audio) {
      ensure();
      // Hand out an independent clone so callers that stop() their stream (e.g. a
      // permission probe) don't stop the shared destination track.
      return new MediaStream([dest.stream.getAudioTracks()[0].clone()]);
    }
    return orig(constraints);
  };
  // Play one clip (base64 of little-endian int16 mono PCM) into the synthetic
  // mic. Resolves when the clip finishes playing.
  window.__benchPlayPCM = async (b64, sampleRate) => {
    ensure();
    try { await ctx.resume(); } catch (e) { /* autoplay: launched with no-gesture policy */ }
    const bin = atob(b64);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    const i16 = new Int16Array(u8.buffer, 0, u8.byteLength >> 1);
    const audioBuf = ctx.createBuffer(1, i16.length, sampleRate);
    const ch = audioBuf.getChannelData(0);
    for (let i = 0; i < i16.length; i++) { const s = i16[i]; ch[i] = s < 0 ? s / 0x8000 : s / 0x7fff; }
    const src = ctx.createBufferSource();
    src.buffer = audioBuf;
    src.connect(dest);
    await new Promise((res) => { src.onended = () => res(true); src.start(); });
  };
})();
`;

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function pct(sortedAsc: number[], p: number): number {
  if (sortedAsc.length === 0) return NaN;
  if (sortedAsc.length === 1) return sortedAsc[0];
  const rank = (p / 100) * (sortedAsc.length - 1);
  const lo = Math.floor(rank);
  const hi = Math.ceil(rank);
  if (lo === hi) return sortedAsc[lo];
  return sortedAsc[lo] + (sortedAsc[hi] - sortedAsc[lo]) * (rank - lo);
}

function summarize(rows: TurnRecord[], key: keyof TurnRecord): {
  n: number;
  p50: number;
  p95: number;
  mean: number;
} {
  const vals = rows
    .map((r) => r[key])
    .filter((v): v is number => typeof v === "number" && !Number.isNaN(v))
    .sort((a, b) => a - b);
  const mean = vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : NaN;
  return { n: vals.length, p50: pct(vals, 50), p95: pct(vals, 95), mean };
}

const STAGES: { key: keyof TurnRecord; label: string }[] = [
  { key: "vadMs", label: "VAD (endpoint hang)" },
  { key: "sttMs", label: "STT (recognize)" },
  { key: "llmMs", label: "LLM (model hops)" },
  { key: "ttsMs", label: "TTS (client 1st audio)" },
  { key: "endToEndMs", label: "END-TO-END (heard)" },
  { key: "sttPerceivedMs", label: "  STT perceived (incl WS)" },
  { key: "llmTtftMs", label: "  LLM first-hop (perceived)" },
];

function fmt(n: number): string {
  return Number.isNaN(n) ? "   —" : Math.round(n).toString();
}

// TTS cache hit-rate over the rows whose cache state is known. A cold benchmark
// run (AAX6_TTS_CACHE=0) should read 0% — proof the TTS numbers are true synth,
// not cross-turn cache hits from a repetitive clip set.
function cacheStats(rows: TurnRecord[]): { hits: number; measured: number; pct: number } {
  const measured = rows.filter((r) => r.cache === "hit" || r.cache === "miss");
  const hits = measured.filter((r) => r.cache === "hit").length;
  return {
    hits,
    measured: measured.length,
    pct: measured.length ? (100 * hits) / measured.length : NaN,
  };
}

function printTable(title: string, rows: TurnRecord[]): void {
  console.log(`\n${title}  (n=${rows.length} turns)`);
  console.log("-".repeat(72));
  console.log(`${"stage".padEnd(30)}${"p50".padStart(9)}${"p95".padStart(9)}${"mean".padStart(9)}`);
  console.log("-".repeat(72));
  for (const { key, label } of STAGES) {
    const s = summarize(rows, key);
    console.log(
      `${label.padEnd(30)}${fmt(s.p50).padStart(9)}${fmt(s.p95).padStart(9)}${fmt(s.mean).padStart(9)}`,
    );
  }
  console.log("-".repeat(72));
  const c = cacheStats(rows);
  const rate = Number.isNaN(c.pct)
    ? "unknown (no Server-Timing header — rebuild the frontend)"
    : `${Math.round(c.pct)}% hit (${c.hits}/${c.measured})` +
      (c.pct === 0 ? "  ✓ cold run — TTS is true synth" : "");
  console.log(`TTS cache: ${rate}`);
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));

  const clips = readdirSync(args.clipsDir)
    .filter((f) => f.toLowerCase().endsWith(".wav"))
    .sort()
    .map((f) => join(args.clipsDir, f));
  if (clips.length === 0) {
    console.error(`ERROR: no .wav clips found in ${args.clipsDir}`);
    process.exit(2);
  }

  console.log("AAX6 end-to-end latency benchmark");
  console.log(`  url        : ${args.url}`);
  console.log(`  clips      : ${clips.length} in ${args.clipsDir}`);
  console.log(`  turns      : ${args.turns} (+${args.warmup} warmup, discarded)`);
  console.log(
    `  reset-every: ${args.resetEvery > 0 ? `${args.resetEvery} turns (fresh call)` : "off (one continuous session)"}`,
  );
  console.log(`  inter-turn : ${args.gapMs}ms idle gap`);
  console.log(`  agent      : ${args.agent ?? "(demo default)"}`);
  console.log("");

  // seq -> latest emitted record (final emission per turn is `complete`).
  const records = new Map<number, TurnRecord>();
  let browser: Browser | null = null;

  try {
    browser = await chromium.launch({
      headless: !args.headed,
      args: [
        "--autoplay-policy=no-user-gesture-required",
        "--use-fake-ui-for-media-stream",
      ],
    });
    const context = await browser.newContext({
      permissions: ["microphone"],
      // The demo's vite dev server uses a self-signed cert (HTTPS is needed for
      // the mic's secure context on non-localhost origins). Accept it so an
      // https:// --url doesn't fail with ERR_CERT_AUTHORITY_INVALID.
      ignoreHTTPSErrors: true,
    });
    const page = await context.newPage();
    await page.addInitScript(INIT_SCRIPT);

    page.on("console", (msg: ConsoleMessage) => {
      const text = msg.text();
      const idx = text.indexOf("[aax6-latency]");
      if (idx === -1) return;
      const brace = text.indexOf("{", idx);
      if (brace === -1) return;
      try {
        const rec = JSON.parse(text.slice(brace)) as TurnRecord;
        records.set(rec.seq, rec); // last emission per seq wins (final is complete)
      } catch {
        /* ignore malformed line */
      }
    });

    const benchUrl = args.url + (args.url.includes("?") ? "&" : "?") + "bench=1";
    await page.goto(benchUrl, { waitUntil: "domcontentloaded" });

    // Confirm the instrumented bundle is deployed (the hook exposes __aax6Bench).
    try {
      await page.waitForFunction(
        () => (window as unknown as { __aax6Bench?: boolean }).__aax6Bench === true,
        { timeout: 15000 },
      );
    } catch {
      console.error(
        "ERROR: the ?bench=1 telemetry hook was not found in the page (window.__aax6Bench).\n" +
          "Rebuild + redeploy the demo frontend so latency.ts's bench hook is in the bundle.",
      );
      process.exit(3);
    }

    // Optionally pick the agent (buttons are pre-start). Best-effort, by label.
    if (args.agent) {
      const label = args.agent.toLowerCase() === "gemini" ? "Gemini" : "Qwen";
      try {
        await page.getByRole("button", { name: new RegExp(label, "i") }).first().click({ timeout: 4000 });
      } catch {
        console.warn(`  (could not select agent "${args.agent}" — using demo default)`);
      }
    }

    // Start the call: the Start button enables once the session is ready.
    await page.waitForSelector(".btn.start:not([disabled])", { timeout: 30000 });
    await page.click(".btn.start");
    // Give the mic socket a moment to open + go live.
    await sleep(2000);

    // If resetting is on, confirm the reset hook exists in this build.
    if (args.resetEvery > 0) {
      const hasReset = await page.evaluate(
        () => typeof (window as unknown as { __aax6Reset?: unknown }).__aax6Reset === "function",
      );
      if (!hasReset) {
        console.warn(
          `  WARNING: --reset-every ${args.resetEvery} is set but window.__aax6Reset is missing in\n` +
            "  this build. Sessions will NOT reset (rebuild/redeploy the frontend, or pass\n" +
            "  --reset-every 0). LLM latency may drift up over a long session.",
        );
      }
    }

    const collected: TurnRecord[] = [];
    let lastSeq = 0;
    const total = args.warmup + args.turns;

    for (let i = 0; i < total; i++) {
      const clipPath = clips[i % clips.length];
      const { sampleRate, pcm } = parseWav(readFileSync(clipPath));
      const b64 = pcm.toString("base64");

      // Play the clip through the synthetic mic (resolves when it finishes).
      await page.evaluate(
        (a: { b64: string; sr: number }) =>
          (window as unknown as { __benchPlayPCM: (b: string, sr: number) => Promise<void> }).__benchPlayPCM(
            a.b64,
            a.sr,
          ),
        { b64, sr: sampleRate },
      );

      // Wait for this turn's completed record (seq beyond the last one).
      const rec = await waitForTurn(records, lastSeq, args.turnTimeoutMs).catch(
        (e: Error) => {
          console.warn(`  turn ${i + 1}: ${e.message} (clip ${clipPath.split("/").pop()})`);
          return null;
        },
      );

      if (rec) {
        lastSeq = rec.seq;
        const warm = i < args.warmup;
        console.log(
          `  turn ${String(i + 1).padStart(2)}${warm ? " (warmup)" : "        "}  ` +
            `VAD=${fmt(rec.vadMs ?? NaN).padStart(4)} STT=${fmt(rec.sttMs ?? NaN).padStart(5)} ` +
            `LLM=${fmt(rec.llmMs ?? NaN).padStart(5)} TTS=${fmt(rec.ttsMs ?? NaN).padStart(4)} ` +
            `E2E=${fmt(rec.endToEndMs ?? NaN).padStart(5)} hops=${rec.llmHops ?? "?"} ` +
            `cache=${rec.cache ?? "?"}`,
        );
        if (!warm) collected.push(rec);
      }

      // Between turns: start a fresh conversation every N measured turns (keeps
      // each turn at a realistic call depth), otherwise idle the inter-turn gap.
      if (i < total - 1) {
        const resetNow =
          args.resetEvery > 0 &&
          rec != null &&
          i >= args.warmup &&
          collected.length > 0 &&
          collected.length % args.resetEvery === 0;
        if (resetNow) {
          console.log(`  — new call (reset every ${args.resetEvery} turns) —`);
          try {
            await page.evaluate(() =>
              (window as unknown as { __aax6Reset?: () => Promise<void> }).__aax6Reset?.(),
            );
          } catch (e) {
            console.warn(`  reset failed: ${(e as Error).message}`);
          }
          await sleep(2500); // let the fresh session + mic settle before the next clip
        } else {
          await sleep(args.gapMs);
        }
      }
    }

    if (collected.length === 0) {
      console.error("\nNo completed turns captured. Nothing to report.");
      process.exit(1);
    }

    // Reports.
    printTable("ALL TURNS", collected);
    const toolTurns = collected.filter((r) => (r.llmHops ?? 1) > 1);
    const replyTurns = collected.filter((r) => (r.llmHops ?? 1) <= 1);
    if (toolTurns.length) printTable("TOOL TURNS (multi-hop)", toolTurns);
    if (replyTurns.length) printTable("REPLY-ONLY TURNS (single hop)", replyTurns);

    // Raw JSON for reproducibility / the exec deck.
    mkdirSync(args.outDir, { recursive: true });
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const outPath = join(args.outDir, `bench-${stamp}.json`);
    const summary: Record<string, ReturnType<typeof summarize>> = {};
    for (const { key } of STAGES) summary[String(key)] = summarize(collected, key);
    writeFileSync(
      outPath,
      JSON.stringify(
        {
          config: { ...args },
          capturedAt: stamp,
          turns: collected,
          summary,
          cache: {
            all: cacheStats(collected),
            tool: cacheStats(toolTurns),
            reply: cacheStats(replyTurns),
          },
          split: {
            tool: Object.fromEntries(STAGES.map((s) => [s.key, summarize(toolTurns, s.key)])),
            reply: Object.fromEntries(STAGES.map((s) => [s.key, summarize(replyTurns, s.key)])),
          },
        },
        null,
        2,
      ),
    );
    console.log(`\nRaw results written to ${outPath}`);
    console.log(
      "\nNOTE: every number above is the demo's OWN measurement (latency.ts), captured live —\n" +
        "no models, no constants. TTS is first-buffer-schedule (~80ms optimistic vs truly\n" +
        "audible), exactly as the in-app control bar defines it.",
    );
  } finally {
    if (browser) await browser.close();
  }
}

async function waitForTurn(
  records: Map<number, TurnRecord>,
  afterSeq: number,
  timeoutMs: number,
): Promise<TurnRecord> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    for (const rec of records.values()) {
      if (rec.seq > afterSeq && rec.complete) return rec;
    }
    await sleep(150);
  }
  throw new Error(`timed out waiting for a completed turn after seq ${afterSeq}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
