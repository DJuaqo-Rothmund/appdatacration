"""
ai_classifier.py
================
Sugerencia automática del estado fenológico (escala BBCH de frambueso) a
partir de la foto de detalle, 100 % local (Edge AI, sin conexión).

Arquitectura
------------
1. **Extracción de descriptores** (embedding L2-normalizado):
   * ``HandcraftedExtractor`` (siempre disponible, solo numpy + Pillow):
     histogramas HSV (imagen completa y zona central), fracciones de clases
     cromáticas agronómicas (blanco-pétalo, rosado, rojo frambuesa, verde,
     pardo, amarillo...), histograma de orientaciones de gradiente (HOG
     simplificado 3×3), magnitud de gradiente y LBP uniforme (textura de
     drupéolas / yemas).
   * ``TFLiteExtractor`` (opcional): MobileNetV3-Small sin cabeza
     (``assets/models/mobilenet_v3_small_embed.tflite``) ejecutado con
     ``tflite_runtime``. Se genera con ``tools/export_mobilenet_tflite.py``.
2. **Clasificador few-shot k-NN** por similitud coseno sobre la base de
   referencia local (``ai_references``), que el usuario alimenta
   etiquetando fotos reales en el módulo de calibración (PIN).
3. **Priors agronómicos** que permiten sugerir desde el día 1 (sin
   referencias) y regularizan el k-NN:
   * temporal: monotonía fenológica (la variedad no retrocede respecto de
     su último registro) + calendario esperado por semana de muestreo;
   * cromático: reglas sobre las fracciones de color (flor blanca -> 6x,
     rojo -> 8x, pardo -> 0x/9x, ...);
   * textual: coincidencia de las notas de campo con las claves de los
     estadios (enriquecidas con los documentos técnicos cargados).
   Se combinan en un modelo log-lineal y se devuelve el top-3 con confianza.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageOps

import phenology as ph
from platform_utils import resource_path

DEFAULT_PIN = "1234"
TFLITE_MODEL = resource_path("assets", "models", "mobilenet_v3_small_embed.tflite")
TFLITE_META = resource_path("assets", "models", "model.json")


# ===========================================================================
# Utilidades de imagen
# ===========================================================================
def load_image(path: str, size: int = 224, center_crop: float = 0.9) -> Image.Image:
    """Abre, corrige orientación EXIF, recorta al centro (cuadrado) y redimensiona."""
    img = Image.open(path)
    img = ImageOps.exif_transpose(img).convert("RGB")
    w, h = img.size
    side = int(min(w, h) * center_crop)
    left, top = (w - side) // 2, (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    return img.resize((size, size), Image.BILINEAR)


# Clases cromáticas (H en grados 0-360, S y V en 0-1)
COLOR_CLASSES = ["white", "pink", "red", "dark_red", "green", "yellow_green",
                 "yellow", "brown"]


def color_masks(hsv: np.ndarray) -> dict[str, np.ndarray]:
    h = hsv[..., 0].astype(np.float32) * (360.0 / 255.0)
    s = hsv[..., 1].astype(np.float32) / 255.0
    v = hsv[..., 2].astype(np.float32) / 255.0
    reddish = (h >= 330) | (h < 15)
    return {
        "white": (s < 0.18) & (v > 0.72),
        "pink": (reddish | ((h >= 300) & (h < 330))) & (s >= 0.18) & (s < 0.5) & (v > 0.55),
        "red": reddish & (s >= 0.5) & (v > 0.35),
        "dark_red": (reddish | ((h >= 280) & (h < 330))) & (s >= 0.35) & (v > 0.08) & (v <= 0.35),
        "green": (h >= 75) & (h < 170) & (s >= 0.2) & (v > 0.15),
        "yellow_green": (h >= 55) & (h < 75) & (s >= 0.25) & (v > 0.2),
        "yellow": (h >= 40) & (h < 55) & (s >= 0.35) & (v > 0.35),
        "brown": (h >= 15) & (h < 40) & (s >= 0.2) & (v > 0.1) & (v <= 0.6),
    }


def color_profile(img: Image.Image) -> dict[str, float]:
    """Fracción de píxeles de cada clase cromática (imagen completa)."""
    hsv = np.asarray(img.convert("HSV"))
    masks = color_masks(hsv)
    total = float(hsv.shape[0] * hsv.shape[1])
    return {k: float(m.sum()) / total for k, m in masks.items()}


def _l2(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


# ===========================================================================
# Extractores de características
# ===========================================================================
class FeatureExtractor:
    name = "base"
    dim = 0

    def extract(self, img: Image.Image) -> np.ndarray:  # pragma: no cover - interfaz
        raise NotImplementedError

    def extract_path(self, path: str) -> np.ndarray:
        return self.extract(load_image(path, self.input_size))

    input_size = 224


class HandcraftedExtractor(FeatureExtractor):
    """Descriptores ligeros de color + textura (numpy puro, ~5 ms en móvil)."""
    name = "rubus-hc-v1"
    input_size = 192
    H_BINS, S_BINS, V_BINS = 9, 3, 3
    WEIGHTS = {"hist": 1.0, "hist_c": 1.0, "frac": 1.5, "hog": 0.7, "mag": 0.5, "lbp": 0.7}

    def __init__(self):
        self.dim = (2 * self.H_BINS * self.S_BINS * self.V_BINS + 2 * len(COLOR_CLASSES)
                    + 81 + 8 + 10)

    def _hsv_hist(self, hsv: np.ndarray) -> np.ndarray:
        h = (hsv[..., 0].astype(np.int32) * self.H_BINS) // 256
        s = (hsv[..., 1].astype(np.int32) * self.S_BINS) // 256
        v = (hsv[..., 2].astype(np.int32) * self.V_BINS) // 256
        idx = (h * self.S_BINS + s) * self.V_BINS + v
        hist = np.bincount(idx.ravel(), minlength=self.H_BINS * self.S_BINS * self.V_BINS)
        hist = hist.astype(np.float32) / max(1, idx.size)
        return np.sqrt(hist)  # Hellinger

    @staticmethod
    def _fractions(hsv: np.ndarray) -> np.ndarray:
        masks = color_masks(hsv)
        total = float(hsv.shape[0] * hsv.shape[1])
        return np.sqrt(np.array([masks[k].sum() / total for k in COLOR_CLASSES],
                                dtype=np.float32))

    @staticmethod
    def _gradients(gray: np.ndarray):
        gx = np.zeros_like(gray)
        gy = np.zeros_like(gray)
        gx[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
        gy[1:-1, :] = gray[2:, :] - gray[:-2, :]
        mag = np.hypot(gx, gy)
        ang = (np.degrees(np.arctan2(gy, gx)) + 180.0) % 180.0
        return mag, ang

    @staticmethod
    def _hog(mag: np.ndarray, ang: np.ndarray, grid: int = 3, bins: int = 9) -> np.ndarray:
        H, W = mag.shape
        out = []
        b = np.minimum((ang / (180.0 / bins)).astype(np.int32), bins - 1)
        for i in range(grid):
            for j in range(grid):
                ys = slice(i * H // grid, (i + 1) * H // grid)
                xs = slice(j * W // grid, (j + 1) * W // grid)
                cell = np.bincount(b[ys, xs].ravel(), weights=mag[ys, xs].ravel(),
                                   minlength=bins).astype(np.float32)
                out.append(_l2(cell))
        return np.concatenate(out)

    @staticmethod
    def _mag_hist(mag: np.ndarray) -> np.ndarray:
        edges = np.array([0, 2, 4, 8, 16, 32, 64, 128, 1e9], dtype=np.float32)
        hist, _ = np.histogram(mag, bins=edges)
        return np.sqrt(hist.astype(np.float32) / max(1, mag.size))

    @staticmethod
    def _lbp_uniform(gray: np.ndarray) -> np.ndarray:
        """LBP 8-vecinos invariante a rotación y uniforme (10 bins)."""
        c = gray[1:-1, 1:-1]
        offs = [(-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1)]
        H, W = gray.shape
        bits = np.stack([(gray[1 + dy:H - 1 + dy, 1 + dx:W - 1 + dx] >= c + 2.0)
                         for dy, dx in offs], axis=0).astype(np.int8)
        ones = bits.sum(axis=0)
        transitions = np.abs(np.diff(np.concatenate([bits, bits[:1]], axis=0), axis=0)).sum(axis=0)
        code = np.where(transitions <= 2, ones, 9)
        hist = np.bincount(code.ravel(), minlength=10).astype(np.float32)
        return np.sqrt(hist / max(1, code.size))

    def extract(self, img: Image.Image) -> np.ndarray:
        if img.size != (self.input_size, self.input_size):
            img = img.resize((self.input_size, self.input_size), Image.BILINEAR)
        hsv = np.asarray(img.convert("HSV"))
        n = self.input_size
        q = n // 4
        hsv_c = hsv[q:n - q, q:n - q]
        gray = np.asarray(img.convert("L"), dtype=np.float32)
        mag, ang = self._gradients(gray)
        blocks = {
            "hist": self._hsv_hist(hsv),
            "hist_c": self._hsv_hist(hsv_c),
            "frac": np.concatenate([self._fractions(hsv), self._fractions(hsv_c)]),
            "hog": self._hog(mag, ang),
            "mag": self._mag_hist(mag),
            "lbp": self._lbp_uniform(gray),
        }
        vec = np.concatenate([_l2(blocks[k]) * w for k, w in self.WEIGHTS.items()])
        return _l2(vec.astype(np.float32))


class TFLiteExtractor(FeatureExtractor):
    """Embeddings MobileNetV3-Small (TFLite) + bloque cromático agronómico."""
    name = "mnv3s-tflite+hc"
    input_size = 224

    def __init__(self, model_path: str = TFLITE_MODEL):
        try:
            from tflite_runtime.interpreter import Interpreter  # type: ignore
        except ImportError:  # PC con TensorFlow completo
            from tensorflow.lite import Interpreter  # type: ignore
        self.interpreter = Interpreter(model_path=model_path, num_threads=2)
        self.interpreter.allocate_tensors()
        self.inp = self.interpreter.get_input_details()[0]
        self.out = self.interpreter.get_output_details()[0]
        meta = {}
        if os.path.exists(TFLITE_META):
            with open(TFLITE_META, encoding="utf-8") as f:
                meta = json.load(f)
        self.input_scale = meta.get("input_scale", "0_255")
        self.hc = HandcraftedExtractor()
        self.dim = int(np.prod(self.out["shape"][1:])) + self.hc.dim

    def _prepare(self, img: Image.Image) -> np.ndarray:
        _, h, w, _ = self.inp["shape"]
        arr = np.asarray(img.resize((int(w), int(h)), Image.BILINEAR), dtype=np.float32)
        if self.inp["dtype"] == np.uint8:
            return arr.astype(np.uint8)[None]
        if self.input_scale == "-1_1":
            arr = arr / 127.5 - 1.0
        elif self.input_scale == "0_1":
            arr = arr / 255.0
        return arr[None].astype(self.inp["dtype"])

    def extract(self, img: Image.Image) -> np.ndarray:
        self.interpreter.set_tensor(self.inp["index"], self._prepare(img))
        self.interpreter.invoke()
        emb = self.interpreter.get_tensor(self.out["index"]).reshape(-1).astype(np.float32)
        if self.out["dtype"] == np.uint8:
            scale, zero = self.out.get("quantization", (1.0, 0))
            emb = (emb - zero) * scale
        hc = self.hc.extract(img)
        return _l2(np.concatenate([_l2(emb) * 1.0, hc * 0.6]))


_EXTRACTOR: FeatureExtractor | None = None


def get_extractor(prefer_tflite: bool = True) -> FeatureExtractor:
    """El mejor extractor disponible (TFLite si hay modelo y runtime; si no, HC)."""
    global _EXTRACTOR
    if _EXTRACTOR is not None:
        return _EXTRACTOR
    if prefer_tflite and os.path.exists(TFLITE_MODEL):
        try:
            _EXTRACTOR = TFLiteExtractor()
            return _EXTRACTOR
        except Exception:  # runtime no disponible: degradar con elegancia
            pass
    _EXTRACTOR = HandcraftedExtractor()
    return _EXTRACTOR


# ===========================================================================
# Texto: documentos técnicos y notas
# ===========================================================================
STOPWORDS = set("""de la el los las un una y o en con por para del al se que su sus
es son como mas muy sin sobre entre hasta desde cada todo toda todos todas este esta
estos estas ese esa aun ya fase estado estadio bbch planta plantas""".split())


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]{3,}", normalize_text(text)) if t not in STOPWORDS]


def extract_document_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader  # import perezoso: dependencia pura Python
        except BaseException as exc:  # noqa: BLE001 - p.ej. backend cripto roto
            raise RuntimeError(f"No se pudo cargar el lector PDF (pypdf): {exc}") from exc
        reader = PdfReader(path)
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    for enc in ("utf-8", "latin-1"):
        try:
            with open(path, encoding=enc) as f:
                text = f.read()
            break
        except UnicodeDecodeError:
            continue
    if ext in (".html", ".htm"):
        text = re.sub(r"<[^>]+>", " ", text)
    return text


_DOC_PATTERNS = [
    re.compile(r"BBCH\s*[-:]?\s*(\d{1,2})\s*[:\-–—.)=]\s*(.{3,240})", re.I),
    re.compile(r"^\s*(\d{2})\s*[:\-–—.)]\s+(.{3,240})$", re.M),
]


def parse_bbch_entries(text: str) -> dict[int, str]:
    """Busca claves BBCH en un documento: 'BBCH 65: Plena floración...' o '65 - ...'."""
    found: dict[int, str] = {}
    for pat in _DOC_PATTERNS:
        for m in pat.finditer(text):
            code = int(m.group(1))
            desc = re.sub(r"\s+", " ", m.group(2)).strip(" .;:-")
            if 0 <= code <= 99 and desc and code not in found:
                found[code] = desc
    return found


# ===========================================================================
# Resultado de la sugerencia
# ===========================================================================
@dataclass
class Suggestion:
    code: int
    label: str
    confidence: float
    top: list[tuple[int, float]] = field(default_factory=list)
    explanation: str = ""
    components: dict = field(default_factory=dict)

    def as_detail_json(self) -> str:
        return json.dumps({"top": self.top, "explanation": self.explanation,
                           "components": self.components}, ensure_ascii=False,
                          default=lambda o: o.item() if hasattr(o, "item") else str(o))


# ===========================================================================
# Clasificador
# ===========================================================================
class PhenologyClassifier:
    K = 7
    TAU = 0.03

    def __init__(self, db, extractor: FeatureExtractor | None = None):
        self.db = db
        self.extractor = extractor or get_extractor()
        self._cache: tuple[np.ndarray, np.ndarray] | None = None

    # ------------------------------------------------------------- catálogo
    def catalog(self) -> list[dict]:
        rows = self.db.list_bbch()
        return rows or [{"code": c, "label": n, "description": d, "keywords": k}
                        for c, n, d, k in ph.BBCH_RUBUS]

    def label(self, code: int) -> str:
        return ph.bbch_label(code, self.db.bbch_names())

    # ------------------------------------------------------- base de referencia
    def _references(self) -> tuple[np.ndarray, np.ndarray]:
        if self._cache is None:
            rows = self.db.list_references(self.extractor.name)
            if rows:
                X = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
                y = np.array([r["bbch_code"] for r in rows], dtype=np.int32)
            else:
                X = np.zeros((0, self.extractor.dim), dtype=np.float32)
                y = np.zeros((0,), dtype=np.int32)
            self._cache = (X, y)
        return self._cache

    def invalidate(self) -> None:
        self._cache = None

    def add_reference(self, image_path: str, bbch_code: int, photo_id: int | None = None) -> int:
        emb = self.extractor.extract_path(image_path)
        ref_id = self.db.add_reference(bbch_code, self.extractor.name, emb.tobytes(),
                                       int(emb.size), image_path, photo_id)
        self.invalidate()
        return ref_id

    def remove_reference(self, ref_id: int) -> None:
        self.db.delete_reference(ref_id)
        self.invalidate()

    def rebuild(self, progress=None) -> int:
        """Re-entrena: recalcula todos los embeddings con el extractor actual."""
        rows = self.db.list_references()
        done = 0
        for i, r in enumerate(rows):
            path = r["image_path"]
            if path and os.path.exists(path):
                emb = self.extractor.extract_path(path)
                self.db.execute(
                    "UPDATE ai_references SET embedding=?, dim=?, extractor=? WHERE id=?",
                    (emb.tobytes(), int(emb.size), self.extractor.name, r["id"]))
                done += 1
            if progress:
                progress(i + 1, len(rows))
        self.invalidate()
        self.db.log("retrain", "ai_references", None,
                    f"{done} embeddings recalculados con {self.extractor.name}")
        return done

    # ---------------------------------------------------------- componentes
    def _knn(self, q: np.ndarray, codes: np.ndarray, exclude: int | None = None):
        X, y = self._references()
        if exclude is not None and len(y):
            mask = np.ones(len(y), dtype=bool)
            mask[exclude] = False
            X, y = X[mask], y[mask]
        if len(y) == 0:
            return None, {}
        # Centrado respecto de la media de referencias: mejora el contraste coseno.
        if len(y) >= 3:
            mu = X.mean(axis=0)
            Xc = X - mu
            Xc /= np.maximum(np.linalg.norm(Xc, axis=1, keepdims=True), 1e-9)
            qc = _l2(q - mu)
        else:
            Xc, qc = X, q
        sims = Xc @ qc
        k = min(self.K, len(y))
        idx = np.argsort(-sims)[:k]
        w = np.exp((sims[idx] - sims[idx].max()) / self.TAU)
        votes: dict[int, float] = {}
        for i, wi in zip(idx, w):
            votes[int(y[i])] = votes.get(int(y[i]), 0.0) + float(wi)
        # Suavizado ordinal: los votos "derraman" hacia códigos vecinos.
        p = np.full(len(codes), 1e-3)
        for code, wv in votes.items():
            p += wv * np.exp(-0.5 * ((codes - code) / 2.5) ** 2)
        p /= p.sum()
        total = float(w.sum())
        info = {"k": int(k), "votes": {str(c): round(float(v) / total, 3) for c, v in votes.items()},
                "best_sim": round(float(sims[idx[0]]), 3), "n_refs": int(len(y))}
        return p, info

    @staticmethod
    def _temporal(codes: np.ndarray, week_number: int | None,
                  previous: tuple[int, int] | None):
        """previous = (bbch_code, week_number) del último registro de la variedad."""
        if previous and week_number:
            prev_code, prev_week = previous
            gap = max(0, week_number - prev_week)
            rate = 0.0
            if gap:
                rate = (ph.expected_bbch_for_week(week_number)
                        - ph.expected_bbch_for_week(prev_week)) / gap
            expected = prev_code + max(0.0, rate) * gap
            sigma = 5.0 + 3.0 * gap
            p = np.exp(-0.5 * ((codes - expected) / sigma) ** 2)
            p = np.where(codes < prev_code - 2, p * 0.1, p)  # sin retroceso fenológico
            info = {"mode": "historial", "expected": round(float(expected), 1),
                    "previous": int(prev_code)}
        elif week_number:
            expected = ph.expected_bbch_for_week(week_number)
            p = np.exp(-0.5 * ((codes - expected) / 14.0) ** 2)
            info = {"mode": "calendario", "expected": round(float(expected), 1)}
        else:
            return np.full(len(codes), 1.0 / len(codes)), {"mode": "uniforme"}
        p = p + 1e-3
        return p / p.sum(), info

    @staticmethod
    def _color(codes: np.ndarray, prof: dict[str, float]):
        macro = codes // 10
        s = np.ones(len(codes))
        white, pink, red = prof["white"], prof["pink"], prof["red"]
        dark, green, brown = prof["dark_red"], prof["green"], prof["brown"]
        yellow = prof["yellow"] + prof["yellow_green"] * 0.5
        berry = pink + red + dark
        if white > 0.04:
            f = 1.0 + min(white, 0.35) * 14
            s *= np.where(macro == 6, f, 1.0)
            s *= np.where(np.isin(codes, [59, 60]), 1.0 + f * 0.4, 1.0)
        if berry > 0.03:
            f = 1.0 + min(berry, 0.4) * 16
            s *= np.where(macro == 8, f, 1.0)
            ripe = (red + 1.5 * dark) / (berry + 1e-6)  # 0 rosado ... 1.5 oscuro
            target = 81 + 8 * min(1.0, ripe / 1.2)
            s *= np.where(macro == 8, np.exp(-0.5 * ((codes - target) / 3.0) ** 2) + 0.3, 1.0)
            s *= np.where(macro < 7, 0.5, 1.0)
        else:
            s *= np.where(macro == 8, 0.6, 1.0)
        if brown > 0.2 and green < 0.1:
            s *= np.where((macro == 0) | (macro == 9), 2.5, 0.7)
        if green > 0.35 and berry < 0.02 and white < 0.02:
            s *= np.where(np.isin(macro, [1, 3, 5, 7]), 1.5, 1.0)
        if yellow > 0.2:
            s *= np.where(macro == 9, 2.0, 1.0)
        s = s / s.sum()
        return s, {k: round(v, 3) for k, v in prof.items()}

    def _text(self, codes: np.ndarray, notes: str, catalog: list[dict]):
        toks = set(tokenize(notes))
        if not toks:
            return None, {}
        by_code = {r["code"]: set(tokenize(f"{r['label']} {r.get('description', '')} "
                                           f"{r.get('keywords', '')}")) for r in catalog}
        scores = np.array([len(toks & by_code.get(int(c), set())) for c in codes], dtype=float)
        if scores.max() <= 0:
            return None, {}
        p = 1.0 + 2.0 * scores / scores.max()
        return p / p.sum(), {"matches": int(scores.max())}

    # ------------------------------------------------------------ inferencia
    def suggest(self, image_path: str, week_number: int | None = None,
                previous: tuple[int, int] | None = None, notes: str = "") -> Suggestion:
        t0 = time.time()
        catalog = self.catalog()
        codes = np.array([r["code"] for r in catalog], dtype=np.float64)
        img = load_image(image_path, self.extractor.input_size)
        q = self.extractor.extract(img)
        prof = color_profile(img)

        comps: dict = {}
        logp = np.zeros(len(codes))
        p_knn, knn_info = self._knn(q, codes)
        n_refs = knn_info.get("n_refs", 0)
        if p_knn is not None:
            w_knn = 1.2 if n_refs >= 10 else (0.8 if n_refs >= 4 else 0.4)
            logp += w_knn * np.log(p_knn)
            comps["knn"] = knn_info | {"weight": w_knn}
        p_t, t_info = self._temporal(codes, week_number, previous)
        logp += 0.8 * np.log(p_t)
        comps["temporal"] = t_info
        p_c, c_info = self._color(codes, prof)
        logp += (0.5 if n_refs >= 10 else 0.8) * np.log(p_c)
        comps["color"] = c_info
        p_x, x_info = self._text(codes, notes, catalog)
        if p_x is not None:
            logp += 0.6 * np.log(p_x)
            comps["notas"] = x_info

        p = np.exp(logp - logp.max())
        p /= p.sum()
        order = np.argsort(-p)[:3]
        top = [(int(codes[i]), round(float(p[i]), 3)) for i in order]
        best = top[0][0]
        # Confianza a nivel de estadio principal (más estable que el código exacto).
        macro_conf = float(p[(codes // 10) == best // 10].sum())
        comps["ms"] = round((time.time() - t0) * 1000, 1)
        return Suggestion(code=best, label=self.label(best),
                          confidence=round(0.5 * top[0][1] + 0.5 * macro_conf, 3),
                          top=top, explanation=self._explain(comps, prof), components=comps)

    @staticmethod
    def _explain(comps: dict, prof: dict) -> str:
        parts = []
        k = comps.get("knn")
        if k:
            best = max(k["votes"].items(), key=lambda kv: kv[1])
            parts.append(f"k-NN ({k['n_refs']} ref.): BBCH {best[0]} ({best[1]:.0%} del voto)")
        else:
            parts.append("Sin referencias etiquetadas: priors agronómicos")
        t = comps.get("temporal", {})
        if t.get("mode") == "historial":
            parts.append(f"historial: último BBCH {t['previous']}, esperado ≈{t['expected']:.0f}")
        elif t.get("mode") == "calendario":
            parts.append(f"calendario: esperado ≈ BBCH {t['expected']:.0f}")
        dom = sorted(prof.items(), key=lambda kv: -kv[1])[:2]
        names = {"white": "blanco", "pink": "rosado", "red": "rojo", "dark_red": "rojo oscuro",
                 "green": "verde", "yellow_green": "verde amarillo", "yellow": "amarillo",
                 "brown": "pardo"}
        parts.append("color: " + ", ".join(f"{names[c]} {v:.0%}" for c, v in dom))
        if "notas" in comps:
            parts.append("notas coinciden con claves del estadio")
        return " · ".join(parts)

    # ------------------------------------------------------------ evaluación
    def evaluate(self) -> dict:
        """Validación cruzada leave-one-out del k-NN sobre la base de referencia."""
        X, y = self._references()
        n = len(y)
        if n < 3:
            return {"n": n, "exact": None, "macro": None}
        codes = np.array(sorted({r["code"] for r in self.catalog()} | set(y.tolist())),
                         dtype=np.float64)
        exact = macro = 0
        for i in range(n):
            p, _ = self._knn(X[i], codes, exclude=i)
            pred = int(codes[int(np.argmax(p))])
            exact += pred == int(y[i])
            macro += pred // 10 == int(y[i]) // 10
        return {"n": n, "exact": exact / n, "macro": macro / n}

    # ------------------------------------------------------------ documentos
    def import_document(self, path: str, title: str | None = None) -> tuple[int, int]:
        text = extract_document_text(path)
        entries = parse_bbch_entries(text)
        existing = {r["code"]: r for r in self.db.list_bbch()}
        for code, desc in entries.items():
            if code in existing:
                r = existing[code]
                kw = " ".join(sorted(set(tokenize(r["keywords"])) | set(tokenize(desc))))
                description = r["description"] or desc
                self.db.upsert_bbch(code, r["label"], description, kw,
                                    source=r["source"] if r["source"] != "base" else "base+doc")
            else:
                label = " ".join(desc.split()[:6])
                self.db.upsert_bbch(code, label, desc, " ".join(tokenize(desc)), source="doc")
        doc_id = self.db.add_document(title or os.path.basename(path), path, text, len(entries))
        return doc_id, len(entries)


# ===========================================================================
# Acceso seguro al módulo de calibración
# ===========================================================================
class PinGuard:
    """PIN numérico (por defecto 1234) almacenado como hash SHA-256 con sal."""
    MAX_ATTEMPTS = 5
    LOCKOUT_S = 30

    def __init__(self, db):
        self.db = db
        self._fails = 0
        self._locked_until = 0.0
        if not db.get_setting("ai_pin_hash"):
            self._store(DEFAULT_PIN)

    def _store(self, pin: str) -> None:
        salt = os.urandom(8).hex()
        self.db.set_setting("ai_pin_salt", salt)
        self.db.set_setting("ai_pin_hash", hashlib.sha256((salt + pin).encode()).hexdigest())

    @property
    def locked_seconds(self) -> int:
        return max(0, int(math.ceil(self._locked_until - time.time())))

    def verify(self, pin: str) -> bool:
        if self.locked_seconds:
            return False
        salt = self.db.get_setting("ai_pin_salt", "")
        ok = hashlib.sha256((salt + (pin or "")).encode()).hexdigest() == \
            self.db.get_setting("ai_pin_hash")
        if ok:
            self._fails = 0
        else:
            self._fails += 1
            if self._fails >= self.MAX_ATTEMPTS:
                self._locked_until = time.time() + self.LOCKOUT_S
                self._fails = 0
        return ok

    def change(self, old: str, new: str) -> bool:
        if not (new.isdigit() and 4 <= len(new) <= 8):
            raise ValueError("El PIN debe tener entre 4 y 8 dígitos.")
        if not self.verify(old):
            return False
        self._store(new)
        self.db.log("update", "ai_pin", None, "PIN cambiado")
        return True
