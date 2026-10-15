"""Трансляція на Chromecast / Google TV / Android TV (протокол Google Cast), а також на
телевізори з DLNA (Hisense VIDAA, Samsung, LG…) — див. dlna.py.

Телевізор сам завантажує HLS-потік за посиланням (сервер відео віддає його без
спеціальних заголовків і з CORS «*»), тож телефон лише керує відтворенням.
Усі функції блокуючі — викликати з фонового потоку (asyncio.to_thread).
"""
import threading
import time

MEDIA_RECEIVER = "CC1AD845"  # стандартний відеоплеєр Google Cast

_lock = threading.Lock()
_browser = None
_devices = {}  # uuid → Chromecast


def discover(timeout=6):
    """Пристрої в мережі: Google Cast і DLNA (пошук одночасно) — [{uuid, name, model, kind}]."""
    import dlna
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(2) as pool:
        cc = pool.submit(_discover_cast, timeout)
        dl = pool.submit(dlna.discover, 4)
        found = []
        for fut, kind in ((cc, "cast"), (dl, "dlna")):
            try:
                found += [dict(d, kind=kind) for d in fut.result()]
            except Exception:
                pass
    # один телевізор часто видно і як Cast, і як DLNA — лишаємо Cast (він надійніший)
    cast_names = {d["name"] for d in found if d["kind"] == "cast"}
    found = [d for d in found if d["kind"] == "cast" or d["name"] not in cast_names]
    # телевізори, додані за IP, без DLNA — через AirPlay (якщо він є)
    import airplay
    dlna_hosts = {dlna._devices[d["uuid"]]["host"] for d in found if d["kind"] == "dlna"}
    for host in dlna.MANUAL_HOSTS:
        if host not in dlna_hosts and airplay.available(host):
            found.append(airplay.device(host))
    return found


def open_session(device, headers=None):
    """Сесія для пристрою зі списку discover()."""
    if device.get("kind") == "dlna":
        import dlna
        return dlna.Session(device["uuid"], headers)
    if device.get("kind") == "airplay":
        import airplay
        return airplay.Session(device["host"], device.get("name", ""))
    return Session(device["uuid"])


def _discover_cast(timeout):
    import pychromecast

    global _browser
    with _lock:
        if _browser is not None:
            try:
                _browser.stop_discovery()
            except Exception:
                pass
        casts, _browser = pychromecast.get_chromecasts(timeout=timeout)
        _devices.clear()
        for c in casts:
            # лише відео-пристрої (без «аудіо»-колонок і груп)
            if c.cast_info.cast_type not in (None, "cast"):
                continue
            _devices[str(c.uuid)] = c
        return [{"uuid": u, "name": c.cast_info.friendly_name, "model": c.cast_info.model_name}
                for u, c in _devices.items()]


class Session:
    """Відтворення на одному пристрої."""

    def __init__(self, uuid):
        self.cc = _devices[uuid]
        self.name = self.cc.cast_info.friendly_name
        self.cc.wait(timeout=15)
        self.mc = self.cc.media_controller

    def play(self, url, title="", poster=None, start=0.0, subtitles=None):
        kw = {}
        if subtitles:
            kw.update(subtitles=subtitles, subtitles_lang="uk", subtitles_mime="text/vtt")
        # спершу явно відкрити стандартний плеєр Google Cast: інакше, якщо на телевізорі
        # уже відкрито інший застосунок із підтримкою Cast (MEGOGO, YouTube…), команда
        # «відтворити» йде йому, і той її мовчки ігнорує
        if not self.ours():
            self.cc.start_app(MEDIA_RECEIVER, force_launch=True, timeout=30)
            for _ in range(40):
                if self.ours():
                    break
                time.sleep(0.25)
        self.mc.play_media(url, "application/x-mpegURL", title=title, thumb=poster or None,
                           current_time=max(0.0, start), stream_type="BUFFERED", **kw)
        self.mc.block_until_active(timeout=20)
        if not self.ours():
            raise RuntimeError(f"телевізор не відкрив відео (зараз: {self.cc.app_display_name})")

    def ours(self):
        """Чи на телевізорі зараз наш плеєр (а не інший застосунок — YouTube, MEGOGO…)."""
        return self.cc.app_id == MEDIA_RECEIVER

    def status(self):
        """(позиція с, тривалість с, стан: PLAYING/PAUSED/BUFFERING/IDLE, причина зупинки).
        Якщо на телевізорі вже інший застосунок — стан IDLE / «OTHER_APP»."""
        if not self.ours():
            return 0.0, 0.0, "IDLE", "OTHER_APP"
        try:
            self.mc.update_status()
        except Exception:
            pass
        s = self.mc.status
        return (s.adjusted_current_time or s.current_time or 0.0, s.duration or 0.0,
                s.player_state, s.idle_reason)

    def toggle(self):
        if self.mc.status.player_state == "PLAYING":
            self.mc.pause()
        else:
            self.mc.play()

    def seek(self, seconds):
        self.mc.seek(max(0.0, seconds))

    def stop(self):
        try:
            if self.ours():  # чужий застосунок на телевізорі не закриваємо
                self.cc.quit_app()
        finally:
            self.cc.disconnect(timeout=5)
