"""Select other with a tool active (#45): the list holds what the tool can pick under the cursor, and choosing a
row replays the tool's click with that row as the pick, so Equal / Mirror / a dimension take the chosen entity."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import Qt  # noqa: E402

from freeshape.sketch import Sketch  # noqa: E402
from tests.test_sketch_inference import VP, _Ev, _sketcher  # noqa: E402

MID = (430, 300)        # on the stacked lines (y = 0), clear of their end points
APART = (430, 100)      # on the lone line at y = 20


class _Key:
    def __init__(self, key):
        self._k = key

    def key(self):
        return self._k

    def modifiers(self):
        return Qt.NoModifier


class _Menu:
    def __init__(self):
        self.actions = {}

    def addAction(self, label, fn=None):
        self.actions[label] = fn


def _setup():
    """A lone line at y = 20 and three lines stacked on y = 0 (one solid, two construction), all free."""
    sk = Sketch()
    lone = sk.add_line(sk.add_point(-10, 20), sk.add_point(10, 20))
    stacked = []
    for half, cons in ((10, False), (15, True), (20, True)):
        stacked.append(sk.add_line(sk.add_point(-half, 0), sk.add_point(half, 0), cons))
    s = _sketcher(sk)
    made = []
    s.popup_factory = lambda vp, x, y, rows, hov, ch, cl: made.append((rows, hov, ch, cl)) or made[-1]
    return s, lone, stacked, made


def _choose(made, payload, mods=Qt.NoModifier):
    _rows, _hov, choose, close = made[-1]
    close()                                  # the real popup closes before it reports the choice
    choose(payload, mods)


def _equal_pairs(s):
    return [set(c["refs"]) for c in s.sk.constraints if c["type"] == "equal"]


def test_equal_takes_the_line_chosen_from_select_other_not_the_one_on_top():
    s, lone, stacked, made = _setup()
    s.set_tool("equal")
    s.mouse_press(VP, *APART, _Ev())                         # first pick: the lone line
    assert s.cons_picks == [lone]
    top = s.hit(VP, *MID, kinds=("point", "entity"))[1]      # what a plain click would take
    s.open_select_other(VP, *MID)
    rows = made[-1][0]
    assert sorted(i for _l, i in rows) == sorted(stacked)
    want = next(i for i in stacked if i != top)
    _choose(made, want)
    assert _equal_pairs(s) == [{lone, want}]
    assert s.cons_picks == [] and s.selection == []


def test_right_click_and_backtick_offer_select_other_with_equal_active():
    s, _lone, _stacked, made = _setup()
    s.set_tool("equal")
    m = _Menu()
    s.add_context_actions(m, VP, *MID)
    assert "Select other…" in m.actions
    m.actions["Select other…"]()
    assert len(made) == 1
    s.mouse_move(VP, *MID, _Ev())
    assert s.key_press(VP, _Key(Qt.Key_QuoteLeft)) and len(made) == 2


def test_hover_highlights_the_row_and_close_clears_it_with_a_tool_active():
    s, _lone, stacked, made = _setup()
    s.set_tool("equal")
    s.open_select_other(VP, *MID)
    _rows, hover, _choose, close = made[-1]
    hover(stacked[2])
    assert s.hover == stacked[2]
    close()
    assert s.hover is None


def test_mirror_takes_the_chosen_stacked_line_as_its_axis():
    s, lone, stacked, made = _setup()
    s.set_tool("sk_mirror")
    s.selection = [lone]
    n = len(s.sk.entities)
    s.open_select_other(VP, *MID)
    _choose(made, stacked[1])
    assert len(s.sk.entities) == n + 1 and s.tool == "select"
    copy = next(e for i, e in s.sk.entities.items() if i not in (lone, *stacked))
    ys = sorted(round(s.sk.P(copy[k])[1], 6) for k in ("p1", "p2"))
    assert ys == [-20.0, -20.0]                              # the lone line at y = 20, mirrored about y = 0


def test_dimension_first_pick_is_the_chosen_row():
    s, _lone, stacked, made = _setup()
    s.set_tool("dimension")
    s.open_select_other(VP, *MID)
    _choose(made, stacked[2])
    assert s.dim_first == stacked[2]


def test_drawing_tools_have_no_list():
    s, _lone, _stacked, made = _setup()
    s.set_tool("line")
    m = _Menu()
    s.add_context_actions(m, VP, *MID)
    assert "Select other…" not in m.actions
    assert s.open_select_other(VP, *MID) is None and made == []
