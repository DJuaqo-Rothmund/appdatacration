"""
ui/celebrate.py
===============
Avisos a pantalla completa, dibujados directo en el canvas (livianos en teléfonos básicos):

* `week_completed(semana)`: serpentinas y papel picado cayendo + «SEMANA x · COMPLETADA».
* `saved(texto)`: tarjeta breve con un visto bueno («Medición guardada»).

Se cierran solos (o al tocarlos) y se quitan de la ventana al terminar.
"""
from __future__ import annotations

import math
import random

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import (Color, Line, PopMatrix, PushMatrix, Rectangle, Rotate,
                           RoundedRectangle)
from kivy.metrics import dp, sp
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label

from ui import theme
from ui.theme import c

PALETTE = [theme.BERRY, theme.BERRY_LIGHT, theme.LEAF_LIGHT, theme.LEAF, "#F4B400", "#4FC3F7",
           "#FF8A65", "#BA68C8"]


class _Overlay(FloatLayout):
    """Capa sobre toda la app; un toque la cierra."""
    duration = 3.0

    def __init__(self, **kw):
        super().__init__(**kw)
        self.size = Window.size
        self.opacity = 0
        self._closing = False

    def show(self):
        Window.add_widget(self)
        Window.bind(size=self._fit)
        Animation(opacity=1, d=.18).start(self)
        Clock.schedule_once(lambda *_: self.close(), self.duration)
        return self

    def _fit(self, _w, size):
        self.size = size

    def close(self, *_):
        if self._closing:
            return
        self._closing = True
        anim = Animation(opacity=0, d=.35)
        anim.bind(on_complete=lambda *_: self._remove())
        anim.start(self)

    def _remove(self):
        Window.unbind(size=self._fit)
        self.stop()
        if self.parent:
            self.parent.remove_widget(self)

    def stop(self):
        pass

    def on_touch_down(self, touch):
        self.close()
        return True


def _card(overlay, lines, width, height):
    """Tarjeta blanca centrada con textos [(texto, tamaño sp, color hex, negrita)]."""
    with overlay.canvas:   # tarjeta sobre el papel picado (canvas.before), bajo los textos
        Color(0, 0, 0, .10)
        shadow = RoundedRectangle(radius=[dp(26)])
        Color(*c("#FFFFFF", .97))
        card = RoundedRectangle(radius=[dp(24)])
        Color(*c(theme.LEAF_LIGHT, .9))
        edge = Line(width=dp(1.4))

    labels = []
    for text, size, color, bold in lines:
        if text.startswith("icon:"):   # ícono de Material Design (fuente de KivyMD)
            from kivymd.icon_definitions import md_icons
            lb = Label(text=md_icons[text[5:]], font_name="Icons", font_size=sp(size),
                       color=c(color), size_hint=(None, None))
        else:
            lb = Label(text=text, font_size=sp(size), bold=bold, color=c(color), halign="center",
                       size_hint=(None, None))
        lb.bind(texture_size=lb.setter("size"))
        overlay.add_widget(lb)
        labels.append(lb)

    def layout(*_):
        cx, cy = overlay.center_x, overlay.center_y + dp(30)
        w, h = width, height
        x, y = cx - w / 2, cy - h / 2
        shadow.pos, shadow.size = (x + dp(2), y - dp(4)), (w, h)
        card.pos, card.size = (x, y), (w, h)
        edge.rounded_rectangle = (x, y, w, h, dp(24))
        top = y + h - dp(22)
        for lb in labels:
            lb.texture_update()
            top -= lb.texture_size[1]
            lb.pos = (cx - lb.texture_size[0] / 2, top)
            top -= dp(4)

    overlay.bind(size=layout, pos=layout)
    Clock.schedule_once(layout, 0)
    return layout


class _Confetti(_Overlay):
    duration = 3.6
    PIECES = 70
    STREAMERS = 12

    def __init__(self, week_text: str, **kw):
        super().__init__(**kw)
        w, h = Window.size
        rnd = random.Random()
        with self.canvas.before:
            Color(*c(theme.LEAF_DARK, .28))
            self._veil = Rectangle(pos=(0, 0), size=(w, h))
        self._pieces = []
        with self.canvas.before:
            for _ in range(self.PIECES):
                Color(*c(rnd.choice(PALETTE)))
                PushMatrix()
                rot = Rotate(angle=rnd.uniform(0, 360), origin=(0, 0))
                size = (dp(rnd.uniform(5, 9)), dp(rnd.uniform(9, 15)))
                rect = Rectangle(size=size)
                PopMatrix()
                self._pieces.append({
                    "rect": rect, "rot": rot, "w": size,
                    "x": rnd.uniform(0, w), "y": h + rnd.uniform(0, h * .6),
                    "vy": dp(rnd.uniform(160, 300)), "spin": rnd.uniform(-360, 360),
                    "sway": rnd.uniform(dp(10), dp(30)), "phase": rnd.uniform(0, 6.3),
                })
            self._streamers = []
            for _ in range(self.STREAMERS):
                Color(*c(rnd.choice(PALETTE)))
                line = Line(width=dp(2.2), cap="round", joint="round")
                self._streamers.append({
                    "line": line, "x": rnd.uniform(dp(10), w - dp(10)),
                    "y": h + rnd.uniform(0, h * .5), "vy": dp(rnd.uniform(140, 230)),
                    "len": dp(rnd.uniform(70, 120)), "amp": dp(rnd.uniform(7, 13)),
                    "phase": rnd.uniform(0, 6.3), "turns": rnd.uniform(2.0, 3.2),
                })
        _card(self, [("SEMANA " + week_text, 22, theme.LEAF_DARK, True),
                     ("COMPLETADA", 30, theme.BERRY, True),
                     ("¡Buen trabajo!", 14, theme.MUTED, False)],
              min(w - dp(48), dp(320)), dp(150))
        self._t = 0.0
        self._ev = Clock.schedule_interval(self._step, 1 / 60)

    def _fit(self, _w, size):
        super()._fit(_w, size)
        self._veil.size = size

    def _step(self, dt):
        self._t += dt
        t = self._t
        for p in self._pieces:
            p["y"] -= p["vy"] * dt
            x = p["x"] + math.sin(t * 2.4 + p["phase"]) * p["sway"]
            p["rect"].pos = (x - p["w"][0] / 2, p["y"] - p["w"][1] / 2)
            p["rot"].origin = (x, p["y"])
            p["rot"].angle += p["spin"] * dt
        for s in self._streamers:
            s["y"] -= s["vy"] * dt
            pts = []
            n = 14
            for i in range(n + 1):
                k = i / n
                pts += [s["x"] + math.sin(k * s["turns"] * 6.283 + s["phase"] + t * 5) * s["amp"],
                        s["y"] + k * s["len"]]
            s["line"].points = pts

    def stop(self):
        self._ev.cancel()


class _Saved(_Overlay):
    duration = 1.6

    def __init__(self, text: str, **kw):
        super().__init__(**kw)
        w = min(Window.width - dp(80), dp(270))
        _card(self, [("icon:check-circle", 40, theme.LEAF, False), (text, 18, theme.LEAF_DARK, True)], w, dp(118))

    def on_touch_down(self, touch):
        return False   # no bloquea: la pantalla de atrás ya está activa


def week_completed(week_text: str):
    return _Confetti(week_text).show()


def saved(text: str = "Medición guardada"):
    return _Saved(text).show()
