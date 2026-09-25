"""
Estética de cuaderno de campo: verde oliva, pizarra y blanco hueso.

KivyMD 1.2 solo acepta paletas con nombre; se re-definen las rampas
«Green» (-> oliva) y «BlueGray» (-> pizarra) y las superficies claras
antes de construir la app.
"""
from kivy.utils import get_color_from_hex

OLIVE = "#5B6B3A"
OLIVE_DARK = "#44512A"
OLIVE_SOFT = "#E4E6D3"
SLATE = "#3E4A56"
SLATE_SOFT = "#DDE1E4"
BONE = "#F4F1E8"
PAPER = "#FFFDF7"
INK = "#22261C"
INK_2 = "#4F5546"
MUTED = "#7D8272"
RULE = "#DDD6C4"
BERRY = "#9C3350"
WARN = "#B7791F"

OLIVE_RAMP = {
    "50": "F3F4EC", "100": "E4E6D3", "200": "CBD0AE", "300": "AEB785", "400": "8E9A61",
    "500": "5B6B3A", "600": "53623A", "700": "485530", "800": "3C4727", "900": "2B3419",
    "A100": "D9E3B0", "A200": "BCCB7E", "A400": "93A650", "A700": "6F8236",
}
SLATE_RAMP = {
    "50": "EEF0F2", "100": "DDE1E4", "200": "BCC4CB", "300": "96A2AD", "400": "6E7C89",
    "500": "3E4A56", "600": "37424D", "700": "2F3942", "800": "272F37", "900": "1B2127",
    "A100": "C9D6E2", "A200": "9FB4C7", "A400": "6F8CA8", "A700": "4E6D8C",
}


def c(hex_color: str, alpha: float = 1.0):
    rgba = get_color_from_hex(hex_color)
    rgba[3] = alpha
    return rgba


def install_palette() -> None:
    from kivymd.color_definitions import colors
    colors["Green"].update(OLIVE_RAMP)
    colors["BlueGray"].update(SLATE_RAMP)
    colors["Light"].update({"StatusBar": "44512A", "AppBar": "5B6B3A",
                            "Background": BONE[1:], "CardsDialogs": PAPER[1:]})


def apply(theme_cls) -> None:
    theme_cls.theme_style = "Light"
    theme_cls.primary_palette = "Green"
    theme_cls.primary_hue = "500"
    theme_cls.accent_palette = "BlueGray"
    theme_cls.material_style = "M2"
