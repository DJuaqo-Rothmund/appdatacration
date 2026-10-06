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
# Escala BBCH del frambueso (Rubus idaeus) para cañas anuales y brotes laterales de las
# cañas de soporte (tabla de referencia del usuario, oct. 2026): (código, cañas anuales,
# brotes laterales); None = no aplica. Los códigos de 3 cifras son subestadios y se
# muestran «89-1» (891 = subestadio 1 del 89). Más abajo se completan con los estados
# que la app ya usaba y que no están en la tabla (registros anteriores).
BBCH_TABLE: list[tuple[int, str | None, str | None]] = [
    (0, None, "Dormancia"),
    (7, None, "Inicio de la brotación"),
    (9, "Los brotes rompen la superficie del suelo", "Las yemas muestran puntas verdes"),
    (10, "Primeras hojas extendidas", "Primeras hojas extendidas"),
    (11, "Primera hoja desplegada", "Primera hoja desplegada"),
    (12, "Segunda hoja desplegada", "Segunda hoja desplegada"),
    (13, "Tercera hoja desplegada; etc.", "Tercera hoja desplegada; etc."),
    (16, "Seis o más hojas desplegadas", "Seis o más hojas desplegadas"),
    (31, "10 % del crecimiento máximo alcanzado (25 cm)", None),
    (33, "30 % del crecimiento máximo alcanzado (75 cm)", None),
    (36, "60 % del crecimiento máximo alcanzado (150 cm)", None),
    (39, "Longitud máxima alcanzada", None),
    (51, "Se hacen visibles los primeros capullos", "Se hacen visibles los primeros capullos"),
    (553, "Los tallos florales se estiran (capullos juntos)",
     "Los tallos florales se estiran (capullos juntos)"),
    (55, "Los tallos florales se estiran (capullos separados)",
     "Los tallos florales se estiran (capullos separados)"),
    (57, "Los capullos se inclinan, algunos de color rojizo",
     "Los capullos se inclinan, algunos de color rojizo"),
    (59, "Los pétalos (blancos) son visibles, la flor aún está cerrada (etapa muy corta)",
     "Los pétalos (blancos) son visibles, la flor aún está cerrada (etapa muy corta)"),
    (60, "Primeras flores abiertas en casos aislados", "Primeras flores abiertas en casos aislados"),
    (61, "Inicio de la floración: 10 % de las flores abiertas o marchitas",
     "Inicio de la floración: 10 % de las flores abiertas o marchitas"),
    (63, "30 % de las flores abiertas o marchitas", "30 % de las flores abiertas o marchitas"),
    (65, "Final de la floración: 50 % de las flores abiertas o marchitas",
     "Final de la floración: 50 % de las flores abiertas o marchitas"),
    (69, "Fin de la floración: la mayoría de las flores marchitas y fructifican",
     "Fin de la floración: la mayoría de las flores marchitas y fructifican"),
    (71, "10 % de frutos jóvenes visibles", "10 % de frutos jóvenes visibles"),
    (73, "30 % de frutos jóvenes visibles", "30 % de frutos jóvenes visibles"),
    (75, "50 % de frutos jóvenes visibles", "50 % de frutos jóvenes visibles"),
    (77, "70 % de frutos jóvenes visibles", "70 % de frutos jóvenes visibles"),
    (79, "Casi todos los frutos jóvenes visibles", "Casi todos los frutos jóvenes visibles"),
    (81, "Inicio de la coloración de los primeros frutos",
     "Inicio de la coloración de los primeros frutos"),
    (85, "Progresión de la coloración de los primeros frutos",
     "Progresión de la coloración de los primeros frutos"),
    (89, "Maduración completa: coloración típica de los primeros frutos alcanzada",
     "Maduración completa: coloración típica de los primeros frutos alcanzada"),
    (891, "10 % de los frutos cosechados", "10 % de los frutos cosechados"),
    (893, "30 % de los frutos cosechados", "30 % de los frutos cosechados"),
    (895, "50 % de los frutos cosechados", "50 % de los frutos cosechados"),
    (897, "70 % de los frutos cosechados", "70 % de los frutos cosechados"),
    (899, "Casi todos los frutos cosechados", "Casi todos los frutos cosechados"),
    (91, "Crecimiento completo del brote, follaje aún verde", None),
    (93, "Coloración de las hojas", None),
    (95, "Caída de hojas", None),
    (97, "Latencia de la vegetación", "Muere el brote de dos años que sostenía las cañas"),
    (99, "Cosecha", "Cosecha"),
]


def _lower1(t: str) -> str:
    return t[0].lower() + t[1:] if t else t


def _table_entry(code: int, canes: str | None, laterals: str | None):
    """(código, nombre, descripción, palabras clave) a partir de las dos columnas."""
    import re
    import unicodedata
    if canes and laterals and canes != laterals:
        label = f"Cañas: {_lower1(canes)} · Laterales: {_lower1(laterals)}"
    elif canes and not laterals:
        label = f"Cañas anuales: {_lower1(canes)}"
    elif laterals and not canes:
        label = f"Brotes laterales: {_lower1(laterals)}"
    else:
        label = canes or laterals or ""
    desc = (f"Cañas anuales (cañas jóvenes): {canes or '—'}\n"
            f"Brotes laterales de las cañas de soporte: {laterals or '—'}")
    words = unicodedata.normalize("NFKD", f"{canes or ''} {laterals or ''}".lower())
    words = "".join(ch for ch in words if not unicodedata.combining(ch))
    kw = " ".join(dict.fromkeys(w for w in re.findall(r"[a-z0-9]+", words) if len(w) > 2))
    return code, label, desc, kw


# Estados que la app usaba antes y que no están en la tabla (se conservan para los
# registros ya hechos y como estados intermedios).
_PREVIOUS: list[tuple[int, str, str, str]] = [
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

BBCH_SCALE_VERSION = 2   # sube cuando cambia la escala base (se actualiza en las bases)


def _scale_value(code: int) -> float:
    return float(code) if code < 100 else code // 10 + (code % 10) / 10


BBCH_RUBUS: list[tuple[int, str, str, str]] = sorted(
    [_table_entry(*row) for row in BBCH_TABLE]
    + [e for e in _PREVIOUS if e[0] not in {row[0] for row in BBCH_TABLE}],
    key=lambda e: _scale_value(e[0]))


MACRO_STAGES: dict[int, str] = {
    0: "Brotación",
    1: "Desarrollo de las hojas",
    3: "Desarrollo del brote",
    5: "Desarrollo del botón floral",
    6: "Floración",
    7: "Desarrollo del fruto",
    8: "Maduración del fruto",
    9: "Inicio de la latencia",
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
    """Estadio principal: 65 -> 6; 891 (= 89-1) -> 8."""
    if code is None:
        return None
    code = int(code)
    return code // 100 if code >= 100 else code // 10


def bbch_value(code: int | None) -> float | None:
    """Posición en la escala para ordenar, graficar y promediar: 891 (89-1) -> 89,1."""
    if code is None:
        return None
    return _scale_value(int(code))


def code_str(code: int | None) -> str:
    """Código para mostrar: 7 -> «07», 65 -> «65», 891 -> «89-1»."""
    if code is None:
        return "—"
    code = int(code)
    return f"{code // 10}-{code % 10}" if code >= 100 else f"{code:02d}"


def bbch_sorted(rows, key=lambda r: r["code"]):
    """Ordena estados por su posición en la escala (89-1 va entre 89 y 91)."""
    return sorted(rows, key=lambda r: _scale_value(int(key(r))))


def bbch_label(code: int | None, catalog: dict[int, str] | None = None) -> str:
    """'BBCH 65: Plena floración'."""
    if code is None:
        return ""
    names = catalog or {c: n for c, n, _d, _k in BBCH_RUBUS}
    name = names.get(int(code))
    if name is None:
        # Código no catalogado: devolver el estadio principal.
        name = MACRO_STAGES.get(macro_of(code), "Estadio no catalogado")
    return f"BBCH {code_str(code)}: {name}"


def parse_bbch_code(text: str | None) -> int | None:
    """Extrae el código numérico de textos como 'BBCH 65: Plena floración' o '65'."""
    if not text:
        return None
    import re
    # «BBCH 65», «65», «BBCH 89-1» (subestadio) o «891».
    m = re.search(r"(?:BBCH\s*)?\b(\d{1,3})(?:-(\d))?\b", str(text), flags=re.I)
    if not m:
        return None
    if m.group(2) is not None and len(m.group(1)) <= 2:
        return int(m.group(1)) * 10 + int(m.group(2))
    v = int(m.group(1))
    return v if 0 <= v <= 999 else None


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
        return f"{week_title(self.start)} · {self.label}"


def iso_week(start) -> tuple[int, int]:
    """(semana del año ISO-8601, año) de una fecha o «aaaa-mm-dd».
    10-08-2026 -> (33, 2026); 28-12-2026 -> (53, 2026); 04-01-2027 -> (1, 2027)."""
    d = start if isinstance(start, _dt.date) else _dt.date.fromisoformat(str(start)[:10])
    year, week, _ = d.isocalendar()
    return week, year


def _week_date(week):
    return week["start_date"] if isinstance(week, dict) else week


def week_title(week) -> str:
    """«Semana 33, año 2026» a partir de una semana de muestreo (o su fecha de inicio)."""
    w, y = iso_week(_week_date(week))
    return f"Semana {w}, año {y}"


def week_short(week) -> str:
    """«S33»: rótulo corto (ejes de gráficos, rangos)."""
    return f"S{iso_week(_week_date(week))[0]}"


def week_of_year(week) -> int:
    return iso_week(_week_date(week))[0]


def season_title(season: int) -> str:
    """Rótulo de una temporada completa: «Año 2026»."""
    return f"Año {season}"


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


# ---------------------------------------------------------------------------
# Nombre de archivo de las fotos: «aaaammdd-<Ensayo>-<Variedad>[S<sector>][ER<riego>]<G|D>»,
# p. ej. 20260928-NV-C11G o 20260928-AM-C11S1ER2G — año-mes-día: se ordenan por fecha.
# (fecha = inicio de la semana de muestreo; G = general/canopia, D = detalle).
# Hasta la 1.1.30 la fecha iba al revés (ddmmaaaa: 28092026-C11G): legacy=True.
# ---------------------------------------------------------------------------
PHOTO_KIND_LETTER = {"canopy": "G", "detail": "D", "attachment": "A"}   # A = adjunta a la ficha


def variety_tag(name: str, code: str | None = None) -> str:
    """Abreviatura de la variedad: su código («C11», «MEE» → «Mee») o derivada del nombre."""
    import re
    import unicodedata

    def clean(t):
        t = unicodedata.normalize("NFKD", t or "")
        return re.sub(r"[^A-Za-z0-9]", "", "".join(ch for ch in t if not unicodedata.combining(ch)))

    tag = clean(code)
    if not tag:
        digits = re.findall(r"\d+", name or "")
        letters = clean(name)
        tag = (letters[:1] + digits[0]) if digits and letters else letters[:3]
    if tag.isalpha() and len(tag) >= 3:
        tag = tag.capitalize()
    return tag or "Var"


SECTORS = range(1, 11)          # sector del ensayo (opcional)
IRRIGATION_UNITS = range(1, 5)  # equipo de riego (opcional)


def location_tag(variety: dict) -> str:
    """«S1ER2» (sector 1, equipo de riego 2); se omite lo que no esté definido."""
    out = ""
    if variety.get("sector"):
        out += f"S{int(variety['sector'])}"
    if variety.get("irrigation"):
        out += f"ER{int(variety['irrigation'])}"
    return out


PHOTO_DATE_FMT, LEGACY_PHOTO_DATE_FMT = "%Y%m%d", "%d%m%Y"


def photo_date(day) -> str:
    """«20260928»: fecha para nombres de archivo (año, mes, día)."""
    import datetime as _d
    if isinstance(day, str):
        day = _d.date.fromisoformat(day[:10])
    return day.strftime(PHOTO_DATE_FMT)


def photo_basename(variety: dict, week_start, kind: str, seq: int = 1, legacy: bool = False,
                   trial: str = "") -> str:
    """trial: código del ensayo o predio («NV», «AM»…), va después de la fecha."""
    import datetime as _d
    if isinstance(week_start, str):
        week_start = _d.date.fromisoformat(week_start[:10])
    date = week_start.strftime(LEGACY_PHOTO_DATE_FMT if legacy else PHOTO_DATE_FMT)
    if trial:
        date = f"{date}-{trial}"
    base = f"{date}-{variety_tag(variety.get('name', ''), variety.get('code'))}" \
           f"{location_tag(variety)}{PHOTO_KIND_LETTER.get(kind, 'X')}"
    return base if seq <= 1 else f"{base}-{seq}"
