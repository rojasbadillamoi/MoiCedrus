"""
modulo_codatacion.py — Panel de Co-datación Visual

Permite comparar series dendrocronológicas, detectar anillos faltantes o
sobrantes mediante búsqueda exhaustiva, y aplicar correcciones en memoria.
"""

import os
import csv
import json
import logging
import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem,
    QSplitter, QFileDialog, QMessageBox, QInputDialog,
    QSpinBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QDialog, QFormLayout,
    QComboBox, QCheckBox, QLineEdit, QTextEdit, QGroupBox,
    QDialogButtonBox, QApplication, QProgressDialog,
    QScrollArea, QRadioButton, QButtonGroup, QFrame,
)
from PyQt6.QtGui import (
    QColor, QFont, QKeySequence, QShortcut, QPainter,
)
from PyQt6.QtCore import Qt, QSettings, QEvent

from constantes import (
    TUCSON_VALOR_FIN, TUCSON_VALOR_FALTANTE, TUCSON_VALOR_999,
    TUCSON_MAX_CHARS_ID, TUCSON_DIVISOR,
    OVERLAP_MINIMO_ANALISIS, OVERLAP_MINIMO_GRAFICO,
    UMBRAL_ANCLA_DEFECTO,
    VENTANA_COFECHADO_DEFECTO, COLORES_SERIES,
)

logger = logging.getLogger(__name__)

# Correcciones máximas a explorar por año (±N anillos)
MAX_CORRECCION = 3
MIN_OVERLAP_DEFAULT = 10


def _ultima_carpeta(nueva_ruta: str | None = None) -> str:
    """
    Lee o guarda la última carpeta usada en el módulo de codatación.

    Usa una clave QSettings INDEPENDIENTE de modulo_medicion.py porque
    los usuarios típicamente organizan imágenes y mediciones en carpetas
    distintas.
    """
    cfg = QSettings("MoiCedrus", "DPI")
    if nueva_ruta is not None:
        cfg.setValue("ultima_carpeta_codatacion", os.path.dirname(nueva_ruta))
        return os.path.dirname(nueva_ruta)
    return cfg.value("ultima_carpeta_codatacion", "")


# =============================================================================
# ESTADÍSTICOS DE COFECHADO
# =============================================================================

def gleichlaufigkeit(serie_a, serie_b, min_overlap: int = 10):
    """GLK (Eckstein & Bauch 1969): proporción de años con cambio en la misma dirección."""
    a = pd.to_numeric(serie_a, errors="coerce").dropna()
    b = pd.to_numeric(serie_b, errors="coerce").dropna()
    overlap = a.index.intersection(b.index)
    if len(overlap) < min_overlap:
        return float("nan"), 0

    a = a.loc[overlap].sort_index().to_numpy(dtype=float)
    b = b.loc[overlap].sort_index().to_numpy(dtype=float)

    da = np.sign(np.diff(a))
    db = np.sign(np.diff(b))
    n = len(da)
    if n == 0:
        return float("nan"), len(overlap)

    coinciden = (da == db) & (da != 0)
    parallels = (da == 0) | (db == 0)
    glk = (np.sum(coinciden) + 0.5 * np.sum(parallels)) / n
    return float(glk), int(len(overlap))


def glk_significance(glk: float, n: int) -> str:
    """Significancia GLK (Jansma 1995): aproximación binomial."""
    if not np.isfinite(glk) or n < 30:
        return "ns"
    z = (glk - 0.5) * 2 * np.sqrt(n)
    if z >= 3.29: return "***"
    if z >= 2.58: return "**"
    if z >= 1.96: return "*"
    return "ns"


def _filtro_hollstein(serie):
    """Filtro de Hollstein: 100 × primera diferencia logarítmica."""
    s = pd.to_numeric(serie, errors="coerce").dropna()
    s = s[s > 0]
    if len(s) < 2:
        return pd.Series(dtype=float)
    return np.log(s).diff().dropna() * 100.0


def t_baillie_pilcher(serie_a, serie_b, min_overlap: int = 20):
    """t de Baillie-Pilcher: Hollstein → Pearson r → t-statistic."""
    a_filt = _filtro_hollstein(serie_a)
    b_filt = _filtro_hollstein(serie_b)
    overlap = a_filt.index.intersection(b_filt.index)
    n = len(overlap)
    if n < min_overlap:
        return float("nan"), float("nan"), n

    a = a_filt.loc[overlap].to_numpy(dtype=float)
    b = b_filt.loc[overlap].to_numpy(dtype=float)
    if np.std(a) == 0 or np.std(b) == 0:
        return float("nan"), float("nan"), n

    r = float(np.corrcoef(a, b)[0, 1])
    if np.isnan(r) or abs(r) >= 1.0:
        return float("nan"), r, n
    t_bp = r * np.sqrt(n - 2) / np.sqrt(1.0 - r * r)
    return float(t_bp), r, n


def estadisticos_cofechado(serie_a, serie_b, min_overlap: int = 20) -> dict:
    """r + GLK + t-BP simultáneos."""
    a = pd.to_numeric(serie_a, errors="coerce").dropna()
    b = pd.to_numeric(serie_b, errors="coerce").dropna()
    overlap = a.index.intersection(b.index)
    n = len(overlap)

    resultado = {
        "r": float("nan"), "n": n,
        "glk": float("nan"), "glk_sig": "ns",
        "t_bp": float("nan"), "r_filt": float("nan"),
    }

    if n < min_overlap:
        return resultado

    a_ov = a.loc[overlap].to_numpy(dtype=float)
    b_ov = b.loc[overlap].to_numpy(dtype=float)
    if np.std(a_ov) > 0 and np.std(b_ov) > 0:
        r = float(np.corrcoef(a_ov, b_ov)[0, 1])
        if np.isfinite(r):
            resultado["r"] = r

    glk, _ = gleichlaufigkeit(serie_a, serie_b, min_overlap)
    resultado["glk"] = glk
    resultado["glk_sig"] = glk_significance(glk, n)

    t_bp, r_filt, _ = t_baillie_pilcher(serie_a, serie_b, min_overlap)
    resultado["t_bp"] = t_bp
    resultado["r_filt"] = r_filt

    return resultado


# =============================================================================
# COLORES Y SCORE COMPUESTO
# =============================================================================

def _color_r(r: float) -> str:
    if not np.isfinite(r): return "#888"
    if r >= 0.50: return "#28a745"
    if r >= 0.35: return "#a3c44e"
    if r >= 0.20: return "#f0ad4e"
    return "#d9534f"


def _color_glk(g: float) -> str:
    if not np.isfinite(g): return "#888"
    if g >= 0.70: return "#28a745"
    if g >= 0.65: return "#a3c44e"
    if g >= 0.60: return "#f0ad4e"
    return "#d9534f"


def _color_tbp(t: float) -> str:
    if not np.isfinite(t): return "#888"
    if t >= 6.0: return "#28a745"
    if t >= 4.0: return "#a3c44e"
    if t >= 3.0: return "#f0ad4e"
    return "#d9534f"


def score_compuesto(r: float, glk: float, t_bp: float) -> float:
    """Media geométrica con signo de r, GLK y t-BP normalizados a [-1, 1].

    Resultado en [-1, +1]:
      • cerca de +1  →  las tres métricas indican correlación fuerte
      • cerca de  0  →  ambiguo / al menos una métrica plana
      • cerca de -1  →  las tres indican ANTI-correlación
                        (señal de que el cofechado está completamente desfasado:
                         el patrón coincide pero invertido)

    Normalización: r ∈ [-1,1] directo, GLK mapeado desde [0,1] a [-1,1] con
    pivote en 0.5, t-BP saturado en ±10. Se usa raíz cúbica (np.cbrt) que
    preserva signo, a diferencia de la potencia 1/3 que en Python devuelve
    complejos para negativos.
    """
    if not (np.isfinite(r) and np.isfinite(glk) and np.isfinite(t_bp)):
        return float("nan")
    r_norm = max(-1.0, min(1.0, r))
    glk_norm = max(-1.0, min(1.0, 2.0 * (glk - 0.5)))
    tbp_norm = max(-1.0, min(1.0, t_bp / 10.0))
    producto = r_norm * glk_norm * tbp_norm
    return float(np.cbrt(producto))


def _color_compuesto(s: float) -> str:
    if not np.isfinite(s): return "#666666"
    if s >= 0.60: return "#28a745"   # verde fuerte
    if s >= 0.40: return "#5cb85c"   # verde
    if s >= 0.20: return "#ffc107"   # amarillo
    if s >= -0.20: return "#fd7e14"  # naranja (zona ambigua cercana a 0)
    return "#dc3545"                  # rojo (anti-correlación)


# =============================================================================
# EPS / RBAR
# =============================================================================

def calcular_eps_rbar_movil(series_dict, nombres, window=15, paso=1, progreso=None):
    """EPS y Rbar móvil — versión numpy directa."""
    cols = []
    for n in nombres:
        if n in series_dict:
            s = series_dict[n]["Ancho_mm"].dropna()
            s.name = n
            cols.append(s)
    if len(cols) < 2:
        return pd.DataFrame(columns=["Rbar", "EPS"])

    df = pd.concat(cols, axis=1).sort_index()
    n_series = df.shape[1]
    years = df.index.to_numpy()
    half = window // 2
    data = df.to_numpy(dtype=float)
    n_years = len(data)

    if n_years < window:
        return pd.DataFrame(columns=["Rbar", "EPS"])

    indices = list(range(half, n_years - half, paso))
    total = len(indices)
    resultados = []
    min_per = 10

    for k, i in enumerate(indices):
        win = data[i - half: i + half + 1]
        rs = []
        for a in range(n_series):
            col_a = win[:, a]
            valid_a = ~np.isnan(col_a)
            if valid_a.sum() < min_per:
                continue
            for b in range(a + 1, n_series):
                col_b = win[:, b]
                common = valid_a & ~np.isnan(col_b)
                if common.sum() < min_per:
                    continue
                a_v = col_a[common]
                b_v = col_b[common]
                ma = a_v.mean()
                mb = b_v.mean()
                da = a_v - ma
                db = b_v - mb
                den = np.sqrt((da * da).sum() * (db * db).sum())
                if den == 0:
                    continue
                r = (da * db).sum() / den
                if np.isfinite(r):
                    rs.append(r)

        if rs:
            rbar = float(np.mean(rs))
            # Profundidad de muestreo PROMEDIO en la ventana: número medio
            # de series con dato válido por año. ARSTAN usa esto (su columna
            # "cores") en la fórmula de EPS, NO el total de series. Usar el
            # total sobreestimaría el EPS en ventanas donde no todas las
            # series están presentes. Esto es clave para que el EPS coincida
            # con ARSTAN.
            sample_depth = (~np.isnan(win)).sum(axis=1)
            n_eff = float(sample_depth[sample_depth > 0].mean()) \
                if (sample_depth > 0).any() else float(n_series)
            # Fórmula de Wigley et al. (1984): EPS = N·r̄ / (1 + (N−1)·r̄)
            eps = (n_eff * rbar) / (1 + (n_eff - 1) * rbar)
            resultados.append((years[i], rbar, float(eps)))

        if progreso is not None and total > 0 and (k % 10 == 0 or k == total - 1):
            progreso(int(100 * (k + 1) / total))

    if not resultados:
        return pd.DataFrame(columns=["Rbar", "EPS"])

    return pd.DataFrame(resultados, columns=["Anio", "Rbar", "EPS"]).set_index("Anio")


def calcular_rbar(series_dict, nombres):
    series = [series_dict[n]["Ancho_mm"].dropna() for n in nombres if n in series_dict]
    rs = []
    for i in range(len(series)):
        for j in range(i + 1, len(series)):
            common = series[i].index.intersection(series[j].index)
            if len(common) < 10:
                continue
            a = series[i].loc[common]
            b = series[j].loc[common]
            if np.std(a) == 0 or np.std(b) == 0:
                continue
            r = np.corrcoef(a, b)[0, 1]
            if np.isfinite(r):
                rs.append(r)
    return float(np.mean(rs)) if rs else np.nan


def calcular_eps(rbar, n):
    if n <= 1 or not np.isfinite(rbar):
        return np.nan
    return (n * rbar) / (1 + (n - 1) * rbar)


# =============================================================================
# FORMATEADORES HTML PARA STATS
# =============================================================================

def formato_stats_hover_html(year: int, mitad: int, r, glk, t_bp, n) -> str:
    def _f(v, dec=2):
        return "—" if not np.isfinite(v) else f"{v:.{dec}f}"

    if np.isfinite(glk) and n >= 30:
        z = (glk - 0.5) * 2 * np.sqrt(n)
        if z >= 3.29: sig = "***"
        elif z >= 2.58: sig = "**"
        elif z >= 1.96: sig = "*"
        else: sig = ""
    else:
        sig = ""

    return (
        f"<span style='font-size:13px;'>"
        f"<span style='color:#888;'>Año <b>{int(year)}</b> (±{mitad} años) ·</span>&nbsp;&nbsp;"
        f"<b>r</b>=<span style='color:{_color_r(r)}; font-weight:bold;'>{_f(r, 2)}</span>&nbsp;&nbsp;"
        f"<b>GLK</b>=<span style='color:{_color_glk(glk)}; font-weight:bold;'>{_f(glk, 2)}</span>"
        f"<span style='color:#bbb;'>{sig}</span>&nbsp;&nbsp;"
        f"<b>t-BP</b>=<span style='color:{_color_tbp(t_bp)}; font-weight:bold;'>{_f(t_bp, 1)}</span>"
        f"&nbsp;&nbsp;<span style='color:#888;'>n={int(n)}</span>"
        f"</span>"
    )


def formato_stats_global_html(stats: dict) -> str:
    r = stats.get("r", float("nan"))
    glk = stats.get("glk", float("nan"))
    glk_sig = stats.get("glk_sig", "")
    t_bp = stats.get("t_bp", float("nan"))
    n = stats.get("n", 0)

    def _f(v, dec=2):
        return "—" if not np.isfinite(v) else f"{v:.{dec}f}"

    return (
        f"<span style='font-size:13px;'>"
        f"<span style='color:#888;'>Global (n={n}) ·</span>&nbsp;&nbsp;"
        f"<b>r</b>=<span style='color:{_color_r(r)}; font-weight:bold;'>{_f(r, 2)}</span>&nbsp;&nbsp;"
        f"<b>GLK</b>=<span style='color:{_color_glk(glk)}; font-weight:bold;'>{_f(glk, 2)}</span>"
        f"<span style='color:#bbb;'>{glk_sig}</span>&nbsp;&nbsp;"
        f"<b>t-BP</b>=<span style='color:{_color_tbp(t_bp)}; font-weight:bold;'>{_f(t_bp, 1)}</span>"
        f"</span>"
    )


def formato_estadisticos(stats: dict) -> str:
    r = stats.get("r", float("nan"))
    glk = stats.get("glk", float("nan"))
    glk_sig = stats.get("glk_sig", "")
    t_bp = stats.get("t_bp", float("nan"))
    n = stats.get("n", 0)

    def _fmt(v, dec=2):
        return "—" if not np.isfinite(v) else f"{v:.{dec}f}"

    return (f"r={_fmt(r, 2)}  GLK={_fmt(glk, 2)}{glk_sig}  t-BP={_fmt(t_bp, 1)}  n={n}")


TOOLTIP_STATS = (
    "<b>Estadísticos de cofechado:</b><br><br>"
    "<b>r (Pearson):</b> correlación lineal.<br>"
    "&nbsp;&nbsp;• <span style='color:#28a745'>≥0.50</span>: excelente<br>"
    "&nbsp;&nbsp;• <span style='color:#a3c44e'>≥0.35</span>: bueno<br>"
    "&nbsp;&nbsp;• <span style='color:#f0ad4e'>≥0.20</span>: aceptable<br>"
    "&nbsp;&nbsp;• <span style='color:#d9534f'>&lt;0.20</span>: pobre<br><br>"
    "<b>GLK:</b> % años con misma dirección. Asteriscos = significancia.<br>"
    "&nbsp;&nbsp;• <span style='color:#28a745'>≥0.70</span>: muy bueno<br>"
    "&nbsp;&nbsp;• <span style='color:#a3c44e'>≥0.65</span>: bueno<br>"
    "&nbsp;&nbsp;• <span style='color:#f0ad4e'>≥0.60</span>: aceptable<br>"
    "&nbsp;&nbsp;• <span style='color:#d9534f'>&lt;0.60</span>: subóptimo<br><br>"
    "<b>t-BP (Baillie-Pilcher):</b> t-statistic sobre series filtradas.<br>"
    "&nbsp;&nbsp;• <span style='color:#28a745'>≥6.0</span>: excelente<br>"
    "&nbsp;&nbsp;• <span style='color:#a3c44e'>≥4.0</span>: bueno<br>"
    "&nbsp;&nbsp;• <span style='color:#f0ad4e'>≥3.0</span>: aceptable<br>"
    "&nbsp;&nbsp;• <span style='color:#d9534f'>&lt;3.0</span>: no significativo<br><br>"
    "<b>n:</b> años de overlap.<br><br>"
    "<i>Los tres altos = cofechado sólido.</i>"
)


ESTILO_BTN_COFECHA = (
    "QPushButton{background:transparent; padding:4px 10px; "
    "border:2px solid #888; border-radius:4px; font-weight:bold;}"
    "QPushButton:hover{border:2px solid #FF8C00;}"
    "QPushButton:checked{border:2px solid #FF8C00; background:#FF8C00; color:white;}"
)

ESTILO_CHK_COFECHA = (
    "QCheckBox::indicator{width:14px; height:14px; border:2px solid #888; "
    "border-radius:2px; background:transparent;}"
    "QCheckBox::indicator:hover{border:2px solid #FF8C00;}"
    "QCheckBox::indicator:checked{border:2px solid #FF8C00; background:#FF8C00;}"
)



# =============================================================================
# LECTORES DE FORMATO DE ARCHIVO
# =============================================================================

def leer_archivo_tucson(ruta: str) -> tuple[pd.DataFrame, str]:
    """Lee un .rwl Tucson UNA serie. Devuelve (DataFrame, id_serie)."""
    anios, mediciones, id_serie = [], [], "Desconocido"

    with open(ruta, "r", encoding="utf-8", errors="replace") as f:
        for linea in f:
            ln = linea.rstrip("\r\n")
            if not ln or ln.startswith("#"):
                continue
            if ln[:8].strip():
                id_serie = ln[:8].strip()
            try:
                anio_base = int(ln[8:12])
            except ValueError:
                continue
            for i in range(10):
                inicio = 12 + i * 6
                if inicio >= len(ln):
                    break
                token = ln[inicio: inicio + 6].strip()
                if not token:
                    continue
                try:
                    valor = int(token)
                except ValueError:
                    continue
                if valor in (TUCSON_VALOR_FIN, TUCSON_VALOR_FALTANTE):
                    break
                if valor == TUCSON_VALOR_999:
                    valor = 999
                anios.append(anio_base + i)
                mediciones.append(valor / TUCSON_DIVISOR)

    df = pd.DataFrame({"Anio": anios, "Ancho_mm": mediciones})
    if df.empty:
        raise ValueError(f"No se encontraron datos válidos en: {os.path.basename(ruta)}")
    df = df.groupby("Anio").mean()
    df.sort_index(inplace=True)
    return df, id_serie


def leer_tucson_multi(ruta: str) -> dict[str, pd.DataFrame]:
    """Lee un .rwl Tucson con UNA o VARIAS series. Devuelve dict {id: DataFrame}."""
    series_dict: dict[str, dict[int, float]] = {}

    with open(ruta, "r", encoding="utf-8", errors="replace") as f:
        for linea in f:
            ln = linea.rstrip("\r\n")
            if not ln or ln.startswith("#"):
                continue
            id_serie = ln[:8].strip()
            if not id_serie:
                continue

            try:
                anio_base = int(ln[8:12])
            except ValueError:
                continue

            if id_serie not in series_dict:
                series_dict[id_serie] = {}

            for i in range(10):
                inicio = 12 + i * 6
                if inicio >= len(ln):
                    break
                token = ln[inicio: inicio + 6].strip()
                if not token:
                    continue
                try:
                    valor = int(token)
                except ValueError:
                    continue
                if valor in (TUCSON_VALOR_FIN, TUCSON_VALOR_FALTANTE):
                    break
                if valor == TUCSON_VALOR_999:
                    valor = 999
                series_dict[id_serie][anio_base + i] = valor / TUCSON_DIVISOR

    resultado: dict[str, pd.DataFrame] = {}
    for sid, anios_vals in series_dict.items():
        if not anios_vals:
            continue
        df = pd.DataFrame({
            "Anio": list(anios_vals.keys()),
            "Ancho_mm": list(anios_vals.values()),
        })
        df = df.groupby("Anio").mean()
        df.sort_index(inplace=True)
        resultado[sid] = df

    if not resultado:
        raise ValueError(f"No se encontraron series válidas en: {os.path.basename(ruta)}")
    return resultado


def leer_formato_compacto(ruta: str, divisor: float = 100.0) -> dict:
    """Lee formato compacto FORTRAN (20F4.0) de CATRAS/Heidelberg."""
    import re
    series_dict = {}
    with open(ruta, 'r', encoding='utf-8', errors='replace') as f:
        lineas = f.readlines()

    i = 0
    while i < len(lineas):
        linea = lineas[i].rstrip('\n\r')
        if not linea:
            i += 1
            continue

        if "=N" in linea and "=I" in linea:
            match_i = re.search(r'(\d+)=I', linea)
            anio_inicio = int(match_i.group(1)) if match_i else 0

            partes = linea.split('=I')
            if len(partes) > 1:
                subpartes = partes[1].strip().split()
                serie_id = subpartes[0] if subpartes else f"Serie_{len(series_dict)+1}"
            else:
                serie_id = f"Serie_{len(series_dict)+1}"

            i += 1
            valores = []
            while i < len(lineas) and "=N" not in lineas[i] and "=I" not in lineas[i]:
                linea_datos = lineas[i].rstrip('\n\r')
                for j in range(0, len(linea_datos), 4):
                    chunk = linea_datos[j:j+4]
                    if chunk.strip():
                        try:
                            valores.append(float(chunk.strip()) / divisor)
                        except ValueError:
                            pass
                i += 1

            if valores:
                anios = list(range(anio_inicio, anio_inicio + len(valores)))
                df = pd.DataFrame({"Anio": anios, "Ancho_mm": valores})
                df.set_index("Anio", inplace=True)
                series_dict[serie_id] = df
        else:
            i += 1

    return series_dict


def leer_dendro_auto(ruta: str):
    """Detecta formato y deriva al parser correcto."""
    if ruta.lower().endswith('.wid'):
        return leer_wid(ruta)

    with open(ruta, 'r', encoding='utf-8', errors='replace') as f:
        primeras_lineas = f.read(500)

    if "=N" in primeras_lineas and "=I" in primeras_lineas:
        return leer_formato_compacto(ruta)

    return leer_archivo_tucson(ruta)


def leer_wid(ruta: str) -> tuple[pd.DataFrame, str]:
    """Lee CooRecorder .wid. Devuelve (DataFrame, nombre)."""
    anchos: list[float] = []
    anio_corteza: int | None = None
    nombre = os.path.splitext(os.path.basename(ruta))[0]

    with open(ruta, "r", encoding="utf-8", errors="replace") as f:
        for linea in f:
            ln = linea.strip()
            if not ln:
                continue
            if ln.startswith("#C DATED"):
                try:
                    anio_corteza = int(ln.split()[2])
                except (IndexError, ValueError):
                    logger.warning("leer_wid: no se pudo parsear #C DATED en '%s'", ruta)
                continue
            if ln.startswith("#"):
                continue
            token = ln.replace(",", ".")
            try:
                anchos.append(float(token))
            except ValueError:
                logger.debug("leer_wid: token ignorado '%s'", ln)

    if not anchos:
        raise ValueError(f"No se encontraron datos de ancho en: {os.path.basename(ruta)}")

    n = len(anchos)
    if anio_corteza is None:
        import datetime
        anio_corteza = datetime.date.today().year
        logger.warning("leer_wid: '%s' sin #C DATED, usando año actual.", ruta)

    anio_medula = anio_corteza - n + 1
    anios = list(range(anio_medula, anio_corteza + 1))

    df = pd.DataFrame({"Ancho_mm": anchos}, index=pd.Index(anios, name="Anio"))
    df.sort_index(inplace=True)
    return df, nombre


def leer_tabular(ruta: str) -> tuple[pd.DataFrame, str]:
    """Lee CSV/TSV/TXT/Excel con detección automática de header, decimal, separador."""
    ext = os.path.splitext(ruta)[1].lower()

    def _leer_excel(con_header):
        return pd.read_excel(ruta, header=0 if con_header else None)

    def _leer_csv(con_header, decimal="."):
        try:
            return pd.read_csv(
                ruta, header=0 if con_header else None,
                sep=None, engine="python", decimal=decimal,
                encoding="utf-8", encoding_errors="replace",
            )
        except Exception:
            return pd.read_csv(
                ruta, header=0 if con_header else None,
                sep=r"[\s,;\t]+", engine="python", decimal=decimal,
                encoding="utf-8", encoding_errors="replace",
            )

    if ext in (".xlsx", ".xls"):
        df_sondeo = _leer_excel(con_header=False)
    else:
        df_sondeo = _leer_csv(con_header=False)

    if df_sondeo.empty or df_sondeo.shape[1] < 2:
        raise ValueError(f"El archivo debe tener al menos dos columnas: {os.path.basename(ruta)}")

    primera = df_sondeo.iloc[0]
    tiene_header = False
    for val in primera:
        s = str(val).strip().replace(",", ".")
        try:
            float(s)
        except (ValueError, TypeError):
            tiene_header = True
            break

    if ext in (".xlsx", ".xls"):
        df = _leer_excel(con_header=tiene_header)
    else:
        df = _leer_csv(con_header=tiene_header)

    if not tiene_header:
        df.columns = [f"col{i}" for i in range(df.shape[1])]

    if ext not in (".xlsx", ".xls") and df.shape[1] >= 2:
        test_col = df.iloc[:, 1]
        if pd.to_numeric(test_col, errors="coerce").isna().all() and len(test_col):
            try:
                df = _leer_csv(con_header=tiene_header, decimal=",")
                if not tiene_header:
                    df.columns = [f"col{i}" for i in range(df.shape[1])]
            except Exception:
                pass

    cols = list(df.columns)

    nombres_anio = {"anio", "año", "year", "anios", "años", "yr"}
    year_col = None
    for c in cols:
        if str(c).strip().lower() in nombres_anio:
            year_col = c
            break

    if year_col is None:
        for c in cols:
            test = pd.to_numeric(df[c], errors="coerce").dropna()
            if len(test) > 0 and test.between(-10000, 10000).all():
                if test.std() > 1 and (test % 1 == 0).all():
                    year_col = c
                    break

    if year_col is None:
        raise ValueError(f"No se encontró columna de año en: {os.path.basename(ruta)}")

    nombres_excluidos = {
        str(year_col).strip().lower(),
        "id_serie", "id", "serie", "muestra", "sample", "code",
        "n", "sd", "se", "samp", "samp.depth", "sample.depth",
    }
    value_col = None
    for c in cols:
        if str(c).strip().lower() in nombres_excluidos:
            continue
        test = pd.to_numeric(df[c], errors="coerce")
        if test.notna().sum() > 0:
            value_col = c
            break

    if value_col is None:
        raise ValueError(f"No se encontró columna de valores en: {os.path.basename(ruta)}")

    df_out = pd.DataFrame({
        "Anio": pd.to_numeric(df[year_col], errors="coerce"),
        "Ancho_mm": pd.to_numeric(df[value_col], errors="coerce"),
    }).dropna()

    if df_out.empty:
        raise ValueError(f"No se encontraron datos válidos en: {os.path.basename(ruta)}")

    df_out["Anio"] = df_out["Anio"].astype(int)
    df_out = df_out.groupby("Anio", as_index=True).mean(numeric_only=True)
    df_out.index.name = "Anio"
    df_out.sort_index(inplace=True)

    nombre = os.path.splitext(os.path.basename(ruta))[0]
    return df_out, nombre


def leer_columna_multi(ruta: str) -> pd.DataFrame:
    """Lee archivo con N columnas de valor, devuelve DataFrame con índice año."""
    ext = os.path.splitext(ruta)[1].lower()

    if ext in (".xlsx", ".xls"):
        df_raw = pd.read_excel(ruta)
    else:
        try:
            df_raw = pd.read_csv(ruta, sep=None, engine="python",
                                 encoding="utf-8", encoding_errors="replace")
        except Exception:
            df_raw = pd.read_csv(ruta, sep=r"[\s,;\t]+", engine="python",
                                 encoding="utf-8", encoding_errors="replace")

        if df_raw.shape[1] >= 2:
            test = pd.to_numeric(df_raw.iloc[1:, 1], errors="coerce")
            if test.isna().all() and len(test) > 0:
                try:
                    df_raw = pd.read_csv(ruta, sep=None, engine="python", decimal=",",
                                         encoding="utf-8", encoding_errors="replace")
                except Exception:
                    pass

    if df_raw.shape[1] < 2:
        raise ValueError("El archivo debe tener al menos dos columnas (año + valor[es]).")

    cols = list(df_raw.columns)
    year_col = None
    for c in cols:
        if str(c).lower() in ("anio", "año", "year", "anios", "años"):
            year_col = c
            break
    if year_col is None:
        year_col = cols[0]

    df_raw[year_col] = pd.to_numeric(df_raw[year_col], errors="coerce")
    df_raw = df_raw.dropna(subset=[year_col])
    df_raw[year_col] = df_raw[year_col].astype(int)
    df_raw.set_index(year_col, inplace=True)
    df_raw.index.name = "Anio"
    df_raw.sort_index(inplace=True)

    for c in df_raw.columns:
        df_raw[c] = pd.to_numeric(df_raw[c], errors="coerce")

    cols_valor = [c for c in df_raw.columns
                  if str(c).strip().lower() not in (
                      "n", "sd", "se", "samp", "samp.depth", "sample.depth",
                      "id_serie", "id", "serie", "muestra", "sample", "code"
                  )]
    if not cols_valor:
        raise ValueError("No se encontraron columnas de valores (todo es N/SD).")

    return df_raw[cols_valor]


# =============================================================================
# CRONOLOGÍA: NORMALIZACIÓN Y CONSTRUCCIÓN
# =============================================================================

def _biweight_mean(values, c: float = 6.0) -> float:
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return np.nan
    if len(x) < 3:
        return float(np.mean(x))
    m = np.median(x)
    mad = np.median(np.abs(x - m))
    if mad == 0:
        return float(np.mean(x))
    u = (x - m) / (c * mad)
    mask = np.abs(u) < 1
    if not np.any(mask):
        return float(np.mean(x))
    w = (1 - u[mask] ** 2) ** 2
    return float(np.sum((x[mask] - m) * w) / np.sum(w) + m)


def _fit_trend(values: np.ndarray, metodo: str, ventana_mm: int | None = None) -> np.ndarray:
    """Ajusta tendencia a la serie según el método indicado.

    Parameters
    ----------
    values : array
        Valores de la serie.
    metodo : str
        Método de ajuste: 'spline', 'negexp', 'linear', 'media_movil', 'mean'.
    ventana_mm : int, opcional
        Ventana (en años) para 'media_movil'. Si None, usa el 10% del largo
        de la serie (estilo ARSTAN). Solo aplica si metodo='media_movil'.
    """
    from scipy.interpolate import UnivariateSpline
    from scipy.optimize import curve_fit

    y = np.asarray(values, dtype=float)
    x = np.arange(len(y), dtype=float)
    m = np.isfinite(y)
    if m.sum() < 3:
        return np.full_like(y, np.nanmean(y[m]) if m.any() else np.nan, dtype=float)

    x_fit = x[m]
    y_fit = y[m]
    metodo = (metodo or "spline").lower()

    if metodo in {"raw", "none", "ninguno"}:
        return np.full_like(y, np.nanmean(y_fit), dtype=float)
    if metodo in {"mean", "media"}:
        return np.full_like(y, np.nanmean(y_fit), dtype=float)
    if metodo in {"linear", "lineal"}:
        p = np.polyfit(x_fit, y_fit, 1)
        return np.polyval(p, x)
    if metodo in {"negexp", "negative_exponential", "exponencial", "exp"}:
        def f(t, a, b, c):
            return a * np.exp(-b * t) + c
        a0 = max(float(np.nanmax(y_fit) - np.nanmin(y_fit)), 1e-3)
        b0 = 0.01
        c0 = float(np.nanmin(y_fit))
        try:
            popt, _ = curve_fit(
                f, x_fit, y_fit, p0=(a0, b0, c0), maxfev=20000,
                bounds=([0.0, 0.0, -np.inf], [np.inf, 10.0, np.inf]),
            )
            return f(x, *popt)
        except Exception:
            p = np.polyfit(x_fit, y_fit, 1)
            return np.polyval(p, x)
    if metodo in {"spline", "spl", "cubic_spline"}:
        try:
            s = max(len(x_fit) * np.nanvar(y_fit) * 0.8, 0.0)
            spl = UnivariateSpline(x_fit, y_fit, s=s)
            return spl(x)
        except Exception:
            p = np.polyfit(x_fit, y_fit, 1)
            return np.polyval(p, x)
    if metodo in {"media_movil", "moving_average", "ma"}:
        # Si el usuario especifica ventana, usar esa. Si no, default ARSTAN-like.
        if ventana_mm is not None and ventana_mm >= 3:
            w = int(ventana_mm)
        else:
            w = max(3, int(round(len(y_fit) * 0.10)))
        if w % 2 == 0:
            w += 1
        return pd.Series(y).rolling(window=w, center=True, min_periods=1).mean().to_numpy(dtype=float)
    p = np.polyfit(x_fit, y_fit, 1)
    return np.polyval(p, x)


def _detrend_serie_indice(serie_mm: pd.Series, metodo: str,
                           ventana_mm: int | None = None) -> pd.Series:
    s = pd.to_numeric(serie_mm, errors="coerce").astype(float).dropna()
    if s.empty:
        return s.copy()
    if (metodo or "").lower() in {"raw", "none", "ninguno"}:
        return s.copy()
    tendencia = _fit_trend(s.to_numpy(dtype=float), metodo, ventana_mm=ventana_mm)
    tendencia = pd.Series(tendencia, index=s.index, dtype=float).replace(0, np.nan)
    idx = (s / tendencia).replace([np.inf, -np.inf], np.nan).dropna()
    return idx


def _residualizar_indice(indice: pd.Series) -> pd.Series:
    s = pd.to_numeric(indice, errors="coerce").astype(float).dropna()
    if len(s) < 4:
        return s.copy()
    c = s - s.mean()
    if np.std(c.to_numpy()) == 0:
        return s.copy()
    phi = np.corrcoef(c.iloc[1:].to_numpy(), c.iloc[:-1].to_numpy())[0, 1]
    if np.isnan(phi):
        phi = 0.0
    resid = c.copy()
    resid.iloc[1:] = c.iloc[1:].to_numpy() - phi * c.iloc[:-1].to_numpy()
    resid = resid - resid.mean() + 1.0
    resid.index = s.index
    return resid


def _normalizar_serie_para_tipo(serie_mm: pd.Series, tipo_cronologia: str,
                                  metodo_estandarizacion: str,
                                  ventana_mm: int | None = None) -> pd.Series:
    tipo = (tipo_cronologia or "raw").lower()
    if tipo == "raw":
        return pd.to_numeric(serie_mm, errors="coerce").astype(float).dropna()
    idx = _detrend_serie_indice(serie_mm, metodo_estandarizacion, ventana_mm=ventana_mm)
    if tipo == "residual":
        return _residualizar_indice(idx)
    return idx


def _construir_cronologia_desde_series(
    series: dict[str, pd.DataFrame],
    nombres: list[str],
    tipo_cronologia: str,
    metodo_estandarizacion: str,
    agregacion: str,
    ventana_mm: int | None = None,
) -> tuple[pd.DataFrame, dict]:
    if not nombres:
        raise ValueError("No hay series seleccionadas.")

    columnas = []
    for nombre in nombres:
        if nombre not in series:
            continue
        s_norm = _normalizar_serie_para_tipo(
            series[nombre]["Ancho_mm"], tipo_cronologia,
            metodo_estandarizacion, ventana_mm=ventana_mm
        )
        s_norm.name = nombre
        columnas.append(s_norm)
    if not columnas:
        raise ValueError("No hay datos válidos para construir la cronología.")

    df = pd.concat(columnas, axis=1).sort_index()
    if df.empty:
        raise ValueError("La cronología quedó vacía.")

    agg = (agregacion or "biweight").lower()
    tipo = (tipo_cronologia or "raw").lower()

    if agg in {"biweight", "robusta", "robust"}:
        valores = df.apply(lambda row: _biweight_mean(row.dropna().values), axis=1)
    elif agg in {"median", "mediana"}:
        valores = df.median(axis=1, skipna=True)
    else:
        valores = df.mean(axis=1, skipna=True)

    ns = df.notna().sum(axis=1)
    sds = df.std(axis=1, skipna=True, ddof=1).fillna(0.0)

    col_name = {"raw": "Raw", "residual": "Residual"}.get(tipo, "Standard")
    cron = pd.DataFrame(
        {col_name: valores, "N": ns.astype(int), "SD": sds},
        index=pd.Index(df.index.astype(int), name="Anio"),
    )
    cron = cron.dropna(subset=[col_name]).sort_index()

    # ── Rbar y EPS de PERÍODO COMPLETO sobre SERIES DETRENDADAS ──────
    # Antes calcular_rbar usaba las series RAW (Ancho_mm), lo que daba
    # valores inflados porque las tendencias decadales hacen que las
    # series brutas covaríen fuertemente a largo plazo. ARSTAN computa
    # su "all possible series rbar" sobre las series DETRENDADAS, que
    # es lo correcto: queremos la señal común año-a-año, no las
    # tendencias compartidas. Acá usamos el `df` de series detrendadas
    # que ya construimos arriba.
    rs_pairs = []
    cols = list(df.columns)
    for i in range(len(cols)):
        si = df[cols[i]].dropna()
        for j in range(i + 1, len(cols)):
            sj = df[cols[j]].dropna()
            common = si.index.intersection(sj.index)
            if len(common) < 10:
                continue
            a = si.loc[common].to_numpy(dtype=float)
            b = sj.loc[common].to_numpy(dtype=float)
            if np.std(a) == 0 or np.std(b) == 0:
                continue
            r = float(np.corrcoef(a, b)[0, 1])
            if np.isfinite(r):
                rs_pairs.append(r)
    rbar_completo = float(np.mean(rs_pairs)) if rs_pairs else float("nan")
    # Profundidad de muestreo promedio (mean number of series per year
    # with valid data) — igual que la columna "cores" de ARSTAN.
    sample_depth_mean = float(ns[ns > 0].mean()) if (ns > 0).any() else 0.0
    if np.isfinite(rbar_completo) and sample_depth_mean > 1:
        eps_completo = float(
            (sample_depth_mean * rbar_completo) /
            (1 + (sample_depth_mean - 1) * rbar_completo)
        )
    else:
        eps_completo = float("nan")

    meta = {
        "nombre": "",
        "tipo_cronologia": tipo,
        "metodo_estandarizacion": metodo_estandarizacion,
        "agregacion": agg,
        "series_usadas": list(nombres),
        "n_series": len(nombres),
        "n_anios": int(len(cron)),
        "creado_en": pd.Timestamp.utcnow().isoformat(),
        "columna_valor": col_name,
        # Rbar/EPS sobre series detrendadas (comparable con ARSTAN
        # "all possible series rbar")
        "Rbar": rbar_completo,
        "EPS": eps_completo,
        "sample_depth_mean": sample_depth_mean,
    }
    return cron, meta


def _ruta_metadatos_cronologia(ruta: str) -> str:
    base = os.path.basename(ruta)
    carpeta = os.path.dirname(ruta)
    return os.path.join(carpeta, f".{os.path.splitext(base)[0]}.json")


def _guardar_metadatos_cronologia(ruta: str, meta: dict):
    with open(_ruta_metadatos_cronologia(ruta), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def _cargar_metadatos_cronologia(ruta: str) -> dict | None:
    meta_path = _ruta_metadatos_cronologia(ruta)
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _exportar_cronologia_df(df: pd.DataFrame, ruta: str):
    ext = os.path.splitext(ruta)[1].lower()
    df_out = df.copy()
    df_out.index.name = "Anio"
    if ext == ".xlsx":
        df_out.to_excel(ruta, index=True)
    elif ext == ".csv":
        df_out.to_csv(ruta, index=True)
    else:
        df_out.to_csv(ruta, index=True, sep="\t")


def _leer_cronologia_guardada(ruta: str) -> tuple[pd.DataFrame, dict]:
    ext = os.path.splitext(ruta)[1].lower()
    if ext == ".xlsx":
        df = pd.read_excel(ruta)
    elif ext == ".csv":
        df = pd.read_csv(ruta)
        if df.shape[1] < 2:
            df = pd.read_csv(ruta, sep=";", decimal=",")
    else:
        try:
            df = pd.read_csv(ruta, sep="\t")
            test_col = df.iloc[:, 1] if df.shape[1] > 1 else pd.Series()
            if pd.to_numeric(test_col, errors="coerce").isna().all() and len(test_col) > 0:
                df = pd.read_csv(ruta, sep="\t", decimal=",")
        except Exception:
            try:
                df = pd.read_csv(ruta, sep=None, engine="python", decimal=",")
            except Exception:
                df = pd.read_csv(ruta, sep=None, engine="python")

    if df.shape[1] < 2:
        raise ValueError("La cronología debe tener al menos dos columnas.")

    cols = list(df.columns)
    year_col = None
    for c in cols:
        if c.lower() in ("anio", "año", "year"):
            year_col = c
            break
    if year_col is None:
        year_col = cols[0]

    df[year_col] = pd.to_numeric(df[year_col], errors="coerce")
    df = df.dropna(subset=[year_col])
    df[year_col] = df[year_col].astype(int)
    df.set_index(year_col, inplace=True)
    df.index.name = "Anio"
    df.sort_index(inplace=True)

    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    value_cols = [c for c in df.columns if c.lower() not in ("n", "sd", "se")]

    meta = _cargar_metadatos_cronologia(ruta) or {}
    nombre = meta.get("nombre") or os.path.splitext(os.path.basename(ruta))[0]

    if not meta:
        cols_lower = {c.lower(): c for c in value_cols}
        columnas_disponibles = []
        for t in ["Raw", "Standard", "Residual"]:
            if t.lower() in cols_lower or t in df.columns:
                columnas_disponibles.append(t)

        if len(columnas_disponibles) > 1:
            tipo = "todas"
            col_valor = columnas_disponibles[0]
        elif len(columnas_disponibles) == 1:
            tipo = columnas_disponibles[0].lower()
            col_valor = columnas_disponibles[0]
        else:
            tipo = "raw"
            col_valor = value_cols[0] if value_cols else df.columns[0]

        meta = {
            "nombre": nombre, "tipo_cronologia": tipo,
            "metodo_estandarizacion": "desconocido", "agregacion": "desconocido",
            "series_usadas": [], "n_series": 0,
            "n_anios": int(len(df)),
            "columna_valor": col_valor,
            "columnas_disponibles": columnas_disponibles if len(columnas_disponibles) > 1 else [],
        }

    meta.setdefault("nombre", nombre)
    meta.setdefault("n_anios", int(len(df)))

    col_val = meta.get("columna_valor", "")
    if col_val not in df.columns:
        for c in df.columns:
            if c.lower() == col_val.lower():
                meta["columna_valor"] = c
                break
        else:
            meta["columna_valor"] = value_cols[0] if value_cols else df.columns[0]

    return df, meta


def _serie_transformada_para_comparar(df: pd.DataFrame, meta: dict) -> pd.Series:
    tipo = (meta.get("tipo_cronologia") or "raw").lower()
    metodo = meta.get("metodo_estandarizacion", "spline")
    return _normalizar_serie_para_tipo(df["Ancho_mm"], tipo, metodo)


def _correlacion_por_desfase(serie_a: pd.Series, serie_b: pd.Series,
                              max_shift: int, min_overlap: int = MIN_OVERLAP_DEFAULT
                              ) -> tuple[list[int], list[float]]:
    shifts, rs = [], []
    for shift in range(-max_shift, max_shift + 1):
        shifted = serie_a.copy()
        shifted.index = shifted.index + shift
        overlap = shifted.index.intersection(serie_b.index)
        if len(overlap) < min_overlap:
            continue
        a = shifted.loc[overlap].to_numpy(dtype=float)
        b = serie_b.loc[overlap].to_numpy(dtype=float)
        if len(a) < 2 or len(b) < 2 or np.std(a) == 0 or np.std(b) == 0:
            continue
        r = float(np.corrcoef(a, b)[0, 1])
        if np.isfinite(r):
            shifts.append(shift)
            rs.append(r)
    return shifts, rs


def _estadisticos_por_desfase(
    serie_a: pd.Series, serie_b: pd.Series,
    max_shift: int, min_overlap: int = MIN_OVERLAP_DEFAULT
) -> tuple[list[int], list[float], list[float], list[float], list[int]]:
    """Versión extendida de `_correlacion_por_desfase` que calcula r, GLK
    y t-BP en cada desplazamiento (shift).

    Sirve para integrar los 3 estadísticos al cofechado con series
    YA fechadas (donde antes solo se mostraba r). El cofechado con
    series flotantes ya usaba los 3 estadísticos vía
    `VentanaTablaOffset`; esta función trae la misma capacidad al
    flujo de "comparar serie vs cronología de referencia".

    Devuelve listas paralelas: (shifts, rs, glks, t_bps, ns) donde
    cada entrada en posición i corresponde a un shift válido (con
    overlap >= min_overlap).

    Diseño:
      - Se reutilizan las funciones `gleichlaufigkeit` y
        `t_baillie_pilcher` que ya están definidas en este módulo,
        para mantener la coherencia con el resto del cofechado.
      - Si en algún shift una métrica no puede calcularse (NaN),
        se conserva ese shift con NaN en la métrica afectada — así
        la tabla puede mostrar exactamente qué pasa en cada caso.
    """
    shifts, rs, glks, tbps, ns = [], [], [], [], []
    for shift in range(-max_shift, max_shift + 1):
        shifted = serie_a.copy()
        shifted.index = shifted.index + shift
        overlap = shifted.index.intersection(serie_b.index)
        n = len(overlap)
        if n < min_overlap:
            continue

        a_arr = shifted.loc[overlap].to_numpy(dtype=float)
        b_arr = serie_b.loc[overlap].to_numpy(dtype=float)
        if len(a_arr) < 2 or len(b_arr) < 2:
            continue
        if np.std(a_arr) == 0 or np.std(b_arr) == 0:
            continue

        r = float(np.corrcoef(a_arr, b_arr)[0, 1])
        if not np.isfinite(r):
            continue

        # Series alineadas para GLK y t-BP. Se construyen sobre el
        # overlap con el shift aplicado para que los cálculos usen
        # exactamente los mismos años en ambas series.
        a_aligned = pd.Series(a_arr, index=overlap)
        b_aligned = pd.Series(b_arr, index=overlap)

        glk, _ = gleichlaufigkeit(a_aligned, b_aligned,
                                   min_overlap=min(10, min_overlap))
        t_bp, _, _ = t_baillie_pilcher(a_aligned, b_aligned,
                                        min_overlap=min(20, min_overlap))

        shifts.append(shift)
        rs.append(r)
        glks.append(glk if np.isfinite(glk) else float("nan"))
        tbps.append(t_bp if np.isfinite(t_bp) else float("nan"))
        ns.append(n)

    return shifts, rs, glks, tbps, ns


# =============================================================================
# MOTOR DE BÚSQUEDA EXHAUSTIVA DE CORRECCIONES
# =============================================================================

def _diff_log(serie: pd.Series) -> pd.Series:
    """Diferencias de logaritmos — transforma anchos en tasas de crecimiento."""
    return np.log(serie.replace(0, np.nan).dropna()).diff().dropna()


def _r_pearson(a: np.ndarray, b: np.ndarray) -> float:
    if np.std(a) == 0 or np.std(b) == 0:
        return np.nan
    r = np.corrcoef(a, b)[0, 1]
    return float(r) if not np.isnan(r) else np.nan


def _r_movil(m: pd.Series, t: pd.Series, overlap: list, mitad: int) -> tuple[list, list]:
    """Correlación de Pearson en ventana deslizante centrada."""
    years, r_vals = [], []
    for anio in overlap:
        ventana = [y for y in overlap if 0 < abs(y - anio) <= mitad]
        if len(ventana) < OVERLAP_MINIMO_GRAFICO:
            continue
        r = _r_pearson(m.loc[ventana].values, t.loc[ventana].values)
        if not np.isnan(r):
            years.append(float(anio))
            r_vals.append(r)
    return years, r_vals


def _stats_movil(m, t, overlap, mitad, m_raw=None, t_raw=None):
    """Versión extendida de _r_movil que calcula r, GLK y t-BP por ventana."""
    if m_raw is None:
        m_raw = m
    if t_raw is None:
        t_raw = t

    years, r_vals, glk_vals, tbp_vals, n_vals = [], [], [], [], []

    for anio in overlap:
        ventana = [y for y in overlap if 0 < abs(y - anio) <= mitad]
        n_v = len(ventana)
        if n_v < OVERLAP_MINIMO_GRAFICO:
            continue

        r = _r_pearson(m.loc[ventana].values, t.loc[ventana].values)
        if np.isnan(r):
            continue

        m_raw_w = m_raw.loc[m_raw.index.intersection(ventana)]
        t_raw_w = t_raw.loc[t_raw.index.intersection(ventana)]
        glk, _ = gleichlaufigkeit(m_raw_w, t_raw_w, min_overlap=5)
        t_bp, _, _ = t_baillie_pilcher(m_raw_w, t_raw_w, min_overlap=5)

        years.append(float(anio))
        r_vals.append(r)
        glk_vals.append(glk)
        tbp_vals.append(t_bp)
        n_vals.append(n_v)

    return years, r_vals, glk_vals, tbp_vals, n_vals


def aplicar_correccion(df: pd.DataFrame, anio: int, delta: int) -> pd.DataFrame:
    """Inserta o elimina delta anillos en una posición.

    Convención **pith-anchored** (médula fija): los años ≤ anio NO cambian.
    Solo se desplazan los años posteriores al punto de corrección. Esto es
    consistente con el flujo de trabajo de DPI donde "Editar Año Base" ancla
    por defecto el año de la médula (primer anillo, lo más cercano al inicio
    de vida del árbol).

      • delta > 0 (anillo faltante): los años ≥ anio se mueven +1, se
        inserta un placeholder en `anio` con el ancho promedio.
      • delta < 0 (anillo extra):    se elimina la fila en `anio` y los
        años > anio se mueven -1.

    Para una corrección en el medio de la serie, los puntos de la curva de
    correlación móvil quedan idénticos antes del año corregido y solo
    cambian a partir de él.
    """
    df = df.copy()
    for _ in range(abs(delta)):
        if delta > 0:
            df.index = [idx + 1 if idx >= anio else idx for idx in df.index]
            df.loc[anio] = df["Ancho_mm"].mean()
        else:
            if anio in df.index:
                df = df.drop(index=anio)
            df.index = [idx - 1 if idx > anio else idx for idx in df.index]
    return df.sort_index()


def buscar_correcciones(ref, problema, mitad_ventana, max_corr=MAX_CORRECCION,
                        transformar_ref=None, transformar_prob=None,
                        delta_r_minimo: float = 0.05,
                        max_fallback: int = 3):
    """Busca correcciones que mejoran la correlación global.

    Devuelve tupla **(sugerencias, es_marginal)**:
      • Si hay correcciones con Δr ≥ delta_r_minimo: devuelve esas (hasta 15) y
        es_marginal = False.
      • Si no hay correcciones significativas pero sí hay alguna mejora positiva:
        devuelve las mejores hasta `max_fallback` y es_marginal = True. Esto
        permite mostrar un "sanity check" al usuario en lugar de una lista vacía,
        con la advertencia de que son cambios dentro del ruido estadístico.
      • Si no hay ninguna mejora positiva: devuelve ([], False).

    delta_r_minimo: filtro de ruido. 0.05 es razonable; con series ya
    correctamente cofechadas las "mejoras" suelen ser 0.01-0.04 (ruido) y
    las correcciones reales suelen estar en 0.10+.
    """
    if transformar_ref is None:
        transformar_ref = lambda s: pd.to_numeric(s, errors="coerce").astype(float).dropna()
    if transformar_prob is None:
        transformar_prob = lambda s: pd.to_numeric(s, errors="coerce").astype(float).dropna()

    d_ref = transformar_ref(ref["Ancho_mm"])
    d_prob = transformar_prob(problema["Ancho_mm"])
    overlap_base = list(d_ref.index.intersection(d_prob.index))
    if len(overlap_base) < OVERLAP_MINIMO_ANALISIS:
        return [], False

    r_global_base = _r_pearson(d_ref.loc[overlap_base].values, d_prob.loc[overlap_base].values)
    if np.isnan(r_global_base):
        return [], False

    _, r_movil_base = _r_movil(d_ref, d_prob, overlap_base, mitad_ventana)

    resultados = []
    anios_problema = list(problema.index)
    for anio in anios_problema:
        for delta in range(-max_corr, max_corr + 1):
            if delta == 0:
                continue
            df_corr = aplicar_correccion(problema, anio, delta)
            d_corr = transformar_prob(df_corr["Ancho_mm"])
            overlap_corr = list(d_ref.index.intersection(d_corr.index))
            if len(overlap_corr) < OVERLAP_MINIMO_ANALISIS:
                continue
            r_global_tras = _r_pearson(d_ref.loc[overlap_corr].values, d_corr.loc[overlap_corr].values)
            if np.isnan(r_global_tras):
                continue
            delta_r = r_global_tras - r_global_base
            if delta_r <= 0:
                continue

            zona = [y for y in overlap_corr if abs(y - anio) <= mitad_ventana]
            r_zona_tras = np.nan
            if len(zona) >= OVERLAP_MINIMO_GRAFICO:
                r_zona_tras = _r_pearson(d_ref.loc[zona].values, d_corr.loc[zona].values)
            zona_base = [y for y in overlap_base if abs(y - anio) <= mitad_ventana]
            r_zona_antes = np.nan
            if len(zona_base) >= OVERLAP_MINIMO_GRAFICO:
                r_zona_antes = _r_pearson(d_ref.loc[zona_base].values, d_prob.loc[zona_base].values)

            resultados.append({
                "anio": anio, "delta": delta,
                "tipo": f"+{delta} anillo(s)" if delta > 0 else f"{delta} anillo(s)",
                "r_antes": r_global_base, "r_tras": r_global_tras,
                "delta_r": delta_r, "r_zona_antes": r_zona_antes,
                "r_zona_tras": r_zona_tras, "df_corregido": df_corr,
            })

    resultados.sort(key=lambda x: x["delta_r"], reverse=True)

    # Deduplicar correcciones solapadas (cerca del mismo año con mismo signo)
    filtrados, ocupados = [], []
    for r in resultados:
        solapado = any(
            abs(r["anio"] - oc["anio"]) <= 5 and np.sign(r["delta"]) == np.sign(oc["delta"])
            for oc in ocupados)
        if not solapado:
            filtrados.append(r)
            ocupados.append(r)

    # Separar en significativas y fallback marginal
    significativas = [r for r in filtrados if r["delta_r"] >= delta_r_minimo]
    if significativas:
        return significativas[:15], False
    return filtrados[:max_fallback], True


# =============================================================================
# MODO COFECHA
# =============================================================================

def aplicar_modo_cofecha(serie_mm, rigidez_spline=32, aplicar_log=True,
                          aplicar_ar=True, aplicar_first_diff=False):
    """Spline detrending + log + AR(1). Holmes (1983)."""
    s = pd.to_numeric(serie_mm, errors="coerce").astype(float).dropna()
    if len(s) < 10:
        return s.copy()

    valores = s.to_numpy(dtype=float)

    try:
        from scipy.interpolate import UnivariateSpline
        x = np.arange(len(valores), dtype=float)
        s_factor = max(
            np.var(valores) * len(valores) * (rigidez_spline / max(len(valores), 1)) ** 1.5,
            1e-3,
        )
        spl = UnivariateSpline(x, valores, s=s_factor)
        tendencia = spl(x)
        tendencia = np.where(tendencia <= 0, np.nanmean(valores), tendencia)
        valores = valores / tendencia
    except Exception as exc:
        logger.warning("Spline COFECHA falló: %s", exc)
        valores = valores / np.mean(valores)

    if aplicar_log:
        media = np.mean(valores)
        c_const = media / 6.0 if media > 0 else 0.001
        valores = np.log(np.maximum(valores + c_const, 1e-10))

    if aplicar_ar and len(valores) > 4:
        c = valores - np.mean(valores)
        if np.std(c) > 0:
            phi = np.corrcoef(c[1:], c[:-1])[0, 1]
            if np.isnan(phi):
                phi = 0.0
            phi = float(np.clip(phi, -0.99, 0.99))
            residuales = c.copy()
            residuales[1:] = c[1:] - phi * c[:-1]
            valores = residuales

    if aplicar_first_diff and len(valores) > 1:
        valores = np.diff(valores)
        return pd.Series(valores, index=s.index[1:])

    return pd.Series(valores, index=s.index)


class DialogoCofecha(QDialog):
    """Configuración del Modo COFECHA."""

    def __init__(self, parent=None, params: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Modo COFECHA — Configuración")
        self.setMinimumWidth(520)
        params = params or {}
        layout = QVBoxLayout(self)

        titulo = QLabel(
            "<b>Transformaciones tipo COFECHA</b><br>"
            "<span style='color:#888; font-size:11px;'>"
            "Holmes (1983). Aplicado a las series antes de calcular correlaciones."
            "</span>"
        )
        titulo.setWordWrap(True)
        layout.addWidget(titulo)

        grp_sp = QGroupBox("1. Spline detrending")
        f_sp = QFormLayout(grp_sp)
        self.spin_rigidez = SpinBoxFlechas()
        self.spin_rigidez.setRange(10, 200)
        self.spin_rigidez.setValue(int(params.get("rigidez_spline", 32)))
        self.spin_rigidez.setSuffix(" años")
        f_sp.addRow("Rigidez:", self.spin_rigidez)
        layout.addWidget(grp_sp)

        grp_t = QGroupBox("2. Transformaciones")
        v_t = QVBoxLayout(grp_t)
        self.chk_log = QCheckBox("Transformación logarítmica  log(x + media/6)")
        self.chk_log.setStyleSheet(ESTILO_CHK_COFECHA)
        self.chk_log.setChecked(params.get("aplicar_log", True))
        self.chk_log.setToolTip(
            "Pondera de forma más equitativa las diferencias proporcionales.\n"
            "La constante 1/6 de la media evita log(0)."
        )
        v_t.addWidget(self.chk_log)
        self.chk_ar = QCheckBox("Modelado AR(1)")
        self.chk_ar.setStyleSheet(ESTILO_CHK_COFECHA)
        self.chk_ar.setChecked(params.get("aplicar_ar", True))
        self.chk_ar.setToolTip(
            "Elimina la autocorrelación (inercia biológica).\n"
            "Resta phi × x[t-1] de cada valor."
        )
        v_t.addWidget(self.chk_ar)
        layout.addWidget(grp_t)

        grp_fd = QGroupBox("3. Opcional")
        v_fd = QVBoxLayout(grp_fd)
        self.chk_fd = QCheckBox("Primeras diferencias  x[t] − x[t−1]")
        self.chk_fd.setStyleSheet(ESTILO_CHK_COFECHA)
        self.chk_fd.setChecked(params.get("aplicar_first_diff", False))
        self.chk_fd.setToolTip(
            "Estabiliza series con períodos prolongados de varianza alta y baja.\n"
            "Activar solo si las transformaciones anteriores no son suficientes."
        )
        v_fd.addWidget(self.chk_fd)
        layout.addWidget(grp_fd)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_params(self) -> dict:
        return {
            "rigidez_spline": self.spin_rigidez.value(),
            "aplicar_log": self.chk_log.isChecked(),
            "aplicar_ar": self.chk_ar.isChecked(),
            "aplicar_first_diff": self.chk_fd.isChecked(),
        }


# =============================================================================
# VENTANA TABLA DE OFFSET
# =============================================================================

class VentanaTablaOffset(QDialog):
    """Tabla detallada con r, GLK y t-BP por cada desfase.

    Si se pasan `serie_a` y `serie_b` (las series transformadas usadas
    en el cálculo), agrega ARRIBA de la tabla un gráfico apropiado al
    tipo de serie:
      - Serie FLOTANTE (año mínimo < 500): gráfico de barras del
        puntaje compuesto por cada desfase. Permite ver de un vistazo
        dónde está el pico que indica la posición candidata.
      - Serie FECHADA (año mínimo ≥ 500): gráfico de correlación
        móvil con ventanas deslizantes año a año, aplicando el mejor
        desfase. Permite identificar zonas problemáticas dentro de la
        serie (años donde el cofechado se debilita).
    """

    def __init__(self, shifts, rs, glks=None, tbps=None, ns=None,
                 serie_a=None, serie_b=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Resultados de Análisis de Desfases")
        # Si vamos a mostrar gráfico, necesitamos más alto
        if serie_a is not None and serie_b is not None and len(shifts) > 0:
            self.resize(720, 720)
        else:
            self.resize(560, 540)
        layout = QVBoxLayout(self)

        info = QLabel(
            "<span style='color:#aaa; font-size:11px;'>"
            "Resultados ordenados por <b>puntaje compuesto</b> descendente. "
            "El puntaje combina las tres métricas (r × GLK × t-BP)^(1/3) — "
            "es lo más confiable para identificar la fecha verdadera."
            "</span>"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        glks = glks if glks is not None else [float("nan")] * len(shifts)
        tbps = tbps if tbps is not None else [float("nan")] * len(shifts)
        ns   = ns   if ns   is not None else [0] * len(shifts)

        scores = [score_compuesto(r, g, t) for r, g, t in zip(rs, glks, tbps)]

        # ── Gráfico opcional ──────────────────────────────────────────
        if serie_a is not None and serie_b is not None and len(shifts) > 0:
            self._agregar_grafico_detalle(
                layout, shifts, rs, glks, tbps, scores,
                serie_a, serie_b,
            )

        self.tabla = QTableWidget(len(shifts), 6)
        self.tabla.setHorizontalHeaderLabels(
            ["Desfase", "Puntaje", "r", "GLK", "t-BP", "n"])
        self.tabla.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tabla.setSortingEnabled(False)

        for row, (sh, r, g, t, n, sc) in enumerate(zip(shifts, rs, glks, tbps, ns, scores)):
            it = QTableWidgetItem()
            it.setData(Qt.ItemDataRole.DisplayRole, int(sh))
            self.tabla.setItem(row, 0, it)

            it = QTableWidgetItem()
            it.setData(Qt.ItemDataRole.DisplayRole, float(round(sc, 3)) if np.isfinite(sc) else 0.0)
            it.setBackground(QColor(_color_compuesto(sc)))
            self.tabla.setItem(row, 1, it)

            it = QTableWidgetItem()
            it.setData(Qt.ItemDataRole.DisplayRole, float(round(r, 3)) if np.isfinite(r) else 0.0)
            it.setBackground(QColor(_color_r(r)))
            self.tabla.setItem(row, 2, it)

            it = QTableWidgetItem()
            it.setData(Qt.ItemDataRole.DisplayRole, float(round(g, 3)) if np.isfinite(g) else 0.0)
            it.setBackground(QColor(_color_glk(g)))
            self.tabla.setItem(row, 3, it)

            it = QTableWidgetItem()
            it.setData(Qt.ItemDataRole.DisplayRole, float(round(t, 2)) if np.isfinite(t) else 0.0)
            it.setBackground(QColor(_color_tbp(t)))
            self.tabla.setItem(row, 4, it)

            it = QTableWidgetItem()
            it.setData(Qt.ItemDataRole.DisplayRole, int(n))
            self.tabla.setItem(row, 5, it)

        self.tabla.setSortingEnabled(True)
        self.tabla.sortItems(1, Qt.SortOrder.DescendingOrder)
        layout.addWidget(self.tabla)

        fila_btns = QHBoxLayout()
        btn_copiar = QPushButton("📋 Copiar Todo")
        btn_copiar.clicked.connect(self.copiar_portapapeles)
        fila_btns.addWidget(btn_copiar)
        btn_exportar = QPushButton("💾 Exportar a CSV")
        btn_exportar.clicked.connect(self.exportar_csv)
        fila_btns.addWidget(btn_exportar)
        layout.addLayout(fila_btns)

    def _agregar_grafico_detalle(self, layout, shifts, rs, glks, tbps,
                                  scores, serie_a, serie_b):
        """Agrega un gráfico apropiado al tipo de serie arriba de la tabla.

        Detecta automáticamente si la serie es flotante o fechada según
        el año mínimo de la serie y dibuja el gráfico correspondiente:

          - Año mínimo < 500 → FLOTANTE → gráfico de barras del puntaje
            compuesto vs desfase. La barra más alta muestra la posición
            candidata.
          - Año mínimo ≥ 500 → FECHADA → correlación móvil año a año
            aplicando el mejor desfase. Permite ver zonas internas
            problemáticas de la serie.
        """
        anio_min = int(serie_a.index.min()) if not serie_a.empty else 0
        es_flotante = anio_min < 500

        grafico = pg.PlotWidget()
        grafico.setBackground(None)
        grafico.showGrid(x=True, y=True, alpha=0.3)
        grafico.setMinimumHeight(180)

        if es_flotante:
            self._dibujar_barras_desfase(grafico, shifts, scores)
        else:
            self._dibujar_correlacion_movil(grafico, serie_a, serie_b,
                                              shifts, scores)

        layout.addWidget(grafico)

    def _dibujar_barras_desfase(self, grafico, shifts, scores):
        """Gráfico de barras: puntaje compuesto vs desfase.

        Útil para series flotantes — la barra más alta indica la posición
        candidata. Las barras se colorean por el umbral del puntaje
        compuesto (verde=alto, amarillo=medio, rojo=bajo) usando los
        mismos colores que la tabla.
        """
        grafico.setTitle("Puntaje compuesto por desfase")
        grafico.setLabel("bottom", "Desfase (años)")
        grafico.setLabel("left", "Puntaje compuesto")

        # Reemplazar NaN por 0 para que las barras sean dibujables
        scores_plot = [s if np.isfinite(s) else 0.0 for s in scores]

        # Una barra por desfase, coloreada por el puntaje
        for x, s in zip(shifts, scores_plot):
            color = _color_compuesto(s) if np.isfinite(s) else "#666"
            bar = pg.BarGraphItem(
                x=[x], height=[s], width=0.8,
                brush=pg.mkBrush(QColor(color)),
                pen=pg.mkPen(QColor(color).darker(110)),
            )
            grafico.addItem(bar)

        # Línea horizontal en y=0 como referencia
        grafico.addItem(pg.InfiniteLine(
            pos=0, angle=0, pen=pg.mkPen("#888", width=1)))

        # Resaltar el mejor desfase con una línea vertical
        scores_validos = [(i, s) for i, s in enumerate(scores)
                          if np.isfinite(s)]
        if scores_validos:
            idx_mejor = max(scores_validos, key=lambda x: x[1])[0]
            grafico.addItem(pg.InfiniteLine(
                pos=shifts[idx_mejor], angle=90,
                pen=pg.mkPen("#5cb85c", width=2, style=Qt.PenStyle.DashLine)))

    def _dibujar_correlacion_movil(self, grafico, serie_a, serie_b,
                                     shifts, scores):
        """Gráfico de correlación móvil: r/GLK/t-BP en ventanas
        deslizantes a lo largo del solape, aplicando el mejor desfase.

        Útil para series fechadas — permite detectar tramos internos
        donde el cofechado se debilita (años con potenciales errores
        de medición o anillos faltantes/extra).
        """
        # Aplicar el mejor desfase a la serie_a antes de calcular ventanas
        scores_validos = [(i, s) for i, s in enumerate(scores)
                          if np.isfinite(s)]
        mejor_shift = shifts[max(scores_validos, key=lambda x: x[1])[0]] \
            if scores_validos else 0

        s_a_shifted = serie_a.copy()
        s_a_shifted.index = s_a_shifted.index + mejor_shift
        overlap = s_a_shifted.index.intersection(serie_b.index)

        if len(overlap) < 30:
            grafico.setTitle("Solape insuficiente para correlación móvil")
            return

        a_arr = s_a_shifted.loc[overlap].to_numpy(dtype=float)
        b_arr = serie_b.loc[overlap].to_numpy(dtype=float)
        anios_overlap = list(overlap)

        # Ventana móvil de 30 años (estándar dendrocronológico)
        VENTANA = 30
        if len(anios_overlap) < VENTANA:
            grafico.setTitle("Solape insuficiente para correlación móvil")
            return

        anios_centro = []
        rs_movil = []
        glks_movil = []
        tbps_movil = []

        for i in range(len(anios_overlap) - VENTANA + 1):
            a_win = a_arr[i:i + VENTANA]
            b_win = b_arr[i:i + VENTANA]
            if np.std(a_win) == 0 or np.std(b_win) == 0:
                continue
            r_win = float(np.corrcoef(a_win, b_win)[0, 1])
            if not np.isfinite(r_win):
                continue

            a_ser = pd.Series(a_win, index=anios_overlap[i:i + VENTANA])
            b_ser = pd.Series(b_win, index=anios_overlap[i:i + VENTANA])
            glk_win, _ = gleichlaufigkeit(a_ser, b_ser, min_overlap=10)
            t_win, _, _ = t_baillie_pilcher(a_ser, b_ser, min_overlap=20)

            anios_centro.append(anios_overlap[i + VENTANA // 2])
            rs_movil.append(r_win)
            glks_movil.append(glk_win if np.isfinite(glk_win) else 0.0)
            # Normalizamos t-BP a escala [-1, 1] dividiendo por 10 para
            # graficarlo en el mismo eje (t-BP > 5 ya es alto)
            tbps_movil.append(min(1.0, max(-1.0, t_win / 10.0))
                              if np.isfinite(t_win) else 0.0)

        if not anios_centro:
            grafico.setTitle("No se pudo calcular correlación móvil")
            return

        grafico.setTitle(
            f"Correlación móvil (ventana {VENTANA} años, "
            f"desfase aplicado: {mejor_shift:+d})"
        )
        grafico.setLabel("bottom", "Año (centro de la ventana)")
        grafico.setLabel("left", "Valor del estadístico")
        grafico.setYRange(-1.05, 1.05)
        grafico.addLegend(offset=(10, 10))

        grafico.plot(anios_centro, rs_movil,
                     pen=pg.mkPen("#3388FF", width=2), name="r (Pearson)")
        grafico.plot(anios_centro, glks_movil,
                     pen=pg.mkPen("#fd7e14", width=2), name="GLK")
        grafico.plot(anios_centro, tbps_movil,
                     pen=pg.mkPen("#9b59b6", width=2), name="t-BP / 10")

        # Línea de referencia en y=0
        grafico.addItem(pg.InfiniteLine(
            pos=0, angle=0, pen=pg.mkPen("#888", width=1)))

    def copiar_portapapeles(self):
        text = "Desfase\tPuntaje\tr\tGLK\tt-BP\tn\n"
        for row in range(self.tabla.rowCount()):
            text += "\t".join(
                self.tabla.item(row, c).text() for c in range(6)) + "\n"
        QApplication.clipboard().setText(text)

    def exportar_csv(self):
        ruta_default = os.path.join(_ultima_carpeta(), "resultados_desfases.csv")
        ruta, _ = QFileDialog.getSaveFileName(
            self, "Exportar Datos", ruta_default, "CSV (*.csv)")
        if not ruta:
            return
        _ultima_carpeta(ruta)
        try:
            with open(ruta, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Desfase", "Puntaje", "r", "GLK", "t-BP", "n"])
                for row in range(self.tabla.rowCount()):
                    writer.writerow([self.tabla.item(row, c).text() for c in range(6)])
            QMessageBox.information(self, "Éxito", "Datos exportados.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo guardar:\n{e}")


# =============================================================================
# DIÁLOGO DE COMPARACIÓN MÚLTIPLE VS CRONOLOGÍA
# =============================================================================

class DialogoComparacionMultiple(QDialog):
    """Tabla resumen de comparación de N series vs cronología activa.

    Cada fila = una serie con su mejor desfase + score compuesto + los 3
    estadísticos (r, GLK, t-BP) en ese desfase. La tabla es ordenable
    (default: por score descendente).

    Interpretación rápida por el usuario:
      - Para una serie FECHADA, el mejor desfase debería ser 0 con Δ≈0.
        Si el mejor desfase es ≠ 0 y Δ alto → posible error de datación
        (anillo faltante o extra).
      - Para una serie FLOTANTE, el mejor desfase indica su POSICIÓN
        candidata respecto al inicio de la cronología.

    Interacciones:
      - Doble clic en fila → abre `VentanaTablaOffset` con TODOS los
        desfases de esa serie individualmente.
      - Botón "Exportar CSV" guarda el resumen completo.

    Las celdas se colorean con los mismos umbrales que `VentanaTablaOffset`
    (verde / amarillo verdoso / amarillo / rojo según rangos publicados
    de r, GLK y t-BP), para ser consecuentes con el resto del cofechado.
    """

    # Umbral de score por debajo del cual NO resaltamos filas con shift≠0
    # (resaltar correlaciones débiles solo agrega ruido visual)
    UMBRAL_SCORE_PARA_RESALTAR = 0.30

    def __init__(self, resultados: list[dict], nombre_crono: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(
            f"Comparación múltiple vs cronología '{nombre_crono}'")
        self.resize(900, 600)
        self._resultados_originales = resultados

        layout = QVBoxLayout(self)

        # ── Header informativo ────────────────────────────────────────
        info = QLabel(
            f"<b>{len(resultados)} series</b> comparadas vs cronología "
            f"<b>'{nombre_crono}'</b>. "
            f"Filas ordenadas por <b>puntaje compuesto</b> descendente.<br>"
            "<span style='color:#aaa; font-size:11px;'>"
            "🟡 Resaltado amarillo = mejor desfase ≠ 0 con puntaje "
            "significativo. Para series fechadas indica posible error de "
            "datación; para flotantes indica la posición candidata. "
            "<b>Doble clic</b> en una fila para ver todos los desfases "
            "de esa serie."
            "</span>"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # ── Tabla ─────────────────────────────────────────────────────
        # 8 columnas: Serie, n, Mejor desfase, Puntaje, r, GLK, t-BP, Δ vs 0
        self.tabla = QTableWidget(len(resultados), 8)
        self.tabla.setHorizontalHeaderLabels([
            "Serie", "n", "Mejor desfase", "Puntaje",
            "r", "GLK", "t-BP", "Δ vs desfase 0",
        ])
        self.tabla.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.tabla.horizontalHeader().setStretchLastSection(False)
        self.tabla.setSortingEnabled(False)
        self.tabla.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self.tabla.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.tabla.itemDoubleClicked.connect(self._abrir_detalle_serie)

        # Ordenar resultados por score descendente para el llenado inicial.
        # (después setSortingEnabled(True) permitirá reordenar por cualquier
        # columna haciendo clic en el header)
        resultados_ord = sorted(
            resultados,
            key=lambda r: r.get("mejor_score") if np.isfinite(
                r.get("mejor_score", float("nan"))) else -1e9,
            reverse=True,
        )

        # Guardar referencia ordenada para resolver el doble clic
        self._resultados_ordenados = resultados_ord

        for row, res in enumerate(resultados_ord):
            self._llenar_fila(row, res)

        # Activar sorting DESPUÉS de llenar (orden inicial preservado)
        self.tabla.setSortingEnabled(True)
        layout.addWidget(self.tabla)

        # ── Botones ───────────────────────────────────────────────────
        botonera = QHBoxLayout()

        btn_copiar = QPushButton("📋 Copiar todo")
        btn_copiar.clicked.connect(self._copiar_portapapeles)
        botonera.addWidget(btn_copiar)

        btn_exportar = QPushButton("💾 Exportar CSV")
        btn_exportar.clicked.connect(self._exportar_csv)
        botonera.addWidget(btn_exportar)

        botonera.addStretch()

        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.clicked.connect(self.accept)
        botonera.addWidget(btn_cerrar)
        layout.addLayout(botonera)

    # ── Construcción de filas ─────────────────────────────────────────

    def _llenar_fila(self, row: int, res: dict):
        """Llena una fila de la tabla con los datos de UNA serie.

        Aplica colores por umbral en cada estadístico y, si corresponde,
        resalta toda la fila en amarillo (mejor_shift ≠ 0 con score
        significativo).
        """
        nombre = res.get("nombre_serie", "?")
        mejor_shift = res.get("mejor_shift", 0)
        mejor_score = res.get("mejor_score", float("nan"))
        mejor_r = res.get("mejor_r", float("nan"))
        mejor_glk = res.get("mejor_glk", float("nan"))
        mejor_tbp = res.get("mejor_tbp", float("nan"))
        mejor_n = res.get("mejor_n", 0)
        score_shift0 = res.get("score_shift0", float("nan"))

        # Δ vs shift=0: cuánto mejor es el mejor shift comparado con quedarse
        # en 0. Para fechadas correctas debería ser ~0 (porque shift=0 ya es
        # el mejor). Para incorrectas o flotantes, será > 0.
        if np.isfinite(mejor_score) and np.isfinite(score_shift0):
            delta = mejor_score - score_shift0
        else:
            delta = float("nan")

        # ── Columna 0: Serie ──
        it = QTableWidgetItem(str(nombre))
        # Guardar referencia al resultado completo para resolver doble clic
        # incluso después de reordenar por otra columna.
        it.setData(Qt.ItemDataRole.UserRole, id(res))
        self.tabla.setItem(row, 0, it)

        # ── Columna 1: n ──
        it = QTableWidgetItem()
        it.setData(Qt.ItemDataRole.DisplayRole, int(mejor_n))
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tabla.setItem(row, 1, it)

        # ── Columna 2: Mejor shift ──
        it = QTableWidgetItem()
        it.setData(Qt.ItemDataRole.DisplayRole, int(mejor_shift))
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        # Resaltar visualmente si el shift es ≠ 0 con score significativo
        resaltar_fila = (
            mejor_shift != 0
            and np.isfinite(mejor_score)
            and mejor_score >= self.UMBRAL_SCORE_PARA_RESALTAR
        )
        if resaltar_fila:
            # Texto en negrita para llamar la atención sobre el shift
            f = it.font(); f.setBold(True); it.setFont(f)
        self.tabla.setItem(row, 2, it)

        # ── Columna 3: Score compuesto ──
        it = QTableWidgetItem()
        it.setData(Qt.ItemDataRole.DisplayRole,
                    float(round(mejor_score, 3))
                    if np.isfinite(mejor_score) else 0.0)
        it.setBackground(QColor(_color_compuesto(mejor_score)))
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tabla.setItem(row, 3, it)

        # ── Columna 4: r ──
        it = QTableWidgetItem()
        it.setData(Qt.ItemDataRole.DisplayRole,
                    float(round(mejor_r, 3))
                    if np.isfinite(mejor_r) else 0.0)
        it.setBackground(QColor(_color_r(mejor_r)))
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tabla.setItem(row, 4, it)

        # ── Columna 5: GLK ──
        it = QTableWidgetItem()
        it.setData(Qt.ItemDataRole.DisplayRole,
                    float(round(mejor_glk, 3))
                    if np.isfinite(mejor_glk) else 0.0)
        it.setBackground(QColor(_color_glk(mejor_glk)))
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tabla.setItem(row, 5, it)

        # ── Columna 6: t-BP ──
        it = QTableWidgetItem()
        it.setData(Qt.ItemDataRole.DisplayRole,
                    float(round(mejor_tbp, 2))
                    if np.isfinite(mejor_tbp) else 0.0)
        it.setBackground(QColor(_color_tbp(mejor_tbp)))
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tabla.setItem(row, 6, it)

        # ── Columna 7: Δ vs shift=0 ──
        it = QTableWidgetItem()
        if np.isfinite(delta):
            it.setData(Qt.ItemDataRole.DisplayRole, float(round(delta, 3)))
        else:
            it.setData(Qt.ItemDataRole.DisplayRole, 0.0)
            it.setText("—")
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tabla.setItem(row, 7, it)

        # Resaltado de fila completa (solo color de fondo "sutil" en las
        # columnas que no tienen ya color por umbral: 0, 1, 2, 7).
        # Usamos un amarillo más oscuro (mostaza) que tiene buen contraste
        # con texto BLANCO (tema oscuro) y mejor que el amarillo claro
        # original que se confundía con letras blancas. Para garantizar
        # legibilidad en tema claro Y oscuro, forzamos el color del texto
        # a negro en las celdas resaltadas: negro sobre mostaza siempre
        # se ve bien.
        if resaltar_fila:
            color_fondo = QColor(218, 165, 32)        # mostaza (goldenrod)
            color_texto = QColor(20, 20, 20)          # casi negro
            for col in (0, 1, 2, 7):
                cell = self.tabla.item(row, col)
                if cell is not None:
                    cell.setBackground(color_fondo)
                    cell.setForeground(color_texto)

    # ── Doble clic: drill-down ────────────────────────────────────────

    def _abrir_detalle_serie(self, item: QTableWidgetItem):
        """Doble clic en una fila → abre VentanaTablaOffset con todos los
        desfases de esa serie.

        Como las filas se pueden reordenar por cualquier columna usando
        el header, NO se puede usar `item.row()` para mapear al resultado
        original. En su lugar, guardamos el `id(res)` en UserRole de la
        columna 0 y buscamos el dict por identidad de objeto en
        `_resultados_originales`. Es a prueba de cualquier sort.
        """
        row = item.row()
        cell_nombre = self.tabla.item(row, 0)
        if cell_nombre is None:
            return
        id_buscado = cell_nombre.data(Qt.ItemDataRole.UserRole)
        if id_buscado is None:
            return
        res = next(
            (r for r in self._resultados_originales if id(r) == id_buscado),
            None,
        )
        if res is None:
            return

        dlg = VentanaTablaOffset(
            res["shifts"], res["rs"],
            glks=res["glks"], tbps=res["tbps"], ns=res["ns"],
            # Pasar las series transformadas si están disponibles para
            # que el diálogo dibuje el gráfico apropiado al tipo de
            # serie (barras si flotante, correlación móvil si fechada).
            serie_a=res.get("serie_a"),
            serie_b=res.get("serie_b"),
            parent=self,
        )
        dlg.setWindowTitle(
            f"Desfases detallados — '{res.get('nombre_serie', '?')}' "
            f"vs cronología"
        )
        dlg.exec()

    # ── Exportación ───────────────────────────────────────────────────

    def _copiar_portapapeles(self):
        encabezado = "Serie\tn\tMejor desfase\tPuntaje\tr\tGLK\tt-BP\tDelta_vs_desfase_0\n"
        filas = []
        for row in range(self.tabla.rowCount()):
            cells = [self.tabla.item(row, c) for c in range(8)]
            filas.append("\t".join(
                c.text() if c is not None else "" for c in cells))
        QApplication.clipboard().setText(encabezado + "\n".join(filas))

    def _exportar_csv(self):
        ruta_default = os.path.join(
            _ultima_carpeta(), "comparacion_multiple.csv")
        ruta, _ = QFileDialog.getSaveFileName(
            self, "Exportar resumen", ruta_default, "CSV (*.csv)")
        if not ruta:
            return
        _ultima_carpeta(ruta)
        try:
            with open(ruta, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Serie", "n", "Mejor desfase", "Puntaje",
                    "r", "GLK", "t-BP", "Delta_vs_desfase_0",
                ])
                for row in range(self.tabla.rowCount()):
                    cells = [self.tabla.item(row, c) for c in range(8)]
                    writer.writerow([
                        c.text() if c is not None else "" for c in cells])
            QMessageBox.information(self, "Éxito", "Resumen exportado.")
        except Exception as e:
            QMessageBox.critical(self, "Error",
                                  f"No se pudo guardar:\n{e}")


# =============================================================================
# QSPINBOX CON FLECHAS UNICODE SIEMPRE VISIBLES
# =============================================================================

class SpinBoxFlechas(QSpinBox):
    """QSpinBox usando las flechas nativas del sistema.

    Históricamente esta clase reemplazaba las flechas del sistema con
    QToolButton custom dibujando caracteres Unicode (▲/▼), porque con
    un stylesheet global pesado las flechas nativas quedaban invisibles.
    Eso ya no aplica: con el stylesheet limpio actual (estilos.py), las
    flechas nativas se renderizan correctamente en ambos temas.

    Se mantiene la clase (en vez de usar QSpinBox directamente) para que
    sea drop-in replacement sin tocar 8 sitios, y por si en el futuro
    queremos volver a personalizar.
    """
    pass


# =============================================================================
# WIDGET DE SECCIÓN COLAPSABLE (ACCORDION)
# =============================================================================

class SeccionColapsable(QWidget):
    """Sección colapsable estilo accordion.

    Click en el header para expandir/contraer. Permite que múltiples secciones
    estén abiertas a la vez. Cuando todas están abiertas y exceden el alto
    disponible, el QScrollArea padre activa el scroll vertical.

    Estilo: usa overlays translúcidos rgba(0,0,0,XX) para que funcione tanto
    en tema oscuro como claro, igual que el patrón del módulo de medición.
    """

    def __init__(self, titulo: str, expandida: bool = True, parent=None):
        super().__init__(parent)
        self._titulo = titulo
        self._expandida = expandida

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.btn_toggle = QPushButton()
        self.btn_toggle.setStyleSheet("""
            QPushButton {
                text-align: left;
                padding: 7px 10px;
                background-color: rgba(0, 0, 0, 50);
                font-weight: bold;
                font-size: 11px;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: rgba(255, 140, 0, 60);
            }
            QPushButton:pressed {
                background-color: rgba(0, 0, 0, 80);
            }
        """)
        self.btn_toggle.clicked.connect(self.toggle)
        layout.addWidget(self.btn_toggle)

        # Contenedor sin background propio. La indentación visual viene de
        # los márgenes internos, no de un color de fondo (que rompería el
        # tema claro).
        self._contenido = QWidget()
        self._contenido_layout = QVBoxLayout(self._contenido)
        self._contenido_layout.setContentsMargins(10, 6, 4, 6)
        self._contenido_layout.setSpacing(4)
        layout.addWidget(self._contenido)

        self._actualizar()

    def _actualizar(self):
        flecha = "▼" if self._expandida else "▶"
        self.btn_toggle.setText(f"  {flecha}  {self._titulo}")
        self._contenido.setVisible(self._expandida)

    def toggle(self):
        self._expandida = not self._expandida
        self._actualizar()

    def expandir(self):
        if not self._expandida:
            self.toggle()

    def contraer(self):
        if self._expandida:
            self.toggle()

    def agregar_widget(self, widget):
        self._contenido_layout.addWidget(widget)

    def agregar_layout(self, layout):
        self._contenido_layout.addLayout(layout)


# =============================================================================
# PANEL DE CO-DATACIÓN
# =============================================================================

class PanelCodatacion(QWidget):
    """Panel principal de co-datación visual."""

    def __init__(self, ventana_padre: QWidget):
        super().__init__()
        self._ventana_padre = ventana_padre

        self.series_originales: dict[str, pd.DataFrame] = {}
        self.series_datos: dict[str, pd.DataFrame] = {}

        # Datos del análisis de offset (analizar_offset)
        self._shifts_actuales: list[int] = []
        self._r_actuales: list[float] = []
        self._glk_actuales: list[float] = []
        self._tbp_actuales: list[float] = []
        self._n_actuales: list[int] = []

        # Datos de la correlación móvil (verificar_datacion / buscar_errores)
        self._stats_movil_years: list[float] = []
        self._stats_movil_r: list[float] = []
        self._stats_movil_glk: list[float] = []
        self._stats_movil_tbp: list[float] = []
        self._stats_movil_n: list[int] = []
        self._mitad_ventana_actual: int = 15

        # Estado del motor de sugerencias
        self._sugerencias: list[dict] = []
        self._sugerencias_es_marginal: bool = False
        self._nombre_referencia: str = ""
        self._nombre_problema: str = ""
        self._item_preview = None
        self._stats_actuales: dict | None = None

        # Cronología activa
        self.cronologia_activa: pd.DataFrame | None = None
        self.meta_cronologia: dict | None = None
        self._ventana_cronologia = None
        self._nombre_cronologia_activa: str = ""

        # Parámetros del modo COFECHA
        self._modo_cofecha_params: dict = {
            "rigidez_spline": 32,
            "aplicar_log": True,
            "aplicar_ar": True,
            "aplicar_first_diff": False,
        }

        self._construir_ui()
        self._sincronizar_fondo_con_tema_app()

    # -------------------------------------------------------------------------
    # CONSTRUCCIÓN DE LA INTERFAZ
    # -------------------------------------------------------------------------

    def _construir_ui(self):
        layout_principal = QHBoxLayout(self)
        layout_principal.setContentsMargins(0, 0, 0, 0)

        splitter_principal = QSplitter(Qt.Orientation.Horizontal)

        # ═══════════════════════════════════════════════════════════════
        # PANEL IZQUIERDO: Scroll area con secciones colapsables
        # ═══════════════════════════════════════════════════════════════
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(220)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        widget_izq = QWidget()
        # Guardamos referencia para poder ajustar su fondo según el tema
        # activo (el global de la app no estila QWidget genérico).
        self._widget_izq_panel = widget_izq
        panel_izq = QVBoxLayout(widget_izq)
        panel_izq.setContentsMargins(4, 4, 4, 4)
        panel_izq.setSpacing(6)

        # ── BLOQUE FIJO ARRIBA: Carga y gestión de series ──
        fila_carga = QHBoxLayout()
        self.btn_cargar_rwl = QPushButton("📂 Series")
        self.btn_cargar_rwl.setToolTip("Cargar series de medición")
        self.btn_cargar_rwl.clicked.connect(self.cargar_archivos_rwl)
        fila_carga.addWidget(self.btn_cargar_rwl)
        self.btn_cargar_crono = QPushButton("📚 Crono")
        self.btn_cargar_crono.setToolTip(
            "Cargar una cronología externa.\n"
            "Si el archivo trae varias series, se auto-genera la cronología.")
        self.btn_cargar_crono.setStyleSheet(
            "QPushButton{background-color:#6f42c1; color:white; font-weight:bold;}"
            "QPushButton:hover{background-color:#5a32a3;}")
        self.btn_cargar_crono.clicked.connect(self.cargar_cronologias_externas)
        fila_carga.addWidget(self.btn_cargar_crono)
        panel_izq.addLayout(fila_carga)

        self.btn_toggle_sel = QPushButton("☑ Des / Seleccionar Todo")
        self.btn_toggle_sel.clicked.connect(self.toggle_seleccion)
        panel_izq.addWidget(self.btn_toggle_sel)

        panel_izq.addWidget(QLabel("Series en Memoria:"))
        self.lista_series = QListWidget()
        self.lista_series.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.lista_series.setMinimumHeight(120)
        self.lista_series.itemChanged.connect(self.graficar_series)
        panel_izq.addWidget(self.lista_series)

        QShortcut(QKeySequence(Qt.Key.Key_Delete), self.lista_series).activated.connect(self.eliminar_series)
        QShortcut(QKeySequence(Qt.Key.Key_Backspace), self.lista_series).activated.connect(self.eliminar_series)

        fila_btns = QHBoxLayout()
        btn_elim = QPushButton("🗑️ Eliminar")
        btn_elim.setStyleSheet("background-color: #d9534f; color: white;")
        btn_elim.clicked.connect(self.eliminar_series)
        fila_btns.addWidget(btn_elim)
        btn_anio = QPushButton("📅 Editar Año Base")
        btn_anio.setStyleSheet("background-color: #f0ad4e; color: white; font-weight: bold;")
        btn_anio.clicked.connect(self.editar_anio_serie)
        fila_btns.addWidget(btn_anio)
        panel_izq.addLayout(fila_btns)

        btn_exp = QPushButton("💾 Exportar Marcada (✅)")
        btn_exp.clicked.connect(self.exportar_serie_codatada)
        panel_izq.addWidget(btn_exp)

        # Botón directo de comparación múltiple vs cronología activa.
        # Permite al usuario marcar varias series en la lista y compararlas
        # contra la cronología ya cargada sin tener que abrir la ventana
        # de cronología por separado. La cronología activa se mantiene
        # entre llamadas (en `_cronologia_para_comparacion`), así que
        # típicamente: cargo cronología una vez → comparo decenas de
        # series con dos clics.
        self.btn_multi_vs_crono = QPushButton("📊 Series múltiples vs Crono")
        self.btn_multi_vs_crono.setStyleSheet(
            "QPushButton{background-color:#8e44ad; color:white; "
            "font-weight:bold; padding:5px 10px; border-radius:4px;}"
            "QPushButton:hover{background-color:#9b59b6;}"
            "QPushButton:disabled{background-color:#555; color:#aaa;}"
        )
        self.btn_multi_vs_crono.setToolTip(
            "Comparar las series marcadas contra una cronología.\n"
            "• Si todavía no hay cronología cargada, se pedirá elegir un archivo .rwl\n"
            "• Si ya hay una cargada, se reutiliza automáticamente\n"
            "• Funciona con 1 o más series marcadas\n"
            "• Resultados en tabla resumen con r, GLK, t-BP y puntaje compuesto")
        self.btn_multi_vs_crono.clicked.connect(self.comparar_series_marcadas_vs_crono)
        panel_izq.addWidget(self.btn_multi_vs_crono)

        # ── SECCIÓN COLAPSABLE: Cronología ──
        # Todas las secciones inician COLAPSADAS para no abrumar al usuario
        # con un panel sobrecargado de botones al abrir el programa. El
        # usuario expande la sección que necesita en cada momento.
        sec_crono = SeccionColapsable("🧭 Cronología", expandida=False)
        self.btn_cronologia = QPushButton("🧭 Generar Cronología")
        self.btn_cronologia.setStyleSheet(
            "background-color: #6f42c1; color: white; font-weight: bold;")
        self.btn_cronologia.clicked.connect(self.abrir_ventana_cronologia)
        sec_crono.agregar_widget(self.btn_cronologia)

        self.lbl_info_crono = QLabel("")
        self.lbl_info_crono.setWordWrap(True)
        self.lbl_info_crono.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_info_crono.setStyleSheet(
            "QLabel{font-size:10px; padding:4px 6px; "
            "background-color: rgba(0,0,0,30); border-radius:3px;}")
        self.lbl_info_crono.setVisible(False)
        sec_crono.agregar_widget(self.lbl_info_crono)

        self.btn_eps_crono = QPushButton("📈 EPS / Rbar de cronología")
        self.btn_eps_crono.setStyleSheet(
            "QPushButton{padding:5px 10px; font-size:11px; font-weight:bold; "
            "background-color:#3a6e3a; color:white; border-radius:3px;}"
            "QPushButton:hover{background-color:#4a8e4a;}"
            "QPushButton:disabled{background-color:#444; color:#888;}"
        )
        self.btn_eps_crono.setEnabled(False)
        self.btn_eps_crono.clicked.connect(self._mostrar_eps_cronologia_activa)
        sec_crono.agregar_widget(self.btn_eps_crono)
        panel_izq.addWidget(sec_crono)

        # ── SECCIÓN COLAPSABLE: Cofechado con año conocido ──
        sec_conocido = SeccionColapsable(
            "📊 Cofechado con Año Conocido", expandida=False)

        # Parámetros del motor (compactos lado a lado)
        fila_params = QHBoxLayout()
        fila_params.addWidget(QLabel("Ventana:"))
        self.spin_ventana = SpinBoxFlechas()
        self.spin_ventana.setRange(10, 200)
        self.spin_ventana.setValue(VENTANA_COFECHADO_DEFECTO)
        self.spin_ventana.setSingleStep(10)
        self.spin_ventana.setSuffix(" años")
        fila_params.addWidget(self.spin_ventana)
        fila_params.addWidget(QLabel("Máx corr:"))
        self.spin_max_corr = SpinBoxFlechas()
        self.spin_max_corr.setRange(1, 10)
        self.spin_max_corr.setValue(MAX_CORRECCION)
        fila_params.addWidget(self.spin_max_corr)
        sec_conocido.agregar_layout(fila_params)

        self.btn_ver_cofechado = QPushButton("📊 Ver Cofechado Actual")
        self.btn_ver_cofechado.setStyleSheet("background-color: #5bc0de; color: white;")
        self.btn_ver_cofechado.clicked.connect(self.verificar_datacion)
        sec_conocido.agregar_widget(self.btn_ver_cofechado)

        self.btn_buscar = QPushButton("🔎 Buscar Anillos Faltantes/Sobrantes")
        self.btn_buscar.setStyleSheet("background-color: #5cb85c; color: white; font-weight: bold;")
        self.btn_buscar.clicked.connect(self.buscar_errores)
        sec_conocido.agregar_widget(self.btn_buscar)

        # Modo COFECHA agrupado dentro de esta sección
        grp_cof = QGroupBox("Modo COFECHA")
        grp_cof.setStyleSheet(
            "QGroupBox{border:1px solid #555; border-radius:4px; "
            "margin-top:6px; padding-top:4px;}"
            "QGroupBox::title{subcontrol-origin:margin; left:8px; padding:0 3px;}"
        )
        layout_cof = QVBoxLayout(grp_cof)
        layout_cof.setSpacing(4)
        layout_cof.setContentsMargins(6, 4, 6, 6)

        self.btn_modo_cofecha = QPushButton("🎯 Activar Modo COFECHA")
        self.btn_modo_cofecha.setCheckable(True)
        self.btn_modo_cofecha.setStyleSheet(ESTILO_BTN_COFECHA)
        self.btn_modo_cofecha.setToolTip(
            "Activa transformaciones tipo COFECHA (spline + log + AR(1))\n"
            "con los parámetros configurados (botón ⚙ al lado para ajustar)."
        )

        self.btn_cof_config = QPushButton("⚙")
        self.btn_cof_config.setMaximumWidth(34)
        self.btn_cof_config.setToolTip(
            "Configurar parámetros del Modo COFECHA\n"
            "(rigidez del spline, log, AR(1), primeras diferencias)"
        )
        self.btn_cof_config.setStyleSheet(
            "QPushButton{padding:4px 8px; font-size:14px; font-weight:bold; "
            "border:2px solid #888; border-radius:4px; "
            "background:transparent;}"
            "QPushButton:hover{border-color:#FF8C00; color:#FF8C00;}"
        )
        self.btn_cof_config.clicked.connect(self._configurar_modo_cofecha)

        fila_cof_btn = QHBoxLayout()
        fila_cof_btn.addWidget(self.btn_modo_cofecha, 1)
        fila_cof_btn.addWidget(self.btn_cof_config)
        layout_cof.addLayout(fila_cof_btn)

        fila_aplicar = QHBoxLayout()
        lbl_aplicar = QLabel("Aplicar a:")
        lbl_aplicar.setStyleSheet("font-size:11px;")
        fila_aplicar.addWidget(lbl_aplicar)
        self.chk_cof_serie = QCheckBox("Serie")
        self.chk_cof_serie.setChecked(True)
        self.chk_cof_serie.setStyleSheet(ESTILO_CHK_COFECHA)
        fila_aplicar.addWidget(self.chk_cof_serie)
        self.chk_cof_crono = QCheckBox("Cronología")
        self.chk_cof_crono.setChecked(False)
        self.chk_cof_crono.setStyleSheet(ESTILO_CHK_COFECHA)
        fila_aplicar.addWidget(self.chk_cof_crono)
        fila_aplicar.addStretch()
        layout_cof.addLayout(fila_aplicar)
        sec_conocido.agregar_widget(grp_cof)
        panel_izq.addWidget(sec_conocido)

        # ── SECCIÓN COLAPSABLE: Cofechado serie flotante ──
        sec_flotante = SeccionColapsable(
            "🔍 Cofechado Serie Flotante", expandida=False)

        btn_off = QPushButton("🔍 Analizar Serie Flotante")
        btn_off.setStyleSheet("background-color: #5bc0de; color: white;")
        btn_off.clicked.connect(self.analizar_offset)
        sec_flotante.agregar_widget(btn_off)

        self.btn_ver_tabla_off = QPushButton("📊 Ver tabla de desfases")
        self.btn_ver_tabla_off.setEnabled(False)
        self.btn_ver_tabla_off.clicked.connect(self.mostrar_tabla_offset)
        sec_flotante.agregar_widget(self.btn_ver_tabla_off)

        # Métrica al final, label arriba y combo a todo el ancho
        lbl_met = QLabel("📐 Métrica del gráfico:")
        lbl_met.setStyleSheet("font-size: 11px; padding-top: 4px;")
        sec_flotante.agregar_widget(lbl_met)
        self.combo_metrica_offset = QComboBox()
        self.combo_metrica_offset.addItems([
            "🎯 Compuesto (las tres juntas)",
            "t-BP (Baillie-Pilcher)",
            "r (Pearson)",
            "GLK (Eckstein)",
        ])
        self.combo_metrica_offset.setToolTip(
            "Métrica del gráfico de barras de desfase.\n\n"
            "🎯 Compuesto: media geométrica de las tres métricas.\n"
            "    La fecha verdadera tiene puntaje alto SOLO cuando\n"
            "    las tres son altas simultáneamente.\n\n"
            "t-BP: pondera r por tamaño de muestra.\n"
            "r: engañoso solo — puede dar máximos azarosos.\n"
            "GLK: robusto pero menos sensible que t-BP."
        )
        self.combo_metrica_offset.currentTextChanged.connect(self._redibujar_offset)
        sec_flotante.agregar_widget(self.combo_metrica_offset)

        panel_izq.addWidget(sec_flotante)

        # ── SECCIÓN COLAPSABLE: Simulador manual ──
        sec_sim = SeccionColapsable("📝 Simulador Manual", expandida=False)
        fila = QHBoxLayout()
        fila.addWidget(QLabel("Año:"))
        self.spin_edit_year = SpinBoxFlechas()
        self.spin_edit_year.setRange(-10000, 5000)
        self.spin_edit_year.setValue(1900)
        fila.addWidget(self.spin_edit_year)
        sec_sim.agregar_layout(fila)

        fila2 = QHBoxLayout()
        btn_ins = QPushButton("➕ Insertar")
        btn_ins.setStyleSheet("background-color: #5bc0de; color: white;")
        btn_ins.clicked.connect(self.simular_insertar)
        fila2.addWidget(btn_ins)
        btn_del = QPushButton("➖ Borrar")
        btn_del.setStyleSheet("background-color: #d9534f; color: white;")
        btn_del.clicked.connect(self.simular_borrar)
        fila2.addWidget(btn_del)
        sec_sim.agregar_layout(fila2)
        btn_rst = QPushButton("🔄 Restaurar Original")
        btn_rst.clicked.connect(self.restaurar_serie)
        sec_sim.agregar_widget(btn_rst)
        panel_izq.addWidget(sec_sim)

        # ── SECCIÓN COLAPSABLE: Sugerencias ──
        self.sec_sug = SeccionColapsable("💡 Sugerencias", expandida=False)

        self.lbl_estado_sug = QLabel("")
        self.lbl_estado_sug.setWordWrap(True)
        self.lbl_estado_sug.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_estado_sug.setStyleSheet(
            "QLabel{font-size:10px; padding:4px 6px; "
            "background-color: rgba(0,0,0,30); border-radius:3px;}")
        self.lbl_estado_sug.setVisible(False)
        self.sec_sug.agregar_widget(self.lbl_estado_sug)

        self.tabla_sugerencias = QTableWidget(0, 5)
        self.tabla_sugerencias.setHorizontalHeaderLabels(
            ["Año", "Tipo", "r antes", "r tras", "Δr"])
        self.tabla_sugerencias.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.tabla_sugerencias.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabla_sugerencias.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.tabla_sugerencias.itemSelectionChanged.connect(
            self._on_seleccion_sugerencia)
        self.tabla_sugerencias.setMaximumHeight(200)
        self.tabla_sugerencias.setMinimumHeight(120)
        self.sec_sug.agregar_widget(self.tabla_sugerencias)

        self.btn_aplicar = QPushButton("✓ Aplicar corrección seleccionada")
        self.btn_aplicar.setStyleSheet("background-color: #5cb85c; color: white; font-weight: bold;")
        self.btn_aplicar.setEnabled(False)
        self.btn_aplicar.clicked.connect(self.aplicar_correccion_seleccionada)
        self.sec_sug.agregar_widget(self.btn_aplicar)
        panel_izq.addWidget(self.sec_sug)

        panel_izq.addStretch()
        scroll.setWidget(widget_izq)

        # ═══════════════════════════════════════════════════════════════
        # PANEL DERECHO: Gráficos
        # ═══════════════════════════════════════════════════════════════
        splitter_der = QSplitter(Qt.Orientation.Vertical)

        self.grafico = pg.PlotWidget(title="Series de Ancho de Anillo")
        self.grafico.showGrid(x=True, y=True, alpha=0.3)
        self.grafico.addLegend()
        splitter_der.addWidget(self.grafico)

        contenedor_offset = QWidget()
        layout_offset = QVBoxLayout(contenedor_offset)
        layout_offset.setContentsMargins(0, 0, 0, 0)
        layout_offset.setSpacing(2)

        self.grafico_offset = pg.PlotWidget(title="Correlación Móvil — Panel de Cofechado")
        self.grafico_offset.showGrid(x=True, y=True, alpha=0.3)
        self.grafico_offset.setYRange(-1.05, 1.05)
        self.grafico_offset.addLegend(offset=(10, 10))
        self._texto_hover = pg.TextItem(fill=(40, 40, 40, 220), color=(255, 255, 255))
        self._texto_hover.hide()
        self.grafico_offset.addItem(self._texto_hover)
        self.grafico_offset.scene().sigMouseMoved.connect(self._on_mouse_movido)
        layout_offset.addWidget(self.grafico_offset, 1)

        self.lbl_stats = QLabel("")
        self.lbl_stats.setWordWrap(False)
        self.lbl_stats.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_stats.setMinimumHeight(38)
        # Fondo oscuro fijo: este label es un overlay sobre el gráfico
        # de pyqtgraph (que es negro siempre), y el HTML interno usa
        # colores #888/#aaa/etc que requieren fondo oscuro para verse.
        self.lbl_stats.setStyleSheet(
            "QLabel{background-color:#1a1a1a; color:#e0e0e0; "
            "border:1px solid #444; border-radius:4px; padding:8px 12px;}"
        )
        self.lbl_stats.setAlignment(Qt.AlignmentFlag.AlignVCenter |
                                    Qt.AlignmentFlag.AlignLeft)
        self.lbl_stats.setToolTip(TOOLTIP_STATS)
        self.lbl_stats.setVisible(False)
        layout_offset.addWidget(self.lbl_stats)

        splitter_der.addWidget(contenedor_offset)

        splitter_principal.addWidget(scroll)
        splitter_principal.addWidget(splitter_der)
        splitter_principal.setStretchFactor(0, 1)
        splitter_principal.setStretchFactor(1, 4)
        splitter_principal.setSizes([280, 1120])
        layout_principal.addWidget(splitter_principal)

    # -------------------------------------------------------------------------
    # SINCRONIZACIÓN DE FONDO CON EL TEMA DE LA APP
    # -------------------------------------------------------------------------

    def _sincronizar_fondo_con_tema_app(self):
        """Aplica el fondo correcto al QWidget contenedor del panel según el
        tema activo de la aplicación.

        El stylesheet global de la app (estilos.py) define backgrounds para
        QMainWindow / QDialog / QListView / QSpinBox / etc., pero NO para
        QWidget genérico. Como el contenedor del scroll del panel es un
        QWidget plano, sin esto Qt lo pinta con palette(window) del sistema
        — que en Linux/GTK puede ser gris claro incluso con el tema oscuro
        de la app activo. Resultado: labels con texto blanco quedan invisibles
        sobre fondo gris claro.

        Detectamos el tema inspeccionando el stylesheet global. ESTILO_OSCURO
        contiene "background-color: #2b2b2b" y ESTILO_CLARO contiene
        "background-color: #f0f0f0" — ambos definidos en estilos.py.
        """
        ss_app = QApplication.instance().styleSheet() or ""

        # Marcador robusto: el QMainWindow en ESTILO_OSCURO tiene #2b2b2b,
        # en ESTILO_CLARO tiene #f0f0f0. Si ninguno está, dejamos sin
        # estilizar (Qt usa palette del sistema).
        if "#2b2b2b" in ss_app:
            fondo = "#2b2b2b"
        elif "#f0f0f0" in ss_app:
            fondo = "#f0f0f0"
        else:
            return  # sin stylesheet reconocible, no tocar nada

        if hasattr(self, "_widget_izq_panel"):
            # Solo el QWidget contenedor — los descendientes con estilo propio
            # (QSpinBox, QListWidget, QPushButton, etc.) mantienen sus colores
            # del stylesheet global porque su selector específico vence al
            # genérico QWidget. Los QLabel también: el global tiene
            # "QLabel { background: transparent; }" que vence a este.
            self._widget_izq_panel.setStyleSheet(
                f"QWidget {{ background-color: {fondo}; }}"
            )

    def changeEvent(self, event):
        """Re-sincroniza el fondo del panel en caliente cuando la app
        cambia su stylesheet global (toggle de tema oscuro/claro).

        Cuando el usuario activa el toggle de tema, la app llama a
        QApplication.setStyleSheet(NUEVO_ESTILO). Eso propaga un evento
        QEvent.StyleChange a todos los widgets visibles e invisibles.
        Aprovechamos ese evento para re-aplicar el fondo apropiado al
        contenedor del scroll sin requerir reinicio.
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._sincronizar_fondo_con_tema_app()

    # -------------------------------------------------------------------------
    # CARGA DE ARCHIVOS
    # -------------------------------------------------------------------------

    def cargar_archivos_rwl(self):
        """Carga uno o más archivos, detectando formato automáticamente."""
        rutas, _ = QFileDialog.getOpenFileNames(
            self, "Seleccionar Series", _ultima_carpeta(),
            "Series compatibles (*.rwl *.txt *.wid *.csv *.tsv *.xlsx *.xls *.cat *.cmp);;"
            "Todos (*.*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not rutas:
            return
        _ultima_carpeta(rutas[0])

        n_agregadas = 0
        n_duplicadas = 0
        errores: list[str] = []

        for ruta in rutas:
            ext = os.path.splitext(ruta)[1].lower()
            base_nombre = os.path.splitext(os.path.basename(ruta))[0]

            try:
                with open(ruta, 'r', encoding='utf-8', errors='replace') as f:
                    primeras = f.read(500)

                if "=N" in primeras and "=I" in primeras:
                    series_extraidas = leer_formato_compacto(ruta)
                elif ext == ".wid":
                    df, sid = leer_wid(ruta)
                    series_extraidas = {f"🌲{sid}": df}
                elif ext in (".rwl", ".txt"):
                    primera_real = ""
                    try:
                        with open(ruta, 'r', encoding='utf-8', errors='replace') as f:
                            for linea in f:
                                ln = linea.rstrip("\r\n")
                                if not ln or ln.startswith("#"):
                                    continue
                                primera_real = ln
                                break
                    except Exception:
                        primera_real = ""

                    if "\t" in primera_real:
                        series_extraidas = self._leer_como_columnas_o_tabular(ruta, base_nombre)
                    else:
                        try:
                            series_extraidas = leer_tucson_multi(ruta)
                        except Exception:
                            series_extraidas = self._leer_como_columnas_o_tabular(ruta, base_nombre)
                elif ext in (".csv", ".tsv", ".xlsx", ".xls"):
                    series_extraidas = self._leer_como_columnas_o_tabular(ruta, base_nombre)
                else:
                    df, sid = leer_tabular(ruta)
                    series_extraidas = {sid: df}

                for id_serie, df_serie in series_extraidas.items():
                    if id_serie in self.series_datos:
                        n_duplicadas += 1
                        continue
                    self.series_datos[id_serie] = df_serie.copy()
                    self.series_originales[id_serie] = df_serie.copy()
                    item = QListWidgetItem(id_serie)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Unchecked)
                    self.lista_series.addItem(item)
                    n_agregadas += 1

            except (ValueError, OSError) as exc:
                logger.warning("Error al cargar %s: %s", ruta, exc)
                errores.append(f"{os.path.basename(ruta)}: {exc}")
                continue

        self.graficar_series()

        if n_agregadas == 0 and not errores:
            QMessageBox.information(
                self, "Sin novedades",
                f"Las {n_duplicadas} series del archivo ya estaban cargadas.")
        elif errores:
            msg = f"Se agregaron {n_agregadas} serie(s)."
            if n_duplicadas:
                msg += f"\n{n_duplicadas} duplicada(s) omitida(s)."
            msg += "\n\nErrores:\n" + "\n".join(errores)
            QMessageBox.warning(self, "Carga con errores", msg)

    def _leer_como_columnas_o_tabular(self, ruta: str, base_nombre: str) -> dict:
        """Si el archivo tiene múltiples columnas de valor, cada una como serie separada."""
        try:
            df_multi = leer_columna_multi(ruta)
        except Exception:
            df, sid = leer_tabular(ruta)
            return {sid: df}

        cols = list(df_multi.columns)
        if len(cols) == 0:
            df, sid = leer_tabular(ruta)
            return {sid: df}

        if len(cols) == 1:
            col = cols[0]
            df_serie = pd.DataFrame(
                {"Ancho_mm": pd.to_numeric(df_multi[col], errors="coerce")},
                index=df_multi.index,
            ).dropna()
            return {base_nombre: df_serie}

        resultado = {}
        for col in cols:
            df_serie = pd.DataFrame(
                {"Ancho_mm": pd.to_numeric(df_multi[col], errors="coerce")},
                index=df_multi.index,
            ).dropna()
            if df_serie.empty:
                continue
            nombre = f"{base_nombre}_{col}"
            resultado[nombre] = df_serie
        return resultado

    def cargar_cronologias_externas(self):
        """Carga cronologías externas con prefijo 📚."""
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Cargar cronología(s) externa(s)", _ultima_carpeta(),
            "Cronologías y series (*.rwl *.txt *.crn *.wid *.csv *.tsv "
            "*.xlsx *.xls *.cat *.cmp);;Todos (*.*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not ruta:
            return
        _ultima_carpeta(ruta)

        ext = os.path.splitext(ruta)[1].lower()
        try:
            # Detectar formato compacto
            with open(ruta, 'r', encoding='utf-8', errors='replace') as f:
                primeras = f.read(500)
            if "=N" in primeras and "=I" in primeras:
                series = leer_formato_compacto(ruta)
                self._procesar_tucson_como_crono(series, ruta)
                return

            # WID directo
            if ext == ".wid":
                df, sid = leer_wid(ruta)
                self._agregar_crono(f"📚 {sid}", df, ruta)
                return

            # Tucson (.rwl/.txt SIN tabs)
            if ext in (".rwl", ".txt"):
                primera_real = ""
                try:
                    with open(ruta, 'r', encoding='utf-8', errors='replace') as f:
                        for linea in f:
                            ln = linea.rstrip("\r\n")
                            if not ln or ln.startswith("#"):
                                continue
                            primera_real = ln
                            break
                except Exception:
                    primera_real = ""
                if "\t" not in primera_real:
                    try:
                        series = leer_tucson_multi(ruta)
                        if series:
                            self._procesar_tucson_como_crono(series, ruta)
                            return
                    except Exception:
                        pass

            # Multi-columna o tabular
            try:
                df_multi = leer_columna_multi(ruta)
                base_nombre = os.path.splitext(os.path.basename(ruta))[0]

                # Detectar columnas auxiliares y descartarlas.
                # Una cronología exportada típicamente trae el VALOR del
                # índice + columnas auxiliares (tamaño de muestra, EPS,
                # Rbar, desviación estándar, etc). Antes cada columna se
                # cargaba como una cronología separada, por eso el usuario
                # veía aparecer 2 o más cronologías al abrir UN archivo.
                # Acá filtramos por nombre los auxiliares conocidos para
                # quedarnos solo con las columnas que son valores de
                # cronología real.
                AUX_COLUMNAS = {
                    # Tamaño de muestra
                    "n", "n_series", "n_samples", "sample_size", "samples",
                    "depth", "count", "cantidad",
                    # Dispersión
                    "sd", "std", "stdev", "stderr", "se", "desv_std",
                    "desviacion", "desviación",
                    # Estadísticos de calidad
                    "rbar", "r_bar", "eps", "snr",
                    # Año (debería estar como índice pero por si acaso)
                    "anio", "año", "year",
                }
                cols_validas = [
                    c for c in df_multi.columns
                    if str(c).strip().lower() not in AUX_COLUMNAS
                ]
                # Si el filtro quitó todo (raro), no aplicamos el filtro
                if not cols_validas:
                    cols_validas = list(df_multi.columns)

                for col in cols_validas:
                    df_serie = pd.DataFrame(
                        {"Ancho_mm": pd.to_numeric(df_multi[col], errors="coerce")},
                        index=df_multi.index,
                    ).dropna()
                    if df_serie.empty:
                        continue
                    nombre = (f"📚 {base_nombre}_{col}"
                              if len(cols_validas) > 1
                              else f"📚 {base_nombre}")
                    self._agregar_crono(nombre, df_serie, ruta)
                return
            except Exception:
                pass

            df, sid = leer_tabular(ruta)
            self._agregar_crono(f"📚 {sid}", df, ruta)

        except Exception as exc:
            QMessageBox.warning(self, "Error", f"No se pudo cargar la cronología:\n{exc}")

        self.graficar_series()

    def _procesar_tucson_como_crono(self, series: dict, ruta: str):
        """Procesa series Tucson de un archivo:
        • 1 serie → la carga directamente como 📚
        • N series → auto-genera cronología (residual+spline+biweight), la
          activa como 📚 y NO carga las series individuales
        """
        if not series:
            return

        if len(series) == 1:
            sid, df = next(iter(series.items()))
            self._agregar_crono(f"📚 {sid}", df, ruta)
            self.graficar_series()
            return

        # Múltiples series → auto-cronología con diálogo de progreso
        nombres = list(series.keys())

        progreso = QProgressDialog(
            f"Generando cronología desde {len(nombres)} series...",
            None, 0, 100, self
        )
        progreso.setWindowTitle("Auto-Cronología")
        progreso.setWindowModality(Qt.WindowModality.WindowModal)
        progreso.setMinimumDuration(0)
        progreso.setCancelButton(None)  # no cancelable, evita estados inconsistentes
        progreso.setValue(5)
        QApplication.processEvents()

        try:
            progreso.setLabelText("Estandarizando series (spline + AR removal)...")
            QApplication.processEvents()
            df_crono, meta = _construir_cronologia_desde_series(
                series, nombres,
                tipo_cronologia="residual",
                metodo_estandarizacion="spline",
                agregacion="biweight",
            )
            progreso.setValue(30)
            QApplication.processEvents()
        except Exception as exc:
            progreso.close()
            QMessageBox.warning(
                self, "Error al generar cronología",
                f"No se pudo generar cronología automática:\n{exc}\n\n"
                "Las series individuales no fueron cargadas."
            )
            return

        nombre_base = os.path.splitext(os.path.basename(ruta))[0]
        nombre = f"{nombre_base}_auto"
        meta["nombre"] = nombre

        # Rbar y EPS ya vienen calculados desde dentro de
        # _construir_cronologia_desde_series sobre las series DETRENDADAS
        # (comparable con ARSTAN "all possible series rbar"). Antes acá
        # se sobrescribían usando series raw, lo que inflaba el Rbar.
        progreso.setValue(50)
        QApplication.processEvents()

        # EPS / Rbar móvil con callback
        progreso.setLabelText("Calculando EPS y Rbar móvil...")
        QApplication.processEvents()

        def _cb_progreso(pct):
            # mapear 0..100 a 50..95
            progreso.setValue(50 + int(pct * 0.45))
            QApplication.processEvents()

        try:
            eps_df = calcular_eps_rbar_movil(
                series, nombres, window=15,
                paso=max(1, len(df_crono) // 200),
                progreso=_cb_progreso,
            )
            if not eps_df.empty:
                meta["EPS_movil"] = eps_df["EPS"].dropna().to_dict()
                meta["Rbar_movil"] = eps_df["Rbar"].dropna().to_dict()
        except Exception as exc:
            logger.warning("EPS/Rbar móvil falló: %s", exc)

        progreso.setLabelText("Activando cronología...")
        progreso.setValue(95)
        QApplication.processEvents()

        # Activar como cronología
        self.set_cronologia_activa(df_crono, meta, nombre)
        progreso.setValue(100)
        progreso.close()

        QMessageBox.information(
            self, "✅ Cronología generada automáticamente",
            f"Se generó la cronología '{nombre}' a partir de "
            f"{len(nombres)} serie(s) del archivo.\n\n"
            f"  • Años: {len(df_crono)}\n"
            f"  • Tipo: residual (spline 32 años + AR removal)\n"
            f"  • Agregación: biweight\n"
            f"  • Rbar: {rbar:.3f}    EPS: {eps_val:.3f}\n\n"
            f"La cronología aparece en la lista con 📚 y está lista para "
            f"cofechado.\n\n"
            f"Si querés ajustar parámetros (tipo, método, estandarización), "
            f"usá el botón 🧭 Cronología."
        )

    def _agregar_crono(self, nombre: str, df: pd.DataFrame, ruta: str):
        """Agrega una cronología 📚 a la lista y la marca como activa.

        La cronología recién cargada queda como `cronologia_activa` para que
        el botón "Series múltiples vs Crono" la encuentre disponible
        inmediatamente. Si ya había una activa anterior, la sobrescribe (el
        usuario puede tener varias en la lista pero solo una "activa" para
        comparaciones).

        Meta mínima: tipo "raw" porque las cronologías cargadas externamente
        ya vienen en su forma final (no se les aplica más transformación).
        """
        if nombre in self.series_datos:
            return
        self.series_datos[nombre] = df.copy()
        self.series_originales[nombre] = df.copy()
        item = QListWidgetItem(nombre)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)
        self.lista_series.addItem(item)

        # Marcar automáticamente como cronología activa SIN volver a
        # agregarla a la lista (ya la agregamos arriba). El flag
        # agregar_a_lista=False evita la duplicación: antes la cronología
        # aparecía dos veces (una por este método y otra porque
        # set_cronologia_activa también la agregaba).
        meta = {
            "tipo_cronologia": "raw",
            "columna_valor": "Ancho_mm",
            "metodo_estandarizacion": "spline",
        }
        nombre_limpio = nombre.replace("📚 ", "").strip()
        self.set_cronologia_activa(df, meta, nombre_limpio, agregar_a_lista=False)


    # -------------------------------------------------------------------------
    # GESTIÓN DE LA LISTA DE SERIES
    # -------------------------------------------------------------------------

    def _series_marcadas(self) -> list[str]:
        return [
            self.lista_series.item(i).text()
            for i in range(self.lista_series.count())
            if self.lista_series.item(i).checkState() == Qt.CheckState.Checked
        ]

    def toggle_seleccion(self):
        todas = all(
            self.lista_series.item(i).checkState() == Qt.CheckState.Checked
            for i in range(self.lista_series.count())
        )
        estado = Qt.CheckState.Unchecked if todas else Qt.CheckState.Checked
        self.lista_series.blockSignals(True)
        for i in range(self.lista_series.count()):
            self.lista_series.item(i).setCheckState(estado)
        self.lista_series.blockSignals(False)
        self.graficar_series()

    def eliminar_series(self):
        """Elimina las series seleccionadas (múltiples permitidas)."""
        items = self.lista_series.selectedItems()
        if not items:
            return

        nombres_a_eliminar = [item.text() for item in items]
        nombres_set = set(nombres_a_eliminar)

        for nombre in nombres_a_eliminar:
            self.series_datos.pop(nombre, None)
            self.series_originales.pop(nombre, None)

        for i in range(self.lista_series.count() - 1, -1, -1):
            if self.lista_series.item(i).text() in nombres_set:
                self.lista_series.takeItem(i)

        if self._nombre_referencia in nombres_set:
            self._nombre_referencia = ""
        if self._nombre_problema in nombres_set:
            self._nombre_problema = ""

        if (self._nombre_cronologia_activa and
                f"📚 {self._nombre_cronologia_activa}" in nombres_set):
            self.cronologia_activa = None
            self.meta_cronologia = None
            self._nombre_cronologia_activa = ""
            self.lbl_info_crono.setVisible(False)
            self.btn_eps_crono.setEnabled(False)

        self.graficar_series()

        necesita_limpiar = (
            not self._nombre_referencia or not self._nombre_problema or
            self._nombre_referencia not in self.series_datos or
            self._nombre_problema not in self.series_datos
        )
        if necesita_limpiar:
            self.grafico_offset.clear()
            self._shifts_actuales.clear()
            self._r_actuales.clear()
            self._glk_actuales.clear()
            self._tbp_actuales.clear()
            self._n_actuales.clear()
            self._stats_movil_years.clear()
            self._stats_movil_r.clear()
            self._stats_movil_glk.clear()
            self._stats_movil_tbp.clear()
            self._stats_movil_n.clear()
            self._sugerencias = []
            self.tabla_sugerencias.setRowCount(0)
            self.btn_aplicar.setEnabled(False)
            self.lbl_stats.setVisible(False)
            self.btn_ver_tabla_off.setEnabled(False)

    def editar_anio_serie(self):
        """Cambia el año base de la serie seleccionada.

        Muestra un diálogo con DOS spinboxes:
          • Año del primer anillo (MÉDULA) ← campo principal, foco por defecto
          • Año del último anillo (CORTEZA)
        Al cambiar uno, el otro se ajusta automáticamente. El usuario edita
        el que tenga datos confiables (típicamente médula para series flotantes,
        corteza para muestras con corteza preservada).
        """
        items = self.lista_series.selectedItems()
        if not items:
            QMessageBox.warning(self, "Atención",
                                "Seleccioná una serie (click sobre su nombre).")
            return
        if len(items) > 1:
            QMessageBox.warning(self, "Atención", "Editá una sola serie a la vez.")
            return

        nombre = items[0].text()
        df = self.series_datos.get(nombre)
        if df is None or df.empty:
            return

        n_anios = len(df)
        anio_medula_actual = int(df.index.min())
        anio_corteza_actual = int(df.index.max())

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Editar año base — {nombre}")
        dlg.setMinimumWidth(440)
        v = QVBoxLayout(dlg)

        info = QLabel(
            f"<b>Serie:</b> {nombre}<br>"
            f"<b>Rango actual:</b> {anio_medula_actual} → {anio_corteza_actual} "
            f"<span style='color:#888;'>({n_anios} años)</span>"
        )
        v.addWidget(info)

        hint = QLabel(
            "<span style='color:#aaa; font-size:11px;'>"
            "Editá el campo que conozcas con certeza. El otro se "
            "ajusta automáticamente. Para series flotantes lo habitual "
            "es anclar el primer año (médula); para muestras con corteza "
            "preservada se ancla el último año."
            "</span>"
        )
        hint.setWordWrap(True)
        v.addWidget(hint)

        form = QFormLayout()
        spin_medula = SpinBoxFlechas()
        spin_medula.setRange(-10000, 5000)
        spin_medula.setValue(anio_medula_actual)
        spin_medula.setStyleSheet("QSpinBox{font-weight:bold; color:#5cb85c;}")
        form.addRow("🌱 <b>Primer año (MÉDULA):</b>", spin_medula)

        spin_corteza = SpinBoxFlechas()
        spin_corteza.setRange(-10000, 5000)
        spin_corteza.setValue(anio_corteza_actual)
        spin_corteza.setStyleSheet("QSpinBox{color:#f0ad4e;}")
        form.addRow("🪵 Último año (CORTEZA):", spin_corteza)
        v.addLayout(form)

        # Vinculación: cuando cambia uno, el otro se ajusta
        _bloqueado = [False]

        def _sync_desde_medula():
            if _bloqueado[0]:
                return
            _bloqueado[0] = True
            spin_corteza.setValue(spin_medula.value() + n_anios - 1)
            _bloqueado[0] = False

        def _sync_desde_corteza():
            if _bloqueado[0]:
                return
            _bloqueado[0] = True
            spin_medula.setValue(spin_corteza.value() - n_anios + 1)
            _bloqueado[0] = False

        spin_medula.valueChanged.connect(_sync_desde_medula)
        spin_corteza.valueChanged.connect(_sync_desde_corteza)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        v.addWidget(btns)

        # Foco en MÉDULA por default
        spin_medula.setFocus()
        spin_medula.selectAll()

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        nuevo_medula = spin_medula.value()
        delta = nuevo_medula - anio_medula_actual
        if delta == 0:
            return

        nuevo_idx = df.index + delta
        df_nuevo = df.copy()
        df_nuevo.index = nuevo_idx
        df_nuevo.sort_index(inplace=True)

        self.series_datos[nombre] = df_nuevo
        self.series_originales[nombre] = df_nuevo.copy()

        self.graficar_series()
        QMessageBox.information(
            self, "Año actualizado",
            f"Serie '{nombre}' desplazada {delta:+d} años.\n"
            f"Ahora va de {int(df_nuevo.index.min())} a {int(df_nuevo.index.max())}."
        )

    def exportar_serie_codatada(self):
        """Exporta la serie marcada con formato según extensión."""
        marcadas = self._series_marcadas()
        if not marcadas:
            QMessageBox.warning(self, "Atención", "Marque con ✅ la serie a exportar.")
            return
        if len(marcadas) > 1:
            QMessageBox.warning(self, "Atención", "Exporte de a una serie a la vez.")
            return

        nombre = marcadas[0]
        df = self.series_datos[nombre]

        # Cronologías → formato 2-columnas con coma decimal
        if nombre.startswith("📚 "):
            nombre_limpio = nombre.replace("📚 ", "").strip()
            ruta_default = os.path.join(_ultima_carpeta(), f"{nombre_limpio}.txt")
            ruta, _ = QFileDialog.getSaveFileName(
                self, "Exportar cronología", ruta_default,
                "Texto separado por tabulaciones (*.txt);;"
                "CSV separado por punto y coma (*.csv);;"
                "Excel (*.xlsx)",
                options=QFileDialog.Option.DontUseNativeDialog,
            )
            if not ruta:
                return
            _ultima_carpeta(ruta)
            ext = os.path.splitext(ruta)[1].lower()
            try:
                df_export = pd.DataFrame({
                    "Anio": df.index.astype(int),
                    "Valor": pd.to_numeric(df["Ancho_mm"], errors="coerce").values,
                }).dropna()

                if ext == ".xlsx":
                    df_export.to_excel(ruta, index=False)
                elif ext == ".csv":
                    df_export.to_csv(ruta, index=False, sep=";", decimal=",", float_format="%.6f")
                else:
                    df_export.to_csv(ruta, index=False, sep="\t", decimal=",", float_format="%.6f")
                QMessageBox.information(
                    self, "✅ Cronología exportada",
                    f"Archivo guardado:\n{ruta}\n\n"
                    f"Formato: 2 columnas (Año, Valor) con coma decimal\n"
                    f"Años: {len(df_export)}")
            except Exception as exc:
                QMessageBox.critical(self, "Error", f"No se pudo guardar:\n{exc}")
            return

        # Series normales → exportador Tucson
        mediciones = [{"anio": int(idx), "ancho_mm": float(val)}
                      for idx, val in df["Ancho_mm"].items()]
        exp = getattr(self._ventana_padre, "exportador", None)
        if exp:
            exp.exportar_datos(nombre, mediciones)
        else:
            QMessageBox.information(self, "Sin exportador",
                                    "No hay un exportador disponible.")

    # -------------------------------------------------------------------------
    # SIMULADOR MANUAL
    # -------------------------------------------------------------------------

    def simular_insertar(self):
        marcadas = self._series_marcadas()
        if len(marcadas) != 1:
            QMessageBox.warning(self, "Atención", "Marque UNA serie para editar.")
            return
        nombre = marcadas[0]
        anio = self.spin_edit_year.value()
        self.series_datos[nombre] = aplicar_correccion(self.series_datos[nombre], anio, 1)
        self.graficar_series()

    def simular_borrar(self):
        marcadas = self._series_marcadas()
        if len(marcadas) != 1:
            QMessageBox.warning(self, "Atención", "Marque UNA serie para editar.")
            return
        nombre = marcadas[0]
        anio = self.spin_edit_year.value()
        self.series_datos[nombre] = aplicar_correccion(self.series_datos[nombre], anio, -1)
        self.graficar_series()

    def restaurar_serie(self):
        marcadas = self._series_marcadas()
        if len(marcadas) != 1:
            QMessageBox.warning(self, "Atención", "Marque UNA serie para restaurar.")
            return
        nombre = marcadas[0]
        if nombre in self.series_originales:
            self.series_datos[nombre] = self.series_originales[nombre].copy()
            self.graficar_series()

    # -------------------------------------------------------------------------
    # VISUALIZACIÓN DE SERIES
    # -------------------------------------------------------------------------

    def graficar_series(self):
        """Redibuja el gráfico principal con las series marcadas."""
        self.grafico.clear()
        graficadas = 0
        for i in range(self.lista_series.count()):
            item = self.lista_series.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            nombre = item.text()
            if nombre not in self.series_datos:
                continue
            df = self.series_datos[nombre]
            color = COLORES_SERIES[graficadas % len(COLORES_SERIES)]
            self.grafico.plot(
                df.index.to_numpy(dtype=float),
                pd.to_numeric(df["Ancho_mm"], errors="coerce").to_numpy(dtype=float),
                pen=pg.mkPen(color, width=2),
                name=nombre,
            )
            graficadas += 1

    # -------------------------------------------------------------------------
    # CRONOLOGÍA ACTIVA
    # -------------------------------------------------------------------------

    def abrir_ventana_cronologia(self):
        """Abre la ventana de cronología."""
        if self._ventana_cronologia is None:
            self._ventana_cronologia = VentanaCronologia(self)
        self._ventana_cronologia.show()
        self._ventana_cronologia.raise_()

    def set_cronologia_activa(self, df: pd.DataFrame, meta: dict, nombre: str,
                               agregar_a_lista: bool = True):
        """Setea la cronología activa para cofechado.

        Parámetro `agregar_a_lista`:
          - True (default): agrega la cronología a la lista de series con
            prefijo 📚 (comportamiento usado cuando se genera una
            cronología desde la VentanaCronologia, que NO la tiene aún
            en la lista).
          - False: NO toca la lista — solo marca la cronología como activa
            y actualiza la etiqueta informativa. Se usa desde
            `_agregar_crono`, que YA agregó la cronología a la lista. Sin
            este flag, la cronología aparecía DUPLICADA: una vez por
            `_agregar_crono` y otra por esta función.
        """
        self.cronologia_activa = df.copy()
        self.meta_cronologia = dict(meta) if meta else {}
        self._nombre_cronologia_activa = nombre or "Cronología"

        if agregar_a_lista:
            # Agregar a la lista como 📚
            nombre_lista = f"📚 {self._nombre_cronologia_activa}"
            col_val = self.meta_cronologia.get("columna_valor", df.columns[0])
            if col_val not in df.columns:
                col_val = df.columns[0]
            df_serie = pd.DataFrame({"Ancho_mm": df[col_val]}, index=df.index)
            df_serie = df_serie.dropna()

            if nombre_lista in self.series_datos:
                self.series_datos[nombre_lista] = df_serie
                self.series_originales[nombre_lista] = df_serie.copy()
            else:
                self.series_datos[nombre_lista] = df_serie
                self.series_originales[nombre_lista] = df_serie.copy()
                item = QListWidgetItem(nombre_lista)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                self.lista_series.addItem(item)

        # Mostrar info de cronología (siempre, sin importar agregar_a_lista)
        n_anios = len(df)
        n_series = self.meta_cronologia.get("n_series", 0)
        rbar = self.meta_cronologia.get("Rbar", float("nan"))
        eps = self.meta_cronologia.get("EPS", float("nan"))
        info = f"📚 <b>{self._nombre_cronologia_activa}</b> — {n_anios} años"
        if n_series:
            info += f", {n_series} series"
        if np.isfinite(rbar):
            info += f", Rbar={rbar:.3f}"
        if np.isfinite(eps):
            info += f", EPS={eps:.3f}"
        self.lbl_info_crono.setText(info)
        self.lbl_info_crono.setVisible(True)

        # Habilitar botón EPS si hay datos móviles
        tiene_eps_movil = bool(self.meta_cronologia.get("EPS_movil"))
        self.btn_eps_crono.setEnabled(tiene_eps_movil)

        self.graficar_series()

    def _mostrar_eps_cronologia_activa(self):
        """Muestra el diálogo de EPS/Rbar móvil de la cronología activa."""
        _mostrar_dialogo_eps_rbar(
            self, self.meta_cronologia or {}, self._nombre_cronologia_activa
        )

    # -------------------------------------------------------------------------
    # MODO COFECHA
    # -------------------------------------------------------------------------

    def _on_modo_cofecha_clicked(self):
        """Toggle del modo COFECHA. Solo cambia el estado visual del botón;
        la configuración de parámetros está en el botón ⚙ al lado."""
        pass

    def _configurar_modo_cofecha(self):
        """Abre el diálogo de configuración del modo COFECHA."""
        dlg = DialogoCofecha(self, self._modo_cofecha_params)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._modo_cofecha_params = dlg.get_params()

    # -------------------------------------------------------------------------
    # TRANSFORMACIÓN PARA CORRELACIÓN
    # -------------------------------------------------------------------------

    def _transformar_para_correlacion(self, serie_mm: pd.Series, es_crono: bool) -> pd.Series:
        """Aplica la transformación apropiada para calcular correlaciones.
        Respeta los checkboxes 'Aplicar a: Serie/Cronología' del modo COFECHA."""
        if self.btn_modo_cofecha.isChecked():
            debe_aplicar = (
                (es_crono and self.chk_cof_crono.isChecked()) or
                (not es_crono and self.chk_cof_serie.isChecked())
            )
            if debe_aplicar:
                return aplicar_modo_cofecha(serie_mm, **self._modo_cofecha_params)
        # Default: diferencias logarítmicas
        return _diff_log(serie_mm)

    def _preparar_diferencias_log(self, n1: str, n2: str):
        """Prepara las series transformadas de un par para cofechado."""
        df1 = self.series_datos[n1]
        df2 = self.series_datos[n2]
        es_crono_1 = n1.startswith("📚")
        es_crono_2 = n2.startswith("📚")
        d1 = self._transformar_para_correlacion(df1["Ancho_mm"], es_crono_1)
        d2 = self._transformar_para_correlacion(df2["Ancho_mm"], es_crono_2)
        return d1, d2, n1, n2

    def _fmt_r(self, r: float) -> str:
        if not np.isfinite(r):
            return "—"
        return f"{r:.3f}"


    # -------------------------------------------------------------------------
    # ANÁLISIS DE OFFSET (BÚSQUEDA DE DESFASE)
    # -------------------------------------------------------------------------

    def analizar_offset(self):
        """Análisis de desfase: r/GLK/t-BP por cada shift posible."""
        marcadas = self._series_marcadas()
        if len(marcadas) != 2:
            return QMessageBox.warning(self, "Atención", "Marque EXACTAMENTE dos series.")

        # Limpiar modo de la curva de cofechado
        self._stats_movil_years = []
        self._stats_movil_r = []
        self._stats_movil_glk = []
        self._stats_movil_tbp = []
        self._stats_movil_n = []

        self.grafico_offset.clear()
        self.grafico_offset.setTitle("Procesando…")
        self.grafico_offset.addItem(self._texto_hover)
        self._texto_hover.hide()

        m_diff, t_diff, n_m, n_t = self._preparar_diferencias_log(marcadas[0], marcadas[1])

        if (len(m_diff) < OVERLAP_MINIMO_ANALISIS or
                len(t_diff) < OVERLAP_MINIMO_ANALISIS):
            return self.grafico_offset.setTitle(
                f"Error: Se requieren >{OVERLAP_MINIMO_ANALISIS} años.")

        serie_a_raw = pd.to_numeric(
            self.series_datos[n_m]["Ancho_mm"], errors="coerce").dropna()
        serie_b_raw = pd.to_numeric(
            self.series_datos[n_t]["Ancho_mm"], errors="coerce").dropna()

        s_min = int(m_diff.index.min() - t_diff.index.max() + OVERLAP_MINIMO_ANALISIS - 1)
        s_max = int(m_diff.index.max() - t_diff.index.min() - OVERLAP_MINIMO_ANALISIS + 1)

        self._shifts_actuales = []
        self._r_actuales = []
        self._glk_actuales = []
        self._tbp_actuales = []
        self._n_actuales = []

        for shift in range(s_min, s_max + 1):
            overlap = m_diff.index.intersection(t_diff.index + shift)
            if len(overlap) < OVERLAP_MINIMO_ANALISIS:
                continue
            r = _r_pearson(m_diff.loc[overlap].values, t_diff.loc[overlap - shift].values)
            if np.isnan(r):
                continue

            # GLK y t-BP sobre RAW desplazado
            b_raw_shift = serie_b_raw.copy()
            b_raw_shift.index = b_raw_shift.index + shift
            overlap_raw = serie_a_raw.index.intersection(b_raw_shift.index)
            n_ov = len(overlap_raw)
            if n_ov >= OVERLAP_MINIMO_ANALISIS:
                a_o = serie_a_raw.loc[overlap_raw]
                b_o = b_raw_shift.loc[overlap_raw]
                glk, _ = gleichlaufigkeit(a_o, b_o, min_overlap=OVERLAP_MINIMO_ANALISIS)
                t_bp, _, _ = t_baillie_pilcher(a_o, b_o, min_overlap=OVERLAP_MINIMO_ANALISIS)
            else:
                glk = float("nan")
                t_bp = float("nan")

            self._shifts_actuales.append(shift)
            self._r_actuales.append(r)
            self._glk_actuales.append(glk)
            self._tbp_actuales.append(t_bp)
            self._n_actuales.append(n_ov)

        if not self._shifts_actuales:
            return self.grafico_offset.setTitle("Sin coincidencias suficientes.")

        self._redibujar_offset()
        self.btn_ver_tabla_off.setEnabled(True)

    def _redibujar_offset(self):
        """Redibuja las barras de offset con la métrica seleccionada en el combo."""
        if not self._shifts_actuales:
            return

        metrica_txt = self.combo_metrica_offset.currentText()

        if metrica_txt.startswith("🎯"):
            vals = [score_compuesto(r, g, t)
                    for r, g, t in zip(self._r_actuales, self._glk_actuales, self._tbp_actuales)]
            metric_name = "Puntaje compuesto"
            color_func = _color_compuesto
            umbrales_html = (
                "<b>Puntaje compuesto</b> = ∛(r × GLK_norm × t-BP_norm) preserva signo: "
                "<span style='color:#dc3545;'>&lt;-0.20 anti-correlación</span> · "
                "<span style='color:#fd7e14;'>-0.20–+0.20 ambiguo</span> · "
                "<span style='color:#ffc107;'>+0.20–+0.40 débil</span> · "
                "<span style='color:#5cb85c;'>+0.40–+0.60 bueno</span> · "
                "<span style='color:#28a745;'>&gt;+0.60 confiable</span>"
            )
            y_range = (-1.05, 1.05)
        elif metrica_txt.startswith("t-BP"):
            vals = list(self._tbp_actuales)
            metric_name = "t-BP"
            color_func = _color_tbp
            umbrales_html = (
                "<b>Umbrales t-BP</b> (Baillie & Pilcher 1973): "
                "<span style='color:#dc3545;'>&lt;3 dudoso</span> · "
                "<span style='color:#fd7e14;'>3–4 débil</span> · "
                "<span style='color:#ffc107;'>4–6 aceptable</span> · "
                "<span style='color:#5cb85c;'>&gt;6 confiable</span>"
            )
            vals_validos = [v for v in vals if np.isfinite(v)]
            y_max = (max(vals_validos) * 1.15) if vals_validos else 10.0
            y_range = (-1.0, max(8.0, y_max))
        elif metrica_txt.startswith("GLK"):
            vals = list(self._glk_actuales)
            metric_name = "GLK"
            color_func = _color_glk
            umbrales_html = (
                "<b>Umbrales GLK</b> (Eckstein 1969): "
                "<span style='color:#dc3545;'>&lt;0.60 azar</span> · "
                "<span style='color:#fd7e14;'>0.60–0.65 débil</span> · "
                "<span style='color:#ffc107;'>0.65–0.70 aceptable</span> · "
                "<span style='color:#5cb85c;'>&gt;0.70 confiable</span>"
            )
            y_range = (0.30, 1.0)
        else:
            vals = list(self._r_actuales)
            metric_name = "r"
            color_func = _color_r
            umbrales_html = (
                "<b>Umbrales r</b> (Pearson, post-AR): "
                "<span style='color:#dc3545;'>&lt;0.20 azar</span> · "
                "<span style='color:#fd7e14;'>0.20–0.35 débil</span> · "
                "<span style='color:#ffc107;'>0.35–0.50 aceptable</span> · "
                "<span style='color:#5cb85c;'>&gt;0.50 confiable</span>"
            )
            y_range = (-1.05, 1.05)

        self.grafico_offset.clear()
        self.grafico_offset.addItem(self._texto_hover)
        self._texto_hover.hide()

        self.grafico_offset.addItem(pg.InfiniteLine(
            pos=0, angle=0, pen=pg.mkPen("#aaaaaa", width=1)))

        for shift, val in zip(self._shifts_actuales, vals):
            if not np.isfinite(val):
                continue
            self.grafico_offset.addItem(pg.BarGraphItem(
                x=[shift], height=[val], width=1.0,
                brush=color_func(val), pen=pg.mkPen(None)
            ))

        vals_para_max = [v if np.isfinite(v) else -np.inf for v in vals]
        if max(vals_para_max) > -np.inf:
            idx_max = int(np.argmax(vals_para_max))
            best_shift = self._shifts_actuales[idx_max]
            best_val = vals[idx_max]
            self.grafico_offset.addItem(pg.BarGraphItem(
                x=[best_shift], height=[best_val], width=1.0,
                brush=color_func(best_val),
                pen=pg.mkPen("white", width=2)
            ))

            def _f(v, dec=2):
                return "—" if not np.isfinite(v) else f"{v:.{dec}f}"

            self.grafico_offset.setTitle(
                f"Análisis de desfase — métrica: <b>{metric_name}</b>  "
                f"|  mejor: shift {best_shift:+d}, {metric_name}={_f(best_val)}"
            )
        else:
            self.grafico_offset.setTitle(f"Análisis de desfase — métrica: <b>{metric_name}</b>")

        self.grafico_offset.setYRange(*y_range)
        self.grafico_offset.setXRange(
            min(self._shifts_actuales) - 2, max(self._shifts_actuales) + 2)

        self.lbl_stats.setText(
            f"<div style='font-size:11px; line-height:1.5;'>"
            f"{umbrales_html}<br>"
            f"<span style='color:#888;'>"
            f"💡 Pasá el mouse sobre las barras para ver r/GLK/t-BP/n de cada shift. "
            f"La fecha verdadera es donde las TRES métricas son altas simultáneamente — "
            f"si solo una es alta, suele ser coincidencia.</span>"
            f"</div>"
        )
        self.lbl_stats.setVisible(True)

    # ── Comparación múltiple directa contra cronología ────────────────────

    def comparar_series_marcadas_vs_crono(self):
        """Compara las series marcadas (1 o varias) contra la cronología
        activa. Lanzada desde el botón "Series múltiples vs Crono" del
        panel principal — el usuario no tiene que abrir la ventana de
        cronología para usar esta funcionalidad.

        Flujo:
          1. Si NO hay cronología cargada: avisa al usuario que primero
             cargue/genere una.
          2. Si hay 0 series marcadas: aviso.
          3. Si hay 1+ series marcadas: ejecuta el cálculo en lote y
             abre `DialogoComparacionMultiple` con los resultados.

        RANGO DE DESFASE DINÁMICO POR SERIE:
        En vez de usar un max_shift fijo, el rango se calcula por serie
        según los años que cubre. Esto permite que el mismo botón funcione
        con dos tipos de series:

          - Series FECHADAS (años calendar 1500-2020 por ejemplo): el
            rango natural es estrecho, solo hay overlap cerca de shift=0.
            El cálculo es rápido y detecta errores de datación pequeños
            (anillos faltantes/extra).

          - Series FLOTANTES (años arbitrarios 0-200 por ejemplo): el
            rango natural cubre TODA la cronología. Encuentra la posición
            donde encaja la serie. Más lento pero necesario — antes el
            límite fijo de 10 hacía que estas series mostraran todo en 0
            porque jamás se solapaban con la cronología.

        Fórmula:
          shift_min = c_min - s_max + min_overlap - 1
          shift_max = c_max - s_min - min_overlap + 1
        donde c_* son años de la cronología y s_* son años de la serie.
        Cualquier shift en ese rango garantiza al menos `min_overlap`
        años de solape.
        """
        # Validación 1: cronología activa
        if self.cronologia_activa is None or self.cronologia_activa.empty:
            QMessageBox.warning(
                self, "Sin cronología activa",
                "Primero carga una cronología desde el botón <b>📚 Crono</b>\n"
                "o genérala desde <b>🧭 Generar Cronología</b>.\n\n"
                "Una vez disponible podrás comparar series contra ella desde acá."
            )
            return

        # Validación 2: series marcadas
        nombres = self._series_marcadas()
        if len(nombres) < 1:
            QMessageBox.warning(
                self, "Sin series marcadas",
                "Marca al menos una serie con check en la lista\n"
                "para compararla contra la cronología."
            )
            return

        # Excluir la propia cronología de la comparación si está marcada.
        # No tiene sentido comparar la cronología consigo misma — daría
        # siempre un puntaje perfecto en shift=0 y confunde el resumen.
        nombre_crono_completo = None
        for nombre in nombres:
            n_limpio = nombre.replace("📚 ", "").strip()
            if n_limpio == self._nombre_cronologia_activa:
                nombre_crono_completo = nombre
                break
        if nombre_crono_completo:
            nombres = [n for n in nombres if n != nombre_crono_completo]
            if not nombres:
                QMessageBox.warning(
                    self, "Sin series comparables",
                    "Solo marcaste la propia cronología activa. Marca otras\n"
                    "series para compararlas contra ella."
                )
                return

        MIN_OVERLAP = 20

        # Preparar la serie de la cronología (lado derecho de la comparación)
        meta = self.meta_cronologia or {}
        col_val = meta.get("columna_valor")
        if not col_val or col_val not in self.cronologia_activa.columns:
            col_val = self.cronologia_activa.columns[0]
        s_b = pd.to_numeric(
            self.cronologia_activa[col_val], errors="coerce"
        ).dropna()
        if s_b.empty:
            QMessageBox.warning(
                self, "Cronología sin datos válidos",
                "La cronología activa no contiene valores numéricos válidos."
            )
            return
        c_min_year = int(s_b.index.min())
        c_max_year = int(s_b.index.max())

        # Cursor de espera durante el cálculo
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        resultados = []
        nombre_crono = self._nombre_cronologia_activa or "cronología"

        try:
            for nombre in nombres:
                df_serie = self.series_datos.get(nombre)
                if df_serie is None or df_serie.empty:
                    continue

                s_a = _serie_transformada_para_comparar(df_serie, meta)
                if s_a.empty:
                    continue

                s_min_year = int(s_a.index.min())
                s_max_year = int(s_a.index.max())

                # Rango de desfase que garantiza al menos MIN_OVERLAP años
                # de solape con la cronología. Para series fechadas dará
                # un rango estrecho; para flotantes (años bajos) dará
                # un rango amplio que cubre toda la cronología.
                shift_min = c_min_year - s_max_year + MIN_OVERLAP - 1
                shift_max = c_max_year - s_min_year - MIN_OVERLAP + 1

                # Convertir a `max_shift` que es lo que pide la función:
                # acepta rango simétrico (-max_shift, +max_shift). Para no
                # quedar limitados, usamos el máximo absoluto.
                if shift_min > shift_max:
                    # Solape imposible incluso con shift máximo
                    resultados.append(self._resultado_sin_overlap(nombre))
                    continue
                max_shift_dinamico = max(abs(shift_min), abs(shift_max))

                # Capear a 5000 para evitar cálculos absurdos si por algún
                # error de datación una serie tiene años fuera de rango
                # razonable (ej. año 999999). 5000 años cubre cualquier
                # cronología real con margen.
                max_shift_dinamico = min(max_shift_dinamico, 5000)

                shifts, rs, glks, tbps, ns = _estadisticos_por_desfase(
                    s_a, s_b, max_shift_dinamico, MIN_OVERLAP,
                )

                if not shifts:
                    resultados.append(self._resultado_sin_overlap(nombre))
                    continue

                # Puntaje compuesto por desfase y elección del mejor
                scores = [score_compuesto(r, g, t)
                          for r, g, t in zip(rs, glks, tbps)]
                scores_validos = [
                    (i, s) for i, s in enumerate(scores) if np.isfinite(s)]
                if scores_validos:
                    idx_mejor = max(scores_validos, key=lambda x: x[1])[0]
                else:
                    idx_mejor = int(np.argmax(rs))

                # Puntaje en desfase=0 para calcular Δ
                if 0 in shifts:
                    score_shift0 = scores[shifts.index(0)]
                else:
                    score_shift0 = float("nan")

                resultados.append({
                    "nombre_serie": nombre,
                    "shifts": shifts, "rs": rs, "glks": glks,
                    "tbps": tbps, "ns": ns,
                    "mejor_shift": shifts[idx_mejor],
                    "mejor_score": scores[idx_mejor],
                    "mejor_r": rs[idx_mejor],
                    "mejor_glk": glks[idx_mejor],
                    "mejor_tbp": tbps[idx_mejor],
                    "mejor_n": ns[idx_mejor],
                    "score_shift0": score_shift0,
                    # Guardamos las series transformadas para poder dibujar
                    # el gráfico apropiado al hacer doble clic (barras si
                    # es flotante, correlación móvil si es fechada)
                    "serie_a": s_a,
                    "serie_b": s_b,
                })

                QApplication.processEvents()
        finally:
            QApplication.restoreOverrideCursor()

        if not resultados:
            QMessageBox.warning(
                self, "Sin resultados",
                "No se pudo comparar ninguna de las series marcadas.\n"
                "Verifica que las series tengan datos válidos."
            )
            return

        dlg = DialogoComparacionMultiple(resultados, nombre_crono, parent=self)
        dlg.exec()

    def _resultado_sin_overlap(self, nombre: str) -> dict:
        """Devuelve un dict de resultado en blanco para series que no
        pueden compararse (sin solape suficiente con la cronología incluso
        con el desfase máximo). Aparecen en la tabla con valores en 0/—
        para que el usuario sepa cuáles quedaron fuera."""
        return {
            "nombre_serie": nombre,
            "shifts": [], "rs": [], "glks": [],
            "tbps": [], "ns": [],
            "mejor_shift": 0,
            "mejor_score": float("nan"),
            "mejor_r": float("nan"),
            "mejor_glk": float("nan"),
            "mejor_tbp": float("nan"),
            "mejor_n": 0,
            "score_shift0": float("nan"),
        }

    def mostrar_tabla_offset(self):
        """Abre el diálogo con la tabla completa de offsets."""
        if not self._shifts_actuales:
            return
        dialogo = VentanaTablaOffset(
            self._shifts_actuales, self._r_actuales,
            self._glk_actuales, self._tbp_actuales, self._n_actuales,
            self,
        )
        dialogo.exec()

    # -------------------------------------------------------------------------
    # COFECHADO: CORRELACIÓN MÓVIL
    # -------------------------------------------------------------------------

    def verificar_datacion(self):
        """Dibuja curva de correlación móvil y stats por ventana."""
        marcadas = self._series_marcadas()
        if len(marcadas) != 2:
            return QMessageBox.warning(self, "Atención", "Marque EXACTAMENTE dos series.")

        # Limpiar modo offset
        self._shifts_actuales = []
        self._r_actuales = []
        self._glk_actuales = []
        self._tbp_actuales = []
        self._n_actuales = []

        m_diff, t_diff, n_m, n_t = self._preparar_diferencias_log(marcadas[0], marcadas[1])
        overlap = list(m_diff.index.intersection(t_diff.index))
        if len(overlap) < OVERLAP_MINIMO_ANALISIS:
            return QMessageBox.warning(self, "Error", "Las series no se solapan lo suficiente.")

        mitad = self.spin_ventana.value() // 2

        serie_a_raw = pd.to_numeric(self.series_datos[n_m]["Ancho_mm"], errors="coerce").dropna()
        serie_b_raw = pd.to_numeric(self.series_datos[n_t]["Ancho_mm"], errors="coerce").dropna()

        years, rs, glks, tbps, ns = _stats_movil(
            m_diff, t_diff, overlap, mitad, serie_a_raw, serie_b_raw)

        self._stats_movil_years = years
        self._stats_movil_r = rs
        self._stats_movil_glk = glks
        self._stats_movil_tbp = tbps
        self._stats_movil_n = ns
        self._mitad_ventana_actual = mitad

        r_global = _r_pearson(m_diff.loc[overlap].values, t_diff.loc[overlap].values)
        self._stats_actuales = estadisticos_cofechado(serie_a_raw, serie_b_raw)
        self._nombre_referencia = n_m
        self._nombre_problema = n_t

        self._dibujar_curva_cofechado(years, rs, overlap, r_global, n_t, [], False)

    def buscar_errores(self):
        """Busca anillos faltantes/sobrantes + calcula stats móviles."""
        marcadas = self._series_marcadas()
        if len(marcadas) != 2:
            return QMessageBox.warning(self, "Atención", "Marque EXACTAMENTE dos series.")

        # Limpiar modo offset
        self._shifts_actuales = []
        self._r_actuales = []
        self._glk_actuales = []
        self._tbp_actuales = []
        self._n_actuales = []

        n1, n2 = marcadas[0], marcadas[1]
        if len(self.series_datos[n1]) >= len(self.series_datos[n2]):
            self._nombre_referencia, self._nombre_problema = n1, n2
        else:
            self._nombre_referencia, self._nombre_problema = n2, n1

        df_ref = self.series_datos[self._nombre_referencia]
        df_prob = self.series_datos[self._nombre_problema]
        mitad = self.spin_ventana.value() // 2
        max_c = self.spin_max_corr.value()
        self._mitad_ventana_actual = mitad

        self.grafico_offset.setTitle("Buscando correcciones…")
        QApplication.processEvents()

        self._stats_actuales = estadisticos_cofechado(
            df_ref["Ancho_mm"], df_prob["Ancho_mm"])

        ref_es_crono = self._nombre_referencia.startswith("📚")
        prob_es_crono = self._nombre_problema.startswith("📚")

        def transformar_ref(serie_mm):
            return self._transformar_para_correlacion(serie_mm, ref_es_crono)

        def transformar_prob(serie_mm):
            return self._transformar_para_correlacion(serie_mm, prob_es_crono)

        self._sugerencias, es_marginal = buscar_correcciones(
            df_ref, df_prob, mitad, max_c,
            transformar_ref=transformar_ref,
            transformar_prob=transformar_prob,
        )
        self._sugerencias_es_marginal = es_marginal
        self._poblar_tabla(self._sugerencias, es_marginal=es_marginal)

        d_ref = transformar_ref(df_ref["Ancho_mm"])
        d_prob = transformar_prob(df_prob["Ancho_mm"])
        overlap = list(d_ref.index.intersection(d_prob.index))

        serie_a_raw = pd.to_numeric(df_ref["Ancho_mm"], errors="coerce").dropna()
        serie_b_raw = pd.to_numeric(df_prob["Ancho_mm"], errors="coerce").dropna()

        years, rs, glks, tbps, ns = _stats_movil(
            d_ref, d_prob, overlap, mitad, serie_a_raw, serie_b_raw)

        self._stats_movil_years = years
        self._stats_movil_r = rs
        self._stats_movil_glk = glks
        self._stats_movil_tbp = tbps
        self._stats_movil_n = ns

        r_global = (_r_pearson(d_ref.loc[overlap].values, d_prob.loc[overlap].values)
                    if overlap else float("nan"))
        self._dibujar_curva_cofechado(
            years, rs, overlap, r_global, self._nombre_problema,
            self._sugerencias[:10], False)

    def _dibujar_curva_cofechado(self, years, r_vals, overlap, r_global,
                                  nombre_target, marcadores, es_preview):
        """Dibuja la curva base de cofechado."""
        if not es_preview:
            self.grafico_offset.clear()
            self._item_preview = None
            self.grafico_offset.setYRange(-1.05, 1.05)
            if overlap:
                self.grafico_offset.setXRange(min(overlap) - 5, max(overlap) + 5)

            self.grafico_offset.addItem(
                pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen("#555555", width=1))
            )
            self.grafico_offset.addItem(
                pg.InfiniteLine(pos=UMBRAL_ANCLA_DEFECTO, angle=0,
                                pen=pg.mkPen("#5cb85c", style=Qt.PenStyle.DashLine, width=1))
            )
            self.grafico_offset.addItem(self._texto_hover)
            self._texto_hover.hide()

            if years:
                self.grafico_offset.plot(
                    years, r_vals,
                    pen=pg.mkPen("#3388FF", width=2),
                    name="r móvil actual",
                )

            for m in marcadores:
                color_hex = "#FF4444" if m["delta"] > 0 else "#FFA500"
                y_pos = m.get("r_zona_antes", 0.0)
                if np.isnan(y_pos):
                    y_pos = 0.0
                self.grafico_offset.addItem(
                    pg.ScatterPlotItem(
                        x=[m["anio"]], y=[y_pos], size=14,
                        brush=pg.mkBrush(color_hex),
                        pen=pg.mkPen("#ffffff", width=1),
                    )
                )
                signo = "+" if m["delta"] > 0 else ""
                html = (
                    f"<div style='color:{color_hex}; font-weight:bold; "
                    f"background:rgba(0,0,0,0.8); padding:3px 6px; border-radius:4px;'>"
                    f"{int(m['anio'])}<br>"
                    f"{signo}{m['delta']} anillo(s)<br>"
                    f"Δr = +{m['delta_r']:.3f}</div>"
                )
                txt = pg.TextItem(html=html, anchor=(0.5, 1.2))
                txt.setPos(m["anio"], y_pos)
                self.grafico_offset.addItem(txt)

            n_sug = len(marcadores)
            estado = "Sin sugerencias" if n_sug == 0 else f"{n_sug} sugerencia(s)"
            modo_str = " [COFECHA]" if self.btn_modo_cofecha.isChecked() else ""
            self.grafico_offset.setTitle(
                f"Cofechado{modo_str} '{nombre_target}' | "
                f"r global: {self._fmt_r(r_global)} | {estado}"
            )

            stats = getattr(self, "_stats_actuales", None)
            if stats and np.isfinite(stats.get("r", float("nan"))):
                self.lbl_stats.setText(formato_stats_global_html(stats))
                self.lbl_stats.setVisible(True)
            else:
                self.lbl_stats.setVisible(False)
        else:
            if self._item_preview is not None:
                self.grafico_offset.removeItem(self._item_preview)
            if years:
                self._item_preview = self.grafico_offset.plot(
                    years, r_vals,
                    pen=pg.mkPen("#FFA500", width=2, style=Qt.PenStyle.DashLine),
                    name="r móvil tras corrección",
                )

    def _limpiar_preview(self):
        if self._item_preview is not None:
            try:
                self.grafico_offset.removeItem(self._item_preview)
            except Exception:
                pass
            self._item_preview = None

    def _dibujar_preview(self, sug: dict):
        if not self._nombre_referencia or not self._nombre_problema:
            return
        df_ref = self.series_datos.get(self._nombre_referencia)
        df_corr = sug.get("df_corregido")
        if df_ref is None or df_corr is None:
            return

        ref_es_crono = self._nombre_referencia.startswith("📚")
        prob_es_crono = self._nombre_problema.startswith("📚")

        d_ref = self._transformar_para_correlacion(df_ref["Ancho_mm"], ref_es_crono)
        d_corr = self._transformar_para_correlacion(df_corr["Ancho_mm"], prob_es_crono)

        overlap_corr = list(d_ref.index.intersection(d_corr.index))
        mitad = self.spin_ventana.value() // 2
        years_p, r_p = _r_movil(d_ref, d_corr, overlap_corr, mitad)
        self._dibujar_curva_cofechado(years_p, r_p, overlap_corr, 0, "", [], True)

    # -------------------------------------------------------------------------
    # TABLA DE SUGERENCIAS
    # -------------------------------------------------------------------------

    def _poblar_tabla(self, sugerencias: list[dict], es_marginal: bool = False):
        """Rellena la tabla de sugerencias.

        es_marginal=True indica que las correcciones están por debajo del umbral
        de significancia (probable ruido). Se muestran como sanity-check con
        una advertencia visible.
        """
        self.tabla_sugerencias.setRowCount(0)
        self.btn_aplicar.setEnabled(False)

        # Mensaje de estado de la sección
        if hasattr(self, "lbl_estado_sug"):
            if not sugerencias:
                self.lbl_estado_sug.setText(
                    "<span style='color:#5cb85c;'>✓ <b>Sin correcciones sugeridas.</b></span><br>"
                    "<span style='font-size:9px;'>La serie parece estar correctamente cofechada "
                    "contra la referencia en esta ventana de análisis.</span>"
                )
                self.lbl_estado_sug.setVisible(True)
            elif es_marginal:
                self.lbl_estado_sug.setText(
                    "<span style='color:#f0ad4e;'>⚠️ <b>Sugerencias marginales</b></span> "
                    "<span style='font-size:9px;'>(Δr &lt; 0.05)</span><br>"
                    "<span style='font-size:9px;'>No hay correcciones significativas. "
                    "Estos cambios podrían ser ruido estadístico. Si la serie ya está "
                    "cofechada, probablemente no necesitás aplicar ninguno.</span>"
                )
                self.lbl_estado_sug.setVisible(True)
            else:
                n = len(sugerencias)
                self.lbl_estado_sug.setText(
                    f"<span style='color:#5cb85c;'>"
                    f"<b>{n} corrección(es) significativa(s)</b> encontrada(s).</span><br>"
                    "<span style='font-size:9px;'>Click en una fila para previsualizar "
                    "el cambio antes de aplicarlo.</span>"
                )
                self.lbl_estado_sug.setVisible(True)

        # Auto-expandir la sección si hay algo que mostrar
        if (sugerencias or es_marginal) and hasattr(self, "sec_sug"):
            self.sec_sug.expandir()

        font_bold = QFont()
        font_bold.setBold(True)

        for sug in sugerencias:
            row = self.tabla_sugerencias.rowCount()
            self.tabla_sugerencias.insertRow(row)

            delta_r = sug["delta_r"]
            if es_marginal:
                # Filas marginales: fondo gris-naranja muy tenue
                bg = QColor(240, 173, 78, 50)
            elif delta_r >= 0.10:
                bg = QColor(80, 180, 80, 120)
            elif delta_r >= 0.05:
                bg = QColor(180, 220, 80, 80)
            else:
                bg = QColor(220, 200, 80, 60)

            signo = "+" if sug["delta"] > 0 else ""
            valores = [
                str(int(sug["anio"])),
                f"{signo}{sug['delta']} anillo(s)",
                self._fmt_r(sug["r_antes"]),
                self._fmt_r(sug["r_tras"]),
                f"+{delta_r:.3f}",
            ]
            for col, val in enumerate(valores):
                celda = QTableWidgetItem(val)
                celda.setBackground(bg)
                celda.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 4:
                    celda.setFont(font_bold)
                self.tabla_sugerencias.setItem(row, col, celda)

    def _on_seleccion_sugerencia(self):
        fila = self.tabla_sugerencias.currentRow()
        if fila < 0 or fila >= len(self._sugerencias):
            self.btn_aplicar.setEnabled(False)
            self._limpiar_preview()
            return
        self.btn_aplicar.setEnabled(True)
        self._dibujar_preview(self._sugerencias[fila])

    def aplicar_correccion_seleccionada(self):
        fila = self.tabla_sugerencias.currentRow()
        if fila < 0 or fila >= len(self._sugerencias):
            return

        sug = self._sugerencias[fila]
        nombre = self._nombre_problema
        anio = sug["anio"]
        delta = sug["delta"]

        self.series_datos[nombre] = aplicar_correccion(self.series_datos[nombre], anio, delta)
        self.spin_edit_year.setValue(anio)

        for i in range(self.lista_series.count()):
            item = self.lista_series.item(i)
            if item.text() == nombre:
                self.lista_series.setCurrentItem(item)
                break

        self._limpiar_preview()
        self._sugerencias.clear()
        self.tabla_sugerencias.setRowCount(0)
        self.btn_aplicar.setEnabled(False)

        self.graficar_series()
        self.buscar_errores()

    # -------------------------------------------------------------------------
    # HOVER EN GRÁFICO
    # -------------------------------------------------------------------------

    def _on_mouse_movido(self, evt):
        """Hover sobre el gráfico de cofechado/offset."""
        if not self.grafico_offset.sceneBoundingRect().contains(evt):
            self._texto_hover.hide()
            self._restaurar_stats_globales()
            return

        punto = self.grafico_offset.plotItem.vb.mapSceneToView(evt)
        x_val = punto.x()

        # Modo curva de cofechado
        if self._stats_movil_years:
            years_arr = np.array(self._stats_movil_years)
            idx = int(np.argmin(np.abs(years_arr - x_val)))
            if abs(years_arr[idx] - x_val) > 5:
                self._restaurar_stats_globales()
                self._texto_hover.hide()
                return

            mitad = self._mitad_ventana_actual
            self.lbl_stats.setText(formato_stats_hover_html(
                int(self._stats_movil_years[idx]), mitad,
                self._stats_movil_r[idx],
                self._stats_movil_glk[idx],
                self._stats_movil_tbp[idx],
                self._stats_movil_n[idx],
            ))
            self.lbl_stats.setVisible(True)
            self._texto_hover.hide()
            return

        # Modo barras de offset
        if self._shifts_actuales:
            xi = int(round(x_val))
            if xi in self._shifts_actuales:
                idx = self._shifts_actuales.index(xi)
                r = self._r_actuales[idx]
                glk = self._glk_actuales[idx] if idx < len(self._glk_actuales) else float("nan")
                t_bp = self._tbp_actuales[idx] if idx < len(self._tbp_actuales) else float("nan")
                n = self._n_actuales[idx] if idx < len(self._n_actuales) else 0
                sc = score_compuesto(r, glk, t_bp)

                def _f(v, dec=2):
                    return "—" if not np.isfinite(v) else f"{v:.{dec}f}"

                html = (
                    f"<div style='background:rgba(40,40,40,230); padding:6px 10px; "
                    f"border-radius:4px; font-family:monospace; font-size:11px;'>"
                    f"<b style='color:#fff;'>Mover {xi:+d} años</b><br>"
                    f"<span style='color:#aaa;'>Puntaje:</span> "
                    f"<span style='color:{_color_compuesto(sc)}; font-weight:bold;'>{_f(sc, 2)}</span><br>"
                    f"<span style='color:#aaa;'>r:</span> "
                    f"<span style='color:{_color_r(r)}; font-weight:bold;'>{_f(r, 2)}</span><br>"
                    f"<span style='color:#aaa;'>GLK:</span> "
                    f"<span style='color:{_color_glk(glk)}; font-weight:bold;'>{_f(glk, 2)}</span><br>"
                    f"<span style='color:#aaa;'>t-BP:</span> "
                    f"<span style='color:{_color_tbp(t_bp)}; font-weight:bold;'>{_f(t_bp, 1)}</span><br>"
                    f"<span style='color:#aaa;'>n:</span> <span style='color:#ddd;'>{n}</span>"
                    f"</div>"
                )
                self._texto_hover.setHtml(html)
                self._texto_hover.setPos(punto.x(), punto.y())
                self._texto_hover.show()
            else:
                self._texto_hover.hide()
            return

        self._texto_hover.hide()

    def _restaurar_stats_globales(self):
        stats = getattr(self, "_stats_actuales", None)
        if stats and np.isfinite(stats.get("r", float("nan"))) and self._stats_movil_years:
            self.lbl_stats.setText(formato_stats_global_html(stats))
            self.lbl_stats.setVisible(True)


# =============================================================================
# VENTANA DE CRONOLOGÍA
# =============================================================================

# =============================================================================
# DIÁLOGO REUSABLE EPS / RBAR (compartido entre panel y VentanaCronologia)
# =============================================================================

def _mostrar_dialogo_eps_rbar(parent, meta: dict, nombre_cronologia: str):
    """Diálogo modal que grafica EPS móvil y Rbar móvil de una cronología,
    con botón para exportar los datos a CSV/TXT/Excel.

    Función a nivel módulo (en vez de método) para que ambos puntos de
    entrada (botón del panel y botón de la ventana de cronología) usen
    exactamente el mismo flujo y diálogo.

    Parameters
    ----------
    parent : QWidget
        Widget padre para el QDialog (típicamente el panel o la ventana).
    meta : dict
        Diccionario de metadatos. Debe tener llaves 'EPS_movil' y 'Rbar_movil'
        (cada una es un dict {año: valor}).
    nombre_cronologia : str
        Nombre que aparece en el título del diálogo.
    """
    eps_dict = (meta or {}).get("EPS_movil", {})
    rbar_dict = (meta or {}).get("Rbar_movil", {})

    if not eps_dict:
        QMessageBox.information(parent, "Sin datos",
            "Esta cronología no tiene EPS móvil calculado.\n\n"
            "Para verlo, regenerala con varias series desde la "
            "ventana de Cronología.")
        return

    dlg = QDialog(parent)
    nombre_safe = nombre_cronologia or "Cronología"
    dlg.setWindowTitle(f"EPS / Rbar — {nombre_safe}")
    dlg.resize(900, 600)
    lay = QVBoxLayout(dlg)

    lbl = QLabel(
        "<b>EPS (Expressed Population Signal)</b> — Wigley et al. (1984)<br>"
        "<span style='color:#888; font-size:11px;'>"
        "EPS = (N·Rbar) / (1 + (N-1)·Rbar). Umbral 0.85 (línea gris).<br>"
        "Rbar = correlación promedio entre pares de series en cada ventana móvil.<br>"
        "Una EPS &lt; 0.85 indica que el promedio (cronología) no captura "
        "adecuadamente la señal común de las series — esa porción de años "
        "es poco confiable.</span>"
    )
    lbl.setWordWrap(True)
    lay.addWidget(lbl)

    plot_eps = pg.PlotWidget(title="EPS móvil")
    plot_eps.showGrid(x=True, y=True, alpha=0.3)
    plot_eps.setYRange(0, 1.05)
    anios_eps = sorted(eps_dict.keys())
    vals_eps = [eps_dict[a] for a in anios_eps]
    plot_eps.plot(anios_eps, vals_eps, pen=pg.mkPen("#5cb85c", width=2))
    plot_eps.addItem(pg.InfiniteLine(
        pos=0.85, angle=0,
        pen=pg.mkPen("#888", style=Qt.PenStyle.DashLine)
    ))
    lay.addWidget(plot_eps)

    if rbar_dict:
        plot_rbar = pg.PlotWidget(title="Rbar móvil")
        plot_rbar.showGrid(x=True, y=True, alpha=0.3)
        anios_r = sorted(rbar_dict.keys())
        vals_r = [rbar_dict[a] for a in anios_r]
        plot_rbar.plot(anios_r, vals_r, pen=pg.mkPen("#3388FF", width=2))
        lay.addWidget(plot_rbar)

    fila_btn = QHBoxLayout()

    btn_export = QPushButton("💾 Exportar EPS / Rbar a CSV")
    btn_export.setStyleSheet(
        "background-color:#5cb85c; color:white; font-weight:bold; "
        "padding:6px 12px;"
    )

    def _exportar_eps_rbar():
        ruta_default = os.path.join(
            _ultima_carpeta(), f"{nombre_safe}_eps_rbar.csv")
        ruta, _ = QFileDialog.getSaveFileName(
            dlg, "Exportar EPS / Rbar",
            ruta_default,
            "CSV separado por punto y coma (*.csv);;"
            "Texto separado por tabulaciones (*.txt);;"
            "Excel (*.xlsx)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not ruta:
            return
        _ultima_carpeta(ruta)
        try:
            todos_anios = sorted(set(eps_dict.keys()) | set(rbar_dict.keys()))
            df_export = pd.DataFrame({
                "Anio": [int(a) for a in todos_anios],
                "EPS": [eps_dict.get(a, float("nan")) for a in todos_anios],
                "Rbar": [rbar_dict.get(a, float("nan")) for a in todos_anios],
            })
            ext = os.path.splitext(ruta)[1].lower()
            if ext == ".xlsx":
                df_export.to_excel(ruta, index=False)
            elif ext == ".csv":
                df_export.to_csv(ruta, index=False, sep=";",
                                 decimal=",", float_format="%.6f")
            else:
                df_export.to_csv(ruta, index=False, sep="\t",
                                 decimal=",", float_format="%.6f")
            QMessageBox.information(
                dlg, "✅ Exportado",
                f"EPS / Rbar exportados a:\n{ruta}\n\n"
                f"Filas: {len(df_export)} años"
            )
        except Exception as exc:
            QMessageBox.critical(dlg, "Error",
                                 f"No se pudo guardar:\n{exc}")

    btn_export.clicked.connect(_exportar_eps_rbar)
    fila_btn.addWidget(btn_export)

    btn_close = QPushButton("Cerrar")
    btn_close.clicked.connect(dlg.accept)
    fila_btn.addWidget(btn_close)

    lay.addLayout(fila_btn)
    dlg.exec()


class VentanaCronologia(QDialog):
    """Ventana para construir, cargar, exportar y comparar cronologías."""

    def __init__(self, panel_padre):
        super().__init__(panel_padre)
        self._series_cronologia: dict[str, pd.DataFrame] = {}
        self._panel = panel_padre
        self.setWindowTitle("Cronología")
        self.setMinimumSize(1200, 780)

        self.cronologia_df: pd.DataFrame | None = None
        self.cronologia_meta: dict = {}
        self.eps_movil: pd.DataFrame | None = None

        self._construir_ui()
        self.refresh_series()
        self._sincronizar_widgets_con_tema_app()

    def _sincronizar_widgets_con_tema_app(self):
        """Aplica colores apropiados al QTextEdit según el tema activo de la app.
        QTextEdit no está cubierto por el stylesheet global (estilos.py solo
        cubre QListView/QSpinBox/etc), así que sin esto el QTextEdit queda con
        fondo blanco default y el texto blanco heredado de `QWidget { color: white; }`
        del estilo global oscuro → metadata invisible.
        """
        ss_app = QApplication.instance().styleSheet() or ""
        if "#2b2b2b" in ss_app:
            # Tema oscuro — replicar el estilo de QListView del global
            self.txt_info.setStyleSheet(
                "QTextEdit { background-color: #3b3b3b; color: white; "
                "border: 1px solid #555; border-radius: 3px; padding: 4px; }"
            )
        elif "#f0f0f0" in ss_app:
            # Tema claro
            self.txt_info.setStyleSheet(
                "QTextEdit { background-color: white; color: black; "
                "border: 1px solid #ccc; border-radius: 3px; padding: 4px; }"
            )
        # Si no hay marcador reconocible, dejar sin estilizar.

    def changeEvent(self, event):
        """Re-sincroniza el QTextEdit cuando cambia el tema de la app en caliente."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange and hasattr(self, "txt_info"):
            self._sincronizar_widgets_con_tema_app()

    def _construir_ui(self):
        layout = QVBoxLayout(self)

        barra = QHBoxLayout()
        self.btn_cargar_series = QPushButton("📂 Cargar series")
        self.btn_cargar_series.clicked.connect(self._cargar_series_desde_panel)
        barra.addWidget(self.btn_cargar_series)

        self.btn_cargar_cron = QPushButton("📥 Cargar cronología")
        self.btn_cargar_cron.clicked.connect(self.cargar_cronologia)
        barra.addWidget(self.btn_cargar_cron)

        self.btn_exportar = QPushButton("💾 Exportar cronología")
        self.btn_exportar.clicked.connect(self.exportar_cronologia)
        self.btn_exportar.setEnabled(False)
        barra.addWidget(self.btn_exportar)

        self.btn_generar = QPushButton("⚙ Generar cronología")
        self.btn_generar.clicked.connect(self.generar_cronologia)
        self.btn_generar.setStyleSheet("background-color: #5cb85c; color: white; font-weight: bold;")
        barra.addWidget(self.btn_generar)

        self.btn_eps_rbar = QPushButton("📈 EPS / Rbar")
        self.btn_eps_rbar.setToolTip(
            "Ver el gráfico de EPS móvil y Rbar móvil de la cronología actual.\n"
            "Permite identificar tramos de años con baja confianza estadística."
        )
        self.btn_eps_rbar.setStyleSheet(
            "QPushButton{background-color:#3a6e3a; color:white; font-weight:bold;}"
            "QPushButton:hover{background-color:#4a8e4a;}"
            "QPushButton:disabled{background-color:#444; color:#888;}"
        )
        self.btn_eps_rbar.setEnabled(False)
        self.btn_eps_rbar.clicked.connect(self._mostrar_eps_rbar)
        barra.addWidget(self.btn_eps_rbar)

        self.btn_usar = QPushButton("📚 Usar en cofechado")
        self.btn_usar.clicked.connect(self.usar_cronologia_en_panel)
        self.btn_usar.setEnabled(False)
        barra.addWidget(self.btn_usar)

        self.btn_comparar = QPushButton("🔎 Comparar con cronología")
        self.btn_comparar.setToolTip(
            "Comparar serie(s) marcada(s) contra la cronología activa.\n"
            "• 1 serie marcada  → gráfico + análisis detallado\n"
            "• 2+ marcadas      → tabla resumen batch (una fila por serie)")
        self.btn_comparar.clicked.connect(self.comparar_con_cronologia)
        self.btn_comparar.setEnabled(False)
        barra.addWidget(self.btn_comparar)

        # Botón "Tabla detallada": muestra los resultados de la última
        # comparación con TODOS los desfases ordenados por score compuesto.
        # Aparece oculto al inicio (no hay resultados todavía) y se muestra
        # automáticamente después de la primera comparación.
        self._btn_tabla_comparacion = QPushButton("📊 Tabla detallada")
        self._btn_tabla_comparacion.setToolTip(
            "Ver todos los desfases con r, GLK, t-BP y puntaje compuesto\n"
            "(disponible después de comparar con la cronología)")
        self._btn_tabla_comparacion.clicked.connect(self._abrir_tabla_comparacion)
        self._btn_tabla_comparacion.setVisible(False)
        barra.addWidget(self._btn_tabla_comparacion)

        layout.addLayout(barra)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)

        left_layout.addWidget(QLabel("<b>Series disponibles:</b>"))

        self.btn_sel_todo = QPushButton("☑ Seleccionar / Deseleccionar todo")
        self.btn_sel_todo.setStyleSheet("QPushButton{padding:4px; font-size:11px;}")
        self.btn_sel_todo.clicked.connect(self.toggle_seleccion)
        left_layout.addWidget(self.btn_sel_todo)

        self.lista_series = QListWidget()
        self.lista_series.itemChanged.connect(self._sincronizar_estado_botones)
        left_layout.addWidget(self.lista_series, 1)

        self.lbl_estado = QLabel("Sin cronología cargada.")
        self.lbl_estado.setWordWrap(True)
        left_layout.addWidget(self.lbl_estado)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)

        box_cfg = QGroupBox("Configuración")
        form = QFormLayout(box_cfg)

        self.combo_tipo = QComboBox()
        self.combo_tipo.addItems(["raw", "standard", "residual", "Todas"])
        self.combo_tipo.currentTextChanged.connect(self._on_tipo_cronologia_cambiado)
        form.addRow("Tipo de cronología:", self.combo_tipo)

        self.combo_metodo = QComboBox()
        # "raw" se quitó porque no aplica estandarización: tipo="standard" con
        # metodo="raw" devuelve exactamente lo mismo que tipo="raw". Para
        # cronología sin detrending, usar tipo="raw" directamente.
        # "media" se muestra en español; _ajustar_curva acepta tanto "media"
        # como "mean", así que el texto visible puede ir en español.
        self.combo_metodo.addItems(["spline", "negexp", "linear", "media_movil", "media"])
        self.combo_metodo.setCurrentText("spline")
        self.combo_metodo.currentTextChanged.connect(self._on_metodo_estandar_cambiado)

        # Spinbox de ventana para media_movil — aparece solo cuando se elige
        # ese método. Default 10% del largo (estilo ARSTAN), pero el usuario
        # puede sobreescribir con un valor fijo.
        self.spin_ventana_mm = SpinBoxFlechas()
        self.spin_ventana_mm.setRange(3, 999)
        self.spin_ventana_mm.setValue(11)
        self.spin_ventana_mm.setSuffix(" años")
        self.spin_ventana_mm.setMinimumWidth(140)
        self.spin_ventana_mm.setToolTip(
            "Ventana de la media móvil (en años). Por convención impar.\n"
            "Valores chicos (5–15) → tendencia local más sensible.\n"
            "Valores grandes (30+) → tendencia más suave (similar a spline rígido)."
        )
        self.spin_ventana_mm.setVisible(False)

        fila_metodo = QHBoxLayout()
        fila_metodo.setContentsMargins(0, 0, 0, 0)
        fila_metodo.setSpacing(6)
        fila_metodo.addWidget(self.combo_metodo, 1)
        fila_metodo.addWidget(self.spin_ventana_mm)
        contenedor_metodo = QWidget()
        contenedor_metodo.setLayout(fila_metodo)
        form.addRow("Estandarización:", contenedor_metodo)

        self.combo_agreg = QComboBox()
        # Texto en español visible para el usuario, pero el VALOR interno
        # (userData) se mantiene en inglés para no romper la lógica de
        # construcción de la cronología. Se lee con currentData().
        self.combo_agreg.addItem("Biponderada (biweight)", "biweight")
        self.combo_agreg.addItem("Media", "mean")
        self.combo_agreg.addItem("Mediana", "median")
        self.combo_agreg.setCurrentIndex(0)  # biponderada por defecto
        self.combo_agreg.setToolTip(
            "Cómo se combinan las series individuales en UNA cronología media:\n\n"
            "• Biponderada (biweight): media robusta de Tukey. Baja el peso\n"
            "  de valores atípicos progresivamente. Es el estándar en\n"
            "  dendrocronología (lo que usa ARSTAN por defecto).\n"
            "• Media: media aritmética simple. Sensible a valores atípicos.\n"
            "• Mediana: el valor central. Robusta pero descarta magnitud."
        )
        form.addRow("Agregación:", self.combo_agreg)

        # Etiqueta de solape con descripción al pasar el mouse
        lbl_overlap = QLabel("Solape mínimo:")
        lbl_overlap.setToolTip(
            "Cantidad mínima de años en común que dos series deben tener\n"
            "para calcular la correlación entre ellas.\n\n"
            "Valores chicos (5–10) → incluye más pares pero correlaciones\n"
            "menos confiables. Valores grandes (30+) → solo pares con\n"
            "buen solape, más confiable pero descarta series cortas."
        )
        self.spin_min_overlap = SpinBoxFlechas()
        self.spin_min_overlap.setRange(2, 500)
        self.spin_min_overlap.setValue(10)
        self.spin_min_overlap.setToolTip(lbl_overlap.toolTip())
        form.addRow(lbl_overlap, self.spin_min_overlap)

        self.spin_shift = SpinBoxFlechas()
        self.spin_shift.setRange(0, 10)
        self.spin_shift.setValue(5)
        form.addRow("Desfase auto máx.:", self.spin_shift)

        # ── Ventana móvil para Rbar / EPS ─────────────────────────────
        # Permite reproducir el formato de ARSTAN (ventana de 50 años con
        # paso de 25) para comparar y reportar en publicaciones. Con
        # ventana chica (15) y paso 1 se obtiene la curva año a año suave.
        lbl_ventana_eps = QLabel("Ventana Rbar/EPS:")
        lbl_ventana_eps.setToolTip(
            "Tamaño de la ventana móvil (en años) para calcular Rbar y EPS.\n\n"
            "• 50 años → formato estándar de ARSTAN (recomendado para\n"
            "  comparar y para publicaciones).\n"
            "• 15 años o menos → curva más detallada año a año, útil para\n"
            "  ver la evolución fina de la señal común.\n\n"
            "Ventanas grandes promedian más datos y dan valores más estables."
        )
        self.spin_ventana_eps = SpinBoxFlechas()
        self.spin_ventana_eps.setRange(10, 200)
        self.spin_ventana_eps.setValue(50)
        self.spin_ventana_eps.setSingleStep(5)
        self.spin_ventana_eps.setSuffix(" años")
        self.spin_ventana_eps.setToolTip(lbl_ventana_eps.toolTip())
        form.addRow(lbl_ventana_eps, self.spin_ventana_eps)

        lbl_paso_eps = QLabel("Paso Rbar/EPS:")
        lbl_paso_eps.setToolTip(
            "Cada cuántos años se desliza la ventana móvil.\n\n"
            "• 25 años → formato estándar de ARSTAN (la ventana se mueve\n"
            "  en saltos de 25 años, generando pocos valores espaciados).\n"
            "• 1 año → un valor por cada año (curva continua y suave).\n\n"
            "Para reproducir ARSTAN usa ventana 50 y paso 25."
        )
        self.spin_paso_eps = SpinBoxFlechas()
        self.spin_paso_eps.setRange(1, 100)
        self.spin_paso_eps.setValue(25)
        self.spin_paso_eps.setSuffix(" años")
        self.spin_paso_eps.setToolTip(lbl_paso_eps.toolTip())
        form.addRow(lbl_paso_eps, self.spin_paso_eps)

        self.input_nombre = QLineEdit()
        self.input_nombre.setPlaceholderText("Nombre de cronología")
        form.addRow("Nombre:", self.input_nombre)

        right_layout.addWidget(box_cfg)

        box_info = QGroupBox("Información / metadatos")
        info_layout = QVBoxLayout(box_info)
        self.txt_info = QTextEdit()
        self.txt_info.setReadOnly(True)
        self.txt_info.setMinimumHeight(180)
        info_layout.addWidget(self.txt_info)
        right_layout.addWidget(box_info, 1)

        self.grafico = pg.PlotWidget(title="Cronología")
        self.grafico.showGrid(x=True, y=True, alpha=0.3)
        right_layout.addWidget(self.grafico, 2)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        # Estado inicial: aplicar enable/disable según el tipo seleccionado
        self._on_tipo_cronologia_cambiado(self.combo_tipo.currentText())

    def _on_tipo_cronologia_cambiado(self, tipo: str):
        """Habilita/deshabilita el combo de estandarización según el tipo.

        Cuando tipo='raw' la estandarización no aplica (la serie se devuelve
        tal cual sin detrending), así que el combo se deshabilita visualmente
        para que el usuario sepa que no tiene efecto.
        """
        if not hasattr(self, "combo_metodo"):
            return
        es_raw = (tipo or "").lower() == "raw"
        self.combo_metodo.setEnabled(not es_raw)
        if hasattr(self, "spin_ventana_mm"):
            # También deshabilitar el spinbox de ventana si tipo=raw
            self.spin_ventana_mm.setEnabled(not es_raw)
        if es_raw:
            self.combo_metodo.setToolTip(
                "No aplica con tipo='raw' — la serie se usa sin estandarización."
            )
        else:
            self.combo_metodo.setToolTip("")

    def _on_metodo_estandar_cambiado(self, metodo: str):
        """Muestra el spinbox de ventana solo cuando se elige media_movil."""
        if hasattr(self, "spin_ventana_mm"):
            self.spin_ventana_mm.setVisible(
                (metodo or "").lower() in {"media_movil", "moving_average", "ma"}
            )

    def refresh_series(self):
        self.lista_series.blockSignals(True)
        checked = {
            self.lista_series.item(i).text()
            for i in range(self.lista_series.count())
            if self.lista_series.item(i).checkState() == Qt.CheckState.Checked
        }
        self.lista_series.clear()
        for nombre in sorted(self._series_cronologia.keys()):
            if nombre.startswith("📚 "):
                continue
            item = QListWidgetItem(nombre)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if nombre in checked else Qt.CheckState.Unchecked)
            self.lista_series.addItem(item)
        self.lista_series.blockSignals(False)
        self._sincronizar_estado_botones()

    def _series_marcadas(self) -> list[str]:
        return [
            self.lista_series.item(i).text()
            for i in range(self.lista_series.count())
            if self.lista_series.item(i).checkState() == Qt.CheckState.Checked
        ]

    def toggle_seleccion(self):
        todas = all(
            self.lista_series.item(i).checkState() == Qt.CheckState.Checked
            for i in range(self.lista_series.count())
        )
        estado = Qt.CheckState.Unchecked if todas else Qt.CheckState.Checked
        self.lista_series.blockSignals(True)
        for i in range(self.lista_series.count()):
            self.lista_series.item(i).setCheckState(estado)
        self.lista_series.blockSignals(False)
        self._sincronizar_estado_botones()

    def _sincronizar_estado_botones(self):
        hay_crono = self.cronologia_df is not None and not self.cronologia_df.empty
        self.btn_exportar.setEnabled(hay_crono)
        self.btn_usar.setEnabled(hay_crono)
        self.btn_comparar.setEnabled(hay_crono)
        # EPS/Rbar solo si hay datos móviles calculados
        tiene_eps_movil = bool((self.cronologia_meta or {}).get("EPS_movil"))
        self.btn_eps_rbar.setEnabled(hay_crono and tiene_eps_movil)
        if hay_crono:
            meta = self.cronologia_meta
            self.lbl_estado.setText(
                f"Cronología cargada: {meta.get('nombre', 'Cronología')} | "
                f"tipo={meta.get('tipo_cronologia', 'raw')} | "
                f"método={meta.get('metodo_estandarizacion', 'raw')} | "
                f"agregación={meta.get('agregacion', 'mean')}"
            )
        else:
            self.lbl_estado.setText("Sin cronología cargada.")

    def _mostrar_eps_rbar(self):
        """Muestra el diálogo EPS/Rbar de la cronología actual del diálogo.
        Reutiliza la misma función que el botón EPS/Rbar del panel."""
        nombre = (self.cronologia_meta or {}).get("nombre", "Cronología")
        _mostrar_dialogo_eps_rbar(self, self.cronologia_meta or {}, nombre)

    def _cargar_series_desde_panel(self):
        """Carga series SOLO en la ventana de cronología."""
        rutas, _ = QFileDialog.getOpenFileNames(
            self, "Seleccionar Series", _ultima_carpeta(),
            "Series compatibles (*.rwl *.txt *.wid *.csv *.tsv *.xlsx *.xls *.cat *.cmp);;Todos (*.*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if rutas:
            _ultima_carpeta(rutas[0])
        for ruta in rutas:
            try:
                with open(ruta, 'r', encoding='utf-8', errors='replace') as f:
                    primeras = f.read(500)
                if "=N" in primeras and "=I" in primeras:
                    series_ext = leer_formato_compacto(ruta)
                    for sid, df in series_ext.items():
                        if sid not in self._series_cronologia:
                            self._series_cronologia[sid] = df.copy()
                    continue
                ext = os.path.splitext(ruta)[1].lower()
                if ext == ".wid":
                    df, sid = leer_wid(ruta)
                    if sid not in self._series_cronologia:
                        self._series_cronologia[sid] = df.copy()
                elif ext in (".csv", ".tsv", ".xlsx", ".xls"):
                    df, sid = leer_tabular(ruta)
                    if sid not in self._series_cronologia:
                        self._series_cronologia[sid] = df.copy()
                else:
                    try:
                        series_ext = leer_tucson_multi(ruta)
                        for sid, df in series_ext.items():
                            if sid not in self._series_cronologia:
                                self._series_cronologia[sid] = df.copy()
                    except Exception:
                        df, sid = leer_tabular(ruta)
                        if sid not in self._series_cronologia:
                            self._series_cronologia[sid] = df.copy()
            except Exception as exc:
                QMessageBox.warning(self, "Error", f"No se pudo cargar:\n{exc}")
        self.refresh_series()

    def generar_cronologia(self):
        nombres = self._series_marcadas()
        if not nombres:
            QMessageBox.warning(self, "Atención", "Seleccione al menos una serie.")
            return

        tipo_sel = self.combo_tipo.currentText()
        metodo = self.combo_metodo.currentText()
        # currentData() devuelve el valor interno en inglés (biweight/mean/
        # median) aunque el texto visible esté en español.
        agreg = self.combo_agreg.currentData() or "biweight"
        nombre_base = self.input_nombre.text().strip() or "Cronologia"

        tipos = ["raw", "standard", "residual"] if tipo_sel == "Todas" else [tipo_sel]

        progreso = QProgressDialog("Generando cronología...", None, 0, 100, self)
        progreso.setWindowTitle("Procesando")
        progreso.setWindowModality(Qt.WindowModality.WindowModal)
        progreso.setMinimumDuration(0)
        progreso.setMaximum(100)
        progreso.setValue(0)
        progreso.setCancelButton(None)
        QApplication.processEvents()

        resultados = {}
        for i, tipo in enumerate(tipos):
            pct_inicio = int(i * 50 / len(tipos))
            progreso.setLabelText(f"Generando cronología {tipo}... ({i+1}/{len(tipos)})")
            progreso.setValue(pct_inicio)
            QApplication.processEvents()

            try:
                # Solo pasar ventana_mm si el método es media_movil
                vmm = (self.spin_ventana_mm.value()
                       if metodo == "media_movil" else None)
                df, meta = _construir_cronologia_desde_series(
                    self._series_cronologia, nombres, tipo, metodo, agreg,
                    ventana_mm=vmm,
                )
                nombre = f"{nombre_base}_{tipo}" if len(tipos) > 1 else nombre_base
                meta["nombre"] = nombre

                # Rbar y EPS ahora vienen calculados desde dentro de
                # _construir_cronologia_desde_series sobre las series
                # DETRENDADAS (comparable con ARSTAN). Antes acá se
                # sobrescribían usando series raw, lo que inflaba el Rbar
                # porque las tendencias decadales hacen covariar las
                # series brutas a largo plazo.

                resultados[tipo] = (df, meta)
            except Exception as exc:
                QMessageBox.critical(self, "Error", f"Error en cronología {tipo}:\n{exc}")

        if not resultados:
            progreso.close()
            return

        progreso.setLabelText("Calculando EPS / Rbar móvil...")
        progreso.setValue(50)
        QApplication.processEvents()

        # Callback de progreso para mantener la UI responsiva durante el
        # cálculo de EPS / Rbar móvil (que itera sobre muchos años con
        # ventana móvil — es la parte más lenta).
        def _cb_progreso(pct):
            progreso.setValue(50 + int(pct * 0.45))
            QApplication.processEvents()

        # Determinar paso para muestreo. El usuario configura ventana y paso
        # en la UI (default 50/25 estilo ARSTAN para comparación directa).
        ventana_eps = self.spin_ventana_eps.value()
        paso_eps = self.spin_paso_eps.value()

        eps_df = calcular_eps_rbar_movil(
            self._series_cronologia, nombres, window=ventana_eps,
            paso=paso_eps, progreso=_cb_progreso,
        )
        self.eps_movil = eps_df

        if len(resultados) > 1:
            frames = []
            for tipo, (df, meta) in resultados.items():
                col = meta.get("columna_valor", tipo.capitalize())
                frames.append(df[[col]].rename(columns={col: tipo.capitalize()}))
                if "N" in df.columns:
                    frames.append(df[["N"]])
            combined = pd.concat(frames, axis=1)
            combined = combined.loc[:, ~combined.columns.duplicated()]
            last_tipo = list(resultados.keys())[-1]
            _, last_meta = resultados[last_tipo]
            last_meta["nombre"] = nombre_base
            last_meta["tipo_cronologia"] = "todas"
            last_meta["columna_valor"] = "Standard"
            last_meta["columnas_disponibles"] = [t.capitalize() for t in resultados.keys()]
            if not eps_df.empty:
                last_meta["EPS_movil"] = eps_df["EPS"].dropna().to_dict()
                last_meta["Rbar_movil"] = eps_df["Rbar"].dropna().to_dict()
            self.cronologia_df = combined
            self.cronologia_meta = last_meta
        else:
            tipo_unico = list(resultados.keys())[0]
            self.cronologia_df, self.cronologia_meta = resultados[tipo_unico]
            if not eps_df.empty:
                self.cronologia_meta["EPS_movil"] = eps_df["EPS"].dropna().to_dict()
                self.cronologia_meta["Rbar_movil"] = eps_df["Rbar"].dropna().to_dict()

        progreso.setValue(100)
        progreso.close()

        self._pintar_cronologia()
        self._actualizar_info()
        self._sincronizar_estado_botones()

    def cargar_cronologia(self):
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Cargar cronología", _ultima_carpeta(),
            "Cronologías (*.csv *.txt *.xlsx);;Todos (*.*)"
        )
        if not ruta:
            return
        _ultima_carpeta(ruta)

        try:
            df, meta = _leer_cronologia_guardada(ruta)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"No se pudo abrir la cronología:\n{exc}")
            return

        meta.setdefault("nombre", os.path.splitext(os.path.basename(ruta))[0])
        meta.setdefault("columna_valor", df.columns[0])
        self.cronologia_df = df
        self.cronologia_meta = meta
        self.input_nombre.setText(meta.get("nombre", "Cronología"))
        self._pintar_cronologia()
        self._actualizar_info()
        self._sincronizar_estado_botones()

    def exportar_cronologia(self):
        if self.cronologia_df is None or self.cronologia_df.empty:
            QMessageBox.warning(self, "Atención", "No hay cronología para exportar.")
            return
        nombre = self.cronologia_meta.get("nombre", "cronologia")
        ruta_default = os.path.join(_ultima_carpeta(), nombre)
        ruta, _ = QFileDialog.getSaveFileName(
            self, "Exportar cronología", ruta_default,
            "Excel (*.xlsx);;CSV (*.csv);;Texto (*.txt)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not ruta:
            return
        _ultima_carpeta(ruta)
        try:
            _exportar_cronologia_df(self.cronologia_df, ruta)
            meta = dict(self.cronologia_meta)
            meta["archivo"] = os.path.basename(ruta)
            _guardar_metadatos_cronologia(ruta, meta)
            QMessageBox.information(self, "OK", f"Cronología exportada:\n{ruta}")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"No se pudo exportar:\n{exc}")

    def usar_cronologia_en_panel(self):
        if self.cronologia_df is None or self.cronologia_df.empty:
            return
        nombre = self.cronologia_meta.get("nombre", "Cronología")
        self._panel.set_cronologia_activa(self.cronologia_df, self.cronologia_meta, nombre)
        QMessageBox.information(
            self, "OK",
            f"Cronología '{nombre}' activada en el panel de cofechado.\n\n"
            "Aparece en la lista del panel con el prefijo 📚.\n"
            "Marcala junto a una serie y dale Verificar / Buscar / Analizar."
        )

    def comparar_con_cronologia(self):
        """Compara series marcadas contra la cronología activa.

        Bifurca según la cantidad de series marcadas:
          - 1 sola → modo individual: gráfico de r vs desfase, texto con
            top 3 candidatos por score, botón "Tabla detallada"
          - 2 o más → modo batch: diálogo nuevo con tabla resumen
            (una fila por serie) ordenable, con doble clic para drill-down
            al modo individual de cada serie.

        El usuario controla el modo con la cantidad de check marcados
        en el panel, sin botones extra.
        """
        if self.cronologia_df is None or self.cronologia_df.empty:
            QMessageBox.warning(self, "Atención", "Primero cargue o genere una cronología.")
            return
        nombres = self._series_marcadas()
        if len(nombres) < 1:
            QMessageBox.warning(
                self, "Atención",
                "Marque al menos una serie para comparar.\n"
                "(Marque varias para hacer comparación múltiple.)")
            return

        # Si hay más de una serie marcada → modo batch
        if len(nombres) > 1:
            self._comparar_multiple_con_cronologia(nombres)
            return

        # Modo individual (1 sola serie) — comportamiento histórico
        nombre = nombres[0]
        self._comparar_individual_con_cronologia(nombre)

    def _comparar_individual_con_cronologia(self, nombre: str):
        """Modo individual: 1 serie → gráfico + texto + tabla detallada.

        Es el comportamiento histórico, conservado tal cual estaba antes
        del modo batch. Útil para inspección a fondo de una sola serie.
        """
        serie = self._series_cronologia[nombre]
        meta = self.cronologia_meta
        s_a = _serie_transformada_para_comparar(serie, meta)
        col_val = meta.get("columna_valor") or self.cronologia_df.columns[0]
        if col_val not in self.cronologia_df.columns:
            col_val = self.cronologia_df.columns[0]
        s_b = pd.to_numeric(self.cronologia_df[col_val], errors="coerce").dropna()

        # Cálculo de los TRES estadísticos por cada desfase
        shifts, rs, glks, tbps, ns = _estadisticos_por_desfase(
            s_a, s_b,
            self.spin_shift.value(),
            self.spin_min_overlap.value(),
        )

        self.grafico.clear()
        if not shifts:
            self.grafico.setTitle("No se encontraron solapamientos suficientes.")
            return

        # Guardar para que el botón "Ver tabla" pueda abrirla después
        self._ultimo_resultado_comparacion = {
            "nombre_serie": nombre,
            "shifts": shifts, "rs": rs, "glks": glks,
            "tbps": tbps, "ns": ns,
        }

        # Línea de referencia en y=0
        self.grafico.addItem(
            pg.InfiniteLine(pos=0, angle=0,
                             pen=pg.mkPen("#888888", width=1)))

        # Plot principal: r (lo más interpretable visualmente, escala [-1, 1])
        self.grafico.plot(shifts, rs,
                          pen=pg.mkPen("#3388FF", width=2), symbol="o")

        # Score compuesto en cada shift y elección del mejor por score
        scores = [score_compuesto(r, g, t)
                  for r, g, t in zip(rs, glks, tbps)]
        scores_validos = [(i, s) for i, s in enumerate(scores) if np.isfinite(s)]
        if scores_validos:
            idx_mejor = max(scores_validos, key=lambda x: x[1])[0]
        else:
            idx_mejor = int(np.argmax(rs))

        best_shift = shifts[idx_mejor]
        best_r = rs[idx_mejor]
        best_glk = glks[idx_mejor]
        best_tbp = tbps[idx_mejor]
        best_score = scores[idx_mejor]
        best_n = ns[idx_mejor]

        # Punto verde grande en el mejor según score compuesto
        self.grafico.addItem(pg.ScatterPlotItem(
            x=[best_shift], y=[best_r], size=14,
            brush=pg.mkBrush("#5cb85c"), pen=pg.mkPen("w", width=2),
        ))

        self.grafico.setYRange(-1.05, 1.05)
        self.grafico.setTitle(
            f"'{nombre}' vs cronología — mejor desfase: {best_shift}"
            f"  (puntaje={best_score:.3f})"
        )

        # Construir resumen multi-stat para txt_info
        def fmt(x, dec=3):
            return f"{x:.{dec}f}" if np.isfinite(x) else "—"

        # Top 3 desfases por score compuesto, para mostrar candidatos
        top3 = sorted(
            range(len(shifts)),
            key=lambda i: scores[i] if np.isfinite(scores[i]) else -1e9,
            reverse=True,
        )[:3]
        lineas_top = []
        for rank, i in enumerate(top3, start=1):
            lineas_top.append(
                f"    #{rank}  desfase={shifts[i]:+d}  "
                f"r={fmt(rs[i])}  GLK={fmt(glks[i])}  "
                f"t-BP={fmt(tbps[i], 2)}  puntaje={fmt(scores[i])}  "
                f"n={ns[i]}"
            )

        self.txt_info.append(
            f"\nComparación '{nombre}' vs cronología:\n"
            f"  Mejor desfase: {best_shift:+d}\n"
            f"    r       = {fmt(best_r)}\n"
            f"    GLK     = {fmt(best_glk)}\n"
            f"    t-BP    = {fmt(best_tbp, 2)}\n"
            f"    puntaje = {fmt(best_score)}  "
            f"(media geom. de las 3 métricas normalizadas)\n"
            f"    n       = {best_n} años de solape\n"
            f"  Top 3 desfases por puntaje compuesto:\n"
            + "\n".join(lineas_top)
            + "\n"
        )

        # Mostrar el botón "Ver tabla detallada" si está oculto
        if hasattr(self, "_btn_tabla_comparacion"):
            self._btn_tabla_comparacion.setVisible(True)

    def _comparar_multiple_con_cronologia(self, nombres: list[str]):
        """Modo batch: N>1 series → tabla resumen con una fila por serie.

        Para cada serie ejecuta `_estadisticos_por_desfase` (mismo cálculo
        que el modo individual) y guarda el resultado completo + el "mejor"
        según score compuesto + el score en shift=0 para calcular el Δ.

        Al terminar abre `DialogoComparacionMultiple` que permite ordenar
        por cualquier columna y hacer drill-down con doble clic.
        """
        meta = self.cronologia_meta
        col_val = meta.get("columna_valor") or self.cronologia_df.columns[0]
        if col_val not in self.cronologia_df.columns:
            col_val = self.cronologia_df.columns[0]
        s_b = pd.to_numeric(self.cronologia_df[col_val],
                             errors="coerce").dropna()
        max_shift_val = self.spin_shift.value()
        min_overlap_val = self.spin_min_overlap.value()

        # Cursor de espera durante el cálculo (puede tomar segundos con
        # decenas de series y rangos de shift amplios)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        resultados = []
        nombre_crono = meta.get("nombre", "cronología")

        try:
            for nombre in nombres:
                serie = self._series_cronologia[nombre]
                s_a = _serie_transformada_para_comparar(serie, meta)

                shifts, rs, glks, tbps, ns = _estadisticos_por_desfase(
                    s_a, s_b, max_shift_val, min_overlap_val,
                )

                if not shifts:
                    # Serie sin solape suficiente → registrar igualmente
                    # para que el usuario sepa cuáles quedaron fuera
                    resultados.append({
                        "nombre_serie": nombre,
                        "shifts": [], "rs": [], "glks": [],
                        "tbps": [], "ns": [],
                        "mejor_shift": 0,
                        "mejor_score": float("nan"),
                        "mejor_r": float("nan"),
                        "mejor_glk": float("nan"),
                        "mejor_tbp": float("nan"),
                        "mejor_n": 0,
                        "score_shift0": float("nan"),
                    })
                    continue

                # Score compuesto por shift + selección del mejor
                scores = [score_compuesto(r, g, t)
                          for r, g, t in zip(rs, glks, tbps)]
                scores_validos = [
                    (i, s) for i, s in enumerate(scores) if np.isfinite(s)]
                if scores_validos:
                    idx_mejor = max(scores_validos, key=lambda x: x[1])[0]
                else:
                    idx_mejor = int(np.argmax(rs))

                # Score en shift=0 (para calcular Δ vs el mejor).
                # Si shift=0 no está en la lista (no hubo overlap
                # suficiente sin shift), Δ queda NaN.
                if 0 in shifts:
                    idx0 = shifts.index(0)
                    score_shift0 = scores[idx0]
                else:
                    score_shift0 = float("nan")

                resultados.append({
                    "nombre_serie": nombre,
                    "shifts": shifts, "rs": rs, "glks": glks,
                    "tbps": tbps, "ns": ns,
                    "mejor_shift": shifts[idx_mejor],
                    "mejor_score": scores[idx_mejor],
                    "mejor_r": rs[idx_mejor],
                    "mejor_glk": glks[idx_mejor],
                    "mejor_tbp": tbps[idx_mejor],
                    "mejor_n": ns[idx_mejor],
                    "score_shift0": score_shift0,
                    # Series transformadas para el gráfico de detalle
                    "serie_a": s_a,
                    "serie_b": s_b,
                })

                # Refrescar UI para evitar congelamiento percibido en
                # series largas o muchas series. processEvents() es
                # suficiente acá; no necesitamos hilos para N modesto.
                QApplication.processEvents()
        finally:
            QApplication.restoreOverrideCursor()

        # Resumen de una línea en txt_info
        n_validas = sum(1 for r in resultados if np.isfinite(r["mejor_score"]))
        self.txt_info.append(
            f"\nComparación múltiple ({len(resultados)} series) vs "
            f"cronología '{nombre_crono}':\n"
            f"  {n_validas} con resultado válido  ·  "
            f"{len(resultados) - n_validas} sin solape suficiente\n"
            f"  → ver tabla resumen para análisis detallado.\n"
        )

        # Abrir el diálogo modal
        dlg = DialogoComparacionMultiple(resultados, nombre_crono, parent=self)
        dlg.exec()

    def _abrir_tabla_comparacion(self):
        """Abre VentanaTablaOffset con los resultados de la última
        `comparar_con_cronologia`. Reutilizamos exactamente la misma tabla
        que ya existe para series flotantes (con colores por umbral,
        ordenable, score compuesto destacado)."""
        datos = getattr(self, "_ultimo_resultado_comparacion", None)
        if not datos:
            QMessageBox.information(
                self, "Sin datos",
                "Ejecuta primero una comparación con la cronología.")
            return
        dlg = VentanaTablaOffset(
            datos["shifts"], datos["rs"],
            glks=datos["glks"], tbps=datos["tbps"], ns=datos["ns"],
            parent=self,
        )
        dlg.setWindowTitle(
            f"Resultados detallados — '{datos['nombre_serie']}' vs cronología"
        )
        dlg.exec()

    def _pintar_cronologia(self):
        if self.cronologia_df is None or self.cronologia_df.empty:
            self.grafico.clear()
            return
        self.grafico.clear()

        colores_tipo = {"Raw": "#FF8C00", "Standard": "#3388FF", "Residual": "#5cb85c"}

        cols_disponibles = self.cronologia_meta.get("columnas_disponibles", [])
        if not cols_disponibles:
            col_val = self.cronologia_meta.get("columna_valor", "")
            if col_val and col_val in self.cronologia_df.columns:
                cols_disponibles = [col_val]
            else:
                cols_disponibles = [c for c in self.cronologia_df.columns
                                    if c.lower() not in ("n", "sd", "se")][:1]

        x = self.cronologia_df.index.to_numpy(dtype=float)
        for col in cols_disponibles:
            if col in self.cronologia_df.columns:
                y = pd.to_numeric(self.cronologia_df[col], errors="coerce").to_numpy(dtype=float)
                color = colores_tipo.get(col, "#FF8C00")
                self.grafico.plot(x, y, pen=pg.mkPen(color, width=2), name=col)

        self.grafico.showGrid(x=True, y=True, alpha=0.3)
        self.grafico.autoRange()

        if self.eps_movil is not None and not self.eps_movil.empty:
            try:
                eps = self.eps_movil["EPS"].dropna()
                if not eps.empty:
                    self.grafico.plot(
                        eps.index.to_numpy(), eps.to_numpy(),
                        pen=pg.mkPen("#5cb85c", width=1, style=Qt.PenStyle.DashLine),
                        name="EPS"
                    )
                    self.grafico.addItem(pg.InfiniteLine(
                        pos=0.85, angle=0,
                        pen=pg.mkPen("#888888", style=Qt.PenStyle.DashLine)))
            except Exception:
                pass

    def _actualizar_info(self):
        if self.cronologia_df is None:
            self.txt_info.setPlainText("Sin cronología cargada.")
            return
        meta = self.cronologia_meta or {}
        lines = [
            f"Nombre: {meta.get('nombre', 'Cronología')}",
            f"Tipo: {meta.get('tipo_cronologia', 'raw')}",
            f"Método: {meta.get('metodo_estandarizacion', 'raw')}",
            f"Agregación: {meta.get('agregacion', 'mean')}",
            f"Series usadas: {meta.get('n_series', 0)}",
            f"Años: {meta.get('n_anios', len(self.cronologia_df))}",
            f"Archivo: {meta.get('archivo', '—')}",
            "",
            f"Rbar: {meta.get('Rbar', float('nan')):.3f}",
            f"EPS: {meta.get('EPS', float('nan')):.3f}",
        ]
        self.txt_info.setPlainText("\n".join(lines))
