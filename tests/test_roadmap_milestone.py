"""I8.3: the roadmap records the browser client's first milestone."""
import os
import re

ROADMAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ROADMAP.md")


def _text():
    with open(ROADMAP, encoding="utf-8") as fh:
        return fh.read()


def test_editing_in_the_browser_is_no_longer_not_started():
    (row,) = [ln for ln in _text().splitlines() if ln.startswith("| Editing in the browser")]
    assert "Not started" not in row


def test_milestone_section_has_its_three_sub_headings():
    text = _text()
    assert re.search(r"^## Browser client: milestone 1$", text, re.M)
    section = text.split("## Browser client: milestone 1", 1)[1].split("\n## ", 1)[0]
    for name in ("Covered", "Desktop only", "Next"):
        assert re.search(rf"^### {name}$", section, re.M), name
