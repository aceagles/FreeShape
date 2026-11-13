"""A part's material (a Part Studio override) drives mass properties and the Assembly bill of materials."""
import numpy as np
import pytest

from conftest import block_doc, only_part
from freeshape import assembly as A
from freeshape import kernel as K
from freeshape import measure as M
from freeshape.document import Document
from freeshape.features import Part


def cube(material=None):
    doc = block_doc(10, 10, 10)
    pid = only_part(doc).id
    if material:
        doc.set_part_override(pid, material=material)
    return doc, pid


def mass(doc, pid):
    p = doc.state.parts[pid]
    return M.mass_g(K.volume(p.shape), doc.part_overrides.get(pid, {}).get("material"))


def asm_of(studios, placements=None):
    """An Assembly instancing the single part of each Part Studio in `studios` ({uid: Document})."""
    a = A.AssemblyDoc()

    def resolve(src):
        d = studios[src["uid"]]
        return next(iter(d.state.parts.values()))
    a.resolver = resolve
    for uid in studios:
        T = np.eye(4)
        T[0, 3] = (placements or {}).get(uid, 0.0)
        a.insert({"kind": "studio", "uid": uid, "part": next(iter(studios[uid].state.parts))}, T=T)
    return a


def test_table_keeps_old_names_and_numbers():
    assert M.density("PLA") == M.PLA and M.density(None) == M.PLA and M.density("unknown") == M.PLA
    assert M.density("aluminium") == pytest.approx(M.ALU) and M.density("Steel") == pytest.approx(M.STEEL)
    for n in ("PLA", "PETG", "ABS", "Nylon", "Acrylic", "Aluminium 6061", "Mild steel", "Stainless steel 304",
              "Brass", "Copper", "Titanium"):
        assert M.material_name(n) == n


def test_cube_mass_by_material():
    doc, pid = cube("Aluminium 6061")
    assert mass(doc, pid) == pytest.approx(2.70)
    doc2, pid2 = cube()
    assert mass(doc2, pid2) == pytest.approx(1000 * 1.24e-3)


def test_material_survives_save_and_reload(tmp_path):
    doc, pid = cube("Brass")
    path = str(tmp_path / "m.fshape")
    doc.save(path)
    back = Document.load(path)
    assert back.part_overrides[pid]["material"] == "Brass"
    assert back.state.parts[pid].material == "Brass"


def test_bom_carries_material_and_total(tmp_path):
    alu, _ = cube("Aluminium 6061")
    a = asm_of({"u1": alu})
    (row,) = a.bom()
    assert row["material"] == "Aluminium 6061" and row["mass"] == pytest.approx(2.70)
    assert a.bom_total([row]) == {"qty": 1, "mass": pytest.approx(2.70)}
    from freeshape.ui.assembly_ui import BomDialog
    csv = BomDialog.csv(type("B", (), {"rows": [row], "total": a.bom_total([row])})())
    lines = csv.strip().split("\n")
    assert lines[0].split(",")[4] == "Material"
    assert lines[1].split(",")[4] == "Aluminium 6061"
    assert lines[-1].split(",")[1] == "Total" and lines[-1].split(",")[-1] == "2.70"


def test_material_reaches_instance_after_override_change():
    doc, pid = cube()
    a = asm_of({"u1": doc})
    assert a.instances[0]["material"] is None
    doc.set_part_override(pid, material="Copper")
    a.refresh()
    assert a.instances[0]["material"] == "Copper"
    assert a.bom()[0]["mass"] == pytest.approx(1000 * 8.96e-3)


def test_assembly_without_materials_weighs_as_before():
    doc, _ = cube()
    a = asm_of({"u1": doc, "u2": cube()[0]})
    for r in a.bom():
        assert r["material"] is None and r["mass"] == pytest.approx(r["volume"] * 1.24e-3, rel=1e-12)
    assert a.bom_total(a.bom())["mass"] == pytest.approx(2 * 1.24)


def test_step_instance_has_no_material():
    p = only_part(cube("Brass")[0])
    plain = Part(p.id, p.name, p.shape)
    assert getattr(plain, "material", None) is None


def test_whole_assembly_mass_and_centre():
    alu, _ = cube("Aluminium 6061")
    steel, _ = cube("Mild steel")
    a = asm_of({"alu": alu, "steel": steel}, {"alu": 0.0, "steel": 40.0})
    s = a.mass_summary()
    assert s["by_material"] == {"Aluminium 6061": pytest.approx(2.70), "Mild steel": pytest.approx(7.85)}
    assert s["mass"] == pytest.approx(10.55)
    x_alu, x_steel = 5.0, 45.0
    assert s["com"][0] == pytest.approx((2.70 * x_alu + 7.85 * x_steel) / 10.55)
    assert x_alu < s["com"][0] < x_steel and abs(s["com"][0] - x_steel) < abs(s["com"][0] - x_alu)
    a.instances[1]["hidden"] = True
    assert a.mass_summary()["mass"] == pytest.approx(2.70)
