"""3MF and OBJ mesh export.

An item is (name, colour, vertices, triangles, 4x4 transform): vertices (n,3) and triangles (m,3) index
into them, wound counter-clockwise seen from outside; the transform is a column-vector 4x4 (translation in
the last column), as the Assembly stores it. Items that share the same `vertices` array and colour are
written as one 3MF object with several build items.
"""
from __future__ import annotations

import zipfile
from xml.sax.saxutils import quoteattr

import numpy as np

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    '</Types>')
_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
    '</Relationships>')


def _hex(colour) -> str:
    r, g, b = (max(0, min(255, int(round(float(c) * 255)))) for c in tuple(colour)[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def matrix_3mf(T) -> str:
    """The 12 numbers of a 3MF transform attribute: m00 m01 m02 m10 m11 m12 m20 m21 m22 m30 m31 m32.
    3MF multiplies row vectors (v' = v * M), so M is the transpose of a column-vector 4x4 and the
    translation is the last row."""
    M = np.asarray(T, float).T
    return " ".join(f"{v:.9g}" for v in M[:4, :3].reshape(-1))


def parse_matrix_3mf(text) -> np.ndarray:
    """Inverse of matrix_3mf: the column-vector 4x4 for a transform attribute."""
    v = np.array([float(x) for x in text.split()]).reshape(4, 3)
    T = np.eye(4)
    T[:3, :] = v.T
    return T


def write_3mf(items, path, title="FreeShape export") -> None:
    items = list(items)
    colours: list = []              # distinct colours, one basematerials entry each
    objects: list = []              # [name, vertices, triangles, colour index]
    obj_of: dict = {}
    builds: list = []               # (object index, transform, item name)
    for name, colour, verts, tris, T in items:
        cidx = _hex(colour)
        if cidx not in colours:
            colours.append(cidx)
        key = (id(verts), cidx)
        if key not in obj_of:
            obj_of[key] = len(objects)
            objects.append((name, verts, tris, colours.index(cidx)))
        builds.append((obj_of[key], T, name))
    out = ['<?xml version="1.0" encoding="UTF-8"?>\n',
           f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}">',
           f'<metadata name="Title">{quoteattr(title)[1:-1]}</metadata>',
           '<metadata name="Application">FreeShape</metadata>',
           '<resources>']
    out.append('<basematerials id="1">')
    for i, c in enumerate(colours):
        out.append(f'<base name="Colour {i + 1}" displaycolor="{c}"/>')
    out.append('</basematerials>')
    for oi, (name, verts, tris, ci) in enumerate(objects):
        out.append(f'<object id="{oi + 2}" type="model" name={quoteattr(str(name))} pid="1" pindex="{ci}">')
        out.append('<mesh><vertices>')
        out.extend(f'<vertex x="{x:.7g}" y="{y:.7g}" z="{z:.7g}"/>' for x, y, z in np.asarray(verts, float))
        out.append('</vertices><triangles>')
        out.extend(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in np.asarray(tris, np.int64))
        out.append('</triangles></mesh></object>')
    out.append('</resources><build>')
    for oi, T, name in builds:
        out.append(f'<item objectid="{oi + 2}" transform="{matrix_3mf(T)}" partnumber={quoteattr(str(name))}/>')
    out.append('</build></model>')
    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", "".join(out))


def write_obj(items, path) -> None:
    lines = ["# FreeShape export (units: millimeter)"]
    base = 1
    for name, _colour, verts, tris, T in items:
        T = np.asarray(T, float)
        v = np.asarray(verts, float)
        w = v @ T[:3, :3].T + T[:3, 3]
        lines.append("o " + ("_".join(str(name).split()) or "part"))
        lines.extend(f"v {x:.7g} {y:.7g} {z:.7g}" for x, y, z in w)
        lines.extend(f"f {a + base} {b + base} {c + base}" for a, b, c in np.asarray(tris, np.int64))
        base += len(v)
    with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def _mesh_of(shape, defl, angle):
    from . import kernel as K
    m = K.tessellate(shape, defl, angle, edges_too=False)
    return np.asarray(m.verts, float), np.asarray(m.tris, np.int64)


def export_instances(entries, path, defl=0.05, angle=0.3) -> None:
    """entries: (shape, 4x4 transform, name, colour). Shapes that are the same object are tessellated once."""
    cache: dict = {}
    items = []
    for shape, T, name, colour in entries:
        if id(shape) not in cache:
            cache[id(shape)] = _mesh_of(shape, defl, angle)
        v, t = cache[id(shape)]
        items.append((name, colour, v, t, T))
    (write_3mf if str(path).lower().endswith(".3mf") else write_obj)(items, path)


def export_parts(entries, path, defl=0.05, angle=0.3) -> None:
    """entries: (shape, name, colour); shapes are already in world position."""
    export_instances([(s, np.eye(4), n, c) for s, n, c in entries], path, defl, angle)
