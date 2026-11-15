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
