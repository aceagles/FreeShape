# Sourced by e2e.sh and shots.sh (POSIX sh). Needs ROOT (the repo root) set by the caller.
#
#   web_build      builds the client into freeshape/static/app (needs web/node_modules: `npm ci`, done by the caller)
#   web_serve      copies the example Documents to a temporary folder and starts `freeshape serve` on the bracket
#                  Document at a free port; sets FS_BASE_URL. Stops with a message if it is not ready in 60 s.
#   a trap (set here) stops the server and removes the temporary folder on exit, failure or interrupt.
#
# Python: $FS_PYTHON if set; else the gate image's fs_py (OCP defaults to the 8.x venv); else python3.

WEB_TMP=""
SERVE_PID=""

web_cleanup() {
  status=$?
  trap - EXIT INT TERM
  if [ -n "$SERVE_PID" ]; then
    kill "$SERVE_PID" 2>/dev/null
    # give it a second to leave, then make sure
    n=0
    while kill -0 "$SERVE_PID" 2>/dev/null && [ "$n" -lt 20 ]; do sleep 0.1; n=$((n + 1)); done
    kill -9 "$SERVE_PID" 2>/dev/null
    wait "$SERVE_PID" 2>/dev/null
  fi
  [ -n "$WEB_TMP" ] && rm -rf "$WEB_TMP"
  exit "$status"
}
trap web_cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

web_build() {
  if [ ! -d "$ROOT/web/node_modules" ]; then
    echo "web/node_modules is missing: run 'npm --prefix web ci' first" >&2
    exit 2
  fi
  echo "== building the client"
  log=$(mktemp) || exit 2
  if ! npm --prefix "$ROOT/web" run build >"$log" 2>&1; then
    cat "$log" >&2
    rm -f "$log"
    echo "client build failed" >&2
    exit 1
  fi
  rm -f "$log"
}

# The gate image keeps the browser's fonts out of /usr/share/fonts (Dockerfile.harness): point fontconfig at them for
# the browser. Call it after web_serve, so the server's Python keeps the Python side's fonts.
web_browser_fonts() {
  if [ -f /opt/browser-fonts.conf ]; then
    FONTCONFIG_FILE=/opt/browser-fonts.conf
    export FONTCONFIG_FILE
  fi
}

web_serve() {
  WEB_TMP=$(mktemp -d) || exit 2
  cp -R "$ROOT/examples/documents" "$WEB_TMP/documents" || exit 2
  if [ -n "${FS_PYTHON:-}" ]; then
    py="$FS_PYTHON"
  elif command -v fs_py >/dev/null 2>&1; then
    OCP=${OCP:-ocp8.0.1.1.0}
    export OCP
    py=fs_py
  else
    py=python3
  fi
  echo "== starting freeshape serve on a copy of examples/documents/bracket"
  (cd "$ROOT" && PYTHONDONTWRITEBYTECODE=1 exec $py -m freeshape serve "$WEB_TMP/documents/bracket" --port 0) \
    >"$WEB_TMP/serve.log" 2>&1 &
  SERVE_PID=$!
  n=0
  port=""
  while [ "$n" -lt 600 ]; do
    port=$(sed -n 's/^FREESHAPE_SERVE_READY port=\([0-9][0-9]*\).*/\1/p' "$WEB_TMP/serve.log" | head -n 1)
    [ -n "$port" ] && break
    kill -0 "$SERVE_PID" 2>/dev/null || break
    sleep 0.1
    n=$((n + 1))
  done
  if [ -z "$port" ]; then
    echo "freeshape serve did not become ready; its output:" >&2
    cat "$WEB_TMP/serve.log" >&2
    exit 1
  fi
  FS_BASE_URL="http://127.0.0.1:$port"
  export FS_BASE_URL
  echo "== serving at $FS_BASE_URL"
}
