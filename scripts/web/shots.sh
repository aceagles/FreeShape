#!/bin/sh
# Whole-screen screenshots, to look at (not to compare): builds the client, serves a copy of the example Documents,
# and under WebKit drives the client into each state in web/e2e/shots.ts at 1024 x 768 and 1366 x 1024. Writes to
# web/shots/out/ (not committed): <state>-<w>x<h>.png has the layout mock (web/design/layout.html) on the left and
# the client on the right, <state>-<w>x<h>.client.png is the client alone.
#
#   sh scripts/web/shots.sh              every state
#   sh scripts/web/shots.sh browsing     only the named states
#
# A Desktop-mode state that names a desktop reference (`desktop` in shots.ts) has that shot of the desktop program on the
# left instead of the mock. scripts/web/desktop_shots.py writes them first, to web/shots/desktop/ (not committed); it
# starts the desktop MainWindow offscreen, so it needs the freeshape env (the gate image has it, see scripts/web/common.sh).
#
# A state whose steps fail is reported and skipped, the others are still captured. Exit non-zero only when a state
# could not be captured, never for a visual difference. Server and browser are stopped on exit, failure or interrupt.
# Runs in the gate image with no network (see scripts/web/e2e.sh), or anywhere with Node 22+ and the freeshape env.
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
. "$ROOT/scripts/web/common.sh"

web_build
web_serve
# The desktop reference shots, before the browser's fonts are put in place (the desktop takes the Python side's). With
# no argument every desktop state is written; with arguments only when one names a desktop state (desktop-...).
# A desktop state that fails does not stop the client shots; the exit status carries it at the end.
desktop_status=0
want_desktop=1
if [ $# -gt 0 ]; then
  want_desktop=0
  for a in "$@"; do
    case "$a" in desktop*) want_desktop=1 ;; esac
  done
fi
if [ "$want_desktop" = 1 ]; then
  echo "== the desktop program, offscreen (web/shots/desktop/)"
  rm -f "$ROOT/web/shots/desktop/"*.png
  desktop_log="$WEB_TMP/desktop.log"
  (cd "$ROOT" && PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen $FS_PY scripts/web/desktop_shots.py) >"$desktop_log" 2>&1 \
    || desktop_status=1
  grep -v 'propagateSizeHints\|QOpenGLWidget\|does not support raise\|does not support grabbing' "$desktop_log"
fi
web_browser_fonts
cd "$ROOT/web" || exit 2
rm -f shots/out/*.png
node e2e/shots.run.ts "$@" || desktop_status=1
exit "$desktop_status"
