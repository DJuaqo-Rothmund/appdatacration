"""
pdfdoc.py
=========
Generador de PDF mínimo y sin dependencias externas (solo zlib + Pillow), pensado
para correr en el teléfono: hojas A4, Helvetica (Normal/Negrita, acentos y «ñ» vía
WinAnsi), rectángulos (con esquinas redondeadas), líneas, círculos, fotos JPEG
(se reducen antes de incrustarlas), enlaces y archivos adjuntos incrustados.

Coordenadas: origen ARRIBA a la izquierda, en puntos (1 pt = 1/72 pulgada);
se convierten al sistema del PDF (origen abajo) al escribir.
"""
from __future__ import annotations

import datetime as _dt
import io
import unicodedata
import zlib

PAGE_W, PAGE_H = 595.28, 841.89      # A4

# Anchos de los glifos (1/1000 em) de Helvetica y Helvetica-Bold, por byte WinAnsi (cp1252).
HELVETICA = (
    278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278,
    278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278,
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584, 350,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    278, 333, 556, 556, 556, 556, 260, 556, 333, 737, 370, 556, 584, 333, 737, 333,
    400, 584, 333, 333, 333, 556, 537, 278, 333, 333, 365, 556, 834, 834, 834, 611,
    667, 667, 667, 667, 667, 667, 1000, 722, 667, 667, 667, 667, 278, 278, 278, 278,
    722, 722, 778, 778, 778, 778, 778, 584, 778, 722, 722, 722, 722, 667, 667, 611,
    556, 556, 556, 556, 556, 556, 889, 500, 556, 556, 556, 556, 278, 278, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 584, 611, 556, 556, 556, 556, 500, 556, 500,
)
HELVETICA_BOLD = (
    278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278,
    278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278, 278,
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
    975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
    333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
    611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584, 350,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    278, 333, 556, 556, 556, 556, 280, 556, 333, 737, 370, 556, 584, 333, 737, 333,
    400, 584, 333, 333, 333, 611, 556, 278, 333, 333, 365, 556, 834, 834, 834, 611,
    722, 722, 722, 722, 722, 722, 1000, 722, 667, 667, 667, 667, 278, 278, 278, 278,
    722, 722, 778, 778, 778, 778, 778, 584, 778, 722, 722, 722, 722, 667, 667, 611,
    556, 556, 556, 556, 556, 556, 889, 556, 556, 556, 556, 556, 278, 278, 278, 278,
    611, 611, 611, 611, 611, 611, 611, 584, 611, 611, 611, 611, 611, 556, 611, 556,
)

_REPLACE = {"≥": ">=", "≤": "<=", "→": "->", "←": "<-", "✓": "OK", "✗": "x", "Δ": "Dif.",
            "≈": "~", "📍": "", "\u00a0": " ", "\u2009": " ", "\u202f": " ", "−": "-"}


def _enc(text: str) -> str:
    """Texto -> cadena 1 carácter = 1 byte WinAnsi (como str latin-1 para escribir)."""
    out = []
    for ch in str(text):
        ch = _REPLACE.get(ch, ch)
        try:
            out.append(ch.encode("cp1252").decode("latin-1"))
        except UnicodeEncodeError:
            base = unicodedata.normalize("NFKD", ch).encode("cp1252", "ignore").decode("latin-1")
            out.append(base or "?")
    return "".join(out)


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").replace("\r", "")


def rgb(color) -> tuple[float, float, float]:
    """«#rrggbb» o (r, g, b) 0-255 -> (r, g, b) 0-1."""
    if isinstance(color, str):
        c = color.lstrip("#")
        if len(c) == 3:
            c = "".join(x * 2 for x in c)
        return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return tuple(v / 255 for v in color[:3])


def _pdf_text_string(s: str) -> str:
    """Cadena para el diccionario Info / nombres de adjuntos (UTF-16BE con BOM)."""
    return "<FEFF" + str(s).encode("utf-16-be").hex().upper() + ">"


class PDFDoc:
    K = 0.5523  # aproximación de un cuarto de círculo con Bézier

    def __init__(self, title: str = "", author: str = "PhenoRubus"):
        self.title, self.author = title, author
        self.pages: list[list[str]] = []
        self.annots: list[list[tuple]] = []
        self.images: dict[str, tuple[int, int, bytes]] = {}   # nombre -> (ancho px, alto px, JPEG)
        self._img_cache: dict[tuple, str] = {}
        self.attachments: list[tuple[str, bytes, str]] = []
        self.page_hook = None          # se llama al crear cada hoja (encabezado)
        self.footer = None             # footer(doc, n, total): dibuja el pie al guardar
        self._page = -1

    # ------------------------------------------------------------- hojas
    def add_page(self):
        self.pages.append([])
        self.annots.append([])
        self._page = len(self.pages) - 1
        if self.page_hook:
            self.page_hook(self)

    @property
    def ops(self) -> list[str]:
        return self.pages[self._page]

    # ------------------------------------------------------------- texto
    @staticmethod
    def width(text: str, size: float, bold: bool = False) -> float:
        table = HELVETICA_BOLD if bold else HELVETICA
        return sum(table[ord(ch)] for ch in _enc(text)) * size / 1000

    def text(self, x: float, y: float, text: str, size: float = 10, bold: bool = False,
             color="#22261c", align: str = "left"):
        """y = línea base. align: left | center | right (x es el ancla)."""
        if text is None or text == "":
            return
        if align != "left":
            w = self.width(text, size, bold)
            x -= w / 2 if align == "center" else w
        r, g, b = rgb(color)
        self.ops.append(f"BT {r:.3f} {g:.3f} {b:.3f} rg /{'F2' if bold else 'F1'} {size:.2f} Tf "
                        f"{x:.2f} {PAGE_H - y:.2f} Td ({_esc(_enc(text))}) Tj ET")

    def wrap(self, text: str, max_w: float, size: float, bold: bool = False) -> list[str]:
        """Divide en líneas que caben en max_w (respeta saltos de línea; corta palabras largas)."""
        lines: list[str] = []
        for para in str(text or "").split("\n"):
            words, cur = para.split(" "), ""
            for w in words:
                cand = f"{cur} {w}" if cur else w
                if self.width(cand, size, bold) <= max_w:
                    cur = cand
                    continue
                if cur:
                    lines.append(cur)
                while self.width(w, size, bold) > max_w and len(w) > 1:   # palabra muy larga
                    cut = len(w)
                    while cut > 1 and self.width(w[:cut], size, bold) > max_w:
                        cut -= 1
                    lines.append(w[:cut])
                    w = w[cut:]
                cur = w
            lines.append(cur)
        return lines

    def fit(self, text: str, max_w: float, size: float, bold: bool = False) -> str:
        """Recorta con «…» para que quepa en una línea."""
        text = str(text or "")
        if self.width(text, size, bold) <= max_w:
            return text
        while text and self.width(text + "…", size, bold) > max_w:
            text = text[:-1]
        return text + "…"

    # ----------------------------------------------------------- formas
    def _color(self, fill=None, stroke=None, lw: float = 0.6) -> str:
        out = []
        if fill is not None:
            out.append("%.3f %.3f %.3f rg" % rgb(fill))
        if stroke is not None:
            out.append("%.3f %.3f %.3f RG %.2f w" % (*rgb(stroke), lw))
        return " ".join(out)

    @staticmethod
    def _paint(fill, stroke) -> str:
        return "B" if fill is not None and stroke is not None else ("f" if fill is not None else "S")

    def _rr_path(self, x, y, w, h, r) -> str:
        """Rectángulo con esquinas redondeadas (coordenadas PDF)."""
        Y = PAGE_H - y - h
        r = max(0.0, min(r, w / 2, h / 2))
        if r <= 0:
            return f"{x:.2f} {Y:.2f} {w:.2f} {h:.2f} re"
        k = r * self.K
        x2, y2 = x + w, Y + h
        return (f"{x + r:.2f} {Y:.2f} m {x2 - r:.2f} {Y:.2f} l "
                f"{x2 - r + k:.2f} {Y:.2f} {x2:.2f} {Y + r - k:.2f} {x2:.2f} {Y + r:.2f} c "
                f"{x2:.2f} {y2 - r:.2f} l {x2:.2f} {y2 - r + k:.2f} {x2 - r + k:.2f} {y2:.2f} {x2 - r:.2f} {y2:.2f} c "
                f"{x + r:.2f} {y2:.2f} l {x + r - k:.2f} {y2:.2f} {x:.2f} {y2 - r + k:.2f} {x:.2f} {y2 - r:.2f} c "
                f"{x:.2f} {Y + r:.2f} l {x:.2f} {Y + r - k:.2f} {x + r - k:.2f} {Y:.2f} {x + r:.2f} {Y:.2f} c h")

    def rect(self, x, y, w, h, fill=None, stroke=None, lw: float = 0.6, radius: float = 0):
        if w <= 0 or h <= 0 or (fill is None and stroke is None):
            return
        self.ops.append(f"q {self._color(fill, stroke, lw)} {self._rr_path(x, y, w, h, radius)} "
                        f"{self._paint(fill, stroke)} Q")

    def line(self, x1, y1, x2, y2, color="#ddd6c4", lw: float = 0.6, dash: tuple | None = None):
        d = f"[{' '.join(str(v) for v in dash)}] 0 d " if dash else ""
        self.ops.append(f"q {self._color(stroke=color, lw=lw)} {d}1 J {x1:.2f} {PAGE_H - y1:.2f} m "
                        f"{x2:.2f} {PAGE_H - y2:.2f} l S Q")

    def polyline(self, pts, color="#22261c", lw: float = 1.2):
        if len(pts) < 2:
            return
        path = " ".join(f"{x:.2f} {PAGE_H - y:.2f} {'m' if i == 0 else 'l'}" for i, (x, y) in enumerate(pts))
        self.ops.append(f"q {self._color(stroke=color, lw=lw)} 1 J 1 j {path} S Q")

    def circle(self, cx, cy, r, fill=None, stroke=None, lw: float = 0.6):
        self.rect(cx - r, cy - r, 2 * r, 2 * r, fill, stroke, lw, radius=r)

    # ------------------------------------------------------------ imágenes
    def add_image(self, src, max_px: int = 1000, name: str | None = None) -> tuple[str, int, int] | None:
        """Registra una foto (ruta o bytes); devuelve (nombre, ancho, alto) o None si falla."""
        key = (src if isinstance(src, str) else id(src), max_px)
        if key in self._img_cache and name is None:
            n = self._img_cache[key]
            w, h, _ = self.images[n]
            return n, w, h
        try:
            from PIL import Image, ImageOps
            img = Image.open(src if isinstance(src, str) else io.BytesIO(src))
            img.draft("RGB", (max_px, max_px))          # decodificación JPEG reducida
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((max_px, max_px), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=78, optimize=True)   # base (no progresiva)
        except Exception:  # noqa: BLE001 - foto dañada o inexistente: se omite
            return None
        n = name or f"Im{len(self.images) + 1}"
        self.images[n] = (img.width, img.height, buf.getvalue())
        if name is None:
            self._img_cache[key] = n
        return n, img.width, img.height

    def image(self, src, x, y, w, h, mode: str = "cover", radius: float = 0, max_px: int | None = None,
              name: str | None = None, bg="#ece7d8") -> bool:
        """Dibuja la foto en la caja (x, y, w, h). cover = llena y recorta; contain = completa."""
        reg = self.add_image(src, max_px or int(min(1200, max(w, h) * 2.4)), name)
        if reg is None:
            return False
        n, iw, ih = reg
        if mode == "contain":
            s = min(w / iw, h / ih)
        else:
            s = max(w / iw, h / ih)
        dw, dh = iw * s, ih * s
        dx, dy = x + (w - dw) / 2, y + (h - dh) / 2
        fill = "%.3f %.3f %.3f rg " % rgb(bg) if bg and mode == "contain" else ""
        bgpath = f"{self._rr_path(x, y, w, h, radius)} f " if fill else ""
        self.ops.append(f"q {fill}{bgpath}{self._rr_path(x, y, w, h, radius)} W n "
                        f"{dw:.2f} 0 0 {dh:.2f} {dx:.2f} {PAGE_H - dy - dh:.2f} cm /{n} Do Q")
        return True

    # ------------------------------------------------------ enlaces/adjuntos
    def link(self, x, y, w, h, url: str):
        self.annots[self._page].append((x, PAGE_H - y - h, x + w, PAGE_H - y, url))

    def attach(self, name: str, data: bytes, mime: str = "application/octet-stream"):
        self.attachments.append((name, data, mime))

    # --------------------------------------------------------------- salida
    def output(self) -> bytes:
        objs: list[bytes | None] = [None]          # índice 0 sin uso (los objetos parten en 1)

        def new(body: bytes | None = None) -> int:
            objs.append(body)
            return len(objs) - 1

        def stream(dict_items: str, data: bytes, compress: bool = True) -> bytes:
            if compress:
                data = zlib.compress(data, 6)
                dict_items += " /Filter /FlateDecode"
            return (f"<< {dict_items} /Length {len(data)} >>\nstream\n".encode("latin-1")
                    + data + b"\nendstream")

        catalog, pages_id = new(), new()
        f1 = new(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = new(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        img_ids = {}
        for n, (w, h, data) in self.images.items():
            img_ids[n] = new(stream(f"/Type /XObject /Subtype /Image /Width {w} /Height {h} "
                                    f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode",
                                    data, compress=False))
        xobjects = " ".join(f"/{n} {i} 0 R" for n, i in img_ids.items())
        resources = (f"<< /Font << /F1 {f1} 0 R /F2 {f2} 0 R >>"
                     + (f" /XObject << {xobjects} >>" if xobjects else "") + " >>")
        total = len(self.pages)
        kids = []
        for i in range(total):
            if self.footer:               # el pie (con «Página n de N») se dibuja al final
                self._page = i
                self.footer(self, i + 1, total)
            content = new(stream("", "\n".join(self.pages[i]).encode("latin-1")))
            annots = []
            for (x1, y1, x2, y2, url) in self.annots[i]:
                annots.append(new(
                    f"<< /Type /Annot /Subtype /Link /Rect [{x1:.2f} {y1:.2f} {x2:.2f} {y2:.2f}] "
                    f"/Border [0 0 0] /A << /S /URI /URI ({_esc(_enc(url))}) >> >>".encode("latin-1")))
            ann = f" /Annots [{' '.join(f'{a} 0 R' for a in annots)}]" if annots else ""
            kids.append(new(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                            f"/Resources {resources} /Contents {content} 0 R{ann} >>".encode("latin-1")))
        objs[pages_id] = (f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] "
                          f"/Count {len(kids)} >>").encode("latin-1")
        names = ""
        if self.attachments:
            specs = []
            for name, data, mime in sorted(self.attachments):
                ef = new(stream(f"/Type /EmbeddedFile /Subtype /{mime.replace('/', '#2F')} "
                                f"/Params << /Size {len(data)} >>", data))
                fs = new(f"<< /Type /Filespec /F ({_esc(_enc(name))}) /UF {_pdf_text_string(name)} "
                         f"/EF << /F {ef} 0 R >> >>".encode("latin-1"))
                specs.append(f"({_esc(_enc(name))}) {fs} 0 R")
            names = f" /Names << /EmbeddedFiles << /Names [{' '.join(specs)}] >> >>"
        objs[catalog] = f"<< /Type /Catalog /Pages {pages_id} 0 R{names} >>".encode("latin-1")
        now = _dt.datetime.now().strftime("D:%Y%m%d%H%M%S")
        info = new(f"<< /Title {_pdf_text_string(self.title)} /Author {_pdf_text_string(self.author)} "
                   f"/Producer (PhenoRubus) /CreationDate ({now}) >>".encode("latin-1"))

        out = io.BytesIO()
        out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for i in range(1, len(objs)):
            offsets.append(out.tell())
            out.write(f"{i} 0 obj\n".encode() + objs[i] + b"\nendobj\n")
        xref = out.tell()
        out.write(f"xref\n0 {len(objs)}\n0000000000 65535 f \n".encode())
        for off in offsets[1:]:
            out.write(f"{off:010d} 00000 n \n".encode())
        out.write(f"trailer\n<< /Size {len(objs)} /Root {catalog} 0 R /Info {info} 0 R >>\n"
                  f"startxref\n{xref}\n%%EOF\n".encode())
        return out.getvalue()

    def save(self, path: str) -> str:
        with open(path, "wb") as f:
            f.write(self.output())
        return path
