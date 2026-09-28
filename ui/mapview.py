"""
ui/mapview.py
=============
Mapa liviano (teselas OpenStreetMap) para marcar la ubicación de una muestra.

El pin queda fijo en el centro: se arrastra el mapa debajo y se confirma el
punto. Zoom con botones o con doble toque. Las teselas se guardan en caché,
así que las zonas ya vistas funcionan sin conexión.
"""
from __future__ import annotations

import math

from kivy.clock import Clock
from kivy.graphics import Color, Line, Rectangle
from kivy.properties import NumericProperty
from kivy.uix.stencilview import StencilView

import geo

MIN_ZOOM, MAX_ZOOM = 3, 19


def tile_px() -> float:
    """Tamaño en pantalla de una tesela: legible sin ampliarla tanto que se vea borrosa."""
    from kivy.metrics import Metrics
    return geo.TILE_SIZE * max(1.0, Metrics.density / 1.5)


class MapPicker(StencilView):
    zoom = NumericProperty(16)
    cx = NumericProperty(0.0)   # centro en coordenadas de tesela (float) al zoom actual
    cy = NumericProperty(0.0)

    def __init__(self, lat: float, lon: float, zoom: int = 16, workers=None, **kw):
        super().__init__(**kw)
        self.workers = workers
        self._textures: dict = {}
        self._pending: set = set()
        self.zoom = zoom
        self.cx, self.cy = geo.latlon_to_tile(lat, lon, zoom)
        self.bind(pos=self._redraw, size=self._redraw, cx=self._redraw, cy=self._redraw,
                  zoom=self._redraw)
        self._drag = None
        Clock.schedule_once(self._redraw, 0)

    # --------------------------------------------------------------- estado
    @property
    def center_latlon(self) -> tuple[float, float]:
        n = 2 ** int(self.zoom)
        return geo.tile_to_latlon(self.cx % n, self.cy, int(self.zoom))

    def center_on(self, lat: float, lon: float, zoom: int | None = None):
        if zoom is not None:
            self.zoom = zoom
        self.cx, self.cy = geo.latlon_to_tile(lat, lon, int(self.zoom))

    def zoom_by(self, step: int):
        z = int(self.zoom)
        nz = max(MIN_ZOOM, min(MAX_ZOOM, z + step))
        if nz == z:
            return
        f = 2 ** (nz - z)
        self.cx, self.cy, self.zoom = self.cx * f, self.cy * f, nz

    # -------------------------------------------------------------- toques
    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        if touch.is_double_tap:
            self.zoom_by(1)
            return True
        touch.grab(self)
        self._drag = touch.pos
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is not self or self._drag is None:
            return super().on_touch_move(touch)
        dx, dy = touch.x - self._drag[0], touch.y - self._drag[1]
        self._drag = touch.pos
        tile = tile_px()
        n = 2 ** int(self.zoom)
        self.cx = (self.cx - dx / tile) % n
        self.cy = min(max(self.cy + dy / tile, 0), n)
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self._drag = None
            return True
        return super().on_touch_up(touch)

    # -------------------------------------------------------------- dibujo
    def _texture(self, z, x, y):
        key = (z, x, y)
        tex = self._textures.get(key)
        if tex is not None:
            return tex
        path = geo.tile_path(z, x, y)
        import os
        if os.path.exists(path):
            try:
                from kivy.core.image import Image as CoreImage
                tex = CoreImage(path).texture
                self._textures[key] = tex
                if len(self._textures) > 160:
                    self._textures.pop(next(iter(self._textures)))
                return tex
            except Exception:  # noqa: BLE001 (tesela corrupta: se vuelve a bajar)
                try:
                    os.remove(path)
                except OSError:
                    pass
        if key not in self._pending:
            self._pending.add(key)

            def work():
                ok = geo.fetch_tile(z, x, y)
                self._pending.discard(key)
                if ok:
                    Clock.schedule_once(self._redraw, 0)

            if self.workers is not None:
                self.workers.submit(work)
        return None

    def _redraw(self, *_):
        self.canvas.clear()
        z = int(self.zoom)
        n = 2 ** z
        size = tile_px()
        w, h = self.width, self.height
        mx, my = self.x + w / 2, self.y + h / 2
        x0 = int(math.floor(self.cx - w / 2 / size)) - 1
        x1 = int(math.floor(self.cx + w / 2 / size)) + 1
        y0 = int(math.floor(self.cy - h / 2 / size)) - 1
        y1 = int(math.floor(self.cy + h / 2 / size)) + 1
        with self.canvas:
            Color(0.90, 0.93, 0.90, 1)
            Rectangle(pos=self.pos, size=self.size)
            for ty in range(max(y0, 0), min(y1, n - 1) + 1):
                for tx in range(x0, x1 + 1):
                    px = mx + (tx - self.cx) * size
                    py = my - (ty + 1 - self.cy) * size   # y de teselas crece hacia abajo
                    tex = self._texture(z, tx % n, ty)
                    if tex is not None:
                        Color(1, 1, 1, 1)
                        Rectangle(texture=tex, pos=(px, py), size=(size, size))
                    else:
                        Color(0.80, 0.85, 0.80, 1)
                        Line(rectangle=(px, py, size, size), width=1)
