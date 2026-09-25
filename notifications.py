"""
notifications.py
================
Recordatorios semanales de muestreo (por defecto: viernes 09:00).

Android
-------
* ``AlarmManager.setExactAndAllowWhileIdle`` (alarma exacta, despierta el
  equipo aun en Doze) programa un ``PendingIntent`` hacia el servicio
  Python ``ServiceReminder`` declarado en ``buildozer.spec``
  (``services = Reminder:service/reminder_service.py:foreground:...``).
* El servicio (proceso ``:service_reminder``) publica la notificación del
  canal «Muestreo», re-programa la siguiente alarma y termina. Funciona con
  la app cerrada.
* Si el sistema no concede alarmas exactas (Android 12+ con
  ``SCHEDULE_EXACT_ALARM`` revocado) se usa ``setAndAllowWhileIdle``
  (ventana inexacta) y la UI ofrece abrir el ajuste del sistema.
* Tras reiniciar el teléfono Android borra las alarmas: la app las vuelve a
  programar en cada inicio (``ReminderManager.apply``).

Escritorio / respaldo
---------------------
``ReminderManager.check_due`` se evalúa cada minuto con la app abierta y
lanza una notificación Plyer (y un aviso en la UI).
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass

import phenology as ph
from platform_utils import IS_ANDROID, android_api_level, android_context

SERVICE_CLASS = "org.rubus.fenorubus.ServiceReminder"
CHANNEL_ID = "fenorubus_muestreo"
CHANNEL_NAME = "Recordatorios de muestreo"
REQUEST_CODE = 7091
NOTIFICATION_ID = 7092

FREQUENCIES = {1: "Semanal", 2: "Cada 2 semanas", 3: "Cada 3 semanas", 4: "Cada 4 semanas"}


@dataclass
class ReminderConfig:
    enabled: bool = True
    weekday: int = 4          # 0 = lunes ... 4 = viernes
    hour: int = 9
    minute: int = 0
    every_weeks: int = 1
    anchor: str | None = None  # fecha ISO del primer disparo (alineación de frecuencia)

    def describe(self) -> str:
        if not self.enabled:
            return "Recordatorios desactivados"
        freq = FREQUENCIES.get(self.every_weeks, f"Cada {self.every_weeks} semanas")
        return f"{freq}, {ph.DIAS[self.weekday]} a las {self.hour:02d}:{self.minute:02d}"


def next_trigger(cfg: ReminderConfig, now: _dt.datetime) -> _dt.datetime:
    """Próximo disparo estrictamente posterior a `now` respetando día, hora y frecuencia."""
    days_ahead = (cfg.weekday - now.weekday()) % 7
    candidate = _dt.datetime.combine(now.date() + _dt.timedelta(days=days_ahead),
                                     _dt.time(cfg.hour, cfg.minute))
    if candidate <= now:
        candidate += _dt.timedelta(days=7)
    step = max(1, int(cfg.every_weeks))
    if step > 1 and cfg.anchor:
        anchor = _dt.date.fromisoformat(cfg.anchor)
        # Mismo día de semana que el ancla y múltiplo de `step` semanas desde ella.
        while ((candidate.date() - anchor).days // 7) % step != 0:
            candidate += _dt.timedelta(days=7)
    return candidate


class ReminderManager:
    SETTING = "reminder"

    def __init__(self, db):
        self.db = db

    # ----------------------------------------------------------- config
    def config(self) -> ReminderConfig:
        raw = self.db.get_setting(self.SETTING) or {}
        cfg = ReminderConfig()
        for k, v in raw.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg

    def save(self, cfg: ReminderConfig, now: _dt.datetime | None = None) -> _dt.datetime | None:
        now = now or _dt.datetime.now()
        cfg.anchor = None
        first = next_trigger(cfg, now)
        cfg.anchor = first.date().isoformat()
        self.db.set_setting(self.SETTING, asdict(cfg))
        self.db.log("update", "reminder", None, cfg.describe())
        return self.apply(now)

    def next_time(self, now: _dt.datetime | None = None) -> _dt.datetime | None:
        cfg = self.config()
        if not cfg.enabled:
            return None
        return next_trigger(cfg, now or _dt.datetime.now())

    def apply(self, now: _dt.datetime | None = None) -> _dt.datetime | None:
        """Programa (o cancela) la próxima alarma según la configuración."""
        nxt = self.next_time(now)
        self.db.set_setting("reminder_next", nxt.isoformat() if nxt else None)
        if IS_ANDROID:
            try:
                if nxt:
                    exact = android_schedule(nxt)
                    self.db.set_setting("reminder_exact", exact)
                else:
                    android_cancel()
            except Exception as exc:  # no bloquear la app por un fallo del sistema
                self.db.log("error", "reminder", None, repr(exc))
        return nxt

    # ------------------------------------------------ respaldo en la app
    def check_due(self, now: _dt.datetime | None = None) -> bool:
        """True si había un recordatorio vencido (lo notifica y programa el siguiente)."""
        now = now or _dt.datetime.now()
        cfg = self.config()
        raw = self.db.get_setting("reminder_next")
        if not cfg.enabled or not raw:
            return False
        if now < _dt.datetime.fromisoformat(raw):
            return False
        if not IS_ANDROID:  # en Android el servicio ya notificó
            notify(*reminder_text(self.db, now.date()))
        self.apply(now)
        return True


def reminder_text(db, today: _dt.date | None = None) -> tuple[str, str]:
    today = today or _dt.date.today()
    week = db.current_week(today)
    pending = 0
    for row in db.week_overview(week["id"]):
        photos = row["photos"]
        obs = row["observation"]
        if len(photos) < 2 or not obs or obs["bbch_code"] is None:
            pending += 1
    title = "Muestreo fenológico · frambueso"
    body = (f"Semana {week['week_number']} ({week['label']}): "
            f"{pending} variedad(es) con registro pendiente.")
    return title, body


# ===========================================================================
# Plataforma
# ===========================================================================
def notify(title: str, message: str) -> None:
    if IS_ANDROID:
        android_post_notification(title, message)
        return
    try:
        from plyer import notification  # type: ignore
        notification.notify(title=title, message=message, app_name="FenoRubus", timeout=10)
    except Exception:
        print(f"[recordatorio] {title}: {message}")


def _pending_intent(ctx):
    from jnius import autoclass  # type: ignore
    PendingIntent = autoclass("android.app.PendingIntent")
    Service = autoclass(SERVICE_CLASS)
    intent = Service.getDefaultIntent(ctx, "", "FenoRubus", "Recordatorio de muestreo", "alarm")
    flags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
    if android_api_level() >= 26:
        return PendingIntent.getForegroundService(ctx, REQUEST_CODE, intent, flags)
    return PendingIntent.getService(ctx, REQUEST_CODE, intent, flags)


def can_schedule_exact() -> bool:
    if not IS_ANDROID or android_api_level() < 31:
        return True
    from jnius import autoclass  # type: ignore
    Context = autoclass("android.content.Context")
    am = android_context().getSystemService(Context.ALARM_SERVICE)
    return bool(am.canScheduleExactAlarms())


def request_exact_alarm_permission() -> None:
    """Abre el ajuste del sistema 'Alarmas y recordatorios' (Android 12+)."""
    if not IS_ANDROID or android_api_level() < 31:
        return
    from jnius import autoclass  # type: ignore
    Intent = autoclass("android.content.Intent")
    Settings = autoclass("android.provider.Settings")
    Uri = autoclass("android.net.Uri")
    ctx = android_context()
    intent = Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
                    Uri.parse("package:" + ctx.getPackageName()))
    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    ctx.startActivity(intent)


def android_schedule(when: _dt.datetime) -> bool:
    from jnius import autoclass  # type: ignore
    Context = autoclass("android.content.Context")
    AlarmManager = autoclass("android.app.AlarmManager")
    ctx = android_context()
    am = ctx.getSystemService(Context.ALARM_SERVICE)
    pi = _pending_intent(ctx)
    ts = int(when.timestamp() * 1000)
    exact = can_schedule_exact()
    if exact:
        am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, ts, pi)
    else:
        am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, ts, pi)
    return exact


def android_cancel() -> None:
    from jnius import autoclass  # type: ignore
    Context = autoclass("android.content.Context")
    ctx = android_context()
    am = ctx.getSystemService(Context.ALARM_SERVICE)
    am.cancel(_pending_intent(ctx))


def android_post_notification(title: str, message: str) -> None:
    from jnius import autoclass, cast  # type: ignore
    Context = autoclass("android.content.Context")
    String = autoclass("java.lang.String")
    Intent = autoclass("android.content.Intent")
    PendingIntent = autoclass("android.app.PendingIntent")
    Builder = autoclass("android.app.Notification$Builder")
    BigText = autoclass("android.app.Notification$BigTextStyle")
    ctx = android_context()
    nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE)
    api = android_api_level()

    def cs(text):
        return cast("java.lang.CharSequence", String(text))

    if api >= 26:
        NotificationChannel = autoclass("android.app.NotificationChannel")
        NotificationManager = autoclass("android.app.NotificationManager")
        channel = NotificationChannel(CHANNEL_ID, cs(CHANNEL_NAME),
                                      NotificationManager.IMPORTANCE_HIGH)
        channel.setDescription("Aviso semanal para registrar fotos y estado BBCH")
        nm.createNotificationChannel(channel)
        builder = Builder(ctx, CHANNEL_ID)
    else:
        builder = Builder(ctx)

    launch = Intent(ctx, autoclass("org.kivy.android.PythonActivity"))
    launch.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP)
    content = PendingIntent.getActivity(ctx, 0, launch,
                                        PendingIntent.FLAG_UPDATE_CURRENT
                                        | PendingIntent.FLAG_IMMUTABLE)
    builder.setSmallIcon(ctx.getApplicationInfo().icon)
    builder.setContentTitle(cs(title))
    builder.setContentText(cs(message))
    builder.setStyle(BigText().bigText(cs(message)))
    builder.setContentIntent(content)
    builder.setAutoCancel(True)
    nm.notify(NOTIFICATION_ID, builder.build())
