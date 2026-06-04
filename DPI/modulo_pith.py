"""
modulo_pith.py — Estimación de anillos faltantes hacia la médula.

Tres métodos combinables:
  1. Geométrico por arco: 3+ clics sobre el arco interno visible → ajuste de
     círculo por mínimos cuadrados → radio del árbol → distancia faltante.
  2. DAP manual: el usuario ingresa el diámetro a la altura del pecho (mm)
     cuando no hay curvatura visible (tarugos rectos).
  3. Serie de referencia: en vez de usar el ancho promedio de los N anillos
     internos como constante, escala cada anillo estimado según el patrón
     de crecimiento relativo de la referencia en el mismo período.

El resultado (pith_offset) se guarda en el sidecar .anom.json y retrocede
el año de médula de la serie para que los años biológicos queden correctos.
"""

import math
import logging
import numpy as np
import pandas as pd

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QDoubleSpinBox, QGroupBox, QFormLayout,
    QDialogButtonBox, QComboBox, QCheckBox, QTabWidget,
    QWidget, QMessageBox, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPen, QColor, QBrush

import pyqtgraph as pg

logger = logging.getLogger(__name__)


# =============================================================================
# MATEMÁTICA DE AJUSTE DE CÍRCULO
# =============================================================================

def ajustar_circulo_minimos_cuadrados(
    puntos: list[tuple[float, float]]
) -> tuple[float, float, float] | None:
    """
    Ajusta el mejor círculo a N≥3 puntos por mínimos cuadrados algebraicos
    (método Pratt / Taubin linearizado — robusto y sin iteración).

    Devuelve (cx, cy, radio) en las mismas unidades que los puntos,
    o None si los puntos son colineales o insuficientes.
    """
    if len(puntos) < 3:
        return None

    pts = np.array(puntos, dtype=float)
    x, y = pts[:, 0], pts[:, 1]

    # Sistema lineal: x² + y² = 2·cx·x + 2·cy·y + (r²−cx²−cy²)
    # → A·[cx, cy, c]ᵀ = b   donde c = cx²+cy²−r²
    A = np.column_stack([2*x, 2*y, np.ones(len(x))])
    b = x**2 + y**2

    try:
        result, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return None

    cx, cy, c = result
    r2 = cx**2 + cy**2 - c   # puede ser negativo si los puntos son casi colineales
    if r2 <= 0:
        return None

    return float(cx), float(cy), float(math.sqrt(r2))


def estimar_anillos_faltantes(
    distancia_faltante_mm: float,
    anchos_internos_mm: list[float],
    serie_ref: pd.Series | None = None,
    anio_inicio_serie: int | None = None,
) -> dict:
    """
    Estima el número de anillos faltantes entre el borde interno medido
    y la médula, usando los anchos de los N anillos más internos.

    Parámetros
    ----------
    distancia_faltante_mm : distancia desde el primer punto medido hasta
                            el centro estimado del árbol, en mm.
    anchos_internos_mm    : anchos (mm) de los N anillos más internos,
                            ordenados de más externo a más interno (último = más
                            cercano a la médula).
    serie_ref             : Serie de pandas con índice=año y valores=ancho_mm.
                            Si se proporciona, se usan anchos relativos en vez
                            del promedio.
    anio_inicio_serie     : año del primer anillo medido. Necesario para
                            alinear la serie de referencia hacia atrás.

    Devuelve un dict con:
      - n_media      : estimación usando ancho promedio
      - n_tendencia  : estimación usando regresión lineal (tendencia)
      - n_referencia : estimación usando serie de referencia (None si no hay ref)
      - ancho_medio  : ancho promedio usado (mm)
      - ancho_tendencia_en0 : ancho extrapolado en el anillo más interno
      - incertidumbre_min : mínimo razonable (percentil 25 de los anchos)
      - incertidumbre_max : máximo razonable (percentil 75 de los anchos)
    """
    if not anchos_internos_mm or distancia_faltante_mm <= 0:
        return {}

    arr = np.array(anchos_internos_mm, dtype=float)
    arr = arr[arr > 0]
    if len(arr) == 0:
        return {}

    # ── Método 1: promedio simple ─────────────────────────────────────────
    ancho_medio = float(np.mean(arr))
    n_media = distancia_faltante_mm / ancho_medio if ancho_medio > 0 else 0

    # ── Método 2: tendencia lineal extrapolada ────────────────────────────
    # Regresión sobre los anchos internos; extrapolamos hacia el centro
    n = len(arr)
    xs = np.arange(n, dtype=float)   # 0 = más externo, n-1 = más interno
    if n >= 3:
        coef = np.polyfit(xs, arr, 1)   # coef[0]=pendiente, coef[1]=intercepto
        # Extrapolamos más allá de n-1 (hacia la médula)
        # Ancho estimado en posición k > n-1: max(0.1, poly(k))
        # Suma acumulada hasta llegar a distancia_faltante_mm
        ancho_en_ultimo = float(np.polyval(coef, n - 1))
        pendiente = float(coef[0])
        # Si la tendencia es decreciente (anillos más estrechos hacia centro),
        # tiene sentido físico — los árboles jóvenes pueden crecer más rápido.
        # Acumulamos hacia atrás hasta cubrir la distancia.
        acum = 0.0
        k = 0
        while acum < distancia_faltante_mm and k < 500:
            ancho_k = max(0.05, np.polyval(coef, (n - 1) + k))
            acum += ancho_k
            k += 1
        n_tendencia = float(k)
        ancho_tendencia_en0 = float(np.polyval(coef, n - 1))
    else:
        n_tendencia = n_media
        ancho_tendencia_en0 = ancho_medio

    # ── Método 3: serie de referencia ────────────────────────────────────
    n_referencia = None
    if serie_ref is not None and anio_inicio_serie is not None and len(serie_ref) >= 5:
        try:
            # Calcular factor de escala: promedio de anchos propios / promedio
            # de la referencia en el mismo rango de años disponibles
            ref_clip = serie_ref.clip(lower=0.01)
            ref_media_global = float(ref_clip.mean())
            escala = ancho_medio / ref_media_global if ref_media_global > 0 else 1.0

            # Ir hacia atrás desde anio_inicio_serie usando los anchos
            # relativos de la referencia, escalados
            acum = 0.0
            k = 0
            anio_actual = anio_inicio_serie - 1
            while acum < distancia_faltante_mm and k < 500:
                if anio_actual in ref_clip.index:
                    ancho_k = float(ref_clip.loc[anio_actual]) * escala
                else:
                    # Fuera del rango de la referencia: usar el promedio escalado
                    ancho_k = ancho_medio
                ancho_k = max(0.05, ancho_k)
                acum += ancho_k
                k += 1
                anio_actual -= 1
            n_referencia = float(k)
        except Exception as exc:
            logger.warning("Error en estimación por referencia: %s", exc)

    # ── Incertidumbre ─────────────────────────────────────────────────────
    p25 = float(np.percentile(arr, 25))
    p75 = float(np.percentile(arr, 75))
    inc_max = distancia_faltante_mm / p25 if p25 > 0 else n_media * 1.5
    inc_min = distancia_faltante_mm / p75 if p75 > 0 else n_media * 0.5

    return {
        "n_media":              round(n_media, 1),
        "n_tendencia":          round(n_tendencia, 1),
        "n_referencia":         round(n_referencia, 1) if n_referencia is not None else None,
        "ancho_medio":          round(ancho_medio, 3),
        "ancho_tendencia_en0":  round(ancho_tendencia_en0, 3),
        "incertidumbre_min":    round(inc_min, 1),
        "incertidumbre_max":    round(inc_max, 1),
    }


# =============================================================================
# MODO INTERACTIVO: MARCA DE PUNTOS EN EL LIENZO
# (se gestiona desde VistaInteractiva, igual que MODO_AUTODETECT)
# =============================================================================

MODO_PITH = 9   # Nuevo modo para marcar puntos del arco


# =============================================================================
# DIÁLOGO DE RESULTADOS
# =============================================================================

class DialogoEstimacionPith(QDialog):
    """
    Muestra los resultados de la estimación de anillos faltantes y
    permite al usuario elegir qué valor aplicar.

    Señal aplicar_offset(n_anillos: int) emitida al aceptar.
    """

    aplicar_offset = pyqtSignal(int)

    def __init__(
        self,
        resultados: dict,
        radio_px: float,
        distancia_faltante_px: float,
        pixeles_por_mm: float,
        n_anillos_internos: int,
        tiene_referencia: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Estimación de anillos faltantes hacia la médula")
        self.setMinimumWidth(520)
        self._resultados = resultados
        self._ppm = pixeles_por_mm

        layout = QVBoxLayout(self)

        # ── Datos geométricos ─────────────────────────────────────────────
        grp_geo = QGroupBox("Geometría estimada")
        fg = QFormLayout(grp_geo)
        radio_mm = radio_px / pixeles_por_mm
        dist_mm  = distancia_faltante_px / pixeles_por_mm
        fg.addRow("Radio del árbol:",
                  QLabel(f"<b>{radio_mm:.1f} mm</b>  ({radio_mm*2:.1f} mm diámetro)"))
        fg.addRow("Distancia faltante hasta médula:",
                  QLabel(f"<b>{dist_mm:.1f} mm</b>"))
        fg.addRow("Anillos internos usados:",
                  QLabel(f"{n_anillos_internos}"))
        fg.addRow("Ancho promedio (N internos):",
                  QLabel(f"{resultados.get('ancho_medio', 0):.3f} mm"))
        layout.addWidget(grp_geo)

        # ── Resultados por método ─────────────────────────────────────────
        grp_res = QGroupBox("Estimaciones")
        fr = QFormLayout(grp_res)

        n_med  = resultados.get("n_media", 0)
        n_tend = resultados.get("n_tendencia", 0)
        n_ref  = resultados.get("n_referencia")
        n_min  = resultados.get("incertidumbre_min", 0)
        n_max  = resultados.get("incertidumbre_max", 0)

        lbl_med = QLabel(f"<b>{n_med:.1f}</b>  anillos")
        lbl_med.setToolTip("Distancia faltante ÷ ancho promedio de los N anillos internos")
        fr.addRow("Promedio simple:", lbl_med)

        lbl_tend = QLabel(f"<b>{n_tend:.1f}</b>  anillos")
        lbl_tend.setToolTip("Extrapolación lineal de la tendencia de crecimiento interno")
        fr.addRow("Tendencia extrapolada:", lbl_tend)

        if n_ref is not None:
            lbl_ref = QLabel(f"<b>{n_ref:.1f}</b>  anillos")
            lbl_ref.setToolTip("Anchos relativos de la serie de referencia, escalados al crecimiento propio")
            fr.addRow("Serie de referencia:", lbl_ref)
        else:
            fr.addRow("Serie de referencia:", QLabel("— (no cargada)"))

        lbl_rango = QLabel(f"{n_min:.0f} – {n_max:.0f}  anillos")
        lbl_rango.setStyleSheet("color: #aaaaaa;")
        lbl_rango.setToolTip("Rango basado en los percentiles 25–75 de los anchos internos")
        fr.addRow("Rango de incertidumbre:", lbl_rango)

        layout.addWidget(grp_res)

        # ── Selector de valor a aplicar ───────────────────────────────────
        grp_sel = QGroupBox("Valor a aplicar")
        fs = QFormLayout(grp_sel)

        self.spin_offset = QSpinBox()
        self.spin_offset.setRange(0, 999)
        self.spin_offset.setValue(max(0, round(n_med)))
        self.spin_offset.setToolTip(
            "Número de anillos que se agregarán antes del primer anillo medido.\n"
            "El año de médula se retrocederá en este valor."
        )
        fs.addRow("Anillos faltantes a aplicar:", self.spin_offset)

        # Botones rápidos para cada método
        fila_rapida = QHBoxLayout()
        for etiqueta, valor in [
            ("Promedio", round(n_med)),
            ("Tendencia", round(n_tend)),
        ] + ([("Referencia", round(n_ref))] if n_ref is not None else []):
            btn = QPushButton(f"{etiqueta}: {valor}")
            btn.setFixedHeight(24)
            btn.clicked.connect(lambda _, v=valor: self.spin_offset.setValue(v))
            fila_rapida.addWidget(btn)
        fs.addRow("Atajos:", fila_rapida)

        self.check_marcar_estimada = QCheckBox(
            "Marcar médula como 'estimada' (no presente físicamente)"
        )
        self.check_marcar_estimada.setChecked(True)
        self.check_marcar_estimada.setToolTip(
            "Actualiza el checkbox 🌱 Médula en la barra y lo guarda en el sidecar\n"
            "con el flag 'pith_estimated: true' para distinguirla de médula real."
        )
        fs.addRow("", self.check_marcar_estimada)

        layout.addWidget(grp_sel)

        # ── Gráfico de los anchos internos + tendencia ────────────────────
        self._grafico = pg.PlotWidget(title="Anchos de los N anillos internos")
        self._grafico.showGrid(x=True, y=True, alpha=0.3)
        self._grafico.setLabel("bottom", "Posición (0=más externo)")
        self._grafico.setLabel("left", "Ancho (mm)")
        self._grafico.setMaximumHeight(160)
        layout.addWidget(self._grafico)

        # ── Botones ───────────────────────────────────────────────────────
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._al_aceptar)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def poblar_grafico(self, anchos: list[float]):
        """Dibuja los anchos internos y la línea de tendencia."""
        if not anchos:
            return
        arr = np.array(anchos, dtype=float)
        xs  = np.arange(len(arr), dtype=float)

        self._grafico.plot(xs, arr,
                           pen=None, symbol="o", symbolSize=7,
                           symbolBrush="#5bc0de", symbolPen=None)
        self._grafico.plot(xs, arr,
                           pen=pg.mkPen("#5bc0de", width=1.5))

        if len(arr) >= 3:
            coef = np.polyfit(xs, arr, 1)
            # Extender la línea hacia la izquierda (hacia la médula)
            n_ext = int(self._resultados.get("n_tendencia", len(arr)))
            xs_ext = np.linspace(-n_ext, len(arr) - 1, 200)
            ys_ext = np.polyval(coef, xs_ext)
            self._grafico.plot(xs_ext, ys_ext,
                               pen=pg.mkPen("#f0ad4e", width=1.5,
                                            style=Qt.PenStyle.DashLine))
            # Línea vertical en x=0 (límite de lo medido)
            self._grafico.addLine(
                x=0, pen=pg.mkPen("#888888", width=1, style=Qt.PenStyle.DotLine))

    def _al_aceptar(self):
        self.aplicar_offset.emit(self.spin_offset.value())
        self.accept()

    @property
    def offset_elegido(self) -> int:
        return self.spin_offset.value()

    @property
    def marcar_estimada(self) -> bool:
        return self.check_marcar_estimada.isChecked()


# =============================================================================
# DIÁLOGO DE ENTRADA: elección de método + parámetros
# =============================================================================

class DialogoMetodoPith(QDialog):
    """
    Paso previo al marcado de puntos. El usuario elige:
      - Método: geométrico (arco) o DAP manual
      - N anillos internos a usar
      - Si usar la serie de referencia cargada
    """

    def __init__(self, tiene_referencia: bool, n_anillos_disponibles: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Estimar anillos faltantes hacia la médula")
        # Tamaño inicial razonable (cabe la descripción sin scrollear)
        self.setMinimumWidth(500)
        self.resize(620, 520)
        # Permitir que el usuario redimensione la ventana — el texto del
        # método se reacomoda con setWordWrap.
        self.setSizeGripEnabled(True)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<b>Método para estimar la distancia a la médula:</b>"
        ))

        # ── Tabs: Arco / DAP ──────────────────────────────────────────────
        self._tabs = QTabWidget()

        # Tab 1: Arco — instrucciones cortas y claras
        tab_arco = QWidget()
        fl_arco = QVBoxLayout(tab_arco)
        fl_arco.setContentsMargins(8, 8, 8, 8)
        # Instrucciones en una sola línea por punto, sin frases largas
        # que se cortaban en la versión anterior.
        instrucciones_arco = QLabel(
            "Marca <b>3 puntos</b> sobre un mismo anillo visible "
            "(cerca del 1er anillo medido):<br>"
            "• Clic izquierdo: agregar punto<br>"
            "• Más puntos = mejor ajuste<br>"
            "• Enter: confirmar &nbsp;·&nbsp; Esc: cancelar"
        )
        instrucciones_arco.setWordWrap(True)
        fl_arco.addWidget(instrucciones_arco)
        fl_arco.addStretch()
        self._tabs.addTab(tab_arco, "🔵 Por arco (3+ puntos)")

        # Tab 2: DAP — ahora en cm (es la unidad más común en dendro)
        tab_dap = QWidget()
        fl_dap = QFormLayout(tab_dap)
        fl_dap.setContentsMargins(8, 8, 8, 8)
        lbl_dap = QLabel(
            "Para tarugos sin curvatura visible.<br>"
            "Ingresa el diámetro a la altura del pecho:"
        )
        lbl_dap.setWordWrap(True)
        fl_dap.addRow(lbl_dap)
        self.spin_dap = QDoubleSpinBox()
        # DAP típicamente se reporta en CM en dendrocronología (no mm).
        # Rango razonable: 1 cm a 500 cm (5 metros, suficiente para
        # árboles gigantes).
        self.spin_dap.setRange(1.0, 500.0)
        self.spin_dap.setValue(20.0)  # 20 cm = 200 mm (default razonable)
        self.spin_dap.setSuffix(" cm")
        self.spin_dap.setDecimals(1)
        self.spin_dap.setToolTip("DAP en centímetros (diámetro total, no radio)")
        self.spin_dap.setMaximumWidth(150)  # no necesita ser tan ancho
        fl_dap.addRow("DAP:", self.spin_dap)
        self._tabs.addTab(tab_dap, "📏 Por DAP")

        layout.addWidget(self._tabs)

        # ── Parámetros comunes ────────────────────────────────────────────
        grp = QGroupBox("Parámetros de estimación")
        grp_layout = QVBoxLayout(grp)

        # Layout de formulario (parámetros principales)
        fg = QFormLayout()

        self.spin_n_internos = QSpinBox()
        self.spin_n_internos.setRange(3, min(20, max(3, n_anillos_disponibles)))
        self.spin_n_internos.setValue(min(5, n_anillos_disponibles))
        self.spin_n_internos.setMaximumWidth(100)
        self.spin_n_internos.setToolTip(
            "Número de anillos junto al arco marcado (hacia la corteza) "
            "para estimar el ancho típico."
        )
        fg.addRow("Anillos de referencia:", self.spin_n_internos)

        # Selector de método de cálculo
        self.combo_metodo_calc = QComboBox()
        self.combo_metodo_calc.addItem("RG — Crecimiento radial (estándar)", "rg")
        self.combo_metodo_calc.addItem("BAI — Área basal incremental", "bai")
        self.combo_metodo_calc.addItem("Promedio RG+BAI (Altman 2016)", "promedio")
        self.combo_metodo_calc.setCurrentIndex(0)  # RG por defecto
        self.combo_metodo_calc.setToolTip(
            "Método matemático para convertir la distancia faltante en "
            "cantidad de anillos."
        )
        fg.addRow("Método de cálculo:", self.combo_metodo_calc)

        self.check_usar_ref = QCheckBox("Usar serie de referencia cargada")
        self.check_usar_ref.setChecked(tiene_referencia)
        self.check_usar_ref.setEnabled(tiene_referencia)
        if not tiene_referencia:
            self.check_usar_ref.setToolTip(
                "Carga una serie de referencia (.rwl o .wid) en el panel de co-datación primero."
            )
        fg.addRow("", self.check_usar_ref)

        grp_layout.addLayout(fg)

        # Descripción del método seleccionado.
        # Usa setWordWrap(True) y un SizePolicy que permite achicarse Y
        # crecer según el ancho del diálogo. Como el diálogo es
        # redimensionable, el texto se re-acomoda cuando el usuario
        # cambia el tamaño de la ventana.
        self.desc_metodo = QLabel()
        self.desc_metodo.setWordWrap(True)
        self.desc_metodo.setStyleSheet(
            "QLabel { background-color: rgba(255,255,255,8); "
            "border: 1px solid rgba(255,255,255,30); "
            "border-radius: 4px; padding: 8px; "
            "font-size: 11px; color: #cccccc; }"
        )
        # MinimumExpanding en horizontal: ocupa todo el ancho disponible.
        # Preferred en vertical: la altura se ajusta al contenido cuando el
        # texto se re-acomoda al cambiar el ancho.
        self.desc_metodo.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Preferred
        )
        self.desc_metodo.setMinimumWidth(0)  # permite achicarse libremente
        self.desc_metodo.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        grp_layout.addWidget(self.desc_metodo)

        # Actualizar descripción cuando cambia la selección + setear estado inicial
        self.combo_metodo_calc.currentIndexChanged.connect(
            self._actualizar_descripcion_metodo)
        self._actualizar_descripcion_metodo()

        layout.addWidget(grp)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _actualizar_descripcion_metodo(self):
        """Actualiza la descripción según el método seleccionado en el combo.

        El QLabel `desc_metodo` tiene setWordWrap(True), así que cuando el
        usuario redimensiona el diálogo (lo agranda o achica), el texto
        se reacomoda automáticamente al nuevo ancho. La altura del label
        se ajusta también porque el SizePolicy vertical es Preferred.
        """
        metodo = self.combo_metodo_calc.currentData()
        if metodo == "rg":
            texto = (
                "<b>RG (Radial Growth) — método estándar.</b><br>"
                "Calcula los anillos faltantes dividiendo la distancia "
                "faltante por el ancho promedio de los anillos de "
                "referencia (los N anillos junto al arco marcado, contando "
                "hacia la corteza). Es el método clásico que usa "
                "CooRecorder y la mayoría del software de dendrocronología. "
                "<br><br>"
                "<b>Sesgo conocido:</b> según Altman et al. (2016), tiende "
                "a <b>sobreestimar</b> hasta ~27% porque los anillos "
                "verdaderamente cercanos a la médula suelen ser más "
                "estrechos que los de referencia."
            )
        elif metodo == "bai":
            texto = (
                "<b>BAI (Basal Area Increment).</b><br>"
                "Divide el área basal faltante (π × distancia²) por el "
                "incremento de área basal promedio de los anillos de "
                "referencia. Toma en cuenta que un anillo más lejano de "
                "la médula representa más área que uno cercano (porque la "
                "circunferencia crece con el radio).<br><br>"
                "<b>Sesgo conocido:</b> según Altman et al. (2016), tiende "
                "a <b>subestimar</b> ~5-20% porque los anillos de "
                "referencia, al estar lejos de la médula, tienen "
                "incrementos de área mucho mayores que los anillos faltantes."
            )
        else:  # promedio
            texto = (
                "<b>Promedio RG+BAI — Altman et al. (2016).</b><br>"
                "Calcula ambos métodos y los promedia. Aprovecha que RG "
                "sobreestima y BAI subestima — los errores se cancelan "
                "parcialmente.<br><br>"
                "<b>Precisión:</b> según el paper original (Altman et al. "
                "2016, <i>Forest Ecology and Management</i> 380:82-89), "
                "este es el método más preciso, con error general "
                "<b>&lt; 3.5%</b>. Recomendado especialmente cuando la "
                "distancia faltante es grande y el arco está lejos del "
                "primer anillo medido."
            )
        self.desc_metodo.setText(texto)

    @property
    def metodo(self) -> str:
        return "arco" if self._tabs.currentIndex() == 0 else "dap"

    @property
    def dap_mm(self) -> float:
        # spin_dap está en CM (cambio para reflejar la unidad típica
        # usada en dendrocronología), pero el resto del programa trabaja
        # en mm. Convertir aquí: 1 cm = 10 mm.
        return self.spin_dap.value() * 10.0

    @property
    def n_internos(self) -> int:
        return self.spin_n_internos.value()

    @property
    def usar_referencia(self) -> bool:
        return self.check_usar_ref.isChecked()

    @property
    def metodo_calculo(self) -> str:
        """Devuelve el método de cálculo seleccionado: 'rg', 'bai' o 'promedio'."""
        return self.combo_metodo_calc.currentData()
