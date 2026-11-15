"""Spike I4.1: what a large SVG/DXF import costs the sketch solver and the region finder.

Run in the gate image on both OCP builds:  both fs_py scripts/spikes/op2_import_bench.py

Builds sketches of 500, 1,000, 1,500, 2,000, 3,000, 4,000 and 5,000 points made of unconstrained closed line loops and closed
interpolating splines (10 points each, alternating; the shape an import produces) and measures, per size:
  solve0   Sketch.solve() with no constraint
  solve1   Sketch.solve() with one distance constraint added
  drag     one drag step with that constraint (what ui/sketcher.py does: copy the original, move a point of the
           constrained line, Sketch.solve(weights=...))
  regions  kernel.sketch_regions(plane, sketch.curves())
Each measurement runs in its own subprocess so a run over 60 s can be killed and recorded as "over 60 s".
Peak memory is the growth of the process peak RSS (VmHWM, reset first) over the RSS before the operation. SVD shapes are caught by
wrapping np.linalg.svd.

Guard thresholds (card I4.1): warn = largest size whose drag step with one constraint stays under 0.3 s,
confirm = largest under 3 s, capped at 5,000. The result file keeps one section per OCP build; the DECISION line
is the smaller of the builds' values so it holds on both.
"""
import json
import math
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RESULT = os.path.join(HERE, "op2_import_bench.result.txt")
SIZES = (500, 1000, 1500, 2000, 3000, 4000, 5000)
TIMEOUT = 60.0
WARN_S, CONFIRM_S, CAP = 0.3, 3.0, 5000
OPS = ("solve0", "solve1", "drag", "regions")
GROUP = 10          # points per loop / spline


def build(npts):
    from freeshape.sketch import Sketch
    sk = Sketch()
    ngroups = npts // GROUP
    side = math.ceil(math.sqrt(ngroups))
    first_line = None
    for g in range(ngroups):
        cx, cy = (g % side) * 30.0, (g // side) * 30.0
        ids = []
        for i in range(GROUP):
            a = 2 * math.pi * i / GROUP
            r = 10.0 + 1.5 * math.sin(3 * a + g)
            ids.append(sk.add_point(cx + r * math.cos(a), cy + r * math.sin(a)))
        if g % 2 == 0:
            for i in range(GROUP):
                e = sk.add_line(ids[i], ids[(i + 1) % GROUP])
                first_line = first_line or e
        else:
            sk.add_spline(ids, closed=True)
    return sk, first_line


def worker(npts, op):
    sys.path.insert(0, ROOT)
    import numpy as np
    from freeshape.sketch import Sketch
    svd_shapes = []
    real_svd = np.linalg.svd

    def svd(a, full_matrices=True, *args, **kw):
        out = real_svd(a, full_matrices, *args, **kw)
        if full_matrices:
            svd_shapes.append([list(np.shape(x)) for x in out])
        return out
    np.linalg.svd = svd

    sk, line = build(npts)
    if op in ("solve1", "drag"):
        l = sk.entities[line]
        d = float(np.linalg.norm(np.array(sk.points[l["p1"]]) - np.array(sk.points[l["p2"]])))
        sk.add_constraint("distance", line, value=d * 1.1)
    if op == "regions":
        from freeshape import kernel as K
        PLANE = K.std_plane("Top")
    res = {"npts": npts, "op": op}

    def rss_kb():
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * (os.sysconf("SC_PAGE_SIZE") // 1024)

    def run_once():
        if op in ("solve0", "solve1"):
            return sk.copy().solve()
        if op == "drag":
            orig = sk.to_dict()
            s2 = Sketch.from_dict(orig)
            p1 = sk.entities[line]["p1"]
            s2.points[p1] = [s2.points[p1][0] + 0.5, s2.points[p1][1] + 0.3]
            return s2.solve(weights={(p1, 0): 1e3, (p1, 1): 1e3})
        regs = K.sketch_regions(PLANE, sk.curves())
        return len(regs)

    try:
        with open("/proc/self/clear_refs", "w") as f:
            f.write("5")          # reset the peak-RSS mark (VmHWM) to the current RSS
    except OSError:
        pass
    base = rss_kb()
    times = []
    for _ in range(3):
        t = time.perf_counter()
        out = run_once()
        times.append(time.perf_counter() - t)
        if times[-1] > 5.0:
            break
    with open("/proc/self/status") as f:
        peak = next(int(l.split()[1]) for l in f if l.startswith("VmHWM:"))
    res.update(result=out, time_s=sorted(times)[len(times) // 2], reps=len(times),
               peak_mb=max(0, peak - base) / 1024.0, svd_shapes=svd_shapes[:1],
               n_vars=2 * (len(sk.points) - 1))
    print("WORKER " + json.dumps(res))


def measure(npts, op):
    t = time.perf_counter()
    try:
        p = subprocess.run([sys.executable, os.path.abspath(__file__), "--worker", str(npts), op],
                           capture_output=True, text=True, timeout=TIMEOUT, cwd=ROOT)
    except subprocess.TimeoutExpired:
        return {"npts": npts, "op": op, "timeout": True}
    for line in p.stdout.splitlines():
        if line.startswith("WORKER "):
            return json.loads(line[7:])
    return {"npts": npts, "op": op, "error": (p.stderr or p.stdout)[-400:]}


def fmt_t(r):
    if r is None:
        return "skipped"
    if r.get("timeout"):
        return "over 60 s"
    if "error" in r:
        return "error"
    return f"{r['time_s']:.3f}"


def main():
    import OCP
    build_tag = "OCP " + OCP.__version__
    load0 = os.getloadavg()
    results = {}
    slow = set()
    for n in SIZES:
        for op in OPS:
            if op in ("solve1", "drag") and slow:
                results[(n, op)] = None       # a smaller size already ran over 60 s
                continue
            r = measure(n, op)
            results[(n, op)] = r
            if r.get("timeout") and op in ("solve1", "drag"):
                slow.add(op)
    load1 = os.getloadavg()

    def ok_under(n, limit):
        r = results.get((n, "drag"))
        return bool(r) and "time_s" in r and r["time_s"] < limit
    def largest(limit):
        best = 0
        for n in SIZES:
            if ok_under(n, limit):
                best = n
        return min(best, CAP)
    warn, confirm = largest(WARN_S), largest(CONFIRM_S)
    warn = min(warn, confirm)

    lines = [f"## build {build_tag}",
             f"# BUILD {build_tag}: warn={warn} confirm={confirm}",
             f"load average before/after: {load0[0]:.2f} {load1[0]:.2f} (12 cores)",
             "points  vars   op       time_s     peak_mb  svd full_matrices output shapes (U, s, Vt)"]
    for n in SIZES:
        for op in OPS:
            r = results[(n, op)]
            if r is None or "time_s" not in r:
                lines.append(f"{n:<7} {2 * n:<6} {op:<8} {fmt_t(r):<10} -        -")
                continue
            lines.append(f"{n:<7} {r['n_vars']:<6} {op:<8} {fmt_t(r):<10} {r['peak_mb']:<8.0f} "
                         f"{r['svd_shapes'][0] if r['svd_shapes'] else 'no svd call'}"
                         + (f"  regions={r['result']}" if op == "regions" else ""))
    section = "\n".join(lines) + "\n"

    # merge with the other build's section already in the file
    sections = {}
    if os.path.exists(RESULT):
        for sec in re.split(r"(?m)^(?=## build )", open(RESULT).read()):
            m = re.match(r"## build (.*)", sec)
            if m:
                sections[m.group(1)] = sec if sec.endswith("\n") else sec + "\n"
    sections[build_tag] = section
    vals = [(int(a), int(b)) for s in sections.values()
            for a, b in re.findall(r"# BUILD .*?: warn=(\d+) confirm=(\d+)", s)]
    dw, dc = min(v[0] for v in vals), min(v[1] for v in vals)
    decision = f"DECISION import warn={dw} confirm={dc}"
    with open(RESULT, "w") as f:
        f.write(decision + "\n\n")
        f.write("Drag step = copy of the sketch, one point of the constrained line moved, Sketch.solve(weights).\n"
                "Thresholds: warn = largest size with drag < 0.3 s, confirm = largest < 3 s (cap 5000); min over builds.\n"
                "peak_mb = growth of process peak RSS (VmHWM, reset before the operation) over the RSS before it; time is the median of up to 3 reps.\n\n")
        f.write("\n".join(sections[k] for k in sorted(sections)))
    print(section)
    print(decision)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker(int(sys.argv[2]), sys.argv[3])
    else:
        main()
