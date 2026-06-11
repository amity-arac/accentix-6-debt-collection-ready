#!/usr/bin/env node
/**
 * Flatten the v6 pre-script catalog into a {text_id: template} map for the
 * frontend to import lazily. Reads the catalog from the repo's data/
 * directory and writes JSON into src/data/.
 *
 * Run via `npm run predev` and `npm run prebuild`.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = resolve(__dirname, "..");
const REPO_ROOT = resolve(FRONTEND_ROOT, "..", "..");
const CATALOG = join(REPO_ROOT, "data", "pre-scripts", "v6_pre_script_database.json");
const OUT = join(FRONTEND_ROOT, "src", "data", "v6_templates.json");

async function main() {
  const raw = await readFile(CATALOG, "utf-8");
  const catalog = JSON.parse(raw);
  if (!Array.isArray(catalog)) {
    throw new Error(`expected an array at ${CATALOG}`);
  }
  const out = {};
  for (const entry of catalog) {
    if (entry && typeof entry === "object" && entry.text_id != null) {
      out[String(entry.text_id)] = String(entry.template ?? "");
    }
  }
  await mkdir(dirname(OUT), { recursive: true });
  await writeFile(OUT, JSON.stringify(out, null, 2) + "\n", "utf-8");
  // eslint-disable-next-line no-console
  console.log(`[build-templates] wrote ${Object.keys(out).length} entries → ${OUT}`);
}

main().catch((err) => {
  // eslint-disable-next-line no-console
  console.error("[build-templates] failed:", err);
  process.exit(1);
});
