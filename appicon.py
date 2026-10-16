"""Іконка застосунку на вибір (налаштування): «classic» (стандартна) або reel / mask / bolt.

- Android / Android TV — MainActivity вмикає відповідний «вхід» (activity-alias) у лаунчері
  (посилання uafixupdate://icon?name=…, див. tools/android_template.py);
- macOS — своя іконка на самому .app (Finder, Dock, Launchpad) і в Dock, поки застосунок
  відкритий. Оновлення ставить новий .app зі стандартною іконкою — тому вибрана ставиться
  знову при кожному запуску;
- Windows — іконка вікна й кнопки на панелі завдань, поки застосунок відкритий (іконку
  самого .exe змінити не можна).
Картинки — assets/app_icons/<назва>.png (macOS, 1024 px) і .ico (Windows).
"""
import ctypes
import os
import sys

VARIANTS = [("classic", "Класична"), ("reel", "Котушка"), ("mask", "Маска"),
            ("bolt", "Блискавка")]


def asset(name, ext):
    """Шлях до картинки іконки (у зібраному застосунку — FLET_ASSETS_DIR)."""
    base = os.environ.get("FLET_ASSETS_DIR") or os.path.join(os.path.dirname(__file__), "assets")
    if name == "classic" and ext == "png":
        path = os.path.join(base, "icon.png")
    else:  # classic.ico — щоб на Windows повернути стандартну
        path = os.path.join(base, "app_icons", f"{name}.{ext}")
    return path if os.path.isfile(path) else None


def preview(name):
    """Картинка для вибору в налаштуваннях (шлях відносно assets — як src у ft.Image)."""
    return "icon.png" if name == "classic" else f"app_icons/{name}_preview.png"


# ───────── macOS ─────────

def _objc():
    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.restype = ctypes.c_void_p
    ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")

    def send(restype, obj, name, *args, argtypes=()):
        f = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(
            ("objc_msgSend", objc))
        return f(obj, objc.sel_registerName(name.encode()), *args)

    def cls(name):
        return objc.objc_getClass(name.encode())

    def nsstring(s):
        return send(ctypes.c_void_p, cls("NSString"), "stringWithUTF8String:",
                    s.encode("utf-8"), argtypes=(ctypes.c_char_p,))

    return send, cls, nsstring


def apply_mac(name):
    """Поставити іконку .app (і в Dock). Лише в зібраному застосунку: у версії для
    розробки «застосунок» — це сам Python, його іконку не чіпаємо."""
    if sys.platform != "darwin" or not os.environ.get("FLET_APP_STORAGE_DATA"):
        return False
    send, cls, nsstring = _objc()
    bundle = send(ctypes.c_void_p, cls("NSBundle"), "mainBundle")
    path_ns = send(ctypes.c_void_p, bundle, "bundlePath")
    path = ctypes.string_at(send(ctypes.c_char_p, path_ns, "UTF8String")).decode()
    if not path.endswith(".app"):
        return False
    img = None
    if name != "classic":
        file = asset(name, "png")
        if not file:
            return False
        img = send(ctypes.c_void_p, send(ctypes.c_void_p, cls("NSImage"), "alloc"),
                   "initWithContentsOfFile:", nsstring(file), argtypes=(ctypes.c_void_p,))
    ws = send(ctypes.c_void_p, cls("NSWorkspace"), "sharedWorkspace")
    # nil — повернути стандартну іконку з бандла
    send(ctypes.c_bool, ws, "setIcon:forFile:options:", img, nsstring(path), 0,
         argtypes=(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong))
    # Dock, поки застосунок відкритий — лише з головного потоку
    app = send(ctypes.c_void_p, cls("NSApplication"), "sharedApplication")
    sel = ctypes.CDLL("/usr/lib/libobjc.A.dylib").sel_registerName
    sel.restype = ctypes.c_void_p
    send(None, app, "performSelectorOnMainThread:withObject:waitUntilDone:",
         sel(b"setApplicationIconImage:"), img, False,
         argtypes=(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool))
    return True


def apply(name, platform, page=None):
    """Застосувати вибрану іконку на ПК (Android — через посилання з main.py).
    → True, якщо щось змінилось."""
    try:
        if platform == "macos":
            return apply_mac(name)
        if platform == "windows" and page is not None:
            ico = asset(name, "ico")
            if ico:
                page.window.icon = ico
                return True
    except Exception:
        pass
    return False
