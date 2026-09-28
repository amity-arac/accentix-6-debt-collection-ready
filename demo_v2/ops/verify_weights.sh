#!/usr/bin/env bash
# Verify a downloaded checkpoint against the sha256 Hugging Face itself reports.
#
#   bash demo_v2/ops/verify_weights.sh <dir> [repo_id] [prefix]
#   bash demo_v2/ops/verify_weights.sh <dir> --emit > checksums.txt
#
# There is no checksums.txt in this repo on purpose. A static list goes stale the
# moment a checkpoint is re-uploaded and then quietly certifies the wrong weights;
# the hub's own LFS oid cannot. Use --emit only to hand a list to somebody who has
# no network at all.
#
# Why this exists: a transfer once arrived sparse -- `du` said 12 GB where `ls` said
# 18.8 GB -- and the model served fluent nonsense. The runtime was blamed for a
# release cycle. Check the weights before the runtime, every time.
set -euo pipefail

DIR="${1:?usage: verify_weights.sh <dir> [repo_id] [prefix]}"
[ -d "$DIR" ] || { echo "not a directory: $DIR" >&2; exit 2; }

if [ "${2:-}" = "--emit" ]; then
    find "$DIR" -type f | sort | while read -r f; do
        printf '%s  %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "${f#"$DIR"/}"
    done
    exit 0
fi

REPO="${2:-amityco/aax6}"
# default prefix: the last two path segments, which is how snapshot_download lays
# `models/<name>/` out under --local-dir
# `${3-...}` not `${3:-...}`: an explicitly empty prefix means "files sit at the
# repo root", and `:-` would silently replace it with the default.
PREFIX="${3-$(basename "$(dirname "$DIR")")/$(basename "$DIR")}"
PY="${PY:-python3}"

"$PY" - "$DIR" "$REPO" "$PREFIX" <<'PYEOF'
import hashlib, pathlib, sys
from huggingface_hub import HfApi

dir_, repo, prefix = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3].strip("/")
try:
    info = HfApi().repo_info(repo, files_metadata=True)
except Exception as e:                                   # noqa: BLE001
    sys.exit(f"cannot read {repo}: {type(e).__name__}. A private repo needs HF_TOKEN "
             f"with read access to that org; with no network at all, ask the sender "
             f"to run `--emit` and check the list by hand.")

want = {}
for s in info.siblings:
    if prefix and not s.rfilename.startswith(prefix + "/"):
        continue
    lfs = getattr(s, "lfs", None)
    oid = (getattr(lfs, "sha256", None) or (lfs or {}).get("sha256")) if lfs else None
    # A file small enough to be stored raw has no LFS sha256 -- fall back to the git
    # blob id, which is a sha1 over b"blob <len>\0" + content and is just as binding.
    # Skipping them instead is how a corrupt tokenizer or config passes a "clean" run.
    key = s.rfilename[len(prefix) + 1:] if prefix else s.rfilename
    want[key] = ("sha256", oid) if oid else ("blob", getattr(s, "blob_id", None))
if not want:
    sys.exit(f"nothing in {repo} under {prefix!r} — wrong prefix? pass it as argument 3")

ok = bad = skipped = missing = 0
for name, (kind, oid) in sorted(want.items()):
    p = dir_ / name
    if not p.exists():
        print(f"  MISSING   {name}"); missing += 1; continue
    if oid is None:
        print(f"  ?         {name}  (the hub reports no hash)"); skipped += 1; continue
    if kind == "sha256":
        h = hashlib.sha256()
    else:
        h = hashlib.sha1(b"blob %d\0" % p.stat().st_size)
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8 << 20), b""):
            h.update(chunk)
    if h.hexdigest() == oid:
        print(f"  ok        {name}"); ok += 1
    else:
        print(f"  CORRUPT   {name}\n              hub  {oid}\n              disk {h.hexdigest()}")
        bad += 1

# `.cache/huggingface/` is snapshot_download's own bookkeeping, not a stray weight
extra = [p for p in dir_.rglob("*")
         if p.is_file() and ".cache/huggingface" not in p.as_posix()
         and str(p.relative_to(dir_)) not in want]
for p in extra:
    print(f"  extra     {p.relative_to(dir_)}  (not in {repo})")

print(f"\n{ok} ok · {bad} corrupt · {missing} missing · {skipped} not hashed by the hub "
      f"· {len(extra)} extra")
sys.exit(1 if (bad or missing) else 0)
PYEOF
