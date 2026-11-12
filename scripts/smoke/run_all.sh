#!/bin/sh
# The cumulative smoke gate: run every scripts/smoke/s*.py, in name order, on both OCP venvs.
# Fails if any script exits non-zero, or if there are no scripts to run.
#
# nightshift runs this as the `e2e` capability (harness.jsonc). A slice adds its smoke by adding a file
# here named s<n>_<topic>.py; nothing else needs editing. Order is byte order (LC_ALL=C), so
# s00_baseline.py runs before s0_headless.py, and s10 would run before s2: zero-pad if it matters.
#
# To add a smoke, a slice commits one file here, scripts/smoke/s<n>_<topic>.py, that exits non-zero on
# failure and prints one "S<n> OK" line on success. Do not edit this loop to list it. With only
# s00_baseline.py present the gate passes; with no s*.py at all it fails.
#
# Needs `both` and `fs_py` on PATH. Dockerfile.harness bakes them into the harness image.
set -u
LC_ALL=C
export LC_ALL

cd "$(dirname "$0")/../.." || exit 2

for tool in both fs_py; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "run_all.sh: '$tool' is not on PATH (it is provided by the image built from Dockerfile.harness)" >&2
    exit 2
  fi
done

count=0
for script in scripts/smoke/s*.py; do
  [ -f "$script" ] || continue
  count=$((count + 1))
  both fs_py "$script" || {
    rc=$?
    echo "SMOKE FAILED: $script (exit $rc)" >&2
    exit "$rc"
  }
done

if [ "$count" -eq 0 ]; then
  echo "SMOKE FAILED: no scripts/smoke/s*.py found" >&2
  exit 1
fi
echo "SMOKE OK: $count script(s) on both venvs"
