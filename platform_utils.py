"""
platform_utils.py
=================
Detección de plataforma y rutas de datos compartidas entre la app (Activity)
y el servicio de recordatorios (proceso Python independiente en Android).
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata

APP_NAME = "FenoRubus"
APP_SLUG = "fenorubus"


def is_android() -> bool:
    return "ANDROID_ARGUMENT" in os.environ or "ANDROID_PRIVATE" in os.environ \
        or hasattr(sys, "getandroidapilevel")


IS_ANDROID = is_android()


def android_context():
    """Context Android válido tanto desde la Activity como desde un Service p4a."""
    from jnius import autoclass, cast  # type: ignore
    try:
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        if activity is not None:
            return cast("android.content.Context", activity)
    except Exception:
        pass
    service = autoclass("org.kivy.android.PythonService").mService
    return cast("android.content.Context", service)


def android_api_level() -> int:
    if not IS_ANDROID:
        return 0
    from jnius import autoclass  # type: ignore
    return int(autoclass("android.os.Build$VERSION").SDK_INT)


def get_data_dir() -> str:
    """
    Directorio persistente y privado de la app.

    * Android: Context.getFilesDir() (mismo valor para Activity y Service).
    * Escritorio: $FENORUBUS_DATA o ~/.local/share/fenorubus (o %APPDATA%).
    """
    override = os.environ.get("FENORUBUS_DATA")
    if override:
        path = override
    elif IS_ANDROID:
        path = android_context().getFilesDir().getAbsolutePath()
    elif sys.platform.startswith("win"):
        path = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
    elif sys.platform == "darwin":
        path = os.path.expanduser(f"~/Library/Application Support/{APP_NAME}")
    else:
        base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
        path = os.path.join(base, APP_SLUG)
    os.makedirs(path, exist_ok=True)
    return path


def data_subdir(*parts: str) -> str:
    path = os.path.join(get_data_dir(), *parts)
    os.makedirs(path, exist_ok=True)
    return path


def resource_path(*parts: str) -> str:
    """Ruta a recursos empaquetados junto al código (templates, modelos)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), *parts)


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower() or "informe"
