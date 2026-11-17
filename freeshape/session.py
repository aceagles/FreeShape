"""A headless Document session (I5.3): one open Document, its tabs live in an engine, with no Qt (ADR 0002, 0009, 0010).

    with DocumentSession(FolderStore(path)) as s:        # or DocumentSession("/path/to/Document")
        s.tabs()                                         # [{"id", "kind", "name", "state", "error", "dirty"}, ...]
        s.submit_edit("T2", {"op": "set_row", "index": 0, "expression": "30 mm"})   # -> ["T2", "T3", "T4"]
        s.outputs("T4")                                  # a contract.TabOutputs of that tab
        s.save()                                         # edits are in the engine until this writes them

The session reads the Document's manifest from the store, starts a `ProcessEngine` (one worker process per tab) and
opens every tab by data, under its manifest tab id, with the tab's blob lookup from the store (`store.blobs(id)`).
Links inside the Document name tab ids, so they resolve by id exactly as in the GUI (`LiveTabs`). No tab file is
read from disk by the engine; the store is the only reader and writer.

Open order. A tab is opened after the tabs it consumes: the Link graph records an edge when a tab is handed its
inputs, and a tab opened before its inputs would start with none. The order is a topological sort of the link
sources in each tab's data (a Variable Studio reference, an Assembly instance, a Drawing source) by tab id, then by
uid, ties and cycles in manifest order. A tab that cannot be opened (bad data, a worker that fails to load it)
does not stop the others: its state is "failed" and `error` says why.

State of a tab (`tabs()[i]["state"]`):
    "ok"       the tab's worker is running and its outputs report no error
    "error"    the worker is running and the tab's outputs carry errors (a feature, row or instance failed);
               `error` is the first message, `outputs(tab).errors` has them all
    "crashed"  the tab's worker process stopped; the next call restarts it once from its bytes and edit log
    "failed"   the tab did not open; it has no worker and no outputs

Rebuilds. `submit_edit` applies the edit and then calls the engine's `rebuild_dependents`, synchronously: when
`ProcessEngine.submit_edit` returns, every consumer whose consumed output changed has been rebuilt, each once,
upstream first. Nothing is polled, so there is no settle timeout to tune (a call that never returns is a stuck
worker, see `edit_timeout`). The result is the edited tab followed by `engine.rebuild_order()`.

Saving. `save()` writes each open tab's `tab_data` through `store.write_tab`, stamped with `_tab_id` as
`FolderDocument._tab_bytes` does (same JSON, indent 1), and the blobs the data names through `store.blobs(id)`.
It does not touch a tab that failed to open. An edit that was not saved is not in the store: a fresh session
shows the old Document.

Tabs (I2.4). `add_tab`, `rename_tab` and `delete_tab` change the tab list. The manifest is edited by a
manifest-only `FolderDocument` (`_fd`, tabs with no loaded document) so the ids, file names and manifest bytes
are the desktop's own. Nothing reaches the store until `save()`: it writes the new tabs' files, removes the files
of deleted tabs, then rewrites the manifest (only when the tab list changed, so a save with no tab change leaves
the manifest bytes alone). A deleted tab's worker ends at once; an added tab that is never saved leaves nothing
on disk. `tabs_unsaved()` says whether the list has changes `save()` has not written.

Autosave (I2.6). `autosave_state()` gives what the server's autosave (freeshape/autosave.py) writes: the data of
every dirty tab, exactly the bytes `save()` would write, the tab list and the blobs the data names. `restore_autosave`
puts such a state back on a freshly opened session as unsaved edits and never writes the store. The session
remembers the sha256 of each tab file and of the manifest as it loaded or last wrote them (`_base`), so an autosave
can say which tabs the saved Document has changed since (`autosave_moved_on`).

Threads. The session does no locking. Call it from one thread at a time: a caller with several threads (the
serve routes of I5.4) must hold one lock around every call. The engine underneath is single-threaded by contract.

Imports no Qt (the test checks sys.modules in a fresh interpreter).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import TimeoutError as FutureTimeout

from . import doc_folder as DF
from . import docstore as DS
from . import kernel as K
from .engine import EngineError, LinkCycleError, ProcessEngine, TabErrored
from .engine.contract import BadPick
from .engine import wire
from .engine.executor import read_data
from . import links
from . import dialog_schema as DSC
from . import features as F
from . import picking as PK
from . import mate_session as MS
from .expr import ExprError, fmt, has_vars, parse_number
from . import tabkinds as TK
from .docstore import ChainedBlobs, HeldBlobs

STATES = ("ok", "error", "crashed", "failed")
NEW_KINDS = ("studio", "assembly")      # what `add_tab` makes
MAX_TAB_NAME = 100                      # longest tab name `check_tab_name` accepts


def check_tab_name(name):
    """The tab name `name` as stored (stripped), or SessionError. Text of 1..MAX_TAB_NAME characters with no
    control character. It is a label, never a file name: see `tab_file_stem`."""
    if not isinstance(name, str):
        raise SessionError("the tab name must be text")
    text = name.strip()
    if not text:
        raise SessionError("the tab name is empty")
    if len(text) > MAX_TAB_NAME:
        raise SessionError(f"the tab name is longer than {MAX_TAB_NAME} characters")
    if any(not c.isprintable() for c in text):
        raise SessionError("the tab name may not contain a control character")
    return text


def tab_file_stem(name, fallback):
    """The file name stem for a tab called `name`: only letters, digits, space, `-`, `_` and `.` survive (the rest
    becomes `-`), runs collapse, leading and trailing `.`, `-` and space go, at most 60 characters; `fallback` when
    nothing is left. Never holds a separator and never starts with a dot."""
    out = "".join(c if (c.isalnum() or c in " _.-") else "-" for c in name)
    stem = re.sub(r"-{2,}", "-", re.sub(r" {2,}", " ", out)).strip(".- ")[:60].strip(".- ")
    return stem or fallback


class SessionError(Exception):
    """A call on a session that cannot be done: an unknown tab, an edit the tab refuses, a tab that did not
    open. The message says which and why; the session stays usable."""


class TabLinked(SessionError):
    """`delete_tab` of a tab other tabs link to. `tab` is its id, `linked_by` is [{"id", "name"}]."""

    def __init__(self, tab, linked_by):
        self.tab, self.linked_by = tab, linked_by
        names = ", ".join(f"{t['name']!r} ({t['id']})" for t in linked_by)
        super().__init__(f"tab {tab!r} cannot be deleted: linked from {names}")


class SaveRefused(SessionError, EngineError):
    """A `save` edit sent to `submit_edit`: both a SessionError (serve answers 400) and an EngineError."""


MAX_DIALOG_ITEMS = 200            # items one selection field of a dialog may hold
MAX_DIALOG_TEXT = 500             # longest text a dialog's text or expression field takes


class _Items(list):
    """The items of a selection row as `check` parses them. `stamp` (I10.4) is the stamp a Mate connector's pick came
    with, or None."""
    stamp = None


class FeatureDialog:
    """The state of one open feature dialog (I5.3): the feature's params, its selection fields and what the person
    typed in each number field. It holds no shape and no engine call: the model is only needed to open it
    (`DocumentSession.open_dialog`) and to accept it (the replace_feature edit). The server keeps it in the
    client's registry entry. Call it from one thread at a time (the Document's session lock).

    `values()` is the reading the client shows: {key: {"value", "text", "error"}} for a number field (`value` the
    evaluated number or None, `text` what was typed or the stored expression, `error` the message of the last
    rejected input, else absent) and {key: {"value"}} for the other single-value rows. `fields()` is {key: [item]}
    for the selection rows; an item is {"ok": true, "kind", "ref", "name"}, as the refs route answers."""

    def __init__(self, tab, feature, variables, defaults, fields=None, extra=None, context=None, owners=None):
        """`feature` without an "id" is a NEW feature (I6.1, create mode): `fid` is None, the engine gives it its id
        when it is added, and a preview asks for a new feature. `fields` ({key: [(kind, ref)]}) and `extra` (params)
        are what the picks it was started with chose (apply_preselection). `context` (a Derived, I10.2) is a
        callable params -> dialog_schema.DerivedContext: what the Document offers the dialog's rows. `owners` (a Mate
        connector, I10.4) is [(part id, name)], [(sketch id, name)]: what its Owner part row lists."""
        self.tab = tab
        self.fid = feature.get("id")
        self.is_new = self.fid is None
        self.feature = feature
        self.type = feature["type"]
        self.variables = dict(variables)
        p = self.feature.setdefault("params", {})
        for k, v in (defaults or {}).items():             # settings added in later versions: the desktop's rule
            p.setdefault(k, v)
        if self.type == "loft":
            DSC._loft_unfold(p)
            if F.front_kind(self.feature):
                p["guides_on"] = True
        self.params = p
        self.fields_ = DSC.params_to_fields(self.type, p)
        if fields or extra:
            p.update(extra or {})
            for key, items in (fields or {}).items():
                if key in self.fields_:
                    self.fields_[key] = [(k, r) for k, r in items]
            DSC.fields_to_params(self.type, self._field_dict(), p)
        self.texts = {}                                    # key -> text of a number field, as typed
        self.errors = {}                                   # key -> why the last input to it was refused
        self.specs = {s["key"]: s for s in DSC._schema_for(self.type, p) if s.get("key")}
        self.preview = None      # the newest preview answered (I5.4): {"n", "data": wire bytes or None, "error"}
        self.owners = owners if self.type == "mate_connector" else None      # I10.4: (parts, sketches) of the model
        self.context = context if self.type == "derived" else None
        self.derived = None      # the DerivedContext the rows show (I10.2); read again when the source changes
        self.note = None         # the Derived's note after an untick (None: the standing note)
        if self.context is not None:
            self.derived = self.context(p)
            DSC._write_placement(p, self.derived)          # the desktop's Placement base list does this on open

    # ---------------------------------------------------------------- reading
    def description(self):
        if self.derived is not None:
            return DSC.derived_describe(self.params, self.derived, self.note)
        if self.owners is not None:
            return DSC.mate_describe(self.params, DSC.mate_owner_options(self.params, *self.owners))
        return DSC.describe(self.type, self.params)

    def values(self):
        if self.derived is not None:
            out = DSC.derived_values(self.params, self.derived)
            for key, message in self.errors.items():
                out[key]["error"] = message
            return out
        out = {}
        for key, spec in self.specs.items():
            kind = spec["t"]
            if kind == "owner_part" and self.owners is not None:           # I10.4: a choice in the server's dialog
                out[key] = {"value": DSC.mate_owner_value(self.params, DSC.mate_owner_options(self.params, *self.owners))}
                continue
            if kind not in ("num", "choice", "ops", "check", "text"):
                continue
            raw = self.params.get(key)
            if kind == "num":
                text = self.texts.get(key)
                if text is None:
                    text = "" if raw is None else (raw if isinstance(raw, str) else fmt(raw))
                try:
                    value = parse_number(raw, angle=spec.get("unit") == "deg", variables=self.variables) \
                        if raw is not None else None
                except (TypeError, ValueError):
                    value = None
                entry = {"value": value, "text": text}
                if key in self.errors:
                    entry["error"] = self.errors[key]
            elif kind == "choice":
                entry = {"value": DSC._plain(self._choice_value(spec, raw))}
            elif kind == "ops":
                entry = {"value": DSC._plain(raw if raw is not None else spec.get("default"))}
            else:
                entry = {"value": DSC._plain(raw)}
            out[key] = entry
        return out

    @staticmethod
    def _choice_value(spec, raw):
        """What a choice row shows: the stored value if it is an option, else the first option (the desktop combo's
        rule: a missing or unknown value picks index 0), so the row is never blank."""
        options = spec.get("options") or []
        if any(raw == v for v, _label in options):
            return raw
        return options[0][0] if options else raw

    def fields(self):
        if self.derived is not None:
            return {}
        out = {}
        for key, spec in self.specs.items():
            if spec["t"] == "sel":
                out[key] = [self._item(k, r) for k, r in self.fields_.get(key, [])]
        return out

    @staticmethod
    def _item(kind, ref):
        return {"ok": True, "kind": kind, "ref": PK.jsonable(ref), "name": PK.item_name((kind, ref))}

    # ---------------------------------------------------------------- changing
    def check(self, changes):
        """Validate the shape of `changes` ({key: value}) without applying it: SessionError for an unknown or
        hand-built key, a number-box row or a value of the wrong type. Returns {key: parsed}, a number field's
        value being its text for now."""
        if not isinstance(changes, dict) or not 1 <= len(changes) <= 64:
            raise SessionError("values: must be an object of 1 to 64 field values")
        if self.derived is not None:
            return self._check_derived(changes)
        out = {}
        for key, value in changes.items():
            at = f"values.{str(key)[:40]}"
            spec = self.specs.get(key)
            if spec is None:
                raise SessionError(f"{at}: no such field")
            kind = spec["t"]
            if kind == "num":
                if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                    raise SessionError(f"{at}: a number field takes text or a number")
                if isinstance(value, str) and len(value) > MAX_DIALOG_TEXT:
                    raise SessionError(f"{at}: text over {MAX_DIALOG_TEXT} characters")
            elif kind == "check":
                if not isinstance(value, bool):
                    raise SessionError(f"{at}: must be true or false")
            elif kind in ("choice", "ops"):
                if not any(value == o[0] and isinstance(value, bool) == isinstance(o[0], bool)
                           for o in spec.get("options") or []):
                    raise SessionError(f"{at}: not one of the options")
            elif kind == "text":
                if not isinstance(value, str) or len(value) > MAX_DIALOG_TEXT:
                    raise SessionError(f"{at}: must be text of at most {MAX_DIALOG_TEXT} characters")
            elif kind == "owner_part" and self.owners is not None:        # I10.4
                if not isinstance(value, str) or value not in [o[0] for o in DSC.mate_owner_options(self.params, *self.owners)]:
                    raise SessionError(f"{at}: not one of the options")
            elif kind == "sel":
                if spec.get("number"):
                    raise SessionError(f"{at}: a field with a number per item cannot be set yet")
                out[key] = self._items(at, value)
                continue
            else:
                raise SessionError(f"{at}: this row cannot be set here")
            out[key] = value
        return out

    def _check_derived(self, changes):
        """check() for a Derived: a choice takes one of its options, a tick list a list of the ids it lists."""
        rows = {f["key"]: f for f in self.description()["fields"] if f["kind"] in ("choice", "ticks")}
        for key, value in changes.items():
            at = f"values.{str(key)[:40]}"
            spec = rows.get(key)
            if spec is None:
                raise SessionError(f"{at}: no such field")
            ids = [o[0] for o in spec["options"]]
            if spec["kind"] == "choice":
                if not isinstance(value, str) or value not in ids:
                    raise SessionError(f"{at}: not one of the options")
            elif not isinstance(value, list) or len(value) > MAX_DIALOG_ITEMS \
                    or not all(isinstance(v, str) and v in ids for v in value):
                raise SessionError(f"{at}: must be a list of the ids the list shows")
        return dict(changes)

    def _apply_derived(self, parsed):
        errors = {}
        self.note = None
        for key, value in parsed.items():
            self.errors.pop(key, None)
            got = DSC.derived_set(key, value, self.params, self.derived)
            if got.error:
                errors[key] = self.errors[key] = got.error
            if got.note is not None:
                self.note = got.note
            if got.reread:
                self.derived = self.context(self.params)
        return errors, None

    @staticmethod
    def _items(at, value):
        if not isinstance(value, list) or len(value) > MAX_DIALOG_ITEMS:
            raise SessionError(f"{at}: must be a list of at most {MAX_DIALOG_ITEMS} items")
        items = _Items()
        for i, it in enumerate(value):
            if not isinstance(it, dict) or set(it) - {"kind", "ref", "ok", "name", "stamp"} or "kind" not in it \
                    or "ref" not in it:
                raise SessionError(f"{at}[{i}]: an item is {{\"kind\", \"ref\"}}")
            if not isinstance(it["kind"], str) or not 1 <= len(it["kind"]) <= 20:
                raise SessionError(f"{at}[{i}].kind: must be a short text")
            if it.get("stamp") is not None:             # I10.4: a Mate connector's pick brings its click and X
                try:
                    items.stamp = DSC.mate_stamp(it["stamp"])
                except ValueError as e:
                    raise SessionError(f"{at}[{i}].stamp: {e}") from e
            items.append((it["kind"], it["ref"]))
        return items

    def apply(self, parsed):
        """Apply what `check` returned, key by key. A number that does not read or is out of range is refused
        with its message and the last good value stays. Returns (errors {key: message}, active field or None)."""
        if self.derived is not None:
            return self._apply_derived(parsed)
        errors, active = {}, None
        for key, value in parsed.items():
            spec = self.specs[key]
            self.errors.pop(key, None)
            if spec["t"] == "sel":
                was = list(self.fields_.get(key, []))
                self.fields_[key] = value
                DSC.fields_to_params(self.type, self._field_dict(), self.params)
                stamp = getattr(value, "stamp", None)
                if self.type == "mate_connector" and key == "element" and value and (stamp or list(value) != was):
                    # I10.4: the stamp of the pick (or none: a stale click and X reference belong to the old element)
                    self.params.update(stamp or {k: None for k in DSC.MATE_STAMP_KEYS})
                continue
            if spec["t"] == "owner_part" and self.owners is not None:     # I10.4
                DSC.mate_set_owner(value, self.params)
                continue
            if spec["t"] == "num":
                try:
                    value = self._number(spec, value)
                except ExprError as e:
                    errors[key] = self.errors[key] = str(e)
                    continue
            before = dict(self.params)
            got = DSC.on_field_set(self.type, key, value, self._field_dict(), self.params)
            self.params.clear()
            self.params.update(got.params)
            self.fields_ = got.fields
            if self.type == "canvas":
                self._couple_sizes(key, value, before)
            if got.active is not None:
                active = got.active
        return errors, active

    def _couple_sizes(self, key, value, before):
        """Keep aspect ratio (I12.2): the other size follows `key`. A size typed as an expression that names a
        #variable is stored as text, so on_field_set cannot follow it: its value is read here and the other size set
        from that. The other size's typed text is dropped so the row shows the new number."""
        if key not in ("width", "height"):
            return
        number = value
        if isinstance(value, str):
            try:
                number = parse_number(value, variables=self.variables)
            except (TypeError, ValueError):
                return
            follow = DSC.canvas_partner(before, key, number)
            if follow is not None:
                self.params[follow[0]] = follow[1]
        other = "height" if key == "width" else "width"
        if self.params.get(other) != before.get(other):
            self.texts.pop(other, None)
            self.errors.pop(other, None)

    def set_image(self, blob, filename, width, height):
        """A new Canvas dialog for an image the client uploaded: `blob` its key, `filename` its name, `width` and
        `height` the default size in mm (the image at its own resolution). The blob takes the place of the `data` param, as
        PartStudioDoc keeps a canvas made with its image inline, so both write the same bytes."""
        if self.type != "canvas":
            raise SessionError("an image belongs to a Canvas dialog")
        p = self.params
        new = {}
        for k, v in p.items():
            new["blob" if k == "data" else k] = blob if k == "data" else v
        new.setdefault("blob", blob)
        new.update(filename=filename, width=width, height=height)
        p.clear()
        p.update(new)
        self.texts.clear()

    def calibrate(self, p0, p1, distance, centre=False, snap=False):
        """Calibrate the Canvas (freeshape/canvas_calibrate.py): scale its size, and optionally move and turn it, so
        the line p0 -> p1 drawn on the image measures `distance` mm. SessionError for a dialog that is not a Canvas,
        a size that is not a number, or a line that cannot be measured."""
        from . import canvas_calibrate as CC
        if self.type != "canvas":
            raise SessionError("only a Canvas dialog can be calibrated")
        if not isinstance(centre, bool) or not isinstance(snap, bool):
            raise SessionError("centre and snap must be true or false")
        have = {}
        for key in ("width", "height", "x", "y", "rotation"):
            raw = self.params.get(key)
            try:
                have[key] = parse_number(raw, angle=key == "rotation", variables=self.variables) \
                    if raw is not None else None
            except (TypeError, ValueError):
                have[key] = None
        try:
            got = CC.calibrate(dict(self.params, **have), p0, p1, distance, centre, snap)
        except CC.CalibrateError as e:
            raise SessionError(str(e)) from e
        for key in ("width", "height", "x", "y", "rotation"):
            if got[key] != have[key]:
                self.params[key] = got[key]
                self.texts.pop(key, None)
                self.errors.pop(key, None)

    def _field_dict(self):
        return {k: list(v) for k, v in self.fields_.items()}

    def _number(self, spec, raw):
        """The value to store for a number field's input (the desktop's rule: an expression that names a
        #variable stays text so it follows the variable; anything else is the number). Remembers the typed text."""
        text = raw.strip() if isinstance(raw, str) else fmt(raw)
        self.texts[spec["key"]] = text                     # what the person typed stays shown, good or not
        v = parse_number(raw, angle=spec.get("unit") == "deg", variables=self.variables, strict=True)
        if v != v or v in (float("inf"), float("-inf")):
            raise ExprError("the value is not a finite number")
        mn = spec.get("min")
        if mn is not None and v < mn:
            raise ExprError(f"the value must be at least {fmt(mn)}")
        mx = spec.get("max")
        if mx is not None and v > mx:
            raise ExprError(f"the value must be at most {fmt(mx)}")
        if spec.get("signed") and abs(v) < 1e-6:
            raise ExprError("the value cannot be zero")
        whole = spec["key"] in ("count", "count2") or bool(spec.get("int"))
        if isinstance(raw, str) and has_vars(raw):
            return text
        return int(round(v)) if whole else v

    # ---------------------------------------------------------------- accepting
    def result(self):
        """The feature dict to store when the dialog is accepted (the desktop's accept, minus the widgets)."""
        f = {k: (dict(v) if k == "params" else v) for k, v in self.feature.items()}
        p = f["params"] = {k: (list(v) if isinstance(v, list) else v) for k, v in self.params.items()}
        if self.type == "loft":
            DSC._loft_fold(p, strip=True)
        if self.type == "hole":
            try:
                label = F.hole_dims(p)[3]
                base = re.sub(r"\s*\(.*\)$", "", f["name"])
                f["name"] = f"{base} ({label})"
            except Exception:                              # noqa: BLE001
                pass
        if self.type == "variable":
            name = str(p.get("name", "")).strip().lstrip("#")
            p["name"] = name
            if str(f["name"]).startswith(("Variable", "#")):
                f["name"] = f"#{name} = {p.get('expr', '')}"
        return f


class DocumentSession:
    """See the module docstring. `store` is a DocumentStore or the path of a Document folder; `spares` and
    `meshes` go to the ProcessEngine; `edit_timeout` (seconds, None for none) bounds how long `submit_edit` waits
    for a worker."""

    def __init__(self, store, spares=1, meshes=True, edit_timeout=300.0):
        if isinstance(store, (str, os.PathLike)):
            store = DS.FolderStore(os.fspath(store))
        self.store = store
        self.edit_timeout = edit_timeout
        self.cycle = None                       # the last circular-link message an edit made, if any
        self._failed = {}                       # tab id -> why it did not open
        self._state = {}                        # tab id -> (state, first error message)
        self._edited = {}                       # tab id -> edits applied since the last save
        self._renamed = set()                   # tab ids renamed here: their name is the manifest's, not the data's
        self._added = set()                     # tab ids added here and not yet saved
        self._deleted = []                      # [(tab id, file)] deleted here whose file save() removes
        self._manifest_dirty = False            # the tab list changed since the last save
        self._base = {}                         # tab id -> sha256 of the tab file as loaded or last saved
        self._manifest_base = None              # sha256 of document.json as loaded or last saved
        self._closed = False
        self._eng = None
        manifest = self._read_manifest()
        self.id = manifest.get("id")
        self.name = manifest.get("name", "")
        self._entries = [{"id": e["id"], "kind": e.get("kind"), "file": e.get("file"),
                          "name": e.get("name") or e.get("kind") or ""}
                         for e in manifest.get("tabs", [])]
        self._fd = self._manifest_folder(manifest)
        self._eng = ProcessEngine(meshes=meshes, spares=spares)
        try:
            self._eng.use_document(self.id)
            self._open_all()
        except BaseException:
            self.close()
            raise

    # ------------------------------------------------------------------ open
    def _read_manifest(self):
        """The parsed, shape-checked manifest (FolderDocument's own check: format, version, tab ids)."""
        try:
            raw = self.store.read_manifest()
            self._manifest_base = hashlib.sha256(bytes(raw)).hexdigest()
            data = DF.FolderDocument._read_manifest(raw)
        except K.KernelError as e:
            raise SessionError(str(e)) from e
        if data.get("version", 1) > DF.FORMAT_VERSION:
            raise SessionError(f"the Document was saved with file version {data['version']}; this build reads "
                               f"up to {DF.FORMAT_VERSION}")
        return data

    def _manifest_folder(self, manifest):
        """A FolderDocument holding only the manifest (its tabs have no document): the one thing that adds a
        tab id and a file name and writes document.json, as the desktop does."""
        fd = DF.FolderDocument()
        fd._store = self.store
        fd.id, fd.name = manifest.get("id") or fd.id, manifest.get("name", fd.name)
        fd.extra = {k: v for k, v in manifest.items() if k not in DF._MANIFEST_KEYS}
        for entry in manifest.get("tabs", []):
            tab = DF.Tab(entry["id"], entry.get("kind"), entry.get("file"),
                         entry.get("name") or entry.get("kind") or "")
            tab.extra = {k: v for k, v in entry.items() if k not in DF._TAB_KEYS}
            fd.tabs.append(tab)
        fd._counter = max([int("".join(c for c in t.id if c.isdigit()) or 0) for t in fd.tabs] + [0])
        return fd

    def _open_all(self):
        raw = {}
        for e in self._entries:
            tid = e["id"]
            try:
                if e["kind"] not in TK.KINDS:
                    raise SessionError(f"unknown tab kind {e['kind']!r}")
                self.store.place_tab(tid, e["file"])         # the store checks the manifest's file name
                raw[tid] = bytes(self.store.read_tab(tid))
                self._base[tid] = hashlib.sha256(raw[tid]).hexdigest()
            except Exception as ex:              # noqa: BLE001 -- a tab that cannot be read is that tab's failure
                self._failed[tid] = f"{type(ex).__name__}: {ex}"
        for e in self._order(raw):
            tid = e["id"]
            if tid in self._failed:
                continue
            try:
                self._eng.open_tab_data(e["kind"], raw[tid], tid, e["name"], blobs=self.store.blobs(tid))
            except Exception as ex:              # noqa: BLE001
                self._failed[tid] = f"{type(ex).__name__}: {ex}"
                continue
            self._edited[tid] = 0
        for e in self._entries:
            self._refresh_state(e["id"])

    def _order(self, raw):
        """The manifest entries, each after the tabs it consumes (by tab id, then uid)."""
        by_id = {e["id"]: e for e in self._entries}
        uids, deps = {}, {}
        for e in self._entries:
            tid = e["id"]
            if tid not in raw:
                continue
            try:
                d = read_data(e["kind"], raw[tid])
            except Exception:                    # noqa: BLE001 -- the engine's open reports it
                continue
            if d.get("uid"):
                uids.setdefault(d["uid"], tid)
            deps[tid] = d
        edges = {}
        for tid, d in deps.items():
            ups = []
            try:
                srcs = links.tab_sources(by_id[tid]["kind"], d)
            except Exception:                    # noqa: BLE001
                srcs = []
            for s in srcs:
                if s.get("document") or s.get("version"):
                    continue
                up = s.get("tab") if s.get("tab") in by_id else uids.get(s.get("uid"))
                if up and up != tid and up not in ups:
                    ups.append(up)
            edges[tid] = ups
        out, done, active = [], set(), set()

        def visit(tid):
            if tid in done or tid in active:     # a cycle: keep manifest order for the rest of it
                return
            active.add(tid)
            for up in edges.get(tid, ()):
                visit(up)
            active.discard(tid)
            done.add(tid)
            out.append(by_id[tid])
        for e in self._entries:
            visit(e["id"])
        return out

    # ------------------------------------------------------------------ reads
    def tabs(self):
        """[{"id", "kind", "name", "state", "error", "dirty"}] in manifest order. `state` is one of STATES; `error`
        is None or a message; `dirty` is True while the tab holds edits `save` has not written."""
        out = []
        for e in self._entries:
            tid = e["id"]
            state, err = self._tab_state(tid)
            name = e["name"]
            if tid not in self._failed and tid not in self._renamed and self._eng is not None:
                try:
                    name = next(t[2] for t in self._eng.tabs() if t[0] == tid)
                except StopIteration:
                    pass
            out.append({"id": tid, "kind": e["kind"], "name": name, "state": state, "error": err,
                        "dirty": self._edited.get(tid, 0) > 0})
        return out

    def outputs(self, tab):
        """The tab's `contract.TabOutputs` (a StudioOutputs, AssemblyOutputs, VariablesOutputs or DrawingOutputs).
        Raises SessionError for an unknown tab, a tab that failed to open, or a worker that cannot be restarted."""
        self._need_open(tab)
        try:
            out = self._retry(tab, lambda: self._eng.outputs(tab))
        except EngineError as e:
            raise SessionError(f"tab {tab!r}: {e}") from e
        self._note(tab, out)
        return out

    def refs(self, tab, picks, dialog=None):
        """Picks turned into stored references by the tab's Part Studio (`engine.Engine.refs`): one answer per pick,
        in order. A query: it changes nothing (no edit, no revision, no undo step, not dirty). Raises SessionError
        for an unknown tab, a tab that is not a Part Studio, or a pick that is malformed or names a part, sketch
        or element the tab does not have (the message names the field, "picks[2].index: ...").
        With `dialog` (a FeatureDialog on `tab`, F3) the picks read the model the dialog edits on (`dialog_base`),
        not the last build."""
        self._need_open(tab)
        base = None if dialog is None else {"feature": dialog.fid}
        try:
            if base is None:
                return self._retry(tab, lambda: self._eng.refs(tab, picks))
            return self._retry(tab, lambda: self._eng.refs(tab, picks, base))
        except BadPick as e:
            raise SessionError(str(e)) from e
        except EngineError as e:
            raise SessionError(f"tab {tab!r}: {e}") from e

    def export(self, tab, fmt, parts=None, options=None):
        """The tab as it is now (unsaved edits included) as STEP or STL: {"data": bytes, "skipped": [part names left
        out]} (`engine.Engine.export`). A query: nothing changes. Raises SessionError for an unknown tab, an unknown part
        id, nothing left to write, or a tab that is not a Part Studio or an Assembly."""
        self._need_open(tab)
        try:
            return self._retry(tab, lambda: self._eng.export(tab, fmt, parts, options))
        except EngineError as e:
            raise SessionError(f"tab {tab!r}: {e}") from e

    def open_dialog(self, tab, fid):
        """A FeatureDialog on the stored feature `fid` of a Part Studio tab (the engine's `feature` query: nothing
        changes). Raises SessionError for an unknown tab or feature or a tab that is not a Part Studio."""
        self._need_open(tab)
        try:
            got = self._retry(tab, lambda: self._eng.feature(tab, fid))
        except EngineError as e:
            raise SessionError(f"tab {tab!r}: {e}") from e
        return FeatureDialog(tab, got["feature"], got["variables"], got.get("defaults"),
                             context=self._derived_context(tab, fid, got["feature"]["type"]),
                             owners=self._mate_owners(tab, fid, got["feature"]["type"]))

    def start_dialog(self, tab, ftype, picks=(), image=None):
        """A FeatureDialog in create mode for a new feature of `ftype` at the rollback position, its selection
        fields seeded from `picks` (the refs route's picks, 1-based; see contract.Engine.start_feature). A query:
        nothing changes. Raises SessionError for a bad pick, an unknown type or a tab that is not a Part Studio.
        `image` ({"blob", "filename", "width", "height"}, a Canvas only, I12.2) is the uploaded image the dialog
        starts with."""
        self._need_open(tab)
        try:
            got = self._retry(tab, lambda: self._eng.start_feature(tab, ftype, list(picks)))
        except BadPick as e:
            raise SessionError(str(e)) from e
        except EngineError as e:
            raise SessionError(f"tab {tab!r}: {e}") from e
        dialog = FeatureDialog(tab, got["feature"], got["variables"], got.get("defaults"),
                               {k: [tuple(i) for i in v] for k, v in got["fields"].items()}, got["params"],
                               context=self._derived_context(tab, None, got["feature"]["type"]),
                               owners=self._mate_owners(tab, None, got["feature"]["type"]))
        if image is not None:
            dialog.set_image(image["blob"], image["filename"], image["width"], image["height"])
        return dialog

    def hold_blob(self, tab, data):
        """Hold `data` (an uploaded image) among the blobs of the Part Studio `tab`: its sha256 key
        (contract.Engine.hold_blob). Nothing is edited. SessionError for an unknown tab or one that is not a Part
        Studio."""
        self._need_open(tab)
        try:
            return self._retry(tab, lambda: self._eng.hold_blob(tab, data))
        except EngineError as e:
            raise SessionError(f"tab {tab!r}: {e}") from e

    def get_blob(self, tab, key):
        """The bytes of the blob `key` of the Part Studio `tab` (contract.Engine.get_blob). A query. SessionError
        when the tab does not have it."""
        self._need_open(tab)
        try:
            return self._retry(tab, lambda: self._eng.get_blob(tab, key))
        except EngineError as e:
            raise SessionError(f"tab {tab!r}: {e}") from e

    def _mate_owners(self, tab, fid, ftype):
        """For a Mate connector dialog (I10.4), ([(part id, name)], [(sketch id, name)]) of the model it picks against
        (the parts and sketches before the feature, as the desktop's Owner part row lists them); None for any other
        type. A model that cannot be read lists none."""
        if ftype != "mate_connector":
            return None
        try:
            base = self._retry(tab, lambda: self._eng.base_outputs(tab, fid))
        except (EngineError, SessionError):
            return [], []
        return [(p.id, p.name) for p in base.parts], [(s.id, s.name) for s in base.sketches]

    def _derived_context(self, tab, fid, ftype):
        """For a Derived dialog on `tab` (the feature `fid`, None for a new one), a callable params ->
        dialog_schema.DerivedContext (I10.2): the other Part Studio tabs of this Document, what the source the
        params name publishes (read again on each call, so a source that changed shows its new contents) and this
        Part Studio's Mate connectors above the Derived (read once, from the model the dialog picks against). None
        for any other type. Called under the Document's session lock, as every dialog call is."""
        if ftype != "derived":
            return None
        studios = [(t["id"], t["name"]) for t in self.tabs() if t["kind"] == "studio" and t["id"] != tab]
        try:
            base = self._retry(tab, lambda: self._eng.base_outputs(tab, fid))
            locations = [(str(c.id), str(c.name or c.id)) for c in base.connectors]
        except (EngineError, SessionError):
            locations = []

        def context(params):
            src = params.get("source") or {}
            names = dict(studios)
            sid = src.get("tab")
            if not sid or src.get("document") or src.get("version"):
                return DSC.DerivedContext(studios, None, "", locations)
            label = src.get("studio_name") or names.get(sid) or sid
            if sid not in names or sid in self._failed:
                return DSC.DerivedContext(studios, None, f"Part Studio “{label}” isn't open", locations)
            try:
                pkg = self._retry(sid, lambda: self._eng.published(sid))
            except (EngineError, SessionError) as e:
                return DSC.DerivedContext(studios, None, f"Part Studio “{label}”: {e}", locations)
            if pkg is None:
                return DSC.DerivedContext(studios, None, f"Part Studio “{label}” isn't open", locations)
            return DSC.DerivedContext(studios, pkg, "", locations)
        return context

    def startable_types(self, tab, picks=()):
        """{feature type: bool}: which tools can start on `tab` with `picks` (contract.Engine.startable)."""
        self._need_open(tab)
        try:
            return self._retry(tab, lambda: self._eng.startable(tab, list(picks)))
        except BadPick as e:
            raise SessionError(str(e)) from e
        except EngineError as e:
            raise SessionError(f"tab {tab!r}: {e}") from e

    def preview_dialog(self, dialog):
        """Ask the engine for a preview of `dialog`'s feature as it would be accepted now (I5.4): (n, Future of the
        engine's preview answer, see contract.Engine.preview). A query: nothing changes. Raises SessionError when
        the tab cannot take the request (gone, not open, a worker that cannot be restarted)."""
        self._need_open(dialog.tab)
        feature = dialog.result()
        try:
            return self._retry(dialog.tab, lambda: self._eng.submit_preview(dialog.tab, dialog.fid, feature["type"],
                                                                             feature["params"]))
        except EngineError as e:
            raise SessionError(f"tab {dialog.tab!r}: {e}") from e

    def dialog_base(self, dialog):
        """The model `dialog` picks against (F3, `engine.Engine.base_outputs`): the Part Studio before the feature it
        edits, or at the rollback bar for a new feature, as engine wire bytes. A query: nothing changes. Raises
        SessionError for a tab that cannot answer or a feature that is gone."""
        self._need_open(dialog.tab)
        try:
            out = self._retry(dialog.tab, lambda: self._eng.base_outputs(dialog.tab, dialog.fid))
        except EngineError as e:
            raise SessionError(f"tab {dialog.tab!r}: {e}") from e
        return wire.to_wire(out)

    def outputs_wire(self, tab):
        """`outputs(tab)` as engine wire bytes (freeshape.engine.wire), what a browser client decodes."""
        return wire.to_wire(self.outputs(tab))

    def errors(self):
        """{tab id: message} for every tab that is not "ok": a tab that failed to open or crashed, and the first
        output error of a tab that reports some. Plus "" for the Document itself: the last circular link, if any."""
        out = {}
        for e in self._entries:
            state, err = self._tab_state(e["id"])
            if state != "ok":
                out[e["id"]] = err or state
        if self.cycle:
            out[""] = self.cycle
        return out

    # ------------------------------------------------------------------ edits
    def submit_edit(self, tab, edit):
        """Apply `edit` (a contract edit dict) to `tab`; returns the ids of every tab that rebuilt: `tab` first,
        then each consumer the engine rebuilt, upstream first. Raises SessionError, naming the tab, for an unknown
        or unopened tab or an edit the tab refuses; the session and the tab carry on as before."""
        self._need_open(tab)
        if not isinstance(edit, dict):
            raise SessionError("an edit is a dict with an 'op' key")
        if edit.get("op") == "save":
            # the worker writes a save edit wherever its `path` points (or over the tab's file, behind the store's
            # back); a session saves through `save()` only
            raise SaveRefused(f"tab {tab!r}: a save edit is refused; use the session's save()")
        try:
            self._retry(tab, lambda: self._eng.submit(tab, edit).result(self.edit_timeout))
            try:
                self._eng.rebuild_dependents(tab)
            except LinkCycleError as e:          # as the GUI: the edit stands, the cycle is reported
                self.cycle = str(e)
            else:
                self.cycle = None
        except FutureTimeout as e:
            raise SessionError(f"tab {tab!r}: the edit did not finish in {self.edit_timeout} s") from e
        except EngineError as e:
            raise SessionError(f"tab {tab!r}: {e}") from e
        except (TypeError, ValueError, KeyError, IndexError, AttributeError) as e:     # a malformed edit
            raise SessionError(f"tab {tab!r}: the edit {edit.get('op')!r} is malformed "
                               f"({type(e).__name__}: {e})") from e
        self._edited[tab] = self._edited.get(tab, 0) + 1
        rebuilt = [tab] + [t for t in self._eng.rebuild_order() if t != tab]
        for t in rebuilt:
            self._refresh_state(t)
        return rebuilt

    def save(self):
        """Write every open tab through the store (with its blobs); returns the tab ids written. A tab that did
        not open is left as it is in the store."""
        self._need_alive()
        live_files = {e["file"] for e in self._entries}
        for tid, file in self._deleted:
            if file not in live_files:               # a later tab may have taken the name
                self.store.delete_tab(tid)
                self._base.pop(tid, None)
        written = []
        for e in self._entries:
            tid = e["id"]
            if tid in self._failed:
                continue
            data = self._tab_bytes(tid, self.store.blobs(tid))
            self.store.write_tab(tid, data)
            self._base[tid] = hashlib.sha256(data).hexdigest()
            self._edited[tid] = 0
            written.append(tid)
        if self._manifest_dirty:
            for tab in self._fd.tabs:                # the manifest's name of each tab is the session's
                tab.name = next(e["name"] for e in self._entries if e["id"] == tab.id)
            self._fd.save()                          # no tab has a document: this writes document.json only
            self._manifest_base = hashlib.sha256(bytes(self.store.read_manifest())).hexdigest()
            self._manifest_dirty = False
            self._added.clear()
            self._deleted.clear()
        return written

    def tabs_unsaved(self):
        """True while the tab list (an added, renamed or deleted tab) has changes `save()` has not written."""
        return self._manifest_dirty

    def has_unsaved(self):
        """True while an edit inside a tab or a change to the tab list has not been written by `save()`."""
        return self._manifest_dirty or any(n > 0 for n in self._edited.values())

    def _tab_bytes(self, tid, sink):
        """The bytes `save()` writes for tab `tid`: the worker's data stamped with the tab id (and the manifest's name
        for a renamed tab); the blobs the data names are put into `sink`."""
        data = self._retry(tid, lambda: self._eng.tab_data(tid, blobs=sink))
        d = json.loads(bytes(data).decode("utf-8"))
        d["_tab_id"] = tid
        entry = next(e for e in self._entries if e["id"] == tid)
        if tid in self._renamed and "name" in d:
            d["name"] = entry["name"]                # the file carries the name the manifest gives it
        return json.dumps(d, indent=1).encode("utf-8")

    # ------------------------------------------------------------------ autosave
    def autosave_state(self):
        """What freeshape.autosave.write takes, or None when nothing is unsaved: {"document", "name", "entries" (the
        tab list), "added", "renamed", "deleted" ([{"id", "file"}]), "tabs": {tab id: (bytes, base)} for every dirty
        tab that opened, "blobs": {key: bytes}, "manifest_base"}. `base` is the sha256 of the tab's saved file as this
        session loaded it, None for a tab added here."""
        self._need_alive()
        if not self.has_unsaved():
            return None
        held = {}
        sink = HeldBlobs(held)
        tabs = {}
        for e in self._entries:
            tid = e["id"]
            if tid in self._failed or not (tid in self._added or self._edited.get(tid, 0) > 0):
                continue
            tabs[tid] = (self._tab_bytes(tid, sink), self._base.get(tid))
        return {"document": self.id, "name": self.name, "entries": [dict(e) for e in self._entries],
                "added": sorted(self._added), "renamed": sorted(self._renamed & {e["id"] for e in self._entries}),
                "deleted": [{"id": tid, "file": file} for tid, file in self._deleted], "tabs": tabs,
                "blobs": dict(held), "manifest_base": self._manifest_base}

    def autosave_moved_on(self, bases, manifest_base=None, list_changed=False):
        """`(tab ids, list moved)`: the tabs of `bases` ({tab id: sha256 of the saved file when the autosave was
        written, or None}) whose saved file is not that any more (changed, deleted, or taken by another tab), and
        whether document.json changed when the autosave holds a tab-list change."""
        self._need_alive()
        moved = sorted(tid for tid, base in bases.items() if self._base.get(tid) != base)
        return moved, bool(list_changed and manifest_base != self._manifest_base)

    def restore_autosave(self, snap):
        """Put an autosave (freeshape.autosave.load's dict) onto this session as unsaved edits: tabs it deleted are
        deleted, every tab it holds data for is opened from that data in place of the loaded one (or added, when it
        is a tab this Document does not have), renamed tabs take their names. Tabs it holds no data for are left as
        they are. Nothing is written to the store. Returns {"recovered": [ids], "skipped": [{"tab", "why"}]}."""
        self._need_alive()
        recovered, skipped = [], []
        have = lambda: {e["id"] for e in self._entries}          # noqa: E731
        for gone in snap["deleted"]:
            if gone["id"] in have():
                try:
                    self.delete_tab(gone["id"])
                except SessionError as ex:
                    skipped.append({"tab": gone["id"], "why": str(ex)})
        auto = {e["id"]: e for e in snap["entries"]}
        raw = snap["tabs"]
        held = HeldBlobs(dict(snap["blobs"]))
        mine, self._entries = self._entries, self._entries + [auto[t] for t in raw if t in auto and t not in have()]
        try:
            order = [e for e in self._order(raw) if e["id"] in raw]
        finally:
            self._entries = mine
        for e in order:
            tid, name = e["id"], e["name"]
            kind = auto.get(tid, e)["kind"]
            current = next((x for x in self._entries if x["id"] == tid), None)
            try:
                if kind not in TK.KINDS:
                    raise SessionError(f"unknown tab kind {kind!r}")
                if current is not None:
                    name = auto[tid]["name"] if tid in snap["renamed"] and tid in auto else current["name"]
                    file = current["file"]
                else:
                    file = self._free_file(auto[tid])
                self.store.place_tab(tid, file)
                blobs = ChainedBlobs(held, self.store.blobs(tid))
                if current is not None and tid not in self._failed:
                    self._eng.close_tab(tid)
                self._failed.pop(tid, None)
                self._eng.open_tab_data(kind, raw[tid], tid, name, blobs=blobs)
            except Exception as ex:                  # noqa: BLE001 -- the tab stays as it was loaded, or failed
                if current is not None:
                    self._failed[tid] = f"{type(ex).__name__}: {ex}"
                skipped.append({"tab": tid, "why": f"{type(ex).__name__}: {ex}"})
                continue
            if current is None:
                self._entries.append({"id": tid, "kind": kind, "file": file, "name": name})
                self._fd.tabs.append(DF.Tab(tid, kind, file, name))
                self._fd._counter = max(self._fd._counter, int("".join(c for c in tid if c.isdigit()) or 0))
                self._added.add(tid)
                self._manifest_dirty = True
            elif tid in snap["renamed"]:
                current["name"] = name
                self._renamed.add(tid)
                self._manifest_dirty = True
            self._edited[tid] = max(self._edited.get(tid, 0), 1)
            self._refresh_state(tid)
            try:
                self._eng.rebuild_dependents(tid)
            except LinkCycleError as ex:
                self.cycle = str(ex)
            recovered.append(tid)
        for tid in recovered:
            self._refresh_state(tid)
        return {"recovered": recovered, "skipped": skipped}

    def _free_file(self, entry):
        """A file name for a tab recovered from an autosave: the entry's own when nothing has it, else a free one."""
        file = entry["file"]
        stem, ext = os.path.splitext(file)
        n = 2
        while self.store.file_taken(file) or any(t.file == file for t in self._fd.tabs):
            file = f"{stem} {n}{ext}"
            n += 1
        return file

    # ------------------------------------------------------------------ the tab list
    def add_tab(self, kind, name=None):
        """A new, empty tab of `kind` ("studio" or "assembly"), opened in its own worker; returns its tab id. `name`
        is checked by `check_tab_name`; None gives "Part Studio 2", "Assembly 1", ... The tab id and file name come
        from the manifest folder (`new_tab_id`, `_unique_filename`), the file name from the name through
        `tab_file_stem`, and no file name is one that is already in the folder. Nothing is written until `save()`."""
        self._need_alive()
        if kind not in NEW_KINDS:
            raise SessionError(f"a new tab is one of {', '.join(NEW_KINDS)}, not {kind!r}")
        label = TK.get(kind).label
        if name is None:
            taken = {e["name"] for e in self._entries}
            n = 1
            while f"{label} {n}" in taken:
                n += 1
            name = f"{label} {n}"
        name = check_tab_name(name)
        fd = self._fd
        ext = TK.get(kind).ext
        stem = tab_file_stem(name, label)
        file = fd._unique_filename(stem, ext)
        n = 2
        while self.store.file_taken(file):
            file = fd._unique_filename(f"{stem} {n}", ext)
            n += 1
        tid = fd.new_tab_id()
        try:
            self.store.place_tab(tid, file)          # the store checks the file name
            self._eng.new_tab(kind, name, tab=tid, blobs=self.store.blobs(tid))
        except Exception as ex:                      # noqa: BLE001
            raise SessionError(f"could not add a {label}: {ex}") from ex
        fd.tabs.append(DF.Tab(tid, kind, file, name))
        self._entries.append({"id": tid, "kind": kind, "file": file, "name": name})
        self._edited[tid] = 1                        # not saved yet
        self._added.add(tid)
        self._manifest_dirty = True
        self._refresh_state(tid)
        return tid

    def rename_tab(self, tab, name):
        """Give `tab` a new name (checked by `check_tab_name`). The file keeps its name: a tab is its id, not its
        file. The tab counts as edited until `save()` writes the manifest and the file's own name field."""
        self._need_open(tab)
        name = check_tab_name(name)
        entry = next(e for e in self._entries if e["id"] == tab)
        if entry["name"] == name and tab in self._renamed:
            return
        entry["name"] = name
        self._renamed.add(tab)
        self._edited[tab] = self._edited.get(tab, 0) + 1
        self._manifest_dirty = True

    def linked_by(self, tab):
        """[{"id", "name"}] of the other tabs that link to `tab`: an Assembly instance, a Drawing source, a Part
        Studio's Variable Studio reference or Derived feature, a Context feature naming it. A tab that has edits
        is read from the engine's Link graph; a tab with none is also read from its saved bytes, so a tab that
        did not open still counts."""
        self._need_alive()
        mine = next((e for e in self._entries if e["id"] == tab), None)
        if mine is None:
            raise SessionError(f"no tab {tab!r} in this Document")
        uid = None
        try:
            uid = read_data(mine["kind"], bytes(self.store.read_tab(tab))).get("uid") if self.store.has_tab(tab) else None
        except Exception:                            # noqa: BLE001
            uid = None
        found = set(self._eng.links.graph.dependents(tab))
        for e in self._entries:
            other = e["id"]
            if other == tab or other in found or self._edited.get(other, 0) > 0 or not self.store.has_tab(other):
                continue
            try:
                d = read_data(e["kind"], bytes(self.store.read_tab(other)))
            except Exception:                        # noqa: BLE001
                continue
            for s in links.tab_sources(e["kind"], d):
                if s.get("document") or s.get("version"):
                    continue
                if s.get("tab") == tab or (s.get("tab") not in {x["id"] for x in self._entries} and uid
                                           and s.get("uid") == uid):
                    found.add(other)
            for f in (d.get("features") or []) if e["kind"] == "studio" else ():
                want = (f.get("params") or {}).get("assembly")
                if want and want in (tab, uid):
                    found.add(other)
        return [{"id": e["id"], "name": e["name"]} for e in self._entries if e["id"] in found]

    def delete_tab(self, tab):
        """Remove `tab` and end its worker. A tab another tab links to is refused with a SessionError that names
        it (`linked_by` is the list). The tab's file is removed, and document.json rewritten, by `save()`."""
        self._need_alive()
        if tab not in {e["id"] for e in self._entries}:
            raise SessionError(f"no tab {tab!r} in this Document")
        by = self.linked_by(tab)
        if by:
            raise TabLinked(tab, by)
        entry = next(e for e in self._entries if e["id"] == tab)
        if tab not in self._failed:
            try:
                self._eng.close_tab(tab)
            except EngineError as ex:
                raise SessionError(f"tab {tab!r}: {ex}") from ex
        self._entries.remove(entry)
        self._fd.tabs[:] = [t for t in self._fd.tabs if t.id != tab]
        for table in (self._failed, self._state, self._edited):
            table.pop(tab, None)
        self._renamed.discard(tab)
        if tab in self._added:                       # never saved: nothing to remove
            self._added.discard(tab)
        elif entry.get("file"):
            self._deleted.append((tab, entry["file"]))
        self._manifest_dirty = True

    # ------------------------------------------------------------------ end
    def pids(self):
        """{tab id: worker process id} of every open tab (the spare worker is not listed)."""
        if self._eng is None:
            return {}
        out = {}
        for t in self._eng.tabs():
            try:
                out[t[0]] = self._eng.pid(t[0])
            except EngineError:
                pass
        return out

    def close(self):
        """End every worker, the warm spare included; returns when they are gone. Safe to call again."""
        if self._closed:
            return
        self._closed = True
        eng, self._eng = self._eng, None
        if eng is not None:
            try:
                eng.close()
            finally:
                from .engine import process
                process.SPARES.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ helpers
    def _need_alive(self):
        if self._closed or self._eng is None:
            raise SessionError("the session is closed")

    def _need_open(self, tab):
        self._need_alive()
        if tab in self._failed:
            raise SessionError(f"tab {tab!r} did not open: {self._failed[tab]}")
        if tab not in {e["id"] for e in self._entries}:
            raise SessionError(f"no tab {tab!r} in this Document")

    def _retry(self, tab, call):
        """`call()`, and once more on a fresh worker if the tab's worker died (the edit log replays onto its
        bytes, so an edit that did not finish is not in it)."""
        try:
            return call()
        except TabErrored:
            try:
                self._eng.restart(tab)
            except EngineError as e:
                self._state[tab] = ("crashed", str(e))
                raise SessionError(f"tab {tab!r}: its worker stopped and could not be restarted ({e})") from e
            return call()

    def _tab_state(self, tid):
        if tid in self._failed:
            return "failed", self._failed[tid]
        if self._eng is not None:
            try:
                why = self._eng.errored(tid)
            except EngineError:
                why = None
            if why:
                return "crashed", why
        return self._state.get(tid, ("ok", None))

    def _note(self, tid, out):
        errs = dict(out.errors or {})
        self._state[tid] = ("error", next(iter(errs.values()))) if errs else ("ok", None)

    def _refresh_state(self, tid):
        if tid in self._failed or self._eng is None:
            return
        try:
            self._note(tid, self._eng.outputs(tid))
        except TabErrored as e:
            self._state[tid] = ("crashed", str(e))
        except EngineError as e:
            self._state[tid] = ("error", str(e))

    # ------------------------------------------------------------------ mate dialogs (I10.5)
    def _mate_call(self, tab, call):
        self._need_open(tab)
        try:
            return self._retry(tab, call)
        except EngineError as e:
            raise SessionError(f"tab {tab!r}: {e}") from e

    def open_mate_dialog(self, tab, mtype=None, mate=None, picks=()):
        """Open a mate dialog on an Assembly tab, for a new mate of `mtype` (seeded from `picks`, the route's
        [{"instance", "kind", "index" (1-based), "point"?}]) or for the stored mate of id `mate`. A query: nothing
        changes. Returns (dialog id, the engine's answer, see contract.Engine.mate_dialog_update). The dialog lives in
        the tab's worker until `close_mate_dialog`. SessionError for a malformed request, an Assembly with fewer than
        two parts (the desktop's message), an unknown type or mate, or a tab that is not an Assembly."""
        import secrets
        if mate is not None:
            if not isinstance(mate, str) or not 1 <= len(mate) <= 64:
                raise SessionError("mate: must be a mate id")
            spec = {"mate": mate}
        else:
            if mtype not in MS.A.MATE_TYPES:
                raise SessionError(f"type: not a mate type ({', '.join(MS.A.MATE_TYPES)})")
            try:
                spec = {"type": mtype, "picks": MS.parse_picks(list(picks) if isinstance(picks, (list, tuple))
                                                              else picks)}
            except ValueError as e:
                raise SessionError(str(e)) from e
        did = secrets.token_hex(8)
        return did, self._mate_call(tab, lambda: self._eng.mate_dialog_open(tab, did, spec))

    def update_mate_dialog(self, tab, dialog, changes):
        """Apply an update body ({type, name, a, b, flip, offset, angle, turn90}, see mate_session.parse_changes) to an
        open mate dialog and preview it; the engine's answer. A query. SessionError for a malformed body (naming the
        field; nothing is applied) or a dialog that is not open."""
        try:
            parsed = MS.parse_changes(changes)
        except ValueError as e:
            raise SessionError(str(e)) from e
        return self._mate_call(tab, lambda: self._eng.mate_dialog_update(tab, dialog, parsed))

    def mate_dialog_edit(self, tab, dialog):
        """The edit that accepts an open mate dialog ({"op": "add_mate" | "replace_mate", "mate"}); the dialog stays
        open. SessionError with the desktop's message while a side is empty."""
        return self._mate_call(tab, lambda: self._eng.mate_dialog_result(tab, dialog))

    def close_mate_dialog(self, tab, dialog):
        """Forget a mate dialog (cancel, or after its accept). Nothing changes."""
        self._mate_call(tab, lambda: self._eng.mate_dialog_close(tab, dialog))

    def accept_mate_dialog(self, tab, dialog):
        """Accept an open mate dialog: its edit through `submit_edit` (one undo step), then the dialog is closed.
        Returns the rebuilt tab ids. A refusal leaves the dialog open."""
        rebuilt = self.submit_edit(tab, self.mate_dialog_edit(tab, dialog))
        self.close_mate_dialog(tab, dialog)
        return rebuilt

    # ------------------------------------------------------------------ sketch sessions (I7.3)
    def sketch_feature(self, tab, fid):
        """The stored sketch feature `fid` of a Part Studio tab (the engine's `feature` query, a deep copy: nothing
        changes) and the tab's feature names. SessionError for an unknown tab or feature, a tab that is not a Part
        Studio, or a feature that is not a sketch."""
        self._need_open(tab)
        try:
            got = self._retry(tab, lambda: self._eng.feature(tab, fid))
        except EngineError as e:
            raise SessionError(f"tab {tab!r}: {e}") from e
        f = got["feature"]
        if f.get("type") != "sketch":
            raise SessionError(f"feature {fid!r} is a {f.get('type')}, not a sketch")
        return f

    def sketch_frame(self, tab, plane):
        """The engine's `sketch_frame` (I7.1) for a plane reference or a sketch feature id: the frame and the model
        edges. A query. SessionError for a tab that cannot answer, a face that is not planar (the message names it)
        or a reference that is not a plane."""
        self._need_open(tab)
        try:
            return self._retry(tab, lambda: self._eng.sketch_frame(tab, plane))
        except EngineError as e:
            raise SessionError(f"tab {tab!r}: {e}") from e
        except (TypeError, ValueError, KeyError, IndexError, AttributeError) as e:      # a malformed reference
            raise SessionError(f"plane: not a plane reference ({type(e).__name__})") from e

    def project_edge(self, tab, plane, pick):
        """The engine's `project_edge` (I7.1) for the sketch plane reference `plane` and an edge pick: the entity the
        edge gives and its reference. A query. SessionError for a tab that cannot answer or an edge that cannot be used."""
        self._need_open(tab)
        try:
            return self._retry(tab, lambda: self._eng.project_edge(tab, plane, pick))
        except EngineError as e:
            raise SessionError(f"tab {tab!r}: {e}") from e

    def feature_names(self, tab):
        """{feature id: name} of a Part Studio tab, in file order. SessionError for any other kind of tab."""
        out = self.outputs(tab)
        if out.kind != "studio":
            raise SessionError(f"tab {tab!r} is not a Part Studio")
        return {f.id: f.name for f in out.features}

    def autosave_state_with(self, kept):
        """`autosave_state()` with unfinished sketches written into their tabs' data, for the server's autosave.
        `kept` is [{"tab", "feature": the sketch feature dict ("id" None for a new sketch)}]: an existing feature
        has its params replaced, a new one (or one deleted since) is added where `add_feature` puts it (at the
        rollback bar) under a free id. The session itself is not changed: these tabs are dirty only in the autosave,
        and `restore_autosave` brings them back as unsaved edits. A tab that is gone or did not open is skipped.
        None when there is nothing to write."""
        state = self.autosave_state()
        live = [k for k in kept or () if k["tab"] in {e["id"] for e in self._entries} and k["tab"] not in self._failed]
        if not live:
            return state
        if state is None:
            state = {"document": self.id, "name": self.name, "entries": [dict(e) for e in self._entries],
                     "added": sorted(self._added), "renamed": sorted(self._renamed & {e["id"] for e in self._entries}),
                     "deleted": [{"id": tid, "file": file} for tid, file in self._deleted], "tabs": {},
                     "blobs": {}, "manifest_base": self._manifest_base}
        for k in live:
            tid = k["tab"]
            if tid in state["tabs"]:
                raw, base = state["tabs"][tid]
            else:
                held = {}
                raw, base = self._tab_bytes(tid, HeldBlobs(held)), self._base.get(tid)
                state["blobs"].update(held)
            data = json.loads(bytes(raw).decode("utf-8"))
            features = data.setdefault("features", [])
            feat = json.loads(json.dumps(k["feature"]))
            at = next((i for i, f in enumerate(features) if f.get("id") == feat.get("id")), None)
            if at is not None:
                features[at] = feat
            else:
                used = {f.get("id") for f in features}
                n = 1 + max([int("".join(c for c in str(x) if c.isdigit()) or 0) for x in used] + [0])
                while f"F{n}" in used:
                    n += 1
                feat["id"] = f"F{n}"
                rb = data.get("rollback")
                if rb is None:
                    features.append(feat)
                else:
                    features.insert(rb, feat)
                    data["rollback"] = None if rb + 1 >= len(features) else rb + 1
            state["tabs"][tid] = (json.dumps(data, indent=1).encode("utf-8"), base)
        return state
