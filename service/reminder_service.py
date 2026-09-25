"""
Servicio Android «Reminder» (proceso :service_reminder).

Lo inicia la alarma exacta programada por ``notifications.android_schedule``.
Publica la notificación de muestreo, programa la siguiente alarma y termina
(el servicio de primer plano de p4a se detiene al terminar este script).
"""
import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database, default_db_path  # noqa: E402
from notifications import ReminderManager, notify, reminder_text  # noqa: E402


def main() -> None:
    db = Database(default_db_path())
    try:
        manager = ReminderManager(db)
        if manager.config().enabled:
            notify(*reminder_text(db))
            db.log("fire", "reminder", None, "Notificación semanal publicada")
        # Margen de 1 h: evita re-disparar si la alarma llegó unos segundos antes.
        manager.apply(_dt.datetime.now() + _dt.timedelta(hours=1))
    finally:
        db.close()


if __name__ == "__main__":
    main()
