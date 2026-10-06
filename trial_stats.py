"""
trial_stats.py
==============
Estadística de los ensayos de tratamientos × repeticiones, sin librerías externas.

* `anova(valores)`: ANOVA de bloques completos al azar (las repeticiones son los
  bloques) cuando todos los tratamientos tienen las mismas repeticiones; si faltan
  datos, ANOVA de un factor. Devuelve F, p y, si p < 0,05, las letras de la prueba
  LSD de Fisher (protegida): tratamientos con la misma letra no difieren.
* `mean_se(valores)`: promedio y error estándar.

El valor p sale de la distribución F mediante la función beta incompleta regularizada
(fracción continua de Lentz), con precisión de sobra para un informe de campo.
"""
from __future__ import annotations

import math

ALPHA = 0.05


# --------------------------------------------------------------- distribuciones
def _betacf(a: float, b: float, x: float) -> float:
    tiny, eps = 1e-300, 3e-14
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Beta incompleta regularizada I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    ln = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    front = math.exp(ln)
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1 - x) / b


def f_pvalue(f: float, d1: int, d2: int) -> float:
    """P(F > f) con d1 y d2 grados de libertad."""
    if f is None or math.isnan(f):
        return float("nan")
    if math.isinf(f):
        return 0.0
    if f <= 0:
        return 1.0
    return max(0.0, min(1.0, betainc(d2 / 2, d1 / 2, d2 / (d2 + d1 * f))))


def t_two_sided_p(t: float, df: int) -> float:
    return betainc(df / 2, 0.5, df / (df + t * t))


def t_crit(df: int, alpha: float = ALPHA) -> float:
    """Valor t bilateral (por bisección)."""
    lo, hi = 0.0, 1000.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_two_sided_p(mid, df) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ------------------------------------------------------------------- resumen
def mean_se(values) -> tuple[float | None, float | None]:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None, None
    m = sum(vals) / len(vals)
    if len(vals) < 2:
        return m, None
    var = sum((v - m) ** 2 for v in vals) / (len(vals) - 1)
    return m, math.sqrt(var / len(vals))


def stars(p: float | None) -> str:
    if p is None or math.isnan(p):
        return ""
    return "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"


def letters(means: dict, lsd: float) -> dict:
    """Letras de significancia: la «a» al promedio más alto; misma letra = no difieren."""
    order = sorted(means, key=lambda k: -means[k])
    groups, last_end = [], -1
    for i, k in enumerate(order):
        j = i
        while j + 1 < len(order) and means[k] - means[order[j + 1]] <= lsd + 1e-12:
            j += 1
        if j > last_end:
            groups.append((i, j))
            last_end = j
    out = {k: "" for k in order}
    for g, (i, j) in enumerate(groups):
        letter = chr(ord("a") + g) if g < 26 else "?"
        for k in order[i:j + 1]:
            out[k] += letter
    return out


def anova(values: dict) -> dict | None:
    """values: {(tratamiento, repetición): valor}. None si no hay datos suficientes."""
    data = {k: float(v) for k, v in values.items() if v is not None}
    trts = sorted({t for t, _r in data})
    if len(trts) < 2:
        return None
    by_t = {t: [v for (tt, _r), v in data.items() if tt == t] for t in trts}
    n = len(data)
    grand = sum(data.values()) / n
    means = {t: sum(v) / len(v) for t, v in by_t.items()}
    reps = sorted({r for _t, r in data})
    complete = len(reps) >= 2 and all((t, r) in data for t in trts for r in reps)
    ss_tot = sum((v - grand) ** 2 for v in data.values())
    ss_t = sum(len(by_t[t]) * (means[t] - grand) ** 2 for t in trts)
    if complete:
        design = "bloques completos al azar"
        rmeans = {r: sum(data[(t, r)] for t in trts) / len(trts) for r in reps}
        ss_b = len(trts) * sum((m - grand) ** 2 for m in rmeans.values())
        df_t, df_e = len(trts) - 1, (len(trts) - 1) * (len(reps) - 1)
        ss_e = max(0.0, ss_tot - ss_t - ss_b)
    else:
        design = "un factor"
        df_t, df_e = len(trts) - 1, n - len(trts)
        ss_e = max(0.0, ss_tot - ss_t)
    if df_e <= 0:
        return None
    ms_t, ms_e = ss_t / df_t, ss_e / df_e
    if ms_e <= 1e-12:
        f = 0.0 if ms_t <= 1e-12 else math.inf
    else:
        f = ms_t / ms_e
    p = f_pvalue(f, df_t, df_e)
    out = {"design": design, "f": f, "p": p, "df": (df_t, df_e), "means": means,
           "mse": ms_e, "stars": stars(p), "letters": {}, "lsd": None,
           "cv": (math.sqrt(ms_e) / grand * 100) if grand else None}
    if p < ALPHA:
        n_h = len(trts) / sum(1 / len(by_t[t]) for t in trts)     # n armónico (si faltan datos)
        lsd = t_crit(df_e) * math.sqrt(2 * ms_e / n_h)
        out["lsd"] = lsd
        out["letters"] = letters(means, lsd)
    return out
