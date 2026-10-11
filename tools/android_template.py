"""Готує шаблон збірки Flet для Android із запитом високої частоти оновлення екрана.

Багато Android-телефонів (Samsung, Xiaomi тощо) тримають застосунки на 60 Гц, поки ті самі
не попросять вищу частоту, а Flutter цього не робить. Скрипт завантажує стандартний шаблон
Flet потрібної версії й замінює MainActivity: та обирає режим дисплея з найбільшою частотою
(за тієї ж роздільності) — 90/120/144 Гц, якщо екран їх підтримує.

Використання (CI):  python tools/android_template.py <тека>  →  flet build apk --template <тека>/build
"""
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

import flet.version

TEMPLATE_URL = "https://github.com/flet-dev/flet/releases/download/v{v}/flet-build-template.zip"

MAIN_ACTIVITY = '''package {{ cookiecutter.org_name_2 }}.{{ cookiecutter.package_name }}

import android.os.Build
import android.os.Bundle
import io.flutter.embedding.android.FlutterFragmentActivity

class MainActivity : FlutterFragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestHighRefreshRate()
    }

    override fun onResume() {
        super.onResume()
        requestHighRefreshRate()
    }

    /** Найвища частота оновлення за поточної роздільності (інакше система може тримати 60 Гц). */
    private fun requestHighRefreshRate() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return
        @Suppress("DEPRECATION")
        val display = (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) display
                       else windowManager.defaultDisplay) ?: return
        val current = display.mode
        val best = display.supportedModes
            .filter { it.physicalWidth == current.physicalWidth && it.physicalHeight == current.physicalHeight }
            .maxByOrNull { it.refreshRate } ?: return
        val params = window.attributes
        if (params.preferredDisplayModeId != best.modeId) {
            params.preferredDisplayModeId = best.modeId
            window.attributes = params
        }
    }
}
'''


def main(out_dir: str) -> None:
    out = Path(out_dir)
    url = TEMPLATE_URL.format(v=flet.version.flet_version)
    print(f"Завантажую {url}")
    with urllib.request.urlopen(url) as resp:
        zipfile.ZipFile(io.BytesIO(resp.read())).extractall(out)
    activities = list(out.glob("build/*/android/app/src/main/kotlin/*/MainActivity.kt"))
    if len(activities) != 1:
        sys.exit(f"MainActivity.kt не знайдено однозначно: {activities}")
    activities[0].write_text(MAIN_ACTIVITY, encoding="utf-8")
    print(f"Оновлено {activities[0]}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "android_template")
