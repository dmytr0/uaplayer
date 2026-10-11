# UAFix Player (Windows · macOS · Android)

Кросплатформна версія `uafix_player.py` на [Flet](https://flet.dev) (Python + Flutter).

- `core.py` — мережа й парсинг: картки з постерами, інфо про тайтл (IMDb, рік, жанри, опис), сезони/серії/джерела
- `ui.py` — палітра, анімації, спільні компоненти
- `main.py` — екрани: каталог (сітка / список, нескінченна прокрутка, бічне меню розділів),
  сторінка тайтлу (hero-зображення, сезони, серії), вбудований плеєр (libmpv) з потрібним Referer
- VLC більше не потрібен; автоперехід на наступну серію; збереження `.m3u` залишилось
- Гарячі клавіші: ⌘F / Ctrl+F — пошук, Esc — назад

## Запуск під час розробки

```bash
python3 -m venv .venv
.venv/bin/pip install flet==1.0.4 flet-video==1.0.4 requests beautifulsoup4
.venv/bin/flet run main.py
```

> macOS + Python з python.org: якщо бачите `CERTIFICATE_VERIFY_FAILED`, один раз запустіть
> `/Applications/Python 3.14/Install Certificates.command`.

## Збірка

Збірки для **macOS, Windows і Android** робить GitHub Actions (`.github/workflows/build.yml`)
при кожному push у `main`; готові файли — в артефактах запуску (вкладка *Actions*).
Тег `v*` (наприклад `v1.0.0`) додатково створює реліз із zip-архівами.

Локально (потрібні Xcode для macOS / Visual Studio для Windows; Flutter і Android SDK
`flet build` встановить сам):

```bash
flet build macos --yes
flet build windows --yes
flet build apk --yes
```

macOS-збірка не підписана: перший запуск — правий клік → «Відкрити», або
`xattr -dr com.apple.quarantine "UAFix Player.app"`.

## Відомі обмеження

- Пошук потоків для серіалів, де кожна серія — окрема сторінка, займає ~0.5 с на серію (як і в оригіналі).
- Якщо сайт увімкне Cloudflare-захист, на ПК можна додати `curl_cffi` у залежності; на Android він недоступний.
