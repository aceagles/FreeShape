"""The Playwright in the gate image must be the one web/ pins: its browsers only run under the same version."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pinned_in_package_json() -> str:
    pkg = json.loads((ROOT / "web" / "package.json").read_text())
    return pkg["devDependencies"]["@playwright/test"]


def test_dockerfile_playwright_equals_web_pin():
    dockerfile = (ROOT / "Dockerfile.harness").read_text()
    found = re.findall(r"^ARG PLAYWRIGHT_VERSION=(\S+)$", dockerfile, re.M)
    assert len(found) == 1, "Dockerfile.harness needs exactly one `ARG PLAYWRIGHT_VERSION=<x.y.z>` line"
    pinned = _pinned_in_package_json()
    assert re.fullmatch(r"\d+\.\d+\.\d+", pinned), f"web/package.json must pin @playwright/test exactly, not {pinned!r}"
    assert found[0] == pinned


def test_dockerfile_sets_a_fixed_browsers_path_and_installs_webkit():
    dockerfile = (ROOT / "Dockerfile.harness").read_text()
    assert re.search(r"^ENV PLAYWRIGHT_BROWSERS_PATH=/opt/\S+$", dockerfile, re.M)
    assert "install --with-deps webkit" in dockerfile


def test_browser_fonts_stay_out_of_the_python_font_dirs():
    # Why: OCCT reads every font under /usr/share/fonts, and the outline of sketch text (so the box-pick tests in
    # tests/test_sketch_extend_spline.py) depends on which font it finds. `playwright install --with-deps` adds
    # about 50 fonts there; the CI gate failed on that once. The Dockerfile must move everything but DejaVu out
    # to /opt/browser-fonts, and the browser scripts must point fontconfig at the moved fonts.
    dockerfile = (ROOT / "Dockerfile.harness").read_text()
    after_install = dockerfile.split("install --with-deps webkit", 1)[1]
    assert "/opt/browser-fonts" in after_install and "/usr/share/fonts/truetype/dejavu) ;;" in after_install
    assert "mv " in after_install and "/opt/browser-fonts.conf" in after_install
    common = (ROOT / "scripts" / "web" / "common.sh").read_text()
    assert "FONTCONFIG_FILE=/opt/browser-fonts.conf" in common
    for script in ("e2e.sh", "shots.sh"):
        assert "web_browser_fonts" in (ROOT / "scripts" / "web" / script).read_text()
