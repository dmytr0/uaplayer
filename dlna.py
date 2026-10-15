"""Трансляція на телевізори з DLNA / UPnP (Hisense VIDAA, Samsung, LG та інші) — бета.

Телевізору передається посилання, і він сам грає відео, а застосунок лише керує (як із
Chromecast). Не кожен вбудований плеєр DLNA розуміє HLS (.m3u8), тож якщо за кілька секунд
відтворення не пішло, застосунок сам віддає телевізору відео одним потоком MPEG-TS
(сегменти HLS — це .ts, їх можна просто склеїти): тоді застосунок має лишатися відкритим,
а перемотка — це новий потік із потрібного місця.
Усі функції блокуючі — викликати з фонового потоку.
"""
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urljoin, urlparse
from xml.sax.saxutils import escape

import requests

AVT = "urn:schemas-upnp-org:service:AVTransport:1"
_devices = {}  # uuid → {"name", "location", "control", "host"}


# ───────── пошук ─────────

MANUAL_HOSTS = []  # IP телевізорів, доданих вручну: їх питаємо напряму (повз мультикаст)
LAST_SEEN = []  # хто відповів на пошук, але не є телевізором (для підказки «не знайдено»)


def discover(timeout=4):
    """Телевізори DLNA (MediaRenderer з AVTransport) у мережі: [{uuid, name, model}]."""
    sts = ("urn:schemas-upnp-org:device:MediaRenderer:1",
           "urn:schemas-upnp-org:service:AVTransport:1", "ssdp:all")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.settimeout(0.5)
    locations = set()
    try:
        for st in sts:
            msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                   f"MAN: \"ssdp:discover\"\r\nMX: 2\r\nST: {st}\r\n\r\n").encode()
            for _ in range(2):
                s.sendto(msg, ("239.255.255.250", 1900))
                for host in MANUAL_HOSTS:  # напряму — якщо роутер не пропускає мультикаст
                    try:
                        s.sendto(msg, (host, 1900))
                    except OSError:
                        pass
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, _ = s.recvfrom(65507)
            except socket.timeout:
                continue
            m = re.search(rb"(?im)^location:\s*(\S+)", data)
            if m:
                locations.add(m.group(1).decode(errors="ignore"))
    finally:
        s.close()
    found, others = [], set()
    for loc in locations:
        try:
            dev = _describe(loc)
        except Exception:
            continue
        if dev and dev.get("control"):
            if dev["uuid"] not in {d["uuid"] for d in found}:
                _devices[dev["uuid"]] = dev
                found.append({"uuid": dev["uuid"], "name": dev["name"], "model": dev["model"]})
        elif dev:
            others.add(dev["name"])
    LAST_SEEN[:] = sorted(others - {d["name"] for d in found})
    return found


def probe(host, timeout=4):
    """Діагностика телевізора за IP: чи досяжний, чи є DLNA (і знайдений пристрій), AirPlay,
    Google Cast. → {"reachable", "dlna": [devices], "airplay", "cast"}."""
    def port_open(port):
        try:
            with socket.create_connection((host, port), timeout=1.5):
                return True
        except OSError:
            return False

    if host not in MANUAL_HOSTS:
        MANUAL_HOSTS.insert(0, host)
    found = [d for d in discover(timeout) if _devices[d["uuid"]]["host"] == host]
    open_ports = {p for p in (7000, 8008, 8009, 1900, 80, 8080, 9197, 38400, 49152, 56789)
                  if port_open(p)}
    return {"reachable": bool(found or open_ports), "dlna": found,
            "airplay": 7000 in open_ports, "cast": bool({8008, 8009} & open_ports)}


def _tag(xml, name):
    m = re.search(rf"<(?:\w+:)?{name}>(.*?)</(?:\w+:)?{name}>", xml, re.S)
    return m.group(1).strip() if m else ""


def _describe(location):
    xml = requests.get(location, timeout=3).text
    control = None
    for svc in re.findall(r"<service>(.*?)</service>", xml, re.S):
        if "AVTransport" in _tag(svc, "serviceType"):
            control = urljoin(location, _tag(svc, "controlURL"))
    udn = _tag(xml, "UDN") or location
    return {"uuid": "dlna:" + udn, "name": _tag(xml, "friendlyName") or "Телевізор (DLNA)",
            "model": " ".join(x for x in (_tag(xml, "manufacturer"), _tag(xml, "modelName")) if x),
            "location": location, "control": control, "host": urlparse(location).hostname}


def _soap(dev, action, **args):
    body = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
    env = ('<?xml version="1.0" encoding="utf-8"?>'
           '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
           's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
           f'<u:{action} xmlns:u="{AVT}"><InstanceID>0</InstanceID>{body}</u:{action}>'
           '</s:Body></s:Envelope>')
    r = requests.post(dev["control"], data=env.encode("utf-8"), timeout=8, headers={
        "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{AVT}#{action}"'})
    if r.status_code >= 400:
        raise RuntimeError(f"{action}: HTTP {r.status_code}")
    return r.text


def _hms(sec):
    sec = max(0, int(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def _sec(hms):
    try:
        h, m, s = (hms or "0:0:0").split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except ValueError:
        return 0.0


# ───────── запасний варіант: HLS → один потік MPEG-TS ─────────

class _Relay:
    """Маленький HTTP-сервер у застосунку: /stream.ts?from=<с> — сегменти HLS підряд."""

    def __init__(self, playlist_url, headers, tv_host):
        self.headers = headers
        text = requests.get(playlist_url, headers=headers, timeout=10).text
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if any(ln.startswith("#EXT-X-STREAM-INF") for ln in lines):  # головний список → перша якість
            sub = next(ln for ln in lines if not ln.startswith("#"))
            playlist_url = urljoin(playlist_url, sub)
            text = requests.get(playlist_url, headers=headers, timeout=10).text
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        self.segments, dur = [], 0.0  # (початок с, тривалість с, url)
        for i, ln in enumerate(lines):
            if ln.startswith("#EXTINF:"):
                d = float(ln[8:].split(",")[0] or 0)
                url = next((x for x in lines[i + 1:] if not x.startswith("#")), None)
                if url:
                    self.segments.append((dur, d, urljoin(playlist_url, url)))
                    dur += d
        self.duration = dur
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"  # без довжини — потік до закриття з'єднання

            def log_message(self, *a):
                pass

            def _head(self):
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("transferMode.dlna.org", "Streaming")
                self.send_header("contentFeatures.dlna.org",
                                 "DLNA.ORG_OP=00;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000")
                self.send_header("Connection", "close")
                self.end_headers()

            def do_HEAD(self):
                self._head()

            def do_GET(self):
                q = parse_qs(urlparse(self.path).query)
                start = float((q.get("from") or ["0"])[0])
                self._head()
                try:
                    for s0, d, url in relay.segments:
                        if s0 + d <= start:
                            continue
                        with requests.get(url, headers=relay.headers, timeout=15, stream=True) as r:
                            for chunk in r.iter_content(64 * 1024):
                                self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass  # телевізор закрив потік (зупинка чи перемотка)

        self.server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        # адреса цього пристрою в мережі телевізора
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((tv_host, 9))
            ip = s.getsockname()[0]
        finally:
            s.close()
        self.base = f"http://{ip}:{self.server.server_address[1]}/stream.ts"

    def url(self, start):
        return f"{self.base}?from={int(start)}"

    def close(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass


# ───────── сесія ─────────

class Session:
    """Відтворення на одному телевізорі DLNA (той самий набір методів, що й у cast.Session)."""

    def __init__(self, uuid, headers=None):
        self.dev = _devices[uuid]
        self.name = self.dev["name"]
        self.headers = headers or {}
        self.relay = None
        self.offset = 0.0  # з якого місця почався поточний потік (для запасного варіанта)
        self.seen_playing = False
        self.started = 0.0
        self.title = ""
        self.poster = None

    def _meta(self, url, mime):
        return escape(
            '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            '<item id="0" parentID="-1" restricted="1">'
            f'<dc:title>{escape(self.title or "UAFix")}</dc:title>'
            '<upnp:class>object.item.videoItem</upnp:class>'
            + (f'<upnp:albumArtURI>{escape(self.poster)}</upnp:albumArtURI>' if self.poster else "")
            + f'<res protocolInfo="http-get:*:{mime}:*">{escape(url)}</res></item></DIDL-Lite>')

    def _load(self, url, mime):
        try:
            _soap(self.dev, "Stop")
        except Exception:
            pass
        _soap(self.dev, "SetAVTransportURI", CurrentURI=escape(url),
              CurrentURIMetaData=self._meta(url, mime))
        _soap(self.dev, "Play", Speed="1")
        self.seen_playing = False
        self.started = time.time()

    def _wait_playing(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            time.sleep(1)
            try:
                state = _tag(_soap(self.dev, "GetTransportInfo"), "CurrentTransportState")
                rel = _sec(_tag(_soap(self.dev, "GetPositionInfo"), "RelTime"))
            except Exception:
                continue
            if state == "PLAYING" and rel > 0:
                return True
            if state in ("STOPPED", "NO_MEDIA_PRESENT") and time.time() > end - seconds + 4:
                return False
        return False

    def play(self, url, title="", poster=None, start=0.0, subtitles=None):
        self.title, self.poster = title, poster
        # 1) пряме посилання на HLS — якщо телевізор його розуміє, це найкраще
        try:
            self._load(url, "application/vnd.apple.mpegurl")
            if self._wait_playing(12):
                if start > 5:
                    try:
                        _soap(self.dev, "Seek", Unit="REL_TIME", Target=_hms(start))
                    except Exception:
                        pass
                return
        except Exception:
            pass
        # 2) запасний варіант — потік MPEG-TS із застосунку
        self.relay = _Relay(url, self.headers, self.dev["host"])
        self.offset = max(0.0, start)
        self._load(self.relay.url(self.offset), "video/mp2t")
        if not self._wait_playing(20):
            self.stop()
            raise RuntimeError("телевізор не відтворює це відео")

    def status(self):
        info = _soap(self.dev, "GetTransportInfo")
        pos = _soap(self.dev, "GetPositionInfo")
        state = _tag(info, "CurrentTransportState")
        rel, dur = _sec(_tag(pos, "RelTime")), _sec(_tag(pos, "TrackDuration"))
        if self.relay:
            rel, dur = self.offset + rel, self.relay.duration
        if state == "PLAYING":
            self.seen_playing = True
            return rel, dur, "PLAYING", None
        if state == "PAUSED_PLAYBACK":
            return rel, dur, "PAUSED", None
        if state in ("STOPPED", "NO_MEDIA_PRESENT"):
            if not self.seen_playing and time.time() - self.started < 25:
                return rel, dur, "BUFFERING", None  # ще не почалось
            done = dur and rel >= dur - 30
            return rel, dur, "IDLE", "FINISHED" if done else "CANCELLED"
        return rel, dur, "BUFFERING", None

    def toggle(self):
        state = _tag(_soap(self.dev, "GetTransportInfo"), "CurrentTransportState")
        if state == "PLAYING":
            _soap(self.dev, "Pause")
        else:
            _soap(self.dev, "Play", Speed="1")

    def seek(self, seconds):
        seconds = max(0.0, seconds)
        if self.relay:  # потік із застосунку — почати новий із потрібного місця
            self.offset = seconds
            self._load(self.relay.url(seconds), "video/mp2t")
        else:
            _soap(self.dev, "Seek", Unit="REL_TIME", Target=_hms(seconds))

    def stop(self):
        try:
            _soap(self.dev, "Stop")
        finally:
            if self.relay:
                self.relay.close()
                self.relay = None
