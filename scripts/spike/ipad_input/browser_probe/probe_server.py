"""Serve the iPad input probe page and collect what it reports. Standard library only.

    python probe_server.py serve [--port 3060] [--log events.jsonl]
    python probe_server.py summary [--log events.jsonl]

`serve` binds to 127.0.0.1 only: the page is reached from the iPad through the preview tunnel. Each event the page
posts is appended to the log as one JSON line. `summary` prints the baseline table: per gesture, what the browser
reported.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_BODY = 4 * 1024 * 1024


def make_handler(log_path: Path):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.split("?")[0] in ("/", "/index.html"):
                self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found")

        def do_POST(self):
            if self.path.split("?")[0] != "/log":
                self._send(404, b"not found")
                return
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                self._send(413, b"bad length")
                return
            try:
                doc = json.loads(self.rfile.read(n))
                events = doc["events"]
                if not isinstance(events, list):
                    raise ValueError("events is not a list")
            except (ValueError, KeyError, TypeError) as err:
                self._send(400, str(err).encode())
                return
            now = time.time()
            ua = str(doc.get("ua", ""))
            with lock, log_path.open("a", encoding="utf-8") as f:
                for e in events:
                    if isinstance(e, dict):
                        e["recv"] = now
                        e["ua"] = ua
                        f.write(json.dumps(e, separators=(",", ":")) + "\n")
            self._send(200, b"ok")

        def log_message(self, fmt, *args):
            if self.command != "POST":
                super().log_message(fmt, *args)

    return Handler


def summarise(log_path: Path) -> str:
    """The baseline table as text: one block per gesture."""
    by = defaultdict(list)
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            e = json.loads(line)
            by[e.get("gesture")].append(e)
    out = []
    for gesture, evs in by.items():
        ptr = [e for e in evs if str(e.get("ev", "")).startswith("pointer")]
        types = sorted({e["pt"] for e in ptr if e.get("pt")})
        down = [e for e in ptr if e.get("b")]
        hover = defaultdict(int)          # moves with no button / contact, by pointer type
        for e in ptr:
            if e["ev"] == "pointermove" and not e.get("b"):
                hover[e.get("pt")] += 1
        pressure = defaultdict(set)
        for e in down:
            pressure[e.get("pt")].add(round(float(e.get("p") or 0), 3))
        together = sorted({e["downTypes"] for e in ptr if "+" in str(e.get("downTypes", ""))})
        counts = defaultdict(int)
        for e in evs:
            counts[e.get("ev")] += 1
        out.append("== %s" % gesture)
        out.append("  pointer types: %s" % (", ".join(types) or "none"))
        out.append("  most pointers down at once: %d" % max([e.get("down", 0) for e in ptr] or [0]))
        out.append("  max touches in a TouchEvent: %d" % max([e.get("n", 0) for e in evs] or [0]))
        out.append("  touch kinds: %s" % (", ".join(sorted({k for e in evs for k in str(e.get("kinds", "")).split(",")
                                                               if k})) or "-"))
        for pt, vals in sorted(pressure.items()):
            out.append("  pressure (%s): %d distinct values, %.3f to %.3f" % (pt, len(vals), min(vals), max(vals)))
        out.append("  moves with nothing down (hover): %s" % (
            ", ".join("%s %d" % (k, v) for k, v in sorted(hover.items())) or "none"))
        out.append("  pen and finger down together: %s" % (", ".join(together) or "no"))
        out.append("  events: %s" % "  ".join("%s %d" % (k, v) for k, v in sorted(counts.items())))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=("serve", "summary"))
    ap.add_argument("--port", type=int, default=3060)
    ap.add_argument("--log", type=Path, default=HERE / "events.jsonl")
    a = ap.parse_args(argv)
    if a.mode == "summary":
        if not a.log.exists():
            print("no log at %s" % a.log, file=sys.stderr)
            return 1
        print(summarise(a.log))
        return 0
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(a.log))
    print("probe on http://127.0.0.1:%d  log %s" % (a.port, a.log), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
