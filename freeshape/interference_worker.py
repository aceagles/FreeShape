"""Runs the pair loop of an interference check in a separate process (a boolean holds Python's lock, so a thread
would freeze the window for the length of each one; see scripts/spikes/op2_interference_bench.result.txt).

    python -m freeshape.interference_worker request.json

The request names a folder of BREP files (one per distinct geometry) and the pairs to check. The worker appends one
JSON line per event to the log file named in the request, flushing each line, so the parent can read it while the
process runs and still read it after the process was killed:

    {"t": "S", "n": k}                                  pair k of the request started
    {"t": "R", "n": k, "kind": ..., "vol": ..., "lo": [..], "hi": [..], "note": ..., "brep": "r<k>.brep" | null}
    {"t": "E"}                                          every pair done

`kind` is null for a pair with no interference. Each pair goes through `interference.find_interferences` as a
two-body list, so the classification is the one the in-process check uses, and one cache dict is shared by every
pair of the run, so identical geometry costs one boolean.
"""
import json
import os
import sys


def _emit(fh, **ev):
    fh.write(json.dumps(ev) + "\n")
    fh.flush()


def run(request_path):
    import numpy as np
    from freeshape import interference as I
    from freeshape import kernel as K
    with open(request_path, encoding="utf-8") as fh:
        req = json.load(fh)
    folder = os.path.dirname(os.path.abspath(request_path))
    geoms = {}

    def geom(k):
        if k not in geoms:
            with open(os.path.join(folder, f"g{k}.brep"), "rb") as fh:
                geoms[k] = K.shape_from_brep(fh.read())
        return geoms[k]

    bodies = []
    for i, b in enumerate(req["bodies"]):
        T = np.array(b["T"], float).reshape(4, 4) if b["T"] is not None else np.eye(4)
        key = ("g", b["geom"]) if b["keyed"] else None
        bodies.append(I.Body(str(i), K.located(geom(b["geom"]), T), key, T, bool(b.get("standard"))))
    expected = frozenset(frozenset((str(a), str(b))) for a, b in req["expected"])
    cache = {}
    with open(req["log"], "a", encoding="utf-8") as log:
        for n, (i, j) in enumerate(req["pairs"]):
            _emit(log, t="S", n=n)
            res = I.find_interferences([bodies[i], bodies[j]], expected_pairs=expected, cache=cache,
                                       min_volume=req["min_volume"], min_depth=req["min_depth"])
            c = res[0] if res else None
            if c is None:
                _emit(log, t="R", n=n, kind=None)
                continue
            brep = None
            if c.shape is not None:
                brep = f"r{n}.brep"
                with open(os.path.join(folder, brep), "wb") as fh:
                    fh.write(K.shape_to_brep(c.shape))
            _emit(log, t="R", n=n, kind=c.kind, vol=c.volume, lo=[float(x) for x in c.lo],
                  hi=[float(x) for x in c.hi], note=c.note, brep=brep)
        _emit(log, t="E")


if __name__ == "__main__":
    run(sys.argv[1])
