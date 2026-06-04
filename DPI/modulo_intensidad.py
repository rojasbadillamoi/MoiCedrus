"""
modulo_intensidad.py — Extracción de Blue/Red/Green Intensity (BI/RI/GI)

Implementa el método de Maxwell et al. (2017) y CooRecorder:
  - Recorre el camino medido segmento a segmento
  - Promedia los píxeles del canal en una banda perpendicular al camino
  - Agrega por anillo: mean, min, max de cada canal (R, G, B)
  - Exporta como .rwl estándar (valores escalados 0-255 → × 1000)
  - Los quiebres (es_salto="espacio"/"quiebre_ini") se excluyen del promedio
    pero no cortan el anillo

Referencia:
  Maxwell, J.T. et al. (2017). Blue intensity: A pilot study to develop
  a new proxy for reconstructing past climate from tree rings.
  Dendrochronologia, 44, 1-7.
"""

import math
import logging
import numpy as np

logger = logging.getLogger(__name__)

# Canales disponibles
CANALES = {
    "Blue":  0,   # índice en BGR de OpenCV
    "Green": 1,
    "Red":   2,
    "Gray":  None,   # canal gris = promedio ponderado luminancia
}


# =============================================================================
# EXTRACCIÓN DE INTENSIDAD POR ANILLO
# =============================================================================

def extraer_intensidad_por_anillo(
    signal_progreso,            # Signal(int, str) o None
    imagen_bgr: np.ndarray,
    coordenadas: list,
    es_salto: list,
    pixeles_por_mm: float,
    banda_px: int = 10,
    canales: list | None = None,
    porcentaje: float = 1.0,    # fracción del anillo a medir (0-1)
    alineacion: str = "centro", # "ew" | "centro" | "lw"
) -> list[dict]:
    """
    Extrae B/G/R/Gray Intensity por anillo de forma eficiente en memoria.

    Usa aritmética vectorizada NumPy: en lugar de acumular listas de píxeles,
    calcula mean/min/max por segmento y los combina con promedio ponderado
    por longitud. Reduce el uso de RAM en 10-50x respecto a acumular píxeles.

    signal_progreso: Signal(int, str) de Qt o None. Emite avance por anillo.
    canales: lista de 'B','G','R','Gray' a calcular. None = todos.
    """
    import cv2 as _cv2

    if canales is None:
        canales = ["B", "G", "R", "Gray"]

    h, w = imagen_bgr.shape[:2]
    n = len(coordenadas)
    if n < 2:
        return []

    # Preparar canales solicitados como arrays contiguos
    ch_arrays = {}
    if "B" in canales:    ch_arrays["B"]    = np.ascontiguousarray(imagen_bgr[:, :, 0])
    if "G" in canales:    ch_arrays["G"]    = np.ascontiguousarray(imagen_bgr[:, :, 1])
    if "R" in canales:    ch_arrays["R"]    = np.ascontiguousarray(imagen_bgr[:, :, 2])
    if "Gray" in canales: ch_arrays["Gray"] = _cv2.cvtColor(imagen_bgr, _cv2.COLOR_BGR2GRAY)

    resultados = []

    # Acumuladores por anillo: suma ponderada y conteo de píxeles
    # Guardar (sum, sum_sq, min_val, max_val, count) por canal
    def _nuevo_acum():
        return {ch: [0.0, float('inf'), float('-inf'), 0] for ch in ch_arrays}
        # [suma, min, max, count]

    def _cerrar_anillo(acum):
        if not any(v[3] > 0 for v in acum.values()):
            return
        entry = {}
        for ch, (s, mn, mx, cnt) in acum.items():
            entry[f"{ch}_mean"] = s / cnt if cnt > 0 else 0.0
            entry[f"{ch}_min"]  = mn
            entry[f"{ch}_max"]  = mx
        resultados.append(entry)

    def _muestrar_segmento(x0, y0, x1, y1, acum):
        """Muestrea la banda perpendicular al segmento y actualiza acum."""
        seg_len = math.hypot(x1 - x0, y1 - y0)
        if seg_len < 1:
            return
        ux = (x1 - x0) / seg_len
        uy = (y1 - y0) / seg_len
        perp_x, perp_y = -uy, ux

        n_s = max(2, int(seg_len))
        ts = np.linspace(0.0, 1.0, n_s)
        xs_c = (x0 + ts * (x1 - x0)).reshape(1, -1)  # (1, n_s)
        ys_c = (y0 + ts * (y1 - y0)).reshape(1, -1)

        offs = np.arange(-banda_px, banda_px + 1).reshape(-1, 1)  # (2b+1, 1)
        xs_all = (xs_c + offs * perp_x).ravel()
        ys_all = (ys_c + offs * perp_y).ravel()

        mask = ((xs_all >= 0) & (xs_all < w - 1) &
                (ys_all >= 0) & (ys_all < h - 1))
        xi = xs_all[mask].astype(np.int32)
        yi = ys_all[mask].astype(np.int32)
        if len(xi) == 0:
            return

        for ch, arr in ch_arrays.items():
            vals = arr[yi, xi].astype(np.float32)
            s, mn, mx, cnt = acum[ch]
            acum[ch] = [s + float(vals.sum()),
                        min(mn, float(vals.min())),
                        max(mx, float(vals.max())),
                        cnt + len(vals)]

    acum = _nuevo_acum()
    n_anillos = sum(1 for v in es_salto if v is False)
    anillo_num = 0

    # Pre-calcular longitudes de cada anillo para el recorte por porcentaje
    # Recorrer primero para saber la longitud total de cada anillo
    # Construir lista de anillos: cada anillo = list of (i, val)
    def _long_anillo_desde(idx_ini):
        """Longitud total del anillo que empieza en idx_ini."""
        total = 0.0
        for j in range(idx_ini + 1, n):
            if es_salto[j] is True:
                break
            if _es_espacio_sal(es_salto[j]):
                continue
            xp, yp = coordenadas[j - 1]
            xc, yc = coordenadas[j]
            total += math.hypot(xc-xp, yc-yp)
            if es_salto[j] is False:
                break
        return total

    ini_anillo = 0
    acum_en_anillo = 0.0
    long_anillo_actual = _long_anillo_desde(0)
    # Calcular rango válido para el primer anillo
    recorte = 1.0 - porcentaje
    def _rango(long_tot):
        if alineacion == "ew":
            return 0.0, long_tot * porcentaje
        elif alineacion == "lw":
            return long_tot * recorte, long_tot
        else:
            m = recorte / 2.0
            return long_tot * m, long_tot * (1.0 - m)

    long_ini_val, long_fin_val = _rango(long_anillo_actual)

    for i in range(1, n):
        val = es_salto[i]

        if val is True:
            _cerrar_anillo(acum)
            acum = _nuevo_acum()
            acum_en_anillo = 0.0
            ini_anillo = i
            long_anillo_actual = _long_anillo_desde(i)
            long_ini_val, long_fin_val = _rango(long_anillo_actual)
            continue

        if _es_espacio_sal(val):
            continue

        x0, y0 = coordenadas[i - 1]
        x1, y1 = coordenadas[i]
        seg_len = math.hypot(x1-x0, y1-y0)

        # Solo muestrear si el segmento cae dentro del rango válido
        seg_ini = acum_en_anillo
        seg_fin = acum_en_anillo + seg_len
        if seg_fin >= long_ini_val and seg_ini <= long_fin_val:
            _muestrar_segmento(x0, y0, x1, y1, acum)

        acum_en_anillo += seg_len

        val_next = es_salto[i + 1] if i + 1 < n else False
        if val_next is False or val_next is True:
            _cerrar_anillo(acum)
            acum = _nuevo_acum()
            acum_en_anillo = 0.0
            anillo_num += 1
            if signal_progreso is not None and n_anillos > 0:
                pct_prog = int(anillo_num * 100 / n_anillos)
                signal_progreso.emit(pct_prog,
                    f"Anillo {anillo_num}/{n_anillos} · banda {banda_px}px")
            if i + 1 < n:
                long_anillo_actual = _long_anillo_desde(i)
                long_ini_val, long_fin_val = _rango(long_anillo_actual)

    _cerrar_anillo(acum)
    return resultados


def _es_amarillo_sal(val) -> bool:
    return val is True or val == "espacio" or val == "quiebre_ini"

def _es_espacio_sal(val) -> bool:
    return val == "espacio" or val == "quiebre_ini"


# =============================================================================
# EXPORTACIÓN EN FORMATO TUCSON (.rwl)
# =============================================================================

def escribir_rwl_intensidad(
    codigo: str,
    anio_inicio: int,
    valores: list[float],
    ruta: str,
):
    """
    Escribe un archivo .rwl con valores de intensidad (0-255) escalados × 1000.
    Compatible con COFECHA y otros programas dendro que leen Tucson.

    Los valores se multiplican por 1000 y se redondean a entero,
    igual que CooRecorder exporta BI.
    """
    from constantes import TUCSON_VALOR_FIN, TUCSON_MAX_CHARS_ID

    codigo = codigo[:TUCSON_MAX_CHARS_ID]
    mods = [int(round(v * 1000)) for v in valores]
    n = len(mods)
    lineas: list[str] = []
    idx = 0

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

    while idx < n:
        cantidad = min(10, n - idx)
        fila = f"{codigo:8s}{anio_inicio + idx:4d}"
        for v in mods[idx: idx + cantidad]:
            fila += f"{v:6d}"
        lineas.append(fila)
        idx += cantidad

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
# DIÁLOGO DE CONFIGURACIÓN
# =============================================================================

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QSpinBox,
    QDialogButtonBox, QLabel, QGroupBox, QCheckBox, QHBoxLayout,
    QFileDialog, QMessageBox,
)

class DialogoConfigBI(QDialog):
    """Configuración de la extracción de intensidad de color."""

    def __init__(self, banda_actual: int = 30,
                 canales_activos: list | None = None,
                 porcentaje: int = 100,
                 alineacion: str = "centro",
                 canal_vis: str = "B",
                 parent=None):
        super().__init__(parent)
        if canales_activos is None:
            canales_activos = ["B", "G", "R", "Gray"]
        self.setWindowTitle("Configurar extracción de intensidad (BI/GI/RI)")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            "Extrae la intensidad de los canales R, G, B y Gray\n"
            "a lo largo del radio medido, anillo por anillo.\n"
            "Compatible con el método de Maxwell et al. (2017)."
        ))

        grp = QGroupBox("Parámetros de muestreo")
        form = QFormLayout(grp)

        self.spin_banda = QSpinBox()
        self.spin_banda.setRange(0, 200)
        self.spin_banda.setValue(banda_actual)
        self.spin_banda.setSuffix(" px")
        self.spin_banda.setToolTip(
            "Píxeles a cada lado del camino para promediar.\n"
            "0 = solo el camino central.\n"
            "10 = banda de 21px de ancho total.\n"
            "CooRecorder usa típicamente 5-15px."
        )
        form.addRow("Banda (px a cada lado):", self.spin_banda)

        # Fila compacta: [___] % [EW] [Centro] [LW]  (como el selector Med/Cor)
        from PyQt6.QtWidgets import QHBoxLayout as _QHL, QRadioButton, QButtonGroup
        fila_pct = _QHL()

        self.spin_pct = QSpinBox()
        self.spin_pct.setRange(10, 100)
        self.spin_pct.setValue(porcentaje)
        self.spin_pct.setSuffix(" %")
        self.spin_pct.setFixedWidth(70)
        self.spin_pct.setToolTip(
            "Porcentaje del anillo a medir.\n"
            "100 = todo el anillo.  80 = descarta el 20% según alineación."
        )
        fila_pct.addWidget(self.spin_pct)
        fila_pct.addSpacing(8)

        self._btn_grp_alin = QButtonGroup(self)
        for etiq, val, tip in [
            ("EW",     "ew",     "Pegado a madera temprana.\nEl recorte se aplica al extremo de madera tardía."),
            ("Centro", "centro", "Centrado en el anillo.\nSe descarta el mismo margen en ambos extremos."),
            ("LW",     "lw",     "Pegado a madera tardía.\nEl recorte se aplica al extremo de madera temprana."),
        ]:
            rb = QRadioButton(etiq)

            rb.setToolTip(tip)
            rb.setProperty("alin_val", val)
            rb.setChecked(val == alineacion)
            self._btn_grp_alin.addButton(rb)
            fila_pct.addWidget(rb)

        fila_pct.addStretch()
        form.addRow("% anillo / alineación:", fila_pct)

        layout.addWidget(grp)

        grp2 = QGroupBox("Canales a calcular")
        form2 = QFormLayout(grp2)

        self.check_b    = QCheckBox("Blue Intensity (BI)")
        self.check_g    = QCheckBox("Green Intensity (GI)")
        self.check_r    = QCheckBox("Red Intensity (RI)")
        self.check_gray = QCheckBox("Gray Intensity — densidad de madera")
        self.check_b.setChecked("B" in canales_activos)
        self.check_g.setChecked("G" in canales_activos)
        self.check_r.setChecked("R" in canales_activos)
        self.check_gray.setChecked("Gray" in canales_activos)
        self.check_gray.setToolTip(
            "Canal de luminancia (escala de grises).\nSensible a la densidad de la madera.\nÚtil para detectar anillos falsos y bandas intra-anuales."
        )
        form2.addRow("", self.check_b)
        form2.addRow("", self.check_g)
        form2.addRow("", self.check_r)
        form2.addRow("", self.check_gray)
        layout.addWidget(grp2)

        grp3 = QGroupBox("Canal a visualizar en imagen")
        form3 = QFormLayout(grp3)
        from PyQt6.QtWidgets import QComboBox
        self.combo_canal_vis = QComboBox()
        for clave, etiq in [("B","Azul (BI)"),("G","Verde (GI)"),
                             ("R","Rojo (RI)"),("Gray","Gris (densidad)")]:
            self.combo_canal_vis.addItem(etiq, clave)
        idx = {"B":0,"G":1,"R":2,"Gray":3}.get(canal_vis, 0)
        self.combo_canal_vis.setCurrentIndex(idx)
        self.combo_canal_vis.setToolTip(
            "Canal cuya intensidad real se muestra sobre la imagen\n"
            "al activar el botón 👁 Ver área BI."
        )
        form3.addRow("Canal:", self.combo_canal_vis)
        layout.addWidget(grp3)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def showEvent(self, event):
        """Colores de controles adaptados al tema, con indicador siempre visible."""
        super().showEvent(event)
        from PyQt6.QtGui import QPalette
        pal    = self.palette()
        oscuro = pal.color(QPalette.ColorRole.Window).lightness() < 128
        txt    = "#ffffff" if oscuro else "#111111"
        borde  = "#aaaaaa" if oscuro else "#666666"
        # Marcado: fondo oscuro + check blanco (siempre contrasta)
        marca = "#FF8C00"  # naranja — siempre visible en tema claro y oscuro
        self.setStyleSheet(
            f"QCheckBox{{color:{txt}}}"
            f" QCheckBox::indicator{{width:13px;height:13px;"
            f"border:2px solid {borde};border-radius:2px;background:transparent}}"
            f" QCheckBox::indicator:checked{{border:2px solid {marca};"
            f"background:{marca}}}"
            f" QRadioButton{{color:{txt}}}"
            f" QRadioButton::indicator{{width:13px;height:13px;"
            f"border:2px solid {borde};border-radius:7px;background:transparent}}"
            f" QRadioButton::indicator:checked{{border:2px solid {marca};"
            f"background:{marca}}}"
            f" QLabel{{color:{txt}}} QGroupBox{{color:{txt}}}"
        )

    @property
    def banda(self) -> int:
        return self.spin_banda.value()

    @property
    def porcentaje(self) -> int:
        return self.spin_pct.value()

    @property
    def alineacion(self) -> str:
        for btn in self._btn_grp_alin.buttons():
            if btn.isChecked():
                return btn.property("alin_val")
        return "centro"

    @property
    def canal_vis(self) -> str:
        return self.combo_canal_vis.currentData()

    @property
    def canales(self) -> list[str]:
        c = []
        if self.check_b.isChecked():    c.append("B")
        if self.check_g.isChecked():    c.append("G")
        if self.check_r.isChecked():    c.append("R")
        if self.check_gray.isChecked(): c.append("Gray")
        return c


# =============================================================================
# DIÁLOGO DE GUARDADO DE BI/GI/RI
# =============================================================================

class DialogoGuardarBI(QDialog):
    """
    Diálogo para seleccionar qué métricas de intensidad guardar
    y en qué formato (.rwl por canal×métrica).
    """

    METRICAS = [
        ("mean", "Promedio (mean)"),
        ("min",  "Mínimo (min)"),
        ("max",  "Máximo (max)"),
    ]
    CANALES_DISPONIBLES = [
        ("B",    "Blue Intensity (BI)"),
        ("G",    "Green Intensity (GI)"),
        ("R",    "Red Intensity (RI)"),
        ("Gray", "Gray Intensity (densidad)"),
        ("B_R",  "B/R — ratio azul/rojo (proxy climático, Maxwell 2017)"),
        ("B_G",  "B/G — ratio azul/verde"),
    ]

    def __init__(self, canales_calculados: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Guardar intensidad de color (BI/GI/RI)")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            "Selecciona qué métricas guardar.\nSe generará un archivo .rwl por cada combinación canal × métrica."
        ))

        # Canales
        grp_c = QGroupBox("Canales")
        fc = QVBoxLayout(grp_c)
        self._checks_canal: dict[str, QCheckBox] = {}
        for clave, etiq in self.CANALES_DISPONIBLES:
            chk = QCheckBox(etiq)

            # Ratios disponibles si los dos canales base están calculados
            if clave == "B_R":
                disponible = "B" in canales_calculados and "R" in canales_calculados
                chk.setToolTip("Calculado automáticamente como B_mean / R_mean por anillo.")
            elif clave == "B_G":
                disponible = "B" in canales_calculados and "G" in canales_calculados
                chk.setToolTip("Calculado automáticamente como B_mean / G_mean por anillo.")
            else:
                disponible = clave in canales_calculados
                if not disponible:
                    chk.setToolTip("No calculado — recalcula con este canal activo.")
            chk.setChecked(False)   # ratios desactivados por defecto
            chk.setEnabled(disponible)
            self._checks_canal[clave] = chk
            fc.addWidget(chk)
        layout.addWidget(grp_c)

        # Métricas
        grp_m = QGroupBox("Métricas (no aplica a ratios B/R, B/G)")
        fm = QVBoxLayout(grp_m)
        self._checks_met: dict[str, QCheckBox] = {}
        for clave, etiq in self.METRICAS:
            chk = QCheckBox(etiq)

            chk.setChecked(True)
            self._checks_met[clave] = chk
            fm.addWidget(chk)
        layout.addWidget(grp_m)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def showEvent(self, event):
        super().showEvent(event)
        from PyQt6.QtGui import QPalette
        pal    = self.palette()
        oscuro = pal.color(QPalette.ColorRole.Window).lightness() < 128
        txt    = "#ffffff" if oscuro else "#111111"
        borde  = "#aaaaaa" if oscuro else "#666666"
        marca = "#FF8C00"
        self.setStyleSheet(
            f"QCheckBox{{color:{txt}}}"
            f" QCheckBox::indicator{{width:13px;height:13px;"
            f"border:2px solid {borde};border-radius:2px;background:transparent}}"
            f" QCheckBox::indicator:checked{{border:2px solid {marca};"
            f"background:{marca}}}"
            f" QLabel{{color:{txt}}} QGroupBox{{color:{txt}}}"
        )

    @property
    def seleccion(self) -> list[tuple[str, str]]:
        """Devuelve lista de (canal, metrica) a exportar."""
        result = []
        for canal, chk_c in self._checks_canal.items():
            if not chk_c.isChecked():
                continue
            for met, chk_m in self._checks_met.items():
                if chk_m.isChecked():
                    result.append((canal, met))
        return result
