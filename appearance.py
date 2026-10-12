"""Вигляд застосунку (тема й стиль виділення), який потрібен ще до побудови інтерфейсу.

Звичайні налаштування читаються асинхронно вже після старту, а кольори задаються на самому
початку (ui.py), тому вигляд зберігається окремим маленьким файлом, що читається одразу.
Зміни діють після перезапуску застосунку.
"""
import json
import os

DEFAULTS = {"theme": "dark", "focus_style": "netflix"}


def _path():
    base = os.environ.get("FLET_APP_STORAGE_DATA") or os.path.expanduser("~")
    return os.path.join(base, ".uafix_appearance.json")


def load():
    data = dict(DEFAULTS)
    try:
        with open(_path(), encoding="utf-8") as f:
            stored = json.load(f)
        data.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except Exception:
        pass
    return data


def save(key, value):
    data = load()
    data[key] = value
    try:
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


CURRENT = load()  # що діє в цьому запуску
