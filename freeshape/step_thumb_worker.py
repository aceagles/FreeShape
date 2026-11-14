"""Draws thumbnails of STEP files in a separate process, for the Component library browser.

    python -m freeshape.step_thumb_worker

A long-lived process: reading a STEP file takes seconds and a damaged one can take OpenCascade down, so the
app keeps a few of these and restarts one that dies. Each stdin line is a JSON job {"path", "out", "size"};
the answer is one stdout line '@@FS ' + JSON {"path", "ok", "error"} (OpenCascade prints its own chatter to
stdout too, which the app ignores). `render(path, out, size)` does one file in the calling process."""
import json
import os
import sys

MAX_TRIS = 250_000


DARKEST = 0.14            # a pure black part would shade to a flat silhouette


def geometry(path):
    """(triangles (m, 3, 3), colours (m, 3) in 0..1, [edge polylines]) of a STEP file, coarsely meshed.
    The polylines are those of the shapes that have no faces (wires, sketches)."""
    import numpy as np
    from freeshape import kernel as K
    try:
        items = K._import_step_with_colors(path)
    except Exception:
        items = []
    if not items:
        items = [(s, None, None, {}) for s in K.import_step(path)]
    boxes = [K.bbox(it[0]) for it in items]
    lo = np.min([b[0] for b in boxes], axis=0)
    hi = np.max([b[1] for b in boxes], axis=0)
    defl = max(float(np.linalg.norm(hi - lo)) * 0.004, 1e-3)
    tris, cols, lines = [np.zeros((0, 3, 3))], [np.zeros((0, 3))], []
    for shape, _name, color, face_colors in items:
        m = K.tessellate(shape, lin_defl=defl, ang_defl=0.5, edges_too=False)
        if len(m.tris) == 0:
            lines += [np.asarray(e, float) for e in K.tessellate(shape, lin_defl=defl, ang_defl=0.5).edge_polys]
            continue
        t = np.asarray(m.verts, float)[np.asarray(m.tris, np.int64)]
        c = np.tile(_rgb(color), (len(t), 1))
        tri_face = np.asarray(m.tri_face)
        for fi, fc in (face_colors or {}).items():
            c[tri_face == fi] = _rgb(fc)
        tris.append(t)
        cols.append(c)
    return np.concatenate(tris), np.maximum(np.concatenate(cols), DARKEST), lines


def _rgb(color):
    import numpy as np
    if color is None or len(color) < 3:
        return np.array([0.7, 0.72, 0.78])
    c = np.array(color[:3], float)
    return np.clip(c / 255.0 if c.max() > 1.0 else c, 0, 1)


def render(path, out, size=256):
    """Write a size x size PNG preview of the STEP file `path` to `out`. Raises when there is nothing to draw."""
    import numpy as np
    from freeshape.ui import preview as PV
    tris, cols, lines = geometry(path)
    if not len(tris) and not any(len(ln) >= 2 for ln in lines):
        raise ValueError("no geometry to draw")
    if len(tris) > MAX_TRIS:                      # a huge model: every k-th triangle still reads as the part
        keep = np.arange(0, len(tris), int(np.ceil(len(tris) / MAX_TRIS)))
        tris, cols = tris[keep], cols[keep]
    img = PV.render_solids(tris, cols, size, lines)
    tmp = out + ".tmp.png"
    if not img.save(tmp):
        raise OSError(f"could not write {out}")
    os.replace(tmp, out)


def main():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])   # noqa: F841  kept alive for QPainter
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            job = json.loads(line)
        except ValueError:
            continue
        answer = {"path": job.get("path"), "ok": True, "error": ""}
        try:
            render(job["path"], job["out"], int(job.get("size", 256)))
        except Exception as e:                    # the app shows the placeholder for this file
            answer.update(ok=False, error=f"{type(e).__name__}: {e}"[:200])
        sys.stdout.write("\n@@FS " + json.dumps(answer) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
