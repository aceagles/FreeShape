"""Preview icons for list and tree rows (I6.2): the Derived dialog's part, sketch and connector lists, the Insert
panel and the Other Document picker. A row gets a placeholder at once; `PreviewFiller` draws the real preview
(ui.preview.render_preview) one row per event-loop turn once its widget is shown, so a dialog never blocks.

Finished icons live in a process-wide cache keyed by (content key, size), so reopening a dialog draws nothing:
`stats["drawn"]` counts the previews actually rendered. `drain()` finishes every pending row at once (tests)."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from ..engine.contract import ConnectorOut, PartOut
from . import preview as PV

DERIVED_SIZE = 96     # px of a preview in the Derived dialog's tick lists (hand check "Dialog previews read clearly at 96 px")
INSERT_SIZE = 64      # px of a preview in the Insert panel and the Other Document picker (a 300 px wide panel)

stats = {"drawn": 0}
_icons: dict = {}
_placeholders: dict = {}
_encoder = None


def placeholder_icon(size):
    ic = _placeholders.get(size)
    if ic is None:
        pm = QPixmap(size, size)
        pm.fill(QColor(235, 237, 240))
        p = QPainter(pm)
        p.setPen(QPen(QColor(190, 195, 203), 1.0, Qt.DashLine))
        p.drawRect(2, 2, size - 5, size - 5)
        p.end()
        ic = _placeholders[size] = QIcon(pm)
    return ic


def clear_icon_cache():
    _icons.clear()


def _brep_hash(shape):
    global _encoder
    if _encoder is None:
        from ..engine.encode import Encoder
        _encoder = Encoder(meshes=False)
    return _encoder.brep(shape)[1]


def _stub(part, digest):
    return PartOut(id=str(part.id), name=str(part.name), color=tuple(float(c) for c in part.color), hidden=False,
                   brep=b"", hash=digest, names={}, mesh=None)


def _meshed(part, digest):
    from ..engine.encode import mesh_out
    out = _stub(part, digest)
    out.mesh = mesh_out(part.shape)
    return out


def part_job(part, size):
    """(key(), make()) for a PublishedPart or a features.Part: the key reads the part's hash, make draws it."""
    def key():
        digest = getattr(part, "hash", None) or _brep_hash(part.shape)
        return PV.content_hash(_stub(part, digest))
    return key, lambda: PV.render_preview(_meshed(part, getattr(part, "hash", None) or _brep_hash(part.shape)), size)


def sketch_job(sketch, size):
    return (lambda: PV.content_hash(sketch)), lambda: PV.render_preview(sketch, size)


def connector_job(pkg, c, size):
    """For a published connector dict {name, part, frame, hash} of a StudioPackage, drawn on its part."""
    pp = pkg.parts.get(pkg.aliases.get(c["part"], c["part"]))

    def key():
        return "connector:" + str(c.get("hash")) + ":" + (pp.hash if pp is not None else "")

    def make():
        con = ConnectorOut(instance="", kind="", index=None, frame=c["frame"], world=c["frame"])
        return PV.render_preview(con, size, part=_meshed(pp, pp.hash) if pp is not None else None)
    return key, make


class PreviewFiller(QObject):
    """Fills the icons of the rows of one list or tree. `add(setter, key, make)`: `setter(QIcon)` puts an icon on the
    row; `key()` is the content key (cheap), `make()` the QImage to draw on a miss."""

    def __init__(self, widget, size):
        super().__init__(widget)
        self.widget, self.size = widget, size
        self.pending = []
        self._queued = False
        self.host = None
        widget.installEventFilter(self)

    def watch(self, host):
        """Fill while `host` is shown, not only while the list itself is: a list on a tab that is not the current
        one is hidden, and its previews should be ready when the tab is opened."""
        self.host = host
        host.installEventFilter(self)
        self._schedule()

    def _shown(self):
        return (self.host or self.widget).isVisible()

    def reset(self):
        """The rows were rebuilt: forget the pending ones."""
        self.pending.clear()

    def add(self, setter, key, make):
        setter(placeholder_icon(self.size))
        self.pending.append((setter, key, make))
        self._schedule()

    def eventFilter(self, obj, ev):
        if (obj is self.widget or obj is self.host) and ev.type() == QEvent.Show:
            self._schedule()
        return False

    def _schedule(self):
        if self.pending and not self._queued and self._shown():
            self._queued = True
            QTimer.singleShot(0, self._tick)

    def _tick(self):
        self._queued = False
        if self.pending:
            self._one()
        self._schedule()

    def _one(self):
        setter, key, make = self.pending.pop(0)
        try:
            k = (key(), self.size)
            ic = _icons.get(k)
            if ic is None:
                img = make()
                stats["drawn"] += 1
                ic = _icons[k] = QIcon(QPixmap.fromImage(img))
            setter(ic)
        except RuntimeError:        # the row was deleted meanwhile
            pass
        except Exception:           # nothing to draw: the placeholder stays
            pass

    def drain(self):
        while self.pending:
            self._one()
