"""Дизайн-токени та повторно використовувані компоненти."""
import flet as ft

# ───────────── палітра ─────────────
BG = "#0D0E12"
SURFACE = "#15171C"
SURFACE_2 = "#1C1F26"
SURFACE_3 = "#262A33"
BORDER = "#14FFFFFF"
TEXT = "#F3F4F6"
MUTED = "#9BA1AC"
ACCENT = "#FF6A3D"
ACCENT_2 = "#FFB547"
GOLD = "#F5C518"

EASE = ft.AnimationCurve.EASE_OUT_CUBIC
FAST = ft.Animation(160, EASE)
MED = ft.Animation(280, EASE)
SLOW = ft.Animation(450, EASE)
APPEAR = ft.Animation(250, EASE)

# «легкий» режим для слабких пристроїв (телевізори): без анімацій появи
LITE = False

ACCENT_GRADIENT = ft.LinearGradient(
    begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT, colors=[ACCENT_2, ACCENT])


def theme():
    return ft.Theme(
        color_scheme_seed=ACCENT,
        color_scheme=ft.ColorScheme(primary=ACCENT, surface=BG, on_surface=TEXT),
        page_transitions=ft.PageTransitionsTheme(
            macos=ft.PageTransitionTheme.FADE_FORWARDS,
            windows=ft.PageTransitionTheme.FADE_FORWARDS,
            linux=ft.PageTransitionTheme.FADE_FORWARDS,
            android=ft.PageTransitionTheme.PREDICTIVE,
            ios=ft.PageTransitionTheme.CUPERTINO,
        ),
        scrollbar_theme=ft.ScrollbarTheme(
            thumb_color=ft.Colors.with_opacity(0.25, TEXT), thickness=6, radius=3),
    )


def hovered(e):
    return e.data in (True, "true")


def pill(text, icon=None, color=MUTED, bg=SURFACE_2, icon_color=None, bold=False):
    """Невеликий бейдж: «★ 8.3», «2024», «18+»."""
    row = []
    if icon:
        row.append(ft.Icon(icon, size=14, color=icon_color or color))
    row.append(ft.Text(text, size=12, color=color,
                       weight=ft.FontWeight.W_700 if bold else ft.FontWeight.W_500))
    return ft.Container(
        ft.Row(row, spacing=4, tight=True),
        padding=ft.Padding.symmetric(horizontal=10, vertical=5),
        border_radius=20, bgcolor=bg, border=ft.Border.all(1, BORDER),
    )


def glass_button(icon, on_click, tooltip=None, size=38, blur=True):
    """Кругла напівпрозора кнопка поверх зображень."""
    c = ft.Container(
        ft.Icon(icon, size=18, color=TEXT),
        width=size, height=size, border_radius=size / 2,
        bgcolor=ft.Colors.with_opacity(0.45, "#000000"),
        border=ft.Border.all(1, BORDER),
        blur=ft.Blur(12, 12) if blur else None,  # на телефоні без розмиття — дорого для GPU
        alignment=ft.Alignment.CENTER,
        tooltip=tooltip, on_click=on_click,
        animate_scale=FAST, scale=1,
    )

    def hover(e):
        c.scale = 1.08 if hovered(e) else 1
        c.update()

    c.on_hover = hover
    return c


def poster_image(src, radius=0, cache_width=None, **kw):
    """Постер з фоном-заглушкою, поки картинка вантажиться (або якщо її нема).
    cache_width — декодувати картинку під розмір картки, а не в повні 1200 px:
    інакше кеш зображень Flutter переповнюється і постери «зникають» при прокрутці."""
    fallback = ft.Container(
        ft.Icon(ft.Icons.MOVIE_FILTER_ROUNDED, size=36, color=SURFACE_3),
        alignment=ft.Alignment.CENTER, bgcolor=SURFACE_2, expand=True)
    if not src:
        return ft.Container(fallback, border_radius=radius, **kw)
    return ft.Container(
        ft.Image(src=src, fit=ft.BoxFit.COVER, gapless_playback=True,
                 error_content=fallback, expand=True, cache_width=cache_width),
        bgcolor=SURFACE_2, border_radius=radius,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS, **kw,
    )


def appear(control):
    """Готує контрол до появи (fade + легкий підйом). Викликати reveal() після update()."""
    if LITE:
        return control
    control.opacity = 0
    control.offset = ft.Offset(0, 0.04)
    control.animate_opacity = APPEAR
    control.animate_offset = APPEAR
    return control


def reveal(control):
    control.opacity = 1
    control.offset = ft.Offset(0, 0)
