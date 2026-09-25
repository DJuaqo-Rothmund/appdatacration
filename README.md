# FenoRubus · Cuaderno de campo digital para frambueso (*Rubus idaeus*)

Aplicación Android (Python + Kivy/KivyMD) para el **seguimiento fenológico y biométrico de ensayos
varietales de frambueso**, pensada para trabajar en terreno **sin conexión** (offline-first):

* Registro semanal por **Semanas de Muestreo** (Semana 1 = *semana del 7 de septiembre*).
* **Registro fotográfico dual** por variedad y semana: canopia/planta completa + detalle/macro
  (cámara o galería).
* **Sugerencia automática del estado BBCH** con IA local (Edge AI) sobre la foto de detalle.
* **Módulo de calibración de la IA protegido por PIN** (`1234` por defecto): documentos técnicos,
  etiquetado de fotos (few-shot k-NN), evaluación y re-entrenamiento.
* Catálogo dinámico de variedades con **parámetros biométricos** y campos personalizados.
* **Recordatorios semanales** (viernes por defecto) con alarmas exactas de Android.
* **Informes HTML autocontenidos** (imágenes en Base64) o **ZIP portátil**, listos para WhatsApp o correo.
* Trazabilidad: bitácora de cambios (`audit_log`) y respaldo de la base SQLite.

---

## 1. Arquitectura

```
main.py                  Punto de entrada, App KivyMD, navegación, servicios compartidos
database.py              Esquema SQLite + CRUD (variedades, métricas, campos extra, semanas,
                         observaciones, fotos, escala BBCH, referencias IA, documentos, bitácora)
ai_classifier.py         Extractor de características, k-NN coseno, priors agronómicos,
                         calibración (documentos, referencias, evaluación LOO) y PinGuard (PIN 1234)
reporter.py              Generador Jinja2 de los 4 informes HTML (Base64) / ZIP portátil
notifications.py         Recordatorios: AlarmManager exacto -> servicio p4a -> notificación
phenology.py             Escala BBCH del frambueso, semanas de muestreo, calendario esperado
android_bridge.py        Cámara (MediaStore), galería (Photo Picker), documentos, compartir, permisos
platform_utils.py        Detección de plataforma y rutas de datos (app y servicio)
service/reminder_service.py   Servicio Android que publica el aviso y re-programa la alarma
ui/layout.kv             Layout KivyMD (estética cuaderno de campo: oliva, pizarra, blanco hueso)
ui/screens.py            Lógica de pantallas: Muestreo, Variedades, Informes, Ajustes,
                         Registro (variedad × semana), Ficha de variedad, Calibración IA
ui/theme.py              Paleta y tema
templates/*.html         Plantillas de informes (CSS responsivo, modo claro/oscuro, impresión)
tools/demo_data.py       Datos de demostración con fotos sintéticas (pruebas en PC)
tools/export_mobilenet_tflite.py   Exportador opcional de MobileNetV3-Small a TFLite
tests/test_core.py       Pruebas del núcleo (pytest)
buildozer.spec           Compilación Android (permisos, servicio, dependencias)
```

### Modelo de datos (SQLite)

| Tabla | Contenido |
|---|---|
| `varieties` | Catálogo (10 variedades iniciales: Código 11, 31, 24, 55, 81, Cascade Harvest, Lagorai, Meeker, Regina, Wakefield). Archivado suave o eliminación definitiva. |
| `variety_metrics` | Por variedad y temporada: rendimiento histórico, proyección, cañas basales, laterales (con unidades). |
| `custom_fields` | Parámetros llave-valor-unidad (ej. «Diámetro de caña (mm)», «Grados Brix»). |
| `sampling_weeks` | Semana N, fecha de inicio y etiqueta («Semana del 14 de septiembre»). |
| `observations` | Variedad × semana: BBCH, sugerencia IA (código, confianza, detalle), aceptada/corregida, notas. |
| `photos` | Foto `canopy` y `detail` por observación (origen cámara/galería, fecha). |
| `bbch_stages` | Escala BBCH editable (base + claves importadas de documentos). |
| `ai_references` | Embeddings etiquetados para el k-NN (few-shot). |
| `documents`, `settings`, `audit_log` | Documentos técnicos, preferencias, bitácora. |

### IA local (Edge AI)

1. **Extractor de descriptores** (`HandcraftedExtractor`, solo numpy + Pillow, ~277 dimensiones):
   histogramas HSV (imagen completa y centro), fracciones de colores con sentido agronómico
   (blanco pétalo, rosado, rojo frambuesa, verde, pardo, amarillo), HOG simplificado, magnitud de
   gradiente y LBP uniforme (textura de drupéolas y yemas).
   *Opcional:* `TFLiteExtractor` con MobileNetV3-Small (ver sección 5).
2. **k-NN por similitud coseno** sobre la base de referencia que se construye etiquetando fotos reales
   en el módulo de calibración (few-shot learning, sin re-entrenar redes).
3. **Priors agronómicos** combinados en un modelo log-lineal, que permiten sugerir desde el primer día:
   * *temporal*: la variedad no retrocede respecto de su último BBCH y avanza a la tasa del calendario;
   * *cromático*: flor blanca → 6x, rojo → 8x, pardo → 0x/9x…;
   * *textual*: coincidencia de las notas de campo con las claves de cada estadio (enriquecidas con
     los documentos cargados).

La sugerencia (top-3 + confianza + explicación) se precarga en el campo **editable** de estado
fenológico; el evaluador la acepta o corrige, y la app registra si fue aceptada o corregida.

---

## 2. Ejecutar y probar en PC (escritorio)

Requisitos: Python 3.10 o 3.11.

```bash
git clone <repo> fenorubus && cd fenorubus
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-desktop.txt

# (opcional) datos de demostración: 10 variedades × 14 semanas con fotos sintéticas
FENORUBUS_DATA=./demo-data python tools/demo_data.py --weeks 14

# ejecutar la app (ventana 412×860, tamaño de teléfono)
FENORUBUS_DATA=./demo-data python main.py      # con datos de demostración
python main.py                                   # base real en ~/.local/share/fenorubus

# pruebas del núcleo (BD, semanas, IA, PIN, recordatorios, informes)
pytest -q
```

En Windows (PowerShell) defina la variable con `$env:FENORUBUS_DATA="demo-data"`.

En escritorio no hay cámara integrada: los botones *Cámara* y *Galería* abren un selector de archivos,
los informes se abren en el navegador y los recordatorios se muestran con Plyer mientras la app está abierta.

**Recorrido sugerido:** Muestreo → tocar una variedad → subir la *Foto 2 · Detalle* → ver la sugerencia
BBCH → Guardar. Luego *Informes* → *Matriz comparativa global* → HTML. El ícono 🧠 de la barra
superior (o *Ajustes → Módulo de calibración*) pide el PIN **1234**.

---

## 3. Compilar el APK con Buildozer

### Opción A — Linux (Ubuntu 22.04 / 24.04 o WSL2)

```bash
sudo apt update
sudo apt install -y git zip unzip openjdk-17-jdk python3-pip python3-venv autoconf libtool \
    pkg-config zlib1g-dev libncurses-dev cmake libffi-dev libssl-dev automake build-essential \
    ccache lld
python3 -m venv ~/bdenv && source ~/bdenv/bin/activate
pip install --upgrade pip "cython<3.1" buildozer==1.6.0 setuptools

cd fenorubus
buildozer -v android debug          # 1ª vez: 20-40 min (descarga SDK/NDK y compila numpy/pillow)
# resultado: bin/fenorubus-1.0.0-arm64-v8a_armeabi-v7a-debug.apk
```

Instalar en un teléfono conectado por USB (con depuración USB activada):

```bash
buildozer android deploy run logcat | grep -iE "python|fenorubus"
# o bien:  adb install -r bin/*.apk
```

### Opción B — Google Colab

```python
# Celda 1: dependencias del sistema
!sudo apt-get update -qq
!sudo apt-get install -y -qq openjdk-17-jdk autoconf libtool pkg-config zlib1g-dev \
    libncurses-dev cmake libffi-dev libssl-dev automake zip unzip lld > /dev/null
!pip install -q "cython<3.1" buildozer==1.6.0 setuptools
```

```python
# Celda 2: código fuente (clonar el repo o subir un .zip del proyecto)
!git clone https://github.com/DJuaqo-Rothmund/appdatacration.git
%cd appdatacration
# alternativa: from google.colab import files; files.upload()  y luego  !unzip -o proyecto.zip
```

```python
# Celda 3: compilar (Colab corre como root; buildozer.spec ya trae warn_on_root = 0)
!yes | buildozer -v android debug
```

```python
# Celda 4: descargar el APK
from google.colab import files
import glob
files.download(glob.glob("bin/*.apk")[0])
```

> Si la sesión de Colab se reinicia se pierde `.buildozer/`; para compilaciones repetidas conviene
> montar Google Drive y trabajar dentro de `/content/drive/MyDrive/...`.

### Versión de lanzamiento (firmada)

```bash
buildozer android release           # genera .aab (android.release_artifact = aab)
# Firmar con su keystore: variables P4A_RELEASE_KEYSTORE, P4A_RELEASE_KEYSTORE_PASSWD,
# P4A_RELEASE_KEYALIAS, P4A_RELEASE_KEYALIAS_PASSWD
```

---

## 4. Permisos y comportamiento en Android

| Permiso | Uso |
|---|---|
| `CAMERA` | Captura directa (la foto se escribe vía MediaStore en *Imágenes/FenoRubus* y se copia al almacenamiento privado). |
| `READ_MEDIA_IMAGES` (13+), `READ/WRITE_EXTERNAL_STORAGE` (≤12 / ≤9) | Galería y guardado local. En Android 13+ se usa el *Photo Picker* del sistema. |
| `POST_NOTIFICATIONS` | Aviso semanal (se solicita al iniciar en Android 13+). |
| `SCHEDULE_EXACT_ALARM`, `USE_EXACT_ALARM`, `WAKE_LOCK` | Alarma exacta aun en modo Doze. |
| `FOREGROUND_SERVICE` | Servicio `Reminder` (tipo `shortService`) que publica el aviso con la app cerrada. |

* Si el sistema revoca las alarmas exactas, *Ajustes* muestra **«Permitir alarmas exactas»**, que abre el
  ajuste del sistema. **Quite `USE_EXACT_ALARM` si va a publicar en Google Play.**
* Android borra las alarmas al reiniciar el teléfono: la app las vuelve a programar cada vez que se abre.
* Los informes se copian a *Descargas/FenoRubus* y se comparten con el menú del sistema (WhatsApp, Gmail, Drive…).

---

## 5. (Opcional) Extractor MobileNetV3 con TFLite

```bash
pip install tensorflow
python tools/export_mobilenet_tflite.py      # crea assets/models/*.tflite y model.json
```

Agregue `tflite-runtime` a `requirements` en `buildozer.spec` y recompile. La app detecta el modelo
automáticamente; en *Calibración IA → Modelo → Re-entrenar* se recalculan las referencias con el nuevo
extractor. Sin el modelo, la app usa el extractor liviano de numpy (sin dependencias nativas extra).

---

## 6. Uso del módulo de calibración (PIN `1234`)

* **Documentos:** cargue PDF/TXT con claves fenológicas. Se detectan patrones como
  `BBCH 65: Plena floración…` o `65 - …`; los estadios nuevos se agregan a la escala y los existentes
  se enriquecen con palabras clave (usadas para interpretar las notas de campo).
* **Etiquetado:** toque una foto de detalle y asigne su estado real → se agrega a la base de referencia.
  *Agregar todas las fotos ya etiquetadas* usa los estados confirmados en los registros.
  *Aprendizaje continuo* agrega automáticamente cada registro guardado.
* **Modelo:** referencias por estadio, **validación cruzada leave-one-out** (exactitud por código y por
  estadio principal), re-entrenamiento y cambio de PIN (hash SHA-256 con sal; bloqueo de 30 s tras 5 intentos).

Recomendación: 5-10 fotos de detalle bien etiquetadas por estadio principal (yema, hoja, botón,
flor, fruto verde, maduración) mejoran notablemente las sugerencias.

---

## 7. Escala BBCH incluida (frambueso)

00-09 desarrollo de yemas · 10-19 hojas · 31-39 brotes/laterales · 51-59 aparición floral ·
60-69 floración · 71-79 desarrollo del fruto · 81-89 maduración · 91-97 senescencia
(Meier, 2001, adaptada a *Rubus idaeus*; editable desde documentos técnicos).
El calendario esperado usado como prior (`phenology.EXPECTED_CALENDAR`) corresponde a frambueso
floricane en la zona centro-sur de Chile y puede ajustarse a cada localidad.
