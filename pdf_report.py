"""
pdf_report.py
=============
Informes en PDF (A4) a partir de los mismos datos que usan las plantillas HTML:
semanal, período/mensual, ficha por variedad y matriz comparativa.

El informe semanal lleva incrustado «phenorubus_semanal.json» (estado BBCH, notas y
qué foto es cada una) para que «Importar informes» pueda recuperar los registros
desde el PDF, como antes desde el HTML.
"""
from __future__ import annotations

import json

import phenology as ph
from pdfdoc import PAGE_H, PAGE_W, PDFDoc

INK, INK2, MUTED, RULE = "#22261c", "#4f5546", "#7d8272", "#ddd6c4"
OLIVE, BERRY, CHIP, PAPER = "#5b6b3a", "#9c3350", "#ece7d8", "#fbf9f2"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
CONTEXT = "#c9cdc0"
M = 36                          # margen
CW = PAGE_W - 2 * M             # ancho útil
BOTTOM = PAGE_H - 46            # límite inferior del contenido
WEEKLY_DATA = "phenorubus_semanal.json"

_SEQ_LIGHT, _SEQ_DARK = (0xEE, 0xF0, 0xE2), (0x2B, 0x36, 0x14)


def seq_color(code):
    t = max(0.0, min(1.0, ph.bbch_value(code) / 99.0)) ** 0.85
    c = tuple(round(a + (b - a) * t) for a, b in zip(_SEQ_LIGHT, _SEQ_DARK))
    lum = (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255
    return c, ("#ffffff" if lum < 0.5 else "#1f2413")


def macro_name(code) -> str:
    return ph.MACRO_STAGES.get(ph.macro_of(code), "") if code is not None else ""


def macro_color(code) -> str:
    return ph.MACRO_COLORS.get(ph.macro_of(code), "#cccccc")


class Report(PDFDoc):
    """Hoja con encabezado, pie «Página n de N» y un cursor vertical (self.y)."""

    def __init__(self, title: str, subtitle: str, meta: list[str], generated: str,
                 bbch_names: dict | None = None, num=None):
        super().__init__(title=title)
        self.subtitle, self.meta, self.generated = subtitle, meta, generated
        self.names = bbch_names or {}
        self.num = num or (lambda v: "—" if v in (None, "") else str(v))
        self.y = M
        self.page_hook = self._running_header
        self.footer = self._footer

    # ------------------------------------------------------------ hojas
    def _running_header(self, doc):
        self.y = M
        if len(self.pages) > 1:
            self.text(M, M - 8, self.fit(self.title, CW * .75, 8, True), 8, True, MUTED)
            self.text(M + CW, M - 8, "PhenoRubus", 8, False, MUTED, "right")
            self.line(M, M - 3, M + CW, M - 3, RULE, .6)
            self.y = M + 10

    def _footer(self, doc, n, total):
        y = PAGE_H - 26
        self.line(M, y - 10, M + CW, y - 10, RULE, .5)
        self.text(M, y, f"PhenoRubus · cuaderno de campo digital · generado {self.generated}", 7.5,
                  False, MUTED)
        self.text(M + CW, y, f"Página {n} de {total}", 7.5, False, MUTED, "right")

    # ------------------------------------------------- bloques sin cortar
    # Cada sección se mide primero «en seco» (sin dibujar) y, si no cabe en lo que queda
    # de la hoja pero sí en una hoja completa, pasa entera a la siguiente. Los títulos
    # (h2) quedan pendientes y se dibujan junto con el primer contenido de su sección.
    _dry = 0
    _pending = None          # (título, bajada) de un h2 aún no dibujado
    _heading_now = False

    FULL = BOTTOM - (M + 10)   # alto útil de una hoja que no es la primera

    def add_page(self):
        if self._dry:
            return
        super().add_page()

    def _heading_h(self) -> float:
        if not self._pending:
            return 0
        _t, lead = self._pending
        h = 32
        if lead:
            h += len(self.wrap(lead, CW, 8.8)) * 8.8 * 1.32 + 4
        return h

    def _draw_heading(self):
        text, lead = self._pending
        self._pending = None
        self._heading_now = True
        try:
            self.y += 18
            self.text(M, self.y, text, 13.5, True, INK)
            self.y += 6
            self.line(M, self.y, M + CW, self.y, RULE, .8)
            self.y += 8
            if lead:
                self.para(lead, 8.8, INK2)
                self.y += 4
        finally:
            self._heading_now = False

    def ensure(self, h: float):
        if self._dry:
            return
        if self._pending and not self._heading_now:
            if self.y + self._heading_h() + h > BOTTOM:   # el título baja con su contenido
                self.add_page()
            self._draw_heading()
        if self.y + h > BOTTOM:
            self.add_page()

    def measure(self, fn) -> float:
        """Alto que ocuparía fn() (sin dibujar nada)."""
        y, pending = self.y, self._pending
        self._pending = None
        self._dry += 1
        self.y = 0
        try:
            fn()
            return self.y
        finally:
            self._dry -= 1
            self.y, self._pending = y, pending

    def keep(self, fn):
        """Dibuja fn() sin cortarlo entre hojas si cabe en una hoja completa."""
        if self._dry:
            return fn()
        h = self.measure(fn)
        self.ensure(min(h, self.FULL - self._heading_h()))
        return fn()

    # Primitivas: no dibujan nada mientras se mide.
    def text(self, *a, **k):
        if not self._dry:
            super().text(*a, **k)

    def rect(self, *a, **k):
        if not self._dry:
            super().rect(*a, **k)

    def line(self, *a, **k):
        if not self._dry:
            super().line(*a, **k)

    def polyline(self, *a, **k):
        if not self._dry:
            super().polyline(*a, **k)

    def circle(self, *a, **k):
        if not self._dry:
            super().circle(*a, **k)

    def image(self, *a, **k):
        return True if self._dry else super().image(*a, **k)

    def link(self, *a, **k):
        if not self._dry:
            super().link(*a, **k)

    def output(self) -> bytes:
        if self._pending:            # título sin contenido al final
            self.ensure(0)
        return super().output()

    LOGO = 80   # logo a línea de la app (esquina superior derecha de la primera hoja)

    def start(self):
        self.add_page()
        from platform_utils import resource_path
        self.image(resource_path("assets", "ui", "logo_line_report.png"), M + CW - self.LOGO, M - 6,
                   self.LOGO, self.LOGO, mode="contain", max_px=400, bg=None)
        tw = CW - self.LOGO - 10           # el texto del encabezado no pisa el logo
        self.text(M, self.y + 8, "PHENORUBUS · INFORME FENOLÓGICO", 8, True, OLIVE)
        self.y += 16
        for line in self.wrap(self.title, tw, 20, True):
            self.y += 22
            self.text(M, self.y, line, 20, True, INK)
        if self.subtitle:
            for line in self.wrap(self.subtitle, tw, 10.5):
                self.y += 15
                self.text(M, self.y, line, 10.5, False, INK2)
        if self.meta:
            self.y += 14
            self.text(M, self.y, self.fit("  ·  ".join(self.meta), tw, 8.5), 8.5, False, MUTED)
        self.y = max(self.y, M - 6 + self.LOGO - 8)
        self.y += 10
        self.line(M, self.y, M + CW, self.y, INK, 1.4)
        self.y += 8

    # ----------------------------------------------------------- texto
    def h2(self, text: str, lead: str | None = None):
        if self._pending:            # dos títulos seguidos: el primero va sin contenido
            self.ensure(0)
        self._pending = (text, lead)  # se dibuja junto con el primer contenido

    def para(self, text: str, size: float = 9, color=INK, bold: bool = False, x: float = M,
             w: float = CW, leading: float = 1.32):
        for line in self.wrap(text, w, size, bold):
            self.ensure(size * leading)
            self.y += size * leading
            self.text(x, self.y - size * .28, line, size, bold, color)

    def chip(self, x: float, y: float, code, max_w: float = CW, size: float = 8.5) -> float:
        """Etiqueta del estado BBCH (con el color del estadio). y = borde superior."""
        if code is None:
            label, fill, sw = "Sin estado registrado", CHIP, None
        else:
            label, fill, sw = ph.bbch_label(code, self.names), "#f1efe6", macro_color(code)
        pad = 12 if sw else 6
        label = self.fit(label, max_w - pad - 8, size, True)
        w = self.width(label, size, True) + pad + 8
        self.rect(x, y, w, size + 8, fill=fill, radius=(size + 8) / 2)
        if sw:
            self.circle(x + 8, y + (size + 8) / 2, 3.2, fill=sw)
        self.text(x + pad + 2, y + size + 2.4, label, size, True, INK)
        return w

    # ------------------------------------------------------------ tablas
    def table(self, headers: list, rows: list[list], widths: list[float], size: float = 8.2,
              aligns: list[str] | None = None):
        """Tabla con salto de página (repite el encabezado). Celda: texto o dict con
        text / fill / color / bold / draw(doc, x, y, w, h)."""
        total = sum(widths)
        widths = [w * CW / total for w in widths]
        aligns = aligns or ["left"] * len(headers)
        pad = 4

        def cell(v):
            return v if isinstance(v, dict) else {"text": "" if v is None else str(v)}

        def row_h(cells, bold=False):
            h = size + 2 * pad
            for c, w in zip(cells, widths):
                if c.get("draw"):
                    h = max(h, c.get("h", 16))
                    continue
                n = len(self.wrap(c.get("text", ""), w - 2 * pad, size, bold or c.get("bold")))
                h = max(h, n * size * 1.25 + 2 * pad - size * .25)
            return h

        def draw_row(cells, header=False):
            h = row_h(cells, header)
            x = M
            for c, w, al in zip(cells, widths, aligns):
                fill = "#efece2" if header else c.get("fill")
                if fill:
                    self.rect(x, self.y, w, h, fill=fill)
                if c.get("draw"):
                    c["draw"](self, x, self.y, w, h)
                else:
                    lines = self.wrap(c.get("text", ""), w - 2 * pad, size, header or c.get("bold"))
                    ty = self.y + pad + size * .82
                    for line in lines:
                        tx = {"left": x + pad, "center": x + w / 2, "right": x + w - pad}[al]
                        self.text(tx, ty, line, size, header or bool(c.get("bold")),
                                  c.get("color", INK2 if header else INK), al)
                        ty += size * 1.25
                x += w
            self.line(M, self.y + h, M + CW, self.y + h, RULE, .5)
            self.y += h

        head = [cell({"text": h} if isinstance(h, str) else h) for h in headers]
        body = [[cell(v) for v in r] for r in rows]
        hh = row_h(head, True)
        heights = [row_h(cells) for cells in body]
        total = hh + sum(heights) + 6
        # Entera en una hoja si cabe; si es más larga que una hoja, empieza aquí (con al
        # menos el encabezado y unas filas) y se corta solo ENTRE filas.
        if total <= self.FULL - self._heading_h():
            self.ensure(total)
        else:
            self.ensure(hh + sum(heights[:3]) + 6)
        draw_row(head, True)
        for cells, h in zip(body, heights):
            if not self._dry and self.y + h > BOTTOM:
                self.add_page()
                self.text(M, self.y + 7, "(continuación)", 7, False, MUTED)
                self.y += 11
                draw_row(head, True)
            draw_row(cells)
        self.y += 6

    # ----------------------------------------------------------- fotos
    def photo(self, src, x, y, w, h, caption: str | None = None, name: str | None = None) -> float:
        """Foto (completa, sobre fondo claro) con leyenda opcional. Devuelve el alto usado."""
        if not src or not self.image(src, x, y, w, h, mode="contain", radius=4, name=name):
            self.rect(x, y, w, h, fill=CHIP, radius=4)
            self.text(x + w / 2, y + h / 2 + 3, "Sin foto", 8, False, MUTED, "center")
        if caption:
            self.text(x, y + h + 10, self.fit(caption.upper(), w, 7, True), 7, True, MUTED)
            return h + 14
        return h

    def gallery(self, items: list[dict], cols: int = 4, ratio: float = .75, size: float = 7.6):
        """items: {src, caption, when}. Fotos en grilla con leyenda (2 líneas máx.)."""
        gap = 8
        w = (CW - gap * (cols - 1)) / cols
        h = w * ratio
        rows = []
        for i in range(0, len(items), cols):
            row = items[i:i + cols]
            caps = [self.wrap(it.get("caption") or "", w, size)[:2] for it in row]
            extra = max(len(c) for c in caps) * size * 1.25 + (11 if any(it.get("when") for it in row) else 0)
            rows.append((row, caps, extra))
        total = sum(h + extra + 10 for _r, _c, extra in rows)
        if total <= self.FULL - self._heading_h():
            self.ensure(total)          # la galería completa en una sola hoja
        for row, caps, extra in rows:
            self.ensure(h + extra + 10)   # si es más larga que una hoja: corta entre filas
            for j, it in enumerate(row):
                x = M + j * (w + gap)
                self.photo(it.get("src"), x, self.y, w, h)
                ty = self.y + h + size + 3
                for line in caps[j]:
                    self.text(x, ty, line, size, False, INK)
                    ty += size * 1.25
                if it.get("when"):
                    self.text(x, ty, self.fit(it["when"], w, 7), 7, False, MUTED)
            self.y += h + extra + 10

    # --------------------------------------------------------- gráfico
    def chart(self, raw: dict, h: float = 220, title: str | None = None):
        """BBCH (0-99) vs. semana: variedades seleccionadas en color, el resto en gris."""
        if not raw:
            return
        self.ensure(h + 40)
        x0, y0 = M + 34, self.y + 6
        w, hh = CW - 34 - 70, h - 28
        wmin, wmax = raw["wmin"], max(raw["wmax"], raw["wmin"] + 1)
        labels = raw.get("labels", {})

        def X(wk):
            return x0 + (wk - wmin) / (wmax - wmin) * w

        def Y(code):   # código BBCH o promedio de repeticiones (float)
            return y0 + (1 - ph.scale_pos(code) / 99) * hh

        from reporter import BANDS
        for i, (lo, hi, name) in enumerate(BANDS):
            self.rect(x0, Y(hi + .5), w, Y(lo - .5) - Y(hi + .5), fill="#f4f2ea" if i % 2 else "#faf8f2")
            self.text(x0 - 4, (Y(lo) + Y(hi)) / 2 + 2.5, name, 6, False, MUTED, "right")
        self.line(x0, y0 + hh, x0 + w, y0 + hh, MUTED, .6)
        span = wmax - wmin
        step = 1 if span <= 14 else (2 if span <= 28 else 4)
        for wk in range(wmin, wmax + 1, step):
            self.text(X(wk), y0 + hh + 10, f"S{labels.get(wk, labels.get(str(wk), wk))}", 6.5, False,
                      MUTED, "center")
        sel = raw.get("sel", [])
        ordered = sorted(raw["series"], key=lambda s_: s_["id"] in sel)
        ends = []
        for s_ in ordered:
            pts = sorted(s_["points"])
            if not pts:
                continue
            on = s_["id"] in sel
            color = SERIES[sel.index(s_["id"]) % len(SERIES)] if on else CONTEXT
            self.polyline([(X(a), Y(b)) for a, b in pts], color, 1.8 if on else .8)
            if on:
                for a, b in pts:
                    self.circle(X(a), Y(b), 2.2, fill=color)
                ends.append((Y(pts[-1][1]), X(pts[-1][0]), s_["name"], color))
        last_y = -99
        for ey, ex, name, color in sorted(ends):
            ey = max(ey, last_y + 9)
            self.text(min(ex + 5, x0 + w + 4), ey + 2.5, self.fit(name, 64, 7, True), 7, True, color)
            last_y = ey
        self.y += h + 4
        if raw.get("note") or any(s_["id"] in sel for s_ in raw["series"]):
            self.text(M, self.y, raw.get("note") or
                      "En color: variedades destacadas · en gris: el resto de las variedades.", 7, False, MUTED)
            self.y += 6

    def spark(self, x, y, w, h, pts, wmin, wmax, color=OLIVE):
        if not pts:
            return
        wmax = max(wmax, wmin + 1)
        coords = [(x + (a - wmin) / (wmax - wmin) * w, y + (1 - ph.scale_pos(b) / 99) * h)
                  for a, b in sorted(pts)]
        self.polyline(coords, color, 1.1)
        self.circle(*coords[-1], 1.6, fill=color)

    # ---------------------------------------------------------- bloques
    def heatmap(self, heat: list[dict], weeks: list[dict], per_table: int = 16, label: str = "Variedad"):
        for i in range(0, max(1, len(weeks)), per_table):
            chunk = weeks[i:i + per_table]
            headers = [label] + [ph.week_short(w) for w in chunk]
            rows = []
            for r in heat:
                row = [{"text": r["variety"]["name"], "bold": True}]
                for c in r["cells"][i:i + per_table]:
                    if c["code"] is None:
                        row.append({"text": "·", "color": MUTED})
                    else:
                        fill, fg = seq_color(c["code"])
                        row.append({"text": f"{ph.code_str(c['code'])}", "fill": fill, "color": fg, "bold": True})
                rows.append(row)
            self.table(headers, rows, [3.4] + [1] * len(chunk), 7.6, ["left"] + ["center"] * len(chunk))

    def measures(self, measures: list[dict], show_week: bool = False, show_variety: bool = True):
        for m in measures:
            self.keep(lambda m=m: self._measure(m, show_week, show_variety))

    def _measure(self, m: dict, show_week: bool, show_variety: bool):
        if True:
            self.ensure(40)
            self.y += 10
            self.text(M, self.y, m["name"], 11, True, INK)
            kind = "Planilla" if m["kind"] == "table" else "Imágenes"
            self.text(M + self.width(m["name"], 11, True) + 6, self.y, f"· {kind}", 8.5, False, MUTED)
            self.y += 8
            if m["kind"] == "table":
                headers = (["Semana"] if show_week else []) + (["Variedad"] if show_variety else []) \
                    + list(m["columns"])
                rows = [([r["week"]] if show_week else []) + ([r["variety"]] if show_variety else [])
                        + [str(v) for v in r["values"]] for r in m["rows"]]
                widths = ([1] if show_week else []) + ([1.6] if show_variety else []) + [1.4] * len(m["columns"])
                self.table(headers, rows, widths, 8)
            else:
                self.gallery(m["pics"], cols=4)

    def ai_geo_notes(self, item: dict, x: float, w: float):
        if item.get("ai_code") is not None:
            t = f"Sugerencia IA: BBCH {ph.code_str(item['ai_code'])}"
            if item.get("ai_conf") is not None:
                t += f" ({round(item['ai_conf'] * 100)} % conf.)"
            if item.get("ai_accepted") == 1:
                t += " · aceptada"
            elif item.get("ai_accepted") == 0:
                t += " · corregida"
            self.para(t, 8, INK2, x=x, w=w)
        geo = item.get("geo")
        if geo:
            t = f"Ubicación: {geo['text']}" + (f" · ±{round(geo['acc'])} m" if geo.get("acc") else "")
            self.para(t + "  (ver mapa)", 8, "#3e6a8a", x=x, w=w)
            self.link(x, self.y - 10, min(w, self.width(t + "  (ver mapa)", 8) + 2), 11, geo["link"])
        if item.get("notes"):
            self.y += 2
            self.para(item["notes"], 8.6, INK, x=x, w=w)


# ===========================================================================
# Informes
# ===========================================================================
def _doc(ctx, meta, names, num) -> Report:
    r = Report(ctx["title"], ctx.get("subtitle", ""), meta, ctx["generated"], names, num)
    r.start()
    return r


def weekly(ctx: dict, names: dict, num=None) -> Report:
    cards, st = ctx["cards"], ctx["stats"]
    r = _doc(ctx, [f"{st['done']}/{st['varieties']} variedades con estado BBCH", f"{st['photos']} fotos"],
             names, num)
    r.h2("Resumen inter-varietal")
    rows = []
    for c in cards:
        n = (1 if c["img"]["canopy"] else 0) + (1 if c["img"]["detail"] else 0) + len(c.get("extras") or [])
        ia = "—"
        if c.get("ai_code") is not None:
            ia = f"BBCH {ph.code_str(c['ai_code'])}" + {1: " · aceptada", 0: " · corregida"}.get(c.get("ai_accepted"), "")
        rows.append([{"text": c["variety"]["name"], "bold": True},
                     ph.bbch_label(c["code"], names) if c["code"] is not None else "Sin estado",
                     {"text": macro_name(c["code"]) or "—", "color": MUTED}, str(n), {"text": ia, "color": MUTED}])
    r.table(["Variedad", "Estado fenológico", "Estadio principal", "Fotos", "IA"], rows,
            [2.2, 3.6, 2.4, .8, 1.8], aligns=["left", "left", "left", "center", "left"])

    r.h2("Registro fotográfico por variedad",
         "Foto general de canopia y foto de detalle, con el estado fenológico asignado y las "
         "observaciones técnicas.")
    data_cards = []
    pw = (CW - 24 - 10) / 2
    ph_h = pw * .6
    def draw_card(i, c):
        photos = [(k, lab) for k, lab in (("canopy", "Canopia"), ("detail", "Detalle")) if c["img"][k]]
        att = c.get("attachments") or []
        top, page = r.y, r._page
        x = M + 12
        r.y += 18
        r.text(x, r.y, c["variety"]["name"], 12, True, INK)
        if c["variety"].get("code"):
            r.text(M + CW - 12, r.y, c["variety"]["code"], 8.5, True, OLIVE, "right")
        r.y += 8
        names_map = {}
        if photos:
            for j, (k, lab) in enumerate(photos):
                name = f"V{i}{k[0]}"
                r.photo(c["img"][k], x + j * (pw + 10), r.y, pw, ph_h, lab, name=name)
                names_map[k] = name
            r.y += ph_h + 16
        else:
            r.rect(x, r.y, CW - 24, 18, fill=CHIP, radius=4)
            r.text(x + 6, r.y + 12, "Sin fotos", 8, False, MUTED)
            r.y += 24
        extras = c.get("extras") or []
        if extras:
            for j, src in enumerate(extras[:9]):
                r.image(src, x + j * 52, r.y, 46, 46, radius=3)
            r.y += 52
        r.chip(x, r.y, c["code"], CW - 24)
        r.y += 20
        r.ai_geo_notes(c, x, CW - 24)
        if att:
            r.y += 6
            _gallery_at(r, att, x, CW - 24, cols=4)
        r.y += 8
        if r._page == page:      # marco de la tarjeta (si no se cortó entre hojas)
            r.rect(M, top, CW, r.y - top, stroke=RULE, lw=.8, radius=6)
        r.y += 10
        return names_map

    for i, c in enumerate(cards):
        names_map = r.keep(lambda i=i, c=c: draw_card(i, c))   # la tarjeta entera en una hoja
        data_cards.append({"name": c["variety"]["name"], "code": c["variety"].get("code") or "",
                           "bbch": c["code"], "notes": c.get("notes") or "", "photos": names_map})
    if ctx.get("measures"):
        r.h2("Mediciones de la semana")
        r.measures(ctx["measures"])
    w = ctx["week"]
    iso_w, iso_y = ph.iso_week(w["start_date"])
    r.attach(WEEKLY_DATA, json.dumps({
        "app": "PhenoRubus", "kind": "weekly", "season": w["season"], "week_number": w["week_number"],
        "start_date": w["start_date"], "iso_week": iso_w, "iso_year": iso_y, "cards": data_cards,
    }, ensure_ascii=False).encode("utf-8"), "application/json")
    return r


def _gallery_at(r: Report, items, x, width, cols=4):
    """Galería dentro de una tarjeta (con sangría)."""
    gap = 8
    w = (width - gap * (cols - 1)) / cols
    h = w * .75
    for i in range(0, len(items), cols):
        row = items[i:i + cols]
        caps = [r.wrap(it.get("caption") or "", w, 7.4)[:2] for it in row]
        extra = max(len(cp) for cp in caps) * 9.3
        r.ensure(h + extra + 8)
        for j, it in enumerate(row):
            xx = x + j * (w + gap)
            r.photo(it.get("src"), xx, r.y, w, h)
            ty = r.y + h + 10
            for line in caps[j]:
                r.text(xx, ty, line, 7.4, False, INK)
                ty += 9.3
        r.y += h + extra + 8


def period(ctx: dict, names: dict, num=None) -> Report:
    r = _doc(ctx, [f"{len(ctx['weeks'])} semanas", f"{len(ctx['strips'])} variedades"], names, num)
    r.h2("Avance fenológico por semana",
         "Código BBCH registrado para cada variedad y semana. El tono se oscurece a medida que avanza "
         "el ciclo (yema → hoja → flor → fruto → maduración).")
    r.heatmap(ctx["heat"], ctx["weeks"])
    if ctx.get("compare"):
        r.h2("Estado BBCH por semana")
        r.chart(ctx["compare"]["raw"])
    r.h2("Evolución por variedad", "Foto de detalle (o de canopia) de cada semana.")
    cols, gap = 6, 6
    w = (CW - gap * (cols - 1)) / cols
    h = w * .75
    def draw_strip(s):
        r.ensure(30 + h + 24)
        r.y += 14
        r.text(M, r.y, s["variety"]["name"], 11, True, INK)
        if s["first"] is not None:
            t = f"BBCH {ph.code_str(s['first'])} → {ph.code_str(s['last'])}" + (
                f" · avance {s['advance']} unidades BBCH" if s["advance"] is not None else "")
        else:
            t = "Sin registros en el período"
        r.text(M + CW, r.y, t, 8, False, MUTED, "right")
        r.y += 6
        tiles = s["tiles"]
        for i in range(0, len(tiles), cols):
            r.ensure(h + 24)
            for j, t in enumerate(tiles[i:i + cols]):
                x = M + j * (w + gap)
                r.photo(t["img"].get("detail") or t["img"].get("canopy"), x, r.y, w, h)
                lab = t["week"]["label"].replace("Semana del ", "")
                r.text(x, r.y + h + 9, r.fit(f"{ph.week_short(t['week'])} · {lab}", w, 6.8), 6.8, False, MUTED)
                r.text(x, r.y + h + 18, f"BBCH {ph.code_str(t['code'])}" if t["code"] is not None else "—", 7.4,
                       True, INK if t["code"] is not None else MUTED)
            r.y += h + 24

    for s in ctx["strips"]:
        r.keep(lambda s=s: draw_strip(s))   # la franja de cada variedad sin cortar
    return r


def variety(ctx: dict, names: dict, num=None) -> Report:
    v, m = ctx["variety"], ctx["metrics"]
    num = num or (lambda x: "—" if x in (None, "") else str(x))
    meta = ([f"Código: {v['code']}"] if v.get("code") else []) + [f"{len(ctx['timeline'])} semanas registradas"]
    r = _doc(ctx, meta, names, num)
    r.h2("Parámetros biométricos y productivos")
    stats = [("historical_yield", "Rendimiento histórico"), ("projected_yield", "Proyección temporada"),
             ("basal_canes", "Cañas basales"), ("laterals", "Laterales reproductivos")]
    bw = (CW - 3 * 8) / 4
    r.ensure(60)
    for i, (k, lab) in enumerate(stats):
        x = M + i * (bw + 8)
        r.rect(x, r.y, bw, 50, fill=PAPER, stroke=RULE, radius=5)
        val = num(m.get(k))
        r.text(x + 8, r.y + 22, val, 15, True, INK)
        r.text(x + 10 + r.width(val, 15, True), r.y + 22, r.fit(m.get(f"{k}_unit") or "", bw - 20 - r.width(val, 15, True), 7.5), 7.5, False, MUTED)
        r.text(x + 8, r.y + 40, r.fit(lab, bw - 12, 7.5), 7.5, False, INK2)
    r.y += 58
    if m.get("historical_note"):
        r.para(f"Nota rendimiento histórico: {m['historical_note']}", 8, MUTED)
    if ctx.get("custom"):
        r.y += 4
        r.table(["Parámetro adicional", "Valor", "Unidad"],
                [[f["key"], {"text": f["value"], "bold": True}, {"text": f["unit"], "color": MUTED}]
                 for f in ctx["custom"]], [3, 2, 1.5])
    if ctx.get("attachments"):
        r.h2("Fotos adjuntas generales")
        r.gallery(ctx["attachments"], cols=4)

    r.h2("Progresión fenológica")
    timeline = ctx["timeline"]
    if ctx.get("compare"):
        r.chart(ctx["compare"]["raw"])
    else:
        pts = [(t["week_number"], t["code"]) for t in timeline if t["code"] is not None]
        if pts:
            lo = min(p[0] for p in pts)
            hi = max(max(p[0] for p in pts), lo + 1)
            labels = {t["week_number"]: ph.week_of_year(t["start_date"]) for t in timeline}
            r.chart({"series": [{"id": v["id"], "name": v["name"], "points": pts}], "sel": [v["id"]],
                     "wmin": lo, "wmax": hi, "labels": labels})
    if any(t["code"] is not None for t in timeline):
        r.table([f"{name} (>= {ph.code_str(code)})" for code, name in [(mm["code"], mm["name"]) for mm in ctx["milestones"]]],
                [[f"Semana {mm['week']}" if mm["week"] else {"text": "—", "color": MUTED}
                  for mm in ctx["milestones"]]], [1] * len(ctx["milestones"]), 7.6,
                ["center"] * len(ctx["milestones"]))
    else:
        r.para("Aún no hay estados BBCH registrados para esta variedad.", 9, MUTED)

    r.h2("Timeline de brotación a cosecha")
    lw = 92
    pw = (CW - lw - 12 - 8) / 2
    ph_h = pw * .72
    def draw_week(t):
        photos = [(k, lab) for k, lab in (("canopy", "Canopia"), ("detail", "Detalle")) if t["img"][k]]
        top = r.y + 4
        r.y = top
        r.text(M, top + 10, ph.week_title(t["start_date"]), 8.5, True, INK)
        for k, line in enumerate(r.wrap(t["week_label"], lw - 4, 7.5)[:3]):
            r.text(M, top + 21 + k * 9.5, line, 7.5, False, MUTED)
        x = M + lw + 12
        if photos:
            for j, (k, lab) in enumerate(photos):
                r.photo(t["img"][k], x + j * (pw + 8), r.y, pw, ph_h, lab)
            r.y += ph_h + 16
        r.chip(x, r.y, t["code"], CW - lw - 12)
        r.y += 20
        r.ai_geo_notes(t, x, CW - lw - 12)
        if t.get("attachments"):
            r.y += 4
            _gallery_at(r, t["attachments"], x, CW - lw - 12, cols=3)
        r.y += 6
        r.line(M, r.y, M + CW, r.y, RULE, .5)

    for t in timeline:
        r.keep(lambda t=t: draw_week(t))   # cada semana completa en una hoja
    if not timeline:
        r.para("Sin registros.", 9, MUTED)
    if ctx.get("measures"):
        r.h2("Mediciones")
        r.measures(ctx["measures"], show_week=True, show_variety=False)
    return r


def matrix(ctx: dict, names: dict, num=None) -> Report:
    rows, weeks = ctx["rows"], ctx["weeks"]
    r = _doc(ctx, [f"{len(rows)} variedades", f"{len(weeks)} semanas"], names, num)
    r.h2("Matriz fenológica", "Estado BBCH por variedad (filas) y semana de muestreo (columnas).")
    r.heatmap(ctx["heat"], weeks)
    if ctx.get("compare"):
        r.h2("Estado BBCH por semana")
        r.chart(ctx["compare"]["raw"])
    r.h2("Precocidad relativa",
         "Diferencia media, en unidades BBCH, entre cada variedad y el promedio de todas las variedades "
         "en las mismas semanas. Positivo = adelantada; negativo = tardía.")
    max_abs = ctx.get("max_abs") or 1.0
    wmin = weeks[0]["week_number"] if weeks else 0
    wmax = weeks[-1]["week_number"] if weeks else 1

    def bar(delta):
        def draw(doc, x, y, w, h):
            mid, cy = x + (w - 34) / 2 + 2, y + h / 2
            doc.line(x + 4, cy, x + w - 34, cy, RULE, 3)
            doc.line(mid, y + 4, mid, y + h - 4, MUTED, .6)
            span = abs(delta) / max_abs * ((w - 38) / 2)
            doc.rect(mid if delta >= 0 else mid - span, cy - 3, span, 6,
                     fill=BERRY if delta >= 0 else "#3e6a8a", radius=2)
            doc.text(x + w - 4, cy + 3, f"{delta:+.1f}", 8, True, INK, "right")
        return {"draw": draw, "h": 18}

    def spark(pts):
        return {"draw": lambda doc, x, y, w, h: doc.spark(x + 4, y + 3, w - 8, h - 6, pts, wmin, wmax),
                "h": 18}

    rank_rows = [[{"text": str(i + 1), "color": MUTED}, {"text": rr["variety"]["name"], "bold": True},
                  bar(rr["delta"]), ph.bbch_label(rr["last"], names) if rr["last"] is not None else "—",
                  spark(rr.get("pts") or [])] for i, rr in enumerate(ctx["ranking"])]
    if rank_rows:
        r.table(["#", "Variedad", "Dif. BBCH vs. promedio", "Último estado", "Trayectoria"], rank_rows,
                [.4, 2, 2.6, 3, 1.6], 8)
    else:
        r.para("Se necesitan al menos dos variedades registradas en una misma semana.", 9, MUTED)
    r.h2("Hitos fenológicos", "Primera semana en que cada variedad alcanzó (o superó) el estado indicado.")
    ms = ctx["milestones"]
    r.table(["Variedad"] + [f"{name} (>= {ph.code_str(code)})" for code, name in ms],
            [[{"text": rr["variety"]["name"], "bold": True}]
             + [f"S{h}" if h else {"text": "—", "color": MUTED} for h in rr["hits"]] for rr in rows],
            [2.2] + [1] * len(ms), 7.6, ["left"] + ["center"] * len(ms))
    r.h2(f"Galería paralela ({ctx.get('gallery_label') or 'fotos de detalle'})")
    cols, gap = 8, 5
    w = (CW - gap * (cols - 1)) / cols
    h = w * .75
    def draw_gallery(rr):
        r.ensure(16 + h + 14)
        r.y += 12
        r.text(M, r.y, rr["variety"]["name"], 9.5, True, INK)
        r.y += 5
        gal = rr["gallery"]
        for i in range(0, len(gal), cols):
            r.ensure(h + 14)
            for j, g in enumerate(gal[i:i + cols]):
                x = M + j * (w + gap)
                r.photo(g["src"], x, r.y, w, h)
                cap = ph.week_short(g["week"]) + (f" · {ph.code_str(g['code'])}" if g["code"] is not None else "")
                r.text(x, r.y + h + 8, cap, 6.6, False, MUTED)
            r.y += h + 13

    for rr in rows:
        r.keep(lambda rr=rr: draw_gallery(rr))
    return r


def treatments(ctx: dict, names: dict, num=None) -> Report:
    weeks, rows = ctx["weeks"], ctx["rows"]
    r = _doc(ctx, [f"{ctx['n_t']} tratamientos", f"{ctx['n_r']} repeticiones", f"{len(weeks)} semanas"],
             names, num)
    r.h2("Tratamientos")
    r.table(["Tratamiento", "Variedad", "Descripción", "Parcelas"],
            [[{"text": t["label"], "bold": True}, t.get("variety") or "—", t.get("description") or "—",
              ", ".join(p["name"] for p in t["parcels"])] for t in ctx["treatments"]], [1.5, 1.3, 2.6, 2.2], 8)

    r.h2("Estado BBCH medio por tratamiento",
         "Promedio de las repeticiones en cada semana. Letras distintas = diferencias significativas "
         "(LSD de Fisher, p < 0,05), solo en las semanas en que el ANOVA es significativo.")
    per = 12
    for i in range(0, max(1, len(weeks)), per):
        chunk = weeks[i:i + per]
        trows = []
        for row in rows:
            cells = [{"text": row["t"]["label"], "bold": True}]
            for c in row["cells"][i:i + per]:
                if c["mean"] is None:
                    cells.append({"text": "·", "color": MUTED})
                else:
                    fill, fg = seq_color(int(round(c["mean"])))
                    cells.append({"text": c["text"] + (f" {c['letter']}" if c["letter"] else ""),
                                  "fill": fill, "color": fg, "bold": True})
            trows.append(cells)
        r.table(["Tratamiento"] + [ph.week_short(w) for w in chunk], trows, [2.6] + [1] * len(chunk), 7.4,
                ["left"] + ["center"] * len(chunk))
    if ctx.get("compare"):
        r.h2("Evolución BBCH por tratamiento")
        r.chart(ctx["compare"]["raw"])

    r.h2("ANOVA por semana",
         "Bloques completos al azar (repetición = bloque); si faltan parcelas, ANOVA de un factor. "
         "ns = sin diferencias; * p < 0,05; ** p < 0,01; *** p < 0,001.")
    if ctx["tests"]:
        r.table(["Semana", "F", "gl", "p", "CV", "LSD 5 %"],
                [[ph.week_title(a["week"]), a["f"], a["df"], {"text": a["p"], "bold": True}, a["cv"], a["lsd"]]
                 for a in ctx["tests"]], [2.4, 1, 1, 1.3, 1, 1], 8)
    else:
        r.para("Se necesitan al menos dos tratamientos con repeticiones registradas en una misma semana.", 9, MUTED)

    r.h2("Precocidad relativa",
         "Diferencia media, en unidades BBCH, entre cada tratamiento y el promedio de todos los "
         "tratamientos en las mismas semanas. Positivo = adelantado; negativo = tardío.")
    max_abs = ctx.get("max_abs") or 1.0

    def bar(delta):
        def draw(doc, x, y, w, h):
            mid, cy = x + (w - 34) / 2 + 2, y + h / 2
            doc.line(x + 4, cy, x + w - 34, cy, RULE, 3)
            doc.line(mid, y + 4, mid, y + h - 4, MUTED, .6)
            span = abs(delta) / max_abs * ((w - 38) / 2)
            doc.rect(mid if delta >= 0 else mid - span, cy - 3, span, 6,
                     fill=BERRY if delta >= 0 else "#3e6a8a", radius=2)
            doc.text(x + w - 4, cy + 3, f"{delta:+.1f}", 8, True, INK, "right")
        return {"draw": draw, "h": 18}

    if ctx["ranking"]:
        r.table(["#", "Tratamiento", "Dif. BBCH vs. promedio", "Último BBCH medio"],
                [[{"text": str(i + 1), "color": MUTED}, {"text": rr["t"]["label"], "bold": True},
                  bar(rr["delta"]), rr["last"]] for i, rr in enumerate(ctx["ranking"])], [.4, 2.4, 3, 1.4], 8)
    else:
        r.para("Sin datos suficientes.", 9, MUTED)

    if ctx["milestones"]:
        r.h2("Hitos fenológicos",
             "Semana del año (promedio de las repeticiones) en que cada tratamiento alcanzó el estado; "
             "entre paréntesis, cuántas repeticiones lo alcanzaron.")
        trts = ctx["treatments"]
        r.table(["Hito"] + [t["label"] for t in trts] + ["p"],
                [[{"text": f"{m['name']} (>= {ph.code_str(m['code'])})", "bold": True}]
                 + [f"{c['text']} ({c['n']})" for c in m["cells"]] + [m["p"]] for m in ctx["milestones"]],
                [2.4] + [1.3] * len(trts) + [1.1], 7.4, ["left"] + ["center"] * (len(trts) + 1))

    for m in ctx["measures"]:
        r.h2(m["name"], "Promedio ± error estándar de las repeticiones (las submuestras de una parcela "
                        "se promedian primero). Letras: LSD de Fisher, p < 0,05.")
        def block(b, m=m):
            r.ensure(50)
            r.y += 10
            r.text(M, r.y, ph.week_title(b["week"]), 10, True, INK)
            r.y += 6
            r.table(["Tratamiento"] + list(m["columns"]),
                    [[{"text": row["t"], "bold": True}] + list(row["values"]) for row in b["rows"]]
                    + [[{"text": "p (ANOVA)", "color": MUTED}] + [{"text": p, "color": MUTED} for p in b["p"]]],
                    [2] + [1.6] * len(m["columns"]), 7.8)

        for b in m["blocks"]:
            r.keep(lambda b=b: block(b))

    r.h2("Detalle por parcela", "Código BBCH registrado en cada parcela (T = tratamiento, R = repetición).")
    r.heatmap(ctx["heat"], weeks, label="Parcela")
    return r


RENDERERS = {"weekly.html": weekly, "period.html": period, "variety.html": variety, "matrix.html": matrix,
             "treatments.html": treatments}


def render(template: str, ctx: dict, names: dict, num=None) -> bytes:
    return RENDERERS[template](ctx, names, num).output()
