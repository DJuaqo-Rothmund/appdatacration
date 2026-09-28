"""
crashguard.py
=============
Red de seguridad: un error de Python en la interfaz o en un hilo de fondo ya no
cierra la app. Se registra en «logs/errores.log» (visible en Ajustes › Registro de
errores) y se avisa con un mensaje breve.
"""
from __future__ import annotations

import datetime as _dt
import os
import threading
import traceback

from platform_utils import app_version, data_subdir

MAX_BYTES = 200_000
_notify = None   # función(texto) para avisar en pantalla


def log_path() -> str:
    return os.path.join(data_subdir("logs"), "errores.log")


def record(exc: BaseException, where: str = "") -> str:
    """Guarda el error con su traza y devuelve un resumen de una línea."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    summary = f"{type(exc).__name__}: {exc}"[:200]
    try:
        path = log_path()
        if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"=== {_dt.datetime.now():%Y-%m-%d %H:%M:%S} · v{app_version()} · {where}\n"
                    f"{tb}\n")
    except OSError:
        pass
    return summary


def entries(limit: int = 20) -> list[str]:
    """Últimos errores (más reciente primero), cada uno con su traza abreviada."""
    try:
        with open(log_path(), encoding="utf-8") as f:
            blocks = [b.strip() for b in f.read().split("=== ") if b.strip()]
    except OSError:
        return []
    out = []
    for b in reversed(blocks[-limit:]):
        head, _, body = b.partition("\n")
        lines = [ln for ln in body.splitlines() if ln.strip()]
        # cabecera + últimas líneas de la traza (archivo:línea y el error)
        out.append(head + "\n" + "\n".join(lines[-6:]))
    return out


def clear() -> None:
    for p in (log_path(), log_path() + ".1"):
        try:
            os.remove(p)
        except OSError:
            pass


def install(notify=None) -> None:
    """Instala los manejadores de Kivy (bucle de la interfaz) y de hilos."""
    global _notify
    _notify = notify
    from kivy.base import ExceptionHandler, ExceptionManager

    class _Guard(ExceptionHandler):
        def handle_exception(self, exc):
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                return ExceptionManager.RAISE
            summary = record(exc, "interfaz")
            if _notify:
                try:
                    from kivy.clock import Clock
                    Clock.schedule_once(lambda *_: _notify(
                        f"Se evitó un cierre por un error: {summary[:90]}"), 0)
                except Exception:  # noqa: BLE001
                    pass
            return ExceptionManager.PASS

    ExceptionManager.add_handler(_Guard())

    def thread_hook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        record(args.exc_value, f"hilo {getattr(args.thread, 'name', '?')}")

    threading.excepthook = thread_hook
