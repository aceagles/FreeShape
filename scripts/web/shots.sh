#!/bin/sh
# Whole-screen screenshots, to look at (not to compare): builds the client, serves a copy of the example Documents,
# and under WebKit drives the client into each state in web/e2e/shots.ts at 1024 x 768 and 1366 x 1024. Writes to
# web/shots/out/ (not committed): <state>-<w>x<h>.png has the layout mock (web/design/layout.html) on the left and
# the client on the right, <state>-<w>x<h>.client.png is the client alone.
#
#   sh scripts/web/shots.sh              every state
#   sh scripts/web/shots.sh browsing     only the named states
#
# A state whose steps fail is reported and skipped, the others are still captured. Exit non-zero only when a state
# could not be captured, never for a visual difference. Server and browser are stopped on exit, failure or interrupt.
# Runs in the gate image with no network (see scripts/web/e2e.sh), or anywhere with Node 22+ and the freeshape env.
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
. "$ROOT/scripts/web/common.sh"

web_build
web_serve
cd "$ROOT/web" || exit 2
rm -f shots/out/*.png
node e2e/shots.run.ts "$@"
