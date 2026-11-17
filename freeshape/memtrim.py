"""Give memory the process has freed back to the operating system.

After a big import or rebuild, Python and OCCT free most of what they used, but the C allocator keeps the freed
pages for reuse, so the process still looks as big as at its peak. `release_memory()` asks the allocator to hand
them back: glibc's malloc_trim(0) on Linux, _heapmin() and HeapCompact over the process heaps on Windows,
malloc_zone_pressure_relief on macOS, nothing elsewhere. It never raises.

FREESHAPE_NO_MEMTRIM=1 turns it off in this process and the engine workers it starts (they inherit the
environment), to measure what the app holds without it (`--import-timing`).
"""
import ctypes
import ctypes.util
import os
import sys

_fn = None          # the platform's release function, found once; False when there is none


def _find():
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None)
        trim = libc.malloc_trim           # glibc only: musl has none (AttributeError, so no-op)
        trim.argtypes, trim.restype = [ctypes.c_size_t], ctypes.c_int
        return lambda: trim(0)
    if sys.platform == "win32":
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32")
        k32.GetProcessHeaps.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        k32.GetProcessHeaps.restype = wintypes.DWORD
        k32.HeapCompact.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.HeapCompact.restype = ctypes.c_size_t
        try:
            heapmin = ctypes.CDLL("ucrtbase")._heapmin
        except (OSError, AttributeError):
            heapmin = None

        def release():
            if heapmin is not None:
                heapmin()
            n = k32.GetProcessHeaps(0, None)
            heaps = (wintypes.HANDLE * max(n, 1))()
            n = min(k32.GetProcessHeaps(len(heaps), heaps), len(heaps))
            for h in heaps[:n]:
                k32.HeapCompact(h, 0)
        return release
    if sys.platform == "darwin":
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "/usr/lib/libSystem.B.dylib")
        relief = libc.malloc_zone_pressure_relief
        relief.argtypes, relief.restype = [ctypes.c_void_p, ctypes.c_size_t], ctypes.c_size_t
        return lambda: relief(None, 0)
    return None


def release_memory():
    """Hand freed heap memory back to the OS. True when the platform's call ran, False when there is none or it
    failed. Never raises."""
    global _fn
    try:
        if os.environ.get("FREESHAPE_NO_MEMTRIM"):
            return False
        if _fn is None:
            try:
                _fn = _find() or False
            except Exception:       # noqa: BLE001 -- no such library or symbol: nothing to do here
                _fn = False
        if not _fn:
            return False
        _fn()
        return True
    except Exception:               # noqa: BLE001 -- releasing memory is a nicety, never a failure
        return False
