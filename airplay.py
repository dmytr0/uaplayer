"""Трансляція через AirPlay (бета) — телевізори з AirPlay (Hisense VIDAA, Samsung, LG…).

Класичний AirPlay-відеопротокол: телевізору передається посилання (HLS він грає сам), а
застосунок лише керує — /play, /rate (пауза), /scrub (перемотка), /playback-info, /stop.
Частина телевізорів вимагає спарювання з PIN-кодом — тоді play() повідомить про це, і
текст помилки допоможе додати підтримку. Усі функції блокуючі — з фонового потоку.
"""
import plistlib
import socket
import uuid as _uuid

import requests

PORT = 7000
UA = "AirPlay/320.20"


def available(host, timeout=1.5):
    try:
        with socket.create_connection((host, PORT), timeout=timeout):
            return True
    except OSError:
        return False


def info(host):
    """Назва й модель з /info (якщо телевізор віддає) — {name, model}."""
    try:
        r = requests.get(f"http://{host}:{PORT}/info", timeout=3, headers={"User-Agent": UA})
        d = plistlib.loads(r.content)
        return {"name": d.get("name") or "", "model": d.get("model") or ""}
    except Exception:
        return {"name": "", "model": ""}


def device(host):
    """Пристрій для списку «Транслювати на»."""
    i = info(host)
    return {"uuid": f"airplay:{host}", "kind": "airplay", "host": host,
            "name": (i["name"] or f"Телевізор {host}"), "model": i["model"]}


class Session:
    """Відтворення на одному AirPlay-пристрої (ті самі методи, що й у cast.Session)."""

    def __init__(self, host, name=""):
        self.host = host
        self.name = name or f"AirPlay {host}"
        self.base = f"http://{host}:{PORT}"
        self.http = requests.Session()  # одне з'єднання: частина приймачів тримає за ним сесію
        self.http.headers.update({"User-Agent": UA,
                                  "X-Apple-Session-ID": str(_uuid.uuid4()).upper()})
        self.paused = False

    def _post(self, path, data=None, ctype=None):
        h = {"Content-Type": ctype} if ctype else {}
        r = self.http.post(self.base + path, data=data, headers=h, timeout=10)
        if r.status_code in (401, 403, 470):
            raise RuntimeError(f"телевізор просить спарювання AirPlay (код {r.status_code})")
        if r.status_code >= 400:
            raise RuntimeError(f"AirPlay {path}: код {r.status_code}")
        return r

    def play(self, url, title="", poster=None, start=0.0, subtitles=None):
        body = f"Content-Location: {url}\nStart-Position: 0\n"
        self._post("/play", body.encode(), "text/parameters")
        if start > 5:
            import time
            for _ in range(20):  # дочекатись, поки відео відкриється, і перемотати
                time.sleep(0.5)
                pos, dur, state, _ = self.status()
                if dur:
                    self.seek(start)
                    break

    def status(self):
        r = self.http.get(self.base + "/playback-info", timeout=6)
        if r.status_code >= 400 or not r.content:
            return 0.0, 0.0, "BUFFERING", None
        d = plistlib.loads(r.content)
        pos, dur = float(d.get("position") or 0), float(d.get("duration") or 0)
        rate = float(d.get("rate") or 0)
        if not d.get("readyToPlay", True) and not dur:
            return pos, dur, "BUFFERING", None
        if dur and pos >= dur - 2 and rate == 0:
            return pos, dur, "IDLE", "FINISHED"
        return pos, dur, "PAUSED" if rate == 0 else "PLAYING", None

    def toggle(self):
        self.paused = not self.paused
        self._post(f"/rate?value={0 if self.paused else 1}")

    def seek(self, seconds):
        self._post(f"/scrub?position={max(0.0, seconds):.1f}")

    def stop(self):
        try:
            self._post("/stop")
        finally:
            self.http.close()
