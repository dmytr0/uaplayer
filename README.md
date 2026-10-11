# UAFix Player (Windows · macOS · Android)

Кросплатформна версія `uafix_player.py` на [Flet](https://flet.dev) (Python + Flutter).

- `core.py` — мережа й парсинг: картки з постерами, інфо про тайтл (IMDb, рік, жанри, опис), сезони/серії/джерела
- `ui.py` — палітра, анімації, спільні компоненти
- `main.py` — екрани: каталог (сітка / список, нескінченна прокрутка, бічне меню розділів),
  сторінка тайтлу (hero-зображення, сезони, серії), вбудований плеєр (libmpv) з потрібним Referer
- VLC більше не потрібен; автоперехід на наступну серію; збереження `.m3u` залишилось
- Історія переглядів: поличка «Продовжити перегляд» на головній і кнопка «Продовжити»
  на сторінці тайтлу — з тієї ж серії, хвилини й озвучки (позиція зберігається кожні 10 с)
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
| Android | `ubuntu-latest` (Android SDK, JDK 17) | `UAFix-Player-Android-arm64.apk` (сучасні телефони), `…-armv7.apk` (старі 32-бітні) |
| Android TV | `ubuntu-latest` | `UAFix-Player-AndroidTV-arm64.apk` / `…-armv7.apk` («UAFix TV», ставиться поруч зі звичайною; більшість TV-приставок — armv7) |

Android-збірки використовують шаблон Flet із запитом найвищої частоти екрана (90/120 Гц) —
див. [tools/android_template.py](tools/android_template.py).

Тег `v1.0.0` (`git tag v1.0.0 && git push --tags`) додатково створює GitHub Release з усіма трьома файлами.

Локально (потрібен Xcode для macOS, Visual Studio для Windows; Flutter, JDK та Android SDK
`flet build` поставить сам з `--yes`):

```bash
flet build macos --yes
flet build apk --yes
```

Збірки не підписані: на macOS перший запуск — правий клік на застосунку → «Відкрити»;
на Android — дозволити встановлення з невідомих джерел.

## Випуск нової версії та оновлення

1. Додайте зверху в [CHANGELOG.md](CHANGELOG.md) розділ `## 1.2.0` з коротким описом змін.
2. Змініть `version` у `pyproject.toml`, закомітьте.
3. `git tag v1.2.0 && git push origin main v1.2.0`

CI збере всі платформи й створить GitHub Release з файлами та описом змін. Застосунок
при запуску (не частіше ніж раз на 6 год) або з меню «Перевірити оновлення» побачить нову
версію й запропонує завантажити файл саме для цього пристрою; після встановлення один раз
покаже «Що нового».

Android-збірки підписуються постійним ключем із секретів `ANDROID_KEYSTORE_B64` /
`ANDROID_KEYSTORE_PASSWORD` — інакше Android не встановить оновлення поверх старої версії.
Ключ зберігається поза репозиторієм; **не втрачайте його**.

## Android TV

Окрема збірка `UAFix-Player-AndroidTV.apk` (CI додає модуль-прапорець `uafix_tv_flag.py`):

- з'являється в меню телевізора (Google TV / Android TV);
- розмітка «як на ПК», усе керується пультом: стрілки — переміщення, OK — вибір;
  фокус — збільшення картки й рамка;
- на сторінці серіалу всі сезони одним списком (гортати вниз);
- плеєр на весь екран: панель ⏮ · −10 с · ▶/⏸ · +10 с · ⏭ · озвучка · субтитри
  (вибір — у вікні по центру); медіа-клавіші пульта Play/Pause і перемотка;
  «Назад» на пульті — вихід із плеєра.

Перевірити TV-режим на комп'ютері: `UAFIX_TV=1 python main.py` (стрілки + Enter замість пульта).

## Відомі обмеження

- Пошук потоків для серіалів, де кожна серія — окрема сторінка, займає ~0.5 с на серію (як і в оригіналі).
- Якщо сайт увімкне Cloudflare-захист, на ПК можна додати `curl_cffi` у залежності; на Android він недоступний.
