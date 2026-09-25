[app]

# (str) Title of your application
title = FenoRubus

# (str) Package name / domain  ->  org.rubus.fenorubus
# (notifications.SERVICE_CLASS depende de este nombre: org.rubus.fenorubus.ServiceReminder)
package.name = fenorubus
package.domain = org.rubus

# (str) Source code where the main.py live
source.dir = .

# (list) Source files to include
source.include_exts = py,png,jpg,kv,atlas,html,json,tflite,txt

# (list) Directories / files to exclude
source.exclude_dirs = tests, bin, .buildozer, .git, __pycache__, .pytest_cache
source.exclude_patterns = tools/demo_data.py, tools/export_mobilenet_tflite.py, README.md

# (str) Application versioning
version = 1.0.0

# (list) Application requirements
# - kivymd se fija en 1.2.0 (API estable usada por ui/).
# - numpy y pillow tienen receta p4a; jinja2/markupsafe/pypdf/plyer son puros Python.
# - Para usar el extractor MobileNetV3 (TFLite) agregar: tflite-runtime
#   y copiar el modelo en assets/models/ (ver tools/export_mobilenet_tflite.py).
requirements = python3,kivy==2.3.1,kivymd==1.2.0,pillow,numpy,jinja2,markupsafe,plyer,pyjnius,android,pypdf,sqlite3

# (str) Presplash / icon
presplash.filename = %(source.dir)s/assets/presplash.png
icon.filename = %(source.dir)s/assets/icon.png
android.presplash_color = #F4F1E8

# (list) Supported orientations
orientation = portrait

# (list) Services: servicio en primer plano de corta duración que publica el
# recordatorio semanal cuando lo dispara AlarmManager (funciona con la app cerrada).
services = Reminder:service/reminder_service.py:foreground:foregroundServiceType=shortService

fullscreen = 0

#
# Android specific
#

# (list) Permissions
# - CAMERA: captura directa.
# - READ_EXTERNAL_STORAGE / WRITE_EXTERNAL_STORAGE (<= Android 9-12) y
#   READ_MEDIA_IMAGES (Android 13+): galería y guardado local.
# - POST_NOTIFICATIONS, WAKE_LOCK, SCHEDULE_EXACT_ALARM: alarmas semanales precisas.
# - USE_EXACT_ALARM (Android 13+): concede alarmas exactas sin ajuste manual.
#   ¡Quitarla si la app se publicará en Google Play (política restringida a apps de alarma/calendario)!
# - FOREGROUND_SERVICE: servicio de recordatorio.
android.permissions = CAMERA,
    (name=android.permission.READ_EXTERNAL_STORAGE;maxSdkVersion=32),
    (name=android.permission.WRITE_EXTERNAL_STORAGE;maxSdkVersion=28),
    READ_MEDIA_IMAGES,
    POST_NOTIFICATIONS,
    WAKE_LOCK,
    SCHEDULE_EXACT_ALARM,
    USE_EXACT_ALARM,
    FOREGROUND_SERVICE,
    VIBRATE

# (int) Target Android API / minimum API
android.api = 34
android.minapi = 26

# (bool) Accept SDK license automatically (necesario en Colab / CI)
android.accept_sdk_license = True

# (list) The Android archs to build for
android.archs = arm64-v8a, armeabi-v7a

# (bool) Enable AndroidX support
android.enable_androidx = True

# (bool) Allow backup (datos de campo): se permite el respaldo automático de Android.
android.allow_backup = True

# (str) Bootstrap
p4a.bootstrap = sdl2

# (str) The format used to package the app for debug/release mode
android.debug_artifact = apk
android.release_artifact = aab

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
# En Google Colab se ejecuta como root: se desactiva la advertencia interactiva.
warn_on_root = 0
