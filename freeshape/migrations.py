"""File-version migration chains for the .fshape / .fasm / .fdwg formats.

Each format owns an ordered list of migration steps; step i (0-based) upgrades a loaded file dict from
version i + 1 to version i + 2. `latest_version` is simply "1 + however many steps exist", so it's always
in sync with the chain -- there's no separate constant to remember to bump. `migrate` applies every step
from the file's own version up to that latest version and stamps the result with it; a file whose version
is already past the latest fails outright, before anything else is read from it, instead of loading some
of it and silently dropping the rest.

Version 1 is today's identity step for every format -- nothing has needed a migration yet. I1.4 (part ids)
and I2.2 (tab-id links) append their steps here, in order, once they need one.

(FreeCAD gates old data inline at each read site with `if reader.FileVersion > N: ...`, rather than through
a shared chain of named steps -- there's no equivalent ordered-migration-list module in its sources to port;
this module follows the same "a version number gates behavior, never a partial load" spirit instead.)
"""
from __future__ import annotations

from .kernel import KernelError

_CHAINS: dict[str, list] = {
    "document": [],
    "assembly": [],
    "drawing": [],
}


def latest_version(fmt: str) -> int:
    return len(_CHAINS[fmt]) + 1


def register(fmt: str, step) -> None:
    """Append a migration step (file dict -> file dict) to a format's chain."""
    _CHAINS[fmt].append(step)


def migrate(fmt: str, data: dict, label) -> dict:
    """Upgrade a loaded file dict to the format's latest version, applying every step the file needs.

    `label` identifies the file in the error raised when its version is newer than this build supports."""
    chain = _CHAINS[fmt]
    latest = len(chain) + 1
    version = data.get("version", 1)
    if version > latest:
        raise KernelError(f"{label} was saved with file version {version}, but this build of FreeShape "
                          f"only supports up to version {latest}. Update FreeShape to open it.")
    for step in chain[version - 1:]:
        data = step(data)
    data["version"] = latest
    return data
