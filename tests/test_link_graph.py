"""The Link graph: the engine records an edge each time it hands a tab another tab's output, and when a tab
changes it rebuilds the tabs that consume it -- only those whose consumed output hash changed, in topological
order (graphlib), each once. Per-tab rebuild counters are exposed by the engine (`rebuild_count`).

The engine tests run on both executors, `inprocess` and `process` (one worker process per tab). Starting a
worker costs `import OCP`, so each test opens as few tabs as it can."""
import pytest

from conftest import add, rect_lines, region_refs, sketch_feature
from freeshape import kernel as K
from freeshape import links
from freeshape.assembly import AssemblyDoc
from freeshape.document import Document
from freeshape.engine import InProcessEngine, LinkCycleError, ProcessEngine
from freeshape.engine import process as P
from freeshape.variable_studio import VariableStudioDoc

ENGINES = {"inprocess": InProcessEngine, "process": ProcessEngine}
REF = "variable_studio_ref"


@pytest.fixture(scope="module", autouse=True)
def _no_spares_left():
    yield
    P.SPARES.clear()


@pytest.fixture(params=sorted(ENGINES))
def engine(request):
    e = ENGINES[request.param]()
    yield e
    e.close()


def set_w(value):
    return {"op": "set_row", "index": 0, "expression": value}


def make_v(tmp_path):
    v = VariableStudioDoc()
    v.add_row("w", "20 mm")
    path = str(tmp_path / "V.fvars")
    v.save(path)
    return path


def make_studio(tmp_path, name, vpath, depth):
    """A Part Studio that reads V's `w` and extrudes a 10 x 10 square by `depth` (an expression)."""
    a = Document()
    a.name = name
    a.var_inputs = lambda src: {"w": 20.0}              # only to build the file: the engine resolves it for real
    a.add_feature({"id": a.new_id(), "type": REF, "name": "Variables", "suppressed": False,
                   "params": {"source": {"kind": "variables", "path": vpath, "name": "V"}}})
    sid = sketch_feature(a, lambda s: rect_lines(s, 0, 0, 10, 10))
    add(a, "extrude", regions=region_refs(a, sid), depth=depth, op="new")
    path = str(tmp_path / f"{name}.fshape")
    a.save(path)
    return path, a


def height(part):
    lo, hi = K.bbox(K.shape_from_brep(part.brep))
    return round(float(hi[2] - lo[2]), 6)


def studio_height(engine, tab):
    out = engine.outputs(tab)
    assert not out.errors, out.errors
    return height(out.parts[0])


# =========================================================================== counters
def test_edit_rebuilds_only_the_consumers_whose_input_hash_changed(engine, tmp_path):
    vpath = make_v(tmp_path)
    apath, _doc = make_studio(tmp_path, "A", vpath, "#w")
    v, a, e = engine.open_tab(vpath, tab="V"), engine.open_tab(apath, tab="A"), engine.new_tab("studio", tab="E")
    assert studio_height(engine, a) == 20
    assert ("A", "V") in engine.link_edges()
    assert engine.rebuild_counts() == {}

    engine.submit_edit(v, set_w("35 mm"))               # a new value: A rebuilds once, the unrelated E not at all
    assert engine.rebuild_count(a) == 1 and engine.rebuild_count(e) == 0
    assert engine.rebuild_order() == ["A"]
    assert studio_height(engine, a) == 35               # V was never saved: A uses the edit

    engine.submit_edit(v, set_w("35mm"))                # the same value, written differently: same output hash
    engine.submit_edit(v, set_w("0.035 m"))
    assert engine.rebuild_count(a) == 1 and engine.rebuild_order() == []

    engine.submit_edit(v, set_w("40 mm"))
    assert engine.rebuild_count(a) == 2 and engine.rebuild_count(e) == 0
    assert studio_height(engine, a) == 40


# =========================================================================== fan-in
@pytest.mark.parametrize("a_first", [True, False], ids=["A-before-B", "B-before-A"])
def test_diamond_rebuilds_both_branches_before_the_join_and_is_not_a_cycle(engine, tmp_path, a_first):
    """V feeds studios A and B; assembly C instances a part of each. The assembly's links name the studios
    by tab id only (no uid, no path)."""
    vpath = make_v(tmp_path)
    apath, adoc = make_studio(tmp_path, "A", vpath, "#w")
    bpath, bdoc = make_studio(tmp_path, "B", vpath, "#w * 2")
    asm = AssemblyDoc()
    asm.name = "C"
    docs = {"A": adoc, "B": bdoc}
    asm.resolver = lambda src: docs[src["tab"]].state.parts[src["part"]]
    for tid, doc in docs.items():
        asm.insert({"kind": "studio", "tab": tid, "part": next(iter(doc.state.parts)), "studio_name": tid})
    cpath = str(tmp_path / "C.fasm")
    asm.save(cpath)

    v = engine.open_tab(vpath, tab="V")
    for tid, path in (("A", apath), ("B", bpath)) if a_first else (("B", bpath), ("A", apath)):
        engine.open_tab(path, tab=tid)
    c = engine.open_tab(cpath, tab="C")

    def heights():
        out = engine.outputs(c)
        assert not out.errors, out.errors
        return [height(out.parts[i.part]) for i in out.instances]       # C's instances: A's part, B's part

    assert heights() == [20, 40]
    assert {("A", "V"), ("B", "V"), ("C", "A"), ("C", "B")} <= set(engine.link_edges())
    assert engine.rebuild_counts() == {}

    engine.submit_edit(v, set_w("35 mm"))               # no LinkCycleError: a fan-in is not a cycle
    order = engine.rebuild_order()
    assert sorted(order) == ["A", "B", "C"]
    assert order.index("C") == 2                        # A and B first, then C
    assert [engine.rebuild_count(t) for t in "ABC"] == [1, 1, 1]
    assert heights() == [35, 70]

    engine.submit_edit(v, set_w("10 mm"))
    assert [engine.rebuild_count(t) for t in "ABC"] == [2, 2, 2]
    assert heights() == [10, 20]


# =========================================================================== cycles
def test_a_cycle_is_an_error_naming_its_tabs_and_a_tab_outside_it_still_edits(engine, tmp_path):
    x, y, z = (engine.new_tab("variables", tab=t) for t in "XYZ")
    engine.links.record_edge("X", "Y", declared=True)   # X consumes Y and Y consumes X
    engine.links.record_edge("Y", "X", declared=True)
    with pytest.raises(LinkCycleError) as err:
        engine.submit_edit(x, {"op": "add_row", "name": "a", "expression": "1"})
    assert "X" in str(err.value) and "Y" in str(err.value)
    assert set(err.value.tabs) == {"X", "Y"}
    assert engine.outputs(x).variables == {"a": 1}      # the edit itself was applied
    engine.submit_edit(z, {"op": "add_row", "name": "b", "expression": "2"})      # Z is not in the cycle


def test_graph_orders_a_chain_and_a_fan_in_and_reports_a_longer_cycle():
    g = links.LinkGraph()
    for consumer, upstream in (("B", "V"), ("A", "V"), ("C", "A"), ("C", "B"), ("D", "C"), ("D", "V")):
        g.add(consumer, upstream, links.Edge("studio", (), None))
    order = g.order("V")
    assert sorted(order) == ["A", "B", "C", "D"]
    assert order.index("C") > max(order.index("A"), order.index("B")) and order[-1] == "D"
    assert g.order("D") == [] and g.order("A") == ["C", "D"]
    g.add("V", "D", links.Edge("studio", (), None))     # V consumes D: V -> A -> C -> D -> V
    with pytest.raises(links.LinkCycle) as err:
        g.order("A")
    assert {"A", "C", "D", "V"} <= set(err.value.tabs)
