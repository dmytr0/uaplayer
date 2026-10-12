"""Звіти про проблеми й побажання — автору в Telegram через бота.

Токен бота й chat_id вшиває CI в app_version.py із секретів репозиторію
FEEDBACK_BOT_TOKEN / FEEDBACK_CHAT_ID (як в Orange Audio Lab). Без них (версія для розробки
чи збірка без секретів) enabled() == False, і застосунок відкриває форму на GitHub.
"""
import time

import requests

try:
    from app_version import FEEDBACK_BOT_TOKEN, FEEDBACK_CHAT_ID
except ImportError:
    FEEDBACK_BOT_TOKEN = FEEDBACK_CHAT_ID = ""

MAX_LEN = 3000
KINDS = {"bug": "🐞 Помилка", "idea": "💡 Пропозиція", "review": "💬 Відгук"}
_last = [0.0]


def enabled():
    return bool(FEEDBACK_BOT_TOKEN and FEEDBACK_CHAT_ID)


def send(kind, text, signature):
    """Надсилає повідомлення; signature — «UAFix Player 1.2.3 · android (TV)…».
    Помилки — ValueError зі зрозумілим текстом."""
    if not enabled():
        raise ValueError("Відправка звітів не налаштована в цій версії")
    text = (text or "").strip()
    if not text:
        raise ValueError("Напишіть кілька слів")
    if len(text) > MAX_LEN:
        raise ValueError(f"Задовге повідомлення: {len(text)} символів, максимум {MAX_LEN}")
    wait = 30 - (time.monotonic() - _last[0])
    if wait > 0:
        raise ValueError(f"Зачекайте {int(wait) + 1} с перед наступним повідомленням")
    msg = f"{KINDS.get(kind, KINDS['review'])}\n\n{text}\n\n— {signature}"
    try:
        r = requests.post(f"https://api.telegram.org/bot{FEEDBACK_BOT_TOKEN}/sendMessage",
                          data={"chat_id": FEEDBACK_CHAT_ID, "text": msg,
                                "disable_web_page_preview": "true"}, timeout=20)
        ok = r.json().get("ok")
    except Exception:
        raise ValueError("Не вдалося надіслати — немає зв'язку з Telegram")
    if not ok:
        raise ValueError("Telegram не прийняв повідомлення")
    _last[0] = time.monotonic()
