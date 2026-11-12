"""Baseline smoke: build a small part headless through the public Document / feature API.

Part of the cumulative smoke gate. `scripts/smoke/run_all.sh` runs every `scripts/smoke/s*.py` in name
order on both OCP venvs (nightshift's `e2e` capability in harness.jsonc). Later slices add their own
script here (`s0_headless.py`, `s1_naming.py`, ...) and the gate picks it up without being edited. Each
script exits non-zero on failure and prints one `... OK` line on success.

    both fs_py scripts/smoke/s00_baseline.py

The model: a 40 x 20 x 10 mm block extruded from a sketch rectangle on the Top plane, with a 10 x 10 mm
square pocket cut through it. Expected: one valid part of 40*20*10 - 10*10*10 = 7000 mm^3, before and
after a full rebuild.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from freeshape import features as F, kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402

TOP = {"kind": "std", "name": "Top"}
EXPECTED_VOLUME = 40.0 * 20.0 * 10.0 - 10.0 * 10.0 * 10.0


def rectangle(doc, x0, y0, w, h):
    """Add a sketch feature holding one w x h rectangle on the Top plane; returns its feature id."""
    s = Sketch()
    pts = [s.add_point(x0, y0), s.add_point(x0 + w, y0), s.add_point(x0 + w, y0 + h), s.add_point(x0, y0 + h)]
    for a, b in zip(pts, pts[1:] + pts[:1]):
        s.add_line(a, b)
    s.solve()
    feat = doc.make_feature("sketch")
    feat["params"].update(plane=TOP, sketch=s.to_dict())
    doc.add_feature(feat)
    return feat["id"]


def extrude(doc, sketch_id, **params):
    regions = doc.state.sketches[sketch_id].regions
    assert len(regions) == 1, f"expected 1 sketch region, found {len(regions)}"
    feat = doc.make_feature("extrude")
    feat["params"].update(regions=[F.region_ref(doc.state, sketch_id, regions[0])], **params)
    doc.add_feature(feat)
    return feat["id"]


def check(doc, when):
    assert not doc.errors, f"{when}: feature errors {doc.errors}"
    parts = list(doc.state.parts.values())
    assert len(parts) == 1, f"{when}: expected 1 part, found {len(parts)}"
    shape = parts[0].shape
    assert K.is_valid(shape), f"{when}: part shape is not valid"
    volume = K.volume(shape)
    assert abs(volume - EXPECTED_VOLUME) <= 1e-6 * EXPECTED_VOLUME, \
        f"{when}: volume {volume!r}, expected {EXPECTED_VOLUME!r}"
    return volume


def main():
    doc = Document()
    extrude(doc, rectangle(doc, 0.0, 0.0, 40.0, 20.0), depth=10.0, op="new")
    extrude(doc, rectangle(doc, 15.0, 5.0, 10.0, 10.0), end="through", op="remove")
    assert len(doc.features) == 4, f"expected 4 features, found {len(doc.features)}"
    check(doc, "after build")
    doc.rebuild()
    volume = check(doc, "after rebuild")
    print(f"BASELINE OK parts=1 volume={volume:.3f} ocp={os.environ.get('OCP', '?')}")


if __name__ == "__main__":
    main()
