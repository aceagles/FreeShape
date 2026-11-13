"""Headless Python-feature smoke (S4): gears as Python features, end to end, no Qt.

Builds a Part Studio with a spur gear, a helical spur gear and a bevel gear through PartStudioDoc.make_feature,
checks element names survive a tooth-count change, round-trips save/load, reopens with the gears plugin
unregistered (locked on the stored build), and checks a studio naming `py:os/system` is refused without
importing anything it names. Volumes use OCC's plain integrator: K.volume is far too slow on gear solids.

    both fs_py scripts/smoke/s4_python_features.py
"""
import importlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

_TMP = tempfile.mkdtemp(prefix="s4_pyfeat_")
os.environ["FREESHAPE_DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["FREESHAPE_PLUGIN_DIR"] = os.path.join(_TMP, "user_plugins")
os.makedirs(os.environ["FREESHAPE_PLUGIN_DIR"])

from OCP.BRepGProp import BRepGProp  # noqa: E402
from OCP.GProp import GProp_GProps  # noqa: E402

import freeshape.document  # noqa: E402,F401  (registers the bundled plugins)
from freeshape import pyfeature  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.pyfeature import host  # noqa: E402

GEARS = os.path.join(ROOT, "freeshape", "addons", "gears")
_T0 = time.time()


def step(msg):
    print(f"[{time.time() - _T0:5.1f}s] {msg}", flush=True)


def fail(msg):
    print(f"S4 FAIL: {msg}", flush=True)
    shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(1)


def check(cond, msg):
    if not cond:
        fail(msg)


def volume(shape):
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return g.Mass()


def add(doc, ftype, **params):
    f = doc.make_feature(ftype)
    f["params"].update(params)
    doc.add_feature(f)
    return f


def names(doc, pid):
    return host.names_json(doc.state.named(pid))


def volumes(doc):
    return {pid: volume(p.shape) for pid, p in doc.state.parts.items()}


def same_volumes(a, b, what):
    check(list(a) == list(b), f"{what}: part ids differ: {list(a)} vs {list(b)}")
    for pid in a:
        check(abs(a[pid] - b[pid]) <= 1e-6 * abs(a[pid]), f"{what}: {pid} volume {a[pid]} vs {b[pid]}")


def face_names(doc, pid):
    """{label: name} for the top, bottom and bore faces, found by label prefix in the names map."""
    out = {}
    for label in ("top", "bottom", "bore"):
        hits = [n for n in names(doc, pid)["face"].values() if n.startswith(label + ";")]
        check(len(hits) == 1, f"gear {pid}: expected one face labelled {label!r}, got {hits}")
        out[label] = hits[0]
    return out


def main():
    check(not pyfeature.problems() or not [p for p in pyfeature.problems() if "gears" in p[0]],
          f"gears plugin problems: {pyfeature.problems()}")

    # 1. build the studio
    doc = PartStudioDoc()
    spur = add(doc, "py:gears/spur", num_teeth=15, bore=5)
    helical = add(doc, "py:gears/spur", num_teeth=15, bore=5, helix_angle=20)
    bevel = add(doc, "py:gears/bevel")
    check(doc.errors == {}, f"build errors: {doc.errors}")
    pids = [f"{f['id']}/1" for f in (spur, helical, bevel)]
    check(list(doc.state.parts) == pids, f"parts {list(doc.state.parts)} != {pids}")
    step(f"1 built 3 gears {pids}, no errors")

    # 2. names survive a tooth-count change
    vols = volumes(doc)
    check(all(v > 0 for v in vols.values()), f"volumes {vols}")
    before_names = {pid: names(doc, pid) for pid in pids}
    before = face_names(doc, pids[0])
    new = json.loads(json.dumps(spur))
    new["params"]["num_teeth"] = 17
    doc.replace_feature(new)
    check(doc.errors == {}, f"errors after teeth 17: {doc.errors}")
    after = face_names(doc, pids[0])
    check(after == before, f"face names changed with teeth 15->17: {before} -> {after}")
    check(abs(volume(doc.state.parts[pids[0]].shape) - vols[pids[0]]) > 1e-3, "teeth 17 did not change the volume")
    step(f"2 teeth 15->17 kept names {sorted(after)}: {after}")
    # back to 15 so later steps compare against the recorded state
    doc.replace_feature(spur)
    check(doc.errors == {}, f"errors after teeth back to 15: {doc.errors}")
    same_volumes(volumes(doc), vols, "teeth 15->17->15")

    # 3. save / load
    path = os.path.join(_TMP, "studio", "gears.fshape")
    os.makedirs(os.path.dirname(path))
    doc.save(path)
    loaded = PartStudioDoc.load(path)
    check(loaded.errors == {}, f"load errors: {loaded.errors}")
    check(loaded.notices() == {}, f"unexpected notices on plain load: {loaded.notices()}")
    same_volumes(volumes(loaded), vols, "save/load")
    step("3 save/load: same volumes (1e-6)")

    # 4. locked reload with the plugin unregistered
    check(pyfeature.unregister_plugin("gears") is not None or "gears" not in {p.id for p in pyfeature.plugins()},
          "could not unregister gears")
    try:
        check("gears" not in {p.id for p in pyfeature.plugins()}, "gears still registered")
        locked = PartStudioDoc.load(path)
        check(locked.errors == {}, f"locked load errors: {locked.errors}")
        check(list(locked.state.parts) == pids, f"locked part ids {list(locked.state.parts)}")
        same_volumes(volumes(locked), vols, "locked reload")
        for pid in pids:
            check(names(locked, pid) == names(loaded, pid), f"locked names differ for {pid}")
        notes = locked.notices()
        check(set(notes) == {spur["id"], helical["id"], bevel["id"]}, f"notices {notes}")
        check(all(t.startswith("locked:") for t in notes.values()), f"notices {notes}")
        check(set(locked.locked_builds()) == set(notes), f"locked_builds {set(locked.locked_builds())}")
    finally:
        info = pyfeature.register_plugin(GEARS)
    check(info is not None, f"could not re-register gears: {pyfeature.problems()}")
    step("4 plugin unregistered: 3 locked parts, same ids/volumes/names, no errors, 3 locked notices")

    # 5. hostile type
    hostile = PartStudioDoc()
    hostile.features = [{"id": "F90", "type": "py:os/system", "name": "System 1", "suppressed": False,
                         "params": {"cmd": "echo hi"}, "plugin": {"id": "os", "version": "1"}}]
    hpath = os.path.join(_TMP, "hostile.fshape")
    hostile.save(hpath)
    mods_before = set(sys.modules)
    calls = []
    real_import, real_spec = importlib.import_module, importlib.util.spec_from_file_location

    def rec_import(name, *a, **kw):
        calls.append(("import_module", str(name)))
        return real_import(name, *a, **kw)

    def rec_spec(name, location=None, *a, **kw):
        calls.append(("spec_from_file_location", f"{name} {location}"))
        return real_spec(name, location, *a, **kw)

    importlib.import_module, importlib.util.spec_from_file_location = rec_import, rec_spec
    try:
        got = PartStudioDoc.load(hpath)
    finally:
        importlib.import_module, importlib.util.spec_from_file_location = real_import, real_spec
    err = got.errors.get("F90", "")
    check("not installed" in err, f"expected 'not installed' error on F90, got {got.errors}")
    for kind, arg in calls:
        first = arg.split(" ")[0]
        check(first not in ("os", "py:os/system") and "system" not in arg and "os/" not in arg,
              f"load imported something the file named: {kind} {arg}")
    new_mods = set(sys.modules) - mods_before
    bad = [m for m in new_mods if m == "os.system" or m.split(".")[0] in ("system", "py:os/system")
           or re.search(r"(^|[./])system$", m)]
    check(not bad, f"modules loaded by opening the hostile studio: {bad}")
    check(not any(p.id == "os" for p in pyfeature.plugins()), "a plugin 'os' was registered")
    step(f"5 py:os/system refused ({err!r}); {len(calls)} import calls recorded, none named it; "
         f"{len(new_mods)} new modules, none from the file")

    # 6. no Qt
    check("PySide6" not in sys.modules, "PySide6 was imported")
    print("S4 OK: 3 gears, names kept across teeth 15->17, locked reload ok, hostile type refused")


try:
    main()
finally:
    shutil.rmtree(_TMP, ignore_errors=True)
