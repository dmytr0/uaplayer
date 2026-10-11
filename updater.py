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
        name = f"UAFix-Player-{'AndroidTV' if tv else 'Android'}-{android_abi()}.apk"
    elif platform_name == "macos":
        name = "UAFix-Player-macOS.zip"
    elif platform_name == "windows":
        name = "UAFix-Player-Windows.zip"
    else:
        return None
    return release["assets"].get(name)
