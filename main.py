"""UAFix Player — кросплатформений клієнт (Windows / macOS / Android) на Flet."""
import locale

# У зібраному застосунку Python працює в одному процесі з плеєром (libmpv), а mpv не
# підтримує нестандартну числову локаль (напр. uk_UA з десятковою комою): створення
# плеєра падає з assertion при першому ж відео. Тому числова локаль — завжди «C».
try:
    locale.setlocale(locale.LC_NUMERIC, "C")
except locale.Error:
    pass

import asyncio
import json
import os
import re
import time
from urllib.parse import urlparse

import flet as ft
import flet_video as fv

import core
import ui
import updater
from ui import ACCENT, BG, BORDER, MUTED, SURFACE, SURFACE_2, SURFACE_3, TEXT

# Розділи бічного меню: (секція, [(назва, шлях, іконка)])
NAV = [
    ("Серіали", [
        ("Усі серіали", "/serials/", ft.Icons.LIVE_TV_ROUNDED),
        ("Нові серії", "/serials/new_uaserial/", ft.Icons.FIBER_NEW_ROUNDED),
        ("Серіали 2026", "/serials/serial_2026_online/", ft.Icons.CALENDAR_MONTH_ROUNDED),
        ("Серіали 2025", "/serials/serial_2025_online/", ft.Icons.CALENDAR_MONTH_ROUNDED),
    ]),
    ("Фільми", [
        ("Усі фільми", "/films/", ft.Icons.THEATERS_ROUNDED),
        ("Новинки", "/films/new_netflix_ua/", ft.Icons.NEW_RELEASES_ROUNDED),
        ("Фільми 2026", "/films/uafilms_2026/", ft.Icons.CALENDAR_MONTH_ROUNDED),
    ]),
    ("Анімація", [
        ("Мультфільми", "/cartoons/", ft.Icons.CHILD_CARE_ROUNDED),
        ("Мультсеріали", "/serials/multseial/", ft.Icons.ANIMATION_ROUNDED),
        ("Аніме", "/anime/", ft.Icons.AUTO_AWESOME_ROUNDED),
    ]),
    ("Інше", [
        ("Дорами", "/dorama/", ft.Icons.FAVORITE_ROUNDED),
        ("Наша озвучка", "/dubbing/", ft.Icons.MIC_ROUNDED),
    ]),
]
DESTINATIONS = [d for _, items in NAV for d in items]

# полички під банером на головній: (назва, розділ)
SHELVES = [
    ("Нові серії", "/serials/new_uaserial/"),
    ("Новинки кіно", "/films/new_netflix_ua/"),
    ("Серіали 2026", "/serials/serial_2026_online/"),
    ("Аніме", "/anime/"),
    ("Мультфільми", "/cartoons/"),
]
BANNER_INTERVAL = 7  # секунд на слайд

# Налаштування mpv для нестабільного відеосервера: за замовчуванням плеєр тримає запас
# лише на кілька секунд і при обриві з'єднання просто стоїть — звідси підвисання, фрізи
# й розсинхрон звуку з картинкою. Більший запас наперед + автоматичне перепідключення.
MPV_STREAMING = {
    "cache": "yes",
    "cache-secs": 60,                 # тримати до хвилини відео наперед
    "demuxer-readahead-secs": 60,
    "demuxer-max-bytes": "150MiB",
    "demuxer-max-back-bytes": "50MiB",  # можна трохи перемотати назад без перезавантаження
    "cache-pause-initial": "yes",     # на старті спершу накопичити трохи буфера
    "cache-pause-wait": 3,            # після підвисання грати, лише коли є ≥3 с запасу
    "network-timeout": 20,
    "stream-lavf-o": "reconnect=1,reconnect_streamed=1,reconnect_on_network_error=1,"
                     "reconnect_delay_max=5",
}

# розмір субтитрів: (назва, множник базового розміру)
SUB_SIZES = [("Малі", 0.75), ("Середні", 1.0), ("Великі", 1.3), ("Дуже великі", 1.6)]
# масштаб відео: (ключ, назва, BoxFit)
FIT_MODES = [("contain", "Вписати", ft.BoxFit.CONTAIN), ("cover", "Заповнити", ft.BoxFit.COVER),
             ("fill", "Розтягнути", ft.BoxFit.FILL)]

# TV-збірка (Android TV) додає модуль-прапорець uafix_tv_flag.py (саме модуль, а не файл:
# на Android код може бути запакований у zip, де «файл поруч» не перевірити);
# для перевірки на ПК — змінна середовища UAFIX_TV=1
try:
    import uafix_tv_flag  # noqa: F401
    TV = True
except ImportError:
    TV = os.environ.get("UAFIX_TV") == "1"
TV_CARD_W = 300

POSTER_RATIO = 16 / 9  # картинки на сайті горизонтальні (1200×675, 800×450)


def plural(n, one, few, many):
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def main(page: ft.Page):
    page.title = "UAFix Player"
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = page.dark_theme = ui.theme()
    if TV:
        page.theme.focus_color = page.dark_theme.focus_color = ft.Colors.with_opacity(0.55, ACCENT)
    page.bgcolor = BG
    is_mac = page.platform == ft.PagePlatform.MACOS
    # телевізор — Android, але з розміткою «як на ПК» і керуванням з пульта
    is_mobile = page.platform in (ft.PagePlatform.ANDROID, ft.PagePlatform.IOS) and not TV
    is_desktop = page.platform in (ft.PagePlatform.MACOS, ft.PagePlatform.WINDOWS,
                                   ft.PagePlatform.LINUX)
    if is_desktop:
        page.window.width, page.window.height = 1240, 800
        page.window.min_width, page.window.min_height = 760, 540
        page.window.bgcolor = BG
    if is_mac:
        # контент під «світлофором», як у нативних застосунках macOS
        page.window.title_bar_hidden = True
        page.window.title_bar_buttons_hidden = False
    traffic_gap = 78 if is_mac else 0
    side_pad = 18 if is_mobile else 40

    def focusable(content, on_click, on_focus_change=None, radius=14, autofocus=False):
        """Обгортка, що отримує фокус з пульта (кнопка без власного оформлення).
        Звичайні контейнери з on_click фокусу не отримують — пультом їх не вибрати."""
        return ft.Button(
            content=content, on_click=on_click, autofocus=autofocus,
            on_focus=(lambda e: on_focus_change(True)) if on_focus_change else None,
            on_blur=(lambda e: on_focus_change(False)) if on_focus_change else None,
            style=ft.ButtonStyle(
                padding=0, elevation=0, bgcolor=ft.Colors.TRANSPARENT,
                overlay_color=ft.Colors.TRANSPARENT, shadow_color=ft.Colors.TRANSPARENT,
                shape=ft.RoundedRectangleBorder(radius=radius), animation_duration=0))

    def tv_tap(ctrl, action, highlight=None, radius=12, autofocus=False):
        """На ПК повертає контейнер як є; на телевізорі — обгортає його у фокусовану кнопку
        (біла рамка, поки фокус на ній)."""
        if not TV:
            return ctrl
        ctrl.on_click = None
        base_border = ctrl.border

        def on_focus(f):
            if highlight:
                highlight(f)
            # біла рамка видна і на звичайних, і на виділених (помаранчевих) елементах
            ctrl.border = ft.Border.all(3, "#FFFFFF") if f else base_border
            ctrl.update()

        return focusable(ctrl, action, on_focus, radius=radius, autofocus=autofocus)

    def round_button(icon, on_click, tooltip=None, size=38):
        """Кругла кнопка «скляна» на ПК; на телевізорі — звичайна IconButton (з фокусом)."""
        if not TV:
            return ui.glass_button(icon, on_click, tooltip=tooltip, size=size, blur=not is_mobile)
        return ft.IconButton(icon, on_click=on_click, tooltip=tooltip, icon_color=TEXT,
                             style=ft.ButtonStyle(
                                 bgcolor={ft.ControlState.FOCUSED: ACCENT,
                                          ft.ControlState.DEFAULT: ft.Colors.with_opacity(0.45, "#000000")},
                                 shape=ft.CircleBorder()))

    prefs = ft.SharedPreferences()
    settings = {"sub_scale": 1.0, "fit": "auto", "skipped_version": ""}  # fit "auto": на телефоні в повному екрані — «Заповнити»

    # Історія переглядів: [{url, title, orig, poster, age, season, label, si, ei, voice,
    # pos, dur, ts}] — найновіше першим. Позиція (pos) і тривалість (dur) — у мілісекундах.
    history = []
    HISTORY_MAX = 40

    async def load_settings():
        try:
            for key in settings:
                value = await prefs.get(key)
                if value is not None:
                    settings[key] = value
            raw = await prefs.get("history")
            if raw:
                history[:] = json.loads(raw)
        except Exception:
            pass
        render_continue()

    # ── оновлення через GitHub Releases
    UPDATE_CHECK_EVERY = 6 * 3600  # автоматично — не частіше ніж раз на 6 годин
    platform_name = {ft.PagePlatform.ANDROID: "android", ft.PagePlatform.MACOS: "macos",
                     ft.PagePlatform.WINDOWS: "windows"}.get(page.platform)

    def dialog_width():
        return min(440, (page.width or 480) - 64)

    def notes_view(notes):
        return ft.Container(
            ft.Column([ft.Markdown(notes or "Без опису змін.", selectable=True,
                                   extension_set=ft.MarkdownExtensionSet.GITHUB_WEB)],
                      scroll=ft.ScrollMode.AUTO, tight=True),
            width=dialog_width())  # висота за вмістом; довгий опис прокручує саме вікно

    def show_update_dialog(rel):
        url = updater.asset_for(rel, platform_name, TV) or rel["url"]
        hint = ("Після завантаження відкрийте файл і натисніть «Встановити»."
                if platform_name == "android" else
                "Розпакуйте архів і замініть ним попередню версію застосунку.")

        def update(e):
            page.pop_dialog()
            page.run_task(ft.UrlLauncher().launch_url, url, mode=ft.LaunchMode.EXTERNAL_APPLICATION)
            toast(hint, ft.Icons.DOWNLOAD_ROUNDED)

        def skip(e):
            save_setting("skipped_version", rel["version"])
            page.pop_dialog()

        # кнопки — власним блоком: стандартний ряд дії на вузькому екрані розсипався стовпчиком
        page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"Доступна версія {rel['version']}", weight=ft.FontWeight.W_800),
            bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18), scrollable=True,
            content=ft.Column([
                ft.Text(f"У вас версія {updater.VERSION}. Що нового:", color=MUTED, size=13),
                notes_view(rel["notes"]),
                ft.Container(height=4),
                ft.FilledButton(
                    "Оновити", icon=ft.Icons.DOWNLOAD_ROUNDED, on_click=update, autofocus=TV,
                    width=dialog_width(),
                    style=ft.ButtonStyle(bgcolor=ACCENT, color="#FFFFFF", icon_color="#FFFFFF",
                                         padding=ft.Padding.symmetric(vertical=16),
                                         shape=ft.RoundedRectangleBorder(radius=12),
                                         text_style=ft.TextStyle(size=15, weight=ft.FontWeight.W_700))),
                ft.Row([
                    ft.TextButton("Пізніше", on_click=lambda e: page.pop_dialog(),
                                  style=ft.ButtonStyle(color=TEXT)),
                    ft.TextButton("Пропустити версію", on_click=skip,
                                  style=ft.ButtonStyle(color=MUTED)),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, width=dialog_width()),
            ], tight=True, spacing=10),
        ))

    async def show_whats_new():
        """Перший запуск нової версії — коротко, що змінилось."""
        last = await prefs.get("last_version")
        if last == updater.VERSION:
            return
        await prefs.set("last_version", updater.VERSION)
        if not last:
            return  # перше встановлення — показувати нічого
        try:
            notes = await asyncio.to_thread(updater.release_notes, updater.VERSION)
        except Exception:
            return
        page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"Що нового у версії {updater.VERSION}", weight=ft.FontWeight.W_800),
            bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18), scrollable=True,
            content=notes_view(notes),
            actions=[ft.FilledButton("Чудово", autofocus=TV, on_click=lambda e: page.pop_dialog(),
                                     style=ft.ButtonStyle(bgcolor=ACCENT, color="#FFFFFF"))],
        ))

    async def check_updates(manual=False):
        if updater.VERSION == "dev":
            if manual:
                toast("Це версія для розробки — оновлення не перевіряються")
            return
        now = time.time()
        if not manual:
            try:
                if now - float(await prefs.get("update_checked_at") or 0) < UPDATE_CHECK_EVERY:
                    return
            except Exception:
                pass
        try:
            rel = await asyncio.to_thread(updater.latest_release)
        except Exception:
            if manual:
                toast("Не вдалося перевірити оновлення", ft.Icons.ERROR_OUTLINE_ROUNDED)
            return
        await prefs.set("update_checked_at", now)
        if updater.is_newer(rel["version"]) and (
                manual or rel["version"] != settings.get("skipped_version")):
            show_update_dialog(rel)
        elif manual:
            toast(f"У вас найновіша версія {updater.VERSION}", ft.Icons.CHECK_CIRCLE_ROUNDED)

    async def check_updates_from_menu():
        await home.close_drawer()  # вікно оновлення — не поверх відкритого меню
        await check_updates(manual=True)

    async def startup():
        await load_settings()
        await show_whats_new()
        await check_updates()

    def save_history():
        page.run_task(prefs.set, "history", json.dumps(history[:HISTORY_MAX], ensure_ascii=False))

    def history_entry(url):
        return next((h for h in history if h["url"] == url), None)

    def fmt_time(ms):
        sec = int(ms // 1000)
        h, m, s_ = sec // 3600, sec % 3600 // 60, sec % 60
        return f"{h}:{m:02d}:{s_:02d}" if h else f"{m}:{s_:02d}"

    def resume_caption(h):
        parts = []
        if h.get("season"):
            parts.append(h["season"].replace("Сезон ", "С"))
        if h.get("label") and h["label"] != "Фільм":
            parts.append(h["label"].split(" — ")[0])
        parts.append(fmt_time(h["pos"]))
        return " · ".join(parts)

    def watched(h):
        return h.get("dur") and h["pos"] >= 0.95 * h["dur"]

    def record_progress(ctx, ep, voice, pos_ms, dur_ms):
        """Зберігає, на якій серії й хвилині зупинились (викликається з плеєра)."""
        si = ei = None
        for i, season in enumerate(ctx["seasons"]):
            for j, e in enumerate(season["episodes"]):
                if e is ep:
                    si, ei = i, j
        if si is None:
            return
        item, info = ctx["item"], ctx["info"] or {}
        multi = len(ctx["seasons"]) > 1
        entry = {
            "url": item["url"], "title": info.get("title") or item["title"],
            "orig": item.get("orig") or info.get("orig") or "",
            "poster": item.get("poster") or info.get("poster") or "", "age": item.get("age") or "",
            "season": ctx["seasons"][si]["name"] if multi else "", "label": ep["label"],
            "si": si, "ei": ei, "voice": voice, "pos": int(pos_ms), "dur": int(dur_ms),
            "ts": int(time.time()),
        }
        history[:] = [entry] + [h for h in history if h["url"] != item["url"]]
        save_history()

    def find_episode(ctx, h):
        """(сезон, серія) запису історії в поточному списку серій тайтлу."""
        for i, season in enumerate(ctx["seasons"]):
            if h.get("season") and season["name"] != h["season"]:
                continue
            for j, e in enumerate(season["episodes"]):
                if e["label"] == h.get("label"):
                    return i, j
        si, ei = h.get("si", 0), h.get("ei", 0)
        if si < len(ctx["seasons"]) and ei < len(ctx["seasons"][si]["episodes"]):
            return si, ei
        return None

    def save_setting(key, value):
        settings[key] = value
        page.run_task(prefs.set, key, value)

    st = {"mode": ("home", None), "page": 1, "items": [], "has_more": True,
          "loading": False, "gen": 0, "layout": "grid"}

    def toast(msg, icon=ft.Icons.INFO_OUTLINE_ROUNDED, retry=None):
        """Спливаюче повідомлення; retry — дія для кнопки «Повторити»."""
        page.show_dialog(ft.SnackBar(
            ft.Row([ft.Icon(icon, color=ACCENT, size=18),
                    ft.Text(msg, color=TEXT, expand=True)], spacing=10),
            bgcolor=SURFACE_3, behavior=ft.SnackBarBehavior.FLOATING,
            shape=ft.RoundedRectangleBorder(radius=12), width=None if is_mobile else 520,
            action=ft.SnackBarAction("Повторити", text_color=ACCENT) if retry else None,
            on_action=(lambda e: retry()) if retry else None,
            duration=8000 if retry else 4000,
        ))

    async def explain(err, what="дані"):
        """Зрозумілий текст помилки (перевірка інтернету — у фоновому потоці)."""
        return await asyncio.to_thread(core.friendly_error, err, what)

    def error_icon(msg):
        return ft.Icons.WIFI_OFF_ROUNDED if "інтернет" in msg else ft.Icons.ERROR_OUTLINE_ROUNDED

    def safe(control):
        """На телефоні — відступи від системних рядків (годинник, батарея, жести)."""
        return ft.SafeArea(control, expand=True) if is_mobile else control

    def drag_area(content):
        return content if is_mobile else ft.WindowDragArea(content, maximizable=True)

    def empty_state(icon, title, text, retry=None):
        actions = [ft.Container(height=4), ft.FilledButton(
            "Спробувати ще", icon=ft.Icons.REFRESH_ROUNDED, on_click=lambda e: retry(),
            style=ft.ButtonStyle(bgcolor=ACCENT, color="#FFFFFF", icon_color="#FFFFFF",
                                 shape=ft.RoundedRectangleBorder(radius=12)))] if retry else []
        return ft.Container(
            ft.Column([
                ft.Container(ft.Icon(icon, size=40, color=ACCENT), width=84, height=84,
                             border_radius=42, bgcolor=ft.Colors.with_opacity(0.1, ACCENT),
                             alignment=ft.Alignment.CENTER),
                ft.Text(title, size=18, weight=ft.FontWeight.W_700, color=TEXT),
                ft.Text(text, size=13, color=MUTED, text_align=ft.TextAlign.CENTER),
                *actions,
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10, tight=True),
            alignment=ft.Alignment.CENTER, expand=True, padding=30,
        )

    # ═════════════════════ бічне меню ═════════════════════
    drawer_controls = [
        ft.Container(
            ft.Row([
                # іконка застосунку (assets/icon.png), як на робочому столі
                ft.Image(src="/icon.png", width=44, height=44, border_radius=10),
                ft.Column([ft.Text("UAFix", size=18, weight=ft.FontWeight.W_800, color=TEXT),
                           ft.Text("Фільми та серіали українською", size=11, color=MUTED)],
                          spacing=0),
            ], spacing=12),
            padding=ft.Padding.only(left=24, right=16, top=40 if is_mac else 20, bottom=12),
        ),
    ]
    drawer_controls.append(ft.NavigationDrawerDestination(
        label="Головна", icon=ft.Icon(ft.Icons.HOME_ROUNDED, color=MUTED),
        selected_icon=ft.Icon(ft.Icons.HOME_ROUNDED, color=ACCENT)))
    for section, items in NAV:
        drawer_controls.append(ft.Container(
            ft.Text(section.upper(), size=11, color=MUTED, weight=ft.FontWeight.W_700),
            padding=ft.Padding.only(left=28, top=14, bottom=6)))
        for name, _, icon in items:
            drawer_controls.append(ft.NavigationDrawerDestination(
                label=name, icon=ft.Icon(icon, color=MUTED), selected_icon=ft.Icon(icon, color=ACCENT)))

    drawer_controls += [
        ft.Container(ft.Divider(color=BORDER), padding=ft.Padding.only(left=20, right=20, top=10)),
        ft.Container(ft.Column([
            ft.Text(f"Версія {updater.VERSION}", size=12, color=MUTED),
            ft.TextButton("Перевірити оновлення", icon=ft.Icons.SYSTEM_UPDATE_ROUNDED,
                          style=ft.ButtonStyle(color=ACCENT, icon_color=ACCENT, padding=0),
                          on_click=lambda e: page.run_task(check_updates_from_menu)),
        ], spacing=4, tight=True), padding=ft.Padding.only(left=28, right=20, bottom=24)),
    ]

    drawer = ft.NavigationDrawer(
        controls=drawer_controls, selected_index=0, bgcolor=SURFACE,
        indicator_color=ft.Colors.with_opacity(0.14, ACCENT),
        indicator_shape=ft.RoundedRectangleBorder(radius=12),
        on_change=lambda e: page.run_task(on_nav, e.control.selected_index),
    )

    async def on_nav(index):
        await home.close_drawer()
        if index == 0:
            go_home()
            return
        await open_category(*DESTINATIONS[index - 1][:2])

    async def open_category(name, path):
        search.value = ""
        clear_btn.visible = False
        set_heading(name)
        st["mode"] = ("cat", path)
        idx = next((i for i, d in enumerate(DESTINATIONS) if d[1] == path), None)
        drawer.selected_index = idx + 1 if idx is not None else None
        await reload()

    # ═════════════════════ верхня панель ═════════════════════
    subheading = ft.Text("", size=12, color=MUTED, visible=False)

    def set_subheading(text):
        subheading.value = text
        subheading.visible = bool(text)

    # старий заголовок зникає миттєво (reverse_duration=0), щоб тексти не накладались
    heading_switcher = ft.AnimatedSwitcher(
        ft.Text(""), duration=180, reverse_duration=0, transition=ft.AnimatedSwitcherTransition.FADE,
        switch_in_curve=ui.EASE, switch_out_curve=ui.EASE)

    def set_heading(text):
        heading_switcher.content = ft.Text(text, size=22, weight=ft.FontWeight.W_800, color=TEXT,
                                           key=text, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)

    set_heading("Головна")

    def clear_search(e=None):
        search.value = ""
        clear_btn.visible = False
        go_home()

    clear_btn = ft.IconButton(ft.Icons.CLOSE_ROUNDED, icon_size=18, icon_color=MUTED,
                              tooltip="Очистити пошук", visible=False, on_click=clear_search,
                              style=ft.ButtonStyle(padding=0), width=32, height=32)

    def on_search_change(e):
        visible = bool(search.value)
        if visible != clear_btn.visible:
            clear_btn.visible = visible
            clear_btn.update()

    search = ft.TextField(
        hint_text="Пошук фільмів і серіалів", height=40, text_size=14, dense=True,
        filled=True, fill_color=SURFACE_2,
        border={
            ft.ControlState.FOCUSED: ft.OutlineInputBorder(
                border_radius=12, side=ft.BorderSide(1, ft.Colors.with_opacity(0.6, ACCENT))),
            ft.ControlState.DEFAULT: ft.OutlineInputBorder(
                border_radius=12, side=ft.BorderSide(1, BORDER)),
        },
        prefix_icon=ft.Icons.SEARCH_ROUNDED, cursor_color=ACCENT,
        hint_style=ft.TextStyle(color=MUTED, size=14),
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=0),
        text_vertical_align=ft.VerticalAlignment.CENTER,
        # іконки не повинні розтягувати поле (на Windows воно ставало дуже високим)
        prefix_icon_size_constraints=ft.BoxConstraints(min_width=40, max_width=40,
                                                       min_height=40, max_height=40),
        suffix_icon_size_constraints=ft.BoxConstraints(min_width=36, max_width=36,
                                                       min_height=40, max_height=40),
        size_constraints=ft.BoxConstraints(min_height=40, max_height=40),
        width=None if is_mobile else 320, expand=is_mobile,
        suffix_icon=clear_btn, on_change=on_search_change,
        on_submit=lambda e: page.run_task(do_search, e.control.value),
    )

    layout_toggle = ft.SegmentedButton(visible=not TV,
        selected=["grid"], show_selected_icon=False,
        segments=[ft.Segment("grid", icon=ft.Icon(ft.Icons.GRID_VIEW_ROUNDED, size=18),
                             tooltip="Сітка"),
                  ft.Segment("list", icon=ft.Icon(ft.Icons.VIEW_AGENDA_ROUNDED, size=18),
                             tooltip="Список")],
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=10),
            side=ft.BorderSide(1, BORDER),
            bgcolor={ft.ControlState.SELECTED: ft.Colors.with_opacity(0.16, ACCENT),
                     ft.ControlState.DEFAULT: SURFACE_2},
            icon_color={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: MUTED},
            padding=ft.Padding.symmetric(horizontal=6),
        ),
        on_change=lambda e: switch_layout(e.control.selected[0]),
    )

    menu_btn = ft.IconButton(ft.Icons.MENU_ROUNDED, icon_color=TEXT, tooltip="Розділи",
                             on_click=lambda e: page.run_task(home.show_drawer))

    title_block = ft.Column([heading_switcher, subheading], spacing=0, expand=True,
                            alignment=ft.MainAxisAlignment.CENTER)
    if is_mobile:
        top_bar = ft.Column([ft.Row([menu_btn, title_block, layout_toggle], spacing=4,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER),
                             ft.Row([search])], spacing=8)
    else:
        top_bar = ft.Row([menu_btn, title_block, search, layout_toggle], spacing=12,
                         vertical_alignment=ft.CrossAxisAlignment.CENTER)
    top_bar_box = drag_area(ft.Container(
        top_bar,
        padding=ft.Padding.only(left=12 + traffic_gap, right=20, top=10, bottom=10),
        bgcolor=BG if is_mobile else ft.Colors.with_opacity(0.85, BG),
        blur=None if is_mobile else ft.Blur(20, 20),
        border=ft.Border.only(bottom=ft.BorderSide(1, BORDER)),
    ))

    # ═════════════════════ картки ═════════════════════
    def grid_card(item, progress=None, caption=None, on_open=None):
        play = ft.Container(
            ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, color="#FFFFFF", size=30),
            width=56, height=56, border_radius=28, gradient=ui.ACCENT_GRADIENT,
            alignment=ft.Alignment.CENTER, opacity=0, scale=0.7,
            animate_opacity=ui.FAST, animate_scale=ui.MED,
            shadow=ft.BoxShadow(blur_radius=24, color=ft.Colors.with_opacity(0.5, ACCENT)),
        )
        layers = [
            ui.poster_image(item["poster"], cache_width=760, left=0, top=0, right=0, bottom=0),
            ft.Container(  # затемнення знизу під назвою
                left=0, right=0, bottom=0, height=110,
                gradient=ft.LinearGradient(
                    begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER,
                    colors=["#00000000", "#CC000000", "#F2000000"]),
            ),
            ft.Container(
                ft.Column([
                    ft.Text(item["title"], size=15, weight=ft.FontWeight.W_700, color="#FFFFFF",
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(caption or item["orig"] or " ", size=11,
                            color=ui.ACCENT_2 if caption else "#C8CCD4", max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=2, tight=True),
                left=12, right=12, bottom=16 if progress is not None else 12,
            ),
            ft.Container(play, left=0, right=0, top=0, bottom=0, alignment=ft.Alignment.CENTER),
        ]
        if progress is not None:  # смужка «скільки переглянуто»
            layers.append(ft.Container(
                ft.ProgressBar(value=max(0.02, min(progress, 1)), color=ACCENT,
                               bgcolor="#55FFFFFF", bar_height=4, border_radius=2),
                left=12, right=12, bottom=8))
        if item["age"]:
            layers.append(ft.Container(
                ft.Text(item["age"], size=10, weight=ft.FontWeight.W_700, color="#FFFFFF"),
                left=10, top=10, padding=ft.Padding.symmetric(horizontal=7, vertical=3),
                border_radius=6, bgcolor=ft.Colors.with_opacity(0.55, "#000000"),
                border=ft.Border.all(1, "#22FFFFFF"),
            ))
        card = ft.Container(
            ft.Stack(layers, expand=True),
            border_radius=14, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            border=ft.Border.all(1, BORDER),
            shadow=ft.BoxShadow(blur_radius=18, offset=ft.Offset(0, 8),
                                color=ft.Colors.with_opacity(0.35, "#000000")),
            scale=1, animate_scale=ui.MED, animate=ui.MED,
            on_click=lambda e: on_open() if on_open else page.run_task(open_title, item),
        )

        def highlight(h):
            card.scale = 1.06 if (h and TV) else 1.045 if h else 1
            card.border = ft.Border.all(1, ft.Colors.with_opacity(0.55, ACCENT) if h else BORDER)
            card.shadow = ft.BoxShadow(
                blur_radius=34 if h else 18, offset=ft.Offset(0, 14 if h else 8),
                color=ft.Colors.with_opacity(0.35, ACCENT if h else "#000000"))
            if TV:
                card.border = ft.Border.all(3 if h else 1, ACCENT if h else BORDER)
            play.opacity, play.scale = (1, 1) if h else (0, 0.7)
            card.update()

        if TV:
            card.on_click = None
            card.width, card.height = TV_CARD_W, TV_CARD_W / POSTER_RATIO
            return ui.appear(focusable(
                card, lambda e: on_open() if on_open else page.run_task(open_title, item), highlight))
        card.on_hover = lambda e: highlight(ui.hovered(e))
        return ui.appear(card)

    def list_card(item):
        lines = [ft.Text(item["title"], size=16, weight=ft.FontWeight.W_700, color=TEXT,
                         max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)]
        if item["orig"]:
            lines.append(ft.Text(item["orig"], size=13, color=MUTED, max_lines=1,
                                 overflow=ft.TextOverflow.ELLIPSIS))
        if item["age"]:
            lines.append(ft.Row([ui.pill(item["age"])]))
        arrow = ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, color=MUTED, offset=ft.Offset(0, 0),
                        animate_offset=ui.FAST)
        row = ft.Container(
            ft.Row([
                ui.poster_image(item["poster"], radius=10, width=176, height=99, cache_width=360),
                ft.Column(lines, spacing=4, expand=True, alignment=ft.MainAxisAlignment.CENTER),
                arrow,
            ], spacing=16),
            padding=10, border_radius=14, bgcolor=SURFACE, border=ft.Border.all(1, BORDER),
            animate=ui.FAST,
            on_click=lambda e: page.run_task(open_title, item),
        )

        def hover(e):
            h = ui.hovered(e)
            row.bgcolor = SURFACE_2 if h else SURFACE
            row.border = ft.Border.all(1, ft.Colors.with_opacity(0.4, ACCENT) if h else BORDER)
            arrow.offset = ft.Offset(0.3 if h else 0, 0)
            arrow.color = ACCENT if h else MUTED
            row.update()

        row.on_hover = hover
        return ui.appear(row)

    # ═════════════════════ каталог ═════════════════════
    def on_scroll(e: ft.OnScrollEvent):
        if e.pixels >= e.max_scroll_extent - 700:
            page.run_task(load_more)

    pad = ft.Padding.only(left=14, right=14, top=14, bottom=30) if is_mobile else \
        ft.Padding.only(left=24, right=24, top=20, bottom=40)
    grid = ft.GridView(
        expand=True, max_extent=420 if is_mobile else 360, child_aspect_ratio=POSTER_RATIO,
        spacing=12 if is_mobile else 20, run_spacing=14 if is_mobile else 22, padding=pad,
        on_scroll=on_scroll, scroll_interval=80,
    )
    listing = ft.ListView(expand=True, spacing=10, padding=pad, on_scroll=on_scroll,
                          scroll_interval=80)
    if TV:
        # кнопка-обгортка не розтягує вміст на всю клітинку сітки, тому на телевізорі —
        # картки фіксованого розміру в рядку з переносом, у прокручуваному списку
        grid = ft.Row(wrap=True, spacing=24, run_spacing=24)
        tv_grid_scroller = ft.ListView([grid], expand=True, padding=pad, on_scroll=on_scroll,
                                       scroll_interval=80)

    def skeleton():
        if st["layout"] == "list":
            tiles = [ft.Container(height=121, bgcolor=SURFACE_2, border_radius=14) for _ in range(8)]
            inner = ft.ListView(tiles, expand=True, spacing=10, padding=pad)
        else:
            tiles = [ft.Container(bgcolor=SURFACE_2, border_radius=14) for _ in range(12)]
            inner = ft.GridView(tiles, expand=True, max_extent=TV_CARD_W if TV else 420 if is_mobile else 360,
                                child_aspect_ratio=POSTER_RATIO, spacing=20,
                                run_spacing=22, padding=pad)
        return ft.Shimmer(inner, base_color=SURFACE_2, highlight_color=SURFACE_3, expand=True,
                          key=f"skeleton{st['gen']}")

    # Плаваючий індикатор дозавантаження. Лише змінює прозорість: якщо ховати/показувати
    # його через visible поруч із сіткою, Flutter перебудовує сітку і скидає прокрутку.
    more_loader = ft.Container(
        ft.Container(
            ft.Row([ft.ProgressRing(width=16, height=16, stroke_width=2, color=ACCENT),
                    ft.Text("Завантажую ще…", size=12, color=TEXT)], spacing=10, tight=True),
            padding=ft.Padding.symmetric(horizontal=16, vertical=10), border_radius=20,
            bgcolor=ft.Colors.with_opacity(0.9, SURFACE_3), border=ft.Border.all(1, BORDER),
            shadow=ft.BoxShadow(blur_radius=20, color=ft.Colors.with_opacity(0.4, "#000000")),
        ),
        left=0, right=0, bottom=18, alignment=ft.Alignment.CENTER,
        opacity=0, animate_opacity=ui.MED, offset=ft.Offset(0, 0.3), animate_offset=ui.MED,
        ignore_interactions=True,
    )

    def set_more_loading(on):
        more_loader.opacity = 1 if on else 0
        more_loader.offset = ft.Offset(0, 0 if on else 0.3)

    # reverse_duration — як швидко зникає старий вміст: коротко, щоб старі картки
    # не просвічували під новими під час перемикання сітка/список
    body = ft.AnimatedSwitcher(
        ft.Container(), duration=180, reverse_duration=0,
        transition=ft.AnimatedSwitcherTransition.FADE,
        switch_in_curve=ui.EASE, switch_out_curve=ui.EASE, expand=True)

    def current_view():
        return grid if st["layout"] == "grid" else listing

    def show_items():
        view = tv_grid_scroller if (TV and st["layout"] == "grid") else current_view()
        body.content = ft.Stack([view, more_loader], expand=True,
                                key=f"{st['layout']}{st['gen']}")

    async def reveal_cards(cards):
        """Каскадна поява карток."""
        for i, c in enumerate(cards):
            ui.reveal(c)
            if i % 4 == 3 or i == len(cards) - 1:
                try:
                    current_view().update()
                except Exception:
                    return  # вигляд уже замінено
                await asyncio.sleep(0.025)

    def render(items, append=False, animate=True):
        make = grid_card if st["layout"] == "grid" else list_card
        cards = [make(it) for it in items]
        if not animate:
            for c in cards:
                ui.reveal(c)
        view = current_view()
        if append:
            view.controls.extend(cards)
        else:
            view.controls = cards
        return cards

    def update_subheading():
        n = len(st["items"])
        set_subheading(f"{n} {plural(n, 'тайтл', 'тайтли', 'тайтлів')}" if n else "")

    async def fetch_page(page_no):
        kind, val = st["mode"]
        url = core.catalog_url(val, page_no) if kind == "cat" else core.search_url(val, page_no)
        return await asyncio.to_thread(lambda: core.parse_catalog(core.fetch(url)))

    async def reload():
        if st["mode"][0] == "home":
            go_home()
            return
        layout_toggle.visible = not TV
        st["gen"] += 1
        gen = st["gen"]
        st.update(page=1, items=[], has_more=True, loading=True)
        set_subheading("Завантаження…")
        body.content = skeleton()
        page.update()
        try:
            items = await fetch_page(1)
        except Exception as ex:
            msg = await explain(ex, "список")
            if gen == st["gen"]:
                st["loading"] = False
                offline = "інтернет" in msg
                body.content = empty_state(
                    error_icon(msg), "Немає з'єднання" if offline else "Не вдалося завантажити",
                    msg, retry=lambda: page.run_task(reload))
                set_subheading("")
                page.update()
            return
        if gen != st["gen"]:
            return
        st.update(items=items, loading=False, has_more=bool(items))
        update_subheading()
        if not items:
            body.content = empty_state(ft.Icons.SEARCH_OFF_ROUNDED, "Нічого не знайдено",
                                       "Спробуйте інший запит або розділ")
            page.update()
            return
        cards = render(items)
        show_items()
        page.update()
        await reveal_cards(cards)
        await load_more()  # сторінка сайту — лише ~12 карток, одразу підвантажуємо наступну

    async def load_more():
        if st["loading"] or not st["has_more"] or not st["items"]:
            return
        st["loading"] = True
        gen = st["gen"]
        set_more_loading(True)
        more_loader.update()
        try:
            items = await fetch_page(st["page"] + 1)
        except Exception:
            items = None  # без повідомлення: наступна прокрутка спробує ще раз
        if gen != st["gen"]:
            return
        st["loading"] = False
        set_more_loading(False)
        if items is None:
            more_loader.update()
            return
        known = {it["url"] for it in st["items"]}
        fresh = [it for it in items if it["url"] not in known]
        st["page"] += 1
        st["has_more"] = bool(fresh)
        st["items"].extend(fresh)
        update_subheading()
        render(fresh, append=True, animate=False)  # нові картки з'являються нижче екрана
        current_view().update()
        more_loader.update()
        subheading.update()

    def switch_layout(layout):
        if layout == st["layout"]:
            return
        st["layout"] = layout
        if not st["items"]:
            return
        cards = render(st["items"])
        for c in cards[24:]:  # анімуємо лише перший екран
            ui.reveal(c)
        show_items()
        page.update()
        page.run_task(reveal_cards, cards[:24])

    async def do_search(q):
        q = (q or "").strip()
        if not q:
            return
        st["mode"] = ("search", q)
        drawer.selected_index = None
        layout_toggle.visible = not TV
        set_heading(f"Пошук: «{q}»")
        await reload()

    # ═════════════════════ головна (в стилі Apple TV) ═════════════════════
    hero = {"items": [], "i": 0, "hover": False, "loaded": False, "timer": 0}
    side = 20 if is_mobile else 56

    def banner_height():
        h = page.height or 800
        return max(360, int((h - 64) * (0.62 if is_mobile else 0.9)))

    def kind_of(url):
        path = urlparse(url).path
        for key, label in (("/anime/", "Аніме"), ("/cartoons/", "Мультфільм"),
                           ("/dorama/", "Дорама"), ("/serials/", "Серіал"), ("/film", "Фільм")):
            if path.startswith(key):
                return label
        return ""

    # два шари на всю площу банера: новий слайд проявляється поверх старого (crossfade)
    fade = ft.Animation(900, ft.AnimationCurve.EASE_IN_OUT)
    layers = [ft.Container(left=0, top=0, right=0, bottom=0, opacity=o, animate_opacity=fade,
                           clip_behavior=ft.ClipBehavior.HARD_EDGE) for o in (1, 0)]
    layers[0].content = ft.Shimmer(ft.Container(bgcolor=SURFACE_2, expand=True),
                                   base_color=SURFACE_2, highlight_color=SURFACE_3, expand=True)
    banner_text = ft.AnimatedSwitcher(
        ft.Container(), duration=500, reverse_duration=150,
        transition=ft.AnimatedSwitcherTransition.FADE,
        switch_in_curve=ui.EASE, switch_out_curve=ui.EASE)
    dots = ft.Row([], spacing=8)
    arrows = []

    def slide_image(item):
        # «Ken Burns»: кадр повільно наближається, поки слайд на екрані
        return ft.Container(
            ft.Image(src=item["poster"], fit=ft.BoxFit.COVER, gapless_playback=True,
                     width=float("inf"), height=float("inf")),
            expand=True, scale=1.0,
            animate_scale=ft.Animation(BANNER_INTERVAL * 1000 + 1500, ft.AnimationCurve.LINEAR),
            key=f"img{item['url']}")

    def banner_focus(e):
        hero["focus"] = True
        hero["autofocused"] = True

    def banner_blur(e):
        hero["focus"] = False

    def slide_text(item):
        info = item.get("info") or {}
        dark = ft.Colors.with_opacity(0.45, "#000000")
        meta = []
        if info.get("imdb"):
            meta.append(ui.pill(f"IMDb {info['imdb']}", ft.Icons.STAR_ROUNDED, color=TEXT,
                                icon_color=ui.GOLD, bold=True, bg=dark))
        for v in (info.get("year"), info.get("duration"), info.get("age") or item.get("age")):
            if v:
                meta.append(ui.pill(v, bg=dark, color="#E6E8EC"))
        genres = " · ".join(g.capitalize() for g in (info.get("genres") or [])[:3])
        kind = kind_of(item["url"])
        orig = item["orig"] or info.get("orig") or ""
        focus_side = {ft.ControlState.FOCUSED: ft.BorderSide(3, ACCENT),
                      ft.ControlState.DEFAULT: ft.BorderSide(0, ft.Colors.TRANSPARENT)}
        primary = ft.ButtonStyle(
            side=focus_side,
            bgcolor={ft.ControlState.HOVERED: "#FFFFFF", ft.ControlState.DEFAULT: "#ECECEC"},
            color="#0D0E12", icon_color="#0D0E12", shape=ft.RoundedRectangleBorder(radius=12),
            padding=ft.Padding.symmetric(horizontal=26, vertical=18),
            text_style=ft.TextStyle(size=15, weight=ft.FontWeight.W_700))
        secondary = ft.ButtonStyle(
            side=focus_side,
            bgcolor={ft.ControlState.HOVERED: ft.Colors.with_opacity(0.35, "#FFFFFF"),
                     ft.ControlState.DEFAULT: ft.Colors.with_opacity(0.2, "#FFFFFF")},
            color="#FFFFFF", icon_color="#FFFFFF", shape=ft.RoundedRectangleBorder(radius=12),
            padding=ft.Padding.symmetric(horizontal=22, vertical=18),
            text_style=ft.TextStyle(size=15, weight=ft.FontWeight.W_600))
        return ft.Column([
            ft.Text(("Новинка · " + kind if kind else "Новинка").upper(), size=12,
                    weight=ft.FontWeight.W_800, color=ACCENT),
            ft.Text(info.get("title") or item["title"], size=30 if is_mobile else 54,
                    weight=ft.FontWeight.W_800, color="#FFFFFF", max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS),
            ft.Text(orig, size=16, color="#C9CDD4", visible=bool(orig)),
            ft.Row(meta + ([ft.Text(genres, size=13, color="#C9CDD4")] if genres else []),
                   spacing=8, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text(info.get("description") or "", size=15, color="#D8DBE0", max_lines=3,
                    overflow=ft.TextOverflow.ELLIPSIS, visible=not is_mobile),
            ft.Container(height=6),
            ft.Row([
                ft.FilledButton("Дивитись", icon=ft.Icons.PLAY_ARROW_ROUNDED, style=primary,
                                autofocus=TV and not hero.get("autofocused"),
                                on_focus=banner_focus, on_blur=banner_blur,
                                on_click=lambda e: page.run_task(open_title, item, True)),
                ft.FilledButton("Детальніше", icon=ft.Icons.INFO_OUTLINE_ROUNDED, style=secondary,
                                on_focus=banner_focus, on_blur=banner_blur,
                                on_click=lambda e: page.run_task(open_title, item)),
            ], spacing=12, wrap=True),
        ], spacing=8, tight=True, key=f"txt{item['url']}")

    def render_dots():
        dots.controls = [
            ft.Container(width=28 if i == hero["i"] else 8, height=8, border_radius=4,
                         bgcolor="#FFFFFF" if i == hero["i"]
                         else ft.Colors.with_opacity(0.4, "#FFFFFF"),
                         animate=ui.MED, on_click=lambda e, i=i: page.run_task(show_slide, i))
            for i in range(len(hero["items"]))]

    async def show_slide(i, user=True):
        items = hero["items"]
        if not items:
            return
        hero["i"] = i % len(items)
        if user:  # ручне перемикання — відлік до наступного слайда заново
            hero["timer"] += 1
            page.run_task(autoplay, hero["timer"])
        item = items[hero["i"]]
        img = slide_image(item)
        front, back = (layers if layers[0].opacity == 1 else layers[::-1])
        back.content = img
        back.opacity, front.opacity = 1, 0
        banner_text.content = slide_text(item)
        render_dots()
        page.update()
        await asyncio.sleep(0.05)
        img.scale = 1.08
        try:
            img.update()
        except Exception:
            pass

    async def autoplay(token):
        while hero["timer"] == token:
            await asyncio.sleep(BANNER_INTERVAL)
            if hero["timer"] != token:
                return
            # банер займає майже все вікно, тож на паузу при наведенні не ставимо —
            # стрілки й крапки дозволяють перемкнути вручну
            on_home = st["mode"][0] == "home" and len(page.views) == 1
            # на телевізорі не гортаємо, поки фокус на кнопках банера: інакше кнопка,
            # на якій стоїть фокус, зникне разом зі слайдом
            if on_home and not (TV and hero.get("focus")):
                await show_slide(hero["i"] + 1, user=False)

    def banner_hover(e):
        if TV:
            return
        hero["hover"] = ui.hovered(e)
        for a in arrows:
            a.opacity = 1 if hero["hover"] else 0
        banner.update()

    def arrow(icon, delta, at):
        btn = round_button(icon, lambda e: page.run_task(show_slide, hero["i"] + delta), size=46)
        holder = ft.Container(btn, top=0, bottom=0, alignment=ft.Alignment.CENTER,
                              opacity=1 if TV else 0, animate_opacity=ui.MED, **{at: 16})
        arrows.append(holder)
        return holder

    banner = ft.Container(
        ft.Stack([
            *layers,
            ft.Container(left=0, top=0, right=0, bottom=0, gradient=ft.LinearGradient(
                begin=ft.Alignment.CENTER_LEFT, end=ft.Alignment.CENTER_RIGHT,
                colors=[ft.Colors.with_opacity(0.92, BG), ft.Colors.with_opacity(0.55, BG),
                        "#00000000"], stops=[0, 0.38, 0.75])),
            ft.Container(left=0, top=0, right=0, bottom=0, gradient=ft.LinearGradient(
                begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER,
                colors=["#00000000", "#00000000", BG], stops=[0, 0.6, 1])),
            ft.Container(banner_text, left=side, bottom=86,
                         width=None if is_mobile else 640, right=side if is_mobile else None),
            ft.Container(dots, left=side, bottom=40),
            arrow(ft.Icons.CHEVRON_LEFT_ROUNDED, -1, "left"),
            arrow(ft.Icons.CHEVRON_RIGHT_ROUNDED, 1, "right"),
        ], expand=True),
        height=banner_height(), clip_behavior=ft.ClipBehavior.HARD_EDGE,
        on_hover=banner_hover,
    )

    # ── полички під банером
    shelf_card_w = 220 if is_mobile else TV_CARD_W

    def shelf(name, path):
        row = ft.ListView(
            horizontal=True, spacing=16, height=shelf_card_w / POSTER_RATIO + 28,
            padding=ft.Padding.symmetric(horizontal=side, vertical=14),
            controls=[ft.Shimmer(ft.Container(width=shelf_card_w, bgcolor=SURFACE_2, border_radius=14),
                                 base_color=SURFACE_2, highlight_color=SURFACE_3)
                      for _ in range(6)])

        async def load():
            try:
                items = await asyncio.to_thread(
                    lambda: core.parse_catalog(core.fetch(core.catalog_url(path, 1))))
            except Exception:
                items = []
            cards = []
            for it in items:
                c = grid_card(it)
                ui.reveal(c)
                cards.append(ft.Container(c, width=shelf_card_w))
            row.controls = cards or [ft.Text("Не вдалося завантажити", color=MUTED)]
            try:
                row.update()
            except Exception:
                pass

        header = ft.Row([
            ft.Text(name, size=20 if is_mobile else 22, weight=ft.FontWeight.W_800, color=TEXT),
            ft.Container(expand=True),
            ft.TextButton("Усі", icon=ft.Icons.CHEVRON_RIGHT_ROUNDED,
                          style=ft.ButtonStyle(color=MUTED, icon_color=MUTED),
                          on_click=lambda e: page.run_task(open_category, name, path)),
        ])
        column = ft.Column([ft.Container(header, padding=ft.Padding.symmetric(horizontal=side)), row],
                           spacing=0)
        return column, load

    shelves = [shelf(n, pth) for n, pth in SHELVES]

    continue_row = ft.ListView(horizontal=True, spacing=16, height=shelf_card_w / POSTER_RATIO + 28,
                               padding=ft.Padding.symmetric(horizontal=side, vertical=14))

    def clear_history(e=None):
        history.clear()
        save_history()
        render_continue()
        toast("Історію переглядів очищено", ft.Icons.CHECK_CIRCLE_ROUNDED)

    continue_section = ft.Column([
        ft.Container(ft.Row([
            ft.Text("Продовжити перегляд", size=20 if is_mobile else 22,
                    weight=ft.FontWeight.W_800, color=TEXT),
            ft.Container(expand=True),
            ft.TextButton("Очистити", icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                          style=ft.ButtonStyle(color=MUTED, icon_color=MUTED), on_click=clear_history),
        ]), padding=ft.Padding.symmetric(horizontal=side)),
        continue_row,
    ], spacing=0, visible=False)

    def render_continue():
        items = [h for h in history if not watched(h)][:15]
        continue_section.visible = bool(items)
        cards = []
        for h in items:
            item = {k: h.get(k, "") for k in ("url", "title", "orig", "poster", "age")}
            c = grid_card(item, progress=h["pos"] / h["dur"] if h.get("dur") else 0,
                          caption=resume_caption(h),
                          on_open=lambda item=item: page.run_task(open_title, item))
            ui.reveal(c)
            cards.append(ft.Container(c, width=shelf_card_w))
        continue_row.controls = cards
        try:
            continue_section.update()
        except Exception:
            pass  # головна ще не на екрані

    home_content = ft.ListView(
        [banner, continue_section, *[col for col, _ in shelves], ft.Container(height=40)],
        expand=True, spacing=18, padding=0, key="home")

    async def load_home():
        hero["loaded"] = True
        for _, load in shelves:
            page.run_task(load)
        try:
            items = await asyncio.to_thread(core.featured)
        except Exception as ex:
            hero["loaded"] = False
            msg = await explain(ex, "головну")
            layers[0].content, layers[0].opacity, layers[1].opacity = ft.Container(bgcolor=SURFACE), 1, 0
            banner_text.content = ft.Column([
                ft.Text(msg, size=16, color=TEXT),
                ft.FilledButton("Спробувати ще", icon=ft.Icons.REFRESH_ROUNDED,
                                on_click=lambda e: page.run_task(load_home))], tight=True)
            page.update()
            return
        hero["items"] = [it for it in items if it.get("poster")]
        await show_slide(0)

    def go_home():
        st["mode"] = ("home", None)
        st["gen"] += 1
        drawer.selected_index = 0
        set_heading("Головна")
        set_subheading("")
        layout_toggle.visible = False
        body.content = home_content
        page.update()
        if not hero["loaded"]:
            page.run_task(load_home)

    def on_resize(e):
        if st["mode"][0] == "home":
            banner.height = banner_height()
            banner.update()

    page.on_resize = on_resize

    home = ft.View(
        route="/", padding=0, bgcolor=BG, drawer=drawer,
        controls=[safe(ft.Column([top_bar_box, body], expand=True, spacing=0))],
    )

    # ═════════════════════ сторінка тайтлу ═════════════════════
    async def open_title(item, autoplay=False, resume=False):
        ctx = {"item": item, "info": None, "seasons": [], "season": 0}

        def backdrop_image(src):
            # «Розмиття» без BackdropFilter: картинка декодується крихітною (36 px) і плавно
            # розтягується. BackdropFilter на Android збивався при прокрутці (то розмито,
            # то ні) і відбирав кадри — цей спосіб стабільний і майже нічого не коштує.
            if not src:
                return None
            return ft.Image(src=src, fit=ft.BoxFit.COVER, opacity=0.45, cache_width=36,
                            filter_quality=ft.FilterQuality.HIGH, gapless_playback=True)

        backdrop = ft.Container(backdrop_image(item["poster"]), left=0, top=0, right=0, height=640,
                                clip_behavior=ft.ClipBehavior.HARD_EDGE)
        backdrop_fade = ft.Container(
            left=0, top=0, right=0, height=640,
            gradient=ft.LinearGradient(
                begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER,
                colors=[ft.Colors.with_opacity(0.3, BG), ft.Colors.with_opacity(0.75, BG), BG],
                stops=[0, 0.55, 1]),
        )

        poster = ft.Container(
            ui.poster_image(item["poster"], radius=18, aspect_ratio=POSTER_RATIO),
            border_radius=18,
            shadow=ft.BoxShadow(blur_radius=50, offset=ft.Offset(0, 24),
                                color=ft.Colors.with_opacity(0.6, "#000000")),
            scale=0.92, animate_scale=ui.SLOW,
        )
        title = ft.Text(item["title"], size=26 if is_mobile else 36, weight=ft.FontWeight.W_800,
                        color=TEXT)
        orig = ft.Text(item["orig"], size=16, color=MUTED, visible=bool(item["orig"]))
        meta_row = ft.Row([ui.pill(item["age"])] if item["age"] else [], spacing=8, wrap=True)
        genres_row = ft.Row([], spacing=8, wrap=True)
        description = ft.Text("", size=14, color="#C9CDD4", max_lines=4,
                              overflow=ft.TextOverflow.ELLIPSIS, selectable=True)
        more_btn = ft.TextButton("Детальніше", visible=False,
                                 style=ft.ButtonStyle(color=ACCENT, padding=0))

        def toggle_desc(e):
            expanded = description.max_lines is None
            description.max_lines = 4 if expanded else None
            more_btn.content = "Детальніше" if expanded else "Згорнути"
            page.update()

        more_btn.on_click = toggle_desc
        cast = ft.Text("", size=12, color=MUTED, visible=False)

        play_btn = ft.FilledButton(
            "Дивитись", icon=ft.Icons.PLAY_ARROW_ROUNDED, disabled=True, autofocus=TV,
            style=ft.ButtonStyle(
                bgcolor={ft.ControlState.DISABLED: SURFACE_3, ft.ControlState.DEFAULT: ACCENT},
                color={ft.ControlState.DISABLED: MUTED, ft.ControlState.DEFAULT: "#FFFFFF"},
                icon_color={ft.ControlState.DISABLED: MUTED, ft.ControlState.DEFAULT: "#FFFFFF"},
                shape=ft.RoundedRectangleBorder(radius=12),
                padding=ft.Padding.symmetric(horizontal=26, vertical=18),
                text_style=ft.TextStyle(size=15, weight=ft.FontWeight.W_700),
                elevation={ft.ControlState.HOVERED: 10, ft.ControlState.DEFAULT: 0},
                shadow_color=ACCENT,
                side={ft.ControlState.FOCUSED: ft.BorderSide(3, "#FFFFFF"),
                      ft.ControlState.DEFAULT: ft.BorderSide(0, ft.Colors.TRANSPARENT)},
            ),
            on_click=lambda e: page.run_task(play_episode, ctx, ctx["season"], 0),
        )
        secondary = ft.ButtonStyle(
            color=TEXT, icon_color=TEXT,
            bgcolor={ft.ControlState.HOVERED: SURFACE_3, ft.ControlState.FOCUSED: SURFACE_3,
                     ft.ControlState.DEFAULT: SURFACE_2},
            side={ft.ControlState.FOCUSED: ft.BorderSide(3, ACCENT),
                  ft.ControlState.DEFAULT: ft.BorderSide(1, BORDER)}, shape=ft.RoundedRectangleBorder(radius=12),
            padding=ft.Padding.symmetric(horizontal=18, vertical=18),
        )
        resume_btn = ft.FilledButton(
            "Продовжити", icon=ft.Icons.REPLAY_ROUNDED, visible=False,
            style=ft.ButtonStyle(
                bgcolor={ft.ControlState.HOVERED: "#FFFFFF", ft.ControlState.DEFAULT: "#ECECEC"},
                color="#0D0E12", icon_color="#0D0E12", shape=ft.RoundedRectangleBorder(radius=12),
                padding=ft.Padding.symmetric(horizontal=22, vertical=18),
                text_style=ft.TextStyle(size=15, weight=ft.FontWeight.W_700),
                side={ft.ControlState.FOCUSED: ft.BorderSide(3, ACCENT),
                      ft.ControlState.DEFAULT: ft.BorderSide(0, ft.Colors.TRANSPARENT)}))

        def update_resume():
            """Кнопка «Продовжити · С1 · Серія 3 · 23:14», якщо тайтл уже почали дивитись."""
            h = history_entry(item["url"])
            spot = find_episode(ctx, h) if (h and ctx["seasons"] and not watched(h)) else None
            resume_btn.visible = spot is not None
            if spot:
                resume_btn.content = f"Продовжити · {resume_caption(h)}"
                resume_btn.on_click = lambda e: page.run_task(resume_watching)
            return spot

        async def resume_watching():
            h = history_entry(item["url"])
            spot = find_episode(ctx, h) if h else None
            if not spot:
                await play_episode(ctx, ctx["season"], 0)
                return
            if h.get("voice") in ctx["voices"]:
                ctx["voice"] = h["voice"]
            await play_episode(ctx, *spot, start_ms=h["pos"])

        trailer_btn = ft.OutlinedButton("Трейлер", icon=ft.Icons.SMART_DISPLAY_ROUNDED,
                                        style=secondary, visible=False)
        m3u_btn = ft.OutlinedButton(".m3u", icon=ft.Icons.DOWNLOAD_ROUNDED, style=secondary,
                                    visible=not TV,
                                    tooltip="Зберегти плейлист", disabled=True,
                                    on_click=lambda e: page.run_task(save_m3u, ctx))
        site_btn = ft.IconButton(
            ft.Icons.OPEN_IN_NEW_ROUNDED, visible=not TV, tooltip="Відкрити на сайті", icon_color=MUTED,
            on_click=lambda e: page.run_task(ft.UrlLauncher().launch_url, item["url"]))

        voice_row = ft.Row([], spacing=8, wrap=True,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER)
        # рядок озвучок з'являється, коли стане відомо, які доріжки є: плавно розгортається
        # (висота 0 → 46), а не зсуває вміст різким стрибком
        voice_box = ft.Container(voice_row, height=0, opacity=0, animate=ui.MED,
                                 animate_opacity=ui.MED, clip_behavior=ft.ClipBehavior.HARD_EDGE)
        ctx["voice_row"] = voice_row
        ctx["voice_box"] = voice_box

        hero_info = ui.appear(ft.Container(ft.Column([
            title, orig,
            ft.Container(height=6),
            meta_row, genres_row,
            ft.Container(height=6),
            description, more_btn,
            ft.Container(height=8),
            voice_box,
            ft.Row([resume_btn, play_btn, trailer_btn, m3u_btn, site_btn], spacing=10, wrap=True),
            ft.Container(height=4),
            cast,
        ], spacing=6), padding=ft.Padding.only(top=8)))

        hero = ft.ResponsiveRow([
            ft.Container(poster, col={"xs": 12, "md": 6, "lg": 5},
                         alignment=ft.Alignment.CENTER if is_mobile else ft.Alignment.TOP_LEFT),
            ft.Container(hero_info, col={"xs": 12, "md": 6, "lg": 7}),
        ], spacing=36, run_spacing=24)

        # ── блок серій
        ep_status = ft.Text("Шукаю серії…", size=13, color=MUTED)
        ep_progress = ft.ProgressBar(value=None, color=ACCENT, bgcolor=SURFACE_2,
                                     border_radius=4, height=4, width=260)
        # на TV — з переносом: у вкладену горизонтальну прокрутку фокус пульта ↑/↓ не заходить
        seasons_row = ft.Row([], spacing=8, wrap=TV, run_spacing=8,
                             scroll=None if TV else ft.ScrollMode.AUTO)
        episodes_box = ft.AnimatedSwitcher(
            ft.Shimmer(
                ft.Column([ft.Container(height=58, bgcolor=SURFACE_2, border_radius=12)
                           for _ in range(5)], spacing=8),
                base_color=SURFACE_2, highlight_color=SURFACE_3),
            duration=180, reverse_duration=0, transition=ft.AnimatedSwitcherTransition.FADE,
            switch_in_curve=ui.EASE, switch_out_curve=ui.EASE,
        )
        episodes_section = ui.appear(ft.Column([
            ft.Row([ft.Text("Серії", size=22, weight=ft.FontWeight.W_800, color=TEXT),
                    ft.Container(expand=True), ep_status], spacing=12),
            ft.Row([ep_progress]),
            seasons_row,
            ft.Container(height=4),
            episodes_box,
        ], spacing=10))
        ctx.update(seasons_row=seasons_row, episodes_box=episodes_box)

        content = ft.Container(
            ft.Column([hero, ft.Container(height=36), episodes_section], spacing=0),
            padding=ft.Padding.only(left=side_pad, right=side_pad, top=8 if is_mobile else 16,
                                    bottom=60),
        )
        # верхня панель — окрема смуга над контентом (зображення під «світлофор» і кнопку
        # «Назад» не заїжджає); після прокрутки в ній з'являється назва й нижня межа
        bar_title = ft.Text(item["title"], size=15, weight=ft.FontWeight.W_700, color=TEXT,
                            opacity=0, animate_opacity=ui.MED, max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS, expand=True)
        bar = ft.Container(
            ft.Row([round_button(ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED, lambda e: go_back(),
                                    tooltip="Назад"), bar_title], spacing=14),
            height=58, padding=ft.Padding.only(left=14 + traffic_gap, right=14),
            bgcolor=BG, animate=ui.MED,
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.with_opacity(0, "#FFFFFF"))),
        )

        def on_scroll(e: ft.OnScrollEvent):
            compact = e.pixels > 40
            if compact == ctx.get("compact", False):
                return
            ctx["compact"] = compact
            bar.border = ft.Border.only(bottom=ft.BorderSide(1, BORDER if compact else "#00FFFFFF"))
            bar_title.value = title.value
            bar_title.opacity = 1 if compact else 0
            bar.update()

        scroller = ft.ListView([ft.Stack([backdrop, backdrop_fade, content])],
                               expand=True, padding=0, on_scroll=on_scroll, scroll_interval=50)
        title_bar = drag_area(bar)
        view = ft.View(
            route="/title", padding=0, bgcolor=BG,
            controls=[safe(ft.Column([title_bar, scroller], expand=True, spacing=0))],
        )
        page.views.append(view)
        page.update()
        await asyncio.sleep(0.05)
        poster.scale = 1
        ui.reveal(hero_info)
        page.update()

        # ── інформація про тайтл
        try:
            html = await asyncio.to_thread(core.fetch, item["url"])
            info = await asyncio.to_thread(core.parse_title, html, item["url"])
        except Exception as ex:
            ep_status.value = await explain(ex, "сторінку тайтлу")
            ep_progress.visible = False
            ui.reveal(episodes_section)
            page.update()
            return
        ctx["info"] = info
        if info["poster"] and info["poster"] != item["poster"]:
            poster.content = ui.poster_image(info["poster"], radius=18, aspect_ratio=POSTER_RATIO)
            backdrop.content = backdrop_image(info["poster"])
        title.value = info["title"] or item["title"]
        if info["orig"]:
            orig.value, orig.visible = info["orig"], True
        meta = []
        if info["imdb"]:
            meta.append(ui.pill(f"IMDb {info['imdb']}", ft.Icons.STAR_ROUNDED, color=TEXT,
                                icon_color=ui.GOLD, bold=True))
        meta += [ui.pill(info[k]) for k in ("year", "duration", "age", "country") if info[k]]
        meta_row.controls = meta
        genres_row.controls = [
            ft.Container(ft.Text(g.capitalize(), size=12, color=ACCENT, weight=ft.FontWeight.W_600),
                         padding=ft.Padding.symmetric(horizontal=10, vertical=4), border_radius=20,
                         bgcolor=ft.Colors.with_opacity(0.1, ACCENT),
                         border=ft.Border.all(1, ft.Colors.with_opacity(0.25, ACCENT)))
            for g in info["genres"]]
        description.value = info["description"]
        more_btn.visible = len(info["description"]) > 280
        if info["actors"]:
            cast.value = "У ролях: " + ", ".join(info["actors"])
            cast.visible = True
        if info["trailer"]:
            trailer_btn.visible = True
            trailer_btn.on_click = lambda e: page.run_task(ft.UrlLauncher().launch_url, info["trailer"])
        ui.reveal(episodes_section)
        page.update()

        # ── серії: або плеєри прямо на сторінці (фільм / серіал з плейлистом),
        #    або окремі сторінки серій — тоді лише список, відео шукаємо при запуску
        err = None
        try:
            found = await asyncio.to_thread(core.page_streams, item["url"], html)
            if found:
                seasons = core.group_streams(found)
            else:
                ep_status.value = "Завантажую список серій…"
                page.update()
                episodes, locked = await asyncio.to_thread(core.list_episodes, item["url"], html)
                seasons = core.group_episode_pages(episodes, locked)
        except Exception as ex:
            seasons, err = [], ex
        if view not in page.views:
            return  # користувач уже пішов з екрана
        ctx["seasons"] = seasons
        ctx["season"] = next((i for i, x in enumerate(seasons) if x["episodes"]), 0)
        ep_progress.visible = False
        if not seasons:
            ep_status.value = (await explain(err, "серії")) if err else ""
            episodes_box.content = empty_state(ft.Icons.VIDEOCAM_OFF_ROUNDED, "Потоків не знайдено",
                                               "Можливо, відео ще не додано на сайт")
            page.update()
            return
        total = sum(len(s["episodes"]) for s in seasons)
        ep_status.value = f"{total} {plural(total, 'серія', 'серії', 'серій')}"
        play_btn.disabled = m3u_btn.disabled = False
        has_resume = update_resume() is not None
        # після виходу з плеєра кнопку «Продовжити» оновлюємо (нова хвилина)
        view.data = {"refresh": lambda: (update_resume(), page.update())}
        if TV:  # autofocus не спрацьовує на вимкненій кнопці — фокус ставимо, щойно ввімкнули
            page.run_task(focus_later, resume_btn if has_resume else play_btn)
        episodes_section.visible = total > 1  # для фільму вистачає кнопки «Дивитись»
        ctx["voices"] = all_voices(seasons)
        ctx["voice"] = ctx["voices"][0] if ctx["voices"] else ""
        render_voices(ctx)
        render_seasons(ctx)
        page.update()
        if not ctx["voices"]:
            # серії ще без потоків — дізнаємось набір озвучок з першої серії
            page.run_task(detect_voices, ctx)
        if resume:  # картка з «Продовжити перегляд» — одразу з тієї ж хвилини
            page.run_task(resume_watching)
        elif autoplay:  # «Дивитись» з банера — одразу вмикаємо
            page.run_task(play_episode, ctx, ctx["season"], 0)

    async def focus_later(control):
        await asyncio.sleep(0.35)  # після того, як кнопку ввімкнено (чи закрито вікно)
        try:
            await control.focus()
        except Exception:
            pass

    async def ensure_sources(ep):
        """Потоки серії; для серіалів з окремими сторінками серій — завантажуються раз, на вимогу."""
        if ep["sources"] is not None:
            return ep["sources"]
        task = ep.get("_loading")
        if task is None:
            task = ep["_loading"] = asyncio.ensure_future(
                asyncio.to_thread(core.episode_sources, ep["page"]))
        try:
            ep["sources"] = await task
        except Exception:
            ep.pop("_loading", None)
            raise
        return ep["sources"]

    async def detect_voices(ctx):
        ep = next((e for x in ctx["seasons"] for e in x["episodes"]), None)
        if ep is None:
            return
        try:
            await ensure_sources(ep)
        except Exception:
            return
        ctx["voices"] = all_voices(ctx["seasons"])
        if ctx["voices"] and ctx["voice"] not in ctx["voices"]:
            ctx["voice"] = ctx["voices"][0]
        render_voices(ctx)
        page.update()

    # ── озвучки
    def all_voices(seasons):
        voices = []
        for s in seasons:
            for ep in s["episodes"]:
                for src in ep["sources"] or []:
                    v = src["entry"].get("voice") or ""
                    if v and v not in voices:
                        voices.append(v)
        return sorted(voices, key=core.is_original)

    def voice_icon(voice):
        return ft.Icons.SUBTITLES_ROUNDED if core.is_original(voice) else ft.Icons.RECORD_VOICE_OVER_ROUNDED

    def pick_source(ep, voice):
        for src in ep["sources"]:
            if src["entry"].get("voice") == voice:
                return src["entry"]
        return ep["sources"][0]["entry"]

    def render_voices(ctx):
        row, box = ctx["voice_row"], ctx["voice_box"]
        voices = ctx["voices"]
        shown = len(voices) > 1
        box.height, box.opacity = (46, 1) if shown else (0, 0)
        if not shown:
            return

        def select(v):
            ctx["voice"] = v
            ctx["voice_selected"] = True  # на TV фокус повертається на вибрану озвучку
            render_voices(ctx)
            row.update()

        chips = []
        for v in voices:
            on = v == ctx["voice"]
            chips.append(tv_tap(ft.Container(
                ft.Row([ft.Icon(voice_icon(v), size=16, color="#FFFFFF" if on else MUTED),
                        ft.Text(v, size=13, weight=ft.FontWeight.W_600,
                                color="#FFFFFF" if on else TEXT)], spacing=8, tight=True),
                padding=ft.Padding.symmetric(horizontal=14, vertical=9), border_radius=12,
                gradient=ui.ACCENT_GRADIENT if on else None, bgcolor=None if on else SURFACE_2,
                border=ft.Border.all(1, BORDER), animate=ui.FAST,
                tooltip="Українська озвучка" if not core.is_original(v)
                else "Оригінальна доріжка з субтитрами",
                on_click=lambda e, v=v: select(v),
            ), lambda e, v=v: select(v), autofocus=on and ctx.get("voice_selected", False)))
        row.controls = [ft.Text("Озвучка", size=13, color=MUTED, weight=ft.FontWeight.W_600),
                        *chips]

    def render_seasons(ctx):
        seasons, current = ctx["seasons"], ctx["season"]
        if TV:
            # на телевізорі — усі сезони одним списком із заголовками: пультом просто
            # гортаєш униз (до кнопок-сезонів збоку стрілками ↑/↓ фокус не доходить)
            ctx["seasons_row"].controls = []
            blocks = []
            for si, season in enumerate(seasons):
                if len(seasons) > 1:
                    blocks.append(ft.Container(
                        ft.Text(season["name"], size=18, weight=ft.FontWeight.W_800, color=TEXT),
                        padding=ft.Padding.only(top=14 if si else 0, bottom=2)))
                if season.get("locked"):
                    blocks.append(ft.Row([ft.Icon(ft.Icons.LOCK_OUTLINE_ROUNDED, size=16, color=MUTED),
                                          ft.Text("Сайт показує серії цього сезону лише "
                                                  "зареєстрованим користувачам", size=13, color=MUTED)],
                                         spacing=8))
                    continue
                blocks += [episode_row(ctx, si, ei, ep) for ei, ep in enumerate(season["episodes"])]
            ctx["episodes_box"].content = ft.Column(blocks, spacing=8, key="all-seasons")
            return

        def select(i):
            ctx["season"] = i
            ctx["season_selected"] = True  # на TV фокус повертається на вибраний сезон
            render_seasons(ctx)
            page.update()

        ctx["seasons_row"].controls = [] if len(seasons) == 1 else [
            tv_tap(ft.Container(
                ft.Row([ft.Text(s["name"], size=13, weight=ft.FontWeight.W_700,
                                color="#FFFFFF" if i == current else MUTED)]
                       + ([ft.Icon(ft.Icons.LOCK_OUTLINE_ROUNDED, size=13,
                                   color="#FFFFFF" if i == current else MUTED)]
                          if s.get("locked") else []), spacing=5, tight=True),
                padding=ft.Padding.symmetric(horizontal=16, vertical=9), border_radius=20,
                gradient=ui.ACCENT_GRADIENT if i == current else None,
                bgcolor=None if i == current else SURFACE_2,
                border=ft.Border.all(1, BORDER), animate=ui.FAST,
                on_click=lambda e, i=i: select(i),
            ), lambda e, i=i: select(i), radius=20,
                autofocus=i == current and ctx.get("season_selected", False))
            for i, s in enumerate(seasons)]
        if seasons[current].get("locked"):
            ctx["episodes_box"].content = ft.Container(empty_state(
                ft.Icons.LOCK_OUTLINE_ROUNDED, "Серії недоступні",
                "Сайт показує серії цього сезону лише зареєстрованим користувачам"),
                key=f"season{current}", height=260)
        else:
            ctx["episodes_box"].content = ft.Column(
                [episode_row(ctx, current, ei, ep)
                 for ei, ep in enumerate(seasons[current]["episodes"])],
                spacing=8, key=f"season{current}")

    def episode_row(ctx, si, ei, ep):
        num_text = ft.Text(str(ep["num"]) if ep["num"] is not None else "▶", size=13,
                           weight=ft.FontWeight.W_800, color=ACCENT)
        num = ft.Container(
            num_text, width=38, height=38, border_radius=19, alignment=ft.Alignment.CENTER,
            bgcolor=ft.Colors.with_opacity(0.1, ACCENT),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.3, ACCENT)), animate=ui.FAST,
        )
        # маленькі значки доступних озвучок серії
        badges = [ft.Icon(voice_icon(src["entry"].get("voice") or ""), size=16, color=MUTED,
                          tooltip=src["label"])
                  for src in ep["sources"]] if len(ep["sources"] or []) > 1 else []
        play_icon = ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, color=MUTED, opacity=0.5,
                            animate_opacity=ui.FAST)
        row = ft.Container(
            ft.Row([
                num,
                ft.Text(ep["label"], size=15, weight=ft.FontWeight.W_600, color=TEXT, expand=True,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Row(badges, spacing=6),
                play_icon,
            ], spacing=14),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10), border_radius=12,
            bgcolor=SURFACE, border=ft.Border.all(1, BORDER), animate=ui.FAST,
            on_click=lambda e: page.run_task(play_episode, ctx, si, ei),
        )

        def highlight(h):
            row.bgcolor = SURFACE_2 if h else SURFACE
            num.gradient = ui.ACCENT_GRADIENT if h else None
            num_text.color = "#FFFFFF" if h else ACCENT
            play_icon.opacity = 1 if h else 0.5
            play_icon.color = ACCENT if h else MUTED
            row.update()

        if TV:
            return tv_tap(row, lambda e: page.run_task(play_episode, ctx, si, ei), highlight)
        row.on_hover = lambda e: highlight(ui.hovered(e))
        return row

    # ═════════════════════ плеєр ═════════════════════
    async def play_episode(ctx, si, ei, start_ms=None):
        if not ctx["seasons"]:
            toast("Серії ще завантажуються…", ft.Icons.HOURGLASS_TOP_ROUNDED)
            return
        ep = ctx["seasons"][si]["episodes"][ei]
        if ep["sources"] is None:
            toast("Завантажую серію…", ft.Icons.HOURGLASS_TOP_ROUNDED)
        try:
            sources = await ensure_sources(ep)
        except Exception as ex:
            msg = await explain(ex, "серію")
            toast(msg, error_icon(msg), retry=lambda: page.run_task(play_episode, ctx, si, ei))
            return
        if not sources:
            toast("Для цієї серії відео не знайдено", ft.Icons.VIDEOCAM_OFF_ROUNDED)
            return
        if ctx["voices"] != all_voices(ctx["seasons"]):
            await detect_voices(ctx)
        multi = len(ctx["seasons"]) > 1
        # усі серії по порядку; черга — з обраної й далі, «before» — попередні
        everything, start = [], 0
        for i, season in enumerate(ctx["seasons"]):
            for j, e in enumerate(season["episodes"]):
                if (i, j) == (si, ei):
                    start = len(everything)
                prefix = f"{season['name']} · " if multi else ""
                everything.append((prefix + e["label"], e))
        show = (ctx["info"] or {}).get("title") or ctx["item"]["title"]
        view = ft.View(route="/play", padding=0, bgcolor="#000000")
        start_pos = ft.Duration(milliseconds=int(start_ms)) if start_ms else None
        view.controls = [safe(build_player(view, ctx, show, everything[start:], ctx.get("voice", ""),
                                      start_pos=start_pos, before=everything[:start]))]
        page.views.append(view)
        page.update()

    def build_player(view, ctx, show, queue, voice, start_pos=None, sub_pref=None, before=()):
        """Плеєр для черги серій у вибраній озвучці; sub_pref — мова увімкнених субтитрів
        (None — вимкнено, за замовчуванням); before — серії перед чергою (для «попередньої»)."""
        queue, before = list(queue), list(before)
        entries = []  # потоки серій, що вже в плейлисті (решта догружається у фоні)
        for _, ep in queue:
            if not ep["sources"]:
                break
            entries.append(pick_source(ep, voice))
        names = [n if n != "Фільм" else "" for n, _ in queue]
        p = {"idx": 0, "sub_lang": sub_pref,  # None — субтитри вимкнено, інакше мова
             "voice": voice, "target": start_pos, "extending": False,
             "restoring": bool(start_pos and start_pos.in_milliseconds > 3000)}
        voices = all_voices([{"episodes": [ep for _, ep in queue]}])

        def subtitle_for(entry):
            subs = entry.get("subs") or []
            if not p["sub_lang"] or not subs:
                return None
            return next((x for x in subs if x["lang"] == p["sub_lang"]), subs[0])

        def track(sub):
            if not sub:
                return fv.VideoSubtitleTrack.none()
            return fv.VideoSubtitleTrack(src=sub["url"], title=sub["title"], language=sub["lang"])

        def sub_config():
            base = 20 if is_mobile else 34 if TV else 28
            return fv.VideoSubtitleConfiguration(
                text_style=ft.TextStyle(size=round(base * float(settings["sub_scale"])),
                                        color="#FFFFFF", weight=ft.FontWeight.W_600,
                                        bgcolor="#99000000"))

        def fit_value(fullscreen_on=False):
            if is_mobile and not fullscreen_on:
                # на телефоні у звичайному (вертикальному) режимі — завжди «вписати»:
                # обраний для повного екрана масштаб тут розтягував би кадр
                return ft.BoxFit.CONTAIN
            mode = settings["fit"]
            if mode == "auto":  # на телефоні в повному екрані — заповнити ширину
                return ft.BoxFit.COVER if is_mobile else ft.BoxFit.CONTAIN
            return next((f for k, _, f in FIT_MODES if k == mode), ft.BoxFit.CONTAIN)

        FIT_ICONS = {"contain": ft.Icons.FIT_SCREEN_ROUNDED, "cover": ft.Icons.CROP_FREE_ROUNDED,
                     "fill": ft.Icons.OPEN_IN_FULL_ROUNDED}

        def current_fit_mode():
            keys = [k for k, _, _ in FIT_MODES]
            if settings["fit"] in keys:
                return settings["fit"]
            return "cover" if is_mobile else "contain"  # «auto»

        def refresh_fit_button():
            """Іконка й підказка кнопки показують поточний режим (замість спливаючої плашки)."""
            mode = current_fit_mode()
            fit_btn.icon = FIT_ICONS[mode]
            fit_btn.tooltip = f"Масштаб: {next(n for k, n, _ in FIT_MODES if k == mode).lower()}"

        def set_sub_size(scale):
            save_setting("sub_scale", scale)
            video.subtitle_configuration = sub_config()
            refresh_bar()
            page.update()

        def cycle_fit(e=None):
            keys = [k for k, _, _ in FIT_MODES]
            mode = keys[(keys.index(current_fit_mode()) + 1) % len(keys)]
            save_setting("fit", mode)
            video.fit = fit_value(st.get("fullscreen", False))
            refresh_fit_button()
            video.update()

        now = ft.Text(names[0], size=12, color=MUTED, visible=bool(names[0]))
        video = fv.Video(
            expand=True, autoplay=True, title=show, fill_color="#000000",
            playlist=[fv.VideoMedia(e["url"], http_headers=core.stream_headers(e)) for e in entries],
            playlist_mode=fv.PlaylistMode.NONE,
            subtitle_track=track(subtitle_for(entries[0])),
            subtitle_configuration=sub_config(),
            fit=fit_value(),
            configuration=fv.VideoConfiguration(mpv_properties=MPV_STREAMING),
        )

        async def on_error(e):
            # Плеєр інколи повідомляє про короткочасні збої потоку, які сам і переживає,
            # тож повідомлень не показуємо. Якщо ж відтворення справді зупинилось —
            # тихо перевідкриваємо поточну серію з того ж місця (не частіше разу на 30 с).
            if time.monotonic() - p.get("error_at", 0) < 30:
                return
            p["error_at"] = time.monotonic()
            await asyncio.sleep(4)
            try:
                if not alive() or await video.is_playing():
                    return
            except Exception:
                return
            await switch_voice(entries[p["idx"]].get("voice") or p["voice"])

        video.on_error = on_error

        def badge(text, bright=True):
            """Значок у стилі «CC»: текст у рамці; напівпрозорий — вимкнено."""
            return ft.Container(
                ft.Text(text, size=12, weight=ft.FontWeight.W_800, color=TEXT),
                width=34, height=24, border_radius=6, alignment=ft.Alignment.CENTER,
                border=ft.Border.all(1.6, TEXT), opacity=1 if bright else 0.4,
                margin=6,
            )

        def menu_item(text, checked, on_click):
            return ft.PopupMenuItem(
                content=ft.Row([ft.Icon(ft.Icons.CHECK_ROUNDED, size=16,
                                        color=ACCENT if checked else ft.Colors.TRANSPARENT),
                                ft.Text(text, color=TEXT)], spacing=10),
                on_click=on_click)

        def voice_code(v):
            if core.is_original(v):
                return "EN"
            if v.startswith("Українська"):
                return "UA"
            return v[:2].upper()

        def menu_state(is_open):
            st["menu_open"] = is_open

        menu_style = dict(bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=12),
                          menu_position=ft.PopupMenuPosition.OVER,
                          on_open=lambda e: menu_state(True), on_cancel=lambda e: menu_state(False),
                          on_select=lambda e: menu_state(False))
        def set_sub(lang):
            p["sub_lang"] = lang
            video.subtitle_track = track(subtitle_for(entries[p["idx"]]))
            refresh_bar()
            page.update()

        voice_menu = ft.PopupMenuButton(tooltip="Озвучка", **menu_style)
        sub_menu = ft.PopupMenuButton(tooltip="Субтитри", **menu_style)

        # На TV випадаюче меню пультом незручне й не показує фокус — замість нього кнопка
        # з білою рамкою фокуса, а вибір відкривається у вікні по центру екрана.
        def choice_dialog(title, options):
            def pick(action):
                def handler(e):
                    page.pop_dialog()
                    action(e)
                    if p.get("refocus"):  # після вікна фокус повертаємо на ▶/⏸
                        page.run_task(focus_later, p["refocus"])
                return handler

            page.show_dialog(ft.AlertDialog(
                title=ft.Text(title, size=22, weight=ft.FontWeight.W_800, color=TEXT),
                bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
                content=ft.Column([
                    ft.FilledButton(
                        text, icon=ft.Icons.CHECK_ROUNDED if checked else None, autofocus=checked,
                        on_click=pick(action), width=380,
                        style=ft.ButtonStyle(
                            bgcolor={ft.ControlState.FOCUSED: ACCENT,
                                     ft.ControlState.DEFAULT: SURFACE_2},
                            color="#FFFFFF", icon_color="#FFFFFF",
                            padding=ft.Padding.symmetric(horizontal=20, vertical=18),
                            shape=ft.RoundedRectangleBorder(radius=12),
                            text_style=ft.TextStyle(size=17, weight=ft.FontWeight.W_600)))
                    for text, checked, action in options], tight=True, spacing=10),
            ))

        def tv_menu_button(title, key):
            # саме IconButton: звичайна Button усередині панелі плеєра фокус не отримує
            return ft.IconButton(
                tooltip=title, on_click=lambda e: choice_dialog(title, p.get(key, [])),
                style=ft.ButtonStyle(
                    bgcolor={ft.ControlState.FOCUSED: ACCENT,
                             ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT},
                    shape=ft.RoundedRectangleBorder(radius=12), padding=4))

        tv_voice_btn = tv_menu_button("Озвучка", "voice_opts")
        tv_sub_btn = tv_menu_button("Субтитри", "sub_opts")

        def fill_menus():
            entry = entries[p["idx"]]
            cur_voice = entry.get("voice") or p["voice"]
            voice_menu.content = badge(voice_code(cur_voice))
            voice_menu.items = [
                menu_item(v, v == cur_voice, lambda e, v=v: page.run_task(switch_voice, v))
                for v in voices]
            subs = entry.get("subs") or []
            current = subtitle_for(entry)
            if len(subs) == 1:
                on_items = [menu_item("Увімкнено", current is not None,
                                      lambda e: set_sub(subs[0]["lang"] or "on"))]
            else:
                on_items = [menu_item(f"Увімкнено ({x['title'].lower()})",
                                      current is not None and current["url"] == x["url"],
                                      lambda e, x=x: set_sub(x["lang"] or "on"))
                            for x in subs]
            sub_menu.content = badge("CC", bright=current is not None)
            size_items = [menu_item(f"Розмір: {name.lower()}",
                                    abs(float(settings["sub_scale"]) - scale) < 0.01,
                                    lambda e, scale=scale: set_sub_size(scale))
                          for name, scale in SUB_SIZES]
            sub_menu.items = [menu_item("Вимкнено", current is None, lambda e: set_sub(None)),
                              *on_items, ft.PopupMenuItem(), *size_items]
            # ті самі пункти — для вікна вибору на TV
            p["voice_opts"] = [(v, v == cur_voice, lambda e, v=v: page.run_task(switch_voice, v))
                               for v in voices]
            sub_on = ([("Увімкнено", current is not None,
                        lambda e: set_sub(subs[0]["lang"] or "on"))] if len(subs) == 1 else
                      [(f"Увімкнено ({x['title'].lower()})",
                        current is not None and current["url"] == x["url"],
                        lambda e, x=x: set_sub(x["lang"] or "on")) for x in subs])
            p["sub_opts"] = [("Вимкнено", current is None, lambda e: set_sub(None)), *sub_on,
                             *[(f"Розмір: {name.lower()}",
                                abs(float(settings["sub_scale"]) - scale) < 0.01,
                                lambda e, scale=scale: set_sub_size(scale))
                               for name, scale in SUB_SIZES]]
            tv_voice_btn.icon = badge(voice_code(cur_voice))
            tv_sub_btn.icon = badge("CC", bright=current is not None)

        def reload_playlist(pos=None):
            """Новий плейлист у тому ж плеєрі (а не новий плеєр): інакше в повноекранному
            режимі лишається зображення старого, вже закритого плеєра — чорний екран."""
            entries.clear()
            for _, ep in queue:
                if not ep["sources"]:
                    break
                entries.append(pick_source(ep, p["voice"]))
            p["idx"] = 0
            video.playlist = [fv.VideoMedia(e["url"], http_headers=core.stream_headers(e))
                              for e in entries]
            video.subtitle_track = track(subtitle_for(entries[0]))
            now.value, now.visible = names[0], bool(names[0])
            refresh_bar()
            page.update()
            if pos and pos.in_milliseconds > 3000:
                p["target"], p["restoring"] = pos, True
                page.run_task(restore_position)
            if len(entries) < len(queue) and not p["extending"]:
                page.run_task(extend_playlist)

        async def switch_voice(v):
            # та сама серія в іншій озвучці — продовжуємо з того ж місця
            try:
                pos = await video.get_current_position()
            except Exception:
                pos = None
            if p["restoring"] or not pos or pos.in_milliseconds <= 0:
                # позиція ще не відновилась (озвучку перемкнули двічі поспіль) — беремо цільову
                pos = p["target"] if p["restoring"] else pos
            idx = p["idx"]
            before.extend(queue[:idx])
            del queue[:idx], names[:idx]
            p["voice"] = v
            reload_playlist(pos)

        async def seek_by(seconds):
            try:
                pos = await video.get_current_position()
                await video.seek(ft.Duration(milliseconds=max(0, pos.in_milliseconds + seconds * 1000)))
            except Exception:
                pass

        st["seek_by"] = seek_by
        st["play_pause"] = lambda: page.run_task(video.play_or_pause)

        async def go_next():
            for _ in range(80):  # наступна серія ще може догружатися
                if len(entries) > p["idx"] + 1:
                    await video.next()
                    return
                await asyncio.sleep(0.25)
            toast("Не вдалося завантажити наступну серію", ft.Icons.ERROR_OUTLINE_ROUNDED)

        async def go_prev():
            if p["idx"] > 0:
                await video.previous()
                return
            if not before:
                return
            # попередня серія ще не в плейлисті — ставимо її на початок плейлиста
            _, ep = before[-1]
            try:
                sources = await ensure_sources(ep)
            except Exception:
                sources = []
            if not sources:
                toast("Для попередньої серії відео не знайдено", ft.Icons.VIDEOCAM_OFF_ROUNDED)
                return
            label, prev_ep = before.pop()
            del queue[:p["idx"]], names[:p["idx"]]
            queue.insert(0, (label, prev_ep))
            names.insert(0, label if label != "Фільм" else "")
            reload_playlist()

        def bar_button(icon, tooltip, on_click):
            return ft.IconButton(icon, icon_color="#FFFFFF", icon_size=34 if TV else 28,
                                 tooltip=tooltip, on_click=on_click,
                                 style=ft.ButtonStyle(
                                     bgcolor={ft.ControlState.FOCUSED: ACCENT,
                                              ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT},
                                     shape=ft.CircleBorder(), padding=10) if TV else None)

        # кнопки створюються один раз і лише переставляються між слотами
        prev_btn = bar_button(ft.Icons.SKIP_PREVIOUS_ROUNDED, "Попередня серія",
                              lambda e: page.run_task(go_prev))
        next_btn = bar_button(ft.Icons.SKIP_NEXT_ROUNDED, "Наступна серія",
                              lambda e: page.run_task(go_next))

        # Панель плеєра будується один раз: кнопки, яких не було в ній від початку (або з
        # visible=False), плеєр уже не покаже. Тому — постійні «слоти», у які кнопку
        # вставляємо чи прибираємо; зміни вмісту слотів доходять до екрана.
        prev_slot, next_slot, voice_slot, sub_slot = (ft.Container() for _ in range(4))
        fit_btn = bar_button(ft.Icons.ASPECT_RATIO_ROUNDED, "Масштаб", cycle_fit)
        refresh_fit_button()

        def refresh_bar():
            fill_menus()
            prev_slot.content = prev_btn if (p["idx"] > 0 or before) else None
            next_slot.content = next_btn if p["idx"] < len(queue) - 1 else None
            voice_slot.content = (tv_voice_btn if TV else voice_menu) if voices else None
            sub_slot.content = ((tv_sub_btn if TV else sub_menu)
                                if entries[p["idx"]].get("subs") else None)

        style = dict(seek_bar_position_color=ACCENT, seek_bar_thumb_color=ACCENT,
                     automatically_imply_skip_next_button=False,
                     automatically_imply_skip_previous_button=False,
                     bottom_button_bar=[prev_slot, fv.VideoPlayOrPauseButton(), next_slot,
                                        fv.VideoVolumeButton(), fv.VideoPositionIndicator(),
                                        fv.VideoSpacer(), voice_slot, sub_slot, fit_btn,
                                        fv.VideoFullscreenButton()])
        if TV:
            # Телевізор: без окремого повноекранного шару (у нього не потрапляє фокус пульта —
            # стрілки йшли на сторінку «під» плеєром). Плеєр і так на весь екран, а панель
            # завжди видима, з великими кнопками; фокус одразу на ▶/⏸.
            def tv_btn(icon, tooltip, action, autofocus=False):
                return ft.IconButton(icon, icon_size=34, icon_color="#FFFFFF", tooltip=tooltip,
                                     autofocus=autofocus, on_click=action,
                                     style=ft.ButtonStyle(
                                         bgcolor={ft.ControlState.FOCUSED: ACCENT,
                                                  ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT},
                                         shape=ft.CircleBorder(), padding=10))

            def toggle_play(e):
                p["paused"] = not p.get("paused", False)
                play_pause.icon = ft.Icons.PLAY_ARROW_ROUNDED if p["paused"] else ft.Icons.PAUSE_ROUNDED
                play_pause.update()
                page.run_task(video.play_or_pause)

            play_pause = tv_btn(ft.Icons.PAUSE_ROUNDED, "Пауза / відтворення", toggle_play,
                                autofocus=True)
            p["refocus"] = play_pause
            style["bottom_button_bar"] = [
                prev_slot,
                tv_btn(ft.Icons.REPLAY_10_ROUNDED, "Назад на 10 с",
                       lambda e: page.run_task(seek_by, -10)),
                play_pause,
                tv_btn(ft.Icons.FORWARD_10_ROUNDED, "Вперед на 10 с",
                       lambda e: page.run_task(seek_by, 10)),
                # UA/CC — одразу за кнопками навігації: справа від роздільника панелі
                # елементи фокусу з пульта не отримують
                next_slot, voice_slot, sub_slot, fit_btn, fv.VideoPositionIndicator(),
                fv.VideoSpacer()]
            # «мобільна» панель: у «настільної» є вбудовані гарячі клавіші, які перехоплюють
            # стрілки (перемотка), і до ⏭ / UA / CC пультом не дістатись; тут стрілки ходять
            # між кнопками, а перемотують кнопки ±10 с
            style.update(visible_on_mount=True, controls_hover_duration=24 * 3600 * 1000,
                         button_bar_height=72, primary_button_bar=[],
                         seek_bar_margin=ft.Padding.only(left=40, right=40, bottom=24),
                         bottom_button_bar_margin=ft.Padding.only(left=32, right=32, bottom=30),
                         backdrop_color="#22000000", seek_gesture=False, volume_gesture=False,
                         brightness_gesture=False, seek_on_double_tap=False)
            video.controls = fv.MaterialVideoControls(**style)
        else:
            mobile_style = dict(
                style,
                # таймлайн не впритул до країв екрана (особливо в повноекранному режимі)
                seek_bar_margin=ft.Padding.only(left=24, right=24, bottom=18),
                bottom_button_bar_margin=ft.Padding.only(left=16, right=16, bottom=22),
                # подвійний тап по лівій / правій половині — перемотка на 10 с
                seek_on_double_tap=True,
                seek_on_double_tap_backward_duration=ft.Duration(seconds=10),
                seek_on_double_tap_forward_duration=ft.Duration(seconds=10),
            )
            video.controls = fv.AdaptiveVideoControls(
                material=fv.MaterialVideoControls(**mobile_style) if is_mobile else None,
                material_desktop=None if is_mobile else fv.MaterialDesktopVideoControls(
                    **style, hide_mouse_on_controls_removal=True))

        def alive():
            try:
                return video.page is not None
            except Exception:
                return False

        async def extend_playlist():
            """Догружає наступні серії (до 3 наперед) і дописує їх у кінець плейлиста —
            плеєр додає їх без переривання відтворення."""
            p["extending"] = True
            try:
                await _extend()
            finally:
                p["extending"] = False

        async def _extend():
            while len(entries) < len(queue):
                if not alive():
                    return
                if len(entries) > p["idx"] + 3:
                    await asyncio.sleep(1)
                    continue
                _, ep = queue[len(entries)]
                try:
                    sources = await ensure_sources(ep)
                except Exception:
                    sources = []
                if not alive():
                    return
                if len(entries) >= len(queue) or queue[len(entries)][1] is not ep:
                    continue  # поки вантажилось, плейлист перебудували (зміна озвучки тощо)
                if not sources:  # серію без відео пропускаємо
                    del queue[len(entries)]
                    del names[len(entries)]
                    continue
                entry = pick_source(ep, p["voice"])
                entries.append(entry)
                video.playlist = video.playlist + [
                    fv.VideoMedia(entry["url"], http_headers=core.stream_headers(entry))]
                refresh_bar()
                video.update()

        def on_track(e):
            try:
                idx = int(e.data)
            except (TypeError, ValueError):
                return
            if 0 <= idx < len(entries):
                p["idx"] = idx
                now.value, now.visible = names[idx], bool(names[idx])
                video.subtitle_track = track(subtitle_for(entries[idx]))
                refresh_bar()
                page.update()

        async def restore_position():
            """Перемотати на збережену позицію (зміна озвучки, «Продовжити перегляд»).
            Перемотка, зроблена до того, як HLS-потік реально почав грати, ігнорується
            плеєром, тому чекаємо на відтворення й перевіряємо результат, повторюючи за потреби."""
            start = p["target"]
            target = start.in_milliseconds
            for _ in range(360):  # до ~90 с: на повільній мережі потік стартує 30–40 с
                await asyncio.sleep(0.25)
                if not alive():
                    return  # плеєр уже закрили
                try:
                    playing = await video.is_playing()
                    duration = await video.get_duration()
                except Exception:
                    continue
                if not playing or not duration or duration.in_milliseconds <= 0:
                    continue
                if p["target"] is not start:
                    return  # з'явилась нова ціль (ще одна зміна озвучки) — її відновить інша задача
                await video.seek(start)
                await asyncio.sleep(0.8)
                try:
                    pos = await video.get_current_position()
                except Exception:
                    continue
                if pos and pos.in_milliseconds >= target - 3000:
                    p["restoring"] = False
                    return  # позицію відновлено
            p["restoring"] = False

        video.on_track_change = on_track
        # кнопки — у власній панелі плеєра, тож вони є й у повноекранному режимі
        refresh_bar()

        def fullscreen(on):
            st["fullscreen"] = on
            if not on:
                st["fullscreen_exit"] = time.monotonic()
            if is_mobile:
                # на весь екран — обраний масштаб (за замовчуванням «заповнити»: фільми
                # закодовано в кадр 16:9 з чорними смугами, а екран телефона ширший);
                # назад у вертикальний режим — знову «вписати»
                video.fit = fit_value(on)
                video.update()

        video.on_enter_fullscreen = lambda e: fullscreen(True)
        video.on_exit_fullscreen = lambda e: fullscreen(False)
        st["video"] = video
        bar = drag_area(ft.Container(
            ft.Row([
                round_button(ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED, lambda e: go_back(), "Назад"),
                ft.Column([ft.Text(show, size=15, weight=ft.FontWeight.W_700, color=TEXT,
                                   max_lines=1, overflow=ft.TextOverflow.ELLIPSIS), now],
                          spacing=0, expand=True),
            ], spacing=10),
            padding=ft.Padding.only(left=14 + traffic_gap, right=14, top=8, bottom=8),
            bgcolor="#000000",
        ))
        async def save_progress():
            if p["restoring"]:
                return  # позиція ще не відновлена — не перезаписуємо збережену нулем
            try:
                pos = await video.get_current_position()
                dur = await video.get_duration()
            except Exception:
                return
            if not pos or not dur or dur.in_milliseconds <= 0 or pos.in_milliseconds < 5000:
                return
            _, ep = queue[p["idx"]]
            record_progress(ctx, ep, entries[p["idx"]].get("voice") or p["voice"],
                            pos.in_milliseconds, dur.in_milliseconds)

        async def track_progress():
            while True:
                await asyncio.sleep(10)
                if not alive():
                    return
                await save_progress()

        st["save_progress"] = save_progress
        page.run_task(track_progress)
        if start_pos and start_pos.in_milliseconds > 3000:
            page.run_task(restore_position)
        if len(entries) < len(queue):
            page.run_task(extend_playlist)
        if TV:
            # autofocus усередині панелі плеєра не спрацьовує, а фокус лишається на сторінці
            # під плеєром (і OK натискав би там) — ставимо його на ▶/⏸, коли панель уже на екрані
            async def focus_player():
                for delay in (0.6, 1.5):
                    await asyncio.sleep(delay)
                    try:
                        await play_pause.focus()
                    except Exception:
                        pass

            page.run_task(focus_player)
            return video  # без верхньої панелі: «Назад» — кнопкою пульта
        return ft.Column([bar, video], expand=True, spacing=0)

    async def save_m3u(ctx):
        eps = [ep for s in ctx["seasons"] for ep in s["episodes"]]
        if any(ep["sources"] is None for ep in eps):
            toast("Збираю посилання на всі серії…", ft.Icons.HOURGLASS_TOP_ROUNDED)
            for ep in eps:
                try:
                    await ensure_sources(ep)
                except Exception:
                    pass
        entries = [pick_source(ep, ctx.get("voice", "")) for ep in eps if ep["sources"]]
        if not entries:
            return
        show = (ctx["info"] or {}).get("title") or ctx["item"]["title"]
        safe = re.sub(r'[\\/:*?"<>|]+', "_", show) or "playlist"
        path = await ft.FilePicker().save_file(
            file_name=f"{safe}.m3u", allowed_extensions=["m3u"],
            file_type=ft.FilePickerFileType.CUSTOM,
            src_bytes=core.build_m3u(entries, show).encode("utf-8"),
        )
        if path:
            toast("Плейлист збережено", ft.Icons.CHECK_CIRCLE_ROUNDED)

    # ═════════════════════ навігація ═════════════════════
    def go_back():
        if len(page.views) <= 1:
            return
        if page.views[-1].route == "/play" and st.get("save_progress"):
            page.run_task(leave_player)  # спершу зберегти, на якій хвилині зупинились
            return
        pop_view()

    async def leave_player():
        save, st["save_progress"] = st.get("save_progress"), None
        try:
            await asyncio.wait_for(save(), 2)
        except Exception:
            pass
        pop_view()

    def pop_view():
        if len(page.views) <= 1:
            return
        page.views.pop()
        top = page.views[-1]
        if isinstance(top.data, dict) and top.data.get("refresh"):
            top.data["refresh"]()
        render_continue()
        page.update()

    async def on_key(e: ft.KeyboardEvent):
        if TV and page.views and page.views[-1].route == "/play":
            if e.key in ("Media Play Pause", "Media Play", "Media Pause") and st.get("play_pause"):
                st["play_pause"]()
                return
            if e.key in ("Media Fast Forward", "Media Rewind") and st.get("seek_by"):
                await st["seek_by"](30 if e.key == "Media Fast Forward" else -10)
                return
        if e.key == "Escape" and st.get("menu_open"):
            st["menu_open"] = False  # Esc лише закриває відкрите меню
        elif e.key == "Escape" and (st.get("fullscreen") or
                                    time.monotonic() - st.get("fullscreen_exit", 0) < 0.8):
            # Esc у повноекранному режимі — повертаємось до звичайного плеєра, а не до серій
            video = st.get("video")
            if st.get("fullscreen") and video is not None:
                video.fullscreen = False
                video.update()
            st["fullscreen"] = False
        elif e.key == "Escape" and len(page.views) > 1:
            go_back()
        elif e.key.upper() == "F" and (e.meta or e.ctrl) and len(page.views) == 1:
            await search.focus()

    page.on_view_pop = lambda e: go_back()
    page.on_keyboard_event = on_key
    page.views.clear()
    page.views.append(home)
    page.update()
    if is_desktop:
        page.run_task(page.window.center)
    page.run_task(startup)
    go_home()


if __name__ == "__main__":
    ft.run(main)
