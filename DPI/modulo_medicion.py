"""
modulo_medicion.py — Módulo de medición interactiva de anillos de árboles.

Contiene:
  - ExportadorDendro: escritura de archivos Tucson, CSV y Excel.
  - VistaInteractiva: lienzo con herramientas de marcado, calibración y edición.
  - PestanaImagen: pestaña completa por imagen con panel de co-datación en vivo.
"""

import os
import math
import json
import re
import logging
import copy

import cv2
import numpy as np
import pandas as pd
import pyqtgraph as pg
from PIL import Image
from modulo_codatacion import leer_archivo_tucson, leer_wid, leer_dendro_auto

from PyQt6.QtCore import QThread, pyqtSignal as Signal, QObject
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QFileDialog, QPushButton, QVBoxLayout, QHBoxLayout,
    QWidget, QLabel, QInputDialog, QMessageBox, QSpinBox,
    QDoubleSpinBox,
    QComboBox, QButtonGroup, QLineEdit, QListWidget,
    QSplitter, QListWidgetItem, QToolTip, QGraphicsEllipseItem,
    QDialog, QDialogButtonBox,
)
from PyQt6.QtGui import (
    QImage, QPixmap, QPainter, QPen, QColor, QBrush,
    QShortcut, QKeySequence, QCursor,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSettings, QEvent

from modulo_codatacion import leer_archivo_tucson, leer_wid
from modulo_autodeteccion import (
    cargar_perfiles, guardar_perfiles,
    extraer_perfil, detectar_limites, posiciones_a_coordenadas,
    DialogoEspecie, DialogoPreviewDeteccion,
)
from modulo_intensidad import (
    extraer_intensidad_por_anillo,
    escribir_rwl_intensidad,
    DialogoConfigBI,
    DialogoGuardarBI,
)
from modulo_pith import (
    MODO_PITH,
    ajustar_circulo_minimos_cuadrados,
    estimar_anillos_faltantes,
    DialogoMetodoPith,
    DialogoEstimacionPith,
)
from constantes import (
    DPI_DEFECTO, MM_POR_PULGADA, PIXELES_POR_MM_DEFECTO,
    MAX_PASOS_HISTORIAL,
    TOLERANCIA_CLICK_PUNTO, TOLERANCIA_CLICK_SEGMENTO,
    TUCSON_MAX_CHARS_ID, TUCSON_VALOR_FIN, TUCSON_VALOR_999,
    INTERVALO_MARCADOR_DECADA, INTERVALO_MARCADOR_CINCUENTENA,
    INTERVALO_MARCADOR_CENTENA,
    COLOR_PUNTO_ACTIVO, COLOR_LINEA_ACTIVA, COLOR_SALTO,
    COLOR_GUIA, COLOR_CALIBRACION, COLOR_PUNTO_INACTIVO,
    COLOR_EWLW,
    COLORES_SERIES_INACTIVAS,
    TIPOS_ANOMALIA,
)

logger = logging.getLogger(__name__)

# Modos de interacción del lienzo
MODO_ANADIR = 1   # alias mantenido por compatibilidad interna
MODO_MEDIR  = 1   # nombre nuevo: botón «Medir»
MODO_CALIBRAR = 2
MODO_MOVER = 3
MODO_SALTO = 4
MODO_INSERTAR = 5
MODO_BORRAR = 6
MODO_AUTODETECT = 7   # Marcar médula y corteza para auto-detección
MODO_ANOMALIA   = 8   # Marcar anomalías sobre anillos ya medidos
MODO_PITH       = 9   # Marcar puntos del arco para estimar médula faltante
MODO_ESPACIO    = 10  # Marcar espacio dentro de un anillo (intra-ring gap)
MODO_EWLW       = 11  # Marcar límite earlywood/latewood dentro de un anillo
MODO_SELECCIONAR = 12  # Seleccionar múltiples anillos para borrar/convertir



# =============================================================================
# UTILIDADES DE es_salto
# =============================================================================
# es_salto[i] puede ser:
#   False        → límite de anillo normal (tick rojo, cuenta y cierra anillo)
#   True         → salto (tick amarillo, rompe la cuenta de anillos)
#   "espacio"    → tramo del quiebre intra-anillo (amarillo, NO suma, NO cierra)
#   "quiebre_ini"→ inicio de quiebre (azul, SUMA distancia, NO cierra anillo)
#   "ew_lw"      → límite earlywood/latewood (suma, NO cierra)

def _es_salto_real(val) -> bool:
    """True solo si el segmento es un salto que rompe la cuenta."""
    return val is True

def _es_espacio(val) -> bool:
    """True si el segmento es un espacio intra-anillo."""
    return val == "espacio"

def _es_quiebre_ini(val) -> bool:
    """True si el segmento es inicio de quiebre (suma distancia, no cierra)."""
    return val == "quiebre_ini"

def _es_ewlw(val) -> bool:
    """True si el segmento es un límite earlywood/latewood."""
    return val == "ew_lw"

def _es_amarillo(val) -> bool:
    """True si el segmento debe dibujarse en amarillo (salto o espacio)."""
    return val is True or val == "espacio"


# =============================================================================
# EXPORTADOR
# =============================================================================

class ExportadorDendro:
    """Gestiona la exportación de mediciones a Tucson, CSV y Excel."""

    def __init__(self, ventana_padre: QWidget):
        self._parent = ventana_padre

    def exportar_datos(self, id_serie: str, lista_mediciones: list[dict],
                        anchos_ewlw: list | None = None):
        """Exporta mediciones a Tucson, CSV o Excel.

        Parameters
        ----------
        id_serie : str
            Identificador de la serie (código).
        lista_mediciones : list[dict]
            Lista de dicts {anio, ancho_mm} con las mediciones totales por anillo.
        anchos_ewlw : list[tuple], opcional
            Lista paralela de (total_mm, ew_mm, lw_mm) para anillos con
            límite EW/LW detectado. None en ew_mm/lw_mm si el anillo no
            tiene split. Si se provee y al menos un anillo tiene split,
            la exportación a CSV/Excel agregará columnas EW_mm y LW_mm.
            Para formato Tucson, ofrece exportar archivos separados
            (codigo.rwl + codigo_EW.rwl + codigo_LW.rwl).
        """
        if not lista_mediciones:
            return QMessageBox.warning(self._parent, "Error", "No hay mediciones para exportar.")

        datos = sorted(lista_mediciones, key=lambda d: d["anio"])
        anios = [d["anio"] for d in datos]
        anchos = [d["ancho_mm"] for d in datos]

        # Detectar si hay info EW/LW útil para incluir
        tiene_ewlw = False
        if anchos_ewlw and len(anchos_ewlw) == len(datos):
            tiene_ewlw = any(ew is not None and lw is not None
                             for (_, ew, lw) in anchos_ewlw)

        # Obtener última carpeta y armar la ruta sugerida
        carpeta = _ultima_carpeta()
        ruta_sugerida = os.path.join(carpeta, id_serie) if carpeta else id_serie

        ruta, fmt = QFileDialog.getSaveFileName(
            self._parent, "Guardar", ruta_sugerida,
            "Formato Tucson (*.txt *.rwl);;Excel (*.xlsx);;CSV (*.csv)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not ruta:
            return

        _ultima_carpeta(ruta)

        try:
            if "*.csv" in fmt:
                self._exportar_tabular(
                    id_serie, anios, anchos, ruta, "csv",
                    anchos_ewlw=anchos_ewlw if tiene_ewlw else None
                )
            elif "*.xlsx" in fmt:
                self._exportar_tabular(
                    id_serie, anios, anchos, ruta, "xlsx",
                    anchos_ewlw=anchos_ewlw if tiene_ewlw else None
                )
            else:
                mediciones_int = [int(round(m * 1000)) for m in anchos]
                self._escribir_tucson(id_serie, anios[0], mediciones_int, ruta)

                # Si hay EW/LW, ofrecer exportar archivos separados
                # (convención CooRecorder/Velmex: _EW.rwl y _LW.rwl).
                if tiene_ewlw:
                    resp = QMessageBox.question(
                        self._parent, "EW/LW detectado",
                        f"Se detectaron límites EW/LW en algunos anillos.\n\n"
                        f"¿Desea exportar también archivos Tucson separados\n"
                        f"para earlywood y latewood?\n\n"
                        f"Se crearán:\n"
                        f"  • {id_serie}_EW.rwl (earlywood)\n"
                        f"  • {id_serie}_LW.rwl (latewood)",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    if resp == QMessageBox.StandardButton.Yes:
                        self._exportar_tucson_ewlw(
                            id_serie, anios, anchos_ewlw, ruta
                        )

            QMessageBox.information(self._parent, "Éxito", f"Exportado a:\n{ruta}")
        except Exception as exc:
            logger.error("Error al exportar: %s", exc)
            QMessageBox.critical(self._parent, "Error", f"Fallo al exportar:\n{exc}")

    @staticmethod
    def _exportar_tabular(id_serie: str, anios: list, anchos: list, ruta: str,
                           formato: str, anchos_ewlw: list | None = None):
        """Exporta a CSV o Excel. Si anchos_ewlw está presente, agrega columnas EW_mm y LW_mm."""
        data = {"ID_Serie": id_serie, "Anio": anios, "Ancho_mm": anchos}
        if anchos_ewlw and len(anchos_ewlw) == len(anios):
            # Columnas separadas: NaN para anillos sin EW/LW detectado
            ew_col = [(t[1] if t[1] is not None else float("nan"))
                      for t in anchos_ewlw]
            lw_col = [(t[2] if t[2] is not None else float("nan"))
                      for t in anchos_ewlw]
            data["EW_mm"] = ew_col
            data["LW_mm"] = lw_col
        df = pd.DataFrame(data)
        if formato == "csv":
            df.to_csv(ruta, index=False)
        else:
            df.to_excel(ruta, index=False)

    def _exportar_tucson_ewlw(self, id_serie: str, anios: list,
                                anchos_ewlw: list, ruta_base: str):
        """Exporta archivos Tucson separados para EW y LW (convención Velmex).

        Crea {ruta_base}_EW.rwl y {ruta_base}_LW.rwl junto al archivo principal.
        Solo escribe los anillos que tienen límite EW/LW detectado; los demás
        se exportan con el valor especial Tucson para missing (999).
        """
        if not anchos_ewlw or len(anchos_ewlw) != len(anios):
            return

        # Separar nombre base y extensión
        base, ext = os.path.splitext(ruta_base)
        if not ext:
            ext = ".rwl"

        ew_vals = []
        lw_vals = []
        for (_, ew, lw) in anchos_ewlw:
            if ew is None or lw is None:
                # Anillo sin EW/LW detectado → marcar como missing (999)
                ew_vals.append(999)
                lw_vals.append(999)
            else:
                ew_vals.append(int(round(ew * 1000)))
                lw_vals.append(int(round(lw * 1000)))

        ruta_ew = base + "_EW" + ext
        ruta_lw = base + "_LW" + ext

        self._escribir_tucson(id_serie + "_EW", anios[0], ew_vals, ruta_ew)
        self._escribir_tucson(id_serie + "_LW", anios[0], lw_vals, ruta_lw)

    @staticmethod
    def _escribir_tucson(codigo: str, anio_inicio: int, mediciones: list[int], ruta: str):
        """Escribe el archivo en formato Tucson (.rwl / .txt)."""
        if not ruta.lower().endswith((".txt", ".rwl")):
            ruta += ".txt"

        codigo = codigo[:TUCSON_MAX_CHARS_ID]
        # Reemplaza el valor 999 colisionante con el código reservado 998
        mods = [TUCSON_VALOR_999 if v == 999 else v for v in mediciones]
        n = len(mods)
        lineas: list[str] = []
        idx = 0

        # Primera fila parcial (si el año de inicio no es múltiplo de 10)
        resto = anio_inicio % 10
        primeros = (10 - resto) if resto != 0 else 10
        if primeros < 10:
            cantidad = min(primeros, n - idx)
            if cantidad > 0:
                fila = f"{codigo:8s}{anio_inicio + idx:4d}"
                for v in mods[idx: idx + cantidad]:
                    fila += f"{v:6d}"
                lineas.append(fila)
                idx += cantidad

        # Filas completas de 10 valores
        while idx < n:
            cantidad = min(10, n - idx)
            fila = f"{codigo:8s}{anio_inicio + idx:4d}"
            for v in mods[idx: idx + cantidad]:
                fila += f"{v:6d}"
            lineas.append(fila)
            idx += cantidad

        # Marcador de fin de serie
        if n == 0:
            lineas = [f"{codigo:8s}{anio_inicio:4d}{TUCSON_VALOR_FIN:6d}"]
        else:
            ultima = lineas[-1].split()
            if max(0, len(ultima) - 2) >= 10:
                lineas.append(f"{codigo:8s}{int(ultima[1]) + 10:4d}{TUCSON_VALOR_FIN:6d}")
            else:
                lineas[-1] += f"{TUCSON_VALOR_FIN:6d}"

        with open(ruta, "w", encoding="utf-8", newline="\r\n") as f:
            for ln in lineas:
                f.write(ln.rstrip() + "\n")


# =============================================================================
# LIENZO INTERACTIVO
# =============================================================================

# =============================================================================
# WORKER — cómputo pesado en hilo separado con barra de progreso
# =============================================================================

def _calcular_ewlw_posiciones(sig_prog, gray, coords, saltos, banda):
    """
    Detecta EW/LW procesando el perfil de intensidad gris PIXEL A PIXEL
    a lo largo de cada anillo completo. Inserta un límite en TODOS los anillos.

    Para cada anillo:
      1. Extrae el perfil continuo de intensidad gris px a px a lo largo
         del camino (promediando en banda perpendicular en cada punto).
      2. Suaviza con Savitzky-Golay.
      3. Encuentra la segunda derivada mínima (máxima tasa de cambio).
      4. El punto de inflexión es el límite EW/LW.

    Corre en hilo secundario. Devuelve lista de (insert_at, px, py).
    """
    from scipy.signal import savgol_filter

    h, w = gray.shape
    n = len(coords)
    inserciones = []

    # Identificar anillos (tramos entre dos False consecutivos)
    anillos = []
    ini = 0
    for i in range(1, n):
        if saltos[i] is False:
            anillos.append(list(range(ini, i + 1)))
            ini = i
    if ini < n - 1:
        anillos.append(list(range(ini, n)))

    n_anillos = len(anillos)

    for a_num, anillo_idxs in enumerate(anillos):
        if sig_prog is not None:
            pct = int(a_num * 100 / max(1, n_anillos))
            sig_prog.emit(pct, f"Anillo {a_num + 1}/{n_anillos}")

        if len(anillo_idxs) < 2:
            continue

        # Si ya tiene ew_lw, saltar
        if any(saltos[j] == "ew_lw"
               for j in anillo_idxs[1:] if j < len(saltos)):
            continue

        # ── Construir perfil pixel a pixel ──────────────────────────────
        # Para cada punto a lo largo del camino del anillo,
        # promediamos los valores de gris en la banda perpendicular.
        perfil_vals = []   # valor gris en cada muestra
        perfil_pos  = []   # (px, py, seg_idx) en cada muestra

        for k in range(1, len(anillo_idxs)):
            ip = anillo_idxs[k - 1]
            ic = anillo_idxs[k]
            if ic >= n:
                break
            val_k = saltos[ic] if ic < len(saltos) else False
            # Saltar quiebres y espacios (pero no ew_lw que ya filtramos)
            if val_k is True or val_k in ("espacio", "quiebre_ini"):
                continue

            x0, y0 = coords[ip]
            x1, y1 = coords[ic]
            seg_len = math.hypot(x1 - x0, y1 - y0)
            if seg_len < 1:
                continue

            ux  = (x1 - x0) / seg_len
            uy  = (y1 - y0) / seg_len
            perp_x, perp_y = -uy, ux

            # Una muestra cada pixel a lo largo del segmento
            n_s  = max(2, int(round(seg_len)))
            ts   = np.linspace(0.0, 1.0, n_s)
            xs_c = x0 + ts * (x1 - x0)   # (n_s,)
            ys_c = y0 + ts * (y1 - y0)

            # Banda perpendicular vectorizada: shape (2b+1, n_s)
            offs  = np.arange(-banda, banda + 1, dtype=np.float32)
            xs_b  = xs_c[np.newaxis, :] + offs[:, np.newaxis] * perp_x  # (2b+1, n_s)
            ys_b  = ys_c[np.newaxis, :] + offs[:, np.newaxis] * perp_y

            # Clip a los bordes de la imagen
            xs_b  = np.clip(xs_b, 0, w - 1).astype(np.int32)
            ys_b  = np.clip(ys_b, 0, h - 1).astype(np.int32)

            # Media por columna (cada columna = una posición a lo largo del camino)
            # shape (n_s,)
            vals_col = gray[ys_b, xs_b].mean(axis=0)

            for j in range(n_s):
                perfil_vals.append(float(vals_col[j]))
                perfil_pos.append((float(xs_c[j]), float(ys_c[j]), ic))

        if len(perfil_vals) < 10:
            continue

        arr = np.array(perfil_vals, dtype=np.float32)

        # Suavizado con ventana ~10% del anillo, mínimo 5 puntos
        # Ventana más grande → señal más limpia para detectar la transición
        win_ideal = max(5, int(len(arr) * 0.10))
        win = win_ideal if win_ideal % 2 == 1 else win_ideal + 1
        win = min(win, len(arr) if len(arr) % 2 == 1 else len(arr) - 1)
        arr_smooth = savgol_filter(arr, win, 2) if win >= 5 else arr.copy()

        # Algoritmo de detección EW→LW:
        # La transición es EW (claro, valor alto) → LW (oscuro, valor bajo).
        # El punto de límite es donde la pendiente negativa es máxima
        # (primera derivada más negativa = mayor tasa de oscurecimiento).
        # Usamos la primera derivada suavizada en lugar de la segunda.
        d1 = np.gradient(arr_smooth)

        # Rango de búsqueda: 15% al 95% del anillo
        m_ini = max(1, int(len(arr) * 0.15))
        m_fin = max(1, int(len(arr) * 0.05))
        if m_ini + m_fin >= len(arr):
            m_ini, m_fin = 1, 1

        d1_sub = d1[m_ini: len(arr) - m_fin]
        if len(d1_sub) == 0:
            continue

        # Mínimo de la primera derivada = punto de mayor oscurecimiento (EW→LW)
        # Si la derivada es siempre positiva (anillo monotónicamente creciente),
        # usar el umbral del 50% del rango como respaldo
        idx_min_d1 = int(np.argmin(d1_sub)) + m_ini

        # Respaldo: si la derivada en ese punto es positiva (no hay oscurecimiento),
        # usar el punto donde el perfil cruza la media del anillo
        if d1[idx_min_d1] >= 0:
            media = float(arr_smooth.mean())
            # Buscar primer cruce por debajo de la media en el rango
            sub = arr_smooth[m_ini: len(arr) - m_fin]
            cruces = np.where(np.diff(np.sign(sub - media)) < 0)[0]
            if len(cruces) > 0:
                idx_min_d1 = cruces[0] + m_ini
            else:
                idx_min_d1 = m_ini + len(sub) // 2

        px_ins, py_ins, insert_at = perfil_pos[idx_min_d1]
        inserciones.append((insert_at, px_ins, py_ins))

    return inserciones


def _calcular_ewlw_posiciones_cancelable(sig_prog, gray, coords, saltos, banda,
                                          cancel_flag):
    """Idéntica a _calcular_ewlw_posiciones pero chequea cancel_flag['stop']
    al inicio de cada anillo para abortar limpiamente sin terminate().

    Si se cancela, devuelve las inserciones encontradas hasta ese punto
    (parcialmente), lo cual permite ver el progreso parcial si el usuario
    quiere quedárselo. Si prefiere descartar, no las aplicará desde el
    diálogo (eso se maneja en _on_ewlw).
    """
    from scipy.signal import savgol_filter

    h, w = gray.shape
    n = len(coords)
    inserciones = []

    anillos = []
    ini = 0
    for i in range(1, n):
        if saltos[i] is False:
            anillos.append(list(range(ini, i + 1)))
            ini = i
    if ini < n - 1:
        anillos.append(list(range(ini, n)))

    n_anillos = len(anillos)

    for a_num, anillo_idxs in enumerate(anillos):
        # Punto de cancelación cooperativa — chequea ANTES de cada anillo.
        # Mucho más seguro que QThread.terminate() porque deja terminar
        # cualquier numpy/scipy en curso sin corromper memoria.
        if cancel_flag.get("stop"):
            return inserciones  # devolver lo parcial

        if sig_prog is not None:
            pct = int(a_num * 100 / max(1, n_anillos))
            sig_prog.emit(pct, f"Anillo {a_num + 1}/{n_anillos}")

        if len(anillo_idxs) < 2:
            continue

        if any(saltos[j] == "ew_lw"
               for j in anillo_idxs[1:] if j < len(saltos)):
            continue

        perfil_vals = []
        perfil_pos  = []

        for k in range(1, len(anillo_idxs)):
            ip = anillo_idxs[k - 1]
            ic = anillo_idxs[k]
            if ic >= n:
                break
            val_k = saltos[ic] if ic < len(saltos) else False
            if val_k is True or val_k in ("espacio", "quiebre_ini"):
                continue

            x0, y0 = coords[ip]
            x1, y1 = coords[ic]
            seg_len = math.hypot(x1 - x0, y1 - y0)
            if seg_len < 1:
                continue

            ux  = (x1 - x0) / seg_len
            uy  = (y1 - y0) / seg_len
            perp_x, perp_y = -uy, ux

            n_s  = max(2, int(round(seg_len)))
            ts   = np.linspace(0.0, 1.0, n_s)
            xs_c = x0 + ts * (x1 - x0)
            ys_c = y0 + ts * (y1 - y0)

            offs  = np.arange(-banda, banda + 1, dtype=np.float32)
            xs_b  = xs_c[np.newaxis, :] + offs[:, np.newaxis] * perp_x
            ys_b  = ys_c[np.newaxis, :] + offs[:, np.newaxis] * perp_y

            xs_b  = np.clip(xs_b, 0, w - 1).astype(np.int32)
            ys_b  = np.clip(ys_b, 0, h - 1).astype(np.int32)

            vals_col = gray[ys_b, xs_b].mean(axis=0)

            for j in range(n_s):
                perfil_vals.append(float(vals_col[j]))
                perfil_pos.append((float(xs_c[j]), float(ys_c[j]), ic))

        if len(perfil_vals) < 10:
            continue

        arr = np.array(perfil_vals, dtype=np.float32)

        win_ideal = max(5, int(len(arr) * 0.10))
        win = win_ideal if win_ideal % 2 == 1 else win_ideal + 1
        win = min(win, len(arr) if len(arr) % 2 == 1 else len(arr) - 1)
        arr_smooth = savgol_filter(arr, win, 2) if win >= 5 else arr.copy()

        d1 = np.gradient(arr_smooth)

        m_ini = max(1, int(len(arr) * 0.15))
        m_fin = max(1, int(len(arr) * 0.05))
        if m_ini + m_fin >= len(arr):
            m_ini, m_fin = 1, 1

        d1_sub = d1[m_ini: len(arr) - m_fin]
        if len(d1_sub) == 0:
            continue

        idx_min_d1 = int(np.argmin(d1_sub)) + m_ini

        if d1[idx_min_d1] >= 0:
            media = float(arr_smooth.mean())
            sub = arr_smooth[m_ini: len(arr) - m_fin]
            cruces = np.where(np.diff(np.sign(sub - media)) < 0)[0]
            if len(cruces) > 0:
                idx_min_d1 = cruces[0] + m_ini
            else:
                idx_min_d1 = m_ini + len(sub) // 2

        px_ins, py_ins, insert_at = perfil_pos[idx_min_d1]
        inserciones.append((insert_at, px_ins, py_ins))

    # Emitir 100% al completar para que el usuario vea que el worker
    # terminó. Sin esto la barra queda pegada al 99% mientras el main
    # thread aplica los resultados, dando sensación de "crash".
    if sig_prog is not None:
        try:
            sig_prog.emit(100, "Procesando resultados...")
        except Exception:
            pass

    return inserciones



def _calcular_anchos_ewlw_por_anillo(coords, saltos, ppm):
    """Calcula los anchos de earlywood (EW) y latewood (LW) para cada anillo.

    Parameters
    ----------
    coords : list[tuple[float, float]]
        Coordenadas de los puntos del camino en píxeles.
    saltos : list
        Lista paralela a coords con los flags: False/True/"espacio"/"ew_lw"/etc.
    ppm : float
        Píxeles por mm para convertir a milímetros.

    Returns
    -------
    list[tuple[float, float | None, float | None]]
        Lista de tuplas (ancho_total_mm, ew_mm, lw_mm) por anillo.
        Si el anillo no tiene límite EW/LW, ew_mm y lw_mm son None.
        El orden coincide con el de los anillos en `recalcular_todas_las_distancias`.
    """
    n = len(coords)
    anchos_anillo = []   # lista de (total_mm, ew_mm_or_None, lw_mm_or_None)
    acum = 0.0           # distancia acumulada desde el inicio del anillo actual
    acum_pre_ewlw = None # si encontramos un EW/LW, congelamos el acum acá

    for i in range(1, n):
        val = saltos[i]
        if i >= len(coords) or i - 1 >= len(coords):
            break
        d = math.hypot(
            coords[i][0] - coords[i-1][0],
            coords[i][1] - coords[i-1][1],
        ) / ppm

        if val is True:
            # Salto real: cierra anillo previo si quedó algo
            if acum > 0:
                if acum_pre_ewlw is not None:
                    # Había un EW/LW, pero después saltó sin completar
                    anchos_anillo.append((acum, acum_pre_ewlw, acum - acum_pre_ewlw))
                else:
                    anchos_anillo.append((acum, None, None))
            acum = 0.0
            acum_pre_ewlw = None
        elif val == "espacio" or val == "quiebre_ini":
            # No suma, no cierra
            pass
        elif val == "ew_lw":
            # Suma y marca el split: el acum hasta acá es el EW
            acum += d
            acum_pre_ewlw = acum
        else:
            # val is False: límite normal — suma y cierra el anillo
            acum += d
            if acum_pre_ewlw is not None:
                ew = acum_pre_ewlw
                lw = acum - acum_pre_ewlw
                anchos_anillo.append((acum, ew, lw))
            else:
                anchos_anillo.append((acum, None, None))
            acum = 0.0
            acum_pre_ewlw = None

    # Último anillo sin cerrar
    if acum > 0:
        if acum_pre_ewlw is not None:
            anchos_anillo.append((acum, acum_pre_ewlw, acum - acum_pre_ewlw))
        else:
            anchos_anillo.append((acum, None, None))

    return anchos_anillo


def _ultima_carpeta(nueva_ruta: str | None = None) -> str:
    """Lee o guarda la última carpeta usada via QSettings."""
    cfg = QSettings("MoiCedrus", "DPI")
    if nueva_ruta is not None:
        import os
        cfg.setValue("ultima_carpeta", os.path.dirname(nueva_ruta))
        return os.path.dirname(nueva_ruta)
    return cfg.value("ultima_carpeta", "")

class _WorkerSignals(QObject):
    progreso  = Signal(int, str)   # (porcentaje 0-100, mensaje)
    resultado = Signal(object)     # payload de vuelta al hilo principal
    error     = Signal(str)        # mensaje de error


class _Worker(QThread):
    """
    Ejecuta una función pesada en un hilo secundario.
    La función recibe una señal de progreso como primer argumento.

    Uso:
        w = _Worker(fn, arg1, arg2)
        w.signals.progreso.connect(actualizar_ui)
        w.signals.resultado.connect(usar_resultado)
        w.signals.error.connect(mostrar_error)
        w.start()
    """

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn     = fn
        self._args   = args
        self._kwargs = kwargs
        self.signals = _WorkerSignals()

    def run(self):
        try:
            resultado = self._fn(
                self.signals.progreso,
                *self._args,
                **self._kwargs,
            )
            self.signals.resultado.emit(resultado)
        except Exception as exc:
            import traceback
            self.signals.error.emit(traceback.format_exc())


class _DialogoProgreso(QDialog):
    """Barra de progreso modal cancelable."""

    cancelado = Signal()

    def __init__(self, titulo: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self.setModal(True)
        self.setMinimumWidth(380)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        layout = QVBoxLayout(self)
        self._lbl = QLabel("Iniciando...")
        layout.addWidget(self._lbl)
        from PyQt6.QtWidgets import QProgressBar
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        layout.addWidget(self._bar)
        btn = QPushButton("Cancelar")
        btn.clicked.connect(self.cancelado)
        layout.addWidget(btn)

    def actualizar(self, pct: int, msg: str):
        self._lbl.setText(msg)
        self._bar.setValue(pct)


class VistaSplitView(QGraphicsView):
    """Vista de solo lectura con zoom/pan independiente."""

    def __init__(self, escena: QGraphicsScene, parent=None):
        super().__init__(escena, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setMouseTracking(True)
        # Permitir mover la imagen arrastrando con el mouse (mano).
        # Sin esto la ventana auxiliar solo soportaba zoom (wheel) pero no
        # paneo, lo que hacía la herramienta poco útil.
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        # Estilo de scrollbars (igual que la vista principal)
        self.setStyleSheet(
            "QScrollBar:horizontal{background:#3b3b3b; height:10px;}"
            "QScrollBar::handle:horizontal{background:#5a5a5a;"
            " border-radius:4px; min-width:20px;}"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal{"
            " width:0px; background:none;}"
            "QScrollBar:vertical{background:#3b3b3b; width:10px;}"
            "QScrollBar::handle:vertical{background:#5a5a5a;"
            " border-radius:4px; min-height:20px;}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical{"
            " height:0px; background:none;}"
            "QGraphicsView{border:2px solid #FF8C00; border-radius:2px;}"
        )
        # Cache de viewport: evita repintar la imagen completa en cada
        # movimiento o cambio menor — mejora notablemente la fluidez de pan
        # y zoom con imágenes grandes.
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheBackground)

    def wheelEvent(self, event):
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)
        event.accept()


class PanelSplit(QWidget):
    """Panel de vista dividida con selector de imagen y vista de solo lectura.

    Permite ver la misma imagen en otra posición/zoom, o bien una imagen
    diferente de otra pestaña abierta.
    """

    def __init__(self, escena_propia: QGraphicsScene, pestana: "PestanaImagen"):
        super().__init__()
        self._pestana = pestana
        self._escena_propia = escena_propia

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Barra de selección ──
        barra = QHBoxLayout()
        barra.setContentsMargins(4, 2, 4, 2)

        lbl = QLabel("◫")
        lbl.setStyleSheet("color:#FF8C00; font-size:14px; font-weight:bold; background:transparent;")
        barra.addWidget(lbl)

        self.combo_fuente = QComboBox()
        self.combo_fuente.setToolTip("Selecciona qué imagen mostrar en este panel")
        self.combo_fuente.setMinimumWidth(140)
        self.combo_fuente.setStyleSheet(
            "QComboBox{background:#3b3b3b; color:white; border:1px solid #FF8C00; "
            "border-radius:3px; padding:2px 6px;}"
            "QComboBox QAbstractItemView{background:#3b3b3b; color:white; "
            "selection-background-color:#FF8C00;}"
        )
        self.combo_fuente.currentIndexChanged.connect(self._cambiar_fuente)
        barra.addWidget(self.combo_fuente, 1)

        # Botón sincronizar zoom/posición con la vista principal
        btn_sync = QPushButton("⟲")
        btn_sync.setFixedSize(26, 26)
        btn_sync.setToolTip("Sincronizar zoom y posición con la vista principal")
        btn_sync.setStyleSheet(
            "QPushButton{background:#555; color:white; border-radius:3px; font-size:14px;}"
            "QPushButton:hover{background:#FF8C00;}")
        btn_sync.clicked.connect(self._sincronizar_con_principal)
        barra.addWidget(btn_sync)

        layout.addLayout(barra)

        # ── Vista ──
        # El estilo (borde naranja + scrollbars) ya está aplicado por
        # VistaSplitView en su __init__, no hace falta aplicarlo aquí.
        self.vista = VistaSplitView(escena_propia, self)
        layout.addWidget(self.vista)

    def refrescar_combo(self):
        """Actualiza la lista de imágenes disponibles."""
        self.combo_fuente.blockSignals(True)
        seleccion_actual = self.combo_fuente.currentData()
        self.combo_fuente.clear()

        # Opción 1: misma imagen
        self.combo_fuente.addItem("📷 Misma imagen", userData="__self__")

        # Opciones 2+: otras pestañas abiertas
        try:
            ventana = self._pestana._ventana_padre
            tabs = getattr(ventana, '_tabs_imagenes', None)
            if tabs is not None:
                for i in range(tabs.count()):
                    widget = tabs.widget(i)
                    if widget is not self._pestana and widget is not None:
                        nombre = tabs.tabText(i)
                        self.combo_fuente.addItem(f"📷 {nombre}", userData=id(widget))
        except Exception:
            pass

        # Restaurar selección
        if seleccion_actual is not None:
            for i in range(self.combo_fuente.count()):
                if self.combo_fuente.itemData(i) == seleccion_actual:
                    self.combo_fuente.setCurrentIndex(i)
                    break

        self.combo_fuente.blockSignals(False)

    def _cambiar_fuente(self, idx):
        """Cambia la escena de la vista según la selección del combo."""
        data = self.combo_fuente.itemData(idx)
        if data == "__self__" or data is None:
            self.vista.setScene(self._escena_propia)
            return

        # Buscar la pestaña por id
        try:
            ventana = self._pestana._ventana_padre
            tabs = getattr(ventana, '_tabs_imagenes', None)
            if tabs is not None:
                for i in range(tabs.count()):
                    widget = tabs.widget(i)
                    if id(widget) == data:
                        self.vista.setScene(widget.escena)
                        # Ajustar zoom para ver toda la imagen
                        self.vista.fitInView(
                            widget.escena.sceneRect(),
                            Qt.AspectRatioMode.KeepAspectRatio)
                        return
        except Exception:
            pass

    def _sincronizar_con_principal(self):
        """Copia zoom y posición de la vista principal."""
        self.vista.setTransform(self._pestana.vista.transform())
        center = self._pestana.vista.mapToScene(
            self._pestana.vista.viewport().rect().center())
        self.vista.centerOn(center)

class DialogoCalibracionRegla(QDialog):
    """Diálogo para preguntar la distancia antes de trazar la regla."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Calibrar Escala (Regla)")
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Ingresa la distancia física que vas a trazar en la imagen:"))

        fila = QHBoxLayout()
        self.spin_val = QDoubleSpinBox()
        self.spin_val.setRange(0.01, 10000.0)
        self.spin_val.setValue(1.0)
        self.spin_val.setDecimals(2)
        self.spin_val.setSingleStep(0.1)
        fila.addWidget(self.spin_val)

        self.combo_unidad = QComboBox()
        self.combo_unidad.addItems(["cm", "mm"])
        fila.addWidget(self.combo_unidad)

        layout.addLayout(fila)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def obtener_mm(self) -> float:
        val = self.spin_val.value()
        if self.combo_unidad.currentText() == "cm":
            return val * 10.0  # Convertir a mm
        return val

class VistaInteractiva(QGraphicsView):
    """
    Vista de imagen con herramientas de marcado de anillos.

    Modos disponibles (constantes MODO_*):
      1 ANADIR    — clic para añadir punto de anillo
      2 CALIBRAR  — clic en dos puntos para calibrar escala
      3 MOVER     — arrastrar puntos o hacer pan de la imagen
      4 SALTO     — añade un salto (discontinuidad del camino)
      5 INSERTAR  — inserta un punto en un segmento existente
      6 BORRAR    — elimina un punto con clic
      7 AUTODETECT — marca médula y corteza para auto-detección
    """

    mediciones_actualizadas = pyqtSignal(list)
    progreso_modificado = pyqtSignal()

    def __init__(self, escena: QGraphicsScene, pestana_padre: "PestanaImagen"):
        super().__init__(escena, pestana_padre)
        self._pestana = pestana_padre

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setMouseTracking(True)

        # Estilo de scrollbars — el global de estilos.py solo cubre la
        # vertical, dejando la horizontal con el default del sistema (blanco
        # en algunos temas de Linux). Acá la estilamos local para que se vea
        # consistente con el resto de la UI.
        self.setStyleSheet(
            "QScrollBar:horizontal{background:#3b3b3b; height:10px;}"
            "QScrollBar::handle:horizontal{background:#5a5a5a;"
            " border-radius:4px; min-width:20px;}"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal{"
            " width:0px; background:none;}"
            "QScrollBar:vertical{background:#3b3b3b; width:10px;}"
            "QScrollBar::handle:vertical{background:#5a5a5a;"
            " border-radius:4px; min-height:20px;}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical{"
            " height:0px; background:none;}"
        )

        # Estado de medición
        self.coordenadas_anillos: list[tuple[float, float]] = []
        self.es_salto: list = []  # False | True | "espacio"
        self.puntos_calibracion: list[tuple[float, float]] = []
        # Items visuales de calibración (puntos, línea, texto) — se limpian
        # al cancelar/aceptar/reiniciar la calibración.
        self._items_calibracion: list = []
        self._pixeles_por_mm: float = PIXELES_POR_MM_DEFECTO

        # Elementos gráficos activos
        self._items_puntos: list = []
        self._items_lineas: list = []
        self._items_textos: list = []
        self.items_caminos_inactivos: dict = {}

        # Estado de interacción
        self.modo_actual: int = MODO_ANADIR
        # Anomalías: {anio: tipo_str}
        self.anomalias: dict[int, str] = {}
        self._items_anomalias: list = []   # QGraphicsItems de marcas visuales
        self._punto_arrastrado_idx: int | None = None
        self._is_panning: bool = False
        self._last_pan_pos = None
        self._linea_guia = None
        self._item_hover_prev = None
        # Estado del modo Medir/Salto: punto de anclaje seleccionado
        self._idx_anclado: int | None = None   # índice del punto de partida
        self._item_anclado = None               # resaltado visual del ancla

        # Historial de deshacer
        self._historial: list[tuple] = []
        self._historial_redo: list[tuple] = []

        # Auto-detección: almacena los dos puntos (médula, corteza)
        self._puntos_autodetect: list[tuple[float, float]] = []
        self._items_autodetect: list = []   # marcadores visuales temporales
        # Imagen OpenCV cacheada para no releerla en cada detección
        self._imagen_bgr: np.ndarray | None = None

        # Estimación de médula: puntos del arco marcados por el usuario
        self._puntos_pith: list[tuple[float, float]] = []
        self._items_pith: list = []   # marcadores visuales temporales
        self._item_circulo_pith = None  # círculo ajustado visualizado
        self._items_pith_extras: list = []  # línea, centro, texto de preview
        self._item_pith_mouse_preview = None  # arco preview siguiendo el mouse

        # Espacio intra-anillo: selección de dos puntos existentes
        self._puntos_espacio: list[tuple[float, float]] = []
        self._items_espacio: list = []
        self._idx_espacio_inicio: int | None = None  # flag: None = esperando clic 1
        self._espacio_p1: tuple[float,float] = (0.0, 0.0)
        self._espacio_seg1: int = 0

        # Pens y brushes — cosméticos (grosor en px pantalla, invariante al zoom)
        self._pen_punto       = QPen()
        self._brush_punto     = QBrush(QColor(COLOR_PUNTO_ACTIVO))
        self._pen_linea       = QPen()
        self._pen_salto       = QPen()
        self._pen_guia        = QPen(QColor(COLOR_GUIA))
        self._pen_guia.setStyle(Qt.PenStyle.DashLine)
        self._pen_guia.setCosmetic(True)
        self._pen_guia.setWidthF(1.0)
        self._pen_calibracion = QPen()
        self._pen_ewlw        = QPen()
        self._actualizar_pens()   # inicializa colores y grosores

        # HUD overlay para mostrar resultado de "anillos al centro".
        # Es un QLabel hijo del VIEWPORT (no de la escena), así NO se mueve
        # cuando el usuario hace pan/zoom. Solución al problema de que el
        # texto quedaba en el centro estimado de la médula que muchas veces
        # cae FUERA de la muestra visible (justo el caso típico — la médula
        # no está en la muestra, por eso la estamos estimando).
        from PyQt6.QtWidgets import QLabel
        self._hud_pith = QLabel(self.viewport())
        self._hud_pith.setStyleSheet(
            "QLabel { background-color: rgba(0,0,0,220); color: #FF8C00; "
            "padding: 6px 12px; border-radius: 4px; font-size: 13px; "
            "font-weight: bold; font-family: Arial; "
            "border: 1px solid rgba(255,140,0,150); }"
        )
        self._hud_pith.setVisible(False)
        self._hud_pith.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # ── Herramienta de selección múltiple ──
        # Permite al usuario seleccionar varios anillos a la vez (clic
        # alterna selección por punto) y aplicarles una acción común:
        # borrarlos todos, convertirlos a salto o convertirlos a quiebre.
        self._puntos_seleccionados: set[int] = set()
        self._items_seleccion: list = []  # marcadores visuales (círculos amarillos)

        # Cache de los datos de caminos inactivos. Necesario para poder
        # re-renderizar en cambios de zoom (los labels y ticks dependen
        # de la escala). Antes los labels viejos quedaban con el tamaño
        # de un zoom anterior y se veían enormes después de acercar.
        self._caminos_inactivos_data: dict = {}

        # Estado para selección por arrastre (rectángulo).
        # Cuando el usuario hace clic+drag en MODO_SELECCIONAR sobre un
        # área vacía, dibujamos un rectángulo de selección. Al soltar,
        # todos los anillos dentro del rectángulo se agregan a la selección.
        self._sel_drag_iniciado: bool = False
        self._sel_drag_start: tuple[float, float] = (0.0, 0.0)
        self._sel_drag_rect_item = None
        self._sel_click_idx_candidato: int | None = None  # punto sobre el que se hizo click
        self._sel_click_pos: tuple[float, float] = (0.0, 0.0)

        # Panel flotante (HUD) con los botones de acción. Se muestra solo
        # en MODO_SELECCIONAR. Lo construimos perezosamente la primera vez
        # que se entra al modo (para no impactar la inicialización).
        self._panel_seleccion: 'QFrame | None' = None
        self._lbl_seleccion_count: 'QLabel | None' = None
        self._btn_borrar_sel = None
        self._btn_salto_sel = None
        self._btn_quiebre_sel = None
        self._btn_limpiar_sel = None

        self.cambiar_modo(MODO_ANADIR)

    # -------------------------------------------------------------------------
    # Historial
    # -------------------------------------------------------------------------

    def guardar_en_historial(self):
        self._historial.append((self.coordenadas_anillos.copy(), self.es_salto.copy()))
        if len(self._historial) > MAX_PASOS_HISTORIAL:
            self._historial.pop(0)
        self._historial_redo.clear()  # Nueva acción invalida el redo

    def limpiar_historial(self):
        self._historial.clear()
        self._historial_redo.clear()

    def deshacer_ultimo(self):
        if not self._historial:
            return
        # Guardar estado actual en redo antes de restaurar
        self._historial_redo.append(
            (self.coordenadas_anillos.copy(), self.es_salto.copy()))
        coords, saltos = self._historial.pop()
        self.coordenadas_anillos = coords.copy()
        self.es_salto = saltos.copy()
        self._limpiar_anclado()
        self.redibujar_todo()
        if self._linea_guia:
            self.scene().removeItem(self._linea_guia)
            self._linea_guia = None
        self.recalcular_todas_las_distancias()
        self.progreso_modificado.emit()

    def rehacer_ultimo(self):
        if not self._historial_redo:
            return
        # Guardar estado actual en historial
        self._historial.append(
            (self.coordenadas_anillos.copy(), self.es_salto.copy()))
        coords, saltos = self._historial_redo.pop()
        self.coordenadas_anillos = coords.copy()
        self.es_salto = saltos.copy()
        self._limpiar_anclado()
        self.redibujar_todo()
        if self._linea_guia:
            self.scene().removeItem(self._linea_guia)
            self._linea_guia = None
        self.recalcular_todas_las_distancias()
        self.progreso_modificado.emit()

    # -------------------------------------------------------------------------
    # Modos
    # -------------------------------------------------------------------------

    def cambiar_modo(self, nuevo_modo: int):
        modo_anterior = self.modo_actual  # capturar antes de asignar
        self.modo_actual = nuevo_modo
        # Limpiar ancla visual al salir del modo Medir/Salto
        if nuevo_modo not in (MODO_MEDIR, MODO_SALTO):
            self._limpiar_anclado()
        # Al entrar a MODO_ESPACIO limpiar la línea guía de Medir
        # pero NO borrar el ancla — así al volver a Medir se reanuda
        if nuevo_modo == MODO_ESPACIO:
            if self._linea_guia:
                self.scene().removeItem(self._linea_guia)
                self._linea_guia = None
        # Limpiar selección espacio al salir del modo
        if nuevo_modo != MODO_ESPACIO:
            self._idx_espacio_inicio = None
            self._limpiar_espacio()
        # Limpiar items visuales del modo PITH al salir de ese modo.
        # Bug previo: si el usuario activaba "Anillos al centro" y luego
        # cambiaba a otra herramienta (Mover, Borrar, etc.), los círculos
        # naranjas, líneas y texto del análisis quedaban dibujados en la
        # imagen indefinidamente. Solo se limpiaban con Enter (confirmar) o
        # Esc — pero cualquier otro modo de salida los dejaba huérfanos.
        # Usamos modo_anterior (capturado antes de asignar) porque
        # self.modo_actual ya fue actualizado a nuevo_modo arriba.
        if (modo_anterior == MODO_PITH and nuevo_modo != MODO_PITH
                and hasattr(self, "_limpiar_items_pith")):
            self._limpiar_items_pith()
        # NOTA: NO limpiamos items de calibración al cambiar de modo.
        # Las marcas persisten como recordatorio visual de que la imagen
        # fue calibrada. Solo se borran cuando el usuario inicia una nueva
        # calibración (iniciar_calibracion las limpia al inicio) o cancela
        # el diálogo de mm.
        # ── Manejo del panel de la herramienta de selección ──
        # Al entrar: mostrar panel + resetear estado de drag.
        # Al salir: limpiar selección, drag activo y panel.
        if nuevo_modo == MODO_SELECCIONAR:
            self._sel_drag_iniciado = False
            self._sel_click_idx_candidato = None
            self._mostrar_panel_seleccion(True)
        elif modo_anterior == MODO_SELECCIONAR:
            # Si quedó un rectángulo de drag en la escena, removerlo
            if self._sel_drag_rect_item is not None:
                try:
                    self.scene().removeItem(self._sel_drag_rect_item)
                except Exception:
                    pass
                self._sel_drag_rect_item = None
            self._sel_drag_iniciado = False
            self._sel_click_idx_candidato = None
            self._limpiar_seleccion_completa()
            self._mostrar_panel_seleccion(False)

        if nuevo_modo not in (MODO_MEDIR, MODO_SALTO) and self._linea_guia:
            self.scene().removeItem(self._linea_guia)
            self._linea_guia = None
        if nuevo_modo not in (MODO_MEDIR, MODO_SALTO) and self._linea_guia:
            self.scene().removeItem(self._linea_guia)
            self._linea_guia = None
        cursores = {
            MODO_MEDIR:       Qt.CursorShape.CrossCursor,
            MODO_ANADIR:      Qt.CursorShape.CrossCursor,
            MODO_CALIBRAR:    Qt.CursorShape.CrossCursor,
            MODO_SALTO:       Qt.CursorShape.CrossCursor,
            MODO_MOVER:       Qt.CursorShape.OpenHandCursor,
            MODO_INSERTAR:    Qt.CursorShape.ArrowCursor,
            MODO_BORRAR:      Qt.CursorShape.ArrowCursor,
            MODO_AUTODETECT:  Qt.CursorShape.CrossCursor,
            MODO_ANOMALIA:    Qt.CursorShape.PointingHandCursor,
            MODO_PITH:        Qt.CursorShape.CrossCursor,
            MODO_ESPACIO:     Qt.CursorShape.CrossCursor,
            MODO_EWLW:        Qt.CursorShape.CrossCursor,
            MODO_SELECCIONAR: Qt.CursorShape.PointingHandCursor,
        }
        self.setCursor(cursores.get(nuevo_modo, Qt.CursorShape.ArrowCursor))

    def _limpiar_items_calibracion(self):
        """Elimina puntos / línea / texto de una calibración previa."""
        for item in self._items_calibracion:
            try:
                self.scene().removeItem(item)
            except Exception:
                pass
        self._items_calibracion.clear()
        self.puntos_calibracion = []

    def iniciar_calibracion(self, objetivo_mm: float | None = None):
        """Inicia el modo de calibración por regla.

        Flujo:
          1. Si había marcas de calibración anteriores, se borran (nueva
             calibración reemplaza la anterior).
          2. Click 1 → marca punto inicial
          3. Click 2 → marca punto final + dibuja línea con distancia en px,
             luego pregunta los mm (QInputDialog)
          4. Si OK: aplica calibración. Las marcas SE MANTIENEN como
             recordatorio visual de que la imagen está calibrada (etiqueta
             cambia a "✓ Calibrada: X px = Y mm").
          5. Si Cancel: se borran las marcas (no hubo calibración).
        """
        # Limpiar marcas de calibración anteriores (puntos, línea, texto)
        # para evitar que se acumulen en la imagen tras múltiples usos.
        # Las marcas de la calibración previa se reemplazan por las nuevas.
        self._limpiar_items_calibracion()

        self.cambiar_modo(MODO_CALIBRAR)
        self._objetivo_calibracion_mm = objetivo_mm
        # Forzar foco en la vista para que el primer click se registre
        # directamente como mousePressEvent en la vista.
        self.setFocus()
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

    # -------------------------------------------------------------------------
    # ── Propiedad pixeles_por_mm ──────────────────────────────────────────

    @property
    def pixeles_por_mm(self) -> float:
        return self._pixeles_por_mm

    @pixeles_por_mm.setter
    def pixeles_por_mm(self, valor: float):
        self._pixeles_por_mm = valor
        self._actualizar_pens()

    def _actualizar_pens(self):
        """
        Recalcula el grosor de los pens en función de los DPI actuales.
        Los pens son COSMÉTICOS: su grosor se expresa en píxeles de pantalla,
        invariante al zoom, pero proporcional a la resolución de la imagen.

        Lógica:
          - A 2400 DPI (defecto, ~94 px/mm) queremos líneas de 2 px → grosor base.
          - A menor DPI (imagen más gruesa por px) escalamos proporcionalmente.
          - Mínimo 1.5 px, máximo 5 px para evitar extremos.
        """
        dpi = self._pixeles_por_mm * MM_POR_PULGADA
        # Grosor base calibrado para 2400 DPI
        factor = dpi / 2400.0
        grosor_linea  = max(1.5, min(5.0, 2.0 * factor))
        grosor_tick   = max(1.5, min(5.0, 2.0 * factor))
        grosor_calib  = max(2.0, min(6.0, 3.0 * factor))

        def _make(color, grosor, estilo=Qt.PenStyle.SolidLine) -> QPen:
            p = QPen(QColor(color))
            p.setCosmetic(True)
            p.setWidthF(grosor)
            p.setStyle(estilo)
            return p

        self._pen_punto       = _make(COLOR_PUNTO_ACTIVO, grosor_tick)
        self._brush_punto     = QBrush(QColor(COLOR_PUNTO_ACTIVO))
        self._pen_linea       = _make(COLOR_LINEA_ACTIVA, grosor_linea)
        self._pen_salto       = _make(COLOR_SALTO, grosor_linea, Qt.PenStyle.DashLine)
        self._pen_ewlw        = _make(COLOR_EWLW, grosor_tick)
        self._pen_calibracion = _make(COLOR_CALIBRACION, grosor_calib)

    # ── Utilidades de dibujo proporcionales al zoom ───────────────────────


    def _escala_vista(self) -> float:
        """
        Devuelve cuántas unidades de escena equivalen a 1 píxel de pantalla.
        Usado para que las líneas perpendiculares mantengan largo visual constante.
        """
        t = self.transform()
        # El factor de escala horizontal de la transformación afín
        escala_px_por_escena = t.m11()   # >1 = zoom in, <1 = zoom out
        return 1.0 / escala_px_por_escena if escala_px_por_escena != 0 else 1.0

    def _perp_tick(
        self,
        coords: list[tuple[float, float]],
        idx: int,
        largo_px_pantalla: float = 22.0,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        """
        Calcula los dos extremos de la línea perpendicular al radio en el
        punto coords[idx].

        La dirección del radio se estima a partir de los vecinos:
          - Punto interior: promedio de los vectores anterior y posterior.
          - Extremo: vector hacia el único vecino.

        largo_px_pantalla: largo visual en píxeles de pantalla (independiente del zoom).
        """
        n = len(coords)
        x, y = coords[idx]

        # Vector tangente al radio
        if n == 1:
            tx, ty = 1.0, 0.0      # sin vecinos: horizontal por defecto
        elif idx == 0:
            nx, ny = coords[1]
            tx, ty = nx - x, ny - y
        elif idx == n - 1:
            px, py = coords[idx - 1]
            tx, ty = x - px, y - py
        else:
            px, py = coords[idx - 1]
            nx, ny = coords[idx + 1]
            tx, ty = nx - px, ny - py

        # Normalizar
        mag = math.hypot(tx, ty)
        if mag < 1e-9:
            tx, ty = 1.0, 0.0
        else:
            tx, ty = tx / mag, ty / mag

        # Perpendicular (rotar 90°)
        perp_x, perp_y = -ty, tx

        # Escalar al largo deseado en píxeles de pantalla
        mitad = largo_px_pantalla * 0.5 * self._escala_vista()
        return (
            (x + perp_x * mitad, y + perp_y * mitad),
            (x - perp_x * mitad, y - perp_y * mitad),
        )

    # -------------------------------------------------------------------------
    # Dibujo
    # -------------------------------------------------------------------------

    def _grosor_tick_zoom(self) -> float:
        """Calcula el ancho del tick perpendicular según el nivel de zoom.

        Principio: los límites de anillo se muestran SIEMPRE (no se filtran).
        Lo que se adapta con el zoom es su grosor: muy fino al alejar para
        evitar el "muro rojo" cuando hay miles de anillos juntos en pocos
        píxeles, normal al acercar para mejor visibilidad.

        El ancho del pen se interpreta en píxeles de PANTALLA porque los
        pens son cosmetic.
        """
        escala = self._escala_vista()
        if escala < 0.3:    # muy acercado
            return 3.0
        elif escala < 1.5:  # acercado
            return 2.5
        elif escala < 3.5:  # medio
            return 1.8
        elif escala < 7.0:  # alejado
            return 1.2
        else:               # muy alejado
            return 0.8

    def redibujar_todo(self):
        """Elimina todos los elementos gráficos y los reconstruye desde el estado."""
        for item in self._items_puntos + self._items_lineas + self._items_textos:
            self.scene().removeItem(item)
        self._items_puntos.clear()
        self._items_lineas.clear()
        self._items_textos.clear()

        # Ajustar grosor de los pens de tick según zoom actual.
        # Esto se hace ANTES del loop para que todos los ticks usen el
        # ancho correcto. Los pens son atributos persistentes; modificar
        # su ancho aquí afecta todo lo que se dibuje en esta pasada.
        grosor_actual = self._grosor_tick_zoom()
        self._pen_punto.setWidthF(grosor_actual)
        self._pen_salto.setWidthF(grosor_actual)
        self._pen_ewlw.setWidthF(grosor_actual)
        # Para el camino activo usamos los mismos pens (con el ancho ya ajustado).
        # Los nombres con sufijo "_zoom" son alias semánticos para claridad.
        self._pen_punto_zoom = self._pen_punto
        self._pen_salto_zoom = self._pen_salto
        self._pen_ewlw_zoom = self._pen_ewlw

        # Tracker para filtro de overlap: bbox del último label dibujado.
        # Se compara con cada nuevo candidato para evitar superposiciones.
        self._ultimo_bbox_label_activo = None

        try:
            anio_medula = self._pestana.spin_medula.value()
            anio_corteza = self._pestana.spin_corteza.value()
            medula_a_corteza = self._pestana.combo_dir.currentIndex() == 0
        except Exception:
            # La vista puede redibujar antes de que los widgets de la pestaña
            # estén completamente inicializados; usamos valores neutros como fallback.
            anio_medula, anio_corteza, medula_a_corteza = 0, 0, True

        anillos_validos = 0
        for i, coord in enumerate(self.coordenadas_anillos):
            val = self.es_salto[i] if i > 0 else False
            es_amar  = _es_amarillo(val)    # salto o espacio → amarillo
            es_corte = _es_salto_real(val)  # solo salto → rompe cuenta

            # Solo los límites normales (False) incrementan la cuenta de anillos.
            # Excepción: si el segmento que SALE es "espacio", es inicio de quiebre
            # dentro del mismo anillo → no incrementa.
            val_sale_cnt = self.es_salto[i + 1] if i + 1 < len(self.es_salto) else False
            if (i > 0
                    and not es_amar
                    and not _es_ewlw(val)
                    and not _es_espacio(val_sale_cnt)):
                anillos_validos += 1

            if medula_a_corteza:
                anio = (anio_medula + anillos_validos - 1) if i > 0 else (anio_medula - 1)
            else:
                anio = (anio_corteza - anillos_validos + 1) if i > 0 else anio_corteza

            # Línea de conexión: ew_lw usa azul (cuenta como distancia)
            if i > 0:
                pen = self._pen_linea if _es_ewlw(val) else (
                    self._pen_salto if es_amar else self._pen_linea)
                prev = self.coordenadas_anillos[i - 1]
                linea_conexion = self.scene().addLine(prev[0], prev[1], coord[0], coord[1], pen)
                # zValue=1 para que la línea quede DEBAJO de los ticks (zValue=2).
                # El usuario quiere los límites siempre prominentes.
                linea_conexion.setZValue(1)
                self._items_lineas.append(linea_conexion)

            # Tick perpendicular
            # Color: naranja si es ew_lw, amarillo si es espacio/salto o inicio quiebre,
            # rojo en cualquier otro caso (límite normal de anillo).
            # IMPORTANTE: TODOS los límites se muestran SIEMPRE. Lo que se
            # adapta con el zoom es el ANCHO del tick (más fino al alejar),
            # no su existencia. Esto evita el "muro rojo" al alejar pero
            # mantiene visible cada límite — fundamental para no perder
            # información en series largas.
            val_sale = self.es_salto[i + 1] if i + 1 < len(self.es_salto) else False
            if _es_ewlw(val):
                pen_tick = self._pen_ewlw_zoom
            elif es_amar or _es_espacio(val_sale):
                pen_tick = self._pen_salto_zoom
            else:
                pen_tick = self._pen_punto_zoom
            p1_t, p2_t = self._perp_tick(self.coordenadas_anillos, i)
            p = self.scene().addLine(p1_t[0], p1_t[1], p2_t[0], p2_t[1], pen_tick)
            # zValue=2 → ticks ON TOP de la línea de conexión (zValue=1).
            # Los límites son lo más importante y deben ser visibles aun
            # cuando se cruzan con la línea azul.
            p.setZValue(2)
            if _es_ewlw(val):
                etiqueta = "EW/LW"
            elif _es_espacio(val):
                etiqueta = "Espacio"
            elif _es_quiebre_ini(val):
                etiqueta = "Quiebre"
            elif es_corte:
                etiqueta = "Salto"
            else:
                etiqueta = str(anio)
            p.setToolTip(etiqueta)
            p.anio_texto = etiqueta
            self._items_puntos.append(p)

            # Etiquetas de año — densidad adaptativa según zoom.
            # _escala_vista() = 1/m11: PEQUEÑO = zoom acercado, GRANDE = alejado.
            # Acercado (escala < 0.3) → cada año individual
            # Medio    (0.3–1.5)      → cada 5 años
            # Alejado  (> 1.5)        → solo décadas
            # En todos los casos el font-size se ajusta para ser siempre legible.
            if i > 0 and not es_amar and not _es_ewlw(val) and not _es_quiebre_ini(val):
                escala = self._escala_vista()
                mostrar = self._filtro_densidad_anio(anio, escala)

                if mostrar:
                    # Colocar la etiqueta sobre el segmento AZUL que cierra
                    # el anillo actual: punto i-1 → punto i.
                    coord_inicio = self.coordenadas_anillos[i - 1]
                    coord_sig = coord

                    # Filtro de OVERLAP: si el label estimado se solapa con
                    # el último aceptado, lo saltamos. El usuario lo dijo
                    # claro: nunca dos números encima — preferible que
                    # desaparezcan intercaladamente a que se ilegibles.
                    bbox_nuevo = self._estimar_bbox_label_pantalla(
                        str(anio), coord_sig, coord_inicio)
                    if (self._ultimo_bbox_label_activo is None
                            or not self._labels_se_solapan(
                                self._ultimo_bbox_label_activo, bbox_nuevo)):
                        if anio % INTERVALO_MARCADOR_DECADA == 0:
                            self._items_textos.append(
                                self._crear_marcador_decada(anio, coord_inicio, coord_sig)
                            )
                        else:
                            self._items_textos.append(
                                self._crear_etiqueta_anio(str(anio), "#cccccc",
                                                           coord_inicio, coord_sig)
                            )
                        self._ultimo_bbox_label_activo = bbox_nuevo

        self._dibujar_anomalias()

    def _anio_en_indice(self, idx: int) -> int | None:
        """Devuelve el año calendario del punto en coordenadas_anillos[idx], o None."""
        try:
            anio_medula = self._pestana.spin_medula.value()
            anio_corteza = self._pestana.spin_corteza.value()
            medula_a_corteza = self._pestana.combo_dir.currentIndex() == 0
        except Exception:
            return None
        anillos_validos = 0
        for i in range(1, idx + 1):
            val_sale_b = self.es_salto[i + 1] if i + 1 < len(self.es_salto) else False
            if (not _es_amarillo(self.es_salto[i])
                    and not _es_ewlw(self.es_salto[i])
                    and not _es_espacio(val_sale_b)):
                anillos_validos += 1
        if medula_a_corteza:
            return (anio_medula + anillos_validos - 1) if idx > 0 else (anio_medula - 1)
        else:
            return (anio_corteza - anillos_validos + 1) if idx > 0 else anio_corteza

    def _dibujar_anomalias(self):
        """Dibuja marcas de color sobre los ticks de anillos con anomalía registrada."""
        for item in self._items_anomalias:
            self.scene().removeItem(item)
        self._items_anomalias.clear()

        if not self.anomalias or not self.coordenadas_anillos:
            return

        # Construir mapa anio→indice
        anio_a_idx = {}
        try:
            anio_medula = self._pestana.spin_medula.value()
            anio_corteza = self._pestana.spin_corteza.value()
            medula_a_corteza = self._pestana.combo_dir.currentIndex() == 0
        except Exception:
            return

        anillos_validos = 0
        for i, coord in enumerate(self.coordenadas_anillos):
            val = self.es_salto[i] if i > 0 else False
            val_sale_a = self.es_salto[i + 1] if i + 1 < len(self.es_salto) else False
            if (i > 0
                    and not _es_amarillo(val)
                    and not _es_ewlw(val)
                    and not _es_espacio(val_sale_a)):
                anillos_validos += 1
            if medula_a_corteza:
                anio = (anio_medula + anillos_validos - 1) if i > 0 else (anio_medula - 1)
            else:
                anio = (anio_corteza - anillos_validos + 1) if i > 0 else anio_corteza
            anio_a_idx[anio] = i

        for anio, tipo in self.anomalias.items():
            idx = anio_a_idx.get(anio)
            if idx is None:
                continue
            coord = self.coordenadas_anillos[idx]
            color = TIPOS_ANOMALIA.get(tipo, ("", "", "#CC0000"))[2]
            p1, p2 = self._perp_tick(self.coordenadas_anillos, idx, largo_px_pantalla=22.0)
            dpi = self._pixeles_por_mm * MM_POR_PULGADA
            grosor_anom = max(2.0, min(6.0, 3.0 * dpi / 2400.0))
            pen = QPen(QColor(color))
            pen.setCosmetic(True); pen.setWidthF(grosor_anom)
            item = self.scene().addLine(p1[0], p1[1], p2[0], p2[1], pen)
            etiqueta_tipo = TIPOS_ANOMALIA.get(tipo, ("", tipo, ""))[1]
            item.setToolTip(f"{anio}: {etiqueta_tipo}")
            self._items_anomalias.append(item)

    def _crear_marcador_decada(
        self,
        anio: int,
        coord_actual: tuple,
        coord_anterior: tuple,
    ):
        if anio % INTERVALO_MARCADOR_CENTENA == 0:
            texto, color = f"••• {anio}", "#FFD700"
        elif anio % INTERVALO_MARCADOR_CINCUENTENA == 0:
            texto, color = f"•• {anio}", "#5cb85c"
        else:
            texto, color = f"• {anio}", "#ffffff"

        return self._crear_etiqueta_anio(texto, color, coord_actual, coord_anterior)

    def _filtro_densidad_anio(self, anio: int, escala: float) -> bool:
        """Decide si mostrar la etiqueta de un año según el nivel de zoom.

        5 niveles de zoom para evitar recargar la imagen cuando se aleja:
          - Muy acercado (escala < 0.3): cada año
          - Acercado    (0.3 ≤ escala < 1.5): cada 5
          - Medio       (1.5 ≤ escala < 3.5): cada 10
          - Alejado     (3.5 ≤ escala < 7.0): cada 50
          - Muy alejado (escala ≥ 7.0): cada 100

        Acéptese también años 0 y negativos (Schulman) usando módulo positivo.
        """
        a = abs(anio)
        if escala < 0.3:
            return True
        elif escala < 1.5:
            return a % 5 == 0
        elif escala < 3.5:
            return a % 10 == 0
        elif escala < 7.0:
            return a % 50 == 0
        else:
            return a % 100 == 0

    def _estimar_bbox_label_pantalla(
        self,
        texto: str,
        coord_actual: tuple,
        coord_anterior: tuple,
        offset_perpendicular_px: float = 8.0,
    ) -> tuple:
        """Estima la bounding box del label en píxeles de PANTALLA sin
        dibujarlo. Devuelve (cx, cy, ancho, alto) en píxeles relativos al
        origen de la escena dividido por la escala — sirve para chequeos
        de overlap entre labels consecutivos.

        Usa QFontMetrics para medir el ANCHO REAL del texto (antes el
        cálculo simple `chars * 8 + 6` era demasiado conservador para
        fuente Arial Bold y subestimaba los labels — sobre todo cuando el
        zoom era alto y el `fs_pantalla` efectivo superaba los 14 px,
        causando la superposición visible que reportó el usuario).
        """
        from PyQt6.QtGui import QFont, QFontMetrics

        escala = self._escala_vista()

        # Font-size en escena (igual cálculo que _crear_etiqueta_anio).
        # El piso de 8 evita texto microscópico, pero implica que cuando
        # hay mucho zoom-in (escala chica) el render en PANTALLA es más
        # grande que 14 px → labels más anchos de lo esperado.
        fs_escena = max(8, int(14 * escala))
        # Tamaño efectivo en pantalla del font:
        #   fs_escena = font-size en unidades de escena
        #   m11 = 1/escala → pantalla = escena * m11 = escena / escala
        fs_pantalla = fs_escena / escala if escala > 1e-9 else fs_escena

        # Medir el ancho real del texto con QFontMetrics
        font = QFont("Arial")
        font.setBold(True)
        font.setPixelSize(max(8, int(round(fs_pantalla))))
        fm = QFontMetrics(font)
        ancho_texto_px = fm.horizontalAdvance(texto)
        alto_texto_px = fm.height()

        # Padding HTML (1px arriba/abajo, 3px izq/der) + documentMargin
        # del QGraphicsTextItem (default 4 px cada lado).
        ancho_px = ancho_texto_px + 6 + 8  # HTML pad horizontal + doc margin
        alto_px = alto_texto_px + 2 + 8     # HTML pad vertical + doc margin

        # Punto medio del segmento en escena
        mid_x = (coord_actual[0] + coord_anterior[0]) / 2
        mid_y = (coord_actual[1] + coord_anterior[1]) / 2

        # Offset perpendicular dinámico (misma fórmula que _crear_etiqueta_anio)
        if offset_perpendicular_px > 0:
            seg_dx = coord_actual[0] - coord_anterior[0]
            seg_dy = coord_actual[1] - coord_anterior[1]
            seg_norm = math.hypot(seg_dx, seg_dy)
            if seg_norm > 1e-6:
                perp_dx = seg_dy / seg_norm
                perp_dy = -seg_dx / seg_norm
                extension_perp_px = (
                    abs(perp_dx) * ancho_px / 2.0
                    + abs(perp_dy) * alto_px / 2.0
                )
                offset_total_px = extension_perp_px + offset_perpendicular_px
                offset_escena = offset_total_px * escala
                mid_x += perp_dx * offset_escena
                mid_y += perp_dy * offset_escena

        # Posición en pantalla (proxy: solo las DIFERENCIAS entre labels
        # son relevantes para el test de overlap)
        cx_pantalla = mid_x / escala if escala > 1e-9 else mid_x
        cy_pantalla = mid_y / escala if escala > 1e-9 else mid_y
        return (cx_pantalla, cy_pantalla, ancho_px, alto_px)

    def _labels_se_solapan(self, bbox_a: tuple, bbox_b: tuple,
                            margen_px: float = 2.0) -> bool:
        """True si dos bboxes (cx, cy, ancho, alto) en pantalla se tocan
        con menos de `margen_px` de espacio entre ellas. Test AABB.
        """
        cx_a, cy_a, w_a, h_a = bbox_a
        cx_b, cy_b, w_b, h_b = bbox_b
        dx = abs(cx_a - cx_b)
        dy = abs(cy_a - cy_b)
        sep_x_min = (w_a + w_b) / 2 + margen_px
        sep_y_min = (h_a + h_b) / 2 + margen_px
        return dx < sep_x_min and dy < sep_y_min

    def _crear_etiqueta_anio(
        self,
        texto: str,
        color: str,
        coord_actual: tuple,
        coord_anterior: tuple,
        offset_perpendicular_px: float = 8.0,
        bg_color: str = "rgba(0,0,0,0.60)",
    ):
        """
        Crea una etiqueta de texto cuyo tamaño en pantalla es siempre ~14 px,
        independientemente del nivel de zoom.

        OFFSET PERPENDICULAR DINÁMICO:
        El parámetro `offset_perpendicular_px` es solo el MARGEN extra
        respecto al borde más cercano del label. El offset real se calcula
        como: extensión_perpendicular_del_label + margen.

        Por qué:
          - Si el segmento es horizontal, la perpendicular es vertical;
            el label se desplaza hacia arriba y la dimensión relevante
            es su ALTURA → el offset = altura/2 + margen.
          - Si el segmento es vertical, la perpendicular es horizontal;
            el label se desplaza lateralmente y la dimensión relevante
            es su ANCHO → el offset = ancho/2 + margen. Esto es crítico
            porque sin ello los números ("1888", "1887"...) tapaban la
            línea azul vertical.
          - En diagonales, la extensión se compone de ambas dimensiones
            según el seno/coseno del ángulo del segmento.

        Geometría: para una caja UPRIGHT de tamaño W×H, su extensión en
        la dirección del vector perpendicular unitario (perp_dx, perp_dy)
        es:  |perp_dx| · W/2  +  |perp_dy| · H/2
        """
        # Tamaño objetivo en píxeles de pantalla
        PX_OBJETIVO = 14
        fs = max(8, int(PX_OBJETIVO * self._escala_vista()))

        item = self.scene().addText("")
        item.setHtml(
            f"<div style='background-color:{bg_color}; color:{color}; "
            f"padding:1px 3px; border-radius:3px; font-weight:bold; "
            f"font-family:Arial; font-size:{fs}px; white-space:nowrap;'>{texto}</div>"
        )
        rect = item.boundingRect()
        mid_x = (coord_actual[0] + coord_anterior[0]) / 2
        mid_y = (coord_actual[1] + coord_anterior[1]) / 2

        if offset_perpendicular_px > 0:
            seg_dx = coord_actual[0] - coord_anterior[0]
            seg_dy = coord_actual[1] - coord_anterior[1]
            seg_norm = math.hypot(seg_dx, seg_dy)
            if seg_norm > 1e-6:
                # Perpendicular CW al segmento (en Qt y crece hacia abajo,
                # CW pone la etiqueta "arriba" para segmentos horizontales
                # de izq a der)
                perp_dx = seg_dy / seg_norm
                perp_dy = -seg_dx / seg_norm

                # Tamaño del label en píxeles de PANTALLA.
                # El bounding rect está en escena; dividimos por escala
                # para convertir a píxeles de pantalla (porque el font
                # se calculó como 14 * escala).
                escala = self._escala_vista()
                rect_w_px = rect.width() / escala if escala > 0 else rect.width()
                rect_h_px = rect.height() / escala if escala > 0 else rect.height()

                # Extensión del label en la dirección perpendicular
                # al segmento (geometría AABB proyectada sobre un eje).
                extension_perp_px = (
                    abs(perp_dx) * rect_w_px / 2.0
                    + abs(perp_dy) * rect_h_px / 2.0
                )

                # Offset total = extensión necesaria + margen libre.
                # Así garantizamos que el borde del label queda a
                # `offset_perpendicular_px` de la línea, sin importar
                # la orientación del segmento.
                offset_total_px = extension_perp_px + offset_perpendicular_px
                offset_escena = offset_total_px * escala

                mid_x += perp_dx * offset_escena
                mid_y += perp_dy * offset_escena

        item.setPos(mid_x - rect.width() / 2, mid_y - rect.height() / 2)
        return item

    # -------------------------------------------------------------------------
    # Cálculos
    # -------------------------------------------------------------------------

    def recalcular_todas_las_distancias(self):
        """
        Calcula el ancho de cada anillo.

        Estructura de es_salto:
          False    → segmento normal (cuenta, cierra anillo)
          True     → salto (no cuenta, cierra anillo previo, inicia nuevo)
          "espacio"→ quiebre intra-anillo (no cuenta, no cierra)
          "ew_lw"  → límite EW/LW (cuenta, NO cierra — el anillo continúa)

        Un anillo se cierra cuando se encuentra un segmento con val=False
        (el límite del anillo) o al final del camino.
        Los segmentos ew_lw se acumulan junto con los False del mismo anillo.
        """
        coords = self.coordenadas_anillos
        n = len(coords)
        distancias = []
        acum = 0.0

        for i in range(1, n):
            val = self.es_salto[i]
            d = math.hypot(
                coords[i][0] - coords[i-1][0],
                coords[i][1] - coords[i-1][1],
            ) / self.pixeles_por_mm

            if _es_salto_real(val):
                # Salto real: emitir lo acumulado y reiniciar
                if acum > 0:
                    distancias.append(acum)
                acum = 0.0

            elif _es_espacio(val):
                # Quiebre: no suma, no cierra
                pass

            elif _es_ewlw(val) or _es_quiebre_ini(val):
                # Límite EW/LW o inicio de quiebre: suma pero no cierra
                acum += d

            else:
                # val is False: segmento normal, suma y cierra el anillo
                acum += d
                distancias.append(acum)
                acum = 0.0

        # Si quedó algo al final (último anillo sin límite explícito)
        if acum > 0:
            distancias.append(acum)

        self.mediciones_actualizadas.emit(distancias)

    @staticmethod
    def _dist_punto_segmento(
        px: float, py: float,
        x1: float, y1: float,
        x2: float, y2: float,
    ) -> float:
        l2 = (x1 - x2) ** 2 + (y1 - y2) ** 2
        if l2 == 0:
            return math.hypot(px - x1, py - y1)
        t = max(0.0, min(1.0, ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / l2))
        return math.hypot(px - (x1 + t * (x2 - x1)), py - (y1 + t * (y2 - y1)))

    def _tolerancia_punto(self) -> float:
        return TOLERANCIA_CLICK_PUNTO / self.transform().m11()

    def _tolerancia_segmento(self) -> float:
        return TOLERANCIA_CLICK_SEGMENTO / self.transform().m11()

    def _indice_punto_mas_cercano(self, x: float, y: float) -> int | None:
        tol = self._tolerancia_punto()
        mejor_idx, mejor_d = None, float("inf")
        for i, (px, py) in enumerate(self.coordenadas_anillos):
            d = math.hypot(px - x, py - y)
            if d < tol and d < mejor_d:
                mejor_d, mejor_idx = d, i
        return mejor_idx

    # =========================================================================
    # Selección múltiple — métodos
    # =========================================================================

    def _construir_panel_seleccion(self):
        """Construye perezosamente el panel HUD para la herramienta de
        selección. Hijo del viewport (no de la escena), así NO se mueve
        cuando el usuario hace pan/zoom.

        El panel solo MUESTRA información (contador + hint). Las acciones
        se ejecutan con los botones que ya existen en la barra de
        herramientas (✕ Borrar, 🔀 Salto, ⊘ Quiebre): cuando se hace clic
        en cualquiera de esos botones estando en MODO_SELECCIONAR con
        selecciones activas, se aplica la acción en bulk a la selección
        en vez de cambiar de modo (ver `DPIInteractiva.set_modo`)."""
        from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel

        self._panel_seleccion = QFrame(self.viewport())
        self._panel_seleccion.setStyleSheet(
            "QFrame { background-color: rgba(20,20,20,235); "
            "border: 1px solid rgba(255,235,59,180); border-radius: 6px; }"
        )

        lay = QHBoxLayout(self._panel_seleccion)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(10)

        # Contador + estado
        self._lbl_seleccion_count = QLabel("🎯 Clic / arrastrá un área para seleccionar")
        self._lbl_seleccion_count.setStyleSheet(
            "color: #FFEB3B; font-weight: bold; font-size: 12px;"
        )
        lay.addWidget(self._lbl_seleccion_count)

        # Separador visual
        sep = QLabel("│")
        sep.setStyleSheet("color: #555; font-size: 14px;")
        lay.addWidget(sep)

        # Hint: las acciones se ejecutan con los botones existentes
        self._lbl_seleccion_hint = QLabel(
            "Aplicar con la barra: <b style='color:#e74c3c;'>✕ Borrar</b>"
            "  ·  <b style='color:#f39c12;'>🔀 Salto</b>"
            "  ·  <b style='color:#9b59b6;'>⊘ Quiebre</b>"
        )
        self._lbl_seleccion_hint.setStyleSheet("color: #cccccc; font-size: 11px;")
        lay.addWidget(self._lbl_seleccion_hint)

        # Mantener referencias compatibles con el código existente, pero
        # ahora ninguna apunta a botones (que fueron eliminados del panel).
        self._btn_borrar_sel = None
        self._btn_salto_sel = None
        self._btn_quiebre_sel = None
        self._btn_limpiar_sel = None

        self._panel_seleccion.adjustSize()
        self._panel_seleccion.setVisible(False)

    def _posicionar_panel_seleccion(self):
        """Coloca el panel en la parte superior central del viewport.
        Llamar después de mostrar el panel o cuando cambia el tamaño del view."""
        if self._panel_seleccion is None:
            return
        self._panel_seleccion.adjustSize()
        vp_w = self.viewport().width()
        panel_w = self._panel_seleccion.width()
        x = max(8, (vp_w - panel_w) // 2)
        y = 8  # 8 px desde el borde superior del viewport
        self._panel_seleccion.move(x, y)

    def _mostrar_panel_seleccion(self, visible: bool):
        if self._panel_seleccion is None:
            self._construir_panel_seleccion()
        if visible:
            self._actualizar_panel_seleccion()
            self._posicionar_panel_seleccion()
            self._panel_seleccion.setVisible(True)
            self._panel_seleccion.raise_()
        else:
            self._panel_seleccion.setVisible(False)

    def _actualizar_panel_seleccion(self):
        """Actualiza solo el contador del panel. Los botones de acción ya
        no están en el panel: están en la barra principal."""
        if self._panel_seleccion is None:
            return
        n = len(self._puntos_seleccionados)
        if n == 0:
            self._lbl_seleccion_count.setText(
                "🎯 Clic / arrastrá un área para seleccionar")
        elif n == 1:
            self._lbl_seleccion_count.setText("🎯 1 seleccionado")
        else:
            self._lbl_seleccion_count.setText(f"🎯 {n} seleccionados")
        self._posicionar_panel_seleccion()

    def _dibujar_marcadores_seleccion(self):
        """Dibuja un círculo amarillo brillante alrededor de cada anillo
        seleccionado. Se redibuja completo cada vez para simplificar."""
        # Limpiar marcadores previos
        for item in self._items_seleccion:
            try:
                self.scene().removeItem(item)
            except Exception:
                pass
        self._items_seleccion.clear()

        if not self._puntos_seleccionados:
            return

        # Radio del círculo de selección en píxeles de PANTALLA constantes
        r_px = 11.0
        r = r_px * self._escala_vista()

        pen = QPen(QColor("#FFEB3B"))  # amarillo brillante
        pen.setCosmetic(True)
        pen.setWidthF(3.0)
        brush = QBrush(Qt.BrushStyle.NoBrush)

        for idx in self._puntos_seleccionados:
            if 0 <= idx < len(self.coordenadas_anillos):
                x, y = self.coordenadas_anillos[idx]
                e = self.scene().addEllipse(
                    x - r, y - r, 2 * r, 2 * r, pen, brush)
                e.setZValue(950)  # arriba de casi todo
                self._items_seleccion.append(e)

    def _toggle_seleccion_punto(self, idx: int):
        """Alterna la selección de un punto: si estaba seleccionado lo
        deselecciona, si no lo agrega a la selección."""
        if idx in self._puntos_seleccionados:
            self._puntos_seleccionados.discard(idx)
        else:
            self._puntos_seleccionados.add(idx)
        self._dibujar_marcadores_seleccion()
        self._actualizar_panel_seleccion()

    def _limpiar_seleccion_completa(self):
        """Vacía la selección y oculta sus marcadores."""
        self._puntos_seleccionados.clear()
        self._dibujar_marcadores_seleccion()
        self._actualizar_panel_seleccion()

    # ── Acciones sobre la selección ──

    def _ejecutar_borrar_seleccionados(self):
        """Borra todos los anillos seleccionados, en cualquier posición
        (inicio, medio, fin). Procesa los índices en orden DESCENDENTE
        para que los índices más altos no se invaliden al hacer pop()
        de los más bajos."""
        if not self._puntos_seleccionados:
            return
        self.guardar_en_historial()
        # Orden descendente: borrar de atrás hacia adelante para no
        # desplazar los índices que aún no procesamos
        indices = sorted(self._puntos_seleccionados, reverse=True)
        n_borrados = 0
        for i in indices:
            if 0 <= i < len(self.coordenadas_anillos):
                self.coordenadas_anillos.pop(i)
                if i < len(self.es_salto):
                    self.es_salto.pop(i)
                n_borrados += 1
        self._puntos_seleccionados.clear()
        self._dibujar_marcadores_seleccion()
        self.redibujar_todo()
        self.recalcular_todas_las_distancias()
        self._actualizar_panel_seleccion()
        self.progreso_modificado.emit()

    def _ejecutar_convertir_a_salto(self):
        """Marca los anillos seleccionados como SALTOS (es_salto = True).
        Esto convierte sus segmentos en discontinuidades (amarillas)."""
        if not self._puntos_seleccionados:
            return
        self.guardar_en_historial()
        for i in self._puntos_seleccionados:
            if 0 <= i < len(self.es_salto):
                self.es_salto[i] = True
        self._puntos_seleccionados.clear()
        self._dibujar_marcadores_seleccion()
        self.redibujar_todo()
        self.recalcular_todas_las_distancias()
        self._actualizar_panel_seleccion()
        self.progreso_modificado.emit()

    def _ejecutar_convertir_a_quiebre(self):
        """Marca los anillos seleccionados como QUIEBRES (es_salto = "espacio").
        Convierte el segmento previo en un quiebre intra-anillo, que no
        cuenta como anillo en la cronología."""
        if not self._puntos_seleccionados:
            return
        self.guardar_en_historial()
        for i in self._puntos_seleccionados:
            if 0 <= i < len(self.es_salto):
                self.es_salto[i] = "espacio"
        self._puntos_seleccionados.clear()
        self._dibujar_marcadores_seleccion()
        self.redibujar_todo()
        self.recalcular_todas_las_distancias()
        self._actualizar_panel_seleccion()
        self.progreso_modificado.emit()

    # =========================================================================
    # Eventos de ratón
    # =========================================================================

    def wheelEvent(self, event):
        """
        Zoom con rueda del mouse o touchpad.

        - Mouse wheel: cada "click" envía angleDelta=±120, factor 1.25 / 0.8
          como antes.
        - Touchpad: envía MUCHOS eventos con deltas pequeños (típicamente
          pixelDelta es no-null). Aplicamos un factor proporcional MUY
          pequeño por evento para que el zoom acumulado sea suave en vez
          de exagerado.

        Además debouncing del `redibujar_todo`: en gestos de touchpad llegan
        decenas de eventos por segundo, no tiene sentido redibujar todo
        en cada uno. Esperamos 80 ms de quietud antes de redibujar.
        """
        # Botón central presionado durante el scroll → ignorar zoom (es paneo)
        if event.buttons() & Qt.MouseButton.MiddleButton:
            return

        pixel_delta = event.pixelDelta()
        angle_delta = event.angleDelta()

        if not pixel_delta.isNull():
            # Touchpad de alta resolución: delta en píxeles
            delta_y = pixel_delta.y()
            if delta_y == 0:
                return
            # 200 px ≈ doble zoom. Suave pero responsive.
            factor = 1.0 + (delta_y / 200.0)
        elif not angle_delta.isNull():
            # Rueda de mouse: angleDelta en 1/8 de grado; 120 = un "click"
            delta_y = angle_delta.y()
            if delta_y == 0:
                return
            # Mantener comportamiento clásico: 1.25 por click, escalado
            # proporcionalmente a la magnitud del delta
            factor = 1.25 ** (delta_y / 120.0)
        else:
            return

        # Limitar el factor por evento para evitar saltos enormes
        factor = max(0.5, min(2.0, factor))

        self.scale(factor, factor)

        # Debounce: en vez de redibujar_todo() en cada evento de touchpad,
        # esperamos a que el usuario "termine" de hacer zoom (80 ms de
        # quietud) y entonces redibujamos una vez. Esto evita la "recarga"
        # constante que mencionaba el usuario.
        #
        # IMPORTANTE: el timer se dispara si hay camino activo O si hay
        # caminos inactivos. Antes solo se chequeaba coordenadas_anillos,
        # entonces al terminar un radio (que vacía la lista activa) los
        # caminos guardados se quedaban "congelados" al último zoom desde
        # el cual fueron renderizados.
        if self.coordenadas_anillos or self._caminos_inactivos_data:
            if not hasattr(self, '_zoom_debounce_timer'):
                from PyQt6.QtCore import QTimer
                self._zoom_debounce_timer = QTimer(self)
                self._zoom_debounce_timer.setSingleShot(True)
                self._zoom_debounce_timer.timeout.connect(self._redibujar_post_zoom)
            self._zoom_debounce_timer.start(80)

    def _redibujar_post_zoom(self):
        """Redibuja después del zoom (llamado por el debounce timer)."""
        if self.coordenadas_anillos:
            self.redibujar_todo()
        # Reescalar marcadores de selección si los hay (su radio se
        # calcula con _escala_vista() para mantener tamaño constante
        # en pantalla — al cambiar el zoom hay que regenerarlos).
        if self._puntos_seleccionados:
            self._dibujar_marcadores_seleccion()
        # Re-renderizar caminos inactivos para que labels y ticks usen
        # la escala nueva. Sin esto, los labels viejos quedan con el
        # tamaño de un zoom anterior (lo cual hace aparecer un "100"
        # enorme tras alejar/acercar).
        if self._caminos_inactivos_data:
            self.cargar_caminos_inactivos(self._caminos_inactivos_data)

    def event(self, ev):
        """Intercepta el evento `ShortcutOverride` para que las teclas
        Delete/Backspace en MODO_SELECCIONAR las maneje keyPressEvent (bulk
        borrar selección) en vez del QShortcut global (que cambiaría a
        MODO_BORRAR).

        Qt envía ShortcutOverride al widget con foco ANTES de disparar
        QShortcut. Si el widget acepta el evento, el shortcut se inhibe
        y la tecla llega a keyPressEvent. Si no lo acepta, el shortcut
        toma precedencia.
        """
        if ev.type() == QEvent.Type.ShortcutOverride:
            if self.modo_actual == MODO_SELECCIONAR:
                if ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                    ev.accept()
                    return True
        return super().event(ev)

    def mousePressEvent(self, event):
        pos = self.mapToScene(event.pos())
        x, y = pos.x(), pos.y()

        # ── Botón central (scroll): paneo inmediato ───────────────────────
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = True
            self._last_pan_pos = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        # ── Botón derecho: salto rápido (solo en modos de medición) ───────
        if event.button() == Qt.MouseButton.RightButton:
            if self.modo_actual in (MODO_MEDIR, MODO_SALTO, MODO_INSERTAR,
                                    MODO_BORRAR, MODO_ANOMALIA):
                modo_previo = self.modo_actual
                self.modo_actual = MODO_SALTO
                self._paso_medir(x, y)
                # _paso_medir ya restaura MODO_MEDIR tras un salto;
                # si el modo previo era especial (anomalía, etc.) lo respetamos
                if modo_previo not in (MODO_MEDIR, MODO_SALTO):
                    self.modo_actual = modo_previo
                    try:
                        self._pestana._botones_modo[modo_previo].setChecked(True)
                    except Exception:
                        pass
            return

        # ── Botón izquierdo: comportamiento normal por modo ───────────────
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)

        if self.modo_actual == MODO_MOVER:
            self._iniciar_mover(x, y, event)
            return

        if self.modo_actual == MODO_INSERTAR:
            self._insertar_en_segmento(x, y)
            return

        if self.modo_actual == MODO_BORRAR:
            self._borrar_punto_cercano(x, y)
            return

        if self.modo_actual == MODO_SELECCIONAR:
            # En MODO_SELECCIONAR hay dos comportamientos según dónde se
            # haga clic:
            #   - Sobre un anillo (a tolerancia): se guarda como candidato
            #     a toggle, que se ejecutará al soltar (mouseRelease) si
            #     no hubo arrastre.
            #   - Sobre área vacía: se inicia un rectángulo de selección
            #     que se completará al soltar. Todos los anillos dentro
            #     del rectángulo se agregan a la selección.
            # Esto permite seleccionar varios anillos rápidamente
            # arrastrando un área (request del usuario).
            idx = self._indice_punto_mas_cercano(x, y)
            self._sel_click_pos = (x, y)
            if idx is not None:
                # Posible toggle de un punto — lo decidiremos en release
                self._sel_click_idx_candidato = idx
                self._sel_drag_iniciado = False
                self._sel_drag_start = (x, y)
            else:
                # Click en área vacía → iniciar drag rectangle
                self._sel_click_idx_candidato = None
                self._sel_drag_iniciado = True
                self._sel_drag_start = (x, y)
                # Si había un rectángulo previo (caso raro), removerlo
                if self._sel_drag_rect_item is not None:
                    try:
                        self.scene().removeItem(self._sel_drag_rect_item)
                    except Exception:
                        pass
                    self._sel_drag_rect_item = None
            return

        if self.modo_actual == MODO_CALIBRAR:
            self._paso_calibracion(x, y)
            return

        if self.modo_actual in (MODO_MEDIR, MODO_SALTO):
            self._paso_medir(x, y)
            return

        if self.modo_actual == MODO_AUTODETECT:
            self._paso_autodetect(x, y)
            return

        if self.modo_actual == MODO_ANOMALIA:
            self._paso_anomalia(x, y)
            return

        if self.modo_actual == MODO_PITH:
            self._paso_pith(x, y)
            return

        if self.modo_actual == MODO_ESPACIO:
            self._paso_espacio(x, y)
            return

        if self.modo_actual == MODO_EWLW:
            self._paso_ewlw(x, y)
            return

        super().mousePressEvent(event)

    def _paso_anomalia(self, x: float, y: float):
        """
        Clic en MODO_ANOMALIA: asigna el tipo activo (seleccionado en la barra)
        al anillo más cercano. Si el anillo ya tiene ese mismo tipo, lo quita (toggle).
        """
        idx = self._indice_punto_mas_cercano(x, y)
        if idx is None or idx == 0:
            return
        anio = self._anio_en_indice(idx)
        if anio is None:
            return

        # Obtener tipo activo desde el combo de la pestaña
        try:
            tipo_activo = self._pestana.combo_tipo_anomalia.currentData()
        except Exception:
            return
        if not tipo_activo:
            return

        tipo_actual = self.anomalias.get(anio)
        if tipo_actual == tipo_activo:
            # Toggle: quitar si ya tiene el mismo tipo
            self.anomalias.pop(anio, None)
        else:
            # Asignar (sobreescribe si tenía otro tipo)
            self.anomalias[anio] = tipo_activo

        self._dibujar_anomalias()
        try:
            self._pestana._anomalias_modificadas()
        except Exception:
            pass

    def _paso_pith(self, x: float, y: float):
        """
        Modo interactivo de estimación de anillos al centro.
        Flujo guiado en 3 pasos:
          Clic 1: Extremo izquierdo del arco interno visible.
          Clic 2: Extremo derecho del arco → se dibuja guía entre ambos.
          Clic 3: Punto central del arco (la curvatura) → ajuste de círculo.
          Clics 4+: Puntos adicionales para refinar el ajuste.
        Enter confirma; Esc cancela.
        """
        pen_punto = QPen(QColor("#FF8C00"))
        pen_punto.setCosmetic(True)
        pen_punto.setWidthF(3.0)
        brush_punto = QBrush(QColor("#FF8C00"))

        self._puntos_pith.append((x, y))
        n = len(self._puntos_pith)

        # Marcador visual del punto
        r = 8 * self._escala_vista()
        marker = self.scene().addEllipse(x - r, y - r, 2*r, 2*r,
                                          pen_punto, brush_punto)
        self._items_pith.append(marker)

        # ── Paso 1: primer extremo ──
        if n == 1:
            try:
                self._pestana.label_estado.setText(
                    "🎯 Paso 2/3: Haz clic en el otro EXTREMO del arco.")
            except Exception:
                pass
            return

        # ── Paso 2: segundo extremo → dibujar guía ──
        if n == 2:
            p1, p2 = self._puntos_pith
            # Línea guía entre los dos extremos
            pen_guia = QPen(QColor("#FFD700"))
            pen_guia.setCosmetic(True)
            pen_guia.setWidthF(1.5)
            pen_guia.setStyle(Qt.PenStyle.DashDotLine)
            linea_guia = self.scene().addLine(p1[0], p1[1], p2[0], p2[1], pen_guia)
            self._items_pith_extras.append(linea_guia)

            # Punto medio (referencia visual)
            mx, my = (p1[0]+p2[0])/2, (p1[1]+p2[1])/2
            r_mid = 5 * self._escala_vista()
            pen_mid = QPen(QColor("#FFD700"))
            pen_mid.setCosmetic(True)
            pen_mid.setWidthF(2.0)
            mid_marker = self.scene().addEllipse(
                mx-r_mid, my-r_mid, 2*r_mid, 2*r_mid,
                pen_mid, QBrush(Qt.BrushStyle.NoBrush))
            self._items_pith_extras.append(mid_marker)

            # NOTA: el círculo guía amarillo que mostraba un preview tras
            # el 2° clic fue removido a petición del usuario ("el círculo
            # no ayuda en nada"). Después del 2° clic solo se mantiene la
            # línea entre los dos puntos como referencia visual. El círculo
            # real (calculado por mínimos cuadrados a través de los 3 puntos)
            # se dibuja después del 3° clic en _actualizar_circulo_pith.

            try:
                self._pestana.label_estado.setText(
                    "🎯 Paso 3/3: Haz clic en un 3er punto sobre el MISMO "
                    "anillo visible (formando un arco claro con los otros 2).")
            except Exception:
                pass
            return

        # ── Paso 3+: ajustar círculo ──
        self._limpiar_preview_pith_mouse()  # limpiar preview del mouse
        self._actualizar_circulo_pith()
        try:
            self._pestana.label_estado.setText(
                f"🎯 Círculo ajustado con {n} puntos. "
                "Enter para confirmar · clic para agregar más puntos · Esc para cancelar.")
        except Exception:
            pass

    def _preview_pith_mouse(self, x: float, y: float):
        """Dibuja un arco preview cuando hay 2 puntos y el mouse se mueve.

        Estilo: línea más gruesa, color amarillo brillante, opacidad alta
        para que sea visible sobre la textura de la madera (la versión
        anterior usaba opacidad 0.5 + naranja, que se confundía con los
        anillos).
        """
        if len(self._puntos_pith) != 2:
            return
        # Limpiar preview anterior
        self._limpiar_preview_pith_mouse()

        puntos_temp = list(self._puntos_pith) + [(x, y)]
        resultado = ajustar_circulo_minimos_cuadrados(puntos_temp)
        if resultado is None:
            return

        cx, cy, radio = resultado
        # Evitar dibujar círculos absurdamente grandes (cuando el mouse
        # cae casi en línea con p1-p2, el radio tiende a infinito).
        # Tamaño máximo razonable: 10x la distancia entre p1 y p2.
        p1, p2 = self._puntos_pith
        d12 = math.hypot(p2[0]-p1[0], p2[1]-p1[1])
        if radio > d12 * 10:
            return

        pen_preview = QPen(QColor("#FFD700"))   # amarillo brillante
        pen_preview.setCosmetic(True)
        pen_preview.setWidthF(2.5)
        pen_preview.setStyle(Qt.PenStyle.DashLine)

        item = self.scene().addEllipse(
            cx - radio, cy - radio, 2*radio, 2*radio,
            pen_preview, QBrush(Qt.BrushStyle.NoBrush))
        item.setOpacity(0.85)
        # Z-value alto: garantiza visibilidad sobre la imagen
        item.setZValue(1000)
        self._item_pith_mouse_preview = item

    def _limpiar_preview_pith_mouse(self):
        """Elimina el arco preview del mouse."""
        item = getattr(self, '_item_pith_mouse_preview', None)
        if item is not None:
            try:
                self.scene().removeItem(item)
            except Exception:
                pass
            self._item_pith_mouse_preview = None

    def _actualizar_circulo_pith(self):
        """Redibuja el círculo ajustado con la estimación de anillos faltantes.

        Algoritmo (simple y correcto para arcos en CUALQUIER dirección):
          1. Ajustar círculo a los puntos marcados (Duncan GM clásico)
          2. El centro del círculo ajustado es la médula estimada
          3. Distancia faltante = `dist(p_inner, centro)` — distancia
             euclidiana desde el primer anillo medido (el más cercano a la
             médula) hasta el centro estimado
          4. Cantidad de anillos = distancia_mm / promedio_ancho_internos

        Importante (corrección del error anterior):
          - NO proyectar sobre eje radial — los anillos son perpendiculares
            al radio, así que un arco bien marcado sobre un anillo tiene
            puntos casi-perpendiculares al radio. Proyectar el centro
            (que está sobre el eje radial) sobre ese mismo eje desde p0
            funciona, PERO requiere que la dirección de medición esté
            "alineada" con la suposición del código. Como p_inner puede
            ser p0 o pK según combo_dir, y la dirección de medición puede
            ir en cualquier sentido, la proyección era inestable.
          - La distancia euclidiana funciona para arcos en cualquier
            dirección (horizontal, vertical, diagonal, etc).

        p_inner se determina por `combo_dir`:
          - med_a_cor=True (medición médula→corteza): p_inner = coords[0]
          - med_a_cor=False (medición corteza→médula): p_inner = coords[-1]
        """
        resultado = ajustar_circulo_minimos_cuadrados(self._puntos_pith)
        if resultado is None:
            try:
                self._pestana.label_estado.setText(
                    "⚠ Puntos colineales. Mueve uno para que el ajuste pueda calcular.")
            except Exception:
                pass
            return
        cx, cy, radio = resultado

        # Detectar fit realmente degenerado: solo cuando los puntos son
        # casi exactamente colineales (radio MUCHÍSIMO más grande que el
        # span de los puntos). Umbral relajado: 50× en lugar de 15×.
        # Un anillo grande puede tener radio 20× el chord medido y NO ser
        # degenerado.
        seg_max = 0.0
        for i in range(len(self._puntos_pith)):
            for j in range(i + 1, len(self._puntos_pith)):
                p_i = self._puntos_pith[i]
                p_j = self._puntos_pith[j]
                seg_max = max(seg_max, math.hypot(p_j[0]-p_i[0], p_j[1]-p_i[1]))
        fit_degenerado = (seg_max > 0 and radio > seg_max * 50)

        # Limpiar items previos del círculo
        if self._item_circulo_pith is not None:
            try:
                self.scene().removeItem(self._item_circulo_pith)
            except Exception:
                pass
        for item in self._items_pith_extras:
            try:
                self.scene().removeItem(item)
            except Exception:
                pass
        self._items_pith_extras.clear()

        color_fit = "#FF6B6B" if fit_degenerado else "#FF8C00"

        # ── Círculo ajustado punteado ──
        pen_circ = QPen(QColor(color_fit))
        pen_circ.setCosmetic(True)
        pen_circ.setWidthF(2.0)
        pen_circ.setStyle(Qt.PenStyle.DashLine)
        self._item_circulo_pith = self.scene().addEllipse(
            cx - radio, cy - radio, 2*radio, 2*radio,
            pen_circ, QBrush(Qt.BrushStyle.NoBrush)
        )
        self._item_circulo_pith.setZValue(900)

        # ── Cruz en el centro del fit (puede caer fuera de la imagen) ──
        r_centro = 10 * self._escala_vista()
        pen_centro = QPen(QColor(color_fit))
        pen_centro.setCosmetic(True)
        pen_centro.setWidthF(2.5)
        linea_h = self.scene().addLine(cx - r_centro, cy, cx + r_centro, cy, pen_centro)
        linea_v = self.scene().addLine(cx, cy - r_centro, cx, cy + r_centro, pen_centro)
        linea_h.setZValue(901)
        linea_v.setZValue(901)
        self._items_pith_extras.extend([linea_h, linea_v])

        # ── Cálculo de distancia faltante (fórmula de Duncan GM) ──
        coords = self.coordenadas_anillos
        if coords:
            # Determinar cuál extremo de la medición es el más cercano a la
            # médula (p_inner), según la dirección de medición elegida.
            try:
                med_a_cor = self._pestana.combo_dir.currentIndex() == 0
            except Exception:
                med_a_cor = True

            if med_a_cor:
                p_inner = coords[0]
            else:
                p_inner = coords[-1] if len(coords) >= 2 else coords[0]

            # ── MÉTRICA PRIMARIA: distancia faltante = dist(p_inner, centro)
            # La médula está en el centro del círculo ajustado (fit center).
            # La distancia faltante SIEMPRE se cuenta desde p_inner (el
            # primer anillo medido más cercano a la médula) hasta el centro.
            #
            # Esto es independiente de DÓNDE marque el usuario el arco. El
            # arco marcado es solo la herramienta para encontrar la médula
            # (vía fit por mínimos cuadrados, Duncan GM 1989). Si el usuario
            # marca el anillo más interno visible: dist ≈ radio del fit.
            # Si marca un anillo más exterior (porque el interno no se ve
            # claro): radio > dist, pero los anillos faltantes se siguen
            # contando desde p_inner.
            dist_p_inner_centro_mm = (
                math.hypot(cx - p_inner[0], cy - p_inner[1])
                / self.pixeles_por_mm)
            missing_mm = dist_p_inner_centro_mm

            # Radio del fit como información secundaria (diagnóstico)
            radio_fit_mm = radio / self.pixeles_por_mm

            # Línea desde p_inner hasta el centro del fit (visualización
            # de la DISTANCIA FALTANTE — la métrica primaria)
            pen_dist = QPen(QColor(color_fit))
            pen_dist.setCosmetic(True)
            pen_dist.setWidthF(2.5)
            pen_dist.setStyle(Qt.PenStyle.SolidLine)
            linea_dist = self.scene().addLine(
                p_inner[0], p_inner[1], cx, cy, pen_dist)
            linea_dist.setZValue(903)
            self._items_pith_extras.append(linea_dist)

            # Línea desde el centro hasta uno de los puntos marcados, punteada
            # (visualización del radio del fit como referencia secundaria)
            if self._puntos_pith:
                p_marca = self._puntos_pith[0]
                pen_radio = QPen(QColor(color_fit))
                pen_radio.setCosmetic(True)
                pen_radio.setWidthF(1.5)
                pen_radio.setStyle(Qt.PenStyle.DotLine)
                linea_radio = self.scene().addLine(
                    cx, cy, p_marca[0], p_marca[1], pen_radio)
                linea_radio.setZValue(902)
                self._items_pith_extras.append(linea_radio)

            # ── Estimación de anillos (RG / BAI / Promedio) ──
            # El usuario elige el método en el diálogo. RG es el default
            # (estándar usado por CooRecorder). BAI y Promedio están
            # disponibles para mayor precisión según Altman et al. (2016).
            #
            # Lógica común (anillos de referencia):
            #   1. Encontrar el anillo medido geométricamente más cercano
            #      al arco marcado (su distancia al centro del fit es la
            #      más parecida al radio del fit).
            #   2. Desde ese anillo "ancla", tomar exactamente N anillos
            #      contando HACIA LA CORTEZA (N = `_pith_n_int`).
            #   3. Los anchos de esos N anillos son la referencia.
            #
            # RG: n = distancia_faltante / promedio_ancho
            # BAI: n = (π × distancia²) / mean_BAI_referencia
            #      donde mean_BAI_ref se calcula con las distancias
            #      absolutas de los anillos al centro del fit (que ahora
            #      conocemos).
            # Promedio: (n_rg + n_bai) / 2
            n_est = None
            mean_w_mm = None
            n_used = 0
            n_rg = None
            n_bai = None
            try:
                pestana = self._pestana
                n_int_user = getattr(pestana, '_pith_n_int', 5)
                metodo_calc = getattr(pestana, '_pith_metodo_calculo', 'rg')
                dm = pestana.datos_medidos

                if dm and len(coords) >= 2:
                    # Paso 1: anillo medido más cercano al arco marcado
                    distancias_coord = [
                        math.hypot(coords[i][0] - cx, coords[i][1] - cy)
                        for i in range(len(coords))
                    ]
                    diferencias = [abs(d - radio) for d in distancias_coord]
                    idx_anchor = diferencias.index(min(diferencias))

                    # Paso 2: tomar N anillos hacia corteza
                    max_idx_dm = len(dm) - 1
                    if med_a_cor:
                        start = min(idx_anchor, max_idx_dm)
                        end = min(start + n_int_user, max_idx_dm + 1)
                        indices = list(range(start, end))
                    else:
                        start = max(0, min(idx_anchor, max_idx_dm))
                        end_back = max(-1, start - n_int_user)
                        indices = list(range(start, end_back, -1))

                    # Paso 3: anchos de referencia
                    anchos_ref = [
                        dm[i]["ancho_mm"] for i in indices
                        if 0 <= i <= max_idx_dm and dm[i]["ancho_mm"] > 0
                    ]

                    if anchos_ref:
                        mean_w_mm = sum(anchos_ref) / len(anchos_ref)
                        n_used = len(anchos_ref)

                        # ── Cálculo RG ──
                        if mean_w_mm > 0:
                            n_rg = missing_mm / mean_w_mm

                        # ── Cálculo BAI ──
                        # Para BAI necesitamos la distancia del anillo ancla
                        # al centro del fit (posición absoluta desde la médula).
                        # Ahora SÍ podemos calcularlo porque tenemos cx, cy.
                        # No tenemos el problema de dependencia circular que
                        # mencionaba antes.
                        r_anchor_mm = distancias_coord[idx_anchor] / self.pixeles_por_mm
                        sum_w_ref = sum(anchos_ref)
                        r_outer_ref_mm = r_anchor_mm + sum_w_ref
                        # Área basal total cubierta por los N anillos de
                        # referencia (en mm²)
                        ba_total_ref_mm2 = math.pi * (
                            r_outer_ref_mm**2 - r_anchor_mm**2)
                        mean_bai_mm2 = ba_total_ref_mm2 / len(anchos_ref)
                        # Área basal faltante = disco de radio missing_mm
                        # (desde el centro hasta p_inner)
                        ba_faltante_mm2 = math.pi * missing_mm**2
                        if mean_bai_mm2 > 0:
                            n_bai = ba_faltante_mm2 / mean_bai_mm2

                        # ── Selección final según método ──
                        if metodo_calc == "rg":
                            n_est = n_rg
                        elif metodo_calc == "bai":
                            n_est = n_bai
                        elif metodo_calc == "promedio":
                            if n_rg is not None and n_bai is not None:
                                n_est = (n_rg + n_bai) / 2.0
                            elif n_rg is not None:
                                n_est = n_rg
                            else:
                                n_est = n_bai
                        else:
                            n_est = n_rg  # fallback
            except Exception:
                pass

            # Etiqueta del método usado (para el HUD)
            etiqueta_metodo = {
                "rg": "RG",
                "bai": "BAI",
                "promedio": "Promedio RG+BAI"
            }.get(getattr(self._pestana, '_pith_metodo_calculo', 'rg'), 'RG')

            # Construir texto del HUD según calidad del resultado
            if fit_degenerado:
                hud_text = (
                    "⚠ Estimación no confiable. "
                    "Los puntos parecen estar casi en línea recta. "
                    "Marca puntos formando un arco más curvado."
                )
                hud_color = "#FF6B6B"
            elif n_est is None:
                hud_text = (
                    f"📏 Distancia p_inner → médula: {missing_mm:.1f} mm "
                    f"(necesitas anillos medidos para estimar cantidad)"
                )
                hud_color = "#FF8C00"
            elif n_est > 500:
                hud_text = (
                    f"⚠ {missing_mm:.0f} mm → ≈{round(n_est)} anillos. "
                    f"Parece demasiado, ¿marcaste sobre el borde externo?"
                )
                hud_color = "#FF6B6B"
            else:
                # Texto principal con el método elegido + comparación si
                # se calcularon los otros dos
                comparacion = ""
                if n_rg is not None and n_bai is not None:
                    comparacion = (
                        f"  ·  RG: {round(n_rg)}  ·  BAI: {round(n_bai)}"
                    )
                hud_text = (
                    f"🪵 Faltan ≈{round(n_est)} anillos [{etiqueta_metodo}]  ·  "
                    f"Distancia: {missing_mm:.1f} mm  "
                    f"(promedio anillo: {mean_w_mm:.2f} mm  ·  "
                    f"radio arco: {radio_fit_mm:.1f} mm{comparacion})"
                )
                hud_color = "#FF8C00"

            # Mostrar en HUD del viewport
            self._actualizar_hud_pith(hud_text, hud_color)

    def _confirmar_pith(self):
        """Llamado desde PestanaImagen cuando el usuario confirma (Enter/botón)."""
        resultado = ajustar_circulo_minimos_cuadrados(self._puntos_pith)
        self._limpiar_items_pith()
        if resultado is None:
            try:
                self._pestana.label_estado.setText(
                    "⚠ No se pudo ajustar el círculo. "
                    "Asegúrate de que los puntos no sean colineales.")
            except Exception:
                pass
            return resultado
        return resultado

    def _limpiar_items_pith(self):
        """Elimina todos los marcadores visuales del modo pith."""
        for item in self._items_pith:
            try:
                self.scene().removeItem(item)
            except Exception:
                pass
        self._items_pith.clear()
        self._puntos_pith.clear()
        if self._item_circulo_pith is not None:
            try:
                self.scene().removeItem(self._item_circulo_pith)
            except Exception:
                pass
            self._item_circulo_pith = None
        for item in self._items_pith_extras:
            try:
                self.scene().removeItem(item)
            except Exception:
                pass
        self._items_pith_extras.clear()
        self._limpiar_preview_pith_mouse()
        # Ocultar HUD del resultado
        if hasattr(self, "_hud_pith"):
            self._hud_pith.setVisible(False)

    def _actualizar_hud_pith(self, html_text: str, color: str = "#FF8C00"):
        """Actualiza el HUD del pith (texto fijo en la esquina del viewport,
        independiente del pan/zoom de la escena).

        El HUD se posiciona en la parte superior central del viewport para
        que siempre esté visible, sin importar dónde caiga la médula
        estimada (que muchas veces está FUERA de la muestra).
        """
        if not hasattr(self, "_hud_pith"):
            return
        # Reconstruir el stylesheet con el color de fit (naranja=bueno,
        # rojo=no confiable). Mantener el resto fijo.
        self._hud_pith.setStyleSheet(
            f"QLabel {{ background-color: rgba(0,0,0,220); color: {color}; "
            f"padding: 6px 12px; border-radius: 4px; font-size: 13px; "
            f"font-weight: bold; font-family: Arial; "
            f"border: 1px solid rgba(255,140,0,150); }}"
        )
        self._hud_pith.setText(html_text)
        self._hud_pith.adjustSize()
        # Posicionar arriba al centro del viewport
        vp = self.viewport()
        x = max(10, (vp.width() - self._hud_pith.width()) // 2)
        self._hud_pith.move(x, 12)
        self._hud_pith.setVisible(True)
        self._hud_pith.raise_()

    def resizeEvent(self, event):
        """Reposicionar el HUD del pith y el panel de selección cuando
        cambia el tamaño del widget."""
        super().resizeEvent(event)
        if hasattr(self, "_hud_pith") and self._hud_pith.isVisible():
            vp = self.viewport()
            x = max(10, (vp.width() - self._hud_pith.width()) // 2)
            self._hud_pith.move(x, 12)
        # Reposicionar también el panel de selección si está visible
        if (hasattr(self, "_panel_seleccion")
                and self._panel_seleccion is not None
                and self._panel_seleccion.isVisible()):
            self._posicionar_panel_seleccion()

    def mapa_anio_coordenada(self) -> dict[int, tuple[float, float]]:
        """Devuelve un dict {año: (x, y)} para cada límite de anillo válido."""
        mapa: dict[int, tuple[float, float]] = {}
        coords = self.coordenadas_anillos
        if not coords or len(coords) < 2:
            return mapa
        try:
            pestana = self._pestana
            anio_medula = pestana.spin_medula.value()
            anio_corteza = pestana.spin_corteza.value()
            med_a_cor = pestana.combo_dir.currentIndex() == 0
        except Exception:
            return mapa

        anillos_validos = 0
        for i, coord in enumerate(coords):
            val = self.es_salto[i] if i > 0 else False
            es_amar = _es_amarillo(val)
            val_sale = self.es_salto[i + 1] if i + 1 < len(self.es_salto) else False
            if (i > 0
                    and not es_amar
                    and not _es_ewlw(val)
                    and not _es_quiebre_ini(val)
                    and not _es_espacio(val_sale)):
                anillos_validos += 1

            if i > 0 and not es_amar and not _es_ewlw(val) and not _es_quiebre_ini(val):
                if med_a_cor:
                    anio = anio_medula + anillos_validos - 1
                else:
                    anio = anio_corteza - anillos_validos + 1
                mapa[anio] = coord
        return mapa

    def _segmento_mas_cercano(self, x: float, y: float) -> tuple[int, float, float] | None:
        """
        Devuelve (idx_segmento, t, d_px) del segmento del camino más cercano al punto
        (x, y), donde idx_segmento es el índice del punto inicial del segmento,
        t es la proyección paramétrica [0,1] sobre ese segmento, y d_px es la
        distancia en px de escena.
        Devuelve None si no hay segmentos o el más cercano supera la tolerancia.
        """
        coords = self.coordenadas_anillos
        if len(coords) < 2:
            return None
        tol = self._tolerancia_segmento()
        mejor_d, mejor_idx, mejor_t = float("inf"), -1, 0.0
        for i in range(len(coords) - 1):
            x1, y1 = coords[i]
            x2, y2 = coords[i + 1]
            l2 = (x2-x1)**2 + (y2-y1)**2
            if l2 < 1e-9:
                continue
            t = max(0.0, min(1.0, ((x-x1)*(x2-x1) + (y-y1)*(y2-y1)) / l2))
            px, py = x1 + t*(x2-x1), y1 + t*(y2-y1)
            d = math.hypot(x-px, y-py)
            if d < mejor_d:
                mejor_d, mejor_idx, mejor_t = d, i, t
        if mejor_idx == -1 or mejor_d > tol:
            return None
        return mejor_idx, mejor_t, mejor_d

    def _paso_espacio(self, x: float, y: float):
        """
        Modo espacio intra-anillo. Dos submodos según dónde cae el clic:

        A) SOBRE LÍNEA EXISTENTE (hay segmento azul cercano):
           Clic 1 → inserta tick AMARILLO (inicio del espacio) en la línea.
           Clic 2 → inserta tick AMARILLO (fin del espacio) en la línea.
           Resultado: prev_tick ─(azul)─ /═══espacio═══/ ─(azul)─ next_tick
           Ambos / son ticks amarillos ("espacio"), el tramo entre ellos también.

        B) EN EL EXTREMO ACTIVO (clic lejos de cualquier segmento):
           Actúa como Medir pero los puntos se insertan como "espacio".
           Clic 1 → añade tick amarillo al final del camino (inicio espacio).
           Clic 2 → añade tick amarillo al final (fin espacio).
           Vuelve automáticamente a Medir.

        Los ticks amarillos NO cuentan como límites de anillo.
        El ancho del anillo = solo los tramos azules (False), no los espacios.
        Esc cancela. Ctrl+Z deshace.
        """
        coords = self.coordenadas_anillos

        # ── Detectar si el clic cae sobre un segmento existente ───────────
        hit = self._segmento_mas_cercano(x, y) if len(coords) >= 2 else None

        pen_v = QPen(QColor("#FFD700"))   # amarillo para marcadores visuales
        pen_v.setCosmetic(True); pen_v.setWidthF(3.0)
        brush_nulo = QBrush(Qt.BrushStyle.NoBrush)

        # ══════════════════════════════════════════════════════════════════
        # SUBMODO B: extremo activo — clic lejos de la línea existente
        #
        # Estructura correcta del quiebre al medir en el extremo activo:
        #
        #  tick_N(False) ──azul── A("quiebre_ini") ══"espacio"══ B("espacio") ──azul── tick_N+1(False)
        #
        # Clic 1 → punto A: es_salto="quiebre_ini" → línea AZUL desde tick anterior,
        #                    suma distancia al anillo pero NO cierra el anillo.
        #
        # Clic 2 → punto B: es_salto="espacio" → línea AMARILLA desde A,
        #                                          tick AMARILLO (no cuenta)
        #          Vuelve a Medir. El siguiente clic normal agrega el límite final
        #          del anillo con línea azul desde B.
        # ══════════════════════════════════════════════════════════════════
        if hit is None:
            if not coords:
                try:
                    self._pestana.label_estado.setText(
                        "↔ Mide al menos un punto antes de insertar un quiebre.")
                except Exception:
                    pass
                return

            # Primer clic: punto A — suma distancia pero NO cierra anillo
            if self._idx_espacio_inicio is None:
                self.guardar_en_historial()
                coords.append((x, y))
                self.es_salto.append("quiebre_ini")   # línea azul, suma, no cierra
                self._idx_espacio_inicio = len(coords) - 1
                self._espacio_p1   = (x, y)
                self._espacio_seg1 = -1
                # Marcador visual (círculo verde para distinguirlo)
                r = 9 * self._escala_vista()
                m = self.scene().addEllipse(x-r, y-r, 2*r, 2*r, pen_v, brush_nulo)
                self._items_espacio.append(m)
                self.redibujar_todo()
                self.recalcular_todas_las_distancias()
                self.progreso_modificado.emit()
                try:
                    self._pestana.label_estado.setText(
                        "↔ Inicio del quiebre marcado. Haz clic en el borde final.")
                except Exception:
                    pass
                return

            # Segundo clic: punto B — línea AMARILLA desde A, tick amarillo
            self.guardar_en_historial()
            coords.append((x, y))
            self.es_salto.append("espacio")   # línea amarilla desde A, tick amarillo
            self._idx_espacio_inicio = None
            self._limpiar_espacio()
            self.redibujar_todo()
            self.recalcular_todas_las_distancias()
            self.progreso_modificado.emit()
            try:
                self._resaltar_anclado(len(self.coordenadas_anillos) - 1)
                self._pestana.label_estado.setText(
                    "↔ Quiebre marcado. Haz clic en el límite final del anillo.")
                self._pestana.set_modo(MODO_MEDIR)
            except Exception:
                pass
            return

        # ══════════════════════════════════════════════════════════════════
        # SUBMODO A: sobre línea existente
        # ══════════════════════════════════════════════════════════════════
        seg_idx, t, _ = hit
        x1, y1 = coords[seg_idx]
        x2, y2 = coords[seg_idx + 1]
        px = x1 + t * (x2 - x1)
        py = y1 + t * (y2 - y1)

        # ── Primer clic A ─────────────────────────────────────────────────
        if self._idx_espacio_inicio is None:
            self._limpiar_espacio()
            r = 9 * self._escala_vista()
            m = self.scene().addEllipse(px-r, py-r, 2*r, 2*r, pen_v, brush_nulo)
            self._items_espacio.append(m)
            self._espacio_p1   = (px, py)
            self._espacio_seg1 = seg_idx
            self._idx_espacio_inicio = seg_idx
            try:
                self._pestana.label_estado.setText(
                    "↔ Inicio del espacio marcado. Haz clic en el borde final.")
            except Exception:
                pass
            return

        # ── Segundo clic A ────────────────────────────────────────────────
        p1, s1 = self._espacio_p1, self._espacio_seg1
        p2, s2 = (px, py), seg_idx
        self._idx_espacio_inicio = None
        self._limpiar_espacio()

        # Ordenar s1 ≤ s2
        if s1 > s2:
            p1, p2 = p2, p1
            s1, s2 = s2, s1
        elif s1 == s2:
            xr1, yr1 = coords[s1]
            d1 = math.hypot(p1[0]-xr1, p1[1]-yr1)
            d2 = math.hypot(p2[0]-xr1, p2[1]-yr1)
            if d1 > d2:
                p1, p2 = p2, p1

        if math.hypot(p1[0]-p2[0], p1[1]-p2[1]) < 2:
            try:
                self._pestana.label_estado.setText("↔ Puntos demasiado cercanos. Cancelado.")
            except Exception:
                pass
            return

        # Validar que no hay ticks normales (anillos completos) entre s1 y s2
        for j in range(s1 + 1, s2 + 1):
            if j < len(self.es_salto) and self.es_salto[j] is False:
                try:
                    self._pestana.label_estado.setText(
                        "↔ El espacio no puede cruzar un límite de anillo.")
                except Exception:
                    pass
                return

        self.guardar_en_historial()

        # Estado final deseado:
        #   prev_tick ─("quiebre_ini")─ p1 ─("espacio")─ p2 ─(False)─ next_tick
        #                                ↑ tick amarillo   ↑ tick amarillo
        #
        # p1 tiene es_salto="quiebre_ini" (azul, SUMA distancia, no cierra)
        # el tramo p1→p2 tiene es_salto="espacio" (amarillo, no suma, no cierra)
        # p2→siguiente tiene es_salto=False (reanuda normal, suma y cierra)
        #
        # Insertar de atrás hacia adelante:
        #   1) p2 en s2+1 con es_salto=False (p2→siguiente normal)
        #   2) p1 en s1+1 con es_salto="quiebre_ini" (prev→p1 azul, suma)
        #   3) El segmento p1→p2 (índice s1+2) = "espacio"
        #   4) Segmentos intermedios si s1 < s2 → también "espacio"

        # 1) Insertar p2
        self.coordenadas_anillos.insert(s2 + 1, p2)
        self.es_salto.insert(s2 + 1, False)   # p2→siguiente: reanuda normal

        # 2) Insertar p1 con es_salto="quiebre_ini" (suma distancia antes del quiebre)
        self.coordenadas_anillos.insert(s1 + 1, p1)
        self.es_salto.insert(s1 + 1, "quiebre_ini")  # prev→p1: azul, suma

        # 3) Segmento p1→p2 = "espacio"
        idx_tramo = s1 + 2
        if idx_tramo < len(self.es_salto):
            self.es_salto[idx_tramo] = "espacio"

        # 4) Segmentos intermedios (si s1 < s2)
        for j in range(idx_tramo + 1, s2 + 2):
            if j < len(self.es_salto) and self.es_salto[j] is False:
                self.es_salto[j] = "espacio"

        self.redibujar_todo()
        self.recalcular_todas_las_distancias()
        self.progreso_modificado.emit()
        try:
            self._pestana.label_estado.setText(
                "↔ Quiebre insertado. Tramo amarillo no cuenta. Ctrl+Z para deshacer.")
            self._pestana.set_modo(MODO_MEDIR)
        except Exception:
            pass


    def _paso_ewlw(self, x: float, y: float):
        """
        Modo EW/LW: clic sobre la línea azul del camino.
        Inserta un punto "ew_lw" (tick naranja) que divide el anillo
        en earlywood (izquierda) y latewood (derecha).
        Un anillo puede tener como máximo un límite EW/LW.
        La distancia total del anillo no cambia (EW+LW = total).
        Esc cancela. Ctrl+Z deshace.
        """
        coords = self.coordenadas_anillos
        hit = self._segmento_mas_cercano(x, y) if len(coords) >= 2 else None
        if hit is None:
            try:
                self._pestana.label_estado.setText(
                    "🍂 Haz clic sobre la línea azul del anillo a dividir.")
            except Exception:
                pass
            return

        seg_idx, t, _ = hit
        # Verificar que el segmento no sea ya un tramo de espacio/quiebre
        val_seg = self.es_salto[seg_idx + 1] if seg_idx + 1 < len(self.es_salto) else False
        if _es_amarillo(val_seg):
            try:
                self._pestana.label_estado.setText(
                    "🍂 No se puede insertar EW/LW en un tramo de quiebre o salto.")
            except Exception:
                pass
            return

        # Verificar que no haya ya un ew_lw en este anillo
        # (buscar el anillo que contiene seg_idx)
        ini_anillo = seg_idx
        while ini_anillo > 0 and self.es_salto[ini_anillo] is not False:
            ini_anillo -= 1
        fin_anillo = seg_idx + 1
        while fin_anillo < len(coords) and self.es_salto[fin_anillo] is not False:
            fin_anillo += 1
        for j in range(ini_anillo + 1, fin_anillo):
            if j < len(self.es_salto) and _es_ewlw(self.es_salto[j]):
                try:
                    self._pestana.label_estado.setText(
                        "🍂 Este anillo ya tiene un límite EW/LW. "
                        "Usa 🖐 Mover para reposicionarlo.")
                except Exception:
                    pass
                return

        # Calcular punto exacto sobre el segmento
        x1, y1 = coords[seg_idx]
        x2, y2 = coords[seg_idx + 1]
        px = x1 + t * (x2 - x1)
        py = y1 + t * (y2 - y1)

        self.guardar_en_historial()
        self.coordenadas_anillos.insert(seg_idx + 1, (px, py))
        self.es_salto.insert(seg_idx + 1, "ew_lw")

        self.redibujar_todo()
        self.recalcular_todas_las_distancias()
        self.progreso_modificado.emit()
        try:
            self._pestana.label_estado.setText(
                "🍂 Límite EW/LW insertado. Usa 🖐 Mover para ajustarlo. "
                "Ctrl+Z para deshacer.")
        except Exception:
            pass

    def _limpiar_espacio(self):
        for item in self._items_espacio:
            try: self.scene().removeItem(item)
            except Exception: pass
        self._items_espacio.clear()
        # No limpiar _idx_espacio_inicio aquí — se maneja en _paso_espacio

    def keyPressEvent(self, event):
        """Enter confirma el modo pith; Esc lo cancela."""
        if self.modo_actual == MODO_PITH:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                try:
                    self._pestana._confirmar_estimacion_pith()
                except Exception:
                    pass
                return
            if event.key() == Qt.Key.Key_Escape:
                self._limpiar_items_pith()
                try:
                    self._pestana.set_modo(MODO_MEDIR)
                    self._pestana.label_estado.setText("Estimación cancelada.")
                except Exception:
                    pass
                return
        if self.modo_actual == MODO_ESPACIO:
            if event.key() == Qt.Key.Key_Escape:
                self._idx_espacio_inicio = None
                self._limpiar_espacio()
                try:
                    self._pestana.label_estado.setText("↔ Selección de espacio cancelada.")
                except Exception:
                    pass
                return
        # Modo selección múltiple: atajos de teclado
        if self.modo_actual == MODO_SELECCIONAR:
            # Delete o Backspace → borrar los seleccionados
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._ejecutar_borrar_seleccionados()
                return
            # Esc → limpiar selección y volver a Medir
            if event.key() == Qt.Key.Key_Escape:
                self._limpiar_seleccion_completa()
                try:
                    self._pestana.set_modo(MODO_MEDIR)
                except Exception:
                    pass
                return
        super().keyPressEvent(event)

    def _iniciar_mover(self, x, y, event):
        idx = self._indice_punto_mas_cercano(x, y)
        if idx is not None:
            self.guardar_en_historial()
            self._punto_arrastrado_idx = idx
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        else:
            self._is_panning = True
            self._last_pan_pos = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _insertar_en_segmento(self, x, y):
        if len(self.coordenadas_anillos) < 2:
            return
        tol = self._tolerancia_segmento()
        mejor_d, mejor_idx = float("inf"), -1
        for i in range(len(self.coordenadas_anillos) - 1):
            x1, y1 = self.coordenadas_anillos[i]
            x2, y2 = self.coordenadas_anillos[i + 1]
            d = self._dist_punto_segmento(x, y, x1, y1, x2, y2)
            if d < mejor_d:
                mejor_d, mejor_idx = d, i
        if mejor_d < tol and mejor_idx != -1:
            self.guardar_en_historial()
            self.coordenadas_anillos.insert(mejor_idx + 1, (x, y))
            self.es_salto.insert(mejor_idx + 1, False)
            self.redibujar_todo()
            self.recalcular_todas_las_distancias()
            self.progreso_modificado.emit()

    def _borrar_punto_cercano(self, x, y):
        idx = self._indice_punto_mas_cercano(x, y)
        if idx is not None:
            self.guardar_en_historial()
            self.coordenadas_anillos.pop(idx)
            self.es_salto.pop(idx)
            self.redibujar_todo()
            self.recalcular_todas_las_distancias()
            self.progreso_modificado.emit()

    def _paso_calibracion(self, x, y):
        """Procesa los clicks de calibración.

        Flujo:
          - Click 1: marca el punto inicial
          - Click 2: marca el punto final + dibuja línea + pregunta los mm
                     → aplica calibración y limpia las marcas
        """
        self.puntos_calibracion.append((x, y))

        # Dibujar el círculo del punto y agregar a la lista limpiable
        marker = self.scene().addEllipse(
            x - 5, y - 5, 10, 10,
            self._pen_calibracion, QBrush(QColor(COLOR_CALIBRACION)))
        marker.setZValue(500)
        self._items_calibracion.append(marker)

        # Solo hay un punto: pedir el segundo
        if len(self.puntos_calibracion) == 1:
            try:
                self._pestana.label_estado.setText(
                    "📏 Calibración: Haz clic en el segundo punto (final de la distancia conocida).")
            except Exception:
                pass
            return

        # Ya tenemos los 2 puntos
        p1, p2 = self.puntos_calibracion
        dist_px = math.hypot(p2[0] - p1[0], p2[1] - p1[1])

        # Dibujar la línea entre los 2 puntos
        pen_linea = QPen(QColor(COLOR_CALIBRACION))
        pen_linea.setCosmetic(True)
        pen_linea.setWidthF(2.5)
        pen_linea.setStyle(Qt.PenStyle.SolidLine)
        linea = self.scene().addLine(p1[0], p1[1], p2[0], p2[1], pen_linea)
        linea.setZValue(499)
        self._items_calibracion.append(linea)

        # Etiqueta con la distancia, posicionada PEGADA AL BORDE INTERNO
        # de la imagen (el borde más cercano a la línea de calibración).
        # Quedaba afuera de la imagen porque calculábamos offsets estimados
        # (22 px de alto, etc.) — ahora usamos el boundingRect REAL del
        # texto después de setear el HTML para posicionarlo con precisión.
        from PyQt6.QtWidgets import QGraphicsItem

        # Crear el texto primero (necesario para conocer su tamaño)
        texto = self.scene().addText("")
        texto.setHtml(
            f"<div style='background:rgba(0,0,0,200); color:{COLOR_CALIBRACION}; "
            f"padding:2px 5px; border-radius:3px; font-size:12px; "
            f"font-weight:bold; font-family:Arial; white-space:nowrap;'>"
            f"📏 {dist_px:.1f} px"
            f"</div>"
        )
        texto.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        rect = texto.boundingRect()  # en píxeles de pantalla (por el flag)
        # Tamaño real del badge, no estimado
        rect_w_px = rect.width()
        rect_h_px = rect.height()

        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2

        # Bordes de la imagen (sceneRect coincide con la imagen — ver
        # _cargar_imagen donde se hace setSceneRect(0, 0, w, h)).
        scene_rect = self.scene().sceneRect()
        left, top = scene_rect.left(), scene_rect.top()
        right, bottom = scene_rect.right(), scene_rect.bottom()

        # Distancias del punto medio a cada borde
        d_top = my - top
        d_bottom = bottom - my
        d_left = mx - left
        d_right = right - mx
        min_d = min(d_top, d_bottom, d_left, d_right)

        # Margen INTERNO (en píxeles de pantalla) desde el borde de la
        # imagen. Garantiza que el badge entero queda DENTRO del rectángulo
        # de la imagen, independiente del zoom.
        MARGEN_INTERNO_PX = 6.0
        escala = self._escala_vista()

        # Convertir píxeles de pantalla a unidades de escena para colocar
        # el setPos en el lugar correcto. El badge ocupa rect_w_px x
        # rect_h_px en PANTALLA. La posición es la esquina top-left del
        # badge en coords de escena.
        rect_w_escena = rect_w_px * escala
        rect_h_escena = rect_h_px * escala
        margen_escena = MARGEN_INTERNO_PX * escala

        if min_d == d_top:
            # Borde superior: badge contra el borde superior, centrado en mx
            label_x = mx - rect_w_escena / 2.0
            label_y = top + margen_escena
        elif min_d == d_bottom:
            # Borde inferior: badge JUSTO ENCIMA del borde, alineado a su
            # base con (bottom - margen). Es decir, top-left.y = bottom -
            # rect_h - margen.
            label_x = mx - rect_w_escena / 2.0
            label_y = bottom - rect_h_escena - margen_escena
        elif min_d == d_left:
            # Borde izquierdo: badge contra el borde izquierdo, centrado
            # verticalmente en my
            label_x = left + margen_escena
            label_y = my - rect_h_escena / 2.0
        else:
            # Borde derecho: badge contra el borde derecho, top-left.x =
            # right - rect_w - margen
            label_x = right - rect_w_escena - margen_escena
            label_y = my - rect_h_escena / 2.0

        # Clamping defensivo: si por alguna razón el cálculo se desbordó,
        # forzar la posición a quedar dentro del sceneRect.
        label_x = max(left + margen_escena,
                      min(label_x, right - rect_w_escena - margen_escena))
        label_y = max(top + margen_escena,
                      min(label_y, bottom - rect_h_escena - margen_escena))

        texto.setPos(label_x, label_y)
        texto.setZValue(501)
        self._items_calibracion.append(texto)

        # Forzar repintado para que el usuario VEA la línea y la distancia
        # antes de que aparezca el diálogo
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

        # Si ya teníamos un objetivo_mm pre-cargado (flujo viejo) lo usamos.
        # En el flujo nuevo es None, así que preguntamos al usuario.
        objetivo = getattr(self, "_objetivo_calibracion_mm", None)
        if objetivo and objetivo > 0:
            dist_mm = objetivo
            ok = True
        else:
            dlg = DialogoCalibracionRegla(self.window())
            dlg.setWindowTitle(f"Calibración (línea = {dist_px:.1f} px)")
            if dlg.exec():
                dist_mm = dlg.obtener_mm()
                ok = (dist_mm > 0)
            else:
                dist_mm = 0
                ok = False

        if ok and dist_mm > 0:
            self.pixeles_por_mm = dist_px / dist_mm
            # Marcar el origen del DPI como "calibrado por regla"
            # para que el diálogo de info lo refleje correctamente.
            try:
                self._pestana._dpi_modo = "regla"
            except Exception:
                pass
            QMessageBox.information(
                self.window(), "Calibración aplicada",
                f"  {dist_px:.2f} px = {dist_mm:.3f} mm\n"
                f"  1 mm = {self.pixeles_por_mm:.2f} px\n"
                f"  ≈ {self.pixeles_por_mm * MM_POR_PULGADA:.0f} DPI"
            )
            # Recalcular distancias y redibujar — sin esto las mediciones
            # existentes quedan en la escala vieja.
            self.recalcular_todas_las_distancias()
            self.redibujar_todo()
            try:
                self._pestana._actualizar_grafico_rt()
            except AttributeError:
                pass
            self._pestana._guardar_estado()
            # Actualizar la etiqueta de la línea con los mm calibrados así
            # el usuario tiene un recordatorio visual de que la imagen
            # ya está calibrada y a qué escala.
            self._actualizar_etiqueta_calibracion(dist_px, dist_mm)
        else:
            # Si canceló el diálogo, limpiar las marcas (no hubo calibración)
            self._limpiar_items_calibracion()

        self._objetivo_calibracion_mm = None
        self._pestana.set_modo(MODO_MEDIR)
        self.progreso_modificado.emit()

    def _actualizar_etiqueta_calibracion(self, dist_px: float, dist_mm: float):
        """Reemplaza el texto de "📏 X px" por "✓ Calibrada: X mm = Y px"
        para que el usuario sepa que la calibración ya está aplicada.
        Las marcas (puntos y línea) se mantienen como recordatorio visual.

        El texto usa ItemIgnoresTransformations para tamaño constante en
        pantalla independiente del zoom.
        """
        from PyQt6.QtWidgets import QGraphicsTextItem, QGraphicsItem
        for i, item in enumerate(self._items_calibracion):
            if isinstance(item, QGraphicsTextItem):
                item.setHtml(
                    f"<div style='background:rgba(0,0,0,200); "
                    f"color:{COLOR_CALIBRACION}; "
                    f"padding:2px 5px; border-radius:3px; font-size:12px; "
                    f"font-weight:bold; font-family:Arial; white-space:nowrap;'>"
                    f"✓ Calibrada: {dist_px:.1f} px = {dist_mm:.2f} mm"
                    f"</div>"
                )
                # Asegurar el flag (por si vino de una versión vieja sin él)
                item.setFlag(
                    QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
                    True)
                break

    # ------------------------------------------------------------------
    # Modo MEDIR / SALTO con ancla seleccionable
    # ------------------------------------------------------------------

    def _limpiar_anclado(self):
        """Elimina el resaltado visual del punto ancla."""
        if self._item_anclado is not None:
            try:
                self.scene().removeItem(self._item_anclado)
            except Exception:
                pass
            self._item_anclado = None
        self._idx_anclado = None

    def _resaltar_anclado(self, idx: int):
        """Dibuja un círculo amarillo sobre el punto anclado."""
        self._limpiar_anclado()
        if idx < 0 or idx >= len(self.coordenadas_anillos):
            return   # índice inválido tras deshacer/borrar
        self._idx_anclado = idx
        x, y = self.coordenadas_anillos[idx]
        pen  = QPen(QColor("#FFD700")); pen.setCosmetic(True); pen.setWidthF(3.0)
        brush = QBrush(Qt.BrushStyle.NoBrush)
        r = 10 * self._escala_vista()
        self._item_anclado = self.scene().addEllipse(x - r, y - r, 2*r, 2*r, pen, brush)

    def _paso_medir(self, x: float, y: float):
        """
        Lógica unificada para los modos Medir y Salto.

        Estado de la máquina:
          • Sin ancla + puntos existentes → busca punto cercano para anclar.
            Si no hay ninguno cerca → ancla al extremo más cercano y añade.
          • Sin ancla + serie vacía       → primer punto de la serie.
          • Con ancla                     → inserta punto adyacente al ancla,
            detectando dirección automáticamente.
        """
        coords = self.coordenadas_anillos
        es_salto_modo = self.modo_actual == MODO_SALTO

        # ── Serie vacía: primer punto ────────────────────────────────────
        if not coords:
            self.guardar_en_historial()
            coords.append((x, y))
            self.es_salto.append(False)
            self._resaltar_anclado(0)
            self.redibujar_todo()
            self.recalcular_todas_las_distancias()
            self.progreso_modificado.emit()
            return

        # ── Sin ancla: intentar seleccionar punto existente ──────────────
        if self._idx_anclado is None:
            idx = self._indice_punto_mas_cercano(x, y)
            if idx is not None:
                # El clic fue sobre un punto → anclar sin añadir nada
                self._resaltar_anclado(idx)
                return
            else:
                # Clic lejos de cualquier punto → anclar al extremo más cercano
                d0 = math.hypot(x - coords[0][0],  y - coords[0][1])
                d1 = math.hypot(x - coords[-1][0], y - coords[-1][1])
                self._resaltar_anclado(0 if d0 < d1 else len(coords) - 1)
                # Caemos al bloque "con ancla" para insertar de inmediato
                # (este clic también es el de inserción)

        # ── Con ancla: insertar punto adyacente ──────────────────────────
        idx_a = self._idx_anclado
        n = len(coords)
        # Guard: ancla puede haber quedado fuera de rango tras deshacer
        if idx_a is None or idx_a >= n:
            self._limpiar_anclado()
            idx_a = n - 1
            self._resaltar_anclado(idx_a)

        # Determinar dirección: el nuevo punto va ANTES o DESPUÉS del ancla.
        # Se compara la distancia euclidiana del cursor a los vecinos para
        # inferir en qué lado del ancla está el usuario apuntando.
        # Regla simple: si el ancla es el último punto → append.
        #               si el ancla es el primero       → prepend.
        #               si está en el medio             → detectar por posición.
        if idx_a == n - 1:
            # Ancla al final → añadir después (hacia corteza o médula según dirección global)
            insert_pos = n
        elif idx_a == 0:
            # Ancla al principio → insertar antes
            insert_pos = 0
        else:
            # Punto intermedio: insertar en el lado donde cae el nuevo clic.
            # Comparar proyección sobre el vector ancla→siguiente vs ancla→anterior
            ax, ay = coords[idx_a]
            bx, by = coords[idx_a + 1]   # siguiente
            px_prev, py_prev = coords[idx_a - 1]  # anterior
            # Distancia del nuevo clic a ambos vecinos
            d_sig  = math.hypot(x - bx,      y - by)
            d_prev = math.hypot(x - px_prev, y - py_prev)
            insert_pos = idx_a + 1 if d_sig < d_prev else idx_a

        self.guardar_en_historial()
        self.coordenadas_anillos.insert(insert_pos, (x, y))
        self.es_salto.insert(insert_pos, es_salto_modo)

        # El nuevo ancla pasa a ser el punto recién insertado
        self._resaltar_anclado(insert_pos)

        self.redibujar_todo()
        self.recalcular_todas_las_distancias()

        if es_salto_modo:
            self._limpiar_anclado()
            self._pestana.set_modo(MODO_MEDIR)

        self.progreso_modificado.emit()

    def _anadir_punto(self, x, y):
        """Alias de compatibilidad — delega a _paso_medir."""
        self._paso_medir(x, y)

    def mouseMoveEvent(self, event):
        pos = self.mapToScene(event.pos())
        x, y = pos.x(), pos.y()

        # ── Selección por arrastre (MODO_SELECCIONAR) ──
        # Si comenzamos un drag desde un área vacía, dibujamos el
        # rectángulo de selección que sigue al mouse.
        # Si comenzamos sobre un punto pero el mouse se aleja del punto
        # de partida (umbral 4 px de pantalla), convertimos el "posible
        # toggle" en un drag-rectangle (cancela el candidato).
        if self.modo_actual == MODO_SELECCIONAR:
            mouse_se_movio = (
                math.hypot(x - self._sel_click_pos[0],
                           y - self._sel_click_pos[1])
                > 4.0 * self._escala_vista()
            )
            if (self._sel_click_idx_candidato is not None
                    and not self._sel_drag_iniciado
                    and mouse_se_movio):
                # El usuario empezó sobre un punto pero ahora está
                # arrastrando — convertir a drag-rectangle desde la pos
                # original.
                self._sel_click_idx_candidato = None
                self._sel_drag_iniciado = True
                # _sel_drag_start ya está seteado en mousePress

            if self._sel_drag_iniciado:
                x0, y0 = self._sel_drag_start
                xa, ya = min(x0, x), min(y0, y)
                xb, yb = max(x0, x), max(y0, y)
                # Crear/actualizar el rectángulo de preview
                if self._sel_drag_rect_item is None:
                    pen = QPen(QColor("#FFEB3B"))
                    pen.setCosmetic(True)
                    pen.setWidthF(1.5)
                    pen.setStyle(Qt.PenStyle.DashLine)
                    brush = QBrush(QColor(255, 235, 59, 40))  # amarillo translúcido
                    self._sel_drag_rect_item = self.scene().addRect(
                        xa, ya, xb - xa, yb - ya, pen, brush)
                    self._sel_drag_rect_item.setZValue(951)
                else:
                    self._sel_drag_rect_item.setRect(xa, ya, xb - xa, yb - ya)
                return

        # ── Preview de arco en modo pith con 2 puntos ──
        if self.modo_actual == MODO_PITH and len(self._puntos_pith) == 2:
            self._preview_pith_mouse(x, y)

        # Cursor dinámico según modo
        if self.modo_actual == MODO_ESPACIO:
            sobre_linea = self._segmento_mas_cercano(x, y) is not None
            self.setCursor(
                Qt.CursorShape.CrossCursor if sobre_linea
                else Qt.CursorShape.ArrowCursor
            )

        elif self.modo_actual == MODO_BORRAR:
            hovering = any(
                math.hypot(px - x, py - y) < self._tolerancia_punto()
                for px, py in self.coordenadas_anillos
            )
            self.setCursor(Qt.CursorShape.CrossCursor if hovering else Qt.CursorShape.ArrowCursor)

        elif self.modo_actual == MODO_INSERTAR:
            tol = self._tolerancia_segmento()
            hovering = any(
                self._dist_punto_segmento(
                    x, y,
                    self.coordenadas_anillos[i][0], self.coordenadas_anillos[i][1],
                    self.coordenadas_anillos[i + 1][0], self.coordenadas_anillos[i + 1][1],
                ) < tol
                for i in range(len(self.coordenadas_anillos) - 1)
            )
            self.setCursor(Qt.CursorShape.CrossCursor if hovering else Qt.CursorShape.ArrowCursor)

        # Tooltip en puntos
        item = self.itemAt(event.pos())
        if isinstance(item, QGraphicsEllipseItem) and hasattr(item, "anio_texto"):
            if item != self._item_hover_prev:
                QToolTip.showText(event.globalPosition().toPoint(), item.anio_texto, self.viewport())
                self._item_hover_prev = item
        elif self._item_hover_prev is not None:
            QToolTip.hideText()
            self._item_hover_prev = None

        super().mouseMoveEvent(event)

        # Pan con botón central (independiente del modo)
        if self._is_panning and self._last_pan_pos and not (
            self.modo_actual == MODO_MOVER and self._punto_arrastrado_idx is not None
        ):
            if event.buttons() & Qt.MouseButton.MiddleButton or (
                self.modo_actual == MODO_MOVER and event.buttons() & Qt.MouseButton.LeftButton
            ):
                delta = event.pos() - self._last_pan_pos
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
                self._last_pan_pos = event.pos()
                return

        # Pan de la imagen (modo mover, sin punto seleccionado)
        if self.modo_actual == MODO_MOVER and self._is_panning and self._last_pan_pos:
            delta = event.pos() - self._last_pan_pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self._last_pan_pos = event.pos()
            return

        # Arrastre de punto
        if self.modo_actual == MODO_MOVER and self._punto_arrastrado_idx is not None:
            self.coordenadas_anillos[self._punto_arrastrado_idx] = (x, y)
            self.redibujar_todo()
            self.recalcular_todas_las_distancias()
            return

        # Línea guía hacia el cursor
        if self.modo_actual == MODO_ESPACIO and self.coordenadas_anillos:
            # Línea guía desde el punto A ya insertado en el camino
            if self._idx_espacio_inicio is not None:
                x_ant, y_ant = self._espacio_p1
                pen_esp = QPen(QColor("#00FF88"))
                pen_esp.setCosmetic(True)
                pen_esp.setWidthF(1.5)
                pen_esp.setStyle(Qt.PenStyle.DashLine)
                if self._linea_guia is None:
                    self._linea_guia = self.scene().addLine(x_ant, y_ant, x, y, pen_esp)
                else:
                    self._linea_guia.setLine(x_ant, y_ant, x, y)
                    self._linea_guia.setPen(pen_esp)
            elif self._linea_guia:
                self.scene().removeItem(self._linea_guia)
                self._linea_guia = None

        elif self.modo_actual in (MODO_MEDIR, MODO_SALTO, MODO_ANOMALIA,
                                   MODO_INSERTAR, MODO_BORRAR) and self.coordenadas_anillos:
            # Botón derecho presionado → previsualizar salto
            es_salto_preview = bool(event.buttons() & Qt.MouseButton.RightButton)
            modo_guia = MODO_SALTO if es_salto_preview else self.modo_actual
            if modo_guia not in (MODO_MEDIR, MODO_SALTO):
                # En otros modos solo mostrar guía si hay ancla activa
                if self._idx_anclado is None:
                    return
            # La guía sale desde el punto anclado (o el último si no hay ancla)
            if self._idx_anclado is not None and self._idx_anclado < len(self.coordenadas_anillos):
                x_ant, y_ant = self.coordenadas_anillos[self._idx_anclado]
            else:
                self._limpiar_anclado()   # ancla inválida tras deshacer → resetear
                x_ant, y_ant = self.coordenadas_anillos[-1]
            pen = self._pen_salto if modo_guia == MODO_SALTO else self._pen_guia
            if self._linea_guia is None:
                self._linea_guia = self.scene().addLine(x_ant, y_ant, x, y, pen)
            else:
                self._linea_guia.setLine(x_ant, y_ant, x, y)
                self._linea_guia.setPen(pen)

    def mouseReleaseEvent(self, event):
        # ── Selección por arrastre (MODO_SELECCIONAR) ──
        # Si terminamos un drag-rectangle: encontrar todos los anillos
        # dentro del área y agregarlos a la selección.
        # Si fue solo un click sobre un punto (sin arrastre): toggle.
        if (self.modo_actual == MODO_SELECCIONAR
                and event.button() == Qt.MouseButton.LeftButton):
            if self._sel_drag_iniciado and self._sel_drag_rect_item is not None:
                # Calcular los bounds del rectángulo final
                rect = self._sel_drag_rect_item.rect()
                xa, ya = rect.left(), rect.top()
                xb, yb = rect.right(), rect.bottom()
                # Buscar anillos dentro
                indices_dentro = []
                for i, (px, py) in enumerate(self.coordenadas_anillos):
                    if xa <= px <= xb and ya <= py <= yb:
                        indices_dentro.append(i)
                # Agregar al set de seleccionados (no toggle, para que
                # arrastres sucesivos sumen sin deseleccionar lo previo).
                for i in indices_dentro:
                    self._puntos_seleccionados.add(i)
                # Limpiar el preview del rectángulo
                try:
                    self.scene().removeItem(self._sel_drag_rect_item)
                except Exception:
                    pass
                self._sel_drag_rect_item = None
                self._sel_drag_iniciado = False
                # Redibujar marcadores + actualizar panel
                self._dibujar_marcadores_seleccion()
                self._actualizar_panel_seleccion()
                return
            elif self._sel_click_idx_candidato is not None:
                # No hubo arrastre: ejecutar el toggle del punto candidato
                self._toggle_seleccion_punto(self._sel_click_idx_candidato)
                self._sel_click_idx_candidato = None
                return
            # Si no hubo ni drag ni candidato, no hacemos nada
            self._sel_drag_iniciado = False
            self._sel_click_idx_candidato = None

        # Botón central: fin del paneo
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = False
            self._last_pan_pos = None
            # Restaurar cursor según el modo activo
            cursores = {
                MODO_MEDIR:    Qt.CursorShape.CrossCursor,
                MODO_SALTO:    Qt.CursorShape.CrossCursor,
                MODO_MOVER:    Qt.CursorShape.OpenHandCursor,
                MODO_INSERTAR: Qt.CursorShape.ArrowCursor,
                MODO_BORRAR:   Qt.CursorShape.ArrowCursor,
                MODO_ANOMALIA: Qt.CursorShape.PointingHandCursor,
            }
            self.setCursor(cursores.get(self.modo_actual, Qt.CursorShape.ArrowCursor))
            return

        if self.modo_actual == MODO_MOVER:
            if self._is_panning:
                self._is_panning = False
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            elif self._punto_arrastrado_idx is not None:
                self._punto_arrastrado_idx = None
                self.setCursor(Qt.CursorShape.OpenHandCursor)
                self.progreso_modificado.emit()
            return
        super().mouseReleaseEvent(event)

    # -------------------------------------------------------------------------
    # Auto-detección de anillos
    # -------------------------------------------------------------------------

    def iniciar_autodetect(self, imagen_bgr: np.ndarray):
        """
        Activa el modo auto-detección y cachea la imagen BGR.
        Comportamiento:
          - Si hay puntos existentes: 1er clic ancla un punto, 2do clic define el extremo.
          - Si la serie está vacía: 1er clic = un extremo, 2do clic = el otro.

        Fix de foco: después de cambiar el modo, forzar foco en la vista
        y procesar eventos pendientes. Sin esto, el primer click sobre la
        imagen se "pierde" — el foco se queda en el botón/popup que invocó
        este método, y el mousePressEvent del primer click no llega
        limpio a la vista. El usuario tenía que hacer dos clicks para
        cada punto, que era exactamente lo que reportó como bug.
        """
        self._imagen_bgr = imagen_bgr
        self._puntos_autodetect = []
        self._idx_ancla_autodetect = None   # índice del punto anclado (si existe)
        for item in self._items_autodetect:
            self.scene().removeItem(item)
        self._items_autodetect.clear()
        self.cambiar_modo(MODO_AUTODETECT)
        # Forzar foco en la vista y procesar eventos pendientes — sin esto
        # el primer click se pierde porque el botón "🔍 Auto-detectar" del
        # top bar retiene el foco hasta el siguiente evento.
        self.setFocus()
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

    def _paso_autodetect(self, x: float, y: float):
        """
        Máquina de estados del modo auto-detección.

        Con puntos existentes:
          Clic 1: si cae sobre un punto existente → ancla ese punto (amarillo).
                  Si no hay ninguno cerca → trata como extremo libre.
          Clic 2: define el otro extremo → lanza detección entre ancla y este punto.

        Sin puntos:
          Clic 1: primer extremo (verde).
          Clic 2: segundo extremo → lanza detección entre los dos.
        """
        pen_verde = QPen(QColor("#00FF88")); pen_verde.setCosmetic(True); pen_verde.setWidthF(3.0)
        brush_verde = QBrush(QColor("#00FF88"))
        pen_amarillo = QPen(QColor("#FFD700")); pen_amarillo.setCosmetic(True); pen_amarillo.setWidthF(3.0)
        brush_nulo = QBrush(Qt.BrushStyle.NoBrush)

        coords = self.coordenadas_anillos

        # ── Primer clic ───────────────────────────────────────────────────────
        if len(self._puntos_autodetect) == 0:
            idx_cercano = self._indice_punto_mas_cercano(x, y)

            if idx_cercano is not None and coords:
                # Anclar punto existente
                self._idx_ancla_autodetect = idx_cercano
                ax, ay = coords[idx_cercano]
                marker = self.scene().addEllipse(ax - 10, ay - 10, 20, 20,
                                                  pen_amarillo, brush_nulo)
                self._items_autodetect.append(marker)
                self._puntos_autodetect.append(coords[idx_cercano])
            else:
                # Extremo libre
                self._idx_ancla_autodetect = None
                marker = self.scene().addEllipse(x - 8, y - 8, 16, 16,
                                                  pen_verde, brush_verde)
                self._items_autodetect.append(marker)
                self._puntos_autodetect.append((x, y))
            return

        # ── Segundo clic: definir extremo y lanzar detección ─────────────────
        marker2 = self.scene().addEllipse(x - 8, y - 8, 16, 16, pen_verde, brush_verde)
        self._items_autodetect.append(marker2)
        self._puntos_autodetect.append((x, y))

        p1 = self._puntos_autodetect[0]
        p2 = self._puntos_autodetect[1]

        # Línea visual del segmento a detectar
        pen_linea = QPen(QColor("#00FF88")); pen_linea.setCosmetic(True); pen_linea.setWidthF(2.0)
        pen_linea.setStyle(Qt.PenStyle.DashLine)
        linea = self.scene().addLine(p1[0], p1[1], p2[0], p2[1], pen_linea)
        self._items_autodetect.append(linea)

        # Delegar a la pestaña con información del ancla
        self._pestana._ejecutar_autodeteccion(
            p1, p2, self._imagen_bgr,
            idx_ancla=self._idx_ancla_autodetect,
        )

        # Limpiar y volver a Medir
        for item in self._items_autodetect:
            self.scene().removeItem(item)
        self._items_autodetect.clear()
        self._puntos_autodetect.clear()
        self._idx_ancla_autodetect = None
        self.cambiar_modo(MODO_MEDIR)

    def colocar_puntos_autodetectados(
        self,
        coordenadas: list[tuple[float, float]],
        idx_ancla: int | None = None,
        reemplazar_tramo: bool = False,
    ):
        """
        Inserta los puntos detectados respetando el ancla.

        idx_ancla=None      → append al final (comportamiento original).
        idx_ancla=i         → inserta a continuación del punto i, en la
                              dirección correcta detectada automáticamente.
        reemplazar_tramo    → si True, borra los puntos existentes entre
                              el ancla y el extremo antes de insertar.
        """
        if not coordenadas:
            return
        self.guardar_en_historial()

        if idx_ancla is None:
            # Sin ancla: añadir al final
            for coord in coordenadas:
                self.coordenadas_anillos.append(coord)
                self.es_salto.append(False)
        else:
            n = len(self.coordenadas_anillos)
            # Detectar dirección: ¿los nuevos puntos van después o antes del ancla?
            # Comparar distancia del primer punto detectado al vecino derecho vs izquierdo
            ax, ay = self.coordenadas_anillos[idx_ancla]
            fx, fy = coordenadas[0]   # primer punto detectado (el más cercano al ancla)
            d_der = math.hypot(fx - self.coordenadas_anillos[min(idx_ancla+1, n-1)][0],
                               fy - self.coordenadas_anillos[min(idx_ancla+1, n-1)][1]) if idx_ancla < n-1 else float('inf')
            d_izq = math.hypot(fx - self.coordenadas_anillos[max(idx_ancla-1, 0)][0],
                               fy - self.coordenadas_anillos[max(idx_ancla-1, 0)][1]) if idx_ancla > 0 else float('inf')
            insertar_despues = d_der <= d_izq or idx_ancla == 0

            if insertar_despues:
                insert_at = idx_ancla + 1
                if reemplazar_tramo:
                    # Determinar cuántos puntos hay hasta el extremo detectado
                    ultimo_x, ultimo_y = coordenadas[-1]
                    fin_tramo = insert_at
                    for k in range(insert_at, n):
                        kx, ky = self.coordenadas_anillos[k]
                        if math.hypot(kx - ultimo_x, ky - ultimo_y) < self._tolerancia_punto() * 3:
                            fin_tramo = k + 1
                            break
                    del self.coordenadas_anillos[insert_at:fin_tramo]
                    del self.es_salto[insert_at:fin_tramo]
            else:
                insert_at = idx_ancla
                coordenadas = list(reversed(coordenadas))
                if reemplazar_tramo:
                    ultimo_x, ultimo_y = coordenadas[-1]
                    ini_tramo = 0
                    for k in range(insert_at - 1, -1, -1):
                        kx, ky = self.coordenadas_anillos[k]
                        if math.hypot(kx - ultimo_x, ky - ultimo_y) < self._tolerancia_punto() * 3:
                            ini_tramo = k
                            break
                    del self.coordenadas_anillos[ini_tramo:insert_at]
                    del self.es_salto[ini_tramo:insert_at]
                    insert_at = ini_tramo

            for i, coord in enumerate(coordenadas):
                self.coordenadas_anillos.insert(insert_at + i, coord)
                self.es_salto.insert(insert_at + i, False)

        self.redibujar_todo()
        self.recalcular_todas_las_distancias()
        self.progreso_modificado.emit()

    # -------------------------------------------------------------------------
    # Caminos inactivos (otros radios guardados)
    # -------------------------------------------------------------------------

    def cargar_caminos_inactivos(self, caminos: dict):
        # Cachear los datos: necesitamos volver a llamar a esta función
        # cuando cambia el zoom (para que las etiquetas de año y los grosores
        # de tick se rerenderan con la nueva escala). Sin esto los labels
        # viejos quedaban con el tamaño en escena de un zoom anterior.
        self._caminos_inactivos_data = dict(caminos)

        # Limpiar items previos (puntos, líneas Y textos)
        for visuales in self.items_caminos_inactivos.values():
            items_a_remover = (
                visuales.get("puntos", [])
                + visuales.get("lineas", [])
                + visuales.get("textos", [])
            )
            for item in items_a_remover:
                try:
                    self.scene().removeItem(item)
                except Exception:
                    pass
        self.items_caminos_inactivos.clear()

        # Grosor de tick adaptado al zoom (mismo helper que el camino activo)
        grosor = self._grosor_tick_zoom()

        pen_p = QPen(QColor(COLOR_PUNTO_INACTIVO))
        pen_p.setCosmetic(True); pen_p.setWidthF(grosor)
        brush_p = QBrush(QColor(COLOR_PUNTO_INACTIVO))
        pen_l = QPen(QColor("#80c080"))
        pen_l.setCosmetic(True); pen_l.setWidthF(grosor)

        escala = self._escala_vista()

        for nombre, datos in caminos.items():
            coords, saltos = datos["coords"], datos["es_salto"]
            anio_medula = datos.get("anio_medula", 0)
            anio_corteza = datos.get("anio_corteza", 0)
            dir_idx = datos.get("dir_idx", 0)
            medula_a_corteza = (dir_idx == 0)

            puntos, lineas, textos = [], [], []
            pen_tick_in = QPen(QColor(COLOR_PUNTO_INACTIVO))
            pen_tick_in.setCosmetic(True); pen_tick_in.setWidthF(grosor)

            # Pre-calcular el año de cada coord (necesario para las etiquetas)
            anios_por_idx: list[int | None] = [None] * len(coords)
            anillos_validos_acum = 0
            for i in range(len(coords)):
                if i > 0:
                    val = saltos[i] if i < len(saltos) else False
                    val_sig = saltos[i + 1] if i + 1 < len(saltos) else False
                    if (not _es_amarillo(val) and not _es_ewlw(val)
                            and not _es_espacio(val_sig)):
                        anillos_validos_acum += 1
                if medula_a_corteza:
                    anios_por_idx[i] = (anio_medula + anillos_validos_acum - 1
                                         if i > 0 else (anio_medula - 1))
                else:
                    anios_por_idx[i] = (anio_corteza - anillos_validos_acum + 1
                                         if i > 0 else anio_corteza)

            # Primera pasada: dibujar TODOS los ticks y líneas (sin filtro).
            # El grosor ya está adaptado al zoom para evitar saturación.
            # Z-values: línea de conexión por debajo del tick (igual que el
            # camino activo) para que los límites siempre sean visibles.
            for i, c in enumerate(coords):
                # Línea de conexión primero (queda detrás del tick)
                if i > 0:
                    pen = self._pen_salto if _es_amarillo(saltos[i]) else pen_l
                    linea_conexion = self.scene().addLine(
                        coords[i - 1][0], coords[i - 1][1], c[0], c[1], pen)
                    linea_conexion.setZValue(1)  # debajo de los ticks
                    lineas.append(linea_conexion)
                # Tick perpendicular — SIEMPRE se dibuja, sin filtro.
                # El grosor se adapta vía pen_tick_in (ya ajustado al zoom).
                t1, t2 = self._perp_tick(coords, i, largo_px_pantalla=22.0)
                tick = self.scene().addLine(t1[0], t1[1], t2[0], t2[1], pen_tick_in)
                tick.setZValue(2)  # arriba de la línea de conexión
                puntos.append(tick)

            # Segunda pasada: etiquetas de año.
            # ESTAS SÍ se filtran por densidad (el helper compartido),
            # porque mostrar todos los números a la vez no aporta valor
            # cuando están comprimidos a pixeles. Además, filtro de
            # OVERLAP por bbox para evitar superposiciones (mismo
            # comportamiento que el camino activo).
            ultimo_bbox_inactivo = None
            for i in range(1, len(coords)):
                val = saltos[i] if i < len(saltos) else False
                val_sig = saltos[i + 1] if i + 1 < len(saltos) else False
                es_amar = _es_amarillo(val)
                es_ewlw_aqui = _es_ewlw(val)
                es_espacio_sig = _es_espacio(val_sig)
                es_quiebre_ini_aqui = _es_quiebre_ini(val)

                # Saltar segmentos que no llevan etiqueta
                if es_amar or es_ewlw_aqui or es_quiebre_ini_aqui:
                    continue

                anio = anios_por_idx[i] if anios_por_idx[i] is not None else 0
                mostrar = self._filtro_densidad_anio(anio, escala)

                if mostrar:
                    # Chequeo de overlap antes de crear el label
                    bbox_nuevo = self._estimar_bbox_label_pantalla(
                        str(anio), coords[i], coords[i - 1])
                    if (ultimo_bbox_inactivo is not None
                            and self._labels_se_solapan(ultimo_bbox_inactivo,
                                                         bbox_nuevo)):
                        continue
                    # Mismo método que el camino activo: solo cambian los
                    # colores del texto. Color base verde claro para años
                    # normales y verde más brillante para décadas — análogo
                    # a "#cccccc" vs blanco del activo. El fondo negro por
                    # defecto se mantiene para que el comportamiento de zoom
                    # / lectura sea idéntico al camino activo (que el usuario
                    # validó).
                    color_etiqueta = "#a0d0a0" if anio % INTERVALO_MARCADOR_DECADA == 0 else "#80c080"
                    item_txt = self._crear_etiqueta_anio(
                        str(anio),
                        color_etiqueta,
                        coord_actual=coords[i],
                        coord_anterior=coords[i - 1],
                    )
                    textos.append(item_txt)
                    ultimo_bbox_inactivo = bbox_nuevo

            self.items_caminos_inactivos[nombre] = {
                "puntos": puntos,
                "lineas": lineas,
                "textos": textos,
            }

    def toggle_camino_inactivo(self, nombre: str, visible: bool):
        if nombre in self.items_caminos_inactivos:
            visuales = self.items_caminos_inactivos[nombre]
            for item in (
                visuales.get("puntos", [])
                + visuales.get("lineas", [])
                + visuales.get("textos", [])
            ):
                item.setVisible(visible)

    def cargar_camino_activo(self, coordenadas: list, es_salto: list):
        self.coordenadas_anillos = [tuple(c) for c in coordenadas]
        self.es_salto = list(es_salto)
        self.redibujar_todo()


# =============================================================================
# PESTAÑA INDIVIDUAL POR IMAGEN
# =============================================================================

class PestanaImagen(QWidget):
    """
    Pestaña completa asociada a una imagen escaneada.
    Incluye el lienzo de marcado, la gestión de radios guardados,
    la co-datación en vivo y las funciones de importación/exportación.
    """
    # Emitida cada vez que médula, corteza o código cambian (incluso con blockSignals)
    metadatos_cambiados = pyqtSignal()

    def __init__(self, ruta_imagen: str, ventana_padre: QWidget):
        super().__init__()
        self._ventana_padre = ventana_padre
        self.ruta_imagen_actual = ruta_imagen
        self.datos_medidos: list[dict] = []
        self.caminos_guardados: dict = {}
        self.serie_maestra: pd.DataFrame | None = None
        self.nombre_maestra: str = "Ninguna"
        self._exportador = ExportadorDendro(self)
        self.item_imagen = None
        # Imagen original para cambio de banda
        self._img_bgr_original: np.ndarray | None = None
        # Perfiles de especie para auto-detección
        self._perfiles_especie = cargar_perfiles()
        self._especie_activa: str = next(iter(self._perfiles_especie))
        # Estimación de médula
        self._pith_estimacion: dict | None = None
        self._pith_undo: dict | None = None
        # Blue/Red/Green Intensity
        self._bi_datos: list[dict] = []    # resultados por anillo
        self._bi_banda: int = 30           # banda de muestreo en px (estándar CooRecorder)
        self._bi_porcentaje: int = 100     # % del anillo a medir
        self._bi_alineacion: str = "centro"  # "ew" | "centro" | "lw"
        self._bi_canal_vis: str = "B"      # canal activo para visualización
        self._bi_canales: list[str] = ["B", "G", "R", "Gray"]
        self._bi_plot_items: dict = {}     # items del gráfico BI
        self._items_banda_bi: list = []    # marcadores de banda en la imagen
        self._pith_metodo: str = "arco"
        self._pith_n_int: int = 5
        self._pith_usar_ref: bool = False
        self._pith_dap_mm: float = 200.0
        self._pith_metodo_calculo: str = "rg"  # 'rg', 'bai', o 'promedio'

        # Origen del DPI actual. Posibles valores:
        #   "auto"     → detectado automáticamente desde metadata JFIF/EXIF/TIFF
        #   "manual"   → cambiado a mano por el usuario en el diálogo DPI
        #   "regla"    → calibrado midiendo una distancia conocida
        #   "default"  → no se detectó, se usa DPI_DEFECTO
        #   "guardado" → restaurado desde un estado/sesión guardada
        # Sirve para mostrar al usuario qué origen tiene el DPI con el que
        # está trabajando, sobre todo cuando lo cambia y vuelve a abrir el
        # diálogo (antes seguía diciendo "Detectado automáticamente" aun
        # después de un override manual, lo cual era confuso).
        self._dpi_modo: str = "default"

        self._construir_ui()
        self._cargar_imagen(ruta_imagen)

    # -------------------------------------------------------------------------
    # Construcción de la interfaz
    # -------------------------------------------------------------------------

    def _construir_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # ── Barra de herramientas colapsable ──
        self._panel_herramientas = QWidget()
        lh = QVBoxLayout(self._panel_herramientas)
        lh.setContentsMargins(0, 0, 0, 0)
        lh.setSpacing(2)
        lh.addLayout(self._construir_barra_herramientas())

        self.label_estado = QLabel("Preparando imagen...")
        self.label_estado.setStyleSheet("font-size:12px; font-weight:bold; color:#5cb85c;")
        lh.addWidget(self.label_estado)

        layout.addWidget(self._panel_herramientas)

        # Botón colapsar barra de herramientas
        self._btn_toggle_barra = QPushButton("🔼 Herramientas")
        self._btn_toggle_barra.setFixedHeight(18)
        self._btn_toggle_barra.setStyleSheet(
            "QPushButton{font-size:10px; padding:0px 6px; border:none; "
            "color:#888; background:transparent;}"
            "QPushButton:hover{color:white; background:#555;}"
        )
        self._btn_toggle_barra.setToolTip("Mostrar/ocultar barra de herramientas (Ctrl+H)")
        self._btn_toggle_barra.clicked.connect(self._toggle_barra)
        layout.addWidget(self._btn_toggle_barra)

        layout.addWidget(self._construir_splitter_principal())

        # Atajos de teclado para colapsar paneles
        QShortcut(QKeySequence("Ctrl+H"), self).activated.connect(self._toggle_barra)
        QShortcut(QKeySequence("Ctrl+G"), self).activated.connect(self._toggle_grafico)

        # Código de serie por defecto
        nombre_base = os.path.splitext(os.path.basename(self.ruta_imagen_actual))[0]
        self.input_codigo.setText(nombre_base.replace(" ", "")[:TUCSON_MAX_CHARS_ID].upper())

    def _toggle_barra(self):
        """Muestra/oculta la barra de herramientas."""
        visible = self._panel_herramientas.isVisible()
        self._panel_herramientas.setVisible(not visible)
        # not visible = nuevo estado: True=ahora visible, False=ahora oculto
        self._btn_toggle_barra.setText(
            "🔼 Herramientas" if not visible else "🔽 Herramientas"
        )

    def _toggle_grafico(self):
        """Muestra/oculta el panel del gráfico."""
        visible = self._panel_codatacion.isVisible()
        self._panel_codatacion.setVisible(not visible)
        self._btn_toggle_grafico.setText(
            "🔼 Gráfico" if not visible else "🔽 Gráfico"
        )

    def _toggle_split_view(self):
        """Activa o desactiva la vista dividida."""
        es_visible = self.panel_split.isVisible()
        if es_visible:
            self.panel_split.hide()
            self._btn_split.setChecked(False)
        else:
            # Refrescar lista de imágenes y abrir
            self.panel_split.refrescar_combo()
            self.panel_split.show()
            self.panel_split._sincronizar_con_principal()
            # Ajustar splitter 50/50
            total = self._splitter_vistas.width()
            self._splitter_vistas.setSizes([total // 2, total // 2])
            self._btn_split.setChecked(True)

    # ── Popup helpers ─────────────────────────────────────────────────────

    def _crear_popup(self) -> QWidget:
        """Crea un QWidget flotante estilizado para usar como dropdown."""
        popup = QWidget(self, Qt.WindowType.Popup)
        popup.setStyleSheet(
            "QWidget{background-color:#333; border:1px solid #555; "
            "border-radius:6px; color:white;}"
            "QPushButton{background-color:#4a4a4a; color:white; "
            "padding:5px 10px; border-radius:3px; min-width:60px;}"
            "QPushButton:hover{background-color:#5a5a5a;}"
            "QPushButton:checked{background-color:#5cb85c; color:white; font-weight:bold;}"
            "QLabel{color:#ccc; font-size:11px; background:transparent;}"
            "QComboBox{background-color:#4a4a4a; color:white; padding:4px; border:1px solid #666;}"
            "QSlider::groove:horizontal{background:#555; height:6px; border-radius:3px;}"
            "QSlider::handle:horizontal{background:#FF8C00; width:14px; height:14px; "
            "border-radius:7px; margin:-4px 0;}"
        )
        return popup

    def _crear_separador(self) -> QWidget:
        """Crea una línea horizontal estilizada para separar secciones."""
        from PyQt6.QtWidgets import QFrame
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("QFrame{color:#555; background:#555; max-height:1px;}")
        return sep

    def _toggle_popup(self, popup: QWidget):
        """Muestra u oculta un popup debajo de su botón padre."""
        if popup.isVisible():
            popup.hide()
            return
        # Cerrar el otro popup si está abierto
        for p in [self._popup_herram, self._popup_imagen, self._popup_ewlw]:
            if p is not popup and p.isVisible():
                p.hide()
        # Posicionar debajo del botón que lo activó
        btn = (self._btn_herram if popup is self._popup_herram
               else self._btn_imagen if popup is self._popup_imagen
               else self._btn_ewlw_dd)
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        popup.move(pos)
        popup.show()

    def _construir_barra_herramientas(self) -> QHBoxLayout:
        barra = QHBoxLayout()
        barra.setSpacing(3)

        # ══════════════════════════════════════════════════════════════════
        # SIEMPRE VISIBLES: botones de medición + edición
        # ══════════════════════════════════════════════════════════════════
        self.grupo_modos = QButtonGroup(self)
        self._botones_modo = {}

        _btn_colores = {
            MODO_MEDIR:    ("#2ecc71", "#27ae60"),  # verde
            MODO_SALTO:    ("#f39c12", "#d68910"),  # ámbar
            MODO_ESPACIO:  ("#9b59b6", "#8e44ad"),  # púrpura
            MODO_INSERTAR: ("#3498db", "#2980b9"),  # azul
            MODO_MOVER:    ("#1abc9c", "#16a085"),  # turquesa
            MODO_BORRAR:   ("#e74c3c", "#c0392b"),  # rojo
            MODO_SELECCIONAR: ("#FFEB3B", "#FBC02D"),  # amarillo
        }

        botones_visibles = [
            ("📍 Medir",       MODO_MEDIR,        "Ctrl+A", "Clic izq: añadir anillo · Clic der: salto · Scroll: zoom"),
            ("🎯 Seleccionar", MODO_SELECCIONAR,  "Ctrl+R", "Seleccionar múltiples anillos para borrar / convertir a salto / quiebre"),
            ("🔀 Salto",       MODO_SALTO,        "Ctrl+D", "Añadir salto (discontinuidad)"),
            ("⊘ Quiebre",      MODO_ESPACIO,      "Ctrl+E", "Marcar quiebre intra-anillo\nNo cuenta como anillo"),
            ("➕ Insertar",     MODO_INSERTAR,     "Ctrl+W", "Insertar punto en segmento existente"),
            ("🖐 Mover",        MODO_MOVER,        "Ctrl+S", "Mover punto"),
            ("✕ Borrar",       MODO_BORRAR,       "Ctrl+X", "Borrar punto con clic"),
        ]
        for texto, modo, atajo, tooltip in botones_visibles:
            bg, bg_hover = _btn_colores[modo]
            btn = QPushButton(texto)
            btn.setCheckable(True)
            # MODO_SELECCIONAR usa color amarillo claro: el texto blanco no
            # contrasta bien, así que aplicamos texto NEGRO en lugar de blanco.
            color_texto = "#222" if modo == MODO_SELECCIONAR else "white"
            btn.setStyleSheet(
                f"QPushButton{{background-color:{bg}; color:{color_texto}; font-weight:bold; "
                f"padding:4px 10px; border-radius:4px;}}"
                f"QPushButton:hover{{background-color:{bg_hover};}}"
                f"QPushButton:checked{{background-color:#fff; color:{bg}; "
                f"border:2px solid {bg};}}"
            )
            btn.setToolTip(f"{tooltip} ({atajo})")
            btn.clicked.connect(lambda checked, m=modo: self.set_modo(m))
            self.grupo_modos.addButton(btn)
            self._botones_modo[modo] = btn
            barra.addWidget(btn)
            QShortcut(QKeySequence(atajo), self).activated.connect(lambda m=modo: self.set_modo(m))
        self._botones_modo[MODO_MEDIR].setChecked(True)

        barra.addSpacing(6)

        # ══════════════════════════════════════════════════════════════════
        # 🍂 EW/LW (dropdown)
        # ══════════════════════════════════════════════════════════════════
        self._btn_ewlw_dd = QPushButton("🍂 EW/LW ▾")
        self._btn_ewlw_dd.setStyleSheet(
            "QPushButton{background-color:#FF8C00; color:white; font-weight:bold; "
            "padding:4px 10px; border-radius:4px;}"
            "QPushButton:hover{background-color:#e67e22;}"
        )
        self._btn_ewlw_dd.setToolTip("Herramientas Earlywood / Latewood")
        self._btn_ewlw_dd.clicked.connect(lambda: self._toggle_popup(self._popup_ewlw))
        barra.addWidget(self._btn_ewlw_dd)

        self._popup_ewlw = self._crear_popup()
        pe_layout = QVBoxLayout(self._popup_ewlw)
        pe_layout.setContentsMargins(8, 8, 8, 8)
        pe_layout.setSpacing(4)

        btn_ewlw_marcar = QPushButton("🍂 Marcar EW/LW")
        btn_ewlw_marcar.setCheckable(True)
        btn_ewlw_marcar.setToolTip("Marcar límite Earlywood/Latewood (Ctrl+L)")
        btn_ewlw_marcar.clicked.connect(
            lambda: (self.set_modo(MODO_EWLW), self._popup_ewlw.hide()))
        self.grupo_modos.addButton(btn_ewlw_marcar)
        self._botones_modo[MODO_EWLW] = btn_ewlw_marcar
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(
            lambda: self.set_modo(MODO_EWLW))
        pe_layout.addWidget(btn_ewlw_marcar)

        self.btn_ewlw_auto = QPushButton("🔍 Auto-detectar EW/LW")
        self.btn_ewlw_auto.setToolTip("Detectar automáticamente EW/LW en cada anillo")
        self.btn_ewlw_auto.setStyleSheet(
            "QPushButton{background-color:#FF8C00; color:white; font-weight:bold;}")
        self.btn_ewlw_auto.clicked.connect(
            lambda: (self._detectar_ewlw_automatico(), self._popup_ewlw.hide()))
        pe_layout.addWidget(self.btn_ewlw_auto)

        self.btn_ewlw_eliminar = QPushButton("🗑 Eliminar todos EW/LW")
        self.btn_ewlw_eliminar.setToolTip("Eliminar todos los límites EW/LW del radio activo")
        self.btn_ewlw_eliminar.setStyleSheet(
            "QPushButton{background-color:#8B0000; color:white;}")
        self.btn_ewlw_eliminar.clicked.connect(
            lambda: (self._eliminar_ewlw(), self._popup_ewlw.hide()))
        pe_layout.addWidget(self.btn_ewlw_eliminar)

        # ══════════════════════════════════════════════════════════════════
        # 🔧 HERRAMIENTAS (dropdown)
        # ══════════════════════════════════════════════════════════════════
        self._btn_herram = QPushButton("🔧 Herramientas ▾")
        self._btn_herram.setStyleSheet(
            "QPushButton{background-color:#555; color:white; font-weight:bold; "
            "padding:4px 10px; border-radius:4px;}"
            "QPushButton:hover{background-color:#666;}"
        )
        self._btn_herram.setToolTip("Deshacer, anomalías, estimación, calibración")
        self._btn_herram.clicked.connect(lambda: self._toggle_popup(self._popup_herram))
        barra.addWidget(self._btn_herram)

        self._popup_herram = self._crear_popup()
        ph_layout = QVBoxLayout(self._popup_herram)
        ph_layout.setContentsMargins(8, 8, 8, 8)
        ph_layout.setSpacing(4)

        fila_undo = QHBoxLayout()
        self.btn_deshacer = QPushButton("⏪ Deshacer")
        self.btn_deshacer.setStyleSheet("background-color:#d9534f; color:white;")
        self.btn_deshacer.setToolTip("Deshacer (Ctrl+Z)")
        self.btn_deshacer.clicked.connect(self._deshacer)
        QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self._deshacer)
        fila_undo.addWidget(self.btn_deshacer)
        self.btn_rehacer = QPushButton("Rehacer ⏩")
        self.btn_rehacer.setStyleSheet("background-color:#5bc0de; color:white;")
        self.btn_rehacer.setToolTip("Rehacer (Ctrl+Y)")
        self.btn_rehacer.clicked.connect(self._rehacer)
        QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(self._rehacer)
        fila_undo.addWidget(self.btn_rehacer)
        ph_layout.addLayout(fila_undo)
        ph_layout.addWidget(self._crear_separador())

        fila_anom = QHBoxLayout()
        self.btn_anomalia = QPushButton("🔴 Anomalías")
        self.btn_anomalia.setCheckable(True)
        self.btn_anomalia.setToolTip("Modo anomalías (Ctrl+Q)")
        self.btn_anomalia.clicked.connect(
            lambda: (self.set_modo(MODO_ANOMALIA), self._popup_herram.hide()))
        self.grupo_modos.addButton(self.btn_anomalia)
        self._botones_modo[MODO_ANOMALIA] = self.btn_anomalia
        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(
            lambda: self.set_modo(MODO_ANOMALIA))
        fila_anom.addWidget(self.btn_anomalia)
        self.combo_tipo_anomalia = QComboBox()
        self.combo_tipo_anomalia.setToolTip("Tipo de anomalía a marcar")
        self.combo_tipo_anomalia.setMinimumWidth(140)
        for tipo, (icono, etiqueta, color) in TIPOS_ANOMALIA.items():
            self.combo_tipo_anomalia.addItem(f"{icono} {etiqueta}", userData=tipo)
        fila_anom.addWidget(self.combo_tipo_anomalia)
        ph_layout.addLayout(fila_anom)
        ph_layout.addWidget(self._crear_separador())

        fila_pith = QHBoxLayout()
        self.btn_pith = QPushButton("🎯 Anillos al centro")
        self.btn_pith.setCheckable(True)
        self.btn_pith.setToolTip("Estimar anillos faltantes (Ctrl+P)")
        self.btn_pith.setStyleSheet(
            "QPushButton{background-color:#8B4513; color:white; font-weight:bold;}"
            "QPushButton:checked{background-color:#FF8C00; color:white;}")
        self.btn_pith.clicked.connect(
            lambda: (self._iniciar_estimacion_pith(), self._popup_herram.hide()))
        self.grupo_modos.addButton(self.btn_pith)
        self._botones_modo[MODO_PITH] = self.btn_pith
        QShortcut(QKeySequence("Ctrl+P"), self).activated.connect(self._iniciar_estimacion_pith)
        fila_pith.addWidget(self.btn_pith)
        self.btn_deshacer_pith = QPushButton("↩ Deshacer estimación")
        self.btn_deshacer_pith.setStyleSheet(
            "QPushButton{background-color:#d9534f; color:white; font-weight:bold;}")
        self.btn_deshacer_pith.clicked.connect(self._deshacer_pith_offset)
        self.btn_deshacer_pith.hide()
        fila_pith.addWidget(self.btn_deshacer_pith)
        ph_layout.addLayout(fila_pith)
        ph_layout.addWidget(self._crear_separador())

        fila_cal = QHBoxLayout()
        self.btn_calibrar_dpi = QPushButton("📐 DPI")
        self.btn_calibrar_dpi.setToolTip("Información de imagen y configuración de DPI")
        self.btn_calibrar_dpi.clicked.connect(
            lambda: (self._calibrar_por_dpi(), self._popup_herram.hide()))
        fila_cal.addWidget(self.btn_calibrar_dpi)
        self.btn_calibrar = QPushButton("📏 Regla")
        self.btn_calibrar.setToolTip("Calibrar midiendo una distancia conocida")
        self.btn_calibrar.clicked.connect(
            lambda: (self._activar_calibracion(), self._popup_herram.hide()))
        fila_cal.addWidget(self.btn_calibrar)
        ph_layout.addLayout(fila_cal)

        # ══════════════════════════════════════════════════════════════════
        # 🎨 COLORES (dropdown)
        # ══════════════════════════════════════════════════════════════════
        from PyQt6.QtWidgets import QSlider

        self._btn_imagen = QPushButton("🎨 Colores ▾")
        self._btn_imagen.setStyleSheet(
            "QPushButton{background-color:#555; color:white; font-weight:bold; "
            "padding:4px 10px; border-radius:4px;}"
            "QPushButton:hover{background-color:#666;}"
        )
        self._btn_imagen.setToolTip("Visualización por banda y brillo/contraste")
        self._btn_imagen.clicked.connect(lambda: self._toggle_popup(self._popup_imagen))
        barra.addWidget(self._btn_imagen)

        self._popup_imagen = self._crear_popup()
        pi_layout = QVBoxLayout(self._popup_imagen)
        pi_layout.setContentsMargins(8, 8, 8, 8)
        pi_layout.setSpacing(6)
        pi_layout.addWidget(QLabel("Canal de visualización:"))

        fila_banda = QHBoxLayout()
        self._grupo_banda = QButtonGroup(self)
        self._grupo_banda.setExclusive(True)
        self._banda_actual = "RGB"
        btn_estilo = (
            "QPushButton{min-width:40px; padding:4px 8px; font-weight:bold; "
            "border:1px solid #555; border-radius:3px; font-size:13px;}"
            "QPushButton:checked{border:2px solid #FFD700;}")
        for nombre_b, color_b, atajo_b in [
            ("RGB", "#aaa", "1"), ("R", "#FF4444", "2"),
            ("G", "#44CC44", "3"), ("B", "#4488FF", "4"), ("Gris", "#999", "5"),
        ]:
            btn_b = QPushButton(nombre_b)
            btn_b.setCheckable(True)
            btn_b.setFixedHeight(30)
            btn_b.setStyleSheet(
                btn_estilo + f"QPushButton{{color:{color_b};}}"
                f"QPushButton:checked{{color:{color_b}; background:rgba(255,255,255,15);}}")
            btn_b.setToolTip(f"Canal {nombre_b} (tecla {atajo_b})")
            btn_b.clicked.connect(lambda _, b=nombre_b: self._cambiar_banda(b))
            self._grupo_banda.addButton(btn_b)
            fila_banda.addWidget(btn_b)
            QShortcut(QKeySequence(atajo_b), self).activated.connect(
                lambda b=nombre_b: self._cambiar_banda(b))
            if nombre_b == "RGB":
                btn_b.setChecked(True)
        pi_layout.addLayout(fila_banda)
        pi_layout.addWidget(self._crear_separador())

        fila_brillo = QHBoxLayout()
        fila_brillo.addWidget(QLabel("☀ Brillo"))
        self._slider_brillo = QSlider(Qt.Orientation.Horizontal)
        self._slider_brillo.setRange(-100, 100)
        self._slider_brillo.setValue(0)
        self._slider_brillo.sliderReleased.connect(self._guardar_ajustes_banda)
        fila_brillo.addWidget(self._slider_brillo)
        pi_layout.addLayout(fila_brillo)

        fila_contraste = QHBoxLayout()
        fila_contraste.addWidget(QLabel("◐ Contraste"))
        self._slider_contraste = QSlider(Qt.Orientation.Horizontal)
        self._slider_contraste.setRange(50, 300)
        self._slider_contraste.setValue(100)
        self._slider_contraste.sliderReleased.connect(self._guardar_ajustes_banda)
        fila_contraste.addWidget(self._slider_contraste)
        pi_layout.addLayout(fila_contraste)

        self._timer_banda = QTimer()
        self._timer_banda.setSingleShot(True)
        self._timer_banda.setInterval(120)
        self._timer_banda.timeout.connect(self._aplicar_banda_actual)
        self._slider_brillo.valueChanged.connect(lambda: self._timer_banda.start())
        self._slider_contraste.valueChanged.connect(lambda: self._timer_banda.start())
        self._slider_brillo.mouseDoubleClickEvent = lambda e: self._slider_brillo.setValue(0)
        self._slider_contraste.mouseDoubleClickEvent = lambda e: self._slider_contraste.setValue(100)

        # ══════════════════════════════════════════════════════════════════
        # Vista dividida
        barra.addSpacing(6)
        self._btn_split = QPushButton("◫ Ventana")
        self._btn_split.setCheckable(True)
        self._btn_split.setStyleSheet(
            "QPushButton{background-color:#555; color:white; font-weight:bold; "
            "padding:4px 10px; border-radius:4px;}"
            "QPushButton:hover{background-color:#666;}"
            "QPushButton:checked{background-color:#FF8C00; color:white;}"
        )
        self._btn_split.setToolTip(
            "Ventana auxiliar: muestra la misma imagen (u otra de las pestañas\n"
            "abiertas) en un panel con zoom y posición independientes.\n"
            "Útil para comparar zonas distantes o dos muestras a la vez. (Ctrl+B)")
        self._btn_split.clicked.connect(self._toggle_split_view)
        barra.addWidget(self._btn_split)
        QShortcut(QKeySequence("Ctrl+B"), self).activated.connect(self._toggle_split_view)

        # ══════════════════════════════════════════════════════════════════
        barra.addStretch()

        self.btn_importar_pos = QPushButton("📥 Cargar .pos")
        self.btn_importar_pos.setStyleSheet(
            "background-color:#5bc0de; color:white; font-weight:bold; padding:4px 8px;")
        self.btn_importar_pos.setToolTip("Importar archivo CooRecorder .pos")
        self.btn_importar_pos.clicked.connect(self._importar_pos)
        barra.addWidget(self.btn_importar_pos)

        # Metadatos ocultos (lógica interna, visualmente en main.py)
        self.input_codigo = QLineEdit()
        self.input_codigo.setMaxLength(TUCSON_MAX_CHARS_ID)
        self.input_codigo.hide()

        self.combo_dir = QComboBox()
        self.combo_dir.addItems(["Médula→Corteza (+1)", "Corteza→Médula (-1)"])
        self.combo_dir.currentIndexChanged.connect(self._al_cambiar_direccion)
        self.combo_dir.hide()

        self.spin_medula = QSpinBox()
        self.spin_medula.setRange(-10000, 5000)
        self.spin_medula.valueChanged.connect(self._aplicar_anio_medula)
        self.spin_medula.hide()

        self.spin_corteza = QSpinBox()
        self.spin_corteza.setRange(-10000, 5000)
        self.spin_corteza.valueChanged.connect(self._aplicar_anio_corteza)
        self.spin_corteza.hide()

        from PyQt6.QtWidgets import QCheckBox
        self.check_tiene_medula = QCheckBox()
        self.check_tiene_medula.setToolTip("¿La serie llega físicamente a la médula?")
        self.check_tiene_medula.hide()

        self.check_tiene_corteza = QCheckBox()
        self.check_tiene_corteza.setToolTip("¿La serie llega físicamente a la corteza?")
        self.check_tiene_corteza.hide()

        # ── Auto-detección: combo especie + botón (se construyen aquí,
        #    pero se EXPONEN mediante métodos para que main_anillos los mueva
        #    a la barra superior si lo desea) ──────────────────────────────
        self.combo_especie = QComboBox()
        self.combo_especie.setToolTip("Especie activa para la auto-detección")
        self.combo_especie.setMinimumWidth(130)
        self._refrescar_combo_especie()
        self.combo_especie.currentTextChanged.connect(self._al_cambiar_especie)
        self.combo_especie.hide()   # Se agrega desde main_anillos a barra_top

        self.btn_cfg_especie = QPushButton("⚙")
        self.btn_cfg_especie.setToolTip("Configurar parámetros de detección para esta especie")
        self.btn_cfg_especie.setFixedWidth(28)
        self.btn_cfg_especie.clicked.connect(self._configurar_especie)
        self.btn_cfg_especie.hide()

        self.btn_nueva_especie = QPushButton("＋")
        self.btn_nueva_especie.setToolTip("Crear nuevo perfil de especie")
        self.btn_nueva_especie.setFixedWidth(28)
        self.btn_nueva_especie.clicked.connect(self._nueva_especie)
        self.btn_nueva_especie.hide()

        self.btn_autodetect = QPushButton("🔍 Auto-detectar")
        self.btn_autodetect.setCheckable(True)
        self.btn_autodetect.setStyleSheet(
            "QPushButton{background-color:#5cb85c; color:white; font-weight:bold;}"
            "QPushButton:checked{background-color:#f0ad4e; color:white;}"
        )
        self.btn_autodetect.setToolTip(
            "Activa el modo auto-detección.\n"
            "Clic 1: médula   Clic 2: corteza\n"
            "El programa detecta todos los anillos automáticamente."
        )
        self.btn_autodetect.clicked.connect(self._toggle_autodetect)
        self.grupo_modos.addButton(self.btn_autodetect)
        self._botones_modo[MODO_AUTODETECT] = self.btn_autodetect
        self.btn_autodetect.hide()  # Se agrega desde main_anillos a barra_top

        self.btn_exportar = QPushButton("💾 Exp")
        self.btn_exportar.clicked.connect(self._ejecutar_exportacion)
        barra.addWidget(self.btn_exportar)

        return barra

    def _construir_splitter_principal(self) -> QWidget:
        contenedor = QWidget()
        layout_v = QVBoxLayout(contenedor)
        layout_v.setContentsMargins(0, 0, 0, 0)
        layout_v.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter_principal = splitter

        # -- Zona superior: imagen (+ vista dividida opcional) + lista de radios --
        splitter_sup = QSplitter(Qt.Orientation.Horizontal)

        # Splitter interno para vista principal + vista dividida
        self._splitter_vistas = QSplitter(Qt.Orientation.Horizontal)
        self.escena = QGraphicsScene()
        self.vista = VistaInteractiva(self.escena, self)
        self.vista.mediciones_actualizadas.connect(self._sincronizar_mediciones)
        self.vista.progreso_modificado.connect(self._guardar_estado)
        self._splitter_vistas.addWidget(self.vista)

        # Vista secundaria (panel dividido, oculto por defecto)
        self.panel_split = PanelSplit(self.escena, self)
        self.panel_split.hide()
        self._splitter_vistas.addWidget(self.panel_split)

        splitter_sup.addWidget(self._splitter_vistas)
        splitter_sup.addWidget(self._construir_panel_radios())
        splitter_sup.setSizes([1000, 150])
        splitter.addWidget(splitter_sup)

        # -- Botón colapsar gráfico --
        self._btn_toggle_grafico = QPushButton("🔼 Gráfico")
        self._btn_toggle_grafico.setFixedHeight(18)
        self._btn_toggle_grafico.setStyleSheet(
            "QPushButton{font-size:10px; padding:0px 6px; border:none; "
            "color:#888; background:transparent;}"
            "QPushButton:hover{color:white; background:#555;}"
        )
        self._btn_toggle_grafico.setToolTip("Mostrar/ocultar panel de gráfico (Ctrl+G)")
        self._btn_toggle_grafico.clicked.connect(self._toggle_grafico)

        # -- Zona inferior: co-datación en vivo --
        self._panel_codatacion = self._construir_panel_codatacion()
        splitter.addWidget(self._panel_codatacion)
        splitter.setSizes([600, 200])
        splitter.setChildrenCollapsible(False)

        layout_v.addWidget(splitter)
        layout_v.addWidget(self._btn_toggle_grafico)
        return contenedor

    def _construir_panel_radios(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Radios Guardados:"))

        self.lista_caminos = QListWidget()
        self.lista_caminos.itemChanged.connect(self._toggle_visual_camino)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self.lista_caminos).activated.connect(self._eliminar_camino)
        QShortcut(QKeySequence(Qt.Key.Key_Backspace), self.lista_caminos).activated.connect(self._eliminar_camino)
        layout.addWidget(self.lista_caminos)

        btn_eliminar = QPushButton("🗑️ Eliminar")
        btn_eliminar.setStyleSheet("background-color:#d9534f; color:white;")
        btn_eliminar.clicked.connect(self._eliminar_camino)
        layout.addWidget(btn_eliminar)

        self.btn_editar_camino = QPushButton("✏️ Editar Radio")
        self.btn_editar_camino.setStyleSheet("background-color:#f0ad4e; color:white; font-weight:bold;")
        self.btn_editar_camino.clicked.connect(self._editar_camino)
        layout.addWidget(self.btn_editar_camino)

        self.btn_finalizar_camino = QPushButton("✅ Terminar Radio")
        self.btn_finalizar_camino.setStyleSheet("background-color:#5cb85c; color:white; font-weight:bold;")
        self.btn_finalizar_camino.clicked.connect(self._finalizar_camino)
        layout.addWidget(self.btn_finalizar_camino)

        return panel

    def _construir_panel_codatacion(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 5, 0, 0)

        # ── Fila de referencia + controles ───────────────────────────────
        fila_maestra = QHBoxLayout()
        fila_maestra.addWidget(QLabel("<b>Referencia:</b>"))
        self.btn_cargar_maestra = QPushButton("📂 .rwl / .txt")
        self.btn_cargar_maestra.setToolTip("Cargar serie de referencia en formato Tucson")
        self.btn_cargar_maestra.clicked.connect(self._cargar_serie_maestra)
        self.btn_cargar_wid = QPushButton("📂 .wid")
        self.btn_cargar_wid.setToolTip("Cargar cronología CooRecorder (.wid)")
        self.btn_cargar_wid.setStyleSheet(
            "background-color:#5bc0de; color:white; font-weight:bold;")
        self.btn_cargar_wid.clicked.connect(self._cargar_serie_wid)
        self.lbl_nombre_maestra = QLabel("Ninguna")
        self.lbl_nombre_maestra.setStyleSheet("color:#aaaaaa;")
        fila_maestra.addWidget(self.btn_cargar_maestra)
        fila_maestra.addWidget(self.btn_cargar_wid)
        fila_maestra.addWidget(self.lbl_nombre_maestra)

        # Botón para eliminar la maestra externa cargada
        self.btn_eliminar_maestra = QPushButton("🗑")
        self.btn_eliminar_maestra.setToolTip(
            "Eliminar la serie de referencia externa cargada")
        self.btn_eliminar_maestra.setFixedWidth(32)
        self.btn_eliminar_maestra.setStyleSheet(
            "QPushButton{background-color:#a94442; color:white; "
            "padding:2px; border-radius:3px; min-width:0px;}"
            "QPushButton:hover{background-color:#c9302c;}"
            "QPushButton:disabled{background-color:#555; color:#888;}"
        )
        self.btn_eliminar_maestra.clicked.connect(self._eliminar_serie_maestra)
        self.btn_eliminar_maestra.setEnabled(False)   # se habilita al cargar una
        fila_maestra.addWidget(self.btn_eliminar_maestra)

        # Combo selector de maestra propia
        fila_maestra.addSpacing(12)
        fila_maestra.addWidget(QLabel("Maestra:"))
        self.combo_maestra = QComboBox()
        self.combo_maestra.setToolTip(
            "Serie propia usada como referencia cuando no hay archivo externo cargado."
        )
        self.combo_maestra.setMinimumWidth(110)
        self.combo_maestra.currentIndexChanged.connect(self._actualizar_grafico_rt)
        fila_maestra.addWidget(self.combo_maestra)

        # Selector de ventana de correlación
        fila_maestra.addSpacing(12)
        fila_maestra.addWidget(QLabel("Ventana:"))
        self.spin_ventana_r = QSpinBox()
        self.spin_ventana_r.setRange(10, 100)
        self.spin_ventana_r.setSingleStep(5)
        self.spin_ventana_r.setValue(50)
        self.spin_ventana_r.setSuffix(" años")
        # Antes era 80px — muy angosto: el padding (4px) + borde (1px) + las
        # flechas de incremento (~16px) consumían casi todo el espacio y las
        # flechas quedaban recortadas/invisibles. 110px deja espacio cómodo.
        self.spin_ventana_r.setFixedWidth(110)
        self.spin_ventana_r.setToolTip(
            "Ventana máxima de correlación deslizante.\n"
            "Mientras la serie tenga menos años, la ventana crece desde 10.\n"
            "El solapamiento entre ventanas es siempre la mitad (estilo COFECHA)."
        )
        self.spin_ventana_r.valueChanged.connect(self._actualizar_grafico_rt)
        fila_maestra.addWidget(self.spin_ventana_r)

        fila_maestra.addStretch()

        # Label de r total
        self.lbl_r_total = QLabel("")
        self.lbl_r_total.setStyleSheet("font-size:12px; font-weight:bold;")
        fila_maestra.addWidget(self.lbl_r_total)

        self.btn_calcular_bi = QPushButton("🎨 Calcular BI")
        self.btn_calcular_bi.setToolTip(
            "Calcular Blue, Green, Red y Gray Intensity por anillo.\n"
            "El resultado queda en memoria — usa 💾 Guardar BI para exportar."
        )
        self.btn_calcular_bi.setStyleSheet(
            "background-color:#5bc0de; color:white; font-weight:bold;"
        )
        self.btn_calcular_bi.clicked.connect(self._calcular_bi)
        fila_maestra.addWidget(self.btn_calcular_bi)

        self.btn_graficar_bi = QPushButton("📊 Graficar BI")
        self.btn_graficar_bi.setToolTip(
            "Abrir ventana con el gráfico de BI/GI/RI/GrayI por anillo.\n"
            "Tiene zoom y paneo independientes del gráfico de series."
        )
        self.btn_graficar_bi.setEnabled(False)
        self.btn_graficar_bi.clicked.connect(self._graficar_bi_ventana)
        fila_maestra.addWidget(self.btn_graficar_bi)

        self.btn_guardar_bi = QPushButton("💾 Guardar BI")
        self.btn_guardar_bi.setToolTip(
            "Guardar los valores de intensidad calculados como archivos .rwl.\n"
            "Elige qué canales y métricas exportar."
        )
        self.btn_guardar_bi.setEnabled(False)
        self.btn_guardar_bi.clicked.connect(self._guardar_bi)
        fila_maestra.addWidget(self.btn_guardar_bi)

        self._btn_cfg_bi = QPushButton("⚙")
        self._btn_cfg_bi.setFixedWidth(28)
        self._btn_cfg_bi.setToolTip("Configurar banda de muestreo y canales")
        self._btn_cfg_bi.clicked.connect(self._configurar_bi)
        fila_maestra.addWidget(self._btn_cfg_bi)

        self.btn_mostrar_banda = QPushButton("👁 Ver área BI")
        self.btn_mostrar_banda.setCheckable(True)
        self.btn_mostrar_banda.setToolTip(
            "Mostrar/ocultar el área de muestreo BI sobre la imagen.\n"
            "Dibuja el rectángulo azul que cubre cada anillo (igual que CooRecorder)."
        )
        self.btn_mostrar_banda.toggled.connect(self._toggle_banda_bi)
        fila_maestra.addWidget(self.btn_mostrar_banda)

        layout.addLayout(fila_maestra)

        # ── Gráfico de series ─────────────────────────────────────────────
        self.grafico_unif = pg.PlotWidget()
        self.grafico_unif.showGrid(x=True, y=True, alpha=0.3)
        self.grafico_unif.setLabel("bottom", "Año")
        self.grafico_unif.setLabel("left",   "Ancho (mm)")
        self._legend = self.grafico_unif.addLegend(offset=(10, 10))
        self._plot_items: dict[str, object] = {}
        self._series_ocultas: set[str] = set()
        self._legend.scene().sigMouseClicked.connect(self._clic_leyenda)
        layout.addWidget(self.grafico_unif)

        # ── Crosshair interactivo: línea vertical + etiqueta de año ────────
        from pyqtgraph import InfiniteLine, TextItem, SignalProxy
        self._graf_vline = InfiniteLine(angle=90, movable=False,
                                         pen=pg.mkPen("#888", width=1,
                                                      style=Qt.PenStyle.DotLine))
        self._graf_vline.setZValue(60)
        self._graf_vline.setVisible(False)
        self.grafico_unif.addItem(self._graf_vline, ignoreBounds=True)

        self._graf_label = TextItem(anchor=(0.0, 1.0),
                                     fill=pg.mkBrush(0, 0, 0, 180),
                                     border=pg.mkPen("#888", width=1))
        self._graf_label.setZValue(61)
        self._graf_label.setVisible(False)
        self.grafico_unif.addItem(self._graf_label, ignoreBounds=True)

        # Marcador del punto más cercano
        self._graf_dot = self.grafico_unif.plot(
            [], [], pen=None, symbol='o', symbolSize=10,
            symbolBrush='#FFD700', symbolPen=pg.mkPen('#FFF', width=1.5))
        self._graf_dot.setZValue(62)

        # Proxy para throttle del mouse-move (cada 30ms)
        self._graf_proxy = SignalProxy(
            self.grafico_unif.scene().sigMouseMoved,
            rateLimit=33, slot=self._graf_mouse_moved)

        # Click en el gráfico → navegar a ese año en la imagen
        self.grafico_unif.scene().sigMouseClicked.connect(self._graf_mouse_clicked)

        # Item de resaltado en la imagen al hacer clic en el gráfico
        self._item_highlight_anio = None

        # ── Barra de correlaciones por ventana (texto enriquecido) ────────
        self.lbl_ventanas_r = QLabel("")
        self.lbl_ventanas_r.setWordWrap(True)
        self.lbl_ventanas_r.setStyleSheet(
            "font-size:11px; padding:3px 6px; "
            "background-color: rgba(0,0,0,30); border-radius:4px;"
        )
        self.lbl_ventanas_r.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.lbl_ventanas_r)

        # ── Atributos de estado ───────────────────────────────────────────
        self._colores_radios = [
            "#5cb85c", "#5bc0de", "#f0ad4e", "#d9534f",
            "#9b59b6", "#1abc9c", "#e67e22", "#3498db",
        ]

        return panel

    # -------------------------------------------------------------------------
    # Carga de imagen
    # -------------------------------------------------------------------------

    def _cargar_imagen(self, ruta: str):
        import time
        t0 = time.perf_counter()
 
        dpi_detectado = self._detectar_dpi(ruta)
        if dpi_detectado and dpi_detectado > 0:
            self.vista.pixeles_por_mm = dpi_detectado / MM_POR_PULGADA
            self._dpi_modo = "auto"
            msg_escala = f"Resolución detectada: {dpi_detectado:.0f} DPI."
            # Aviso informativo (no bloqueante) al usuario sobre el DPI
            # detectado y cómo cambiarlo si lo necesita. Se usa
            # QTimer.singleShot para que aparezca DESPUÉS de que la
            # imagen termine de cargar y la ventana esté visible.
            QTimer.singleShot(800, lambda d=dpi_detectado: QMessageBox.information(
                self, "Resolución detectada",
                f"Se detectaron automáticamente <b>{d:.0f} DPI</b> "
                f"desde los metadatos de la imagen.<br><br>"
                f"Si necesitas cambiar este valor, puedes hacerlo desde "
                f"<b>Herramientas → 📐 DPI</b>.",
            ))
        else:
            self.vista.pixeles_por_mm = PIXELES_POR_MM_DEFECTO
            self._dpi_modo = "default"
            msg_escala = f"DPI no detectado. Asumiendo {DPI_DEFECTO} DPI por defecto."
            QTimer.singleShot(500, lambda: QMessageBox.information(
                self, "Aviso de Resolución",
                f"No se detectaron DPI en los metadatos.\n\nSe usará {DPI_DEFECTO} DPI por defecto.\n\n"
                f"Si conoces el DPI real del escáner, puedes configurarlo "
                f"desde Herramientas → 📐 DPI.",
            ))
 
        print("[CARGA] DPI detectado en %.2fs", time.perf_counter() - t0)
        t1 = time.perf_counter()
 
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            img_cv = cv2.imread(ruta)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            self.label_estado.setText(f"⚠ Error al leer la imagen: {exc}")
            return
 
        if img_cv is None:
            QApplication.restoreOverrideCursor()
            self.label_estado.setText("⚠ Error: no se pudo abrir la imagen.")
            return
 
        h, w = img_cv.shape[:2]
        mp = (w * h) / 1e6
        print("[CARGA] cv2.imread %dx%d (%.1f MP) en %.2fs",
                    w, h, mp, time.perf_counter() - t1)
        t2 = time.perf_counter()
 
        img_rgb = cv2.cvtColor(img_cv, cv2.COLOR_BGR2RGB)
        QApplication.processEvents()
        print("[CARGA] cvtColor en %.2fs", time.perf_counter() - t2)
        t3 = time.perf_counter()
 
        q_img = QImage(img_rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
        del img_rgb
        QApplication.processEvents()
        print("[CARGA] QImage en %.2fs", time.perf_counter() - t3)
        t4 = time.perf_counter()
 
        self.item_imagen = QGraphicsPixmapItem(QPixmap.fromImage(q_img))
        self.item_imagen.setTransformationMode(
            Qt.TransformationMode.FastTransformation
        )
        del q_img
        self.escena.addItem(self.item_imagen)
        self.escena.setSceneRect(0, 0, w, h)
        print("[CARGA] Pixmap + escena en %.2fs", time.perf_counter() - t4)
        t5 = time.perf_counter()
 
        self._img_bgr_original = img_cv
        QApplication.restoreOverrideCursor()
 
        restaurado = self._cargar_estado()
        if restaurado:
            self.label_estado.setText(
                f"Progreso restaurado. Escala: {self.vista.pixeles_por_mm * MM_POR_PULGADA:.0f} DPI."
            )
        else:
            self.label_estado.setText(f"Imagen lista. {msg_escala}")
 
        print("[CARGA] _cargar_estado en %.2fs", time.perf_counter() - t5)
        print("[CARGA] TOTAL: %.2fs para %.1f MP", time.perf_counter() - t0, mp)
 
        QTimer.singleShot(100, self._ajustar_zoom_inicial)
 
    @staticmethod
    def _detectar_dpi(ruta: str) -> float | None:
        """Detecta DPI de la imagen desde metadatos. Robusto a múltiples formatos.

        Estrategia (en orden, devuelve el primer valor válido encontrado):
          1. PIL `img.info['dpi']` — Funciona para la mayoría de JPEG/PNG.
          2. EXIF moderno via `img.getexif()` — la nueva API de PIL (más
             confiable que `_getexif()` para JPGs de scanner).
          3. TIFF tags (XResolution=282 + ResolutionUnit=296).
          4. EXIF legacy via `_getexif()` (deprecated pero algunos archivos
             viejos lo necesitan).
          5. ExifTags por nombre (fallback) — algunos JPGs raros.

        Bug histórico: JPGs de scanner viejo o de scanners no estándar (que
        guardan DPI en EXIF en vez de JFIF) no eran detectados. Ahora se
        prueban múltiples métodos en cascada.

        Bug 2026-05 — DecompressionBombError: PIL rechaza por defecto
        imágenes con más de ~178 MP (protección anti-bomba). Esto hacía
        fallar la detección en escaneos grandes (>= 16000x16000 px a
        2400 DPI). Subimos el límite antes de abrir.
        """
        # Desactivar el límite anti-bomba de PIL.
        # Las imágenes de escáneres a 2400 DPI rutinariamente superan
        # 178 MP (un disco de 17 cm = 16000x16000 = 256 MP). Para SOLO
        # leer metadatos del header esto es seguro.
        try:
            from PIL import Image as _PIL_Image
            _PIL_Image.MAX_IMAGE_PIXELS = None
        except Exception:
            pass

        try:
            with Image.open(ruta) as img:
                # ── Método 1: info['dpi'] (JPEG con JFIF, PNG con pHYs) ─────
                dpi_info = img.info.get("dpi")
                if dpi_info:
                    try:
                        val = float(dpi_info[0])
                        if 50 < val < 30000:   # rango sano para descartar valores corruptos
                            return val
                    except (TypeError, ValueError, IndexError):
                        pass

                # ── Método 2: EXIF moderno (img.getexif()) ──────────────────
                # API recomendada por PIL >=9.0. Más confiable que _getexif()
                # para JPGs de scanners modernos.
                try:
                    exif_dict = img.getexif()
                    if exif_dict:
                        # 0x011A = XResolution
                        x_res = exif_dict.get(0x011A)
                        # 0x0128 = ResolutionUnit (2=pulgada, 3=cm)
                        res_unit = exif_dict.get(0x0128, 2)
                        if x_res is not None:
                            if isinstance(x_res, tuple) and len(x_res) >= 2 and x_res[1]:
                                val = float(x_res[0]) / float(x_res[1])
                            else:
                                val = float(x_res)
                            if res_unit == 3:  # cm → pulgada
                                val = val * 2.54
                            if 50 < val < 30000:
                                return val
                except Exception:
                    pass

                # ── Método 3: TIFF tags ─────────────────────────────────────
                if hasattr(img, 'tag_v2'):
                    try:
                        from PIL.TiffImagePlugin import IFDRational
                        x_res = img.tag_v2.get(282)
                        if x_res is not None:
                            if isinstance(x_res, IFDRational):
                                val = float(x_res)
                            elif isinstance(x_res, tuple) and len(x_res) >= 2:
                                val = float(x_res[0]) / float(x_res[1]) if x_res[1] else 0
                            else:
                                val = float(x_res)
                            if val > 0:
                                res_unit = img.tag_v2.get(296, 2)
                                if res_unit == 3:  # cm → pulgada
                                    val = val * 2.54
                                if 50 < val < 30000:
                                    return val
                    except Exception:
                        pass

                # ── Método 4: EXIF legacy (_getexif) ────────────────────────
                # Algunos archivos antiguos solo responden a este método.
                try:
                    exif = getattr(img, '_getexif', lambda: None)()
                    if exif and 0x011A in exif:
                        x_res = exif[0x011A]
                        res_unit = exif.get(0x0128, 2)
                        if isinstance(x_res, tuple) and len(x_res) >= 2 and x_res[1]:
                            val = float(x_res[0]) / float(x_res[1])
                        else:
                            val = float(x_res)
                        if res_unit == 3:
                            val = val * 2.54
                        if 50 < val < 30000:
                            return val
                except Exception:
                    pass

                # ── Método 5: ExifTags por nombre (último fallback) ─────────
                # Algunos JPGs raros (scanners viejos, software no estándar)
                # solo exponen los tags por nombre y no por código numérico.
                try:
                    from PIL.ExifTags import TAGS
                    exif_dict = img.getexif()
                    if exif_dict:
                        for tag, value in exif_dict.items():
                            tag_name = TAGS.get(tag, "")
                            if tag_name == "XResolution":
                                if isinstance(value, tuple) and len(value) >= 2 and value[1]:
                                    val = float(value[0]) / float(value[1])
                                else:
                                    val = float(value)
                                if 50 < val < 30000:
                                    return val
                except Exception:
                    pass

        except Exception as exc:
            logger.debug("No se pudo leer DPI de %s: %s", ruta, exc)
        return None

    def _ajustar_zoom_inicial(self):
        if self.item_imagen:
            self.vista.fitInView(self.escena.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
    # -------------------------------------------------------------------------
    # Visualización por banda (R, G, B, Gray)
    # -------------------------------------------------------------------------

    def _cambiar_banda(self, banda: str):
        """Cambia la banda activa y actualiza la imagen."""
        self._banda_actual = banda
        # Marcar el botón correcto
        for btn in self._grupo_banda.buttons():
            btn.setChecked(btn.text() == banda)
        # Cargar ajustes guardados para esta especie+banda
        self._cargar_ajustes_banda()
        self._aplicar_banda_actual()

    def _aplicar_banda_actual(self):
        """Reenderiza la imagen con la banda y ajustes de brillo/contraste actuales.

        Usa cv2.LUT (lookup table de 256 bytes) para aplicar brillo/contraste
        sin crear copias float32 de la imagen completa — crítico para imágenes
        de alta resolución (2400+ DPI) que pueden superar los 500 MB.
        """
        if self._img_bgr_original is None or self.item_imagen is None:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            img = self._img_bgr_original
            banda = self._banda_actual
            brillo = self._slider_brillo.value()
            contraste = self._slider_contraste.value() / 100.0

            # ── Construir LUT de 256 entradas (O(256), no O(pixeles)) ──
            lut = np.arange(256, dtype=np.float32)
            lut = contraste * lut + brillo
            lut = np.clip(lut, 0, 255).astype(np.uint8)

            sin_ajuste = (brillo == 0 and contraste == 1.0)

            # ── Extraer canal y aplicar ──
            if banda == "RGB":
                if sin_ajuste:
                    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                else:
                    rgb = cv2.LUT(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), lut)
            else:
                if banda == "R":
                    gray = img[:, :, 2]
                elif banda == "G":
                    gray = img[:, :, 1]
                elif banda == "B":
                    gray = img[:, :, 0]
                else:  # Gris
                    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

                if not sin_ajuste:
                    gray = cv2.LUT(gray, lut)
                rgb = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
                del gray

            QApplication.processEvents()

            h, w = rgb.shape[:2]
            rgb = np.ascontiguousarray(rgb)
            q_img = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888)
            self.item_imagen.setPixmap(QPixmap.fromImage(q_img.copy()))
            del rgb
        finally:
            QApplication.restoreOverrideCursor()

    def _guardar_ajustes_banda(self):
        """Guarda brillo/contraste de la banda activa en el perfil de especie."""
        try:
            especie = self._especie_activa
            settings = QSettings("MoiCedrus", "DPI")
            key = f"banda/{especie}/{self._banda_actual}"
            settings.setValue(f"{key}/brillo", self._slider_brillo.value())
            settings.setValue(f"{key}/contraste", self._slider_contraste.value())
        except Exception:
            pass

    def _cargar_ajustes_banda(self):
        """Restaura brillo/contraste guardados para la especie+banda actual."""
        try:
            especie = self._especie_activa
            settings = QSettings("MoiCedrus", "DPI")
            key = f"banda/{especie}/{self._banda_actual}"
            brillo = settings.value(f"{key}/brillo", 0, type=int)
            contraste = settings.value(f"{key}/contraste", 100, type=int)
            self._slider_brillo.blockSignals(True)
            self._slider_contraste.blockSignals(True)
            self._slider_brillo.setValue(brillo)
            self._slider_contraste.setValue(contraste)
            self._slider_brillo.blockSignals(False)
            self._slider_contraste.blockSignals(False)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Modos de interacción
    # -------------------------------------------------------------------------

    def set_modo(self, modo: int):
        # Caso especial: si estamos en MODO_SELECCIONAR con selección
        # activa y el usuario hace clic en alguno de los botones de
        # acción de la barra (Borrar / Salto / Quiebre), se ejecuta la
        # operación en BULK sobre la selección en vez de cambiar el modo
        # — exactamente lo que pidió el usuario para evitar duplicar
        # botones en el panel flotante.
        if (self.vista.modo_actual == MODO_SELECCIONAR
                and self.vista._puntos_seleccionados):
            if modo == MODO_BORRAR:
                self.vista._ejecutar_borrar_seleccionados()
                modo = MODO_MEDIR  # tras la acción volver a MEDIR
            elif modo == MODO_SALTO:
                self.vista._ejecutar_convertir_a_salto()
                modo = MODO_MEDIR
            elif modo == MODO_ESPACIO:
                self.vista._ejecutar_convertir_a_quiebre()
                modo = MODO_MEDIR

        btn = self._botones_modo.get(modo)
        if btn:
            btn.setChecked(True)
        self.vista.cambiar_modo(modo)
        self.vista.setFocus()

    def _deshacer(self):
        self.vista.deshacer_ultimo()

    def _rehacer(self):
        self.vista.rehacer_ultimo()

    # -------------------------------------------------------------------------
    # Auto-detección de anillos por especie
    # -------------------------------------------------------------------------

    def _refrescar_combo_especie(self):
        self.combo_especie.blockSignals(True)
        self.combo_especie.clear()
        self.combo_especie.addItems(list(self._perfiles_especie.keys()))
        if self._especie_activa in self._perfiles_especie:
            self.combo_especie.setCurrentText(self._especie_activa)
        self.combo_especie.blockSignals(False)

    def _al_cambiar_especie(self, nombre: str):
        if nombre in self._perfiles_especie:
            self._especie_activa = nombre

    def _configurar_especie(self):
        """Abre el diálogo de configuración para la especie activa."""
        perfil = self._perfiles_especie.get(self._especie_activa, {})
        dlg = DialogoEspecie(self._especie_activa, perfil, self)
        if dlg.exec():
            self._perfiles_especie[self._especie_activa] = dlg.obtener_perfil()
            guardar_perfiles(self._perfiles_especie)

    def _nueva_especie(self):
        """Crea un perfil de especie nuevo a partir del nombre que ingrese el usuario."""
        from modulo_autodeteccion import PERFIL_DEFECTO
        nombre, ok = QInputDialog.getText(self, "Nueva especie", "Nombre de la especie:")
        if not ok or not nombre.strip():
            return
        nombre = nombre.strip()
        if nombre in self._perfiles_especie:
            QMessageBox.warning(self, "Especie existente",
                                f'Ya existe un perfil para "{nombre}".')
            return
        self._perfiles_especie[nombre] = PERFIL_DEFECTO.copy()
        guardar_perfiles(self._perfiles_especie)
        self._especie_activa = nombre
        self._refrescar_combo_especie()
        # Abrir configuración inmediatamente
        self._configurar_especie()

    def _toggle_autodetect(self):
        """Activa/desactiva el modo auto-detección desde el botón."""
        if self.btn_autodetect.isChecked():
            # Cargar imagen BGR si aún no está cacheada
            img_bgr = cv2.imread(self.ruta_imagen_actual, cv2.IMREAD_COLOR)
            if img_bgr is None:
                QMessageBox.critical(self, "Error",
                                     "No se pudo leer la imagen para la detección.")
                self.btn_autodetect.setChecked(False)
                return
            self.vista.iniciar_autodetect(img_bgr)
            self.label_estado.setText(
                "🔍 Auto-detección: clic en la MÉDULA, luego en la CORTEZA."
            )
        else:
            self.vista.cambiar_modo(MODO_MEDIR)
            self._botones_modo[MODO_MEDIR].setChecked(True)
            self.label_estado.setText("Modo añadir activado.")

    def _ejecutar_autodeteccion(
        self,
        p1: tuple[float, float],
        p2: tuple[float, float],
        imagen_bgr: np.ndarray,
        idx_ancla: int | None = None,
    ):
        """
        Llamado por VistaInteractiva tras los 2 clics de auto-detección.
        Extrae el perfil entre p1 y p2, muestra preview y coloca los puntos.

        idx_ancla: índice del punto existente desde el que se continúa,
                   o None si la detección es sobre una zona vacía.
        """
        perfil = self._perfiles_especie.get(self._especie_activa, {})
        ppm    = self.vista.pixeles_por_mm

        dist, intens = extraer_perfil(
            imagen_bgr, p1, p2,
            canal=perfil.get("canal", "gray"),
            banda_px=perfil.get("banda_px", 5),
        )

        if len(dist) == 0:
            QMessageBox.warning(self, "Sin datos",
                                "No se pudo extraer el perfil de intensidad.")
            return

        dlg = DialogoPreviewDeteccion(dist, intens, perfil, ppm, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        banda_nueva = dlg.obtener_banda_final()
        if banda_nueva != perfil.get("banda_px", 5):
            perfil["banda_px"] = banda_nueva
            self._perfiles_especie[self._especie_activa] = perfil
            guardar_perfiles(self._perfiles_especie)
            dist, intens = extraer_perfil(
                imagen_bgr, p1, p2,
                canal=perfil.get("canal", "gray"),
                banda_px=banda_nueva,
            )

        perfil["prominencia"] = dlg.obtener_prominencia_final()

        posiciones_px = detectar_limites(dist, intens, perfil)
        if len(posiciones_px) == 0:
            QMessageBox.information(self, "Sin detecciones",
                                    "No se detectaron límites de anillo.\n"
                                    "Prueba a reducir la prominencia mínima o\n"
                                    "verificar el canal y la polaridad de la especie.")
            return

        coordenadas = posiciones_a_coordenadas(posiciones_px, p1, p2)

        # Si hay ancla, verificar si hay puntos en el tramo y preguntar qué hacer
        reemplazar_tramo = False
        if idx_ancla is not None and len(self.vista.coordenadas_anillos) > 1:
            n_total = len(self.vista.coordenadas_anillos)
            # Hay puntos más allá del ancla?
            tiene_vecinos = (idx_ancla > 0 or idx_ancla < n_total - 1)
            if tiene_vecinos:
                resp = QMessageBox.question(
                    self,
                    "Puntos existentes en el tramo",
                    "Ya hay puntos entre el ancla y el extremo detectado.\n\n"
                    "¿Qué desea hacer?\n\n"
                    "  • Sí  → Reemplazar los puntos existentes en ese tramo\n"
                    "  • No  → Insertar los nuevos sin borrar los existentes",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                reemplazar_tramo = resp == QMessageBox.StandardButton.Yes

        self.vista.colocar_puntos_autodetectados(
            coordenadas,
            idx_ancla=idx_ancla,
            reemplazar_tramo=reemplazar_tramo,
        )
        n = len(coordenadas)
        self.label_estado.setText(
            f"✅ Auto-detección: {n} anillos colocados"
            + (f" desde punto #{idx_ancla}" if idx_ancla is not None else "")
            + ". Usa las herramientas para corregir."
        )

    # -------------------------------------------------------------------------
    # Gestión de años (médula / corteza)
    # -------------------------------------------------------------------------

    def _recalcular_anios(self, ancla: str):
        """
        Recalcula los años de todas las mediciones a partir del ancla dada.
        ancla: 'medula' o 'corteza'
        """
        if not self.datos_medidos:
            return
        n = len(self.datos_medidos)
        med_a_cor = self.combo_dir.currentIndex() == 0

        self.spin_medula.blockSignals(True)
        self.spin_corteza.blockSignals(True)

        if ancla == "medula":
            a_med = self.spin_medula.value()
            for i in range(n):
                anio = (a_med + i) if med_a_cor else (a_med + n - 1 - i)
                self.datos_medidos[i]["anio"] = anio
            anio_corteza = self.datos_medidos[-1]["anio"]
            self.spin_corteza.setValue(anio_corteza)
        else:  # ancla == "corteza"
            a_cor = self.spin_corteza.value()
            for i in range(n):
                anio = (a_cor - n + 1 + i) if med_a_cor else (a_cor - i)
                self.datos_medidos[i]["anio"] = anio
            anio_medula = self.datos_medidos[0]["anio"] if med_a_cor else self.datos_medidos[-1]["anio"]
            self.spin_medula.setValue(anio_medula)

        self.spin_medula.blockSignals(False)
        self.spin_corteza.blockSignals(False)
        self._guardar_estado()
        self.metadatos_cambiados.emit()
        self._actualizar_grafico_rt()
        self.vista.redibujar_todo()

    def _aplicar_anio_medula(self):
        self._recalcular_anios("medula")

    def _aplicar_anio_corteza(self):
        self._recalcular_anios("corteza")

    def _al_cambiar_direccion(self):
        self._recalcular_anios("medula")

    # -------------------------------------------------------------------------
    # Sincronización de mediciones desde el lienzo
    # -------------------------------------------------------------------------

    def _sincronizar_mediciones(self, lista_anchos: list[float]):
        self.spin_medula.blockSignals(True)
        self.spin_corteza.blockSignals(True)

        n = len(lista_anchos)
        # Ajusta la lista de datos al tamaño actual
        while len(self.datos_medidos) < n:
            self.datos_medidos.append({"anio": 0, "ancho_mm": 0.0})
        while len(self.datos_medidos) > n:
            self.datos_medidos.pop()

        if n == 0:
            self.label_estado.setText("Listo para el primer anillo.")
        else:
            med_a_cor = self.combo_dir.currentIndex() == 0
            a_med = self.spin_medula.value()
            a_cor = self.spin_corteza.value()
            for i in range(n):
                anio = (a_med + i) if med_a_cor else (a_cor - i)
                self.datos_medidos[i]["anio"] = anio
                self.datos_medidos[i]["ancho_mm"] = lista_anchos[i]

            anio_extremo = self.datos_medidos[-1]["anio"]
            if med_a_cor:
                self.spin_corteza.setValue(anio_extremo)
            else:
                self.spin_medula.setValue(anio_extremo)

            ultimo = self.datos_medidos[-1]
            self.label_estado.setText(
                f"Último: {ultimo['ancho_mm']:.3f} mm (año {ultimo['anio']}) | Total: {n}"
            )

        self.spin_medula.blockSignals(False)
        self.spin_corteza.blockSignals(False)
        self.metadatos_cambiados.emit()
        self._actualizar_grafico_rt()
        self.vista.redibujar_todo()

    # -------------------------------------------------------------------------
    # Co-datación en vivo
    # -------------------------------------------------------------------------

    def _cargar_serie_maestra(self):
        carpeta = _ultima_carpeta()
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Cargar Serie de Referencia", carpeta,
            "Archivos Dendro (*.rwl *.txt *.cat *.cmp *.pos);;Todos (*.*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not ruta:
            return
        try:
            # AQUÍ ESTÁ EL CAMBIO PRINCIPAL
            self.serie_maestra, self.nombre_maestra = leer_dendro_auto(ruta)

            self.lbl_nombre_maestra.setText(self.nombre_maestra)
            self.lbl_nombre_maestra.setStyleSheet("color:#5cb85c;")
            self.btn_eliminar_maestra.setEnabled(True)
            self._actualizar_grafico_rt()
            _ultima_carpeta(ruta)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Error", f"No se pudo leer la referencia:\n{exc}")
            
    def _cargar_serie_wid(self):
        """Carga una cronología CooRecorder (.wid) como serie de referencia."""
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Cargar Cronología CooRecorder", _ultima_carpeta(),
            "CooRecorder WID (*.wid);;Todos (*.*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not ruta:
            return
        try:
            self.serie_maestra, self.nombre_maestra = leer_wid(ruta)
            self.lbl_nombre_maestra.setText(f"🌲 {self.nombre_maestra}")
            self.lbl_nombre_maestra.setStyleSheet("color:#5bc0de; font-weight:bold;")
            self.btn_eliminar_maestra.setEnabled(True)
            self._actualizar_grafico_rt()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Error", f"No se pudo leer el archivo .wid:\n{exc}")

    def _eliminar_serie_maestra(self):
        """Elimina la serie maestra externa cargada.

        Útil cuando el usuario quiere volver a comparar solo entre radios
        internos sin la referencia externa, o cambiar a otra de cero.
        """
        if self.serie_maestra is None:
            return
        self.serie_maestra = None
        self.nombre_maestra = "Ninguna"
        self.lbl_nombre_maestra.setText("Ninguna")
        self.lbl_nombre_maestra.setStyleSheet("color:#aaaaaa;")
        self.btn_eliminar_maestra.setEnabled(False)
        # Limpiar items del gráfico que referenciaban la externa
        self._plot_items = {k: v for k, v in getattr(self, "_plot_items", {}).items()
                            if not k.startswith("📂") and not k.startswith("★")}
        self._actualizar_grafico_rt()

    # -------------------------------------------------------------------------
    # Interacción con el gráfico: crosshair + navegación por clic
    # -------------------------------------------------------------------------

    def _graf_mouse_moved(self, evt):
        """Muestra crosshair vertical con año y valores al mover el mouse."""
        pos = evt[0]
        vb = self.grafico_unif.getViewBox()
        if not self.grafico_unif.sceneBoundingRect().contains(pos):
            self._graf_vline.setVisible(False)
            self._graf_label.setVisible(False)
            self._graf_dot.setData([], [])
            return

        mouse_point = vb.mapSceneToView(pos)
        anio = round(mouse_point.x())

        # Buscar valores de cada serie visible en ese año
        lineas = []
        dot_x, dot_y = [], []
        for nombre, item in self._plot_items.items():
            if not item.isVisible():
                continue
            xdata, ydata = item.getData()
            if xdata is None or len(xdata) == 0:
                continue
            # Buscar índice más cercano al año
            idx = int(np.argmin(np.abs(np.array(xdata) - anio)))
            if abs(xdata[idx] - anio) < 1.0:
                val = ydata[idx]
                # Quitar ★ y (maestra) del nombre para mostrar más limpio
                nombre_corto = nombre.replace("★ ", "").replace(" (maestra)", "")
                lineas.append(f"{nombre_corto}: {val:.3f}")
                dot_x.append(xdata[idx])
                dot_y.append(val)

        if not lineas:
            self._graf_vline.setVisible(False)
            self._graf_label.setVisible(False)
            self._graf_dot.setData([], [])
            return

        # Posicionar crosshair
        self._graf_vline.setPos(anio)
        self._graf_vline.setVisible(True)

        # Texto
        texto_html = (
            f"<span style='color:white; font-size:9pt; font-family:Arial;'>"
            f"<b>{anio}</b><br/>"
            + "<br/>".join(lineas)
            + "</span>"
        )
        self._graf_label.setHtml(texto_html)
        # Posicionar arriba a la derecha de la línea
        [[_xmin, _xmax], [_ymin, y_max]] = vb.viewRange()
        self._graf_label.setPos(anio + (_xmax - _xmin) * 0.01, y_max)
        self._graf_label.setVisible(True)

        # Puntos amarillos
        self._graf_dot.setData(dot_x, dot_y)

    def _graf_mouse_clicked(self, ev):
        """Al hacer clic en el gráfico, centra la imagen en el año clickeado."""
        # Ignorar si ya fue aceptado (e.g. por clic en leyenda)
        if ev.isAccepted():
            return
        # Solo clic izquierdo, y solo si el clic es sobre el área del plot
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        # No navegar si el clic fue sobre la leyenda
        pos = ev.scenePos()
        if not self.grafico_unif.getViewBox().sceneBoundingRect().contains(pos):
            return

        vb = self.grafico_unif.getViewBox()
        mouse_point = vb.mapSceneToView(pos)
        anio = round(mouse_point.x())

        # Obtener mapa año→coordenada de la imagen
        mapa = self.vista.mapa_anio_coordenada()
        if anio not in mapa:
            # Buscar el año más cercano
            if not mapa:
                return
            anio = min(mapa.keys(), key=lambda a: abs(a - anio))

        coord = mapa[anio]
        # Centrar la vista en esa coordenada manteniendo el zoom actual
        self.vista.centerOn(coord[0], coord[1])

        # Resaltar el punto con un círculo pulsante
        self._resaltar_anio_en_imagen(coord, anio)
        ev.accept()

    def _resaltar_anio_en_imagen(self, coord: tuple, anio: int):
        """Dibuja un círculo brillante temporal sobre el punto del año en la imagen."""
        # Limpiar resaltado anterior
        if self._item_highlight_anio is not None:
            try:
                self.vista.scene().removeItem(self._item_highlight_anio)
            except Exception:
                pass
            self._item_highlight_anio = None

        x, y = coord
        r = 18 * self.vista._escala_vista()
        pen = QPen(QColor("#00BFFF"))
        pen.setCosmetic(True)
        pen.setWidthF(3.5)
        brush = QBrush(QColor(0, 191, 255, 50))
        self._item_highlight_anio = self.vista.scene().addEllipse(
            x - r, y - r, 2 * r, 2 * r, pen, brush)
        self._item_highlight_anio.setZValue(80)

        # Auto-eliminar después de 2 segundos
        QTimer.singleShot(2000, self._limpiar_highlight_anio)

    def _limpiar_highlight_anio(self):
        if self._item_highlight_anio is not None:
            try:
                self.vista.scene().removeItem(self._item_highlight_anio)
            except Exception:
                pass
            self._item_highlight_anio = None

    def _clic_leyenda(self, ev):
        """Toggle visibilidad al hacer clic en un item de la leyenda."""
        pos = ev.scenePos()
        for nombre, item in self._plot_items.items():
            sample, label = None, None
            for s, l in self._legend.items:
                # Comparar por referencia al item de datos, no por nombre
                if s.item is item:
                    sample, label = s, l
                    break
            if sample is None:
                # Fallback: buscar por texto (nombres sin ★)
                for s, l in self._legend.items:
                    if l.text == nombre:
                        sample, label = s, l
                        break
            if sample is None:
                continue
            sr = sample.mapRectToScene(sample.boundingRect())
            lr = label.mapRectToScene(label.boundingRect())
            if sr.contains(pos) or lr.contains(pos):
                if nombre in self._series_ocultas:
                    self._series_ocultas.discard(nombre)
                    item.setVisible(True)
                    label.setAttr("color", "FFFFFF")
                else:
                    self._series_ocultas.add(nombre)
                    item.setVisible(False)
                    label.setAttr("color", "666666")
                ev.accept()
                return

    def _normalizar(self, valores: list) -> list:
        """Z-score. Si std≈0 devuelve ceros."""
        arr = np.array(valores, dtype=float)
        std = arr.std()
        if std < 1e-9:
            return [0.0] * len(valores)
        return ((arr - arr.mean()) / std).tolist()

    def _r_global(self, anios_a, anchos_a, anios_b, anchos_b):
        """r de Pearson sobre diferencias log en el overlap completo."""
        if len(anios_a) < 5 or len(anios_b) < 5:
            return None
        sa = pd.Series(anchos_a, index=anios_a).clip(lower=0.01)
        sb = pd.Series(anchos_b, index=anios_b).clip(lower=0.01)
        da = np.log(sa).diff().dropna()
        db = np.log(sb).diff().dropna()
        idx = da.index.intersection(db.index)
        if len(idx) < 5:
            return None
        r = np.corrcoef(da[idx].values, db[idx].values)[0, 1]
        return None if np.isnan(r) else float(r)

    def _r_deslizante(self, anios_a, anchos_a, anios_b, anchos_b, ventana: int):
        """
        Correlación deslizante estilo COFECHA.

        Calcula r de Pearson (sobre diff-log) en cada ventana de `ventana` años,
        avanzando de la mitad de la ventana en la mitad (solapamiento 50%).
        Cuando la serie tiene menos de `ventana` años, usa todos los años
        disponibles como única ventana (ventana creciente al inicio).

        Devuelve dos listas paralelas: (años_centro, r_vals).
        Los años_centro son el año central de cada ventana.
        """
        MIN_VENTANA = 10
        if len(anios_a) < MIN_VENTANA or len(anios_b) < MIN_VENTANA:
            return [], []

        sa = pd.Series(anchos_a, index=anios_a).clip(lower=0.01)
        sb = pd.Series(anchos_b, index=anios_b).clip(lower=0.01)
        da = np.log(sa).diff().dropna()
        db = np.log(sb).diff().dropna()
        overlap = sorted(da.index.intersection(db.index))
        n_ov = len(overlap)
        if n_ov < MIN_VENTANA:
            return [], []

        años_centro, r_vals = [], []
        paso = max(1, ventana // 2)   # solapamiento = mitad de la ventana

        if n_ov <= ventana:
            # Serie corta: una sola ventana con todo el solapamiento disponible
            a_seg = da.loc[overlap].values
            b_seg = db.loc[overlap].values
            if np.std(a_seg) > 1e-9 and np.std(b_seg) > 1e-9:
                r = float(np.corrcoef(a_seg, b_seg)[0, 1])
                if not np.isnan(r):
                    centro = overlap[n_ov // 2]
                    años_centro.append(centro)
                    r_vals.append(r)
        else:
            # Ventana deslizante con paso = ventana/2
            k = 0
            while k + ventana <= n_ov:
                seg = overlap[k: k + ventana]
                a_seg = da.loc[seg].values
                b_seg = db.loc[seg].values
                if np.std(a_seg) > 1e-9 and np.std(b_seg) > 1e-9:
                    r = float(np.corrcoef(a_seg, b_seg)[0, 1])
                    if not np.isnan(r):
                        centro = seg[ventana // 2]
                        años_centro.append(centro)
                        r_vals.append(r)
                k += paso

        return años_centro, r_vals

    def _actualizar_combo_maestra(self, entradas, nombre_maestra_ext):
        """Actualiza el combo selector de maestra con las series disponibles."""
        previo = self.combo_maestra.currentText()
        self.combo_maestra.blockSignals(True)
        self.combo_maestra.clear()
        if nombre_maestra_ext:
            self.combo_maestra.addItem(f"↑ Ext: {nombre_maestra_ext}")
        for nombre, *_ in entradas:
            self.combo_maestra.addItem(nombre)
        idx = self.combo_maestra.findText(previo)
        if idx >= 0:
            self.combo_maestra.setCurrentIndex(idx)
        self.combo_maestra.blockSignals(False)

    def _dibujar_segmentos_correlacion(self, pw, entradas, maestra):
        """
        Dibuja anotaciones de correlación por segmento directamente
        sobre el gráfico de series.

        Lógica de segmentación (igual a la descrita por el usuario):
          - Ventana W años → segmentos de W/2 años cada uno, sin solapamiento.
          - Ejemplo W=10: segmentos [0-5), [5-10), [10-15)...
          - Ejemplo W=20: segmentos [0-10), [10-20), [20-30)...
          - Cada segmento muestra su r como texto coloreado en el centro,
            sobre una barra de fondo semitransparente.
          - El primer segmento puede ser más pequeño si la serie tiene
            menos de W/2 años disponibles (ventana creciente al inicio).
        """
        from pyqtgraph import InfiniteLine, LinearRegionItem, TextItem

        if maestra is None or not entradas:
            return

        self._textos_corr_pendientes = []

        ventana = self.spin_ventana_r.value()
        paso    = max(5, ventana // 2)   # cada segmento = mitad de la ventana
        nombre_m, anios_m, anchos_m = maestra

        def _col_rgba(r):
            if r >= 0.50:  return (46,  204, 113, 55)   # verde fuerte
            if r >= 0.35:  return (92,  184,  92, 55)   # verde
            if r >= 0.15:  return (240, 173,  78, 55)   # naranja
            return (217,  83,  79, 55)                   # rojo

        def _col_hex(r):
            if r >= 0.50:  return "#2ecc71"
            if r >= 0.35:  return "#5cb85c"
            if r >= 0.15:  return "#f0ad4e"
            return "#d9534f"

        # Preparar diff-log de la maestra
        sb_m = pd.Series(anchos_m, index=anios_m).clip(lower=0.01)
        db_m = np.log(sb_m).diff().dropna()

        for nombre, anios, anchos, color, *_ in entradas:
            sa = pd.Series(anchos, index=anios).clip(lower=0.01)
            da = np.log(sa).diff().dropna()
            overlap = sorted(da.index.intersection(db_m.index))
            if len(overlap) < 5:
                continue

            yr_min = overlap[0]
            yr_max = overlap[-1]

            # Generar segmentos de paso años comenzando desde yr_min
            seg_inicio = yr_min
            while seg_inicio < yr_max:
                seg_fin = seg_inicio + paso
                seg = [y for y in overlap if seg_inicio <= y < seg_fin]
                if len(seg) < 3:
                    seg_inicio += paso
                    continue

                a_seg = da.loc[seg].values
                b_seg = db_m.loc[seg].values
                if np.std(a_seg) < 1e-9 or np.std(b_seg) < 1e-9:
                    seg_inicio += paso
                    continue

                r = float(np.corrcoef(a_seg, b_seg)[0, 1])
                if np.isnan(r):
                    seg_inicio += paso
                    continue

                # Fondo semitransparente para el segmento
                rgba = _col_rgba(r)
                region = LinearRegionItem(
                    values=[seg_inicio, seg_fin],
                    orientation='vertical',
                    brush=pg.mkBrush(*rgba),
                    pen=pg.mkPen(None),
                    movable=False,
                )
                region.setZValue(-10)
                pw.addItem(region)

                # Texto de correlación centrado en el segmento
                centro_x = (seg_inicio + seg_fin) / 2.0
                txt = TextItem(
                    anchor=(0.5, 0.0),
                    fill=pg.mkBrush(0, 0, 0, 160),
                    border=pg.mkPen(_col_hex(r), width=1),
                )
                txt.setHtml(
                    f"<span style='color:{_col_hex(r)}; font-size:10pt; "
                    f"font-weight:bold; font-family:Arial;'>"
                    f" r={r:+.2f} </span>"
                )
                txt.setZValue(50)
                pw.addItem(txt)
                # Posicionar en la parte alta del rango visible
                self._textos_corr_pendientes.append((txt, centro_x))

                seg_inicio += paso

    def _actualizar_correlaciones_texto(self, entradas, maestra):
        """
        Actualiza la barra de texto de correlaciones debajo del gráfico.

        Muestra para cada serie vs maestra:
          - Símbolo de calidad  ●●● / ●●○ / ●○○ / ○○○
          - r total coloreado
          - r por ventanas crecientes (10, 20, 30... hasta la ventana elegida)
          - N años de solapamiento
        """
        if maestra is None or not entradas:
            self.lbl_r_total.setText("")
            self.lbl_ventanas_r.setText("")
            return

        ventana_max = self.spin_ventana_r.value()
        nombre_m, anios_m, anchos_m = maestra

        def _col(r):
            if r is None:    return "#666666"
            if r >= 0.50:    return "#2ecc71"
            if r >= 0.35:    return "#5cb85c"
            if r >= 0.15:    return "#f0ad4e"
            return "#d9534f"

        def _simbolo(r):
            if r is None:    return "○○○"
            if r >= 0.50:    return "●●●"
            if r >= 0.35:    return "●●○"
            if r >= 0.15:    return "●○○"
            return "○○○"

        # Calcular ventanas a mostrar: 10, 20, 30... hasta ventana_max
        ventanas = list(range(10, ventana_max + 1, 10))
        if not ventanas or ventanas[-1] != ventana_max:
            ventanas.append(ventana_max)
        # Máximo 6 ventanas para no saturar el texto
        if len(ventanas) > 6:
            paso = len(ventanas) // 6
            ventanas = ventanas[::paso]
            if ventanas[-1] != ventana_max:
                ventanas.append(ventana_max)

        # Línea superior: r total por serie (label fila de referencia)
        partes_total = []
        for nombre, anios, anchos, color, *_ in entradas:
            r_tot = self._r_global(anios, anchos, anios_m, anchos_m)
            c = _col(r_tot)
            sym = _simbolo(r_tot)
            val = f"{r_tot:+.2f}" if r_tot is not None else "—"
            # Overlap
            sa = pd.Series(anchos, index=anios)
            sb = pd.Series(anchos_m, index=anios_m)
            n_ov = len(sa.index.intersection(sb.index))
            partes_total.append(
                f"<span style='color:{color}'><b>{nombre}</b></span>"
                f"&nbsp;<span style='color:{c}'>{sym}&nbsp;r={val}</span>"
                f"&nbsp;<span style='color:#666666'>({n_ov} años)</span>"
            )

        self.lbl_r_total.setText("&nbsp;&nbsp;&nbsp;".join(partes_total))

        # Línea inferior: desglose por ventana
        if not partes_total:
            self.lbl_ventanas_r.setText("")
            return

        lineas_ventana = []
        for nombre, anios, anchos, color, *_ in entradas:
            celdas = [f"<span style='color:{color}'><b>{nombre}</b></span>&nbsp;"]
            for v in ventanas:
                res = self._r_deslizante(anios, anchos, anios_m, anchos_m, v)
                if res[0]:
                    # Media de todos los r de esa ventana
                    r_v = float(np.mean(res[1]))
                    c = _col(r_v)
                    celdas.append(
                        f"<span style='color:#888888'>{v}a:</span>"
                        f"<span style='color:{c}'>&nbsp;{r_v:+.2f}</span>"
                    )
                else:
                    celdas.append(
                        f"<span style='color:#888888'>{v}a:&nbsp;—</span>"
                    )
            lineas_ventana.append("&nbsp;&nbsp;".join(celdas))

        self.lbl_ventanas_r.setText("&nbsp;&nbsp;|&nbsp;&nbsp;".join(lineas_ventana))

    def _actualizar_grafico_unificado(self):
        """
        Gráfico de series (arriba) + correlación deslizante estilo COFECHA (abajo).
        La maestra puede ser externa (.rwl/.wid) o una serie propia elegida en el combo.
        """
        pw = self.grafico_unif
        pw.clear()
        self._legend.clear()
        self._plot_items.clear()
        self._textos_corr_pendientes = []

        def _color_r(r):
            return "#5cb85c" if r >= 0.4 else "#f0ad4e" if r >= 0.0 else "#d9534f"

        # ── Recopilar series propias ──────────────────────────────────────
        # IMPORTANTE: recalcular anchos desde coordenadas+es_salto en tiempo real
        # para evitar usar datos_medidos obsoletos (que pueden haber sido guardados
        # con bugs de EW/LW anteriores). La función _anchos_desde_coords usa la
        # misma lógica que recalcular_todas_las_distancias.
        def _anchos_desde_coords(coords, saltos, anio_medula, anio_corteza,
                                  med_a_cor, ppm):
            """Recalcula anchos y años desde coordenadas (misma lógica que recalcular)."""
            n = len(coords)
            distancias = []
            acum = 0.0
            for i in range(1, n):
                val = saltos[i]
                d = math.hypot(coords[i][0]-coords[i-1][0],
                               coords[i][1]-coords[i-1][1]) / ppm
                if val is True:
                    if acum > 0: distancias.append(acum)
                    acum = 0.0
                elif _es_espacio(val):
                    pass
                elif _es_ewlw(val) or _es_quiebre_ini(val):
                    acum += d          # EW o inicio quiebre: suma pero no cierra
                else:                  # False: suma y cierra
                    acum += d
                    distancias.append(acum)
                    acum = 0.0
            if acum > 0:
                distancias.append(acum)
            # Asignar años
            anios, anchos = [], []
            for k, ancho in enumerate(distancias):
                anio = (anio_medula + k) if med_a_cor else (anio_corteza - k)
                anios.append(anio)
                anchos.append(ancho)
            return anios, anchos

        entradas = []
        for i, (nombre, datos) in enumerate(self.caminos_guardados.items()):
            dm = datos.get("datos_medidos", [])
            if not dm:
                continue
            color = self._colores_radios[i % len(self._colores_radios)]
            # Recalcular desde coordenadas si están disponibles
            coords_g = datos.get("coords", [])
            saltos_g = datos.get("es_salto", [])
            if coords_g and saltos_g and len(coords_g) >= 2:
                anio_ini = dm[0]["anio"] if dm else 0
                anio_fin = dm[-1]["anio"] if dm else 0
                med_a_cor = anio_fin >= anio_ini
                ppm = self.vista.pixeles_por_mm
                anios_g, anchos_g = _anchos_desde_coords(
                    [tuple(c) for c in coords_g], saltos_g,
                    anio_ini, anio_ini, med_a_cor, ppm)
            else:
                anios_g  = [d["anio"]     for d in dm]
                anchos_g = [d["ancho_mm"] for d in dm]
            entradas.append((nombre, anios_g, anchos_g,
                             color, Qt.PenStyle.SolidLine))

        if self.datos_medidos:
            nombre_act = self.input_codigo.text().strip() or "En medición"
            color_act  = self._colores_radios[
                len(self.caminos_guardados) % len(self._colores_radios)]
            entradas.append((nombre_act,
                             [d["anio"]     for d in self.datos_medidos],
                             [d["ancho_mm"] for d in self.datos_medidos],
                             color_act, Qt.PenStyle.SolidLine))

        # ── Determinar maestra ────────────────────────────────────────────
        # Lógica: si hay maestra externa cargada (.rwl/.wid) Y el combo está
        # mostrando "↑ Ext: ..." (la opción de la externa), usar la externa.
        # Si el usuario eligió otra entrada del combo, respetarlo y usar esa
        # serie interna como maestra. Así el combo nunca queda "muerto"
        # cuando hay externa cargada.
        #
        # IMPORTANTE: aunque el usuario haya elegido una interna en el combo,
        # la externa SIGUE existiendo y debe aparecer en el combo (como opción
        # "↑ Ext: ...") y dibujarse en el gráfico (como referencia adicional).
        # Bug previo: si user cargaba externa pero elegía interna en combo,
        # nombre_maestra_ext quedaba en None → la externa desaparecía del
        # combo y del gráfico. Por eso "no aparecen series en el gráfico".
        maestra = None
        nombre_maestra_ext = None
        serie_maestra_data = None  # tupla (nombre, anios, anchos) de la externa
        seleccion_combo = self.combo_maestra.currentText()
        es_seleccion_externa = seleccion_combo.startswith("↑ Ext:")

        # 1) Si hay externa cargada, siempre preparar sus datos para que
        #    aparezca en el combo y se dibuje en el plot (independientemente
        #    de qué tenga seleccionado el usuario).
        if self.serie_maestra is not None and not self.serie_maestra.empty:
            col = ("Ancho_mm" if "Ancho_mm" in self.serie_maestra.columns
                   else self.serie_maestra.columns[0])
            ref = self.serie_maestra[col].dropna()
            if not ref.empty:
                nombre_maestra_ext = self.nombre_maestra
                serie_maestra_data = (
                    self.nombre_maestra,
                    list(ref.index.astype(int)),
                    list(ref.values.astype(float)),
                )

        # 2) Decidir cuál se usa como maestra para la correlación (=lo que
        #    se compara contra las demás).
        if serie_maestra_data is not None and (
                es_seleccion_externa or not seleccion_combo):
            # Combo apunta a la externa (o está vacío al principio) → usar externa
            maestra = serie_maestra_data
        else:
            # Combo apunta a una interna → usar esa
            sel = seleccion_combo
            for e in entradas:
                if e[0] == sel:
                    maestra = (e[0], e[1], e[2])
                    break
            # Fallback: si el seleccionado no está en entradas pero hay
            # externa, usar externa para no quedar sin maestra.
            if maestra is None and serie_maestra_data is not None:
                maestra = serie_maestra_data

        # Actualizar combo
        self._actualizar_combo_maestra(entradas, nombre_maestra_ext)

        # ── Normalizar ────────────────────────────────────────────────────
        n_curvas = len(entradas) + (1 if nombre_maestra_ext else 0)
        normalizar = n_curvas > 1
        pw.setLabel("left", "Ancho norm. (z)" if normalizar else "Ancho (mm)")

        # ── Dibujar series ────────────────────────────────────────────────
        # NUEVA LÓGICA (acorde a lo solicitado):
        # - La serie SELECCIONADA como maestra (la que se compara contra las
        #   demás, sin importar si es externa o interna) lleva ★ en su nombre
        #   en la leyenda Y se dibuja con línea SÓLIDA gruesa.
        # - Todas las otras series se dibujan con línea PUNTEADA fina.
        # - La externa, si está cargada pero no es la seleccionada, se dibuja
        #   con su propio etiquetado de referencia.
        nombre_maestra_actual = maestra[0] if maestra else None
        es_maestra_externa = (
            maestra is not None and maestra is serie_maestra_data
        )

        for nombre, anios, anchos, color, estilo in entradas:
            y = self._normalizar(anchos) if normalizar else anchos
            es_seleccionada = (nombre == nombre_maestra_actual
                                and not es_maestra_externa)
            if es_seleccionada:
                # Línea sólida gruesa + ★ en la leyenda
                nombre_legend = f"★ {nombre} (maestra)"
                pen = pg.mkPen(color, width=3, style=Qt.PenStyle.SolidLine)
            else:
                # Línea punteada fina
                nombre_legend = nombre
                pen = pg.mkPen(color, width=1.8, style=Qt.PenStyle.DashLine)
            item = pw.plot(anios, y,
                           pen=pen,
                           symbol="o", symbolSize=3,
                           symbolBrush=color, symbolPen=None,
                           name=nombre_legend)
            self._plot_items[nombre] = item
            if nombre in self._series_ocultas:
                item.setVisible(False)

        # Dibujar maestra externa si está cargada
        if serie_maestra_data is not None:
            nombre_m, anios_m, anchos_m = serie_maestra_data
            y_m = self._normalizar(anchos_m) if normalizar else anchos_m
            if es_maestra_externa:
                # Externa SELECCIONADA → ★ + sólida gruesa
                nombre_m_legend = f"★ {nombre_m} (maestra)"
                pen_m = pg.mkPen("#f0ad4e", width=3, style=Qt.PenStyle.SolidLine)
            else:
                # Externa NO seleccionada (hay una interna como maestra) →
                # se muestra como referencia adicional, dashed delgada
                nombre_m_legend = f"📂 {nombre_m} (ref. ext.)"
                pen_m = pg.mkPen("#f0ad4e", width=1.5, style=Qt.PenStyle.DashLine)
            item_m = pw.plot(anios_m, y_m,
                             pen=pen_m,
                             name=nombre_m_legend)
            self._plot_items[nombre_m] = item_m
            if nombre_m in self._series_ocultas:
                item_m.setVisible(False)

        # ── Correlaciones por segmento sobre el gráfico ─────────────────
        # Excluir de correlación la serie que ES la maestra (no tiene
        # sentido correlacionarla consigo misma). Funciona tanto si la
        # maestra es interna como si es externa.
        entradas_corr = [e for e in entradas
                         if not (maestra and e[0] == maestra[0]
                                 and not es_maestra_externa)]
        self._dibujar_segmentos_correlacion(pw, entradas_corr, maestra)
        self._actualizar_correlaciones_texto(entradas_corr, maestra)

        # Autoescala forzada: reactivar auto-range y disparar refresco,
        # así al cambiar de maestra el eje Y se reajusta solo. Antes
        # `autoRange()` no siempre se aplicaba si el usuario había hecho
        # pan/zoom manual previo (la viewbox quedaba en modo manual).
        pw.getViewBox().enableAutoRange()
        pw.getViewBox().autoRange()

        # Posicionar textos de correlación en la parte alta del gráfico
        self._posicionar_textos_correlacion(pw)
        # Reconectar: reposicionar textos al hacer zoom/pan
        try:
            pw.getViewBox().sigRangeChanged.disconnect(self._reposicionar_textos_corr)
        except (TypeError, RuntimeError):
            pass
        pw.getViewBox().sigRangeChanged.connect(self._reposicionar_textos_corr)

    # Aliases para compatibilidad con todas las llamadas existentes
    def _actualizar_grafico_serie(self):
        self._actualizar_grafico_unificado()

    def _actualizar_grafico_rt(self):
        self._actualizar_grafico_unificado()

    def _posicionar_textos_correlacion(self, pw):
        """Coloca los textos de correlación en la parte alta del rango Y visible."""
        pendientes = getattr(self, "_textos_corr_pendientes", [])
        if not pendientes:
            return
        vb = pw.getViewBox()
        [[_xmin, _xmax], [_ymin, y_max]] = vb.viewRange()
        # Posicionar justo en el tope del rango visible (anchor 0.0 = cuelga hacia abajo)
        y_top = y_max - (y_max - _ymin) * 0.01
        for txt, cx in pendientes:
            txt.setPos(cx, y_top)

    def _reposicionar_textos_corr(self, vb=None):
        """Slot: reposiciona textos de correlación cuando cambia el rango visible."""
        pw = self.grafico_unif
        self._posicionar_textos_correlacion(pw)

    # -------------------------------------------------------------------------
    # Gestión de radios guardados
    # -------------------------------------------------------------------------

    def _finalizar_camino(self):
        if not self.datos_medidos:
            return
        sufijo = "A" if not self.caminos_guardados else "B"
        nombre_defecto = self.input_codigo.text().strip() + sufijo
        nombre, ok = QInputDialog.getText(self, "Guardar Radio", "Identificador:", text=nombre_defecto)
        if not ok or not nombre:
            return

        self.caminos_guardados[nombre] = {
            "coords": list(self.vista.coordenadas_anillos),
            "es_salto": list(self.vista.es_salto),
            # deepcopy: cada radio guardado tiene SU PROPIA copia inmutable de
            # los anchos+años. Antes era list() shallow, que compartía los
            # dicts con la lista viva — si después algo modificaba esos dicts
            # (por ejemplo si datos_medidos = datos["datos_medidos"] en
            # _editar_camino apuntaba a los mismos), el guardado se
            # corrompía silenciosamente.
            "datos_medidos": copy.deepcopy(self.datos_medidos),
            # Guardar el año de médula/corteza y dirección EXPLÍCITAMENTE
            # (no solo derivados de datos_medidos[0]/[-1]) para evitar
            # ambigüedad al restaurar — si la dirección era corteza→médula,
            # los extremos se interpretan al revés. Con estos valores
            # explícitos, el restore en _editar_camino es robusto.
            "anio_medula": self.spin_medula.value(),
            "anio_corteza": self.spin_corteza.value(),
            "dir_idx": self.combo_dir.currentIndex(),
        }
        item = QListWidgetItem(nombre)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self.lista_caminos.addItem(item)

        self.vista.limpiar_historial()
        self.datos_medidos.clear()
        self.vista.coordenadas_anillos.clear()
        self.vista.es_salto.clear()
        self.vista.redibujar_todo()

        self.spin_medula.blockSignals(True)
        self.spin_corteza.blockSignals(True)
        self.spin_medula.setValue(0)
        self.spin_corteza.setValue(0)
        self.spin_medula.blockSignals(False)
        self.spin_corteza.blockSignals(False)

        self.vista.cargar_caminos_inactivos(self.caminos_guardados)
        self._guardar_estado()
        self._actualizar_grafico_rt()
        self.label_estado.setText(f"Radio '{nombre}' guardado. Listo para medir nuevo radio.")

    def _editar_camino(self):
        items = self.lista_caminos.selectedItems()
        if not items:
            return QMessageBox.information(self, "Aviso", "Seleccione un radio para editar.")
        if self.datos_medidos:
            if QMessageBox.question(
                self, "Atención",
                "Tiene un radio en progreso. ¿Desea descartarlo?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return

        nombre = items[0].text()
        datos = self.caminos_guardados.pop(nombre)

        self.vista.limpiar_historial()
        self.datos_medidos.clear()
        self.vista.coordenadas_anillos.clear()
        self.vista.es_salto.clear()
        self.vista.redibujar_todo()

        # deepcopy: la lista en datos["datos_medidos"] viene del save
        # (que ya hizo deepcopy). Aún así, deepcopy acá protege contra
        # mutaciones futuras de la lista activa que podrían corromper
        # otros radios guardados que compartieran refs (legacy data).
        self.datos_medidos = copy.deepcopy(datos["datos_medidos"])
        self.vista.cargar_camino_activo(datos["coords"], datos["es_salto"])

        if nombre in self.vista.items_caminos_inactivos:
            # Remover TODOS los items del radio inactivo, incluyendo los
            # textos de año (antes solo se removían puntos y líneas — eso
            # dejaba etiquetas de año verdes huérfanas sobre la imagen,
            # superpuestas con las del radio activo recién cargado).
            visuales_inact = self.vista.items_caminos_inactivos[nombre]
            for grupo in ("puntos", "lineas", "textos"):
                for item in visuales_inact.get(grupo, []):
                    try:
                        self.escena.removeItem(item)
                    except Exception:
                        pass
            del self.vista.items_caminos_inactivos[nombre]
            # También quitar del caché de datos: si no lo hacemos, al
            # próximo zoom `_redibujar_post_zoom` re-renderiza el radio
            # como inactivo encima del activo, recreando el bug.
            if hasattr(self.vista, "_caminos_inactivos_data"):
                self.vista._caminos_inactivos_data.pop(nombre, None)

        self.lista_caminos.takeItem(self.lista_caminos.row(items[0]))

        # Restaurar año/dir/etc. Usa los valores EXPLÍCITOS guardados si
        # existen (saves nuevos), si no cae al método legacy de derivar de
        # datos_medidos[0]/[-1] (saves viejos sin esos campos).
        self.spin_medula.blockSignals(True)
        self.spin_corteza.blockSignals(True)
        self.combo_dir.blockSignals(True)

        if "dir_idx" in datos:
            self.combo_dir.setCurrentIndex(datos["dir_idx"])
        if "anio_medula" in datos and "anio_corteza" in datos:
            # Saves nuevos: valores guardados explícitamente
            self.spin_medula.setValue(datos["anio_medula"])
            self.spin_corteza.setValue(datos["anio_corteza"])
        elif self.datos_medidos:
            # Saves legacy: derivar de los extremos del datos_medidos
            med_a_cor = self.combo_dir.currentIndex() == 0
            if med_a_cor:
                self.spin_medula.setValue(self.datos_medidos[0]["anio"])
                self.spin_corteza.setValue(self.datos_medidos[-1]["anio"])
            else:
                self.spin_corteza.setValue(self.datos_medidos[0]["anio"])
                self.spin_medula.setValue(self.datos_medidos[-1]["anio"])

        self.combo_dir.blockSignals(False)
        self.spin_medula.blockSignals(False)
        self.spin_corteza.blockSignals(False)

        self.input_codigo.setText(nombre)
        self._actualizar_grafico_rt()
        # IMPORTANTE: emitir metadatos_cambiados para que main_anillos.py
        # sincronice los spinboxes/combo GLOBALES del top bar con los locales
        # (ocultos) recién restaurados. Sin esto, el usuario sigue viendo
        # año=0 en la barra superior aunque el radio cargado tenga año 1500
        # — esa era la causa real del bug "se volvió al año cero".
        self.metadatos_cambiados.emit()
        self._guardar_estado()
        self.label_estado.setText(f"Editando radio '{nombre}'.")

    def _eliminar_camino(self):
        items = self.lista_caminos.selectedItems()
        if not items:
            return
        if QMessageBox.question(
            self, "Eliminar",
            "¿Eliminar permanentemente el radio seleccionado?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        for item in items:
            nombre = item.text()
            self.caminos_guardados.pop(nombre, None)
            if nombre in self.vista.items_caminos_inactivos:
                for p in self.vista.items_caminos_inactivos[nombre]["puntos"]:
                    self.escena.removeItem(p)
                for l in self.vista.items_caminos_inactivos[nombre]["lineas"]:
                    self.escena.removeItem(l)
                del self.vista.items_caminos_inactivos[nombre]
            self.lista_caminos.takeItem(self.lista_caminos.row(item))

        self._guardar_estado()
        self._actualizar_grafico_rt()

    def _toggle_visual_camino(self, item: QListWidgetItem):
        self.vista.toggle_camino_inactivo(item.text(), item.checkState() == Qt.CheckState.Checked)
        self._actualizar_grafico_rt()

    # -------------------------------------------------------------------------
    # Calibración
    # -------------------------------------------------------------------------

    def _calibrar_por_dpi(self):
        """Abre una ventana auxiliar con información completa de la imagen
        y permite cambiar el DPI manualmente.

        La ventana muestra:
          - Dimensiones en píxeles + megapíxeles totales
          - DPI actual y método con el que se detectó (JFIF / EXIF /
            TIFF / manual / default)
          - Tamaño físico calculado (mm y cm)
          - Resolución espacial (px / mm)
          - Spin box para override manual del DPI

        Útil para:
          - Verificar que el DPI detectado coincide con el real del
            escáner (cuando el usuario lo conoce)
          - Diagnosticar imágenes sin metadata (DPI default)
          - Confirmar tamaño físico calculado vs. tamaño real de la
            pieza escaneada

        Históricamente: reemplaza al QInputDialog simple que solo mostraba
        un número, sin contexto sobre cómo se obtuvo ni qué implicaba.
        """
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox,
            QPushButton, QFrame, QGridLayout,
        )
        from PyQt6.QtGui import QFont

        # ── Recopilar información actual ──────────────────────────────
        dpi_actual = int(round(self.vista.pixeles_por_mm * MM_POR_PULGADA))
        ruta_img = getattr(self, "ruta_imagen_actual", None)

        # Tamaño de la imagen cargada (pixmap)
        if self.item_imagen is not None:
            pm = self.item_imagen.pixmap()
            w_px, h_px = pm.width(), pm.height()
        else:
            w_px = h_px = 0

        # Re-detectar para mostrar el método empleado
        dpi_detectado = None
        if ruta_img:
            dpi_detectado = self._detectar_dpi(ruta_img)

        # Texto descriptivo del MODO actual (puede ser distinto al
        # resultado de la metadata si el usuario lo cambió manualmente).
        modo_label = {
            "auto":     "✓ Detectado automáticamente desde metadata",
            "manual":   "✏️ Cambiado manualmente por el usuario",
            "regla":    "📏 Calibrado midiendo una distancia conocida",
            "default":  "⚠ No se detectó — usando DPI por defecto",
            "guardado": "💾 Restaurado desde sesión guardada",
        }
        metodo_deteccion = modo_label.get(
            getattr(self, "_dpi_modo", "default"),
            "Desconocido")

        # Cálculos derivados
        if dpi_actual > 0:
            w_mm = w_px / dpi_actual * MM_POR_PULGADA
            h_mm = h_px / dpi_actual * MM_POR_PULGADA
        else:
            w_mm = h_mm = 0
        mp = (w_px * h_px) / 1e6

        # ── Construir el diálogo ──────────────────────────────────────
        dlg = QDialog(self)
        dlg.setWindowTitle("Información de imagen — Configuración DPI")
        dlg.setMinimumWidth(500)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)

        # Título
        titulo = QLabel("📐 Información de la imagen")
        f = QFont(); f.setBold(True); f.setPointSize(11)
        titulo.setFont(f)
        layout.addWidget(titulo)

        # Grid con datos
        grid_frame = QFrame()
        grid_frame.setFrameShape(QFrame.Shape.StyledPanel)
        grid = QGridLayout(grid_frame)
        grid.setSpacing(8)

        def add_row(row: int, label: str, valor: str, destacar: bool = False):
            lab = QLabel(label)
            val = QLabel(valor)
            if destacar:
                fb = QFont(); fb.setBold(True)
                val.setFont(fb)
            val.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(lab, row, 0)
            grid.addWidget(val, row, 1)

        add_row(0, "Dimensiones (px):", f"{w_px:,} × {h_px:,}".replace(",", "."))
        add_row(1, "Megapíxeles totales:", f"{mp:.1f} MP")
        add_row(2, "DPI actual:", f"{dpi_actual} DPI", destacar=True)
        add_row(3, "Método de detección:", metodo_deteccion)
        if dpi_detectado is not None and dpi_detectado != dpi_actual:
            add_row(4, "DPI desde metadata:", f"{int(round(dpi_detectado))} DPI")
            offset = 5
        else:
            offset = 4
        add_row(offset, "Tamaño físico calculado:",
                f"{w_mm:.1f} × {h_mm:.1f} mm  ({w_mm/10:.1f} × {h_mm/10:.1f} cm)")
        add_row(offset + 1, "Resolución espacial:",
                f"{self.vista.pixeles_por_mm:.2f} px/mm  "
                f"({1000/self.vista.pixeles_por_mm:.1f} μm/px)")
        layout.addWidget(grid_frame)

        # Ayuda
        ayuda = QLabel(
            "💡 Si el DPI detectado no coincide con el real del escáner, "
            "puedes sobreescribirlo manualmente aquí. El cambio recalcula "
            "todas las mediciones existentes."
        )
        ayuda.setWordWrap(True)
        ayuda.setStyleSheet("color: #888; font-style: italic;")
        layout.addWidget(ayuda)

        # Spin box para override
        fila_override = QHBoxLayout()
        fila_override.addWidget(QLabel("DPI manual:"))
        spin_dpi = QSpinBox()
        spin_dpi.setRange(72, 12800)
        spin_dpi.setValue(dpi_actual)
        spin_dpi.setSuffix(" DPI")
        spin_dpi.setSingleStep(100)
        spin_dpi.setMinimumWidth(120)
        fila_override.addWidget(spin_dpi)

        # Botones rápidos de DPI común
        for dpi_preset in (1200, 1600, 2400, 3200):
            btn_p = QPushButton(str(dpi_preset))
            btn_p.setMaximumWidth(60)
            btn_p.clicked.connect(lambda _, v=dpi_preset: spin_dpi.setValue(v))
            fila_override.addWidget(btn_p)
        fila_override.addStretch()
        layout.addLayout(fila_override)

        # Botones
        botonera = QHBoxLayout()
        btn_cancelar = QPushButton("Cancelar")
        btn_cancelar.clicked.connect(dlg.reject)
        btn_aplicar = QPushButton("✓ Aplicar DPI")
        btn_aplicar.setDefault(True)
        btn_aplicar.setStyleSheet(
            "QPushButton{background:#5cb85c; color:white; "
            "font-weight:bold; padding:6px 14px; border-radius:4px;}")
        btn_aplicar.clicked.connect(dlg.accept)
        botonera.addStretch()
        botonera.addWidget(btn_cancelar)
        botonera.addWidget(btn_aplicar)
        layout.addLayout(botonera)

        # ── Ejecutar ──────────────────────────────────────────────────
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        dpi_nuevo = spin_dpi.value()
        if dpi_nuevo <= 0 or dpi_nuevo == dpi_actual:
            return

        # Aplicar el cambio
        self.vista.pixeles_por_mm = dpi_nuevo / MM_POR_PULGADA
        # Marcar el origen como manual para que el diálogo lo refleje
        # correctamente la próxima vez que se abra.
        self._dpi_modo = "manual"
        # CRUCIAL: recalcular mediciones con el nuevo DPI. Sin esto los
        # anchos en datos_medidos quedan basados en el DPI viejo y el
        # cambio "no se nota".
        self.vista.recalcular_todas_las_distancias()
        self.vista.redibujar_todo()
        self._actualizar_grafico_rt()
        self._guardar_estado()
        self.label_estado.setText(
            f"📐 DPI cambiado manualmente: {dpi_nuevo} "
            f"(1 mm = {self.vista.pixeles_por_mm:.2f} px). "
            f"Mediciones recalculadas."
        )

    def _activar_calibracion(self):
        """Activa el modo de calibración por regla.

        Flujo nuevo (acorde a la solicitud del usuario):
          1. Activar modo
          2. Usuario hace clic en 2 puntos sobre la imagen
          3. Después del 2° clic, se dibuja la línea con la distancia en px
             y aparece el diálogo pidiendo los mm
          4. Al aplicar, las marcas visuales se borran solas

        Antes el flujo era inverso: preguntaba primero los mm y después
        el usuario marcaba — era contra-intuitivo y dejaba las marcas
        permanentes en la imagen.
        """
        self.vista.iniciar_calibracion()
        self.label_estado.setText(
            "📏 Calibración: Haz clic en el primer punto de la distancia conocida.")
    # -------------------------------------------------------------------------
    # Importación de archivos .pos (CooRecorder)
    # -------------------------------------------------------------------------

    def _importar_pos(self):
        if self.datos_medidos:
            if QMessageBox.question(
                self, "Atención",
                "Tiene un radio en progreso. ¿Sobrescribirlo con el archivo importado?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return

        ruta, _ = QFileDialog.getOpenFileName(
            self, "Importar CooRecorder .pos", _ultima_carpeta(),
            "CooRecorder POS (*.pos);;Todos (*.*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not ruta:
            return

        try:
            dpi_pos, anio_datado, coords_px, saltos = self._parsear_pos(ruta)
        except (OSError, ValueError) as exc:
            return QMessageBox.critical(self, "Error", f"No se pudo procesar el archivo:\n{exc}")

        if not coords_px:
            return QMessageBox.warning(self, "Error", "No se encontraron coordenadas válidas.")

        self.vista.limpiar_historial()
        self.vista.pixeles_por_mm = dpi_pos / MM_POR_PULGADA
        self.vista.coordenadas_anillos = coords_px
        self.vista.es_salto = saltos

        # Detectar automáticamente médula/corteza:
        # el año más reciente (#C DATED) = corteza; el más antiguo = médula.
        anillos_validos = sum(1 for s in saltos[1:] if not s)
        self.spin_medula.blockSignals(True)
        self.spin_corteza.blockSignals(True)
        if anio_datado is not None:
            # El archivo declara explícitamente el año de corteza
            anio_corteza = anio_datado
            anio_medula  = anio_datado - anillos_validos + 1
        else:
            # Sin fecha declarada: usar año actual como corteza estimada
            import datetime
            anio_corteza = datetime.date.today().year
            anio_medula  = anio_corteza - anillos_validos + 1
        self.spin_corteza.setValue(anio_corteza)
        self.spin_medula.setValue(anio_medula)
        # Asegurar que la dirección quede en Médula→Corteza (+1)
        self.combo_dir.setCurrentIndex(0)
        self.spin_medula.blockSignals(False)
        self.spin_corteza.blockSignals(False)

        self.input_codigo.setText(os.path.splitext(os.path.basename(ruta))[0][:TUCSON_MAX_CHARS_ID].upper())
        self.vista.redibujar_todo()
        self.vista.recalcular_todas_las_distancias()
        self._guardar_estado()
        self.label_estado.setText(f"Archivo importado correctamente ({dpi_pos:.0f} DPI).")

    @staticmethod
    def _parsear_pos(ruta: str) -> tuple[float, int | None, list, list]:
        """Parsea un archivo .pos de CooRecorder y devuelve (dpi, anio, coords_px, saltos)."""
        dpi = DPI_DEFECTO
        anio_datado = None
        coords_px: list[tuple[float, float]] = []
        saltos: list[bool] = []

        with open(ruta, "r", encoding="utf-8", errors="replace") as f:
            for linea in f:
                ln = linea.strip()
                if not ln:
                    continue
                if ln.startswith("#DPI"):
                    try:
                        dpi = float(ln.split()[1].replace(",", "."))
                    except (IndexError, ValueError):
                        pass
                elif ln.startswith("#C DATED"):
                    try:
                        anio_datado = int(ln.split()[2])
                    except (IndexError, ValueError):
                        pass
                elif ln.startswith("#") or ln.startswith("SCALE") or ln.startswith("DATED"):
                    continue
                else:
                    for i, token in enumerate(ln.split()):
                        token_limpio = re.sub(r"[^\d.,-]", "", token)
                        if "," not in token_limpio:
                            continue
                        try:
                            x_mm, y_mm = map(float, token_limpio.split(",", 1))
                            coords_px.append((x_mm * (dpi / MM_POR_PULGADA), y_mm * (dpi / MM_POR_PULGADA)))
                            saltos.append(False if not saltos else i > 0)
                        except ValueError:
                            pass

        return dpi, anio_datado, coords_px, saltos

    # -------------------------------------------------------------------------
    # Persistencia (guardado automático en JSON oculto)
    # -------------------------------------------------------------------------

    def _ruta_json(self) -> str:
        directorio = os.path.dirname(self.ruta_imagen_actual)
        nombre = os.path.basename(self.ruta_imagen_actual)
        return os.path.join(directorio, f".{nombre}_dpi.json")

    def _guardar_estado(self):
        try:
            datos = {
                "pixeles_por_mm": self.vista.pixeles_por_mm,
                "caminos_guardados": self.caminos_guardados,
                "camino_activo": {
                    "coordenadas": [list(c) for c in self.vista.coordenadas_anillos],
                    "es_salto": self.vista.es_salto,
                    "datos_medidos": self.datos_medidos,
                    "anio_medula": self.spin_medula.value(),
                    "anio_corteza": self.spin_corteza.value(),
                    "dir_idx": self.combo_dir.currentIndex(),
                    "codigo": self.input_codigo.text(),
                },
            }
            with open(self._ruta_json(), "w") as f:
                json.dump(datos, f)
        except OSError as exc:
            logger.warning("No se pudo guardar el estado: %s", exc)

    def _cargar_estado(self) -> bool:
        ruta = self._ruta_json()
        if not os.path.exists(ruta):
            return False
        try:
            with open(ruta, "r") as f:
                datos = json.load(f)

            if "pixeles_por_mm" in datos:
                self.vista.pixeles_por_mm = datos["pixeles_por_mm"]
                # Marcar como restaurado desde sesión. Si después el usuario
                # quiere saber de dónde viene este DPI, el diálogo le dirá
                # que fue cargado de un estado guardado, no detectado en
                # este momento.
                self._dpi_modo = "guardado"

            self.caminos_guardados = datos.get("caminos_guardados", {})
            self.lista_caminos.clear()
            for nombre in self.caminos_guardados:
                item = QListWidgetItem(nombre)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
                self.lista_caminos.addItem(item)
            self.vista.cargar_caminos_inactivos(self.caminos_guardados)

            activo = datos.get("camino_activo", {})
            if activo:
                self.spin_medula.blockSignals(True)
                self.spin_corteza.blockSignals(True)
                self.input_codigo.setText(activo.get("codigo", self.input_codigo.text()))
                self.combo_dir.setCurrentIndex(activo.get("dir_idx", 0))
                self.spin_medula.setValue(activo.get("anio_medula", activo.get("anio_inicio", 0)))
                self.spin_corteza.setValue(activo.get("anio_corteza", activo.get("anio_actual", 0)))
                self.datos_medidos = activo.get("datos_medidos", [])
                self.vista.limpiar_historial()
                self.vista.cargar_camino_activo(
                    activo.get("coordenadas", []), activo.get("es_salto", [])
                )
                self.spin_medula.blockSignals(False)
                self.spin_corteza.blockSignals(False)

            self._actualizar_grafico_rt()
            return True

        except (OSError, json.JSONDecodeError, KeyError) as exc:
            logger.warning("No se pudo cargar el estado guardado: %s", exc)
            return False

    # -------------------------------------------------------------------------
    # Exportación
    # -------------------------------------------------------------------------

    def _ejecutar_exportacion(self):
        opciones = []
        if self.datos_medidos:
            opciones.append("Radio Actual (En progreso)")
        opciones.extend(list(self.caminos_guardados.keys()))
        if not opciones:
            return QMessageBox.warning(self, "Aviso", "No hay radios para exportar.")

        seleccion, ok = QInputDialog.getItem(
            self, "Exportar", "Seleccione el radio a exportar:", opciones, 0, False
        )
        if not ok or not seleccion:
            return

        if seleccion == "Radio Actual (En progreso)":
            datos = self.datos_medidos
            codigo = self.input_codigo.text().strip()
            anomalias_export = self.vista.anomalias
            # Para EW/LW: usar el estado en vivo de la vista
            coords_export = self.vista.coordenadas_anillos
            saltos_export = self.vista.es_salto
        else:
            datos = self.caminos_guardados[seleccion]["datos_medidos"]
            codigo = seleccion
            anom_data = self.caminos_guardados[seleccion].get("anomalias", {})
            anomalias_export = anom_data
            # Para EW/LW: usar los coords/saltos GUARDADOS del radio elegido,
            # NO los de la vista en vivo (que pueden estar vacíos o ser de
            # otro radio en progreso). Este era el bug que hacía que el
            # usuario nunca pudiera "guardar" la info de EW/LW de un radio
            # ya finalizado.
            coords_export = self.caminos_guardados[seleccion].get("coords", [])
            saltos_export = self.caminos_guardados[seleccion].get("es_salto", [])

        if not codigo:
            return QMessageBox.warning(self, "Aviso", "El radio no tiene nombre/código.")

        # Diálogo de guardado para capturar ruta y guardar sidecar
        # Obtener última carpeta y armar la ruta sugerida
        carpeta = _ultima_carpeta()
        ruta_sugerida = os.path.join(carpeta, codigo) if carpeta else codigo

        # Diálogo de guardado para capturar ruta y guardar sidecar
        ruta, fmt = QFileDialog.getSaveFileName(
            self, "Guardar", ruta_sugerida,
            "Formato Tucson (*.txt *.rwl);;Excel (*.xlsx);;CSV (*.csv)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not ruta:
            return
            
        # Guardar la ruta elegida
        _ultima_carpeta(ruta)
        
        try:
            import pandas as pd
            anios  = [d["anio"]     for d in sorted(datos, key=lambda d: d["anio"])]
            anchos = [d["ancho_mm"] for d in sorted(datos, key=lambda d: d["anio"])]

            # Calcular EW/LW por anillo si hay límites — para incluir como
            # columnas extras en CSV/Excel.
            ppm = self.vista.pixeles_por_mm
            anchos_ewlw = None
            tiene_ewlw_en_radio = any(_es_ewlw(v) for v in saltos_export)
            if tiene_ewlw_en_radio and coords_export:
                try:
                    anchos_ewlw = _calcular_anchos_ewlw_por_anillo(
                        coords_export, saltos_export, ppm
                    )
                    # Alinear largo con datos por seguridad
                    if len(anchos_ewlw) != len(datos):
                        anchos_ewlw = None
                except Exception as exc:
                    logger.warning("No se pudo calcular EW/LW para tabla: %s", exc)
                    anchos_ewlw = None

            if "*.csv" in fmt:
                ExportadorDendro._exportar_tabular(
                    codigo, anios, anchos, ruta, "csv",
                    anchos_ewlw=anchos_ewlw)
            elif "*.xlsx" in fmt:
                ExportadorDendro._exportar_tabular(
                    codigo, anios, anchos, ruta, "xlsx",
                    anchos_ewlw=anchos_ewlw)
            else:
                mediciones_int = [int(round(m * 1000)) for m in anchos]
                ExportadorDendro._escribir_tucson(codigo, anios[0], mediciones_int, ruta)

            # Exportar EW/LW si hay límites marcados en el radio elegido
            # (no en la vista en vivo, que puede ser distinta).
            tiene_ewlw = any(_es_ewlw(v) for v in saltos_export)
            if tiene_ewlw:
                self._exportar_ewlw(
                    codigo, ruta,
                    coords=coords_export,
                    saltos=saltos_export,
                    datos_medidos=datos,
                )

            QMessageBox.information(self, "Éxito", f"Exportado a:\n{ruta}")
            # Guardar sidecar junto al .rwl
            self._ultima_ruta_exportada = ruta
            self._guardar_sidecar_anom(ruta)
        except Exception as exc:
            logger.error("Error al exportar: %s", exc)
            QMessageBox.critical(self, "Error", f"Fallo al exportar:\n{exc}")

    # -------------------------------------------------------------------------
    # Sidecar de anomalías  (.rwl.anom.json)
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Estimación de anillos faltantes hacia la médula
    # -------------------------------------------------------------------------

    def _iniciar_estimacion_pith(self):
        """Abre el diálogo de configuración y activa el modo PITH."""
        # Limpieza defensiva: si por algún motivo quedaron items del modo
        # pith de una sesión anterior (incompleta, mal cancelada, etc.),
        # los borramos antes de empezar. Sin esto, los círculos/líneas/
        # texto naranjas anteriores se solapaban con los nuevos.
        if hasattr(self.vista, "_limpiar_items_pith"):
            self.vista._limpiar_items_pith()

        n_disponibles = len(self.datos_medidos)
        if n_disponibles < 3:
            QMessageBox.warning(self, "Pocos anillos",
                "Necesitas al menos 3 anillos medidos para usar esta función.")
            self.btn_pith.setChecked(False)
            return

        tiene_ref = (self.serie_maestra is not None
                     and not self.serie_maestra.empty)

        dlg = DialogoMetodoPith(
            tiene_referencia=tiene_ref,
            n_anillos_disponibles=n_disponibles,
            parent=self,
        )
        if dlg.exec() != DialogoMetodoPith.DialogCode.Accepted:
            self.btn_pith.setChecked(False)
            return

        self._pith_metodo    = dlg.metodo
        self._pith_n_int     = dlg.n_internos
        self._pith_usar_ref  = dlg.usar_referencia
        self._pith_dap_mm    = dlg.dap_mm
        self._pith_metodo_calculo = dlg.metodo_calculo  # 'rg', 'bai', 'promedio'

        if self._pith_metodo == "arco":
            self.set_modo(MODO_PITH)
            self.label_estado.setText(
                "🎯 Paso 1/3: Haz clic en un EXTREMO del arco del anillo más interno."
            )
        else:
            # DAP: no necesita marcado en imagen, calcular directamente
            self.btn_pith.setChecked(False)
            self._calcular_pith_por_dap()

    def _calcular_pith_por_dap(self):
        """Calcula la distancia faltante usando el DAP ingresado."""
        radio_mm    = self._pith_dap_mm / 2.0
        radio_px    = radio_mm * self.vista.pixeles_por_mm

        # Longitud del radio medido: distancia desde el primer punto hasta el último
        if len(self.vista.coordenadas_anillos) < 2:
            QMessageBox.warning(self, "Sin camino",
                "Necesitas tener puntos medidos en el camino activo.")
            return

        coords = self.vista.coordenadas_anillos
        longitud_medida_px = sum(
            math.hypot(coords[i][0]-coords[i-1][0], coords[i][1]-coords[i-1][1])
            for i in range(1, len(coords))
            if not self.vista.es_salto[i]
        )

        distancia_faltante_px = max(0.0, radio_px - longitud_medida_px)

        if distancia_faltante_px <= 0:
            QMessageBox.information(self, "Sin anillos faltantes",
                "La longitud medida ya cubre o supera el radio estimado por DAP.")
            return

        # Centro estimado: extrapolando desde el primer punto
        cx = coords[0][0]
        cy = coords[0][1]

        self._mostrar_dialogo_resultados_pith(
            cx, cy, radio_px, distancia_faltante_px, metodo="dap"
        )

    def _confirmar_estimacion_pith(self):
        """Confirma la estimación de pith por método arco.

        Llamado desde VistaInteractiva al presionar Enter en MODO_PITH.

        Algoritmo:
          1. La médula está en el centro del círculo ajustado (Duncan GM,
             1989): r = L²/(8h) + h/2 = radio del círculo por mínimos
             cuadrados a través de los puntos marcados.
          2. La distancia faltante es `dist(p_inner, centro)`: desde el
             primer anillo medido más cercano a la médula hasta el centro
             estimado. Esta es la métrica relevante INDEPENDIENTEMENTE
             de qué anillo marque el usuario — el arco marcado es solo
             la herramienta para localizar la médula.
          3. Los anillos faltantes se calculan en el diálogo de resultados
             usando RG (Altman 2016): anillos = distancia / promedio_ancho,
             donde el promedio viene de N anillos junto al arco marcado
             contando hacia la corteza.
        """
        resultado = self.vista._confirmar_pith()
        self.set_modo(MODO_MEDIR)
        self.btn_pith.setChecked(False)

        if resultado is None:
            return

        cx, cy, radio_px = resultado
        coords = self.vista.coordenadas_anillos

        if radio_px <= 0 or not coords:
            QMessageBox.warning(self, "Estimación inválida",
                "El círculo ajustado tiene radio 0. Verifica los puntos marcados.")
            return

        # p_inner según dirección de medición
        try:
            med_a_cor = self.combo_dir.currentIndex() == 0
        except Exception:
            med_a_cor = True

        if med_a_cor:
            p_inner = coords[0]
        else:
            p_inner = coords[-1] if len(coords) >= 2 else coords[0]

        # Distancia faltante = dist(p_inner, centro). NO usar radio.
        distancia_faltante_px = math.hypot(cx - p_inner[0], cy - p_inner[1])

        if distancia_faltante_px <= 0:
            QMessageBox.warning(self, "Estimación inválida",
                "El primer anillo medido coincide con la médula estimada.\n"
                "No hay anillos faltantes para estimar.")
            return

        self._mostrar_dialogo_resultados_pith(
            cx, cy, radio_px, distancia_faltante_px, metodo="arco"
        )

    def _mostrar_dialogo_resultados_pith(
        self,
        cx: float, cy: float,
        radio_px: float,
        distancia_faltante_px: float,
        metodo: str,
    ):
        """Calcula los resultados y muestra el diálogo de confirmación."""
        import math as _math

        n_int = getattr(self, '_pith_n_int', 5)
        usar_ref = getattr(self, '_pith_usar_ref', False)

        # Obtener los N anillos más internos (los primeros en la lista)
        # datos_medidos está ordenado médula→corteza
        med_a_cor = self.combo_dir.currentIndex() == 0
        if med_a_cor:
            # Los más internos son los del inicio
            anchos_internos = [d["ancho_mm"] for d in self.datos_medidos[:n_int]]
            anio_inicio = self.datos_medidos[0]["anio"] if self.datos_medidos else None
        else:
            # Los más internos son los del final
            anchos_internos = [d["ancho_mm"] for d in self.datos_medidos[-n_int:]]
            anio_inicio = self.datos_medidos[-1]["anio"] if self.datos_medidos else None

        anchos_internos = [a for a in anchos_internos if a > 0]

        # Serie de referencia si está disponible y seleccionada
        serie_ref = None
        if usar_ref and self.serie_maestra is not None and not self.serie_maestra.empty:
            col = ("Ancho_mm" if "Ancho_mm" in self.serie_maestra.columns
                   else self.serie_maestra.columns[0])
            serie_ref = self.serie_maestra[col].dropna()

        resultados = estimar_anillos_faltantes(
            distancia_faltante_mm=distancia_faltante_px / self.vista.pixeles_por_mm,
            anchos_internos_mm=anchos_internos,
            serie_ref=serie_ref,
            anio_inicio_serie=anio_inicio,
        )

        if not resultados:
            QMessageBox.warning(self, "Sin resultados",
                "No se pudo calcular la estimación. Verifica los datos.")
            return

        tiene_ref = (serie_ref is not None)
        dlg = DialogoEstimacionPith(
            resultados=resultados,
            radio_px=radio_px,
            distancia_faltante_px=distancia_faltante_px,
            pixeles_por_mm=self.vista.pixeles_por_mm,
            n_anillos_internos=len(anchos_internos),
            tiene_referencia=tiene_ref,
            parent=self,
        )
        dlg.poblar_grafico(anchos_internos)
        dlg.aplicar_offset.connect(
            lambda n: self._aplicar_pith_offset(n, dlg.marcar_estimada, resultados, metodo,
                                                 radio_px / self.vista.pixeles_por_mm,
                                                 distancia_faltante_px / self.vista.pixeles_por_mm)
        )
        dlg.exec()

    def _aplicar_pith_offset(
        self,
        n_anillos: int,
        marcar_estimada: bool,
        resultados: dict,
        metodo: str,
        radio_mm: float,
        distancia_faltante_mm: float,
    ):
        """
        Aplica el offset: retrocede el año de médula en n_anillos.
        Actualiza el sidecar con la información de la estimación.
        """
        if n_anillos <= 0:
            return

        # Guardar estado previo para poder deshacer
        med_a_cor = self.combo_dir.currentIndex() == 0
        self._pith_undo = {
            "med_a_cor":        med_a_cor,
            "anio_medula_old":  self.spin_medula.value(),
            "anio_corteza_old": self.spin_corteza.value(),
            "medula_checked":   self.check_tiene_medula.isChecked(),
            "n_anillos":        n_anillos,
        }

        if med_a_cor:
            nuevo_anio_medula = self.spin_medula.value() - n_anillos
            self.spin_medula.setValue(nuevo_anio_medula)
        else:
            nuevo_anio_corteza = self.spin_corteza.value() + n_anillos
            self.spin_corteza.setValue(nuevo_anio_corteza)

        if marcar_estimada:
            self.check_tiene_medula.setChecked(True)

        # Guardar en el sidecar
        self._pith_estimacion = {
            "pith_estimated":       True,
            "pith_offset":          n_anillos,
            "pith_method":          metodo,
            "pith_radio_mm":        round(radio_mm, 2),
            "pith_distancia_mm":    round(distancia_faltante_mm, 2),
            "pith_n_media":         resultados.get("n_media"),
            "pith_n_tendencia":     resultados.get("n_tendencia"),
            "pith_n_referencia":    resultados.get("n_referencia"),
            "pith_ancho_medio":     resultados.get("ancho_medio"),
            "pith_rango_min":       resultados.get("incertidumbre_min"),
            "pith_rango_max":       resultados.get("incertidumbre_max"),
        }
        self._guardar_sidecar_anom()
        self._guardar_estado()

        dist = distancia_faltante_mm
        self.label_estado.setText(
            f"✅ Estimación aplicada: {n_anillos} anillos faltantes al centro · "
            f"distancia {dist:.1f} mm · año médula → {self.spin_medula.value()}"
        )
        self.btn_deshacer_pith.show()

    def _deshacer_pith_offset(self):
        """Revierte la última estimación de médula aplicada."""
        undo = getattr(self, "_pith_undo", None)
        if not undo:
            QMessageBox.information(self, "Nada que deshacer",
                "No hay estimación para revertir.")
            return

        resp = QMessageBox.question(
            self, "Deshacer estimación",
            f"¿Revertir la estimación de {undo['n_anillos']} anillos faltantes?\n"
            f"El año de médula volverá a su valor anterior.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        # Restaurar años
        self.spin_medula.setValue(undo["anio_medula_old"])
        self.spin_corteza.setValue(undo["anio_corteza_old"])
        self.check_tiene_medula.setChecked(undo["medula_checked"])

        # Limpiar estimación
        self._pith_estimacion = None
        self._pith_undo = None
        self.btn_deshacer_pith.hide()

        self._guardar_sidecar_anom()
        self._guardar_estado()
        self.vista.recalcular_todas_las_distancias()
        self.vista.redibujar_todo()

        self.label_estado.setText(
            f"↩ Estimación revertida · "
            f"año médula → {self.spin_medula.value()}"
        )

    # -------------------------------------------------------------------------
    # Earlywood / Latewood
    # -------------------------------------------------------------------------

    def _eliminar_ewlw(self):
        """Elimina todos los límites EW/LW del radio activo tras confirmación."""
        n_ewlw = sum(1 for v in self.vista.es_salto if _es_ewlw(v))
        if n_ewlw == 0:
            QMessageBox.information(self, "Sin límites EW/LW",
                "No hay límites EW/LW en el radio activo.")
            return
        resp = QMessageBox.question(
            self, "Eliminar límites EW/LW",
            f"¿Eliminar los {n_ewlw} límite(s) EW/LW del radio activo?\n"
            "Esta acción se puede deshacer con Ctrl+Z.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        self.vista.guardar_en_historial()
        # Eliminar de atrás hacia adelante para no invalidar índices
        indices = [i for i, v in enumerate(self.vista.es_salto) if _es_ewlw(v)]
        for i in reversed(indices):
            self.vista.coordenadas_anillos.pop(i)
            self.vista.es_salto.pop(i)
        self.vista.redibujar_todo()
        self.vista.recalcular_todas_las_distancias()
        self.vista.progreso_modificado.emit()
        self.label_estado.setText(
            f"🗑 {n_ewlw} límite(s) EW/LW eliminados. Ctrl+Z para deshacer.")

    def _detectar_ewlw_automatico(self):
        """Detecta EW/LW automáticamente en hilo separado.

        Bug histórico (causaba freezes y en un caso reinicio de la PC):
          1. cv2.imread() corría en el MAIN thread → bloqueaba la UI varios
             segundos en imágenes grandes (scanners a 2400dpi).
          2. No había protección contra doble clic → se podían lanzar varios
             workers concurrentes, cada uno con su propia copia del array
             gris en memoria (decenas/cientos de MB) → OOM kill.
          3. dlg.cancelado.connect(worker.terminate) — QThread.terminate()
             es UNSAFE según docs de Qt: para el thread sin limpiar
             recursos, puede corromper memoria si estaba en numpy/cv2.
          4. Re-leía la imagen del disco aunque self._img_bgr_original ya
             la tuviera cacheada en RAM.

        Fixes aplicados:
          - Si hay otro worker activo, mensaje y bail (sin lanzar segundo).
          - Reusa self._img_bgr_original cuando está disponible.
          - Mueve cv2.cvtColor también fuera del main thread.
          - Cancelación cooperativa con flag en vez de terminate().
          - Limpia self._ewlw_worker al terminar para liberar memoria.
        """
        # Protección contra doble lanzamiento — uno de los caminos al crash.
        worker_previo = getattr(self, "_ewlw_worker", None)
        if worker_previo is not None and worker_previo.isRunning():
            QMessageBox.information(
                self, "Detección en curso",
                "Ya se está ejecutando una detección de EW/LW.\n"
                "Espera a que termine o cancela el diálogo de progreso."
            )
            return

        if not self.vista.coordenadas_anillos or len(self.vista.coordenadas_anillos) < 2:
            QMessageBox.information(
                self, "Sin anillos",
                "No hay anillos marcados todavía. Marca al menos un anillo antes.")
            return

        # Capturar estado antes de lanzar el hilo
        coords = list(self.vista.coordenadas_anillos)
        saltos = list(self.vista.es_salto)
        banda  = self._bi_banda
        ruta   = self.ruta_imagen_actual
        # Usar la imagen cacheada si existe (común: ya cargada al abrir
        # la pestaña). Solo re-leemos del disco si por algún motivo no
        # está disponible.
        img_cached = self._img_bgr_original

        dlg = _DialogoProgreso("Detectando límites EW/LW", parent=self)
        # Flag de cancelación cooperativa — más seguro que terminate().
        # Se chequea dentro del loop principal de la función worker.
        cancel_flag = {"stop": False}
        dlg.show()

        def _tarea_ewlw(sig_prog, img_bgr, c, s, b, ruta_disco):
            """Ejecuta en thread secundario. NO toca widgets Qt directamente."""
            import cv2 as _cv2
            if img_bgr is None:
                # Fallback: leer del disco (solo si no está cacheada)
                img_bgr = _cv2.imread(ruta_disco, _cv2.IMREAD_COLOR)
                if img_bgr is None:
                    raise RuntimeError(
                        "No se pudo leer la imagen desde el disco para detectar EW/LW.")
            gray = _cv2.cvtColor(img_bgr, _cv2.COLOR_BGR2GRAY).astype(np.float32)
            return _calcular_ewlw_posiciones_cancelable(
                sig_prog, gray, c, s, b, cancel_flag)

        worker = _Worker(_tarea_ewlw, img_cached, coords, saltos, banda, ruta)
        self._ewlw_worker = worker

        worker.signals.progreso.connect(dlg.actualizar)

        def _on_error(e):
            dlg.close()
            self._ewlw_worker = None  # liberar referencia
            QMessageBox.critical(self, "Error EW/LW", e)
        worker.signals.error.connect(_on_error)

        def _on_ewlw(inserciones):
            """Callback que aplica los resultados del worker EW/LW.

            Bug arreglado (causaba el "crash al 99%"):
              - Antes: aplicaba TODAS las inserciones (incluso parciales
                de una cancelación) y llamaba redibujar_todo de forma
                sincrónica. Con 200+ inserts + redibujar el main thread
                quedaba freezeado varios segundos → usuario veía el
                progreso pegado al 99% y pensaba que crasheaba.
              - Ahora: (1) si se canceló, descarta los parciales sin
                aplicar nada; (2) si hay resultados, los aplica en
                chunks con QApplication.processEvents() entre chunks
                para que la UI responda; (3) muestra el estado del
                proceso en el diálogo ("Aplicando...", "Redibujando...")
                en vez de quedar 99% silencioso.
            """
            # ── Caso 1: el usuario canceló ──
            # Descartar parciales para no aplicar 200 EW/LW que no quería.
            if cancel_flag.get("stop"):
                try:
                    dlg.close()
                except Exception:
                    pass
                self._ewlw_worker = None
                self.label_estado.setText(
                    "🛑 Detección EW/LW cancelada — sin cambios aplicados."
                )
                return

            # ── Caso 2: sin resultados ──
            if not inserciones:
                try:
                    dlg.close()
                except Exception:
                    pass
                self._ewlw_worker = None
                self.label_estado.setText(
                    "🍂 No se encontraron límites EW/LW."
                )
                return

            # ── Caso 3: aplicar resultados ──
            # Actualizar el diálogo para indicar la fase de aplicación.
            # El diálogo sigue VISIBLE durante esta fase para que el
            # usuario sepa que el programa está trabajando, no crasheado.
            try:
                if hasattr(dlg, "_bar"):
                    dlg._bar.setValue(100)
                if hasattr(dlg, "_lbl"):
                    dlg._lbl.setText(
                        f"Aplicando {len(inserciones)} límites EW/LW…")
                QApplication.processEvents()
            except Exception:
                pass

            self.vista.guardar_en_historial()

            # Insertar en CHUNKS con processEvents() entre cada uno.
            # list.insert es O(n) en el peor caso (shift de elementos),
            # con muchas inserciones se acumula. Procesar eventos cada
            # 100 inserts mantiene la UI viva.
            inv = list(reversed(inserciones))  # de atrás hacia adelante
            CHUNK = 100
            for i in range(0, len(inv), CHUNK):
                for insert_at, px_ins, py_ins in inv[i:i + CHUNK]:
                    self.vista.coordenadas_anillos.insert(
                        insert_at, (px_ins, py_ins))
                    self.vista.es_salto.insert(insert_at, "ew_lw")
                # Permitir que la UI procese eventos pendientes
                # (re-pintar diálogo, atender clicks, etc.)
                QApplication.processEvents()

            # Redibujar es el paso más pesado: crea cientos de items
            # Qt nuevos. No se puede chunkar (necesita estado completo).
            # Pero al menos mostramos qué estamos haciendo para que el
            # usuario no piense que crasheó.
            try:
                if hasattr(dlg, "_lbl"):
                    total = len(self.vista.coordenadas_anillos)
                    dlg._lbl.setText(f"Redibujando {total} puntos…")
                QApplication.processEvents()
            except Exception:
                pass

            self.vista.redibujar_todo()
            self.vista.recalcular_todas_las_distancias()
            self.vista.progreso_modificado.emit()

            # Cerrar el diálogo SOLO cuando todo terminó.
            try:
                dlg.close()
            except Exception:
                pass
            self._ewlw_worker = None
            self.label_estado.setText(
                f"🍂 EW/LW automático: {len(inserciones)} límites insertados. "
                "Usa 🖐 Mover para ajustar."
            )

        worker.signals.resultado.connect(_on_ewlw)

        def _on_cancel():
            """Cancelación segura: setea flag, el worker termina en próxima
            iteración del loop. Mucho más seguro que worker.terminate()."""
            cancel_flag["stop"] = True
            dlg._lbl.setText("Cancelando... espera a que termine la iteración actual.")

        dlg.cancelado.connect(_on_cancel)
        worker.start()
        return

        # — CÓDIGO SÍNCRONO ORIGINAL ELIMINADO (reemplazado por _calcular_ewlw_posiciones) —
        h, w = gray_img.shape
        n = len(coords)
        ppm = self.vista.pixeles_por_mm
        banda_unused = banda  # mantenido para compatibilidad

        # Recopilar anillos: cada anillo es lista de índices entre dos False
        anillos = []
        ini = 0
        for i in range(1, n):
            if saltos[i] is False:
                anillos.append(list(range(ini, i + 1)))
                ini = i
        # Ultimo anillo abierto
        if ini < n - 1:
            anillos.append(list(range(ini, n)))

        self.vista.guardar_en_historial()
        insertar_count = 0

        # Procesar de atrás hacia adelante para no invalidar índices
        for anillo_idxs in reversed(anillos):
            if len(anillo_idxs) < 3:
                continue
            # Verificar que no haya ya un ew_lw en este anillo
            tiene_ewlw = any(
                _es_ewlw(saltos[j]) for j in anillo_idxs[1:]
                if j < len(saltos)
            )
            if tiene_ewlw:
                continue

            # Extraer perfil de intensidad gris a lo largo del anillo
            perfil = []
            posiciones_px = []   # posición acumulada en px
            acum = 0.0
            for k in range(1, len(anillo_idxs)):
                idx_prev = anillo_idxs[k - 1]
                idx_curr = anillo_idxs[k]
                if idx_curr >= n:
                    break
                val_k = saltos[idx_curr] if idx_curr < len(saltos) else False
                if _es_amarillo(val_k):
                    continue  # saltar quiebres/espacios

                x0, y0 = coords[idx_prev]
                x1, y1 = coords[idx_curr]
                seg_len = math.hypot(x1 - x0, y1 - y0)
                if seg_len < 1:
                    continue

                ux = (x1 - x0) / seg_len
                uy = (y1 - y0) / seg_len
                perp_x, perp_y = -uy, ux
                n_s = max(2, int(seg_len))
                ts = np.linspace(0, 1, n_s)

                xs_c = x0 + ts * (x1 - x0)
                ys_c = y0 + ts * (y1 - y0)

                vals_seg = []
                for off in range(-banda, banda + 1):
                    xs = xs_c + off * perp_x
                    ys = ys_c + off * perp_y
                    mask = (xs >= 0) & (xs < w - 1) & (ys >= 0) & (ys < h - 1)
                    xi = xs[mask].astype(int)
                    yi = ys[mask].astype(int)
                    if len(xi):
                        vals_seg.extend(gray[yi, xi].tolist())

                if vals_seg:
                    perfil_seg = np.mean(vals_seg) if vals_seg else 128.0
                else:
                    perfil_seg = 128.0

                # Guardar una muestra por unidad de px a lo largo del segmento
                for j, t_j in enumerate(np.linspace(0, 1, n_s)):
                    px_j = x0 + t_j * (x1 - x0)
                    py_j = y0 + t_j * (y1 - y0)
                    perfil.append(perfil_seg)
                    posiciones_px.append((acum + j, px_j, py_j, idx_prev, idx_curr, t_j))
                acum += n_s

            if len(perfil) < 5:
                continue

            # Segunda derivada del perfil suavizado
            arr = np.array(perfil, dtype=float)
            # Suavizar antes de derivar
            from scipy.signal import savgol_filter
            if len(arr) > 7:
                arr = savgol_filter(arr, min(11, len(arr) if len(arr) % 2 == 1
                                             else len(arr) - 1), 2)
            d2 = np.gradient(np.gradient(arr))
            # El mínimo de d2 = mayor tasa de cambio (paso EW→LW oscuro)
            # Rango asimétrico: 15% desde médula (inicio EW) y 5% desde corteza
            # (fin LW). Esto captura latewood muy delgado sin tocar los límites.
            margen_ini = max(1, int(len(arr) * 0.15))   # 15% lado médula
            margen_fin = max(1, int(len(arr) * 0.05))   # 5%  lado corteza
            d2_sub = d2[margen_ini:-margen_fin]
            if len(d2_sub) == 0:
                continue
            idx_min = int(np.argmin(d2_sub)) + margen_ini
            _, px_ins, py_ins, idx_seg_prev, idx_seg_curr, t_ins = posiciones_px[idx_min]

            # Insertar punto "ew_lw" entre idx_seg_prev e idx_seg_curr
            # (usar idx_seg_curr como posición de inserción)
            insert_at = idx_seg_curr
            self.vista.coordenadas_anillos.insert(insert_at, (px_ins, py_ins))
            self.vista.es_salto.insert(insert_at, "ew_lw")
            insertar_count += 1
            # Actualizar coords/saltos locales para los siguientes anillos
            coords = list(self.vista.coordenadas_anillos)
            saltos = list(self.vista.es_salto)
            n = len(coords)

        self.vista.redibujar_todo()
        self.vista.recalcular_todas_las_distancias()
        self.vista.progreso_modificado.emit()
        self.label_estado.setText(
            f"🍂 EW/LW automático: {insertar_count} límites insertados. "
            "Usa 🖐 Mover para ajustar.")

    def _exportar_ewlw(self, codigo: str, ruta_base: str,
                        coords: list | None = None,
                        saltos: list | None = None,
                        datos_medidos: list | None = None):
        """
        Genera codigo_ew.rwl y codigo_lw.rwl con los anchos de earlywood
        y latewood.

        Si coords/saltos/datos_medidos vienen explícitos, los usa (caso
        común: exportar un radio guardado). Si no vienen, cae al estado
        de la vista activa (caso: exportar el radio en progreso).

        Bug histórico: antes siempre leía self.vista.coordenadas_anillos
        y self.vista.es_salto, ignorando el radio que el usuario estaba
        exportando. Si el usuario exportaba un radio guardado pero la
        vista activa estaba vacía, tiene_ewlw era False y nunca se
        generaban los archivos _ew/_lw.
        """
        # Usar parámetros explícitos si se pasaron, si no caer a la vista
        if coords is None:
            coords = self.vista.coordenadas_anillos
        if saltos is None:
            saltos = self.vista.es_salto
        if datos_medidos is None:
            datos_medidos = self.datos_medidos

        if not datos_medidos:
            return

        n = len(coords)
        ppm = self.vista.pixeles_por_mm
        med_a_cor = self.combo_dir.currentIndex() == 0

        anios_ew, anchos_ew = [], []
        anios_lw, anchos_lw = [], []

        acum_ew = 0.0
        acum_lw = 0.0
        en_lw = False
        anio_actual = datos_medidos[0]["anio"] if datos_medidos else 0
        anillo_idx = 0

        for i in range(1, n):
            val = saltos[i]
            d = math.hypot(coords[i][0]-coords[i-1][0],
                           coords[i][1]-coords[i-1][1]) / ppm

            if val is True:  # salto
                acum_ew = acum_lw = 0.0
                en_lw = False
                continue
            if _es_espacio(val):
                continue
            if _es_ewlw(val):
                en_lw = True
                if d > 0:
                    acum_lw = 0.0  # reiniciar LW desde aquí
                continue
            if val is False:
                # Fin de anillo
                if en_lw:
                    acum_lw += d
                else:
                    acum_ew += d

                # ¿El siguiente es un límite de anillo o fin?
                val_next = saltos[i + 1] if i + 1 < n else False
                if val_next is False or i == n - 1:
                    if anillo_idx < len(datos_medidos):
                        anio = datos_medidos[anillo_idx]["anio"]
                        anios_ew.append(anio)
                        anchos_ew.append(acum_ew)
                        anios_lw.append(anio)
                        anchos_lw.append(acum_lw)
                        anillo_idx += 1
                    acum_ew = acum_lw = 0.0
                    en_lw = False
                else:
                    if en_lw:
                        acum_lw += d
                    else:
                        acum_ew += d
                continue

            # Tramo normal (también acumula según estado EW/LW)
            if en_lw:
                acum_lw += d
            else:
                acum_ew += d

        if not anios_ew:
            return

        import os
        dir_base = os.path.dirname(ruta_base)
        nombre_base = os.path.splitext(os.path.basename(ruta_base))[0]

        from constantes import TUCSON_MAX_CHARS_ID

        for sufijo, anios, anchos in [("ew", anios_ew, anchos_ew),
                                       ("lw", anios_lw, anchos_lw)]:
            ruta_rwl = os.path.join(dir_base, f"{nombre_base}_{sufijo}.rwl")
            try:
                mediciones_int = [int(round(a * 1000)) for a in anchos]
                ExportadorDendro._escribir_tucson(
                    f"{codigo[:7]}{sufijo[0]}", anios[0], mediciones_int, ruta_rwl
                )
            except Exception as exc:
                logger.warning("Error escribiendo %s: %s", ruta_rwl, exc)

    # -------------------------------------------------------------------------
    # Blue / Red / Green Intensity
    # -------------------------------------------------------------------------

    def _configurar_bi(self):
        """Abre el diálogo de configuración de la extracción de intensidad."""
        dlg = DialogoConfigBI(
            banda_actual=self._bi_banda,
            canales_activos=self._bi_canales,
            porcentaje=self._bi_porcentaje,
            alineacion=self._bi_alineacion,
            canal_vis=self._bi_canal_vis,
            parent=self,
        )
        if dlg.exec():
            self._bi_banda       = dlg.banda
            self._bi_canales     = dlg.canales
            self._bi_porcentaje  = dlg.porcentaje
            self._bi_alineacion  = dlg.alineacion
            self._bi_canal_vis   = dlg.canal_vis
            # Redibujar banda si está activa
            if self.btn_mostrar_banda.isChecked():
                self._dibujar_banda_bi()

    def _calcular_bi(self):
        """Calcula BI/GI/RI en hilo separado con barra de progreso."""
        if not self.datos_medidos:
            QMessageBox.information(self, "Sin mediciones",
                "Mide al menos un anillo antes de calcular la intensidad.")
            return

        # Protección contra doble lanzamiento (mismo problema que en EW/LW:
        # múltiples workers concurrentes con copias de la imagen llevan a OOM
        # y posible crash del sistema).
        worker_previo = getattr(self, "_bi_worker", None)
        if worker_previo is not None and worker_previo.isRunning():
            QMessageBox.information(
                self, "Cálculo en curso",
                "Ya se está ejecutando un cálculo de intensidad.\n"
                "Espera a que termine.")
            return

        # Asegurar que haya canales activos (si el usuario no configuró, usar todos)
        if not self._bi_canales:
            self._bi_canales = ["B", "G", "R", "Gray"]

        ruta = getattr(self, "ruta_imagen_actual", None)
        if not ruta:
            QMessageBox.warning(self, "Sin imagen",
                "No hay imagen cargada para extraer la intensidad.")
            return

        # Usar imagen cacheada si está disponible — evita re-leer del disco
        img_bgr = self._img_bgr_original
        if img_bgr is None:
            img_bgr = cv2.imread(ruta, cv2.IMREAD_COLOR)
            if img_bgr is None:
                QMessageBox.critical(self, "Error",
                    "No se pudo leer la imagen para extraer la intensidad.")
                return

        # Capturar estado en este momento (el worker corre en otro hilo)
        coords   = list(self.vista.coordenadas_anillos)
        saltos   = list(self.vista.es_salto)
        ppm      = self.vista.pixeles_por_mm
        banda    = self._bi_banda
        canales  = list(self._bi_canales)
        n_anillos = len(self.datos_medidos)

        dlg = _DialogoProgreso("Calculando intensidad de color", parent=self)
        dlg.show()

        pct_calculo  = self._bi_porcentaje / 100.0
        alin_calculo = self._bi_alineacion

        def _tarea(sig_prog, img, c, s, p, b, ch, pct, alin):
            return extraer_intensidad_por_anillo(
                signal_progreso=sig_prog,
                imagen_bgr=img,
                coordenadas=c,
                es_salto=s,
                pixeles_por_mm=p,
                banda_px=b,
                canales=ch,
                porcentaje=pct,
                alineacion=alin,
            )

        worker = _Worker(_tarea, img_bgr, coords, saltos, ppm, banda, canales,
                          pct_calculo, alin_calculo)
        self._bi_worker = worker  # evitar que el GC lo destruya

        worker.signals.progreso.connect(dlg.actualizar)

        def _on_error(e):
            dlg.close()
            self._bi_worker = None
            QMessageBox.critical(self, "Error al calcular BI", e)
        worker.signals.error.connect(_on_error)

        def _on_resultado(resultados):
            dlg.close()
            self._bi_worker = None  # liberar referencia
            for k in range(min(len(resultados), n_anillos)):
                resultados[k]["anio"] = self.datos_medidos[k]["anio"]
            self._bi_datos = resultados
            self.btn_guardar_bi.setEnabled(bool(resultados))
            self.btn_graficar_bi.setEnabled(bool(resultados))
            self.label_estado.setText(
                f"🎨 {len(resultados)} anillos · banda {banda}px · "
                f"canales: {', '.join(canales)}")

        worker.signals.resultado.connect(_on_resultado)

        def _on_cancel():
            """Cancelación segura: cierra el diálogo y desconecta callbacks,
            pero deja al worker terminar en silencio. NO usa terminate()
            porque interrumpir un thread con cv2/numpy en pleno cálculo
            puede corromper memoria o reiniciar el sistema."""
            try:
                worker.signals.resultado.disconnect()
                worker.signals.error.disconnect()
                worker.signals.progreso.disconnect()
            except (TypeError, RuntimeError):
                pass
            dlg.close()
            self.label_estado.setText(
                "Cálculo BI cancelado (terminará silenciosamente en background).")

        dlg.cancelado.connect(_on_cancel)
        worker.start()

    # ── Banda de muestreo BI sobre la imagen ─────────────────────────────
    def _toggle_banda_bi(self, activo: bool):
        """Muestra u oculta la banda de muestreo BI sobre la imagen."""
        if activo:
            self._dibujar_banda_bi()
        else:
            self._limpiar_banda_bi()

    def _dibujar_banda_bi(self):
        """
        Dibuja el área de muestreo BI sobre la imagen.

        Para cada anillo (entre dos límites False consecutivos):
          - Dibuja un polígono por columna de píxeles, coloreado con
            la intensidad real del canal elegido.
          - Sigue la curvatura exacta del camino.
          - Respeta el porcentaje y alineación configurados.
        """
        self._limpiar_banda_bi()

        ruta = getattr(self, "ruta_imagen_actual", None)
        if not ruta:
            return
        img_bgr = cv2.imread(ruta, cv2.IMREAD_COLOR)
        if img_bgr is None:
            return

        import cv2 as _cv2
        from PyQt6.QtGui import QPolygonF, QColor as QC
        from PyQt6.QtCore import QPointF

        canal = self._bi_canal_vis
        if canal == "B":
            ch_img = img_bgr[:, :, 0].astype(np.float32)
            def _rgba(v): return QC(int(v*0.25), int(v*0.55), int(v), 210)
        elif canal == "G":
            ch_img = img_bgr[:, :, 1].astype(np.float32)
            def _rgba(v): return QC(0, int(v), 0, 210)
        elif canal == "R":
            ch_img = img_bgr[:, :, 2].astype(np.float32)
            def _rgba(v): return QC(int(v), 0, 0, 210)
        else:
            ch_img = _cv2.cvtColor(img_bgr, _cv2.COLOR_BGR2GRAY).astype(np.float32)
            def _rgba(v): return QC(int(v), int(v), int(v), 210)

        h_img, w_img = ch_img.shape

        color_borde = {"B": "#4488FF", "G": "#44BB44",
                       "R": "#FF4444", "Gray": "#AAAAAA"}[canal]
        pen_borde = QPen(QColor(color_borde))
        pen_borde.setCosmetic(True)
        pen_borde.setWidthF(1.0)
        pen_sin = QPen(Qt.PenStyle.NoPen)

        coords = self.vista.coordenadas_anillos
        saltos = self.vista.es_salto
        banda  = self._bi_banda
        pct    = self._bi_porcentaje / 100.0
        alin   = self._bi_alineacion
        n      = len(coords)
        if n < 2:
            return

        escena = self.vista.scene()
        items  = []

        # ── Recopilar anillos ────────────────────────────────────────────
        # Un anillo es la lista de segmentos entre dos límites False
        def _recopilar_anillos():
            """
            Cada anillo = todos los segmentos entre dos límites False.
            Los segmentos ew_lw son parte del anillo (no lo cierran).
            Los quiebres/espacios se saltan pero no cierran el anillo.
            Los saltos reales (True) descartan el acumulado.
            """
            anillos = []
            segs = []
            for i in range(1, n):
                val = saltos[i] if i < len(saltos) else False
                if val is True:        # salto real: descartar y reiniciar
                    segs = []
                    continue
                if _es_espacio(val):   # quiebre/espacio: no cuenta como segmento
                    continue
                # Segmento normal (False) o ew_lw: añadir al anillo actual
                segs.append((i-1, i))
                # Solo cerrar el anillo en el límite final (val is False)
                # ew_lw NO cierra el anillo — es interno
                if val is False:
                    if segs:
                        anillos.append(list(segs))
                    segs = []
                # Nota: val == "ew_lw" → continúa acumulando en el mismo anillo
            if segs:
                anillos.append(segs)
            return anillos

        anillos = _recopilar_anillos()

        for segs in anillos:
            if not segs:
                continue

            # Calcular longitud total
            long_total = sum(
                math.hypot(coords[ic][0]-coords[ip][0],
                           coords[ic][1]-coords[ip][1])
                for ip, ic in segs
            )
            if long_total < 2:
                continue

            # Rango de longitud a visualizar según alineación
            recorte = 1.0 - pct
            if alin == "ew":
                l_ini, l_fin = 0.0, long_total * pct
            elif alin == "lw":
                l_ini, l_fin = long_total * recorte, long_total
            else:
                m = recorte / 2.0
                l_ini, l_fin = long_total * m, long_total * (1.0 - m)

            # ── Construir columnas pixel a pixel ─────────────────────────
            # Paso 1: recopilar todos los valores y posiciones del anillo
            cols_vals = []   # valor medio del canal en cada columna
            cols_pts  = []   # (sup, inf) QPointF para cada columna
            cols_activa = [] # bool: está dentro del rango l_ini..l_fin

            acum = 0.0
            for ip, ic in segs:
                x0, y0 = coords[ip]
                x1, y1 = coords[ic]
                seg_len = math.hypot(x1-x0, y1-y0)
                if seg_len < 1:
                    continue
                ux = (x1-x0)/seg_len; uy = (y1-y0)/seg_len
                perp_x, perp_y = -uy, ux

                n_s = max(2, int(round(seg_len)))
                for j in range(n_s):
                    t_j   = j / max(1, n_s-1)
                    pos_j = acum + j * (seg_len / max(1, n_s-1))
                    cx = x0 + t_j*(x1-x0)
                    cy = y0 + t_j*(y1-y0)
                    sup = QPointF(cx + banda*perp_x, cy + banda*perp_y)
                    inf = QPointF(cx - banda*perp_x, cy - banda*perp_y)

                    activa = l_ini <= pos_j <= l_fin
                    if activa:
                        offs = np.arange(-banda, banda+1)
                        xs = np.clip(cx + offs*perp_x, 0, w_img-1).astype(np.int32)
                        ys = np.clip(cy + offs*perp_y, 0, h_img-1).astype(np.int32)
                        cols_vals.append(float(ch_img[ys, xs].mean()))
                    else:
                        cols_vals.append(None)
                    cols_pts.append((sup, inf))
                    cols_activa.append(activa)
                acum += seg_len

            if not any(cols_activa):
                continue

            # Paso 2: normalizar al rango del anillo para maximizar contraste
            vals_activos = [v for v in cols_vals if v is not None]
            v_min = min(vals_activos)
            v_max = max(vals_activos)
            v_rng = v_max - v_min if v_max > v_min else 1.0

            # Paso 3: dibujar polígono por columna con color normalizado
            prev_pts = None
            for k, (sup, inf) in enumerate(cols_pts):
                if not cols_activa[k]:
                    prev_pts = None
                    continue
                # Normalizar al rango 40-255 para que siempre se vea algo
                v_norm = 40 + int(215 * (cols_vals[k] - v_min) / v_rng)
                color = _rgba(v_norm)
                brush = QBrush(color)

                if prev_pts is not None:
                    p_sup_ant, p_inf_ant = prev_pts
                    poly = QPolygonF([p_sup_ant, sup, inf, p_inf_ant])
                    item = escena.addPolygon(poly, pen_sin, brush)
                    item.setZValue(5)
                    items.append(item)
                prev_pts = (sup, inf)

            # Borde exterior del área (polígono de contorno)
            # Reconstruir los puntos del borde para el contorno
            acum = 0.0
            pts_sup, pts_inf = [], []
            for ip, ic in segs:
                x0, y0 = coords[ip]
                x1, y1 = coords[ic]
                seg_len = math.hypot(x1-x0, y1-y0)
                if seg_len < 1:
                    continue
                ux = (x1-x0)/seg_len; uy = (y1-y0)/seg_len
                perp_x, perp_y = -uy, ux
                n_s = max(2, int(round(seg_len)))
                for j in range(n_s):
                    t_j   = j / max(1, n_s-1)
                    pos_j = acum + j*(seg_len/max(1, n_s-1))
                    if pos_j < l_ini or pos_j > l_fin:
                        continue
                    cx = x0 + t_j*(x1-x0)
                    cy = y0 + t_j*(y1-y0)
                    pts_sup.append(QPointF(cx + banda*perp_x, cy + banda*perp_y))
                    pts_inf.append(QPointF(cx - banda*perp_x, cy - banda*perp_y))
                acum += seg_len

            if len(pts_sup) >= 2:
                poly_borde = QPolygonF(pts_sup + list(reversed(pts_inf)))
                borde = escena.addPolygon(
                    poly_borde, pen_borde, QBrush(Qt.BrushStyle.NoBrush))
                borde.setZValue(6)
                items.append(borde)

        self._items_banda_bi = items

    def _limpiar_banda_bi(self):
        """Elimina los marcadores de banda del escenario."""
        for item in getattr(self, "_items_banda_bi", []):
            try:
                self.vista.scene().removeItem(item)
            except Exception:
                pass
        self._items_banda_bi = []

    def _graficar_bi_ventana(self):
        """Abre una ventana independiente con el gráfico de BI/GI/RI con zoom."""
        if not self._bi_datos:
            return

        anios = [d.get("anio", i) for i, d in enumerate(self._bi_datos)]
        colores = {"B": "#4488FF", "G": "#44BB44", "R": "#FF4444", "Gray": "#AAAAAA"}
        estilos = {
            "mean": (Qt.PenStyle.SolidLine,  "Promedio"),
            "min":  (Qt.PenStyle.DashLine,   "Mínimo"),
            "max":  (Qt.PenStyle.DotLine,    "Máximo"),
        }

        win = QDialog(self)
        win.setWindowTitle("Intensidad de color — BI/GI/RI/GrayI")
        win.resize(900, 450)
        lay = QVBoxLayout(win)

        pw = pg.PlotWidget()
        pw.showGrid(x=True, y=True, alpha=0.3)
        pw.setLabel("bottom", "Año")
        pw.setLabel("left",   "Intensidad (0-255)")
        pw.setYRange(0, 255, padding=0.05)
        pw.addLegend(offset=(10, 10))
        pw.setMouseEnabled(x=True, y=True)
        lay.addWidget(pw)

        from PyQt6.QtWidgets import QCheckBox, QGroupBox, QHBoxLayout as QHL
        chks_canal: dict[str, QCheckBox] = {}
        chks_met:   dict[str, QCheckBox] = {}

        def _reconstruir():
            pw.clear()
            pw.addLegend(offset=(10, 10))
            for canal, chk_c in chks_canal.items():
                if not chk_c.isChecked():
                    continue
                color = colores.get(canal, "#888888")
                for met, (estilo, etiq) in estilos.items():
                    if not chks_met[met].isChecked():
                        continue
                    key = f"{canal}_{met}"
                    vals = [d.get(key) for d in self._bi_datos if key in d]
                    if not vals or len(vals) != len(anios):
                        continue
                    pw.plot(anios, vals,
                            pen=pg.mkPen(color,
                                         width=2.0 if met == "mean" else 1.0,
                                         style=estilo),
                            name=f"{canal} {etiq}")

        fila = QHL()
        grp_c = QGroupBox("Canales")
        lay_c = QHL(grp_c)
        for canal in ["B", "G", "R", "Gray"]:
            chk = QCheckBox(canal)
            chk.setChecked(canal in self._bi_canales)
            chk.toggled.connect(_reconstruir)
            lay_c.addWidget(chk)
            chks_canal[canal] = chk
        fila.addWidget(grp_c)

        grp_m = QGroupBox("Métricas")
        lay_m = QHL(grp_m)
        for met, etiq in [("mean","Promedio"), ("min","Mínimo"), ("max","Máximo")]:
            chk = QCheckBox(etiq)
            chk.setChecked(True)
            chk.toggled.connect(_reconstruir)
            lay_m.addWidget(chk)
            chks_met[met] = chk
        fila.addWidget(grp_m)
        fila.addStretch()
        lay.addLayout(fila)

        _reconstruir()
        win.show()
        self._bi_ventana = win   # evitar GC

    def _guardar_bi(self):
        """
        Abre el diálogo de selección de métricas y guarda en el formato
        elegido por la extensión del archivo:
          • .rwl       → un archivo Tucson por canal×métrica
          • .xlsx      → un Excel multi-columna con todas las series
          • .csv/.txt  → un texto multi-columna con coma decimal
        """
        if not self._bi_datos:
            QMessageBox.information(self, "Sin datos",
                "Primero calcula la intensidad con el botón 🎨 Calcular BI.")
            return
 
        # 1. Diálogo de selección de canales y métricas
        dlg = DialogoGuardarBI(
            canales_calculados=self._bi_canales,
            parent=self,
        )
        if dlg.exec() != DialogoGuardarBI.DialogCode.Accepted:
            return
 
        seleccion = dlg.seleccion
        if not seleccion:
            QMessageBox.information(self, "Sin selección",
                "No se seleccionó ningún canal/métrica para exportar.")
            return
 
        # 2. Selector de archivo con formato
        codigo = self.input_codigo.text().strip() or "serie"
        ruta_default = os.path.join(_ultima_carpeta(), f"{codigo}_bi.txt")
 
        ruta, filtro = QFileDialog.getSaveFileName(
            self, "Guardar intensidad de color",
            ruta_default,
            "Texto multi-columna (*.txt);;"
            "CSV multi-columna (*.csv);;"
            "Excel multi-columna (*.xlsx);;"
            "Tucson — un archivo por canal (*.rwl)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not ruta:
            return
 
        _ultima_carpeta(ruta)
        ext = os.path.splitext(ruta)[1].lower()
        if not ext:
            # Si no escribieron extensión, deducir del filtro
            if "rwl" in filtro:
                ruta += ".rwl"
                ext = ".rwl"
            elif "csv" in filtro:
                ruta += ".csv"
                ext = ".csv"
            elif "xlsx" in filtro:
                ruta += ".xlsx"
                ext = ".xlsx"
            else:
                ruta += ".txt"
                ext = ".txt"
 
        anios = [d.get("anio", i) for i, d in enumerate(self._bi_datos)]
        if not anios:
            return
 
        try:
            if ext == ".rwl":
                self._exportar_bi_tucson(ruta, anios, seleccion)
            else:
                self._exportar_bi_multicolumna(ruta, ext, anios, seleccion, codigo)
        except Exception as exc:
            logger.error("Error al exportar BI: %s", exc)
            QMessageBox.critical(self, "Error",
                                 f"Fallo al exportar:\n{exc}")
 
    def _exportar_bi_tucson(self, ruta: str, anios: list, seleccion: list):
        """Genera un .rwl Tucson por cada (canal, métrica)."""
        codigo = self.input_codigo.text().strip() or "serie"
        directorio = os.path.dirname(ruta)
        base = os.path.splitext(os.path.basename(ruta))[0]
        # Si terminaba en "_bi", quitarlo para evitar duplicación
        if base.lower().endswith("_bi"):
            base = base[:-3]
 
        escritos = []
        errores = []
 
        for canal, met in seleccion:
            if canal in ("B_R", "B_G"):
                canal2 = "R" if canal == "B_R" else "G"
                vals_b = [d.get("B_mean", 0.0) for d in self._bi_datos]
                vals_x = [d.get(f"{canal2}_mean", 1.0) for d in self._bi_datos]
                vals_ratio = [b / x if x > 0 else 0.0
                              for b, x in zip(vals_b, vals_x)]
                r_max = max(vals_ratio) if vals_ratio else 1.0
                vals_norm = [v / r_max for v in vals_ratio]
                suf = canal.lower().replace("_", "div")
            else:
                key = f"{canal}_{met}"
                vals = [d.get(key, 0.0) for d in self._bi_datos]
                vals_norm = [v / 255.0 for v in vals]
                suf = f"{canal.lower()}_{met}"
 
            nombre_archivo = f"{base}_{suf}.rwl"
            ruta_rwl = os.path.join(directorio, nombre_archivo)
            try:
                escribir_rwl_intensidad(
                    codigo=f"{codigo[:6]}{canal[:1].lower()}{met[:1] if met else ''}",
                    anio_inicio=anios[0],
                    valores=vals_norm,
                    ruta=ruta_rwl,
                )
                escritos.append(nombre_archivo)
            except Exception as exc:
                errores.append(f"{nombre_archivo}: {exc}")
 
        msg = f"✅ Guardados {len(escritos)} archivos Tucson en:\n{directorio}"
        if escritos:
            msg += "\n\n" + "\n".join(f"  • {f}" for f in escritos)
        if errores:
            msg += "\n\n⚠ Errores:\n" + "\n".join(errores)
        QMessageBox.information(self, "Guardado completado", msg)
 
    def _exportar_bi_multicolumna(self, ruta: str, ext: str, anios: list,
                                    seleccion: list, codigo: str):
        """Genera un solo archivo Excel/CSV/TXT con todas las series como columnas."""
        # Construir DataFrame con una columna por cada (canal, métrica)
        data = {"Anio": anios}
        for canal, met in seleccion:
            if canal in ("B_R", "B_G"):
                canal2 = "R" if canal == "B_R" else "G"
                vals_b = [d.get("B_mean", 0.0) for d in self._bi_datos]
                vals_x = [d.get(f"{canal2}_mean", 1.0) for d in self._bi_datos]
                col_name = f"{canal2}div"  # rdiv, gdiv
                col_label = f"B_{canal2}_ratio"
                vals = [b / x if x > 0 else 0.0
                        for b, x in zip(vals_b, vals_x)]
                data[col_label] = vals
            else:
                key = f"{canal}_{met}"
                vals = [d.get(key, 0.0) for d in self._bi_datos]
                col_label = f"{canal}_{met}"
                data[col_label] = vals
 
        df = pd.DataFrame(data)
 
        if ext == ".xlsx":
            df.to_excel(ruta, index=False)
        elif ext == ".csv":
            # CSV chileno: separador ; y decimal con coma
            df.to_csv(ruta, index=False, sep=";", decimal=",",
                      float_format="%.6f")
        else:  # .txt
            df.to_csv(ruta, index=False, sep="\t", decimal=",",
                      float_format="%.6f")
 
        n_cols = len(df.columns) - 1  # restar la columna Anio
        QMessageBox.information(
            self, "✅ Exportación completada",
            f"Archivo guardado:\n{ruta}\n\n"
            f"Formato: multi-columna ({n_cols} series)\n"
            f"Filas: {len(df)} años\n"
            f"Columnas: Anio + {n_cols} canales/métricas"
        )
 

    def _ruta_sidecar(self, ruta_rwl: str | None = None) -> str | None:
        """Devuelve la ruta del archivo sidecar (oculto) para el radio exportado más reciente."""
        ruta = ruta_rwl or getattr(self, '_ultima_ruta_exportada', None)
        if not ruta:
            return None
        directorio = os.path.dirname(ruta)
        nombre = os.path.basename(ruta)
        return os.path.join(directorio, f".{nombre}.anom.json")

    def _guardar_sidecar_anom(self, ruta_rwl: str | None = None):
        """Escribe el .anom.json con anomalías + metadata del radio activo."""
        ruta_s = self._ruta_sidecar(ruta_rwl)
        if not ruta_s:
            return  # No hay ruta — silencioso (serie aún no exportada)
        try:
            datos = {
                "code":    self.input_codigo.text().strip(),
                "medula":  self.check_tiene_medula.isChecked(),
                "corteza": self.check_tiene_corteza.isChecked(),
                "anomalias": {
                    str(anio): tipo
                    for anio, tipo in self.vista.anomalias.items()
                },
            }
            # Agregar datos de estimación de médula si existen
            pith_est = getattr(self, "_pith_estimacion", None)
            if pith_est:
                datos.update(pith_est)
            with open(ruta_s, "w", encoding="utf-8") as fh:
                json.dump(datos, fh, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("No se pudo guardar sidecar: %s", e)

    def _cargar_sidecar_anom(self, ruta_rwl: str):
        """Lee el .anom.json (oculto) y carga anomalías + checkboxes si existe."""
        directorio = os.path.dirname(ruta_rwl)
        nombre = os.path.basename(ruta_rwl)
        ruta_s = os.path.join(directorio, f".{nombre}.anom.json")
        # Retrocompatibilidad: buscar formato anterior (no oculto)
        if not os.path.exists(ruta_s):
            ruta_legacy = ruta_rwl + ".anom.json"
            if os.path.exists(ruta_legacy):
                ruta_s = ruta_legacy
            else:
                return
        try:
            with open(ruta_s, "r", encoding="utf-8") as fh:
                datos = json.load(fh)
            self.check_tiene_medula.setChecked(bool(datos.get("medula", False)))
            self.check_tiene_corteza.setChecked(bool(datos.get("corteza", False)))
            self.vista.anomalias = {
                int(k): v for k, v in datos.get("anomalias", {}).items()
            }
            self.vista._dibujar_anomalias()
            # Restaurar datos de estimación de médula si existen
            if datos.get("pith_estimated"):
                self._pith_estimacion = {
                    k: datos[k] for k in datos
                    if k.startswith("pith_")
                }
                # Reconstruir undo para permitir revertir
                offset = datos.get("pith_offset", 0)
                if offset > 0:
                    med_a_cor = self.combo_dir.currentIndex() == 0
                    self._pith_undo = {
                        "med_a_cor":        med_a_cor,
                        "anio_medula_old":  self.spin_medula.value() + (offset if med_a_cor else 0),
                        "anio_corteza_old": self.spin_corteza.value() - (offset if not med_a_cor else 0),
                        "medula_checked":   False,
                        "n_anillos":        offset,
                    }
                    self.btn_deshacer_pith.show()
        except Exception as e:
            logger.warning("No se pudo cargar sidecar: %s", e)

    def _anomalias_modificadas(self):
        """Llamado desde la vista cuando el usuario cambia una anomalía."""
        self._guardar_sidecar_anom()

