"""
geo.py
======
Ubicación (opcional) de cada muestra.

* `exif_gps(path)`: coordenadas GPS guardadas por la cámara en la foto (EXIF).
* `LocationRequest`: posición actual del teléfono (LocationManager de Android,
  sin librerías extra). Se llama desde el hilo principal de Kivy.
* Utilidades de teselas OpenStreetMap para el mapa (`ui.mapview.MapPicker`),
  con caché en disco para reutilizarlas sin conexión.
"""
from __future__ import annotations

import math
import os
import threading
import urllib.request

from platform_utils import IS_ANDROID, data_subdir

TILE_SIZE = 256
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
USER_AGENT = "PhenoRubus/1.1 (cuaderno fenologico de frambueso; Android)"
DEFAULT_CENTER = (-33.45, -70.66)   # Chile central (vista inicial sin otra referencia)


def fmt(lat: float, lon: float) -> str:
    return f"{lat:.6f}, {lon:.6f}"


def osm_link(lat: float, lon: float, zoom: int = 18) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map={zoom}/{lat:.6f}/{lon:.6f}"


# ------------------------------------------------------------------- EXIF
def _to_deg(value) -> float:
    d, m, s = (float(x) for x in value)
    return d + m / 60 + s / 3600


def exif_gps(path: str) -> tuple[float, float] | None:
    """(lat, lon) de la foto, o None si no tiene ubicación."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            gps = img.getexif().get_ifd(0x8825)
        if not gps or 2 not in gps or 4 not in gps:
            return None
        lat, lon = _to_deg(gps[2]), _to_deg(gps[4])
        if str(gps.get(1, "N")).upper().startswith("S"):
            lat = -lat
        if str(gps.get(3, "E")).upper().startswith("W"):
            lon = -lon
        if abs(lat) < 1e-9 and abs(lon) < 1e-9:
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return lat, lon
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------ teselas OSM
def latlon_to_tile(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    lat = max(min(lat, 85.0511), -85.0511)
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    lr = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lr)) / math.pi) / 2.0 * n
    return x, y


def tile_to_latlon(x: float, y: float, zoom: int) -> tuple[float, float]:
    n = 2 ** zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def tile_path(z: int, x: int, y: int) -> str:
    return os.path.join(data_subdir("tiles", str(z), str(x)), f"{y}.png")


_fetching: set = set()
_lock = threading.Lock()


def fetch_tile(z: int, x: int, y: int) -> str | None:
    """Descarga (una vez) la tesela y devuelve su ruta en caché; None si falla."""
    n = 2 ** z
    if not (0 <= y < n):
        return None
    x %= n
    path = tile_path(z, x, y)
    if os.path.exists(path):
        return path
    key = (z, x, y)
    with _lock:
        if key in _fetching:
            return None
        _fetching.add(key)
    try:
        from drive_backup import _ssl_context
        req = urllib.request.Request(TILE_URL.format(z=z, x=x, y=y),
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as r:
            data = r.read()
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return path
    except Exception:  # noqa: BLE001 (sin red: se muestra la cuadrícula vacía)
        return None
    finally:
        with _lock:
            _fetching.discard(key)


# ---------------------------------------------------- ubicación del teléfono
LOCATION_PERMISSIONS = ["android.permission.ACCESS_FINE_LOCATION",
                        "android.permission.ACCESS_COARSE_LOCATION"]


def request_location_permission(callback) -> None:
    """callback(bool) con el permiso de ubicación concedido o no."""
    if not IS_ANDROID:
        callback(True)
        return
    from android.permissions import check_permission, request_permissions  # type: ignore
    if any(check_permission(p) for p in LOCATION_PERMISSIONS):
        callback(True)
        return
    request_permissions(LOCATION_PERMISSIONS, lambda perms, grants: callback(any(grants)))


class LocationRequest:
    """Posición actual (una sola lectura). callback(lat, lon, precisión_m) o error(msg).

    Android 11+: LocationManager.getCurrentLocation; Android 8-10: requestSingleUpdate.
    Si no llega una posición nueva, se usa la última conocida del teléfono.
    Debe iniciarse en el hilo principal de Kivy (clases resueltas por pyjnius).
    """

    def __init__(self, callback, error):
        self.callback, self.error = callback, error
        self._refs = []
        self._done = False

    def _finish(self, loc):
        from kivy.clock import Clock
        if self._done:
            return
        self._done = True
        if loc is None:
            last = self._last_known()
            if last is None:
                Clock.schedule_once(lambda *_: self.error(
                    "No se obtuvo la ubicación. Active la ubicación del teléfono y reintente "
                    "al aire libre."))
                return
            loc = last
        lat, lon, acc = loc.getLatitude(), loc.getLongitude(), loc.getAccuracy()
        Clock.schedule_once(lambda *_: self.callback(lat, lon, acc))

    def _manager(self):
        from jnius import autoclass  # type: ignore
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        Context = autoclass("android.content.Context")
        return act, act.getSystemService(Context.LOCATION_SERVICE)

    def _last_known(self):
        try:
            _act, lm = self._manager()
            best = None
            for prov in lm.getProviders(True).toArray():
                loc = lm.getLastKnownLocation(prov)
                if loc is not None and (best is None or loc.getTime() > best.getTime()):
                    best = loc
            return best
        except Exception:  # noqa: BLE001
            return None

    def start(self, timeout: float = 30) -> None:
        if not IS_ANDROID:
            self.error("La ubicación GPS está disponible en el teléfono.")
            return
        from jnius import PythonJavaClass, autoclass, java_method  # type: ignore
        from kivy.clock import Clock
        act, lm = self._manager()
        LocationManager = autoclass("android.location.LocationManager")
        enabled = [p for p in ("fused", LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)
                   if lm.getAllProviders().contains(p) and lm.isProviderEnabled(p)]
        if not enabled:
            self.error("La ubicación del teléfono está desactivada. Actívela en Ajustes rápidos.")
            return
        provider = enabled[0]
        me = self
        api = autoclass("android.os.Build$VERSION").SDK_INT
        if api >= 30:
            class Consumer(PythonJavaClass):
                __javainterfaces__ = ["java/util/function/Consumer"]
                __javacontext__ = "app"

                @java_method("(Ljava/lang/Object;)V")
                def accept(self, loc):
                    me._finish(loc)

            cons = Consumer()
            self._refs = [cons]
            lm.getCurrentLocation(provider, None, act.getMainExecutor(), cons)
        else:
            class Listener(PythonJavaClass):
                __javainterfaces__ = ["android/location/LocationListener"]
                __javacontext__ = "app"

                @java_method("(Landroid/location/Location;)V")
                def onLocationChanged(self, loc):  # noqa: N802
                    me._finish(loc)

                @java_method("(Ljava/lang/String;ILandroid/os/Bundle;)V")
                def onStatusChanged(self, provider, status, extras):  # noqa: N802
                    pass

                @java_method("(Ljava/lang/String;)V")
                def onProviderEnabled(self, provider):  # noqa: N802
                    pass

                @java_method("(Ljava/lang/String;)V")
                def onProviderDisabled(self, provider):  # noqa: N802
                    pass

            lis = Listener()
            self._refs = [lis]
            lm.requestSingleUpdate(provider, lis, autoclass("android.os.Looper").getMainLooper())
        Clock.schedule_once(lambda *_: self._finish(None), timeout)
