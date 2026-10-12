"""Перевірка оновлень через GitHub Releases.

Номер версії вшиває CI у модуль app_version.py (з тегу v1.2.3 → "1.2.3"); у версії для
розробки модуля немає — тоді VERSION == "dev" і оновлення не пропонуються.
"""
import platform
import re

import requests

REPO = "minkoroman/uafix-player"
API = f"https://api.github.com/repos/{REPO}/releases"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "UAFix-Player"}

try:
    from app_version import VERSION  # створює CI під час збірки
except ImportError:
    VERSION = "dev"


def parse_version(v):
    nums = re.findall(r"\d+", (v or "").lstrip("v").split("-")[0])
    return tuple(int(x) for x in nums[:3]) if nums else (0,)


def is_newer(latest, current=VERSION):
    return current != "dev" and parse_version(latest) > parse_version(current)


def _release(data):
    return {
        "version": data["tag_name"].lstrip("v"),
        "notes": (data.get("body") or "").strip(),
        "url": data["html_url"],
        "assets": {a["name"]: a["browser_download_url"] for a in data.get("assets", [])},
    }


def latest_release():
    """Останній реліз: {version, notes, url, assets: {ім'я файлу: посилання}}."""
    r = requests.get(f"{API}/latest", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return _release(r.json())


def release_notes(version):
    """Опис змін конкретної версії (для вікна «Що нового»)."""
    r = requests.get(f"{API}/tags/v{version}", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return _release(r.json())["notes"]


def android_abi():
    machine = platform.machine().lower()
    # armv8l — 32-бітний застосунок на 64-бітному процесорі: ставимо той самий armv7
    return "arm64" if machine in ("aarch64", "arm64") else "armv7"


def asset_for(release, platform_name, tv=False):
    """Посилання на файл саме для цього пристрою (або None)."""
    if platform_name == "android":
        kind = "AndroidTV" if tv else "Android"
        name = f"UAFix-Player-{kind}-{android_abi()}.apk"
        # 32-бітна версія працює на будь-якому ARM-процесорі — запасний варіант
        return release["assets"].get(name) or release["assets"].get(f"UAFix-Player-{kind}-armv7.apk")
    elif platform_name == "macos":
        name = "UAFix-Player-macOS.zip"
    elif platform_name == "windows":
        name = "UAFix-Player-Windows.zip"
    else:
        return None
    return release["assets"].get(name)


def download(url, dest, progress=lambda done, total: None, chunk=256 * 1024):
    """Завантажує файл (оновлення) у dest через тимчасовий .part; progress(байти, всього)."""
    import os
    tmp = dest + ".part"
    with requests.get(url, headers={"User-Agent": "UAFix-Player"}, stream=True, timeout=30) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            for part in r.iter_content(chunk):
                f.write(part)
                done += len(part)
                progress(done, total)
    if total and done != total:
        raise IOError(f"завантажено {done} з {total} байт")
    os.replace(tmp, dest)
    return dest


# ── оновлення на Mac і Windows: завантажений архів розпаковується, а маленький скрипт
#    після закриття застосунку замінює стару версію новою й запускає її


def _exe_path():
    """Шлях до виконуваного файлу застосунку (Python працює всередині нього)."""
    import ctypes
    import os
    import sys
    try:
        if sys.platform == "darwin":
            buf = ctypes.create_string_buffer(4096)
            size = ctypes.c_uint32(len(buf))
            if ctypes.CDLL(None)._NSGetExecutablePath(buf, ctypes.byref(size)) == 0:
                return os.path.realpath(buf.value.decode())
        elif sys.platform == "win32":
            buf = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.kernel32.GetModuleFileNameW(None, buf, len(buf)):
                return buf.value
    except Exception:
        pass
    return sys.executable


def desktop_target(platform_name):
    """Що замінювати: теку застосунку .app (Mac) чи теку з .exe (Windows); None — не можна
    (запуск не зі збірки, або немає прав на запис — тоді оновлення через браузер)."""
    import os
    exe = _exe_path() or ""
    if os.path.basename(exe).lower().startswith("python") or "python.framework" in exe.lower():
        return None  # запуск звичайним Python (розробка), а не зібраний застосунок
    if platform_name == "macos":
        path = exe
        while path and path != "/" and not path.endswith(".app"):
            path = os.path.dirname(path)
        if not path.endswith(".app") or "/flet-desktop" in path or "Flet.app" in path:
            return None  # версія для розробки (клієнт Flet), а не зібраний застосунок
    elif platform_name == "windows":
        if not exe.lower().endswith(".exe") or os.path.basename(exe).lower().startswith("python"):
            return None
        path = os.path.dirname(exe)
    else:
        return None
    return path if os.access(os.path.dirname(path) if platform_name == "macos" else path,
                             os.W_OK) else None


def install_desktop(zip_path, platform_name, target=None, relaunch=True):
    """Розпаковує оновлення й запускає заміну після виходу застосунку. Після виклику
    застосунок має завершитись (скрипт чекає на це)."""
    import glob
    import os
    import subprocess
    import tempfile
    target = target or desktop_target(platform_name)
    if not target:
        raise RuntimeError("немає доступу до теки застосунку")
    work = tempfile.mkdtemp(prefix="uafix-update-")
    pid = os.getpid()
    if platform_name == "macos":
        # ditto, а не zipfile: зберігає права на виконання й символьні посилання в .app
        subprocess.run(["/usr/bin/ditto", "-x", "-k", zip_path, work], check=True)
        apps = glob.glob(os.path.join(work, "*.app"))
        if not apps:
            raise RuntimeError("в архіві немає застосунку")
        new_app, old = apps[0], target
        script = os.path.join(work, "update.sh")
        with open(script, "w") as f:
            f.write(f'''#!/bin/sh
while kill -0 {pid} 2>/dev/null; do sleep 0.5; done
rm -rf "{old}.old"
mv "{old}" "{old}.old" && mv "{new_app}" "{old}" && rm -rf "{old}.old" || mv "{old}.old" "{old}"
xattr -dr com.apple.quarantine "{old}" 2>/dev/null
{'open "' + old + '"' if relaunch else ''}
rm -rf "{work}"
''')
        subprocess.Popen(["/bin/sh", script], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        import zipfile
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(work)
        exe = _exe_path()
        src = work
        if not os.path.exists(os.path.join(src, os.path.basename(exe))):
            # архів міг містити ще одну теку всередині
            found = glob.glob(os.path.join(work, "*", os.path.basename(exe)))
            if not found:
                raise RuntimeError("в архіві немає застосунку")
            src = os.path.dirname(found[0])
        script = os.path.join(work, "update.bat")
        with open(script, "w", encoding="utf-8") as f:
            f.write(f'''@echo off
:wait
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (timeout /t 1 /nobreak >nul & goto wait)
robocopy "{src}" "{target}" /E /R:10 /W:1 /NFL /NDL /NJH /NJS >nul
{'start "" "' + exe + '"' if relaunch else ''}
''')
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
        subprocess.Popen(["cmd", "/c", script], creationflags=flags, close_fds=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
