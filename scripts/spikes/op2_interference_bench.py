"""Benchmark for interference detection (card I5.1): thresholds, and whether a helper process is needed.

    both fs_py scripts/spikes/op2_interference_bench.py

Runs once per OCP build. It measures, on synthetic assemblies of 50, 200 and 500 instances and on the bolted
bracket example:
  - pairs that survive the bounding-box reject, and the time per surviving pair in K.common and K.volume;
  - the mate solver's positional noise, which sets min_depth;
  - whether OCP releases the GIL inside BRepAlgoAPI_Common.Build().
It prints one DECISION line and writes scripts/spikes/op2_interference_bench.result.txt. Each build writes its
own section of that file and keeps the other build's section, so after both builds have run the file's DECISION
line is the combined (most cautious) answer and is the line the run prints.

Changes no product code; the pair loop copies AssemblyDoc.interferences (assembly.py) with a clock around each step.
"""
import math
import os
import re
import sys
import tempfile
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "examples"))

from freeshape import api as API, assembly as A, kernel as K  # noqa: E402
from freeshape.features import Part  # noqa: E402

RESULT = os.path.join(HERE, "op2_interference_bench.result.txt")
SIZES = (50, 200, 500)
MIN_VOLUME = 1e-3
DEPTH_FLOOR = 1e-4
TOTAL_BUDGET_S = 2.0
LONGEST_PAIR_LIMIT_S = 1.0
GIL_KEEP = 0.25


def ocp_build():
    return os.environ.get("OCP") or "unknown"


# ---------------------------------------------------------------------------------------------- synthetic parts
def plate_with_holes(w, d, t, holes, r):
    s = API.box(w, d, t)
    for x, y in holes:
        s = K.cut(s, K.cylinder((x, y, -1.0), (0, 0, 1), r, t + 2.0))
    return s


def make_library():
    """Five distinct geometries: plate (4 holes), pin that fits the hole loosely, pin that is pressed in,
    flange (6-hole bolt circle) and bolt. Hole radius 2.25 mm."""
    hole_r = 2.25
    plate = plate_with_holes(60, 40, 6, [(8, 8), (52, 8), (8, 32), (52, 32)], hole_r)
    circ = [(30 + 12 * math.cos(math.radians(60 * k)), 30 + 12 * math.sin(math.radians(60 * k))) for k in range(6)]
    flange = plate_with_holes(60, 60, 8, circ, hole_r)
    loose = K.cylinder((0, 0, 0), (0, 0, 1), 2.0, 14.0)
    tight = K.cylinder((0, 0, 0), (0, 0, 1), 2.3, 14.0)
    bolt = K.cylinder((0, 0, 0), (0, 0, 1), 2.1, 16.0)
    return {"plate": plate, "flange": flange, "loose": loose, "tight": tight, "bolt": bolt}, circ


def translation(v):
    return A.translation(np.asarray(v, float))


def build_synthetic(n, lib, circ):
    """An AssemblyDoc of n instances: a row of cells; each is a plate with a loose pin and a pressed pin in two of
    its holes; every fifth is a flange with six bolts. Neighbouring cells overlap by 2 mm so boxes meet."""
    asm = A.AssemblyDoc()
    asm.resolver = lambda src: Part(src["part"], src["part"], lib[src["part"]])
    asm.name = f"synthetic {n}"
    cols = 20
    k = 0
    cell = 0
    src = lambda name: {"kind": "studio", "part": name}
    while len(asm.instances) < n:
        row, col = divmod(cell, cols)
        ox, oy, oz = col * 58.0, row * 62.0, 0.0
        if cell % 5 == 4:
            asm.insert(src("flange"), translation((ox, oy, oz)), checkpoint=False)
            for x, y in circ:
                if len(asm.instances) < n:
                    asm.insert(src("bolt"), translation((ox + x, oy + y, oz - 4)), checkpoint=False)
        else:
            asm.insert(src("plate"), translation((ox, oy, oz)), checkpoint=False)
            for name, (x, y) in (("loose", (8, 8)), ("tight", (52, 32))):
                if len(asm.instances) < n:
                    asm.insert(src(name), translation((ox + x, oy + y, oz - 4)), checkpoint=False)
        cell += 1
    return asm


# ---------------------------------------------------------------------------------------------- pair measurement
def surface_area(shape):
    return K.mass_props(shape)["area"]


def measure_pairs(asm):
    """The loop of AssemblyDoc.interferences with a clock around each step. Returns a dict of the numbers."""
    t0 = time.perf_counter()
    vis = [i for i in asm.visible_instances() if not A.is_sketch_source(i["source"])]
    shapes = {i["id"]: asm.world_shape(i["id"]) for i in vis}
    boxes = {k: K.bbox(s) for k, s in shapes.items()}
    t_prep = time.perf_counter() - t0

    ids = [i["id"] for i in vis]
    n = len(ids)
    t0 = time.perf_counter()
    lo = np.array([boxes[i][0] for i in ids])
    hi = np.array([boxes[i][1] for i in ids])
    pairs = []
    for a in range(n):
        sep = np.any(hi[a] < lo[a + 1:] - 1e-6, axis=1) | np.any(hi[a + 1:] < lo[a] - 1e-6, axis=1)
        pairs += [(a, a + 1 + int(j)) for j in np.nonzero(~sep)[0]]
    t_box = time.perf_counter() - t0

    t_common, t_vol, per_pair, hits, nulls, errors = [], [], [], 0, 0, 0
    worst = (0.0, None)
    t_loop0 = time.perf_counter()
    for a, b in pairs:
        sa, sb = shapes[ids[a]], shapes[ids[b]]
        t1 = time.perf_counter()
        try:
            ov = K.common(sa, sb)
            t2 = time.perf_counter()
            v = K.volume(ov) if not ov.IsNull() else 0.0
            t3 = time.perf_counter()
        except Exception:
            errors += 1
            continue
        t_common.append(t2 - t1)
        t_vol.append(t3 - t2)
        per_pair.append(t3 - t1)
        if v > MIN_VOLUME:
            hits += 1
        if ov.IsNull():
            nulls += 1
        if t3 - t1 > worst[0]:
            worst = (t3 - t1, (a, b))
    t_loop = time.perf_counter() - t_loop0
    total = t_prep + t_box + t_loop
    k = max(len(per_pair), 1)
    return {
        "instances": n, "all_pairs": n * (n - 1) // 2, "survivors": len(pairs), "overlaps": hits, "null": nulls,
        "errors": errors, "ms_common": 1e3 * sum(t_common) / k, "ms_volume": 1e3 * sum(t_vol) / k,
        "ms_pair": 1e3 * sum(per_pair) / k, "ms_longest": 1e3 * worst[0], "s_prep": t_prep, "s_box": t_box,
        "s_loop": t_loop, "s_total": total, "longest": None if worst[1] is None else
        (shapes[ids[worst[1][0]]], shapes[ids[worst[1][1]]]),
    }


# ---------------------------------------------------------------------------------------------- bolted bracket
def load_bracket(folder):
    import make_bolted_bracket as EX
    from freeshape.document import Document
    EX.main(folder)
    studio = Document.load(os.path.join(folder, "bolted_bracket_parts.fshape"))
    return A.AssemblyDoc.load(os.path.join(folder, "bolted_bracket.fasm"),
                              resolver=lambda src: studio.state.parts[src["part"]])


# ---------------------------------------------------------------------------------------------- solver noise
def planar_face(shape, nz, z):
    for i, f in enumerate(K.faces(shape)):
        info = K.face_info(f)
        if "n" in info and abs(abs(info["n"][2]) - nz) < 1e-6 and abs(info["c"][2] - z) < 1e-6:
            return i
    raise RuntimeError("face not found")


def pair_depth(asm, a, b):
    """(depth, volume, area) of the overlap of two instances; depth = 2 V / A, 0 when they only touch."""
    ov = K.common(asm.world_shape(a), asm.world_shape(b))
    if ov.IsNull():
        return 0.0, 0.0, 0.0
    v = K.volume(ov)
    area = surface_area(ov)
    return (2 * v / area if area > 0 and v > 0 else 0.0), v, area


def mate_gap(asm, m):
    fa, fb = asm.world_frame(m["a"]), asm.world_frame(m["b"])
    oa, ob = np.asarray(fa["o"], float), np.asarray(fb["o"], float)
    za = np.asarray(fa["z"], float)
    d = oa - ob
    return abs(float(d @ za)), float(np.linalg.norm(d - (d @ za) * za))


def solver_noise(bracket):
    """Largest overlap depth between parts mated face to face, and the connector misalignment, on the solved
    bracket and on chains of plates solved from random starts (fastened mates, top face of one on the bottom face
    of the next)."""
    rows = []
    plate = API.box(60, 40, 6)
    rng = np.random.default_rng(7)

    def record(label, asm, pairs, mates):
        depth = max([pair_depth(asm, a, b)[0] for a, b in pairs] or [0.0])
        gaps = [mate_gap(asm, m) for m in mates]
        rows.append({"case": label, "pairs": len(pairs), "dof": asm.dof, "depth": depth,
                     "normal_gap": max([g[0] for g in gaps] or [0.0]), "lateral": max([g[1] for g in gaps] or [0.0])})

    # the bracket: plate and bracket, one fastened mate
    ids = [i["id"] for i in bracket.instances]
    record("bolted bracket", bracket, [(ids[0], ids[1])], bracket.mates)

    for n_plates, tag in ((6, "chain of 6 plates"), (12, "chain of 12 plates")):
        asm = A.AssemblyDoc()
        asm.resolver = lambda src: Part(src["part"], "plate", plate)
        src = {"kind": "studio", "part": "plate"}
        iids = []
        for k in range(n_plates):
            if k == 0:
                T = np.eye(4)
            else:
                T = A.translation(rng.uniform(-80, 80, 3)) @ A.rot_about(rng.normal(size=3), rng.uniform(0, 6.28))
            iids.append(asm.insert(src, T, checkpoint=False))
        top = planar_face(plate, 1, 6.0)
        bottom = planar_face(plate, 1, 0.0)
        for a, b in zip(iids, iids[1:]):
            m = asm.make_mate("fastened")
            m["a"] = asm.connector(a, "face", top)
            m["b"] = asm.connector(b, "face", bottom)
            asm.add_mate(m, checkpoint=False)
        record(tag, asm, list(zip(iids, iids[1:])), asm.mates)
    return rows


# ---------------------------------------------------------------------------------------------- GIL
def _counter_run(seconds, busy=None):
    """Main-thread counter over `seconds` with `busy(stop_event)` running in a worker thread. Returns
    (increments/s, longest gap between two counter checks in s, share of the time spent in gaps over 20 ms)."""
    stop = threading.Event()
    worker = threading.Thread(target=busy, args=(stop,), daemon=True) if busy else None
    n, gaps = 0, []
    if worker:
        worker.start()
    t0 = last = time.perf_counter()
    while last - t0 < seconds:
        for _ in range(1000):
            n += 1
        now = time.perf_counter()
        gaps.append(now - last)
        last = now
    el = last - t0
    stop.set()
    if worker:
        worker.join()
    return n / el, max(gaps), sum(g for g in gaps if g > 0.02) / el


def heavy_pair(grid=8):
    """A slow pair: a plate with grid x grid holes against grid x grid pins (as one compound) that are 0.15 mm
    too big, so every hole gives an overlap ring. About 0.4 s per Common."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    def comp(shapes):
        c = TopoDS_Compound()
        bld = BRep_Builder()
        bld.MakeCompound(c)
        for sh in shapes:
            bld.Add(c, sh)
        return c
    pos = [(10 + i * 8, 10 + j * 8) for i in range(grid) for j in range(grid)]
    plate = K.cut(API.box(8 * grid + 12, 8 * grid + 12, 10), comp([K.cylinder((x, y, -1), (0, 0, 1), 2.25, 12) for x, y in pos]))
    pins = comp([K.cylinder((x, y, -1), (0, 0, 1), 2.4, 12) for x, y in pos])
    return plate, pins


def gil_test(pairs):
    """pairs: [(label, shape a, shape b)]. For each, a worker repeats the boolean for 2 s while the main thread
    counts; median of 3 runs by rate. Returns (idle rate, idle spread, rows)."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common

    def raw(sa, sb):
        def run(stop):
            while not stop.is_set():
                op = BRepAlgoAPI_Common(sa, sb)          # the form K.common uses
                op.SetFuzzyValue(1e-6)
                op.Build()
        return run

    def kern(sa, sb):
        def run(stop):
            while not stop.is_set():
                K.common(sa, sb)
        return run

    _counter_run(0.3)                                   # warm up
    idle = sorted(_counter_run(1.0)[0] for _ in range(3))
    base = idle[1]
    rows = []
    for label, sa, sb in pairs:
        t0 = time.perf_counter()
        op = BRepAlgoAPI_Common(sa, sb)
        op.SetFuzzyValue(1e-6)
        op.Build()
        one = time.perf_counter() - t0
        for name, fn in (("BRepAlgoAPI_Common.Build", raw), ("K.common", kern)):
            runs = sorted((_counter_run(2.0, fn(sa, sb)) for _ in range(3)), key=lambda r: r[0])
            rate, stall, share = runs[1]
            rows.append({"pair": label, "call": name, "build_ms": one * 1e3, "rate": rate, "fraction": rate / base,
                         "max_stall_ms": stall * 1e3, "stalled_share": share})
    return base, (idle[0], idle[2]), rows


# ---------------------------------------------------------------------------------------------- report
def table(rows, headers):
    w = [max(len(str(h)), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
    line = lambda r: "  ".join(str(c).rjust(w[i]) for i, c in enumerate(r))
    return "\n".join([line(headers), line(["-" * x for x in w])] + [line(r) for r in rows])


def load_other_sections(build):
    """{build: {"metrics": line, "text": section text}} for the other builds already in the result file."""
    try:
        text = open(RESULT).read()
    except OSError:
        return {}
    out = {}
    for m in re.finditer(r"^== (\S+) ==\n(.*?)(?=^== \S+ ==\n|\Z)", text, re.S | re.M):
        if m.group(1) != build:
            mm = re.search(r"^metrics min_depth=(\S+) engine=(\S+) open_runs=(\d+)$", m.group(2), re.M)
            if mm:
                out[m.group(1)] = {"depth": float(mm.group(1)), "engine": mm.group(2), "open": int(mm.group(3)),
                                   "text": m.group(0)}
    return out


def main():
    build = ocp_build()
    t_start = time.perf_counter()
    lib, circ = make_library()
    lines = []
    out = lines.append

    load = os.getloadavg()
    cores = os.cpu_count()
    out(f"== {build} ==")
    out(f"load average at start (1/5/15 min): {load[0]:.2f} {load[1]:.2f} {load[2]:.2f} on {cores} cores")

    results = []
    longest = (0.0, None)
    for n in SIZES:
        asm = build_synthetic(n, lib, circ)
        r = measure_pairs(asm)
        results.append(("synthetic", r))
        if r["longest"] and r["ms_longest"] > longest[0]:
            longest = (r["ms_longest"], r["longest"], f"synthetic {n}")
    with tempfile.TemporaryDirectory() as folder:
        bracket = load_bracket(folder)
        rb = measure_pairs(bracket)
        results.append(("bolted bracket", rb))
        if rb["longest"] and rb["ms_longest"] > longest[0]:
            longest = (rb["ms_longest"], rb["longest"], "bolted bracket")
        noise_rows = solver_noise(bracket)

    out("")
    out("Pair counts and times (one build, this run). Pairs = all pairs; survive = pairs left after the 1e-6 mm bbox reject.")
    rows = []
    for kind, r in results:
        rows.append([f"{kind} {r['instances']}" if kind == "synthetic" else kind, r["instances"], r["all_pairs"],
                     r["survivors"], r["overlaps"], f"{r['ms_common']:.2f}", f"{r['ms_volume']:.2f}",
                     f"{r['ms_pair']:.2f}", f"{r['ms_longest']:.1f}", f"{r['s_box']:.3f}", f"{r['s_total']:.2f}"])
    out(table(rows, ["case", "inst", "pairs", "survive", "overlap", "ms/pair common", "ms/pair volume",
                     "ms/pair total", "ms longest", "s bbox", "s total"]))
    out("overlap = survivors with volume > 1e-3 mm3. s total = shape placement + bbox + bbox reject + all booleans + volumes.")
    errs = sum(r["errors"] for _, r in results)
    out(f"boolean errors (pair skipped): {errs}")

    out("")
    out("Mate solver noise (largest overlap depth 2V/A between parts mated face to face; connector misalignment in mm).")
    out(table([[x["case"], x["pairs"], x["dof"], f"{x['depth']:.3e}", f"{x['normal_gap']:.3e}", f"{x['lateral']:.3e}"]
               for x in noise_rows], ["case", "mated pairs", "dof left", "max depth mm", "max normal gap mm",
                                      "max lateral mm"]))
    noise = max(max(x["depth"], x["normal_gap"]) for x in noise_rows)
    min_depth = max(10 * noise, DEPTH_FLOOR)
    out(f"noise = {noise:.3e} mm (the larger of the overlap depth and the normal gap); min_depth = max(10 x noise, {DEPTH_FLOOR:g}) = {min_depth:.3e} mm")

    out("")
    sa, sb = longest[1]
    hp = heavy_pair()
    out("GIL test: a worker thread repeats the boolean for 2 s while the main thread counts; median of 3 runs. "
        "A stall is the gap between two counter checks.")
    base, spread, rows = gil_test([(f"longest pair ({longest[2]})", sa, sb), ("heavy pair (64 holes, 64 pins)", *hp)])
    out(f"idle counter (no worker): {base:.3e} increments/s (3 runs: {spread[0]:.3e} .. {spread[1]:.3e})")
    out(table([[r["pair"], r["call"], f"{r['build_ms']:.1f}", f"{r['rate']:.3e}", f"{r['fraction']:.2f}",
                f"{r['max_stall_ms']:.0f}", f"{r['stalled_share']:.0%}"] for r in rows],
              ["pair", "worker runs", "one Common ms", "increments/s", "fraction of idle", "max stall ms",
               "time in stalls >20 ms"]))
    out("A fraction near 1 means the boolean released the GIL. A short boolean looks better than it is: the worker "
        "hands the GIL over between booleans, so read the heavy pair and the stall columns.")
    keep = min(r["fraction"] for r in rows)

    longest_s = longest[0] / 1e3
    engine = "thread" if (longest_s < LONGEST_PAIR_LIMIT_S and keep >= GIL_KEEP) else "helper"
    # open_runs_at_most: surviving pairs a check may have and still finish under 2 s, from the slowest per-pair mean
    # seen at any size (fixed cost: placement + bbox reject at 500 instances, charged once)
    worst_ms = max(r["ms_pair"] for _, r in results)
    fixed = max(r["s_prep"] + r["s_box"] for _, r in results)
    open_runs = int(max(TOTAL_BUDGET_S - fixed, 0) * 1e3 / worst_ms) // 5 * 5
    out("")
    out(f"engine rule: thread when longest pair < {LONGEST_PAIR_LIMIT_S:g} s ({longest_s:.3f} s) and the lowest counter fraction over both pairs is >= "
        f"{GIL_KEEP:.0%} of idle ({keep:.0%}) -> {engine}")
    out(f"open_runs_at_most: ({TOTAL_BUDGET_S:g} s - {fixed:.2f} s fixed) / {worst_ms:.2f} ms worst mean per pair "
        f"= {open_runs} surviving pairs, rounded down to a multiple of 5")
    out(f"metrics min_depth={min_depth:.3e} engine={engine} open_runs={open_runs}")
    out(f"script time on this build: {time.perf_counter() - t_start:.0f} s; load average at end: "
        + " ".join(f"{x:.2f}" for x in os.getloadavg()))
    out("")

    sections = load_other_sections(build)
    sections[build] = {"depth": min_depth, "engine": engine, "open": open_runs, "text": "\n".join(lines)}
    depth = max(s["depth"] for s in sections.values())
    eng = "helper" if any(s["engine"] == "helper" for s in sections.values()) else "thread"
    opn = min(s["open"] for s in sections.values())
    decision = (f"DECISION interference min_depth={depth:.3g} min_volume=1e-3 engine={eng} "
                f"open_runs_at_most={opn}")
    body = decision + "\n(combined over: " + ", ".join(sorted(sections)) + "; most cautious value of each key)\n\n"
    body += "\n".join(sections[b]["text"].rstrip("\n") + "\n" for b in sorted(sections))
    with open(RESULT, "w") as fh:
        fh.write(body)
    print(decision)


if __name__ == "__main__":
    main()
