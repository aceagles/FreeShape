"""Checks on docs/reference/element-naming.md, the element-naming spec ported from FreeCAD 1.0.

The spec is the only record of the naming rules later work implements, so it is worth guarding
mechanically: the nine sections must all be present, sections 1-8 must each cite the reference
source, and every citation must point at a line that actually exists.

The reference source lives at /opt/freecad-src in the harness image only. Outside it, the
citation targets cannot be checked and that one check is skipped; the rest still runs.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "docs/reference/element-naming.md"
ADR = REPO / "docs/adr/0003-element-naming-port-of-freecad-element-map.md"
FREECAD_SRC = Path("/opt/freecad-src")

# "## 3. History sources"
HEADING = re.compile(r"^## (\d+)\. +(.+?)\s*$", re.MULTILINE)
# "/opt/freecad-src/src/App/ElementMap.cpp:656"
CITATION = re.compile(r"/opt/freecad-src/([A-Za-z0-9_./+-]+?):(\d+)")

SECTIONS = {
    1: "Name grammar",
    2: "Composition",
    3: "History sources",
    4: "Sketch to solid",
    5: "Split and merge",
    6: "Deleted elements",
    7: "Copies",
    8: "Joint frame",
    9: "Scenarios",
}

# Scenario -> a word that must appear on the same line, so a renamed scenario is caught too.
SCENARIOS = [
    "sketch dimension change",
    "sketch curve added",
    "face split by an inserted hole",
    "faces merged by an edit",
    "inserted part-creating feature",
    "consumed edge",
    "twin edges",
    "mirrored copy",
    "hole through a fillet",
]


def spec_text():
    assert SPEC.is_file(), f"{SPEC} is missing"
    text = SPEC.read_text(encoding="utf-8")
    assert text.strip(), f"{SPEC} is empty"
    return text


def spec_sections():
    """{number: body text} for every numbered '## N. Title' heading, in file order."""
    text = spec_text()
    matches = list(HEADING.finditer(text))
    bodies = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        bodies[int(m.group(1))] = text[m.end():end]
    return bodies


def test_adr_present():
    assert ADR.is_file(), f"{ADR} is missing"
    assert ADR.read_text(encoding="utf-8").strip(), f"{ADR} is empty"


def test_source_revision_in_first_lines():
    """The spec has to say which FreeCAD it describes, up front."""
    head = "\n".join(spec_text().splitlines()[:5])
    assert "1.0.2" in head, f"tag 1.0.2 not stated in the first lines:\n{head}"
    assert "256fc7ef" in head, f"commit 256fc7ef not stated in the first lines:\n{head}"


def test_nine_numbered_sections():
    found = {num: title for num, title in HEADING.findall(spec_text())}
    found = {int(num): title for num, title in found.items()}
    assert found == SECTIONS


@pytest.mark.parametrize("number", sorted(n for n in SECTIONS if n <= 8))
def test_section_cites_the_reference_source(number):
    body = spec_sections()[number]
    assert CITATION.search(body), (
        f"section {number} ({SECTIONS[number]}) cites no /opt/freecad-src/<path>:<line>"
    )


def test_every_citation_points_at_an_existing_line():
    citations = sorted(set(CITATION.findall(spec_text())))
    assert citations, "the spec cites nothing"
    if not FREECAD_SRC.is_dir():
        pytest.skip(
            f"{FREECAD_SRC} is absent (not the harness image): checked that "
            f"{len(citations)} citations are well formed, but not that the files exist"
        )
    missing, short = [], []
    for relative, line in citations:
        path = FREECAD_SRC / relative
        if not path.is_file():
            missing.append(relative)
            continue
        count = sum(1 for _ in path.open("rb"))
        if int(line) > count:
            short.append(f"{relative}:{line} (file has {count} lines)")
    assert not missing, f"cited files not in {FREECAD_SRC}: {missing}"
    assert not short, f"citations past the end of the file: {short}"


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_scenario_named_with_its_provenance(scenario):
    """Section 9 lists each scenario with the FreeCAD test it came from, or says there is none."""
    body = spec_sections()[9]
    lines = [line for line in body.splitlines() if scenario in line.lower()]
    assert lines, f"section 9 does not name the {scenario!r} scenario"
    for line in lines:
        assert "test" in line.lower(), (
            f"the {scenario!r} entry names neither a FreeCAD test nor 'no FreeCAD test': {line}"
        )
