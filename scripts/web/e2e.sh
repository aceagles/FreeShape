#!/bin/sh
# Browser smoke: builds the client, serves a copy of the example Documents, runs web/e2e/s<n>_*.spec.ts under WebKit
# (1024 x 768, touch), stops the server, and prints "WEB SMOKE S<n> OK" for each slice that passes.
#
#   sh scripts/web/e2e.sh        every slice that has a spec
#   sh scripts/web/e2e.sh 2      only slice 2 (also S2 or s2)
#
# PW_CHROMIUM=1 adds a second pass under Chromium that is reported ("CHROMIUM S<n> ...") and never gates.
# Exit 0 only if every slice run passed. Runs in the gate image with no network (the repo mounted, web/node_modules
# installed on the runner); the server and any browser are stopped on exit, failure or interrupt.
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
. "$ROOT/scripts/web/common.sh"

only=$(printf '%s' "${1:-}" | tr -d 'Ss')
slices=""
for f in "$ROOT"/web/e2e/s[0-9]*_*.spec.ts; do
  [ -e "$f" ] || continue
  n=$(basename "$f" | sed -n 's/^s\([0-9][0-9]*\)_.*/\1/p')
  [ -n "$n" ] || continue
  [ -z "$only" ] || [ "$n" = "$only" ] || continue
  case " $slices " in *" $n "*) ;; *) slices="$slices $n" ;; esac
done
slices=$(printf '%s\n' $slices | sort -n | tr '\n' ' ')
if [ -z "$slices" ]; then
  echo "no spec web/e2e/s${only:-<n>}_*.spec.ts" >&2
  exit 2
fi

web_build
web_serve
web_browser_fonts

failed=0
cd "$ROOT/web" || exit 2
for n in $slices; do
  echo "== slice S$n under WebKit"
  if npx playwright test --config playwright.serve.config.ts --project=webkit "e2e/s${n}_"; then
    echo "WEB SMOKE S$n OK"
  else
    echo "WEB SMOKE S$n FAILED" >&2
    failed=1
  fi
  if [ -n "${PW_CHROMIUM:-}" ]; then
    if PW_CHROMIUM=1 npx playwright test --config playwright.serve.config.ts --project=chromium "e2e/s${n}_"; then
      echo "CHROMIUM S$n OK (optional, not gating)"
    else
      echo "CHROMIUM S$n FAILED (optional, not gating)"
    fi
  fi
done
exit "$failed"
