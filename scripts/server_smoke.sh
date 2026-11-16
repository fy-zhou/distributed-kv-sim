#!/usr/bin/env bash
# CPU-only, no sudo, no GPU workload. Keeps all outputs on user's partition.
set -euo pipefail
PARTITION="${FY_PARTITION:-/state/partition/fyzhou}"
ROOT="${1:-$PARTITION/distributed-kv-sim}"
cd "$ROOT"
PY="${PYTHON:-python3}"
echo "Python: $("$PY" --version)"
echo "Repository: $PWD"
"$PY" -m compileall -q kv_sim scripts tests
"$PY" -m unittest discover -s tests -v
mkdir -p "$PARTITION/results/distributed-kv-sim"
"$PY" scripts/run.py \
  --nodes 4 --requests 200 --families 8 \
  --shared-blocks 32 --cache-blocks 256 \
  --initial-occupancy 1.0 --arrival-ms 12 \
  --out "$PARTITION/results/distributed-kv-sim/smoke"
"$PY" - "$PARTITION/results/distributed-kv-sim/smoke" <<'PYCODE'
import csv, pathlib, sys
p = pathlib.Path(sys.argv[1])
summary = list(csv.DictReader((p / "summary.csv").open()))
rows = list(csv.DictReader((p / "requests.csv").open()))
assert len(summary) == 4, "expected four policies"
assert len(rows) == 800, "expected 200 x four requests"
assert all(float(r["ttft_ms"]) >= 0 for r in rows)
assert all(int(r["total_hit_tokens"]) + int(r["recomputed_tokens"]) == int(r["length_tokens"]) for r in rows)
assert any(int(r["remote_hit_tokens"]) > 0 for r in rows), "expected remote transfers in smoke"
print("PASS: 4 policies, 800 rows, remote transfers and token conservation")
print("Summary:", p / "summary.csv")
PYCODE
