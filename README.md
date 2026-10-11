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

Збирає GitHub Actions ([.github/workflows/build.yml](.github/workflows/build.yml)) — для кожного
push у `main`, вручну (Actions → Build → Run workflow) або при тезі `v*`:

| Платформа | Runner | Результат (artifact) |
|---|---|---|
| macOS | `macos-latest` (Xcode) | `UAFix-Player-macOS.zip` → `UAFix Player.app` |
| Windows | `windows-latest` (Visual Studio) | `UAFix-Player-Windows.zip` → `.exe` з бібліотеками |
| Android | `ubuntu-latest` (Android SDK, JDK 17) | `UAFix-Player-Android.apk` |

Тег `v1.0.0` (`git tag v1.0.0 && git push --tags`) додатково створює GitHub Release з усіма трьома файлами.

Локально (потрібен Xcode для macOS, Visual Studio для Windows; Flutter, JDK та Android SDK
`flet build` поставить сам з `--yes`):

```bash
flet build macos --yes
flet build apk --yes
```

Збірки не підписані: на macOS перший запуск — правий клік на застосунку → «Відкрити»;
на Android — дозволити встановлення з невідомих джерел.

## Відомі обмеження

- Пошук потоків для серіалів, де кожна серія — окрема сторінка, займає ~0.5 с на серію (як і в оригіналі).
- Якщо сайт увімкне Cloudflare-захист, на ПК можна додати `curl_cffi` у залежності; на Android він недоступний.
