"""
modulo_autodeteccion.py — Detección automática de límites de anillos

Pipeline:
  1. Usuario marca médula y corteza con 2 clics (modo MODO_AUTODETECT)
  2. Se extrae el perfil de intensidad a lo largo de esa línea,
     promediando una banda de N píxeles de ancho (configurable)
  3. Se detectan mínimos/máximos locales según parámetros de especie
  4. Se colocan los puntos automáticamente en la VistaInteractiva
  5. El usuario corrige con las herramientas existentes

Perfiles de especie se guardan en species_profiles.json junto al ejecutable.
"""

import os
import json
import math
import logging

import cv2
import numpy as np
from scipy.signal import find_peaks, savgol_filter

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QDialogButtonBox, QGroupBox, QSlider, QMessageBox,
    QInputDialog, QFormLayout, QWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPen, QColor

import pyqtgraph as pg

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Ruta del archivo de perfiles
# ------------------------------------------------------------------
_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_PERFILES = os.path.join(_DIR, "species_profiles.json")

# Perfil por defecto (coníferas con escáner plano)
PERFIL_DEFECTO = {
    "canal":           "gray",   # "gray" | "red" | "green" | "blue"
    "invertir":        False,    # True si los límites son claros (madera temprana oscura)
    "suavizado":       7,        # Ventana Savitzky-Golay (px, impar)
    "prominencia":     12.0,     # Prominencia mínima del pico (0-255)
    "anchura_min_px":  15,       # Ancho mínimo del anillo en píxeles
    "anchura_max_px":  3000,     # Ancho máximo del anillo en píxeles
    "banda_px":        5,        # Semi-ancho de la banda de muestreo
}


# =============================================================================
# GESTIÓN DE PERFILES DE ESPECIE
# =============================================================================

def cargar_perfiles() -> dict:
    if os.path.exists(RUTA_PERFILES):
        try:
            with open(RUTA_PERFILES, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("No se pudo leer species_profiles.json: %s", exc)
    return {"Conífera (defecto)": PERFIL_DEFECTO.copy()}


def guardar_perfiles(perfiles: dict):
    try:
        with open(RUTA_PERFILES, "w", encoding="utf-8") as f:
            json.dump(perfiles, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.error("No se pudo guardar species_profiles.json: %s", exc)


# =============================================================================
# EXTRACCIÓN DE PERFIL DE INTENSIDAD
# =============================================================================

def extraer_perfil(
    imagen_bgr: np.ndarray,
    p_inicio: tuple[float, float],
    p_fin: tuple[float, float],
    canal: str,
    banda_px: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Extrae el perfil de intensidad a lo largo del segmento p_inicio→p_fin.

    Promedia `banda_px` píxeles a cada lado de la línea (perpendicular)
    para reducir el ruido de la textura local.

    Devuelve:
        distancias_px : posición a lo largo del radio (en píxeles)
        intensidades  : valor de intensidad promediado (0-255 float)
    """
    h, w = imagen_bgr.shape[:2]

    # Canal de imagen
    if canal == "gray":
        img = cv2.cvtColor(imagen_bgr, cv2.COLOR_BGR2GRAY).astype(float)
    elif canal == "red":
        img = imagen_bgr[:, :, 2].astype(float)
    elif canal == "green":
        img = imagen_bgr[:, :, 1].astype(float)
    elif canal == "blue":
        img = imagen_bgr[:, :, 0].astype(float)
    else:
        img = cv2.cvtColor(imagen_bgr, cv2.COLOR_BGR2GRAY).astype(float)

    x0, y0 = p_inicio
    x1, y1 = p_fin
    longitud = math.hypot(x1 - x0, y1 - y0)
    if longitud < 2:
        return np.array([]), np.array([])

    n_muestras = int(longitud)
    # Vector unitario a lo largo del radio
    ux = (x1 - x0) / longitud
    uy = (y1 - y0) / longitud
    # Vector perpendicular
    px_v = -uy
    py_v = ux

    distancias = np.arange(n_muestras, dtype=float)
    intensidades = np.zeros(n_muestras, dtype=float)
    conteo = np.zeros(n_muestras, dtype=int)

    for offset in range(-banda_px, banda_px + 1):
        xs = x0 + distancias * ux + offset * px_v
        ys = y0 + distancias * uy + offset * py_v
        # Solo píxeles dentro de la imagen
        mask = (xs >= 0) & (xs < w - 1) & (ys >= 0) & (ys < h - 1)
        xi = xs[mask].astype(int)
        yi = ys[mask].astype(int)
        intensidades[mask] += img[yi, xi]
        conteo[mask] += 1

    validos = conteo > 0
    intensidades[validos] /= conteo[validos]
    return distancias[validos], intensidades[validos]


# =============================================================================
# DETECCIÓN DE PICOS
# =============================================================================

def detectar_limites(
    distancias: np.ndarray,
    intensidades: np.ndarray,
    perfil: dict,
) -> np.ndarray:
    """
    Detecta los límites de anillos en el perfil de intensidad.

    Devuelve array de posiciones en píxeles (a lo largo del radio)
    donde se encuentran los límites detectados.
    """
    if len(intensidades) < 10:
        return np.array([])

    señal = intensidades.copy()

    # Invertir si los límites son más claros que el interior
    if perfil.get("invertir", False):
        señal = 255.0 - señal

    # Suavizado Savitzky-Golay para preservar la forma de los picos
    ventana = perfil.get("suavizado", 7)
    ventana = max(3, ventana if ventana % 2 == 1 else ventana + 1)
    if len(señal) > ventana:
        try:
            señal = savgol_filter(señal, window_length=ventana, polyorder=2)
        except Exception:
            pass

    # Invertir para buscar mínimos como picos
    señal_inv = -señal

    prominencia = perfil.get("prominencia", 12.0)
    ancho_min   = perfil.get("anchura_min_px", 15)
    ancho_max   = perfil.get("anchura_max_px", 3000)

    picos, props = find_peaks(
        señal_inv,
        prominence=prominencia,
        distance=ancho_min,
        width=(1, ancho_max),
    )

    if len(picos) == 0:
        return np.array([])

    return distancias[picos]


# =============================================================================
# CONVERSIÓN DE POSICIONES → COORDENADAS DE ESCENA
# =============================================================================

def posiciones_a_coordenadas(
    posiciones_px: np.ndarray,
    p_inicio: tuple[float, float],
    p_fin: tuple[float, float],
) -> list[tuple[float, float]]:
    """
    Convierte posiciones a lo largo del radio (en píxeles de imagen)
    a coordenadas (x, y) de la escena Qt.
    """
    x0, y0 = p_inicio
    x1, y1 = p_fin
    longitud = math.hypot(x1 - x0, y1 - y0)
    if longitud == 0:
        return []
    ux = (x1 - x0) / longitud
    uy = (y1 - y0) / longitud
    return [(x0 + d * ux, y0 + d * uy) for d in posiciones_px]


# =============================================================================
# DIÁLOGO DE CONFIGURACIÓN DE ESPECIE
# =============================================================================

class DialogoEspecie(QDialog):
    """Editor de parámetros para un perfil de especie."""

    def __init__(self, nombre: str, perfil: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Configurar especie: {nombre}")
        self.setMinimumWidth(400)
        self._perfil = perfil.copy()

        layout = QVBoxLayout(self)

        form = QFormLayout()

        # Canal
        self.combo_canal = QComboBox()
        self.combo_canal.addItems(["gray", "red", "green", "blue"])
        self.combo_canal.setCurrentText(perfil.get("canal", "gray"))
        self.combo_canal.setToolTip(
            "Canal de color a analizar.\n"
            "gray: escala de grises (más común)\n"
            "green: útil en algunas latifoliadas teñidas"
        )
        form.addRow("Canal de imagen:", self.combo_canal)

        # Invertir
        self.chk_invertir = QCheckBox("Invertir señal")
        self.chk_invertir.setChecked(perfil.get("invertir", False))
        self.chk_invertir.setToolTip(
            "Activar si los límites de anillo son MÁS CLAROS que el interior.\n"
            "Común en algunas latifoliadas y preparaciones con tinción invertida."
        )
        form.addRow("Polaridad:", self.chk_invertir)

        # Suavizado
        self.spin_suavizado = QSpinBox()
        self.spin_suavizado.setRange(3, 51)
        self.spin_suavizado.setSingleStep(2)
        self.spin_suavizado.setValue(perfil.get("suavizado", 7))
        self.spin_suavizado.setToolTip(
            "Ventana de suavizado Savitzky-Golay (píxeles, debe ser impar).\n"
            "Valores mayores: ignora variaciones pequeñas (útil para anillos amplios).\n"
            "Valores menores: más sensible al detalle (útil para anillos estrechos)."
        )
        form.addRow("Suavizado (px):", self.spin_suavizado)

        # Prominencia
        self.spin_prom = QDoubleSpinBox()
        self.spin_prom.setRange(1.0, 100.0)
        self.spin_prom.setSingleStep(1.0)
        self.spin_prom.setValue(perfil.get("prominencia", 12.0))
        self.spin_prom.setToolTip(
            "Profundidad mínima del valle para ser considerado límite de anillo (0-255).\n"
            "Aumentar si detecta demasiados anillos falsos.\n"
            "Disminuir si se pierden anillos reales."
        )
        form.addRow("Prominencia mínima:", self.spin_prom)

        # Ancho mínimo
        self.spin_amin = QSpinBox()
        self.spin_amin.setRange(1, 500)
        self.spin_amin.setValue(perfil.get("anchura_min_px", 15))
        self.spin_amin.setToolTip(
            "Distancia mínima entre dos límites consecutivos (píxeles).\n"
            "= ancho mínimo de anillo en píxeles.\n"
            "Aumentar para especies de crecimiento lento o alto DPI."
        )
        form.addRow("Ancho mínimo anillo (px):", self.spin_amin)

        # Ancho máximo
        self.spin_amax = QSpinBox()
        self.spin_amax.setRange(10, 10000)
        self.spin_amax.setValue(perfil.get("anchura_max_px", 3000))
        self.spin_amax.setToolTip(
            "Distancia máxima entre límites consecutivos (píxeles).\n"
            "Aumentar para árboles de crecimiento muy rápido."
        )
        form.addRow("Ancho máximo anillo (px):", self.spin_amax)

        # Banda de muestreo
        self.spin_banda = QSpinBox()
        self.spin_banda.setRange(0, 50)
        self.spin_banda.setValue(perfil.get("banda_px", 5))
        self.spin_banda.setToolTip(
            "Píxeles a cada lado de la línea central para promediar.\n"
            "0 = solo la línea exacta (rápido pero ruidoso).\n"
            "5-10 = bueno para escáner plano.\n"
            "Aumentar si la imagen tiene mucho ruido de textura."
        )
        form.addRow("Banda de muestreo (px):", self.spin_banda)

        layout.addLayout(form)

        botones = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        layout.addWidget(botones)

    def obtener_perfil(self) -> dict:
        suav = self.spin_suavizado.value()
        if suav % 2 == 0:
            suav += 1
        return {
            "canal":          self.combo_canal.currentText(),
            "invertir":       self.chk_invertir.isChecked(),
            "suavizado":      suav,
            "prominencia":    self.spin_prom.value(),
            "anchura_min_px": self.spin_amin.value(),
            "anchura_max_px": self.spin_amax.value(),
            "banda_px":       self.spin_banda.value(),
        }


# =============================================================================
# DIÁLOGO DE PREVIEW Y AJUSTE DE SENSIBILIDAD
# =============================================================================

class DialogoPreviewDeteccion(QDialog):
    """
    Muestra el perfil de intensidad extraído con los límites detectados
    y permite al usuario ajustar la prominencia en tiempo real antes
    de aceptar la detección.
    """

    def __init__(
        self,
        distancias: np.ndarray,
        intensidades: np.ndarray,
        perfil: dict,
        pixeles_por_mm: float,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Preview — Ajustar detección")
        self.setMinimumSize(700, 420)

        self._distancias    = distancias
        self._intensidades  = intensidades
        self._perfil        = perfil.copy()
        self._ppm           = pixeles_por_mm
        self._posiciones    = np.array([])

        layout = QVBoxLayout(self)

        # Gráfico del perfil
        self._plot = pg.PlotWidget(title="Perfil de intensidad a lo largo del radio")
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._plot.setLabel("bottom", "Distancia (mm)")
        self._plot.setLabel("left",   "Intensidad")
        layout.addWidget(self._plot)

        # Controles de ajuste rápido
        grp = QGroupBox("Ajuste de sensibilidad")
        form = QFormLayout(grp)

        self.slider_prom = QSlider(Qt.Orientation.Horizontal)
        self.slider_prom.setRange(1, 100)
        self.slider_prom.setValue(int(perfil.get("prominencia", 12)))
        self.lbl_prom = QLabel(str(self.slider_prom.value()))
        self.slider_prom.valueChanged.connect(self._on_prom_changed)
        fila_prom = QWidget(); hl = QHBoxLayout(fila_prom)
        hl.addWidget(self.slider_prom); hl.addWidget(self.lbl_prom)
        form.addRow("Prominencia mínima:", fila_prom)

        self.spin_banda = QSpinBox()
        self.spin_banda.setRange(0, 50)
        self.spin_banda.setValue(perfil.get("banda_px", 5))
        self.spin_banda.setToolTip("Banda de muestreo — requiere re-extraer el perfil.")
        form.addRow("Banda de muestreo (px):", self.spin_banda)

        self.lbl_count = QLabel("Anillos detectados: —")
        self.lbl_count.setStyleSheet("font-weight: bold; font-size: 13px;")
        form.addRow(self.lbl_count)

        layout.addWidget(grp)

        botones = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        layout.addWidget(botones)

        self._actualizar()

    def _on_prom_changed(self, val: int):
        self.lbl_prom.setText(str(val))
        self._perfil["prominencia"] = float(val)
        self._actualizar()

    def _actualizar(self):
        self._posiciones = detectar_limites(
            self._distancias, self._intensidades, self._perfil
        )
        self._redibujar()
        n = len(self._posiciones)
        self.lbl_count.setText(f"Anillos detectados: {n}")

    def _redibujar(self):
        self._plot.clear()
        if len(self._distancias) == 0:
            return

        # Eje en mm
        dist_mm = self._distancias / self._ppm if self._ppm > 0 else self._distancias

        # Perfil suavizado
        señal = self._intensidades.copy()
        if self._perfil.get("invertir", False):
            señal = 255.0 - señal
        ventana = self._perfil.get("suavizado", 7)
        ventana = max(3, ventana if ventana % 2 == 1 else ventana + 1)
        if len(señal) > ventana:
            try:
                señal_s = savgol_filter(señal, window_length=ventana, polyorder=2)
            except Exception:
                señal_s = señal
        else:
            señal_s = señal

        self._plot.plot(dist_mm, self._intensidades,
                        pen=pg.mkPen("#555555", width=1), name="Raw")
        self._plot.plot(dist_mm, señal_s,
                        pen=pg.mkPen("#3388FF", width=2), name="Suavizado")

        # Líneas verticales en cada límite detectado
        for pos in self._posiciones:
            pos_mm = pos / self._ppm if self._ppm > 0 else pos
            self._plot.addItem(
                pg.InfiniteLine(pos=pos_mm, angle=90,
                                pen=pg.mkPen("#FF4444", width=1.5,
                                             style=Qt.PenStyle.DashLine))
            )

    def obtener_posiciones(self) -> np.ndarray:
        return self._posiciones

    def obtener_prominencia_final(self) -> float:
        return float(self.slider_prom.value())

    def obtener_banda_final(self) -> int:
        return self.spin_banda.value()
