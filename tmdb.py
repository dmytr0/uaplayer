"""Офіційні широкі кадри (backdrop 16:9) з TMDB замість картинок сайту (800×450, з написами).

Ключ — «API Read Access Token»: у збірці його вшиває CI (секрет TMDB_TOKEN → app_version.py),
у версії для розробки — файл ~/.uafix_tmdb_token. Без ключа все працює як раніше.
Знайдене (і не знайдене) запам'ятовується на диску — той самий фільм удруге не шукаємо.
Дані: TMDB (умови TMDB вимагають цієї згадки в застосунку)."""
import json
import os
import re
import threading

import requests

try:
    from app_version import TMDB_TOKEN
except Exception:
    TMDB_TOKEN = ""
if not TMDB_TOKEN:
    try:
        with open(os.path.expanduser("~/.uafix_tmdb_token"), encoding="utf-8") as f:
            TMDB_TOKEN = f.read().strip()
    except OSError:
        pass

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"
_session = requests.Session()
_lock = threading.Lock()
_cache = None  # url сторінки на сайті → шлях кадру в TMDB ("" — не знайдено)


def enabled():
    return bool(TMDB_TOKEN)


def _cache_file():
    base = os.environ.get("FLET_APP_STORAGE_DATA") or os.path.expanduser("~")
    return os.path.join(base, ".uafix_tmdb.json")


def _load():
    global _cache
    if _cache is None:
        try:
            with open(_cache_file(), encoding="utf-8") as f:
                _cache = json.load(f)
        except Exception:
            _cache = {}
    return _cache


def _save():
    try:
        with open(_cache_file(), "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False)
    except Exception:
        pass


def _get(path, **params):
    r = _session.get(API + path, params=params, timeout=8,
                     headers={"Authorization": f"Bearer {TMDB_TOKEN}",
                              "accept": "application/json"})
    r.raise_for_status()
    return r.json()


def _norm(s):
    return re.sub(r"[^\w]+", " ", (s or "").lower()).strip()


def _search(kind, query, year):
    """Найкращий збіг: спершу з роком, потім без нього (у серіалів рік сайту — часто рік
    сезону). Серед результатів — той, чия назва збігається, інакше найпопулярніший."""
    key_year = "primary_release_year" if kind == "movie" else "first_air_date_year"
    for params in ({key_year: year} if year else None, {}):
        if params is None:
            continue
        res = _get(f"/search/{kind}", query=query, language="uk-UA", include_adult="false",
                   **params).get("results", [])
        if not res:
            continue
        q = _norm(query)
        same = [r for r in res
                if q in (_norm(r.get("title") or r.get("name")),
                         _norm(r.get("original_title") or r.get("original_name")))]
        return (same or res)[0]
    return None


def _best_backdrop(kind, tmdb_id, fallback):
    """Кадр без тексту (мова «null») з найвищою оцінкою; інакше — основний кадр TMDB."""
    try:
        imgs = _get(f"/{kind}/{tmdb_id}/images", include_image_language="null").get("backdrops", [])
    except Exception:
        imgs = []
    imgs = [i for i in imgs if i.get("aspect_ratio", 0) > 1.6]
    if imgs:
        imgs.sort(key=lambda i: (i.get("vote_average", 0), i.get("width", 0)), reverse=True)
        return imgs[0]["file_path"]
    return fallback or ""


def cached(url, size="w1280"):
    """(чи вже шукали, URL або None) — без мережі, щоб одразу показати знайдене раніше."""
    if not TMDB_TOKEN:
        return True, None
    with _lock:
        cache = _load()
        if url not in cache:
            return False, None
        return True, IMG + size + cache[url] if cache[url] else None


def backdrop(url, title, orig="", year="", size="w1280"):
    """URL офіційного широкого кадру для тайтлу сайту або None. Блокує (мережа) —
    викликати з потоку."""
    if not TMDB_TOKEN or not title:
        return None
    with _lock:
        cache = _load()
        if url in cache:
            path = cache[url]
            return IMG + size + path if path else None
    kinds = ("movie", "tv") if "/films/" in url else ("tv", "movie")
    path = ""
    try:
        for kind in kinds:
            hit = None
            for q in (title, orig):
                if q:
                    hit = _search(kind, q, str(year or "")[:4])
                    if hit:
                        break
            if hit:
                path = _best_backdrop(kind, hit["id"], hit.get("backdrop_path"))
                break
    except Exception:
        return None  # мережа/ліміт — не запам'ятовуємо, спробуємо наступного разу
    with _lock:
        _cache[url] = path
        _save()
    return IMG + size + path if path else None
