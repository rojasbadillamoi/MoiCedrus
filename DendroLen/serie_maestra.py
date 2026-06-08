# serie_maestra.py — DendroLen
# Panel de co-datación en vivo con serie maestra.
# Algoritmos adaptados del modulo_medicion.py de DPI.

import os
import re
import numpy as np
import pandas as pd

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QFrame, QSizePolicy, QComboBox
)
from PyQt6.QtCore import Qt, QTimer

import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.patches as mpatches

from tema_manager import tema as _tema_mgr


# ─────────────────────────────────────────────────────────────────────────────
# LECTORES DE FORMATO
# ─────────────────────────────────────────────────────────────────────────────

def leer_rwl(ruta: str) -> pd.Series:
    """
    Lee un archivo Tucson (.rwl / .txt / .TXT).
    Robusto ante variantes de formato: ignora comentarios, líneas cortas,
    y valores reservados (-9999, 999).
    Devuelve pd.Series con índice = año (int), valores = mm (float).
    """
    from constantes import TUCSON_SAMPLE_ID_WIDTH, TUCSON_TERMINATOR
    datos = {}
    with open(ruta, encoding="utf-8", errors="ignore") as f:
        for linea in f:
            linea = linea.rstrip()
            if not linea or linea.startswith(";"):
                continue
            # Necesitamos al menos código(8) + año(4) + un valor(6) = 18 chars
            if len(linea) < 18:
                continue
            # Extraer año de la posición 8:12
            try:
                anio = int(linea[8:12].strip())
            except (ValueError, IndexError):
                continue
            # Validar rango de año razonable
            if not (-10000 <= anio <= 2200):
                continue
            # Leer valores en campos de 6 chars a partir de la posición 12
            pos = 12
            while pos + 6 <= len(linea):
                campo = linea[pos:pos+6].strip()
                if not campo:
                    pos += 6
                    continue
                try:
                    v = int(campo)
                except ValueError:
                    break
                # Terminadores
                if v == TUCSON_TERMINATOR or v == -9999:
                    break
                # Valor 999 es reservado (reemplazado por 998 al escribir),
                # pero al leer lo aceptamos como medición válida
                if v > 0:
                    datos[anio] = v / 1000.0   # milésimas → mm
                anio += 1
                pos += 6
    if not datos:
        raise ValueError(f"No se encontraron mediciones en {os.path.basename(ruta)}")
    return pd.Series(datos).sort_index()


def leer_wid(ruta: str) -> pd.Series:
    """
    Lee un archivo CooRecorder (.wid).
    Formato: líneas con "año valor" o CSV simple.
    """
    datos = {}
    with open(ruta, encoding="utf-8", errors="ignore") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#"):
                continue
            partes = re.split(r"[\s,;]+", linea)
            if len(partes) >= 2:
                try:
                    anio = int(partes[0])
                    val  = float(partes[1])
                    datos[anio] = val
                except ValueError:
                    continue
    if not datos:
        raise ValueError(f"No se encontraron datos en {ruta}")
    return pd.Series(datos).sort_index()


def leer_xlsx_csv(ruta: str) -> pd.Series:
    """
    Lee un archivo Excel (.xlsx) o CSV (.csv) con co-datación.

    Formato esperado: cualquier tabla donde:
      - La PRIMERA columna contiene los años (enteros).
        El encabezado puede llamarse 'año', 'year', 'yr', 'Year', 'AÑO', etc.
      - La SEGUNDA columna contiene los anchos de anillo en mm.
        El encabezado puede tener cualquier nombre.

    La función ignora el nombre de los encabezados y trabaja por posición.
    Las filas con años o valores no numéricos se descartan automáticamente.
    El solapamiento temporal con la serie en medición se calcula en tiempo real,
    por lo que no es necesario recortar la serie maestra — puede tener miles de años.
    """
    ext = os.path.splitext(ruta)[1].lower()
    if ext in (".xlsx", ".xls", ".xlsm"):
        df = pd.read_excel(ruta, header=0)
    else:
        # CSV: intentar detectar separador automáticamente
        try:
            df = pd.read_csv(ruta, sep=None, engine="python", header=0)
        except Exception:
            df = pd.read_csv(ruta, sep=",", header=0)

    if df.shape[1] < 2:
        raise ValueError("El archivo debe tener al menos dos columnas (año, valor).")

    # Usar las dos primeras columnas independientemente del nombre
    col_anio = df.iloc[:, 0]
    col_val  = df.iloc[:, 1]

    # Convertir a numérico, descartar filas no numéricas
    anios = pd.to_numeric(col_anio, errors="coerce")
    vals  = pd.to_numeric(col_val,  errors="coerce")

    mask  = anios.notna() & vals.notna()
    anios = anios[mask].astype(int)
    vals  = vals[mask].astype(float)

    if len(anios) == 0:
        raise ValueError("No se encontraron datos numéricos válidos en las dos primeras columnas.")

    serie = pd.Series(vals.values, index=anios.values).sort_index()
    # Eliminar duplicados de año conservando el primero
    serie = serie[~serie.index.duplicated(keep="first")]
    return serie


def leer_serie_maestra(ruta: str) -> tuple[pd.Series, str]:
    """
    Auto-detecta el formato y devuelve (serie, nombre).
    Soporta: Tucson .rwl/.txt, CooRecorder .wid, Excel .xlsx, CSV .csv
    """
    ext    = os.path.splitext(ruta)[1].lower()
    nombre = os.path.splitext(os.path.basename(ruta))[0]
    if ext in (".rwl", ".txt"):
        return leer_rwl(ruta), nombre
    elif ext == ".wid":
        return leer_wid(ruta), nombre
    elif ext in (".xlsx", ".xls", ".xlsm", ".csv"):
        return leer_xlsx_csv(ruta), nombre
    else:
        # Fallback: probar todos los formatos
        for fn in [leer_rwl, leer_wid, leer_xlsx_csv]:
            try:
                return fn(ruta), nombre
            except Exception:
                continue
        raise ValueError(f"No se pudo leer el archivo: {os.path.basename(ruta)}")


# ─────────────────────────────────────────────────────────────────────────────
# ESTADÍSTICAS DE CORRELACIÓN
# ─────────────────────────────────────────────────────────────────────────────

def _diff_log(serie: pd.Series) -> pd.Series:
    return np.log(serie.clip(lower=0.01)).diff().dropna()


def r_global(anios_a, vals_a, anios_b, vals_b) -> float | None:
    """r de Pearson sobre diferencias log en el solapamiento completo."""
    if len(anios_a) < 5 or len(anios_b) < 5:
        return None
    da = _diff_log(pd.Series(vals_a, index=anios_a))
    db = _diff_log(pd.Series(vals_b, index=anios_b))
    idx = da.index.intersection(db.index)
    if len(idx) < 5:
        return None
    r = np.corrcoef(da[idx].values, db[idx].values)[0, 1]
    return None if np.isnan(r) else float(r)


def r_deslizante(anios_a, vals_a, anios_b, vals_b, ventana: int) -> tuple[list, list]:
    """
    Correlación deslizante estilo COFECHA.
    Paso = ventana // 2. Devuelve (años_centro, r_vals).
    """
    MIN_V = 10
    if len(anios_a) < MIN_V or len(anios_b) < MIN_V:
        return [], []
    da = _diff_log(pd.Series(vals_a, index=anios_a))
    db = _diff_log(pd.Series(vals_b, index=anios_b))
    overlap = sorted(da.index.intersection(db.index))
    n = len(overlap)
    if n < MIN_V:
        return [], []

    años_c, r_vals = [], []
    paso = max(1, ventana // 2)

    if n <= ventana:
        a = da.loc[overlap].values
        b = db.loc[overlap].values
        if np.std(a) > 1e-9 and np.std(b) > 1e-9:
            r = float(np.corrcoef(a, b)[0, 1])
            if not np.isnan(r):
                años_c.append(overlap[n // 2])
                r_vals.append(r)
    else:
        k = 0
        while k + ventana <= n:
            seg = overlap[k: k + ventana]
            a = da.loc[seg].values
            b = db.loc[seg].values
            if np.std(a) > 1e-9 and np.std(b) > 1e-9:
                r = float(np.corrcoef(a, b)[0, 1])
                if not np.isnan(r):
                    años_c.append(seg[ventana // 2])
                    r_vals.append(r)
            k += paso

    return años_c, r_vals


def normalizar_zscore(valores: list) -> list:
    arr = np.array(valores, dtype=float)
    std = arr.std()
    if std < 1e-9:
        return [0.0] * len(valores)
    return ((arr - arr.mean()) / std).tolist()


def color_r(r) -> str:
    if r is None:   return "#666666"
    if r >= 0.50:   return "#2ecc71"
    if r >= 0.35:   return "#5cb85c"
    if r >= 0.15:   return "#f0ad4e"
    return "#d9534f"


def simbolo_r(r) -> str:
    if r is None:   return "○○○"
    if r >= 0.50:   return "●●●"
    if r >= 0.35:   return "●●○"
    if r >= 0.15:   return "●○○"
    return "○○○"


# ─────────────────────────────────────────────────────────────────────────────
# PANEL DE CO-DATACIÓN
# ─────────────────────────────────────────────────────────────────────────────

class PanelMaestra(QWidget):
    """
    Panel de co-datación en vivo.
    Se conecta a PanelMedicion y recibe las mediciones actuales.
    Muestra la serie en construcción vs la serie maestra cargada.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._serie_maestra: pd.Series | None = None
        self._nombre_maestra = ""
        self._meds: list[int] = []
        self._anio_inicio = 1850
        self._serie_nombre = "En medición"
        self._ventana = 30
        self._eventos: dict = {}   # anomalías de la serie en medición
        self._zoom_activo = False   # True cuando el usuario hace scroll
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.timeout.connect(self._redibujar)
        self._build_ui()
        _tema_mgr.suscribir(self._aplicar_tema)
        self._aplicar_tema(_tema_mgr.t)

    # ── Construcción ──────────────────────────────────────────────────────────
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Header
        hdr = QWidget()
        hdr.setObjectName("maestra_hdr")
        hdr.setFixedHeight(36)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 0, 12, 0)
        hl.setSpacing(8)

        dot = QLabel("⬤"); dot.setObjectName("dot_teal")
        title = QLabel("SERIE MAESTRA · CO-DATACIÓN EN VIVO")
        title.setObjectName("maestra_title")
        hl.addWidget(dot); hl.addWidget(title)
        hl.addStretch()

        # Ventana de correlación
        lbl_vent = QLabel("Ventana:")
        lbl_vent.setObjectName("maestra_lbl")
        hl.addWidget(lbl_vent)
        self.cmb_ventana = QComboBox()
        self.cmb_ventana.setObjectName("maestra_spin")
        for v in [5, 10, 15, 20, 30, 50, 100]:
            self.cmb_ventana.addItem(f"{v} años", v)
        self.cmb_ventana.setCurrentIndex(4)   # 30 años por defecto
        self.cmb_ventana.setFixedWidth(90)
        self.cmb_ventana.currentIndexChanged.connect(self._on_ventana_changed)
        hl.addWidget(self.cmb_ventana)

        # Botones cargar
        self.btn_rwl = QPushButton("📂 Cargar maestra")
        self.btn_rwl.setObjectName("maestra_btn")
        self.btn_rwl.setFixedHeight(26)
        self.btn_rwl.clicked.connect(self._cargar_maestra)
        hl.addWidget(self.btn_rwl)

        self.btn_limpiar = QPushButton("✕")
        self.btn_limpiar.setObjectName("maestra_btn_x")
        self.btn_limpiar.setFixedSize(26, 26)
        self.btn_limpiar.setToolTip("Quitar serie maestra")
        self.btn_limpiar.clicked.connect(self._limpiar_maestra)
        hl.addWidget(self.btn_limpiar)

        lay.addWidget(hdr)

        # Etiqueta de maestra cargada
        self.lbl_maestra = QLabel("  Sin serie maestra cargada")
        self.lbl_maestra.setObjectName("maestra_nombre")
        self.lbl_maestra.setFixedHeight(22)
        lay.addWidget(self.lbl_maestra)

        # Canvas matplotlib
        self.fig = Figure(figsize=(10, 3.5))
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setObjectName("maestra_canvas")
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(self.canvas)

        # Barra de correlaciones
        self.lbl_r_bar = QLabel("")
        self.lbl_r_bar.setObjectName("maestra_r_bar")
        self.lbl_r_bar.setFixedHeight(28)
        self.lbl_r_bar.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_r_bar.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.lbl_r_bar)

    # ── Tema ──────────────────────────────────────────────────────────────────
    def _aplicar_tema(self, t=None):
        if t is None:
            t = _tema_mgr.t
        self.setStyleSheet(f"""
            QWidget#maestra_hdr {{
                background-color: {t['BG_BAR']};
                border-bottom: 1px solid {t['BORDER']};
                border-top: 1px solid {t['BORDER']};
            }}
            QLabel#maestra_title {{
                font-size: 10px; color: {t['TEXT_DIM']};
                letter-spacing: 1px; font-weight: 700;
                background: transparent;
            }}
            QLabel#dot_teal {{
                font-size: 8px; color: {t['TEAL'] if 'TEAL' in t else t['GREEN']};
                background: transparent;
            }}
            QLabel#maestra_lbl {{
                font-size: 12px; color: {t['TEXT_DIM']};
                background: transparent;
            }}
            QLabel#maestra_nombre {{
                font-size: 12px; color: {t['TEXT_DIM']};
                background-color: {t['BG_BAR']};
                padding-left: 14px;
                border-bottom: 1px solid {t['BORDER']};
            }}
            QLabel#maestra_r_bar {{
                font-size: 12px; background-color: {t['BG_BAR']};
                border-top: 1px solid {t['BORDER']};
                padding-left: 14px;
            }}
            QPushButton#maestra_btn {{
                background-color: {t['BG_CARD']};
                border: 1px solid {t['BORDER']};
                border-radius: 5px;
                color: {t['BLUE']};
                font-size: 12px; padding: 0 8px; min-height: 0;
            }}
            QPushButton#maestra_btn:hover {{
                border-color: {t['BLUE']};
            }}
            QPushButton#maestra_btn_x {{
                background-color: {t['BG_CARD']};
                border: 1px solid {t['BORDER']};
                border-radius: 5px;
                color: {t['TEXT_DIM']};
                font-size: 12px; padding: 0; min-height: 0;
            }}
            QPushButton#maestra_btn_x:hover {{ color: {t['RED']}; border-color: {t['RED']}; }}
            QComboBox#maestra_spin {{
                background-color: {t['BG_CARD']};
                color: {t['TEXT_TITLE']};
                border: 1px solid {t['BORDER']};
                border-radius: 4px;
                font-size: 12px; padding: 2px 6px;
                min-height: 0;
            }}
            QComboBox#maestra_spin::drop-down {{ border: none; width: 18px; }}
            QComboBox#maestra_spin QAbstractItemView {{
                background-color: {t['BG_CARD']};
                color: {t['TEXT_TITLE']};
                border: 1px solid {t['BORDER']};
                selection-background-color: {t['BLUE']};
                selection-color: #ffffff;
                font-size: 12px;
            }}
            FigureCanvas {{ background-color: {t['BG']}; border: none; }}
        """)
        self.fig.patch.set_facecolor(t['BG'])
        self.canvas.setStyleSheet(f"background-color: {t['BG']}; border: none;")
        self._redibujar()

    # ── API pública ───────────────────────────────────────────────────────────
    def actualizar_mediciones(self, meds: list, anio_inicio: int,
                              serie_nombre: str, eventos: dict = None):
        """Llamado desde PanelMedicion en cada nueva medición."""
        self._meds = list(meds)
        self._anio_inicio = anio_inicio
        self._serie_nombre = serie_nombre
        self._eventos = dict(eventos) if eventos else {}
        # Throttle: no redibujar más de una vez por segundo
        if not self._update_timer.isActive():
            self._update_timer.start(800)

    def resetear_zoom(self):
        """Vuelve al autorange — doble clic en el gráfico."""
        self._zoom_activo = False
        self._redibujar()

    def _on_ventana_changed(self, _idx=None):
        self._ventana = self.cmb_ventana.currentData()
        self._redibujar()

    # ── Carga de maestra ──────────────────────────────────────────────────────
    def _cargar_maestra(self):
        t = _tema_mgr.t
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Cargar serie de referencia",
            os.path.expanduser("~"),
            "Tucson / CooRecorder (*.rwl *.txt *.TXT *.wid);;Excel (*.xlsx *.xls);;CSV (*.csv);;Todos los archivos (*)",
            options=QFileDialog.Option.DontUseNativeDialog
        )
        if not ruta:
            return
        try:
            serie, nombre = leer_serie_maestra(ruta)
            self._serie_maestra = serie
            self._nombre_maestra = nombre
            self.lbl_maestra.setText(
                f"  🌲  {nombre}  ·  {len(serie)} anillos  "
                f"({int(serie.index.min())}–{int(serie.index.max())})")
            self.lbl_maestra.setStyleSheet(
                f"font-size:12px;color:{t['GREEN']};background-color:{t['BG_BAR']};"
                f"padding-left:14px;border-bottom:1px solid {t['BORDER']};")
            self._redibujar()
        except Exception as e:
            self.lbl_maestra.setText(f"  ⚠  Error: {e}")
            self.lbl_maestra.setStyleSheet(
                f"font-size:12px;color:{t['RED']};background-color:{t['BG_BAR']};"
                f"padding-left:14px;border-bottom:1px solid {t['BORDER']};")

    def _limpiar_maestra(self):
        self._serie_maestra = None
        self._nombre_maestra = ""
        t = _tema_mgr.t
        self.lbl_maestra.setText("  Sin serie maestra cargada")
        self.lbl_maestra.setStyleSheet(
            f"font-size:12px;color:{t['TEXT_DIM']};background-color:{t['BG_BAR']};"
            f"padding-left:14px;border-bottom:1px solid {t['BORDER']};")
        self.lbl_r_bar.setText("")
        self._redibujar()

    # ── Dibujo ────────────────────────────────────────────────────────────────
    def _redibujar(self):
        t = _tema_mgr.t
        self.fig.clear()

        if not self._meds:
            self._dibujar_vacio(t)
            return

        anios_a = list(range(self._anio_inicio,
                             self._anio_inicio + len(self._meds)))
        vals_a  = [m / 1000.0 for m in self._meds]
        tiene_maestra = (self._serie_maestra is not None
                         and len(self._serie_maestra) > 0)

        if not tiene_maestra:
            # Solo mostrar la serie en construcción
            ax = self.fig.add_subplot(111)
            self._estilo_ax(ax, t)
            ax.plot(anios_a, vals_a,
                    color=t['BLUE'], linewidth=1.5, label=self._serie_nombre)
            ax.fill_between(anios_a, vals_a, alpha=0.12, color=t['BLUE'])
            ax.scatter([anios_a[-1]], [vals_a[-1]],
                       color=t['GREEN'], s=30, zorder=5)
            ax.legend(facecolor=t['BG'], edgecolor=t['BORDER'],
                      labelcolor=t['TEXT'], fontsize=9)
            self.lbl_r_bar.setText(
                f"<span style='color:{t['TEXT_DIM']}'>Sin serie maestra cargada — "
                f"usa el botón 📂 para cargar un .rwl o .wid</span>")
            self.fig.tight_layout(pad=0.8)
            self.canvas.draw()
            return

        # ── Con serie maestra ─────────────────────────────────────────────────
        anios_m = list(self._serie_maestra.index.astype(int))
        vals_m  = list(self._serie_maestra.values.astype(float))

        # Normalizar si hay solapamiento
        overlap_a = [v for a, v in zip(anios_a, vals_a) if a in self._serie_maestra.index]
        normalizar = len(overlap_a) >= 5

        y_a = normalizar_zscore(vals_a) if normalizar else vals_a
        y_m = normalizar_zscore(vals_m) if normalizar else vals_m

        # ── Un solo gráfico con zoom interactivo ─────────────────────────────
        ax1 = self.fig.add_subplot(111)
        self.fig.subplots_adjust(left=0.07, right=0.97, top=0.93, bottom=0.12)

        self._estilo_ax(ax1, t)
        ax1.plot(anios_m, y_m, color=t['ORANGE'], linewidth=1.2,
                 linestyle='--', alpha=0.8, label=f"↑ {self._nombre_maestra}")
        ax1.plot(anios_a, y_a, color=t['BLUE'], linewidth=1.8,
                 label=self._serie_nombre)
        ax1.scatter([anios_a[-1]], [y_a[-1]],
                    color=t['GREEN'], s=35, zorder=5)

        # ── Marcas de anomalías ────────────────────────────────────────────
        from constantes import ANOMALIA_COLORES
        for year, cod in self._eventos.items():
            if anios_a[0] <= year <= anios_a[-1]:
                color_anom = ANOMALIA_COLORES.get(cod, t['ORANGE'])
                ax1.axvline(x=year, color=color_anom,
                            linewidth=1.2, alpha=0.8, linestyle='--', zorder=4)
                # Etiqueta del tipo de anomalía en la parte superior
                idx = year - anios_a[0]
                if 0 <= idx < len(y_a):
                    ax1.text(year, ax1.get_ylim()[1] if ax1.get_ylim()[1] != 1 else 1,
                             cod, ha='center', va='top', fontsize=7,
                             color=color_anom, fontweight='bold',
                             bbox=dict(boxstyle='round,pad=0.1',
                                       facecolor=t['BG'],
                                       edgecolor=color_anom,
                                       alpha=0.8, linewidth=0.6))

        ax1.set_ylabel("z-score" if normalizar else "mm", color=t['TEXT'], fontsize=9)
        ax1.set_xlabel("Año", color=t['TEXT'], fontsize=9)
        ax1.legend(facecolor=t['BG_CARD'], edgecolor=t['BORDER'],
                   labelcolor=t['TEXT'], fontsize=9, loc='upper left')

        # Segmentos de correlación — fondo coloreado + etiqueta al fondo
        ventana = self._ventana
        paso    = max(5, ventana // 2)
        años_r, vals_r = r_deslizante(anios_a, vals_a, anios_m, vals_m, ventana)

        # Primero dibujar todos los fondos (detrás de las líneas)
        if años_r:
            y_min, y_max = ax1.get_ylim()
            for centro, r in zip(años_r, vals_r):
                seg_ini = centro - paso // 2
                seg_fin = centro + paso // 2
                ax1.axvspan(seg_ini, seg_fin,
                            color=self._rgba_r(r, as_mpl=True),
                            alpha=0.20, linewidth=0, zorder=1)

            # Redibujar líneas encima de los fondos
            ax1.set_zorder(2)

            # Etiquetas al fondo del gráfico con transform para posición fija en Y
            y_min, y_max = ax1.get_ylim()
            for centro, r in zip(años_r, vals_r):
                ax1.text(centro, y_min,
                         f"r={r:+.2f}",
                         ha='center', va='bottom', fontsize=7,
                         color=color_r(r), fontweight='bold',
                         zorder=10,
                         bbox=dict(boxstyle='round,pad=0.15',
                                   facecolor=t['BG'],
                                   edgecolor=color_r(r),
                                   alpha=0.85, linewidth=0.8))
        elif len(anios_a) >= 5:
            ax1.text(0.5, 0.02,
                     f"Se necesitan al menos {ventana} años de solapamiento",
                     ha='center', va='bottom', transform=ax1.transAxes,
                     color=t['TEXT_DIM'], fontsize=9)

        # Zoom: restaurar rango si el usuario había hecho scroll
        if self._zoom_activo and hasattr(self, '_xlim_guardado'):
            ax1.set_xlim(self._xlim_guardado)
        else:
            # Autorange: asegurar que toda la serie en medición sea visible
            # añadiendo un margen del 5% a cada lado
            all_years = anios_a + anios_m
            if all_years:
                yr_min, yr_max = min(all_years), max(all_years)
                margin = max(5, (yr_max - yr_min) * 0.03)
                ax1.set_xlim(yr_min - margin, yr_max + margin)

        # Conectar eventos de interacción
        self.canvas.mpl_connect('scroll_event', self._on_scroll)
        self.canvas.mpl_connect('button_press_event', self._on_dblclick)

        self.canvas.draw()

        # ── Texto de correlaciones ────────────────────────────────────────────
        r_tot = r_global(anios_a, vals_a, anios_m, vals_m)
        sa = pd.Series(vals_a, index=anios_a)
        sm = pd.Series(vals_m, index=anios_m)
        n_ov = len(sa.index.intersection(sm.index))
        sim = simbolo_r(r_tot)
        cr  = color_r(r_tot)
        val_txt = f"{r_tot:+.3f}" if r_tot is not None else "—"

        # Ventanas: 10, 20, 30 ... hasta ventana
        ventanas_txt = range(10, ventana + 1, 10)
        partes_vent = []
        for v in ventanas_txt:
            yr, rr = r_deslizante(anios_a, vals_a, anios_m, vals_m, v)
            if yr:
                r_v = float(np.mean(rr))
                partes_vent.append(
                    f"<span style='color:{t['TEXT_DIM']}'>{v}a:</span>"
                    f"<span style='color:{color_r(r_v)}'>&nbsp;{r_v:+.2f}</span>"
                )
            else:
                partes_vent.append(
                    f"<span style='color:{t['TEXT_DIM']}'>{v}a:&nbsp;—</span>")

        html = (
            f"<span style='color:{t['BLUE']}'><b>{self._serie_nombre}</b></span>"
            f"&nbsp;vs&nbsp;"
            f"<span style='color:{t['ORANGE']}'><b>{self._nombre_maestra}</b></span>"
            f"&nbsp;&nbsp;"
            f"<span style='color:{cr}'>{sim}&nbsp;r={val_txt}</span>"
            f"&nbsp;&nbsp;"
            f"<span style='color:{t['TEXT_DIM']}'>({n_ov} años solapamiento)</span>"
            f"&nbsp;&nbsp;&nbsp;"
            + "&nbsp;&nbsp;".join(partes_vent)
        )
        self.lbl_r_bar.setText(html)

    def _dibujar_vacio(self, t):
        ax = self.fig.add_subplot(111)
        self._estilo_ax(ax, t)
        ax.text(0.5, 0.5,
                "Las mediciones aparecerán aquí en tiempo real",
                ha='center', va='center', transform=ax.transAxes,
                color=t['TEXT_DIM'], fontsize=11)
        self.canvas.draw()

    def _estilo_ax(self, ax, t):
        ax.set_facecolor(t['BG'])
        self.fig.patch.set_facecolor(t['BG'])
        ax.tick_params(colors=t['TEXT'], labelcolor=t['TEXT'], labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor(t['BORDER'])
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        for lbl in ax.get_xticklabels() + ax.get_yticklabels():
            lbl.set_color(t['TEXT'])

    def _rgba_r(self, r, as_mpl=False):
        if r >= 0.50:   c = (46, 204, 113)
        elif r >= 0.35: c = (92, 184, 92)
        elif r >= 0.15: c = (240, 173, 78)
        else:           c = (217, 83, 79)
        if as_mpl:
            return tuple(x/255 for x in c)
        return c

    def _on_scroll(self, event):
        """Zoom con rueda del mouse centrado en la posición del cursor."""
        ax = event.inaxes
        if ax is None:
            return
        factor = 0.85 if event.button == 'up' else 1.15
        xmin, xmax = ax.get_xlim()
        ymin, ymax = ax.get_ylim()
        xdata, ydata = event.xdata, event.ydata
        if xdata is None or ydata is None:
            return
        new_xmin = xdata - (xdata - xmin) * factor
        new_xmax = xdata + (xmax - xdata) * factor
        ax.set_xlim([new_xmin, new_xmax])
        ax.set_ylim([ydata - (ydata - ymin) * factor,
                     ydata + (ymax - ydata) * factor])
        # Marcar que el usuario está en zoom manual
        self._zoom_activo = True
        self._xlim_guardado = (new_xmin, new_xmax)
        self.canvas.draw_idle()

    def _on_dblclick(self, event):
        """Doble clic: volver al autorange."""
        if event.dblclick:
            self._zoom_activo = False
            if hasattr(self, '_xlim_guardado'):
                del self._xlim_guardado
            self._redibujar()

    def closeEvent(self, event):
        _tema_mgr.desuscribir(self._aplicar_tema)
        super().closeEvent(event)


# Parche para poder hacer QLabel(...).also(lambda l: ...) inline
_orig_init = QLabel.__init__
def _qlabel_also(self, op):
    op(self)
    return self
QLabel.also = _qlabel_also
