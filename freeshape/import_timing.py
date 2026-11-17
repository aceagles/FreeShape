"""`FreeShape --import-timing <out file> <source> [source ...]`: time importing big STEP assemblies (#60).

Starts the real window with the engine on and imports each source into a new tab as File > Import does
(`import_step_file`), waiting until the parts are drawn. A source is a STEP file's path or `synthetic:<n>`: an
assembly written here of a few distinct parts (a many-faced screw, a board with holes) placed n times over, in
sub-assemblies, the way a CAD export repeats its fasteners. One summary line per source goes to the out file:

    RESULT synthetic:400 parts=440 total=12.3 longest_freeze=0.21 peak_mb=1830 window_peak_mb=950

`total` is seconds until the parts are drawn; `longest_freeze` the longest the window went without handling an
event (a 50 ms timer that could not fire); `peak_mb` the most memory the window and every process it started
held at once (sampled), `window_peak_mb` the window's own, `worker_peak_mb` the most any one other process held.
`window_steady_mb` and `worker_steady_mb` are what the window and the tab's engine worker hold once the import has
settled: the window keeps handling events for 30 s (FREESHAPE_STEADY_WAIT seconds, if set), then each process runs
gc and memtrim.release_memory() and reads its own memory. Run with FREESHAPE_NO_MEMTRIM=1 to see them without the
release. Used by the Windows test build on GitHub (.github/workflows/mac.yml); from source:
python -m freeshape --import-timing out.txt synthetic:400
"""
import math
import os
import sys
import tempfile
import threading
import time


# ---------------------------------------------------------------------------- memory of a process tree

def _tree_mb_windows(root):
    import ctypes
    from ctypes import wintypes

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_char * 260)]

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    parent = {}
    e = PE()
    e.dwSize = ctypes.sizeof(PE)
    ok = k32.Process32First(snap, ctypes.byref(e))
    while ok:
        parent[e.th32ProcessID] = e.th32ParentProcessID
        ok = k32.Process32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    tree, grew = {root}, True
    while grew:
        grew = False
        for pid, pp in parent.items():
            if pp in tree and pid not in tree:
                tree.add(pid)
                grew = True
    out = {}
    for pid in tree:
        h = k32.OpenProcess(0x1000 | 0x0010, False, pid)       # QUERY_LIMITED_INFORMATION | VM_READ
        if not h:
            continue
        m = PMC()
        m.cb = ctypes.sizeof(PMC)
        if psapi.GetProcessMemoryInfo(h, ctypes.byref(m), m.cb):
            out[pid] = m.WorkingSetSize / 1e6
        k32.CloseHandle(h)
    return out


def _tree_mb_proc(root):
    kids = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as fh:
                    kids.setdefault(int(fh.read().rsplit(")", 1)[1].split()[1]), []).append(int(d))
            except (OSError, ValueError, IndexError):
                pass
    out, todo = {}, [root]
    while todo:
        pid = todo.pop()
        try:
            with open(f"/proc/{pid}/status") as fh:
                out[pid] = next(int(ln.split()[1]) for ln in fh if ln.startswith("VmRSS:")) / 1e3
        except (OSError, StopIteration, ValueError):
            pass
        todo += kids.get(pid, [])
    return out


def tree_mb(root=None):
    """{pid: MB in memory} of a process and everything it started."""
    root = root or os.getpid()
    try:
        return _tree_mb_windows(root) if sys.platform == "win32" else _tree_mb_proc(root)
    except Exception:       # noqa: BLE001 -- no numbers rather than no run
        return {}


def steady_mb():
    """(pid, MB in memory) of this process after gc and memtrim.release_memory(). Runs in the window and, through
    ProcessEngine.run_in_worker, in an engine worker."""
    import gc
    from .memtrim import release_memory
    gc.collect()
    release_memory()
    me = os.getpid()
    return me, tree_mb(me).get(me, 0.0)


def _settle_wait(app, seconds):
    """Keep the window handling events for `seconds` (deferred deletes, timers and queued results all run)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.05)
    app.processEvents()


class MemorySampler:
    def __init__(self, every=0.2):
        self.every, self.peak, self.window_peak, self._stop = every, 0.0, 0.0, threading.Event()
        self.other_peak = 0.0       # the most one other process (an engine worker, the STEP reader) held
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        me = os.getpid()
        while not self._stop.is_set():
            mb = tree_mb(me)
            self.peak = max(self.peak, sum(mb.values()))
            self.window_peak = max(self.window_peak, mb.get(me, 0.0))
            self.other_peak = max([self.other_peak] + [v for k, v in mb.items() if k != me])
            self._stop.wait(self.every)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._t.join(5)


# ---------------------------------------------------------------------------- a synthetic assembly

def write_synthetic(path, copies):
    """An assembly STEP like a CAD export: 8 screw variants (about 1000 faces each) and 2 boards with 100 holes,
    in modules of a board and 10 screws, the modules placed until there are `copies` screws."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS_Compound
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    def screw(v):
        ts = BRepOffsetAPI_ThruSections(True, True)
        for k in range(40):
            r = 3.0 if k % 2 == 0 else 2.6
            pol = BRepBuilderAPI_MakePolygon()
            for s in range(24):
                a = 2 * math.pi * s / 24 + k * 0.13
                pol.Add(gp_Pnt(r * math.cos(a), r * math.sin(a), (10 + v * 0.37) * k / 39))
            pol.Close()
            ts.AddWire(pol.Wire())
        ts.Build()
        return ts.Shape()

    def board(v):
        comp, bb = TopoDS_Compound(), BRep_Builder()
        bb.MakeCompound(comp)
        for i in range(10):
            for j in range(10):
                bb.Add(comp, BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(5 + i * 9.5, 5 + j * 7.5, -1), gp_Dir(0, 0, 1)),
                                                      1.0 + 0.01 * v, 4).Shape())
        return BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(100 + v, 80, 1.6).Shape(), comp).Shape()

    def loc(x, y, z, rot=0.0):
        t = gp_Trsf()
        t.SetTranslation(gp_Vec(x, y, z))
        r = gp_Trsf()
        r.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), rot)
        t.Multiply(r)
        return TopLoc_Location(t)
    doc = TDocStd_Document(TCollection_ExtendedString("synthetic"))
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    def named(label, name):
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
        return label
    screws = [named(st.AddShape(screw(v), False), f"Screw {v}") for v in range(8)]
    boards = [named(st.AddShape(board(v), False), f"Board {v}") for v in range(2)]
    modules = []
    for m in range(min(20, max(1, copies // 10))):
        sub = named(st.NewShape(), f"Module {m}")
        st.AddComponent(sub, boards[m % 2], loc(0, 0, 0))
        for k in range(10):
            st.AddComponent(sub, screws[(m * 10 + k) % 8], loc(5 + k * 9.5, 5, 0))
        modules.append(sub)
    top = named(st.NewShape(), "Top")
    for k in range(max(1, copies // 10)):
        st.AddComponent(top, modules[k % len(modules)], loc((k % 20) * 120, (k // 20) * 100, 0, 0.01 * k))
    st.UpdateAssemblies()
    w = STEPCAFControl_Writer()
    w.SetNameMode(True)
    w.Transfer(doc, STEPControl_AsIs)
    w.Write(str(path))
    return path


# ---------------------------------------------------------------------------- the run

def run(out_path, *sources, steady_wait=None):
    import faulthandler
    from . import timing
    if sys.stderr is not None:
        faulthandler.enable(all_threads=True)       # a crash in a native call still says where it was
    timing.enable()
    if steady_wait is None:
        steady_wait = float(os.environ.get("FREESHAPE_STEADY_WAIT", "30"))
    tmp = tempfile.mkdtemp(prefix="freeshape-import-timing-")
    os.environ["FREESHAPE_DATA_DIR"] = os.path.join(tmp, "appdata")
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    lines, ok = [], True
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication(sys.argv[:1])
    warned = []
    for name in ("warning", "critical", "information", "question"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: warned.append(a[2:3]) or QMessageBox.Discard))
    from .ui import mainwindow
    w = mainwindow.MainWindow(None, engine=True)
    w.settings = QSettings(os.path.join(tmp, "settings.ini"), QSettings.IniFormat)
    w.show()
    app.processEvents()
    for src in sources:
        try:
            path = src
            if src.startswith("synthetic:"):
                path = write_synthetic(os.path.join(tmp, f"synthetic-{src[10:]}.step"), int(src[10:]))
            mb = os.path.getsize(path) / 1e6
            gaps = {"last": time.monotonic(), "max": 0.0}

            def tick():
                now = time.monotonic()
                gaps["max"] = max(gaps["max"], now - gaps["last"])
                gaps["last"] = now
            timer = QTimer()
            timer.timeout.connect(tick)
            timer.start(50)
            del warned[:]
            t0 = time.monotonic()
            with MemorySampler() as mem:
                gaps["last"] = time.monotonic()
                w.import_step_file(path)
                settled = w.studio_engine.settle(3600) if w.studio_engine is not None else True
                tick()
            total = time.monotonic() - t0
            timer.stop()
            n = len(w.doc.state.parts)
            err = "; ".join(str(e) for e in w.doc.errors.values()) or "; ".join(str(x) for x in warned)
            _settle_wait(app, steady_wait)
            window_steady = steady_mb()[1]
            worker_steady = float("nan")
            if w.studio_engine is not None and w.studio_engine.attached(w.doc):
                worker_steady = w.studio_engine.in_engine(
                    w.doc, lambda eng, tab: eng.run_in_worker(tab, steady_mb))[1]
            lines.append(f"RESULT {src} file_mb={mb:.1f} parts={n} total={total:.1f} "
                         f"longest_freeze={gaps['max']:.2f} peak_mb={mem.peak:.0f} "
                         f"window_peak_mb={mem.window_peak:.0f} worker_peak_mb={mem.other_peak:.0f} "
                         f"window_steady_mb={window_steady:.0f} worker_steady_mb={worker_steady:.0f} "
                         f"settled={settled}" + (f" error={err}" if err else ""))
            ok = ok and settled and not err
        except Exception as e:      # noqa: BLE001
            import traceback
            lines.append(f"RESULT {src} failed: " + "".join(traceback.format_exception(e)))
            ok = False
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    if w.studio_engine is not None:
        w.studio_engine.shutdown()
    w.close()
    return 0 if ok else 1
