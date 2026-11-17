"""`FreeShape --open-timing <out file> [seconds on Home]`: time opening a small Document (#52).

Starts the real window with the engine on, makes two Documents like the one in the report (one empty Part
Studio each), waits on Home for the given seconds, then opens the first from Home as a card click does
(`open_any`) and waits for its Part Studio's first rebuild to come back from the worker; then goes Home and
opens the second the same way. Every stage (freeshape.timing) goes to the out file, a summary line last:

    RESULT home_wait=0.0 first_ui_blocked=0.051 first_result=1.012 second_ui_blocked=0.040 second_result=0.120

`ui_blocked` is how long the window could not respond (open_any running); `result` is until the parts are
drawn. Used by the Windows test build on GitHub (.github/workflows/mac.yml); from source:
python -m freeshape --open-timing out.txt 0
"""
import os
import sys
import tempfile
import time


def run(out_path, home_wait=0.0):
    from . import timing
    timing.enable()
    timing.begin(f"startup ({home_wait:.0f} s on Home)")
    tmp = tempfile.mkdtemp(prefix="freeshape-open-timing-")
    os.environ["FREESHAPE_DATA_DIR"] = os.path.join(tmp, "appdata")
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    result, times = "RESULT failed", []
    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv[:1])
        for name in ("warning", "critical", "information", "question"):
            setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Discard))
        from . import doc_folder, doc_registry
        from . import kernel  # noqa: F401
        timing.mark("startup: kernel imported")
        paths = []
        for name in ("Test Shapes", "Test Shapes 2"):
            fd = doc_folder.FolderDocument.create(os.path.join(tmp, name), name,
                                                  registry=doc_registry.default_registry())
            fd.add_tab("studio", "Part Studio 1")
            fd.save()
            paths.append(fd.path)
        timing.mark("startup: test Documents made")
        from .ui import mainwindow
        w = mainwindow.MainWindow(None, engine=True)
        w.settings = QSettings(os.path.join(tmp, "settings.ini"), QSettings.IniFormat)
        w.show()
        app.processEvents()
        timing.mark("startup: window shown")
        w.show_home()
        end = time.monotonic() + home_wait
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
        for path in paths:
            w.open_any(path)                        # a Home card click; starts the clock again
            ui = timing.since_begin()
            settled = w.studio_engine.settle(300)
            if not settled or w.folder is None or os.path.normpath(w.folder.path) != os.path.normpath(path):
                raise RuntimeError(f"{os.path.basename(path)} did not open (settled={settled})")
            times.append((ui, timing.since_begin()))
            w.show_home()
            app.processEvents()
        (u1, r1), (u2, r2) = times
        result = (f"RESULT home_wait={home_wait:.1f} first_ui_blocked={u1:.3f} first_result={r1:.3f} "
                  f"second_ui_blocked={u2:.3f} second_result={r2:.3f}")
        w.studio_engine.shutdown()
        w.close()
    except Exception as e:      # noqa: BLE001
        import traceback
        result = "RESULT failed: " + "".join(traceback.format_exception(e))
    lines = [f"{t:8.3f} s  {stage}" for t, stage in timing.marks()]
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines + [result]) + "\n")
    return 0 if result.startswith("RESULT home_wait=") else 1
