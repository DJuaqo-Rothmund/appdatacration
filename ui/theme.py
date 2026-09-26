"""
Estética «frambuesa»: verdes de hoja + rojos de fruto sobre un fondo claro
difuminado, con tarjetas translúcidas tipo vidrio (glassmorphism).

KivyMD 1.2 solo acepta paletas con nombre; se re-definen las rampas «Green»
(-> verde hoja) y «Pink» (-> frambuesa) antes de construir la app.
"""
from kivy.utils import get_color_from_hex

from platform_utils import resource_path

# --- Verdes (hoja) --------------------------------------------------------
LEAF_DARK = "#1E4A3A"     # tinta principal / trazos
LEAF = "#2F6B4F"
LEAF_LIGHT = "#6FA86A"
LEAF_SOFT = "#DDEFD8"
# --- Rojos (fruto) --------------------------------------------------------
BERRY = "#A8184A"         # acción principal / estado activo
BERRY_DARK = "#7E1238"
BERRY_LIGHT = "#E0718F"
BERRY_SOFT = "#F8DCE4"
# --- Neutros y vidrio -----------------------------------------------------
INK = "#1B2A23"
INK_2 = "#41534A"
MUTED = "#6E7E75"
WARN = "#B7791F"
GLASS = "#FFFFFFA8"       # tarjetas translúcidas (~66 %)
GLASS_STRONG = "#FFFFFFD9"
GLASS_EDGE = "#FFFFFFE6"  # borde luminoso del vidrio
LINE = "#1E4A3A2E"        # líneas finas verdes translúcidas
CLEAR = "#00000000"

BACKGROUND = resource_path("assets", "ui", "background.jpg")


def nav_icon(name: str, active: bool) -> str:
    return resource_path("assets", "ui", f"nav_{name}_{'active' if active else 'idle'}.png")


def c(hex_color: str, alpha: float | None = None):
    """Color Kivy desde hex (#RRGGBB o #RRGGBBAA); `alpha` lo sobrescribe."""
    rgba = get_color_from_hex(hex_color)
    if len(rgba) == 3:
        rgba.append(1.0)
    if alpha is not None:
        rgba[3] = alpha
    return rgba


# Colores de etiqueta por estadio principal BBCH: del verde vegetativo al rojo
# de maduración (fondo, texto).
STAGE_COLORS = {
    0: ("#EDE3D6", "#6B4A2E"),   # yemas
    1: ("#DDEFD8", "#1E4A3A"),   # hojas
    3: ("#C8E4C0", "#1E4A3A"),   # brotes
    5: ("#EAF2D4", "#4D6B1F"),   # botón floral
    6: ("#FFFFFF", "#2F6B4F"),   # floración (pétalo blanco)
    7: ("#B9DDB0", "#1E4A3A"),   # fruto verde
    8: ("#F8D3DD", "#A8184A"),   # maduración
    9: ("#F3E7CC", "#7A5A1E"),   # senescencia
}


def stage_colors(code):
    if code is None:
        return c("#FFFFFF", .55), c(MUTED)
    bg, fg = STAGE_COLORS.get(int(code) // 10, ("#FFFFFF", INK))
    return c(bg, .92), c(fg)


LEAF_RAMP = {
    "50": "EEF7EC", "100": "DDEFD8", "200": "BCDDB4", "300": "96C78C", "400": "6FA86A",
    "500": "2F6B4F", "600": "295E46", "700": "22513C", "800": "1E4A3A", "900": "143326",
    "A100": "C9EBC0", "A200": "9CD68E", "A400": "6FB85F", "A700": "4F9A45",
}
BERRY_RAMP = {
    "50": "FCEEF2", "100": "F8DCE4", "200": "F0B3C4", "300": "E78AA4", "400": "E0718F",
    "500": "A8184A", "600": "961541", "700": "7E1238", "800": "690F2F", "900": "4D0A22",
    "A100": "FF9FBA", "A200": "FF6E97", "A400": "F23D72", "A700": "D81B5A",
}


def install_palette() -> None:
    from kivymd.color_definitions import colors
    colors["Green"].update(LEAF_RAMP)
    colors["Pink"].update(BERRY_RAMP)
    colors["Light"].update({"StatusBar": "1E4A3A", "AppBar": "2F6B4F",
                            "Background": "F3F7F2", "CardsDialogs": "FBFDFB"})


def apply(theme_cls) -> None:
    theme_cls.theme_style = "Light"
    theme_cls.primary_palette = "Green"
    theme_cls.primary_hue = "500"
    theme_cls.accent_palette = "Pink"
    theme_cls.material_style = "M2"
