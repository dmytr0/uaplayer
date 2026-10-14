"""Робоча область монітора (без рядка меню й Dock на Mac, без панелі завдань на Windows) —
для міні-плеєра. Flet розміру екрана не дає, тож питаємо систему напряму через ctypes.
Координати — логічні точки з початком у лівому верхньому куті головного екрана, як у
page.window.left/top. Якщо щось не вдалось — None (міні-плеєр тоді рахує від вікна)."""
import ctypes
import platform
import sys


def work_area(x, y):
    """(left, top, width, height) робочої області монітора, на якому точка (x, y)."""
    try:
        if sys.platform == "darwin":
            # AppKit — лише всередині зібраного застосунку (Python там у процесі програми).
            # В окремому процесі Python (версія для розробки) звернення до AppKit робить його
            # «програмою» зі значком у Dock, що стрибає, — там рахуємо через CoreGraphics
            import os
            if os.environ.get("FLET_APP_STORAGE_DATA"):
                return _mac(x, y)
            return _mac_cg(x, y)
        if sys.platform == "win32":
            return _win(x, y)
    except Exception:
        pass
    return None


class _Rect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double),
                ("w", ctypes.c_double), ("h", ctypes.c_double)]


def _mac(x, y):
    ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.restype = ctypes.c_void_p
    sel = objc.sel_registerName

    def send(restype, obj, name, *args, argtypes=()):
        f = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(
            ("objc_msgSend", objc))
        return f(obj, sel(name.encode()), *args)

    def rect(obj, name):
        # NSRect (32 байти) на Intel повертається через objc_msgSend_stret
        if platform.machine() == "x86_64":
            r = _Rect()
            f = ctypes.CFUNCTYPE(None, ctypes.POINTER(_Rect), ctypes.c_void_p, ctypes.c_void_p)(
                ("objc_msgSend_stret", objc))
            f(ctypes.byref(r), obj, sel(name.encode()))
            return r
        return send(_Rect, obj, name)

    screens = send(ctypes.c_void_p, objc.objc_getClass(b"NSScreen"), "screens")
    count = send(ctypes.c_ulong, screens, "count")
    items = [send(ctypes.c_void_p, screens, "objectAtIndex:", i, argtypes=(ctypes.c_ulong,))
             for i in range(count)]
    if not items:
        return None
    main_h = rect(items[0], "frame").h  # Cocoa рахує y знизу головного екрана
    best = None
    for s in items:
        f, v = rect(s, "frame"), rect(s, "visibleFrame")
        top = main_h - (f.y + f.h)
        area = (v.x, main_h - (v.y + v.h), v.w, v.h)
        if f.x <= x < f.x + f.w and top <= y < top + f.h:
            return area
        best = best or area
    return best


def _mac_cg(x, y):
    """Межі монітора з CoreGraphics (без AppKit): рядок меню — ~30 точок зверху, Dock
    невідомий — запас знизу."""
    cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")

    class _Point(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

    cg.CGGetDisplaysWithPoint.argtypes = [_Point, ctypes.c_uint32,
                                          ctypes.POINTER(ctypes.c_uint32),
                                          ctypes.POINTER(ctypes.c_uint32)]
    cg.CGDisplayBounds.restype = _Rect
    cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
    cg.CGMainDisplayID.restype = ctypes.c_uint32
    ids, n = (ctypes.c_uint32 * 1)(), ctypes.c_uint32(0)
    cg.CGGetDisplaysWithPoint(_Point(x, y), 1, ids, ctypes.byref(n))
    b = cg.CGDisplayBounds(ids[0] if n.value else cg.CGMainDisplayID())
    return (b.x, b.y + 30, b.w, b.h - 30 - 70)


def _win(x, y):
    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                    ("r", ctypes.c_long), ("b", ctypes.c_long)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT),
                    ("dwFlags", ctypes.c_ulong)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    user32 = ctypes.windll.user32
    user32.MonitorFromPoint.restype = ctypes.c_void_p
    user32.MonitorFromPoint.argtypes = [POINT, ctypes.c_ulong]
    scale = 1.0
    try:  # вікно Flutter знає про DPI — системні координати фізичні, а Flet — логічні
        dpi = ctypes.c_uint()
        mon0 = user32.MonitorFromPoint(POINT(0, 0), 2)
        ctypes.windll.shcore.GetDpiForMonitor(ctypes.c_void_p(mon0), 0, ctypes.byref(dpi),
                                              ctypes.byref(ctypes.c_uint()))
        scale = (dpi.value or 96) / 96
    except Exception:
        pass
    mon = user32.MonitorFromPoint(POINT(int(x * scale), int(y * scale)), 2)  # NEAREST
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(mi)):
        return None
    w = mi.rcWork
    return (w.l / scale, w.t / scale, (w.r - w.l) / scale, (w.b - w.t) / scale)
