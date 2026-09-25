"""
phenology.py
============
Conocimiento agronómico base de la app:

* Escala BBCH adaptada a frambueso (Rubus idaeus), según Meier (2001) y
  la codificación BBCH para frutales de baya (bush/cane fruit).
* Cálculo de "Semanas de Muestreo" a partir de una semana de referencia
  (por defecto: Semana 1 = semana del 7 de septiembre).
* Calendario fenológico esperado (hemisferio sur, frambueso floricane),
  usado como *prior* temporal por el clasificador.

Este módulo es puro Python (sin Kivy ni Android) para poder testearlo en PC.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Escala BBCH — frambueso
# ---------------------------------------------------------------------------
# (código, etiqueta corta, descripción de campo, palabras clave)
BBCH_RUBUS: list[tuple[int, str, str, str]] = [
    # Estadio principal 0: Desarrollo de yemas
    (0, "Dormancia", "Yemas cerradas, cubiertas por escamas pardas.",
     "latencia dormancia yema cerrada escamas pardas invierno"),
    (1, "Inicio hinchazón de yemas", "Yemas comienzan a hincharse; escamas se separan levemente.",
     "hinchazon yema inicio escamas"),
    (3, "Yemas hinchadas", "Fin de la hinchazón: escamas claras con bordes visibles.",
     "yema hinchada escamas claras"),
    (7, "Inicio de brotación", "Primeras puntas verdes de hojas visibles.",
     "brotacion punta verde apertura yema"),
    (9, "Punta verde", "Puntas de hojas ~5 mm sobre las escamas.",
     "punta verde 5 mm hojas emergiendo"),
    # 1: Desarrollo de hojas
    (10, "Oreja de ratón", "Primeras hojas separándose (estado 'oreja de ratón').",
     "oreja raton hojas separandose"),
    (11, "Primeras hojas desplegadas", "Primeras hojas desplegadas.",
     "primeras hojas desplegadas"),
    (15, "Más hojas desplegadas", "Varias hojas desplegadas, laterales en crecimiento.",
     "hojas desplegadas laterales"),
    (19, "Hojas completamente expandidas", "Primeras hojas alcanzan tamaño final.",
     "hojas expandidas tamano final"),
    # 3: Crecimiento de brotes / laterales / cañas
    (31, "Inicio crecimiento de brotes", "Ejes de laterales y cañas nuevas visibles.",
     "crecimiento brotes laterales canas nuevas primocana"),
    (35, "Brotes al 50 %", "Laterales / cañas al 50 % de su longitud final.",
     "brotes 50 largo final"),
    (39, "Brotes al 90 %", "Laterales / cañas al 90 % de su longitud final.",
     "brotes 90 largo final"),
    # 5: Aparición del órgano floral
    (51, "Botones florales visibles", "Inflorescencias visibles, botones cerrados agrupados.",
     "botones florales visibles inflorescencia racimo"),
    (55, "Botones florales separados", "Primeros botones individuales visibles (aún cerrados).",
     "botones separados cerrados"),
    (57, "Sépalos abiertos", "Primeras flores con sépalos abiertos; pétalos aún no visibles.",
     "sepalos abiertos"),
    (59, "Estado de globo", "Pétalos visibles, flor aún cerrada (globo).",
     "petalos visibles globo balon"),
    # 6: Floración
    (60, "Primeras flores abiertas", "Primeras flores abiertas (esporádicas).",
     "primeras flores abiertas"),
    (61, "Inicio de floración", "~10 % de flores abiertas.",
     "inicio floracion 10 flores"),
    (65, "Plena floración", "~50 % de flores abiertas; primeros pétalos caen.",
     "plena floracion 50 flores blancas abiertas"),
    (67, "Flores marchitándose", "Mayoría de pétalos caídos.",
     "flores marchitas petalos caidos"),
    (69, "Fin de floración", "Todos los pétalos caídos; estambres secos.",
     "fin floracion estambres secos"),
    # 7: Desarrollo del fruto
    (71, "Cuajado", "Primeros frutos cuajados; drupéolas verdes visibles.",
     "cuajado fruto drupeolas verdes"),
    (73, "Fruto en crecimiento", "Frutos verdes al ~30 % del tamaño final.",
     "fruto verde crecimiento 30"),
    (75, "Fruto a mitad de tamaño", "Frutos verdes al ~50 % del tamaño final.",
     "fruto verde 50 tamano desarrollo"),
    (77, "Fruto verde desarrollado", "Frutos verdes al ~70 % del tamaño final.",
     "fruto verde 70"),
    (79, "Fruto a tamaño final", "Frutos con tamaño final, aún verdes/blanquecinos.",
     "fruto tamano final verde blanquecino"),
    # 8: Maduración
    (81, "Inicio de maduración (pinta)", "Primeros frutos virando a rosado.",
     "pinta maduracion inicio rosado envero"),
    (85, "Maduración avanzada", "Frutos rojos; primeras cosechas.",
     "maduracion avanzada rojo primera cosecha"),
    (87, "Madurez de cosecha", "Mayoría de frutos con color de cosecha; cosecha principal.",
     "madurez cosecha principal rojo"),
    (89, "Fruto sobremaduro / fin de cosecha", "Frutos listos o sobremaduros; fin de cosecha.",
     "sobremaduro fin cosecha oscuro"),
    # 9: Senescencia
    (91, "Fin de crecimiento", "Crecimiento terminado; follaje aún verde.",
     "fin crecimiento follaje verde postcosecha"),
    (93, "Inicio caída de hojas", "Hojas comienzan a decolorarse y caer.",
     "decoloracion hojas caida inicio"),
    (95, "50 % hojas caídas", "Mitad del follaje caído.",
     "caida hojas 50"),
    (97, "Fin caída de hojas", "Todas las hojas caídas; cañas en reposo.",
     "fin caida hojas reposo"),
]

MACRO_STAGES: dict[int, str] = {
    0: "Desarrollo de yemas",
    1: "Desarrollo de hojas",
    3: "Crecimiento de brotes",
    5: "Aparición floral",
    6: "Floración",
    7: "Desarrollo del fruto",
    8: "Maduración",
    9: "Senescencia",
}

# Colores por estadio principal (usados en reportes / chips de UI).
MACRO_COLORS: dict[int, str] = {
    0: "#8B7355",   # pardo yema
    1: "#9BAF6A",   # verde claro hoja
    3: "#6E8B3D",   # verde oliva brote
    5: "#C9B458",   # botón floral
    6: "#E8E4D8",   # flor blanca (hueso)
    7: "#7FA06B",   # fruto verde
    8: "#B03A55",   # frambuesa
    9: "#A07D4F",   # senescencia
}


def macro_of(code: int | None) -> int | None:
    if code is None:
        return None
    return int(code) // 10


def bbch_label(code: int | None, catalog: dict[int, str] | None = None) -> str:
    """'BBCH 65: Plena floración'."""
    if code is None:
        return ""
    names = catalog or {c: n for c, n, _d, _k in BBCH_RUBUS}
    name = names.get(int(code))
    if name is None:
        # Código no catalogado: devolver el estadio principal.
        name = MACRO_STAGES.get(macro_of(code), "Estadio no catalogado")
    return f"BBCH {int(code):02d}: {name}"


def parse_bbch_code(text: str | None) -> int | None:
    """Extrae el código numérico de textos como 'BBCH 65: Plena floración' o '65'."""
    if not text:
        return None
    import re
    m = re.search(r"(?:BBCH\s*)?(\d{1,2})\b", str(text), flags=re.I)
    if not m:
        return None
    v = int(m.group(1))
    return v if 0 <= v <= 99 else None


# ---------------------------------------------------------------------------
# Semanas de muestreo
# ---------------------------------------------------------------------------
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def season_of(date: _dt.date) -> int:
    """Temporada agrícola (hemisferio sur): jul-jun. 2026-09 -> 2026; 2027-02 -> 2026."""
    return date.year if date.month >= 7 else date.year - 1


def default_season_start(season: int) -> _dt.date:
    """Semana 1 por defecto: semana del 7 de septiembre de la temporada."""
    return _dt.date(season, 9, 7)


def week_label(start: _dt.date) -> str:
    return f"Semana del {start.day} de {MESES[start.month - 1]}"


def format_date_es(d: _dt.date, with_year: bool = True) -> str:
    s = f"{d.day} de {MESES[d.month - 1]}"
    return f"{s} de {d.year}" if with_year else s


@dataclass(frozen=True)
class SamplingWeek:
    number: int
    start: _dt.date

    @property
    def end(self) -> _dt.date:
        return self.start + _dt.timedelta(days=6)

    @property
    def label(self) -> str:
        return week_label(self.start)

    @property
    def long_label(self) -> str:
        return f"Semana {self.number} · {self.label}"


def week_start(season_start: _dt.date, number: int) -> _dt.date:
    return season_start + _dt.timedelta(days=7 * (number - 1))


def week_number_for(date: _dt.date, season_start: _dt.date) -> int:
    """Semana de muestreo que contiene `date` (1-based; <1 si es anterior a la semana 1)."""
    delta = (date - season_start).days
    return delta // 7 + 1


def generate_weeks(season_start: _dt.date, count: int) -> list[SamplingWeek]:
    return [SamplingWeek(n, week_start(season_start, n)) for n in range(1, count + 1)]


# ---------------------------------------------------------------------------
# Calendario fenológico esperado (prior temporal)
# ---------------------------------------------------------------------------
# Frambueso floricane, zona centro-sur de Chile, Semana 1 = 7 de septiembre.
# (semana, BBCH esperado). Se interpola linealmente entre puntos.
# Es solo un *prior* débil: la app lo combina con la visión y la historia.
EXPECTED_CALENDAR: list[tuple[int, int]] = [
    (1, 7), (2, 10), (3, 15), (5, 31), (7, 51), (8, 57),
    (9, 61), (10, 65), (11, 69), (12, 71), (14, 75), (16, 81),
    (18, 87), (21, 89), (24, 91), (32, 95), (36, 97),
]


def expected_bbch_for_week(week_number: int) -> float:
    pts = EXPECTED_CALENDAR
    if week_number <= pts[0][0]:
        return float(pts[0][1])
    if week_number >= pts[-1][0]:
        return float(pts[-1][1])
    for (w0, b0), (w1, b1) in zip(pts, pts[1:]):
        if w0 <= week_number <= w1:
            t = (week_number - w0) / (w1 - w0)
            return b0 + t * (b1 - b0)
    return float(pts[-1][1])
