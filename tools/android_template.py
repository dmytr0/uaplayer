"""Готує шаблон збірки Flet для Android: висока частота екрана й встановлення оновлень.

Багато Android-телефонів (Samsung, Xiaomi тощо) тримають застосунки на 60 Гц, поки ті самі
не попросять вищу частоту, а Flutter цього не робить. Скрипт завантажує стандартний шаблон
Flet потрібної версії й замінює MainActivity: та обирає режим дисплея з найбільшою частотою
(за тієї ж роздільності) — 90/120/144 Гц, якщо екран їх підтримує.

Також MainActivity відкриває системне вікно встановлення для завантаженого застосунком
оновлення (посилання uafixupdate://install, файл cache/update.apk).

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

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.core.content.FileProvider
import io.flutter.embedding.android.FlutterFragmentActivity
import java.io.File

class MainActivity : FlutterFragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestHighRefreshRate()
        handleInstallIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        requestHighRefreshRate()
    }

    override fun onNewIntent(intent: Intent) {
        if (handleInstallIntent(intent)) return
        super.onNewIntent(intent)
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

    /**
     * uafixupdate://install — Python-частина завантажила оновлення в cache/update.apk і просить
     * відкрити системне вікно встановлення. Береться лише цей фіксований файл із кешу самого
     * застосунку, тож стороннє посилання не змусить встановити щось інше.
     */
    private fun handleInstallIntent(intent: Intent?): Boolean {
        val data = intent?.data ?: return false
        if (data.scheme != "uafixupdate") return false
        val apk = File(cacheDir, "update.apk")
        if (!apk.isFile) return true
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && !packageManager.canRequestPackageInstalls()) {
            // спершу дозволити цьому застосунку встановлювати програми
            startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                                 Uri.parse("package:$packageName")))
            return true
        }
        val uri = FileProvider.getUriForFile(this, "$packageName.provider", apk)
        startActivity(Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        })
        return true
    }
}
'''

# посилання uafixupdate://install відкриває саме цей застосунок (див. handleInstallIntent)
INSTALL_INTENT_FILTER = '''
            <intent-filter>
                <action android:name="android.intent.action.VIEW" />
                <category android:name="android.intent.category.DEFAULT" />
                <category android:name="android.intent.category.BROWSABLE" />
                <data android:scheme="uafixupdate" />
            </intent-filter>'''
LAUNCHER_FILTER_END = "<!-- Google TV -->\n            </intent-filter>"


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

    manifests = list(out.glob("build/*/android/app/src/main/AndroidManifest.xml"))
    if len(manifests) != 1:
        sys.exit(f"AndroidManifest.xml не знайдено однозначно: {manifests}")
    manifest = manifests[0].read_text(encoding="utf-8")
    if LAUNCHER_FILTER_END not in manifest:
        sys.exit("Не знайдено intent-filter лаунчера в AndroidManifest.xml — шаблон змінився")
    manifest = manifest.replace(LAUNCHER_FILTER_END, LAUNCHER_FILTER_END + INSTALL_INTENT_FILTER, 1)
    manifests[0].write_text(manifest, encoding="utf-8")
    print(f"Оновлено {manifests[0]}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "android_template")
