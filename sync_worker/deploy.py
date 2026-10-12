"""Розгортання сервера синхронізації в Cloudflare (Workers + D1) через API, без wrangler.

Токен читається з ~/.uafix_cf_token (права: Workers Scripts Edit, D1 Edit) і ніде
не виводиться. Запуск:  python sync_worker/deploy.py
"""
import json
import pathlib
import sys

import requests

API = "https://api.cloudflare.com/client/v4"
NAME = "uafix-sync"
HERE = pathlib.Path(__file__).parent

token = (pathlib.Path.home() / ".uafix_cf_token").read_text().strip()
s = requests.Session()
s.headers["Authorization"] = f"Bearer {token}"


def call(method, path, **kw):
    r = s.request(method, API + path, timeout=60, **kw)
    try:
        data = r.json()
    except ValueError:
        sys.exit(f"{method} {path}: HTTP {r.status_code}")
    if not data.get("success"):
        errors = "; ".join(e.get("message", "") for e in data.get("errors", []))
        raise RuntimeError(f"{method} {path}: {errors or r.status_code}")
    return data.get("result")


accounts = call("GET", "/accounts")
if not accounts:
    sys.exit("Токен не бачить жодного акаунта")
acc = accounts[0]["id"]
print("Акаунт:", accounts[0]["name"])

# база D1
dbs = call("GET", f"/accounts/{acc}/d1/database", params={"name": NAME}) or []
db = next((d for d in dbs if d["name"] == NAME), None) or \
    call("POST", f"/accounts/{acc}/d1/database", json={"name": NAME})
db_id = db["uuid"]
print("База D1:", NAME)
call("POST", f"/accounts/{acc}/d1/database/{db_id}/query", json={
    "sql": "CREATE TABLE IF NOT EXISTS sync (code TEXT PRIMARY KEY, data TEXT NOT NULL, "
           "updated INTEGER NOT NULL)"})

# воркер
metadata = {"main_module": "worker.js", "compatibility_date": "2025-01-01",
            "bindings": [{"type": "d1", "name": "DB", "id": db_id}]}
call("PUT", f"/accounts/{acc}/workers/scripts/{NAME}", files={
    "metadata": (None, json.dumps(metadata), "application/json"),
    "worker.js": ("worker.js", (HERE / "worker.js").read_bytes(), "application/javascript+module"),
})
print("Воркер завантажено")

# адреса *.workers.dev
try:
    sub = call("GET", f"/accounts/{acc}/workers/subdomain")["subdomain"]
except RuntimeError:
    sub = None
if not sub:
    sub = call("PUT", f"/accounts/{acc}/workers/subdomain",
               json={"subdomain": f"uafix-{acc[:6]}"})["subdomain"]
call("POST", f"/accounts/{acc}/workers/scripts/{NAME}/subdomain", json={"enabled": True})
url = f"https://{NAME}.{sub}.workers.dev"
print("Адреса:", url)
(HERE / "url.txt").write_text(url + "\n")
