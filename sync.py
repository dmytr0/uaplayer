"""Синхронізація історії переглядів і обраного між пристроями (сервер — sync_worker/).

Код синхронізації (12 знаків) генерує застосунок; той самий код, введений на іншому
пристрої, зв'язує їх. Злиття — як на сервері: для кожного фільму перемагає новіший запис.
"""
import secrets

import requests

URL = "https://uafix-sync.uafix-35038b.workers.dev"  # адресу записує sync_worker/deploy.py
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # без схожих I/O/0/1
HISTORY_MAX = 200
HEADERS = {"User-Agent": "UAFix-Player"}


def new_code():
    return "".join(secrets.choice(ALPHABET) for _ in range(12))


def normalize(code):
    """'k7qf-29xm pl4d' → 'K7QF29XMPL4D' або None, якщо код неправильний."""
    c = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    c = c.replace("O", "0").replace("I", "1")  # на випадок переплутаних літер
    return c if len(c) == 12 and all(ch in ALPHABET for ch in c) else None


def pretty(code):
    return "-".join(code[i:i + 4] for i in range(0, 12, 4)) if code else ""


def _merge_by_url(a, b, limit):
    by_url = {}
    for item in list(a or []) + list(b or []):
        if not isinstance(item, dict) or not item.get("url"):
            continue
        prev = by_url.get(item["url"])
        if prev is None or item.get("ts", 0) >= prev.get("ts", 0):
            by_url[item["url"]] = item
    return sorted(by_url.values(), key=lambda x: x.get("ts", 0), reverse=True)[:limit]


def merge(local, remote):
    cleared = max(local.get("history_cleared", 0) or 0, remote.get("history_cleared", 0) or 0)
    history = [h for h in _merge_by_url(local.get("history"), remote.get("history"), HISTORY_MAX)
               if not cleared or h.get("ts", 0) > cleared]
    favorites = _merge_by_url(local.get("favorites"), remote.get("favorites"), 500)
    return {"history": history, "favorites": favorites, "history_cleared": cleared}


def push(code, doc):
    """Надіслати свої дані й отримати злиті з усіх пристроїв."""
    r = requests.post(f"{URL}/v1/sync/{code}", json=doc, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def pull(code):
    r = requests.get(f"{URL}/v1/sync/{code}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()
