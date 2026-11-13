"""Round-trip cost of the process executor on the examples: one `submit_edit` (a dimension change) plus the
`outputs` that follow it, each tab in its own worker process (freeshape/engine/process.py).

    python scripts/bench_engine.py [repeats]

Prints one line per example with the median round trip in ms. Worker start-up (a Python start plus
`import OCP`) is paid once, at open, and is not part of the round trip; it is printed alongside.
"""
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

EXAMPLES = ("hinge_parts.fshape", "bracket.fshape")


def first_depth(path):
    """(feature id, depth) of the first extrude with a numeric depth: the dimension the benchmark edits."""
    with open(path) as fh:
        doc = json.load(fh)
    for f in doc["features"]:
        depth = f.get("params", {}).get("depth")
        if f.get("type") == "extrude" and isinstance(depth, (int, float)):
            return f["id"], float(depth)
    raise SystemExit(f"{path}: no extrude depth to edit")


def bench(engine, name, repeats):
    path = os.path.join(ROOT, "examples", name)
    fid, depth = first_depth(path)
    t0 = time.perf_counter()
    tab = engine.open_tab(path)
    engine.outputs(tab)
    opened = time.perf_counter() - t0
    times = []
    for i in range(repeats):
        value = depth * (1.01 + 0.01 * i)        # a new value every time: no rebuild or mesh cache can answer it
        t0 = time.perf_counter()
        engine.submit_edit(tab, {"op": "set_param", "feature": fid, "key": "depth", "value": value})
        out = engine.outputs(tab)
        times.append(time.perf_counter() - t0)
        if out.errors:
            raise SystemExit(f"{name}: {out.errors}")
    engine.close_tab(tab)
    return statistics.median(times) * 1000, opened * 1000, fid


def main():
    from freeshape.engine import ProcessEngine
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    with ProcessEngine() as engine:
        for name in EXAMPLES:
            ms, open_ms, fid = bench(engine, name, repeats)
            print(f"{name}: round trip {ms:.1f} ms  (set {fid} depth + outputs, median of {repeats}; "
                  f"open incl. worker start {open_ms:.0f} ms)", flush=True)
    from freeshape.engine import process
    process.SPARES.clear()


if __name__ == "__main__":
    main()
