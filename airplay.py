"""Трансляція через AirPlay (бета) — телевізори з AirPlay (Hisense VIDAA, Samsung, LG…).

Телевізору передається посилання (HLS він грає сам), застосунок лише керує:
/play, /rate (пауза), /scrub (перемотка), /playback-info, /stop.
Багато телевізорів пускають лише спарені пристрої (403 на /play): тоді одноразове
спарювання з PIN-кодом з екрана телевізора (hap.py), далі — шифроване з'єднання
(pair-verify) без коду. Ключі спарювання зберігає застосунок (credentials).
Усі функції блокуючі — з фонового потоку.
"""
import os
import plistlib
import socket
import uuid as _uuid

import hap

PORT = 7000
UA = "AirPlay/320.20"


class PairingRequired(Exception):
    """Телевізор хоче PIN-код: на його екрані вже показано код — потрібен pair(pin)."""


# ───────── простий HTTP/1.1 поверх сокета (з шифруванням після pair-verify) ─────────

class Conn:
    def __init__(self, host, timeout=10):
        self.sock = socket.create_connection((host, PORT), timeout=timeout)
        self.host = host
        self.cipher = None
        self.session_id = str(_uuid.uuid4()).upper()

    def request(self, method, path, body=b"", ctype=None, extra=None):
        head = [f"{method} {path} HTTP/1.1", f"Host: {self.host}:{PORT}", f"User-Agent: {UA}",
                f"X-Apple-Session-ID: {self.session_id}", f"Content-Length: {len(body)}"]
        if ctype:
            head.append(f"Content-Type: {ctype}")
        for k, v in (extra or {}).items():
            head.append(f"{k}: {v}")
        raw = ("\r\n".join(head) + "\r\n\r\n").encode() + body
        self.sock.sendall(self.cipher.encrypt(raw) if self.cipher else raw)
        return self._read()

    def _recv(self):
        data = self.sock.recv(65536)
        if not data:
            raise ConnectionError("телевізор закрив з'єднання")
        return self.cipher.decrypt(data) if self.cipher else data

    def _read(self):
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self._recv()
        head, rest = buf.split(b"\r\n\r\n", 1)
        lines = head.decode(errors="ignore").split("\r\n")
        status = int(lines[0].split()[1])
        headers = {ln.split(":", 1)[0].strip().lower(): ln.split(":", 1)[1].strip()
                   for ln in lines[1:] if ":" in ln}
        n = int(headers.get("content-length", "0") or 0)
        while len(rest) < n:
            rest += self._recv()
        return status, rest[:n]

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


HKP = {"X-Apple-HKP": "3"}
OCTET = "application/octet-stream"


def _check(tlv, step):
    if hap.T_ERROR in tlv:
        code = tlv[hap.T_ERROR][0]
        raise RuntimeError({2: "неправильний PIN-код", 3: "забагато спроб — зачекайте",
                            5: "телевізор зайнятий спарюванням", 6: "телевізор зараз не приймає "
                            "спарювання"}.get(code, f"помилка спарювання {code} ({step})"))


# ───────── спарювання (один раз, з PIN-кодом) ─────────

class Pairing:
    """start() — телевізор показує PIN; finish(pin) → облікові дані (dict) для збереження."""

    def __init__(self, host):
        self.host = host

    def start(self):
        self.conn = Conn(self.host)
        try:
            self.conn.request("POST", "/pair-pin-start", extra=HKP)
        except Exception:
            pass
        st, body = self.conn.request("POST", "/pair-setup", hap.tlv_encode(
            [(hap.T_METHOD, 0), (hap.T_STATE, 1)]), OCTET, HKP)
        if st >= 400:
            raise RuntimeError(f"pair-setup: код {st}")
        tlv = hap.tlv_decode(body)
        _check(tlv, "M2")
        self.salt, self.B = tlv[hap.T_SALT], tlv[hap.T_PUBLIC_KEY]

    def finish(self, pin):
        srp = hap.SRPClient("Pair-Setup", str(pin).strip())
        proof = srp.process(self.salt, self.B)
        st, body = self.conn.request("POST", "/pair-setup", hap.tlv_encode(
            [(hap.T_STATE, 3), (hap.T_PUBLIC_KEY, srp.public), (hap.T_PROOF, proof)]), OCTET, HKP)
        tlv = hap.tlv_decode(body)
        _check(tlv, "M4")
        if not srp.verify(tlv.get(hap.T_PROOF, b"")):
            raise RuntimeError("неправильний PIN-код")
        seed = os.urandom(32)
        ltpk = hap.ed25519_public(seed)
        pairing_id = str(_uuid.uuid4()).upper().encode()
        x = hap.hkdf(srp.K, b"Pair-Setup-Controller-Sign-Salt", b"Pair-Setup-Controller-Sign-Info")
        sig = hap.ed25519_sign(seed, x + pairing_id + ltpk)
        key = hap.hkdf(srp.K, b"Pair-Setup-Encrypt-Salt", b"Pair-Setup-Encrypt-Info")
        inner = hap.tlv_encode([(hap.T_IDENTIFIER, pairing_id), (hap.T_PUBLIC_KEY, ltpk),
                                (hap.T_SIGNATURE, sig)])
        st, body = self.conn.request("POST", "/pair-setup", hap.tlv_encode(
            [(hap.T_STATE, 5), (hap.T_ENCRYPTED, hap.seal(key, hap.nonce_str("PS-Msg05"), inner))]),
            OCTET, HKP)
        tlv = hap.tlv_decode(body)
        _check(tlv, "M6")
        acc = hap.tlv_decode(hap.open_(key, hap.nonce_str("PS-Msg06"), tlv[hap.T_ENCRYPTED]))
        self.conn.close()
        return {"seed": seed.hex(), "id": pairing_id.decode(),
                "acc_id": acc.get(hap.T_IDENTIFIER, b"").decode(errors="ignore"),
                "acc_ltpk": acc.get(hap.T_PUBLIC_KEY, b"").hex()}


def verify(conn, creds):
    """pair-verify із збереженими ключами → далі з'єднання шифроване."""
    priv, pub = hap.x25519_keypair()
    st, body = conn.request("POST", "/pair-verify", hap.tlv_encode(
        [(hap.T_STATE, 1), (hap.T_PUBLIC_KEY, pub)]), OCTET, HKP)
    if st >= 400:
        raise RuntimeError(f"pair-verify: код {st}")
    tlv = hap.tlv_decode(body)
    _check(tlv, "V2")
    acc_pub = tlv[hap.T_PUBLIC_KEY]
    shared = hap.x25519(priv, acc_pub)
    key = hap.hkdf(shared, b"Pair-Verify-Encrypt-Salt", b"Pair-Verify-Encrypt-Info")
    hap.open_(key, hap.nonce_str("PV-Msg02"), tlv[hap.T_ENCRYPTED])  # перевірка ключа
    seed, pid = bytes.fromhex(creds["seed"]), creds["id"].encode()
    sig = hap.ed25519_sign(seed, pub + pid + acc_pub)
    inner = hap.tlv_encode([(hap.T_IDENTIFIER, pid), (hap.T_SIGNATURE, sig)])
    st, body = conn.request("POST", "/pair-verify", hap.tlv_encode(
        [(hap.T_STATE, 3), (hap.T_ENCRYPTED, hap.seal(key, hap.nonce_str("PV-Msg03"), inner))]),
        OCTET, HKP)
    _check(hap.tlv_decode(body), "V4")
    conn.cipher = hap.Cipher(
        hap.hkdf(shared, b"Control-Salt", b"Control-Write-Encryption-Key"),
        hap.hkdf(shared, b"Control-Salt", b"Control-Read-Encryption-Key"))


# ───────── пошук і опис ─────────

def available(host, timeout=1.5):
    try:
        with socket.create_connection((host, PORT), timeout=timeout):
            return True
    except OSError:
        return False


def info(host):
    """Назва й модель з /info — {name, model}."""
    try:
        c = Conn(host, timeout=4)
        st, body = c.request("GET", "/info")
        c.close()
        d = plistlib.loads(body) if st < 400 and body else {}
        return {"name": d.get("name") or "", "model": d.get("model") or ""}
    except Exception:
        return {"name": "", "model": ""}


def device(host):
    i = info(host)
    return {"uuid": f"airplay:{host}", "kind": "airplay", "host": host,
            "name": (i["name"] or f"Телевізор {host}"), "model": i["model"]}


# ───────── сесія ─────────

CREDENTIALS = {}  # host → облікові дані спарювання (заповнює й зберігає застосунок)


class Session:
    """Відтворення на одному AirPlay-пристрої (ті самі методи, що й у cast.Session)."""

    def __init__(self, host, name=""):
        self.host = host
        self.name = name or f"AirPlay {host}"
        self.conn = None
        self.paused = False

    def _connect(self):
        if self.conn:
            return
        self.conn = Conn(self.host)
        creds = CREDENTIALS.get(self.host)
        if creds:
            try:
                verify(self.conn, creds)
            except Exception:
                self.conn.close()
                self.conn = None
                CREDENTIALS.pop(self.host, None)  # ключі вже не дійсні — спарити заново
                raise PairingRequired()

    def _call(self, method, path, body=b"", ctype=None):
        self._connect()
        try:
            st, data = self.conn.request(method, path, body, ctype)
        except (OSError, ConnectionError):
            self.conn = None  # з'єднання впало — одна спроба наново
            self._connect()
            st, data = self.conn.request(method, path, body, ctype)
        if st in (401, 403, 470):
            if not CREDENTIALS.get(self.host):
                raise PairingRequired()
            raise RuntimeError(f"телевізор відмовив (код {st})")
        if st >= 400:
            raise RuntimeError(f"AirPlay {path}: код {st}")
        return data

    def play(self, url, title="", poster=None, start=0.0, subtitles=None):
        body = plistlib.dumps({"Content-Location": url, "Start-Position": 0.0,
                               "X-Apple-Session-ID": self.conn.session_id if self.conn else ""},
                              fmt=plistlib.FMT_BINARY)
        try:
            self._call("POST", "/play", body, "application/x-apple-binary-plist")
        except RuntimeError:
            # деякі приймачі хочуть старий текстовий формат
            self._call("POST", "/play",
                       f"Content-Location: {url}\nStart-Position: 0\n".encode(), "text/parameters")
        if start > 5:
            import time
            for _ in range(20):  # дочекатись, поки відео відкриється, і перемотати
                time.sleep(0.5)
                if self.status()[1]:
                    self.seek(start)
                    break

    def status(self):
        try:
            data = self._call("GET", "/playback-info")
        except RuntimeError:
            return 0.0, 0.0, "BUFFERING", None
        if not data:
            return 0.0, 0.0, "BUFFERING", None
        d = plistlib.loads(data)
        pos, dur = float(d.get("position") or 0), float(d.get("duration") or 0)
        rate = float(d.get("rate") or 0)
        if not dur:
            return pos, dur, "BUFFERING", None
        if pos >= dur - 2 and rate == 0:
            return pos, dur, "IDLE", "FINISHED"
        return pos, dur, "PAUSED" if rate == 0 else "PLAYING", None

    def toggle(self):
        self.paused = not self.paused
        self._call("POST", f"/rate?value={0 if self.paused else 1}")

    def seek(self, seconds):
        self._call("POST", f"/scrub?position={max(0.0, seconds):.1f}")

    def stop(self):
        try:
            self._call("POST", "/stop")
        finally:
            if self.conn:
                self.conn.close()
                self.conn = None
