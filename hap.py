"""Спарювання й шифрований канал за протоколом Apple (HAP: pair-setup / pair-verify) — для
AirPlay-телевізорів, які пускають лише спарені пристрої (відповідь 403 на /play).

Усе на чистому Python, без нових залежностей (щоб не ламати збірку під Android):
SRP-6a (3072 біт, SHA-512), X25519, Ed25519, ChaCha20-Poly1305, HKDF-SHA512, TLV8.
Обмін невеликий (кілька повідомлень), тож швидкості чистого Python досить.
"""
import hashlib
import hmac
import os
import struct

# ───────── TLV8 ─────────

T_METHOD, T_IDENTIFIER, T_SALT, T_PUBLIC_KEY, T_PROOF = 0, 1, 2, 3, 4
T_ENCRYPTED, T_STATE, T_ERROR, T_SIGNATURE, T_FLAGS = 5, 6, 7, 10, 19


def tlv_encode(items):
    out = bytearray()
    for t, v in items:
        if isinstance(v, int):
            v = bytes([v])
        if not v:
            out += bytes([t, 0])
        for i in range(0, len(v), 255):
            chunk = v[i:i + 255]
            out += bytes([t, len(chunk)]) + chunk
    return bytes(out)


def tlv_decode(data):
    res, i, last = {}, 0, None
    while i + 2 <= len(data):
        t, n = data[i], data[i + 1]
        v = data[i + 2:i + 2 + n]
        if t == last and t in res:
            res[t] += v  # продовження довгого значення
        else:
            res[t] = v
        last = t
        i += 2 + n
    return res


# ───────── HKDF-SHA512 ─────────

def hkdf(key, salt, info, length=32):
    prk = hmac.new(salt, key, hashlib.sha512).digest()
    out, t, i = b"", b"", 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha512).digest()
        out += t
        i += 1
    return out[:length]


# ───────── ChaCha20-Poly1305 (RFC 8439) ─────────

def _rotl(v, c):
    return ((v << c) & 0xffffffff) | (v >> (32 - c))


def _chacha_block(key, counter, nonce):
    c = [0x61707865, 0x3320646e, 0x79622d32, 0x6b206574]
    st = c + list(struct.unpack("<8L", key)) + [counter] + list(struct.unpack("<3L", nonce))
    x = st[:]

    def qr(a, b, cc, d):
        x[a] = (x[a] + x[b]) & 0xffffffff; x[d] = _rotl(x[d] ^ x[a], 16)
        x[cc] = (x[cc] + x[d]) & 0xffffffff; x[b] = _rotl(x[b] ^ x[cc], 12)
        x[a] = (x[a] + x[b]) & 0xffffffff; x[d] = _rotl(x[d] ^ x[a], 8)
        x[cc] = (x[cc] + x[d]) & 0xffffffff; x[b] = _rotl(x[b] ^ x[cc], 7)

    for _ in range(10):
        qr(0, 4, 8, 12); qr(1, 5, 9, 13); qr(2, 6, 10, 14); qr(3, 7, 11, 15)
        qr(0, 5, 10, 15); qr(1, 6, 11, 12); qr(2, 7, 8, 13); qr(3, 4, 9, 14)
    return struct.pack("<16L", *[(x[i] + st[i]) & 0xffffffff for i in range(16)])


def _chacha20(key, counter, nonce, data):
    out = bytearray()
    for i in range(0, len(data), 64):
        ks = _chacha_block(key, counter + i // 64, nonce)
        out += bytes(a ^ b for a, b in zip(data[i:i + 64], ks))
    return bytes(out)


def _poly1305(key, msg):
    r = int.from_bytes(key[:16], "little") & 0x0ffffffc0ffffffc0ffffffc0fffffff
    s = int.from_bytes(key[16:], "little")
    p, acc = (1 << 130) - 5, 0
    for i in range(0, len(msg), 16):
        n = int.from_bytes(msg[i:i + 16] + b"\x01", "little")
        acc = (acc + n) * r % p
    return ((acc + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _pad16(b):
    return b"\x00" * (-len(b) % 16)


def _aead_tag(key, nonce, aad, ct):
    otk = _chacha_block(key, 0, nonce)[:32]
    mac = aad + _pad16(aad) + ct + _pad16(ct) + struct.pack("<QQ", len(aad), len(ct))
    return _poly1305(otk, mac)


def seal(key, nonce, plaintext, aad=b""):
    ct = _chacha20(key, 1, nonce, plaintext)
    return ct + _aead_tag(key, nonce, aad, ct)


def open_(key, nonce, data, aad=b""):
    ct, tag = data[:-16], data[-16:]
    if not hmac.compare_digest(_aead_tag(key, nonce, aad, ct), tag):
        raise ValueError("помилка розшифрування")
    return _chacha20(key, 1, nonce, ct)


def nonce_str(s):
    return b"\x00" * (12 - len(s)) + s.encode()


# ───────── X25519 (RFC 7748) ─────────

_P = 2 ** 255 - 19


def x25519(k, u):
    k = bytearray(k)
    k[0] &= 248; k[31] &= 127; k[31] |= 64
    k = int.from_bytes(k, "little")
    u = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x1, x2, z2, x3, z3, swap = u, 1, 0, u, 1, 0
    for t in reversed(range(255)):
        kt = (k >> t) & 1
        swap ^= kt
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = kt
        a, b = (x2 + z2) % _P, (x2 - z2) % _P
        aa, bb = a * a % _P, b * b % _P
        e = (aa - bb) % _P
        c, d = (x3 + z3) % _P, (x3 - z3) % _P
        da, cb = d * a % _P, c * b % _P
        x3 = (da + cb) ** 2 % _P
        z3 = x1 * (da - cb) ** 2 % _P
        x2 = aa * bb % _P
        z2 = e * (aa + 121665 * e) % _P
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def x25519_keypair():
    priv = os.urandom(32)
    return priv, x25519(priv, (9).to_bytes(32, "little"))


# ───────── Ed25519 (RFC 8032) ─────────

_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _xrecover(y):
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * _I % _P
    return _P - x if x % 2 else x


_BY = 4 * pow(5, _P - 2, _P) % _P
_B = (_xrecover(_BY) % _P, _BY, 1, _xrecover(_BY) * _BY % _P)


def _add(p1, p2):
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    dd = z1 * 2 * z2 % _P
    e, f, g, h = b - a, dd - c, dd + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s, pt):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _encode(pt):
    x, y, z, _ = pt
    zi = pow(z, _P - 2, _P)
    x, y = x * zi % _P, y * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decode(b):
    y = int.from_bytes(b, "little") & ((1 << 255) - 1)
    x = _xrecover(y)
    if (x & 1) != (b[31] >> 7):
        x = _P - x
    return (x, y, 1, x * y % _P)


def _h(m):
    return int.from_bytes(hashlib.sha512(m).digest(), "little")


def ed25519_public(seed):
    a = hashlib.sha512(seed).digest()
    s = int.from_bytes(a[:32], "little")
    s &= (1 << 254) - 8
    s |= 1 << 254
    return _encode(_mul(s, _B))


def ed25519_sign(seed, msg):
    a = hashlib.sha512(seed).digest()
    s = int.from_bytes(a[:32], "little")
    s &= (1 << 254) - 8
    s |= 1 << 254
    pub = _encode(_mul(s, _B))
    r = _h(a[32:] + msg) % _Q
    rb = _encode(_mul(r, _B))
    k = _h(rb + pub + msg) % _Q
    return rb + ((r + k * s) % _Q).to_bytes(32, "little")


def ed25519_verify(pub, msg, sig):
    try:
        r, s = _decode(sig[:32]), int.from_bytes(sig[32:], "little")
        k = _h(sig[:32] + pub + msg) % _Q
        return _encode(_mul(s, _B)) == _encode(_add(r, _mul(k, _decode(pub))))
    except Exception:
        return False


# ───────── SRP-6a, 3072 біт, SHA-512 (як у HomeKit) ─────────

_N = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A0879"
    "8E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B"
    "0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA4836"
    "1C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804"
    "F1746C08CA18217C32905E462E36CE3BE39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6"
    "955817183995497CEA956AE515D2261898FA051015728E5A8AAAC42DAD33170D04507A33A85521ABDF1CBA64"
    "ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7ABF5AE8CDB0933D71E8C94E04A25619DCEE3D226"
    "1AD2EE6BF12FFA06D98A0864D87602733EC86A64521F2B18177B200CBBE117577A615D6C770988C0BAD946E2"
    "08E24FA074E5AB3143DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF", 16)
_G = 5
_NLEN = 384


def _sha(*parts):
    h = hashlib.sha512()
    for p in parts:
        h.update(p)
    return h.digest()


def _ib(n, length=None):
    return n.to_bytes(length or max(1, (n.bit_length() + 7) // 8), "big")


class SRPClient:
    def __init__(self, username, password):
        self.I, self.p = username.encode(), password.encode()
        self.a = int.from_bytes(os.urandom(32), "big")
        self.A = pow(_G, self.a, _N)

    @property
    def public(self):
        return _ib(self.A)

    def process(self, salt, B_bytes):
        B = int.from_bytes(B_bytes, "big")
        k = int.from_bytes(_sha(_ib(_N), _ib(_G, _NLEN)), "big")
        u = int.from_bytes(_sha(_ib(self.A, _NLEN), _ib(B, _NLEN)), "big")
        x = int.from_bytes(_sha(salt, _sha(self.I + b":" + self.p)), "big")
        S = pow((B - k * pow(_G, x, _N)) % _N, self.a + u * x, _N)
        self.K = _sha(_ib(S))
        hn, hg = _sha(_ib(_N)), _sha(_ib(_G))
        hxor = bytes(a ^ b for a, b in zip(hn, hg))
        self.M1 = _sha(hxor, _sha(self.I), salt, _ib(self.A), B_bytes, self.K)
        self.M2 = _sha(_ib(self.A), self.M1, self.K)
        return self.M1

    def verify(self, server_proof):
        return hmac.compare_digest(server_proof, self.M2)


# ───────── шифрований HTTP-канал після pair-verify ─────────

class Cipher:
    """Кадри: 2 байти довжини (LE, ≤1024) + шифротекст + 16 байт тегу; AAD — довжина."""

    def __init__(self, write_key, read_key):
        self.wk, self.rk, self.wn, self.rn = write_key, read_key, 0, 0
        self.buf = b""

    @staticmethod
    def _nonce(n):
        return b"\x00" * 4 + struct.pack("<Q", n)

    def encrypt(self, data):
        out = bytearray()
        for i in range(0, len(data), 1024):
            block = data[i:i + 1024]
            ln = struct.pack("<H", len(block))
            out += ln + seal(self.wk, self._nonce(self.wn), block, ln)
            self.wn += 1
        return bytes(out)

    def decrypt(self, data):
        """Додати отримані байти; повертає розшифроване з повних кадрів."""
        self.buf += data
        out = bytearray()
        while len(self.buf) >= 2:
            n = struct.unpack("<H", self.buf[:2])[0]
            if len(self.buf) < 2 + n + 16:
                break
            frame = self.buf[2:2 + n + 16]
            out += open_(self.rk, self._nonce(self.rn), frame, self.buf[:2])
            self.rn += 1
            self.buf = self.buf[2 + n + 16:]
        return bytes(out)
