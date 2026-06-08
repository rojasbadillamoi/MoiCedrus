# panel_medicion.py — DendroLen v2.0
# Pantalla 2: ventana de medición en tiempo real.
# Los colores se aplican via _aplicar_tema() para soportar cambio de tema sin reconstruir.

import os
import numpy as np
from typing import List, Dict, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QSplitter, QFrame, QSizePolicy, QInputDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QSize, QEvent
from PyQt6.QtGui import QKeySequence, QShortcut, QTextCursor

from constantes import (
    MSG_PAUSA, MSG_REANUDA,
    BEEP_DECADE_FREQ, BEEP_DECADE_DUR,
    BEEP_SAVE_FREQ, BEEP_SAVE_DUR, BEEP_SAVE_FREQ2, BEEP_SAVE_DUR2,
    ANOMALIAS, ANOMALIA_CODIGOS, ANOMALIA_COLORES, color_anomalia,
)
from tema_manager import tema as _tema_mgr
from tucson_writer import escribir_tucson, preview_tucson, escribir_sidecar
from serial_worker import SerialWorker, listar_puertos, detectar_puerto_auto
from dialogo_nueva_muestra import DialogoNuevaMuestra
from dialogo_ayuda import DialogoAyuda
from serie_maestra import PanelMaestra

# Iconos para anomalías
ANOM_ICONOS = {
    "FR": "❄",   # Frost ring
    "LR": "◌",   # Light ring
    "AF": "⊕",   # Anillo falso
    "MR": "✕",   # Missing ring
    "FU": "🔥",  # Fuego
    "GO": "◉",   # Golpe
    "OT": "★",   # Otro
}


class PanelMedicion(QWidget):
    nueva_muestra_solicitada = pyqtSignal(dict)

    def __init__(self, config: dict, parent=None,
                 meds_previas=None, eventos_previos=None):
        super().__init__(parent)
        self._cfg     = config
        self._meds: List[int] = list(meds_previas)   if meds_previas   else []
        self._eventos: Dict[int, str] = dict(eventos_previos) if eventos_previos else {}
        self._paused  = False
        self._referencia_fijada = False
        self._log_entries: List[tuple] = []  # (timestamp, level, msg) para regenerar al cambiar tema
        self._worker: Optional[SerialWorker] = None
        self._beep_timer = QTimer(self)
        self._beep_timer.setSingleShot(True)
        # Timer de debounce para la vista Tucson (evita reconstruir en cada anillo)
        self._tucson_timer = QTimer(self)
        self._tucson_timer.setSingleShot(True)
        self._tucson_timer.timeout.connect(self._update_tucson)
        self._build_ui()
        self._register_shortcuts()
        _tema_mgr.suscribir(self._aplicar_tema)
        self._start_serial()
        self._refresh_info()
        self._refresh_stats()
        if self._meds:
            self._update_tucson()
            self.panel_maestra.actualizar_mediciones(
                self._meds, self._cfg['anio'], self._cfg['serie'])
        self.log("ok", f"Sesión iniciada · {config['serie']} · año inicio {config['anio']}")
        if config.get('port'):
            self.log("ok", f"Puerto: {config['port']} · {config['baud']} baud")
        else:
            self.log("warn", "Sin puerto. Conecta el carro y presiona F9.")
        # Mostrar mensaje inicial en la vista Tucson si no hay mediciones previas
        if not self._meds:
            self._mostrar_mensaje_referencia()

    # ─────────────────────────────────────────────────────────────────────────
    # CONSTRUCCIÓN DE UI (colores neutros — _aplicar_tema los pone)
    # ─────────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_info_bar())
        # Splitter vertical entre zona Tucson/Log y gráfico
        self.vsplitter = QSplitter(Qt.Orientation.Vertical)
        self.vsplitter.setHandleWidth(4)
        self.vsplitter.setObjectName("vsplitter")
        self.vsplitter.addWidget(self._build_middle())
        # Panel de co-datación — incluye el gráfico de la serie en construcción
        self.panel_maestra = PanelMaestra()
        self.vsplitter.addWidget(self.panel_maestra)
        self.vsplitter.setSizes([420, 280])
        self.vsplitter.setStretchFactor(0, 2)
        self.vsplitter.setStretchFactor(1, 1)
        root.addWidget(self.vsplitter, stretch=1)
        root.addWidget(self._build_bottom_bar())
        self._aplicar_tema(_tema_mgr.t)
        # Instalar eventFilter en QApplication para capturar teclas F
        # desde cualquier widget hijo (QTextEdit, FigureCanvas, etc.)
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().installEventFilter(self)

    # ── 1. Info bar ───────────────────────────────────────────────────────────
    def _build_info_bar(self):
        self.info_bar = QWidget()
        self.info_bar.setObjectName("info_bar")
        self.info_bar.setFixedHeight(46)
        lay = QHBoxLayout(self.info_bar)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(0)

        def field(key, attr, editable=False, accent=None, small=False):
            w = QWidget()
            w.setObjectName("field_editable" if editable else "field_sep")
            fl = QHBoxLayout(w)
            fl.setContentsMargins(12, 0, 12, 0)
            fl.setSpacing(6)
            k = QLabel(key.upper())
            k.setObjectName("field_key")
            v = QLabel("—")
            v.setObjectName(f"field_val{'_sm' if small else ''}")
            if accent:
                v.setProperty("accent", accent)
            if editable:
                v.setCursor(Qt.CursorShape.PointingHandCursor)
                v.setToolTip("Clic para editar")
                # Agregar icono de lápiz sutil
                edit_icon = QLabel("✎")
                edit_icon.setObjectName("edit_icon")
            fl.addWidget(k); fl.addWidget(v)
            if editable:
                fl.addWidget(edit_icon)
            setattr(self, attr, v)
            return w

        lay.addWidget(field("Serie", "lbl_serie", editable=True, accent="blue"))
        lay.addWidget(field("Año inicio", "lbl_anio", editable=True))
        lay.addWidget(field("Año actual", "lbl_actual", accent="green"))

        # Valor grande
        val_w = QWidget(); val_w.setObjectName("field_sep")
        vl = QHBoxLayout(val_w)
        vl.setContentsMargins(12, 0, 12, 0); vl.setSpacing(5)
        vk = QLabel("ÚLTIMO VALOR"); vk.setObjectName("field_key")
        self.lbl_valor = QLabel("—")
        self.lbl_valor.setObjectName("valor_grande")
        vu = QLabel("mm"); vu.setObjectName("field_key")
        vl.addWidget(vk); vl.addWidget(self.lbl_valor); vl.addWidget(vu)
        lay.addWidget(val_w)

        lay.addWidget(field("N° anillos", "lbl_n", accent="blue"))
        lay.addWidget(field("Promedio", "lbl_prom", small=True))
        lay.addWidget(field("Especie", "lbl_especie", small=True))

        # Anomalías
        anom_w = QWidget(); anom_w.setObjectName("field_sep")
        al = QHBoxLayout(anom_w)
        al.setContentsMargins(12, 0, 12, 0); al.setSpacing(6)
        ak = QLabel("ANOMALÍAS"); ak.setObjectName("field_key")
        self.lbl_anomalias = QLabel("0"); self.lbl_anomalias.setObjectName("field_val")
        al.addWidget(ak); al.addWidget(self.lbl_anomalias)
        lay.addWidget(anom_w)

        # Archivo — sin borde derecho
        arc_w = QWidget()
        al2 = QHBoxLayout(arc_w)
        al2.setContentsMargins(12, 0, 12, 0); al2.setSpacing(6)
        ak2 = QLabel("ARCHIVO"); ak2.setObjectName("field_key")
        self.lbl_archivo = QLabel("—"); self.lbl_archivo.setObjectName("field_val_sm")
        al2.addWidget(ak2); al2.addWidget(self.lbl_archivo)
        lay.addWidget(arc_w)

        lay.addStretch()
        self.serial_lbl = QLabel("⬤  Sin conexión")
        self.serial_lbl.setObjectName("serial_lbl")
        lay.addWidget(self.serial_lbl)

        self.lbl_serie.mousePressEvent = lambda e: self._edit_serie()
        self.lbl_anio.mousePressEvent  = lambda e: self._edit_anio()
        return self.info_bar

    # ── 2. Middle: Tucson + Log ───────────────────────────────────────────────
    def _build_middle(self):
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(1)

        tuc_w = QWidget(); tuc_w.setObjectName("panel_tuc")
        tl = QVBoxLayout(tuc_w)
        tl.setContentsMargins(0, 0, 0, 0); tl.setSpacing(0)
        tl.addWidget(self._panel_header("Vista previa · Tucson (.rwl)", "green"))
        self.tucson_edit = QTextEdit()
        self.tucson_edit.setReadOnly(True)
        self.tucson_edit.setObjectName("tucson_edit")
        tl.addWidget(self.tucson_edit)

        log_w = QWidget(); log_w.setObjectName("panel_log")
        ll = QVBoxLayout(log_w)
        ll.setContentsMargins(0, 0, 0, 0); ll.setSpacing(0)
        log_hdr = self._panel_header("Registro de medición", "blue")
        clear_btn = QPushButton("✕")
        clear_btn.setFixedSize(24, 24)
        clear_btn.setObjectName("clear_btn")
        clear_btn.clicked.connect(self._clear_log)
        log_hdr.layout().addWidget(clear_btn)
        ll.addWidget(log_hdr)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setObjectName("log_edit")
        ll.addWidget(self.log_edit)

        self.splitter.addWidget(tuc_w)
        self.splitter.addWidget(log_w)
        self.splitter.setSizes([750, 250])
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        return self.splitter

    # ── 4. Bottom bar ─────────────────────────────────────────────────────────
    def _build_bottom_bar(self):
        self.bottom_bar = QWidget()
        self.bottom_bar.setObjectName("bottom_bar")
        self.bottom_bar.setFixedHeight(54)
        lay = QHBoxLayout(self.bottom_bar)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(4)

        # Botones de acción (izquierda) con tooltips de atajos
        self._btn_guardar = self._make_action_btn(
            "💾", "Guardar Avance", self._guardar, "blue",
            tooltip="Guarda la medición en proceso · Ctrl+G")
        self._btn_pausa = self._make_action_btn(
            "⏸", "Pausar", self._toggle_pausa, "orange",
            tooltip="Pausar/reanudar recepción del carro · Ctrl+P")
        self._btn_deshacer = self._make_action_btn(
            "↩", "Deshacer", self._deshacer, "dim",
            tooltip="Deshacer última medición · Ctrl+Z")
        self._btn_cerrar = self._make_action_btn(
            "✓", "Guardar y Salir", self._guardar_cerrar, "green", bold=True,
            tooltip="Guarda la medición y vuelve al inicio · Ctrl+S")
        self._btn_nueva = self._make_action_btn(
            "↺", "Nueva", self._nueva_muestra, "orange",
            tooltip="Iniciar nueva muestra")
        self._btn_ayuda = self._make_action_btn(
            "?", "Ayuda", self._ayuda, "dim",
            tooltip="Atajos de teclado · F1")

        for b in [self._btn_guardar, self._btn_pausa, self._btn_deshacer,
                  self._btn_cerrar, self._btn_nueva, self._btn_ayuda]:
            lay.addWidget(b)

        lay.addStretch()

        # Botones de anomalías (icono + nombre responsivo)
        self._anom_btns = {}
        for cod, nombre, tecla, color in ANOMALIAS:
            icono = ANOM_ICONOS.get(cod, cod)
            btn = QPushButton(f"{icono} {nombre}")
            btn.setFixedHeight(38)
            btn.setToolTip(f"{tecla} — {nombre}")
            btn.setObjectName(f"anom_btn_{cod}")
            btn.setProperty("icono", icono)
            btn.setProperty("nombre", nombre)
            btn.setProperty("anom_color", color)
            _cod = cod
            btn.clicked.connect(lambda checked=False, c=_cod: self._marcar_anomalia(c))
            self._anom_btns[cod] = btn
            lay.addWidget(btn)

        self._estilo_anom_btns()
        return self.bottom_bar

    def _make_action_btn(self, icono, texto, slot, color_key, bold=False, tooltip=""):
        btn = QPushButton(f"{icono}  {texto}")
        btn.setFixedHeight(40)
        btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        btn.clicked.connect(slot)
        btn.setProperty("icono", icono)
        btn.setProperty("texto", texto)
        btn.setProperty("color_key", color_key)
        btn.setProperty("bold", bold)
        btn.setProperty("base_tooltip", tooltip)
        btn.setToolTip(tooltip)
        return btn

    def _estilo_action_btn(self, btn, compact=False):
        t = _tema_mgr.t
        icono = btn.property("icono")
        texto = btn.property("texto")
        color_key = btn.property("color_key")
        bold = btn.property("bold")
        base_tooltip = btn.property("base_tooltip") or ""
        color_map = {
            "blue": t['BLUE'], "green": t['GREEN'],
            "orange": t['ORANGE'], "dim": t['TEXT_DIM']
        }
        fg = color_map.get(color_key, t['TEXT'])
        bg = t['GREEN_DIM'] if color_key == "green" else t['BG_CARD']
        label = icono if compact else f"{icono}  {texto}"
        btn.setText(label)
        # Tooltip siempre muestra el atajo, en modo compacto además el nombre
        if compact:
            tip = f"{texto} · {base_tooltip}" if base_tooltip else texto
        else:
            tip = base_tooltip
        btn.setToolTip(tip)
        btn.setMinimumWidth(40 if compact else 0)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg};
                color: {fg};
                border: 1px solid {fg}55;
                border-radius: 7px;
                font-family: 'DejaVu Sans Mono', monospace;
                font-size: {'14px' if compact else '13px'};
                {'font-weight: 700;' if bold else 'font-weight: 600;'}
                padding: 0 {'6px' if compact else '12px'};
                min-height: 0;
            }}
            QPushButton:hover {{
                background-color: {bg};
                border: 1px solid {fg};
            }}
            QPushButton:pressed {{ background-color: {t['BG_CARD']}; }}
        """)

    def _estilo_anom_btns(self, compact=False):
        """Estiliza botones de anomalía — compacto (solo icono) o expandido (icono+nombre)."""
        tema_nombre = _tema_mgr.nombre
        for cod, btn in self._anom_btns.items():
            icono = btn.property("icono")
            nombre = btn.property("nombre")
            # Color según tema actual (adapta el amarillo del Light Ring etc)
            color = color_anomalia(cod, tema_nombre)
            if compact:
                btn.setText(icono)
                btn.setFixedWidth(38)
                btn.setMinimumWidth(38)
                btn.setMaximumWidth(38)
            else:
                btn.setText(f"{icono} {nombre}")
                btn.setFixedWidth(16777215)  # Remove fixed width
                btn.setMinimumWidth(38)
                btn.setMaximumWidth(16777215)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: {color};
                    border: 1px solid {color}66;
                    border-radius: 6px;
                    font-size: {'15px' if compact else '12px'};
                    font-weight: 700;
                    padding: 0 {'2px' if compact else '8px'};
                }}
                QPushButton:hover {{
                    background-color: {color}22;
                    border: 1px solid {color};
                }}
                QPushButton:pressed {{ background-color: {color}44; }}
            """)

    # ── Panel header ──────────────────────────────────────────────────────────
    def _panel_header(self, title, dot_color_key):
        w = QWidget(); w.setObjectName(f"phdr_{dot_color_key}")
        w.setFixedHeight(28)
        hl = QHBoxLayout(w); hl.setContentsMargins(12,0,8,0); hl.setSpacing(6)
        dot = QLabel("⬤"); dot.setObjectName(f"dot_{dot_color_key}")
        lbl = QLabel(title.upper()); lbl.setObjectName("phdr_lbl")
        hl.addWidget(dot); hl.addWidget(lbl)
        return w

    # ─────────────────────────────────────────────────────────────────────────
    # APLICAR TEMA (llamado al cambiar tema — actualiza todos los colores)
    # ─────────────────────────────────────────────────────────────────────────
    def _aplicar_tema(self, t=None):
        if t is None:
            t = _tema_mgr.t

        self.setStyleSheet(f"""
            /* ── Base ── */
            QWidget {{ background-color: {t['BG']}; color: {t['TEXT']}; }}

            /* ── Info bar ── */
            QWidget#info_bar {{
                background-color: {t['BG_BAR']};
                border-bottom: 1px solid {t['BORDER']};
            }}
            QWidget#field_sep {{
                background: transparent;
                border-right: 1px solid {t['BORDER']};
            }}
            QWidget#field_editable {{
                background-color: {t['BG_CARD']};
                border: 1px solid {t['BLUE']}55;
                border-right: 1px solid {t['BORDER']};
                border-radius: 6px;
                margin: 4px 0;
            }}
            QWidget#field_editable:hover {{
                border: 1px solid {t['BLUE']};
            }}
            QLabel#edit_icon {{
                font-size: 12px; color: {t['BLUE']};
                background: transparent;
            }}
            QLabel#field_key {{
                font-size: 11px; color: {t['TEXT_DIM']};
                letter-spacing: 1px; background: transparent;
                font-weight: 700;
            }}
            QLabel#field_val {{
                font-size: 15px; color: {t['TEXT_TITLE']};
                font-weight: 700; background: transparent;
            }}
            QLabel#field_val_sm {{
                font-size: 13px; color: {t['TEXT']};
                font-weight: 600; background: transparent;
            }}
            QLabel#valor_grande {{
                font-size: 26px; font-weight: 300;
                color: {t['GREEN']};
                font-family: 'Sora','Segoe UI',sans-serif;
                background: transparent;
            }}
            QLabel#serial_lbl {{
                font-size: 13px; color: {t['ORANGE']};
                background: transparent; padding-left: 10px;
            }}

            /* ── Panels ── */
            QWidget#panel_tuc {{ background-color: {t['BG']}; }}
            QWidget#panel_log {{
                background-color: {t['BG_BAR']};
                border-left: 1px solid {t['BORDER']};
            }}
            QWidget#panel_chart {{
                background-color: {t['BG']};
                border-top: 1px solid {t['BORDER']};
            }}
            QWidget#phdr_green, QWidget#phdr_blue {{
                background-color: {t['BG_BAR']};
                border-bottom: 1px solid {t['BORDER']};
            }}
            QLabel#phdr_lbl {{
                font-size: 10px; color: {t['TEXT_DIM']};
                text-transform: uppercase; letter-spacing: 1px;
                background: transparent;
            }}
            QLabel#dot_green {{ font-size: 8px; color: {t['GREEN']}; background: transparent; }}
            QLabel#dot_blue  {{ font-size: 8px; color: {t['BLUE']};  background: transparent; }}
            QLabel#master_lbl {{
                font-size: 11px; color: {t['BORDER']}; font-style: italic;
                background: transparent;
            }}

            /* ── Tucson edit ── */
            QTextEdit#tucson_edit {{
                background-color: {t['BG']};
                color: {t['TUC_LINE']};
                border: none;
                font-family: 'DejaVu Sans Mono','Courier New',monospace;
                font-size: 15px;
                padding: 8px 12px;
            }}

            /* ── Log edit ── */
            QTextEdit#log_edit {{
                background-color: {t['BG_BAR']};
                color: {t['LOG_INFO']};
                border: none;
                font-family: 'DejaVu Sans Mono','Courier New',monospace;
                font-size: 14px;
                padding: 6px 10px;
            }}

            /* ── Clear btn ── */
            QPushButton#clear_btn {{
                background: transparent; border: none;
                color: {t['TEXT_DIM']}; font-size: 13px;
            }}
            QPushButton#clear_btn:hover {{ color: {t['TEXT']}; }}

            /* ── Bottom bar ── */
            QWidget#bottom_bar {{
                background-color: {t['BG_PANEL']};
                border-top: 2px solid {t['BORDER']};
            }}
            QFrame#bar_sep {{ color: {t['BORDER']}; }}

            /* ── Splitter ── */
            QSplitter::handle {{ background-color: {t['BORDER']}; }}
            QSplitter#vsplitter::handle:vertical {{
                background-color: {t['BORDER']};
                height: 4px;
            }}
            QSplitter#vsplitter::handle:vertical:hover {{
                background-color: {t['BLUE']};
            }}
        """)

        # Actualizar botones de acción con colores del tema
        compact = self.width() < 1100
        for btn in [self._btn_guardar, self._btn_pausa, self._btn_deshacer,
                    self._btn_cerrar, self._btn_nueva, self._btn_ayuda]:
            self._estilo_action_btn(btn, compact)
        # Anomalías: compactas si ventana < 1400
        self._estilo_anom_btns(compact=self.width() < 1400)

        # Regenerar contenido HTML del Tucson preview con colores del nuevo tema
        # (el HTML tiene colores incrustados que no se actualizan solos)
        if hasattr(self, 'tucson_edit'):
            if self._meds:
                self._update_tucson()
            elif not self._referencia_fijada:
                self._mostrar_mensaje_referencia()

        # Regenerar el log con colores del nuevo tema
        if hasattr(self, 'log_edit'):
            self._regenerar_log()

    def eventFilter(self, obj, event):
        """
        Intercepta teclas F en cualquier widget hijo para llamar
        directamente la función correspondiente.
        QTextEdit readonly y FigureCanvas consumen teclas F antes
        de que lleguen a los QShortcut.
        """
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_F1:
                self._ayuda(); return True
            elif key == Qt.Key.Key_F2:
                self._marcar_anomalia("FR"); return True
            elif key == Qt.Key.Key_F3:
                self._marcar_anomalia("LR"); return True
            elif key == Qt.Key.Key_F4:
                self._marcar_anomalia("AF"); return True
            elif key == Qt.Key.Key_F5:
                self._marcar_anomalia("MR"); return True
            elif key == Qt.Key.Key_F6:
                self._marcar_anomalia("FU"); return True
            elif key == Qt.Key.Key_F7:
                self._marcar_anomalia("GO"); return True
            elif key == Qt.Key.Key_F8:
                self._marcar_anomalia("OT"); return True
            elif key == Qt.Key.Key_F9:
                self._reconectar(); return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        """Adaptar botones al ancho de la ventana."""
        super().resizeEvent(event)
        compact = self.width() < 1100
        for btn in [self._btn_guardar, self._btn_pausa, self._btn_deshacer,
                    self._btn_cerrar, self._btn_nueva, self._btn_ayuda]:
            self._estilo_action_btn(btn, compact)
        # Anomalías: mostrar nombres si hay espacio (>1400px)
        self._estilo_anom_btns(compact=self.width() < 1400)

    # ─────────────────────────────────────────────────────────────────────────
    # ATAJOS
    # ─────────────────────────────────────────────────────────────────────────
    def _register_shortcuts(self):
        ctx = Qt.ShortcutContext.WindowShortcut
        def sc(key, fn):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(ctx)
            s.activated.connect(fn)
            return s
        sc("Ctrl+G", self._guardar)
        sc("Ctrl+S", self._guardar_cerrar)
        sc("Ctrl+Z", self._deshacer)
        sc("Ctrl+P", self._toggle_pausa)
        sc("Ctrl+Y", self._edit_anio)
        sc("F1",     self._ayuda)
        sc("F9",     self._reconectar)
        for anom in ANOMALIAS:
            _cod = anom[0]; _key = anom[2]
            s = QShortcut(QKeySequence(_key), self)
            s.setContext(ctx)
            s.activated.connect(lambda c=_cod: self._marcar_anomalia(c))

    # ─────────────────────────────────────────────────────────────────────────
    # ACCIONES
    # ─────────────────────────────────────────────────────────────────────────
    def _guardar(self):
        if not self._meds:
            self.log("warn", "Sin mediciones para guardar.")
            return
        archivo = self._cfg['archivo']
        if not os.path.isabs(archivo):
            from PyQt6.QtWidgets import QFileDialog
            # Usar la última ruta guardada como directorio inicial
            ultima_ruta = self._cfg.get('ultima_ruta', '')
            dir_inicial = ultima_ruta if ultima_ruta and os.path.isdir(ultima_ruta) else os.path.expanduser("~")
            ruta, _ = QFileDialog.getSaveFileName(
                self, "Guardar archivo Tucson",
                os.path.join(dir_inicial, archivo),
                "Tucson RWL (*.rwl);;Texto (*.txt);;Todos los archivos (*)",
                options=QFileDialog.Option.DontUseNativeDialog
            )
            if not ruta:
                self.log("warn", "Guardado cancelado.")
                return
            self._cfg['archivo'] = ruta
            self._cfg['ultima_ruta'] = os.path.dirname(ruta)
            self.lbl_archivo.setText(os.path.basename(ruta))
            archivo = ruta
            # Persistir la ruta para futuras sesiones
            from PyQt6.QtCore import QSettings
            from constantes import SETTINGS_ORG, SETTINGS_APP, SETTINGS_ULTIMA_RUTA
            try:
                QSettings(SETTINGS_ORG, SETTINGS_APP).setValue(SETTINGS_ULTIMA_RUTA, os.path.dirname(ruta))
            except Exception:
                pass
        try:
            escribir_tucson(self._cfg['serie'], self._cfg['anio'], self._meds, archivo)
            self.log("ok", f"✓ Guardado: {os.path.basename(archivo)}")
            self.log("ok", f"  {len(self._meds)} anillos")
            self._play_beep(BEEP_SAVE_FREQ, BEEP_SAVE_DUR)
            QTimer.singleShot(130, lambda: self._play_beep(BEEP_SAVE_FREQ2, BEEP_SAVE_DUR2))
        except Exception as e:
            self.log("err", f"Error al guardar: {e}")

    def _guardar_cerrar(self):
        self._guardar()
        if self._eventos:
            try:
                archivo = self._cfg['archivo']
                anio_corteza = self._cfg['anio'] + len(self._meds) - 1
                ruta = escribir_sidecar(
                    self._cfg['serie'], self._cfg['anio'], anio_corteza,
                    self._cfg['especie'], self._eventos, archivo
                )
                self.log("ok", f"✓ Sidecar: {os.path.basename(ruta)}")
            except Exception as e:
                self.log("err", f"Error sidecar: {e}")
        self._stop_serial()
        win = self.window()
        if hasattr(win, 'mostrar_config'):
            win.mostrar_config()

    def _deshacer(self):
        if not self._meds:
            self.log("warn", "No hay mediciones para deshacer.")
            return
        ultimo = self._meds.pop()
        year = self._cfg['anio'] + len(self._meds)
        self.lbl_valor.setText("↩")
        self.log("warn", f"Deshecho: año {year} · {ultimo/1000:.3f} mm")
        self._refresh_stats()
        self._update_tucson()
        # Actualizar panel de co-datación en tiempo real
        self.panel_maestra.actualizar_mediciones(
            self._meds, self._cfg['anio'], self._cfg['serie'], self._eventos)

    def _toggle_pausa(self):
        self._paused = not self._paused
        if self._worker:
            self._worker.set_paused(self._paused)
        t = _tema_mgr.t
        if self._paused:
            # Guardar avance automáticamente al pausar
            if self._meds:
                self._guardar()
            self.log("warn", MSG_PAUSA)
            self._btn_pausa.setProperty("icono", "▶")
            self._btn_pausa.setProperty("texto", "Reanudar")
            self._estilo_action_btn(self._btn_pausa, self.width() < 1100)
        else:
            self.log("ok", MSG_REANUDA)
            self._btn_pausa.setProperty("icono", "⏸")
            self._btn_pausa.setProperty("texto", "Pausar")
            self._estilo_action_btn(self._btn_pausa, self.width() < 1100)

    def _reconectar(self):
        self.log("", "Buscando puerto serie...")
        self._stop_serial()
        from serial_worker import detectar_puerto_auto, listar_puertos
        # Mostrar puertos disponibles para diagnóstico
        puertos = listar_puertos()
        if puertos:
            self.log("", f"Puertos detectados: {', '.join(p for p,_ in puertos)}")
        port = detectar_puerto_auto()
        if port:
            self._cfg['port'] = port
            self.log("ok", f"Puerto seleccionado: {port}")
            QTimer.singleShot(500, self._start_serial)
        else:
            self.log("warn", "No se encontró ningún puerto USB-serial.")
            self.log("", "Conecta el carro y presiona F9 de nuevo.")

    def _ayuda(self):
        dlg = DialogoAyuda(self)
        dlg.exec()

    def _marcar_anomalia(self, codigo_anom: str):
        if not self._meds:
            self.log("warn", "Mide al menos un anillo antes de marcar una anomalía.")
            return
        year = self._cfg['anio'] + len(self._meds) - 1
        nombre = ANOMALIA_CODIGOS.get(codigo_anom, codigo_anom)
        if self._eventos.get(year) == codigo_anom:
            del self._eventos[year]
            self.log("warn", f"Anomalía eliminada: {year} · {nombre}")
        else:
            self._eventos[year] = codigo_anom
            self.log("decade", f"[{year}]  ▶ {codigo_anom} — {nombre}")
        self._update_tucson()
        self._update_anomalia_bar()

    def _update_anomalia_bar(self):
        n = len(self._eventos)
        t = _tema_mgr.t
        self.lbl_anomalias.setText(str(n) if n == 0 else f"{n} ▶")
        color = t['ORANGE'] if n > 0 else t['TEXT_DIM']
        self.lbl_anomalias.setStyleSheet(
            f"font-size:15px;font-weight:700;color:{color};background:transparent;")

    def _nueva_muestra(self):
        dlg = DialogoNuevaMuestra(self)
        if dlg.exec():
            res = dlg.get_result()
            if res:
                if self._meds:
                    self._guardar()
                self.nueva_muestra_solicitada.emit(res)

    # ── Edición en línea ──────────────────────────────────────────────────────
    def _edit_serie(self):
        val, ok = QInputDialog.getText(self, "Editar nombre de serie",
                                       "Nuevo nombre:", text=self.lbl_serie.text())
        if ok and val.strip():
            self._cfg['serie'] = val.strip()
            self.lbl_serie.setText(val.strip())
            self.log("warn", f"Serie cambiada a {val.strip()}")
            self._update_tucson()

    def _edit_anio(self):
        val, ok = QInputDialog.getInt(self, "Editar año de inicio",
                                      "Nuevo año:", value=self._cfg['anio'],
                                      min=-10000, max=2200)
        if ok:
            self._cfg['anio'] = val
            self.lbl_anio.setText(str(val))
            self.log("warn", f"Año inicio ajustado a {val}")
            self._update_tucson()
    
    # ─────────────────────────────────────────────────────────────────────────
    # SERIAL
    # ─────────────────────────────────────────────────────────────────────────
    def _start_serial(self):
        port = self._cfg.get('port')
        if not port:
            self.log("warn", "Sin puerto configurado.")
            self.log("", "Conecta el carro y presiona F9 para reconectar.")
            return
        self._worker = SerialWorker(port, self._cfg['baud'])
        self._worker.valor_recibido.connect(self._on_valor)
        self._worker.log_message.connect(self.log)
        self._worker.conexion_ok.connect(self._on_conexion_ok)
        self._worker.conexion_perdida.connect(self._on_conexion_perdida)
        self._worker.start()

    def _stop_serial(self):
        if self._worker:
            self._worker.stop()
            self._worker.wait(2000)
            self._worker = None

    def _on_conexion_ok(self, port: str):
        self.serial_lbl.setText(f"⬤  {port}")
        self.serial_lbl.setStyleSheet(
            f"font-size:13px;color:{_tema_mgr.t['GREEN']};background:transparent;padding-left:10px;")
        self.log("ok", f"Puerto conectado: {port}")
        self.log("warn", "▶ Presiona el botón del carro para fijar la referencia.")

    def _on_conexion_perdida(self):
        self.serial_lbl.setText("⬤  Sin conexión")
        self.serial_lbl.setStyleSheet(
            f"font-size:13px;color:{_tema_mgr.t['RED']};background:transparent;padding-left:10px;")
        self.log("err",  "Conexión con el carro perdida.")
        self.log("warn", "Presiona F9 para reconectar.")

    # ─────────────────────────────────────────────────────────────────────────
    # RECEPCIÓN DE VALORES
    # ─────────────────────────────────────────────────────────────────────────
    def _on_valor(self, mil: int):
        if self._paused:
            return
        # Al recibir el primer valor, marcar referencia como fijada
        if not self._referencia_fijada:
            self._referencia_fijada = True
        year = self._cfg['anio'] + len(self._meds)
        self._meds.append(mil)
        is_decade = year > self._cfg['anio'] and year % 10 == 0
        mm = mil / 1000.0
        self.lbl_valor.setText(f"{mm:.3f}")
        self.lbl_actual.setText(str(year))
        if is_decade:
            self.log("decade", f"♪  Década {year}")
            self._play_beep(BEEP_DECADE_FREQ, BEEP_DECADE_DUR)
        else:
            self.log("val", f"[{year}]  {mm:.3f} mm")
        self._refresh_stats()
        # Debounce de la vista Tucson — actualiza 250ms después del último anillo
        if not self._tucson_timer.isActive():
            self._tucson_timer.start(250)
        self.panel_maestra.actualizar_mediciones(
            self._meds, self._cfg['anio'], self._cfg['serie'], self._eventos)

    # ─────────────────────────────────────────────────────────────────────────
    # ACTUALIZACIÓN DE PANELES
    # ─────────────────────────────────────────────────────────────────────────
    def _refresh_info(self):
        cfg = self._cfg
        self.lbl_serie.setText(cfg['serie'])
        self.lbl_anio.setText(str(cfg['anio']))
        self.lbl_especie.setText(cfg.get('especie', '—'))
        self.lbl_archivo.setText(os.path.basename(cfg['archivo']))

    def _refresh_stats(self):
        n = len(self._meds)
        self.lbl_n.setText(str(n))
        if n > 0:
            prom_mm = (sum(self._meds) / n) / 1000.0
            self.lbl_prom.setText(f"{prom_mm:.3f} mm")
            self.lbl_actual.setText(str(self._cfg['anio'] + n - 1))
        else:
            self.lbl_prom.setText("—")
            self.lbl_actual.setText("—")

    def _update_tucson(self):
        txt = preview_tucson(self._cfg['serie'], self._cfg['anio'],
                             self._meds, self._eventos)
        t = _tema_mgr.t
        html_lines = []
        for i, line in enumerate(txt.split('\n')):
            if line.startswith(';'):
                color = t['TUC_HDR']
            elif '-9999' in line:
                color = t['TUC_CUR']
            else:
                color = t['TUC_LINE']
            escaped = line.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
            html_lines.append(
                f'<span style="color:{color};font-size:15px">{escaped}</span>')
        self.tucson_edit.setHtml(
            '<pre style="margin:0;padding:0">' + '<br>'.join(html_lines) + '</pre>')
        c = self.tucson_edit.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        self.tucson_edit.setTextCursor(c)

    def _mostrar_mensaje_referencia(self):
        """Muestra mensaje de espera en la vista Tucson antes de fijar referencia."""
        t = _tema_mgr.t
        # Color principal del mensaje — siempre visible en ambos temas
        c_main = t["TEXT_TITLE"]
        c_acc = t["ORANGE"]
        c_sub = t["TEXT_DIM"]
        html = (
            f'<br><br>'
            f'<p align="center">'
            f'<font color="{c_acc}" size="6"><b>▶</b></font>'
            f'&nbsp;&nbsp;'
            f'<font color="{c_main}" size="5"><b>'
            f'Presiona el botón del carro para fijar la referencia'
            f'</b></font></p>'
            f'<br>'
            f'<p align="center">'
            f'<font color="{c_sub}" size="4">'
            f'Las mediciones aparecerán aquí en formato Tucson'
            f'</font></p>'
        )
        self.tucson_edit.setHtml(html)

    # ─────────────────────────────────────────────────────────────────────────
    # LOG
    # ─────────────────────────────────────────────────────────────────────────
    LOG_COLORS = {
        "ok":     "LOG_OK",
        "warn":   "LOG_WARN",
        "err":    "LOG_ERR",
        "val":    "LOG_VAL",
        "decade": "LOG_DECADE",
        "":       "LOG_INFO",
        "info":   "LOG_INFO",
    }

    def log(self, level: str, msg: str):
        from datetime import datetime
        t = _tema_mgr.t
        ts = datetime.now().strftime("%H:%M:%S")
        # Guardar entrada para poder regenerar al cambiar tema
        self._log_entries.append((ts, level, msg))
        color_key = self.LOG_COLORS.get(level, "LOG_INFO")
        color = t.get(color_key, t['LOG_INFO'])
        html = (f'<span style="font-size:14px;color:{t["LOG_TIME"]}">{ts}&nbsp;</span>'
                f'<span style="font-size:14px;color:{color}">{msg}</span>')
        self.log_edit.append(html)
        c = self.log_edit.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        self.log_edit.setTextCursor(c)

    def _regenerar_log(self):
        """Regenera el log con los colores del tema actual.
        Necesario porque los <span style=...> incrustados tienen el color del tema viejo."""
        t = _tema_mgr.t
        self.log_edit.clear()
        for ts, level, msg in self._log_entries:
            color_key = self.LOG_COLORS.get(level, "LOG_INFO")
            color = t.get(color_key, t['LOG_INFO'])
            html = (f'<span style="font-size:14px;color:{t["LOG_TIME"]}">{ts}&nbsp;</span>'
                    f'<span style="font-size:14px;color:{color}">{msg}</span>')
            self.log_edit.append(html)
        c = self.log_edit.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        self.log_edit.setTextCursor(c)

    def _clear_log(self):
        self.log_edit.clear()
        self._log_entries.clear()

    # ─────────────────────────────────────────────────────────────────────────
    # SONIDO
    # ─────────────────────────────────────────────────────────────────────────
    def _play_beep(self, freq: int = 880, duration_ms: int = 150):
        """Reproduce un beep SIN bloquear la interfaz (en hilo separado)."""
        import threading
        threading.Thread(
            target=self._beep_worker, args=(freq, duration_ms), daemon=True
        ).start()

    @staticmethod
    def _beep_worker(freq: int, duration_ms: int):
        try:
            import platform
            sistema = platform.system()

            if sistema == "Windows":
                import winsound
                winsound.Beep(freq, duration_ms)
                return

            # Linux / macOS: generar .wav temporal
            import subprocess, tempfile, wave, struct, math
            sr, dur_s = 44100, duration_ms / 1000.0
            samples = [int(32767 * math.sin(2 * math.pi * freq * i / sr))
                       for i in range(int(sr * dur_s))]
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                tmp = f.name
            with wave.open(tmp, 'w') as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
                wf.writeframes(struct.pack(f'<{len(samples)}h', *samples))
            if sistema == "Linux":
                subprocess.Popen(["aplay", "-q", tmp], stderr=subprocess.DEVNULL)
            elif sistema == "Darwin":
                subprocess.Popen(["afplay", tmp], stderr=subprocess.DEVNULL)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # LIMPIEZA
    # ─────────────────────────────────────────────────────────────────────────
    def closeEvent(self, event):
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().removeEventFilter(self)
        _tema_mgr.desuscribir(self._aplicar_tema)
        self._stop_serial()
        super().closeEvent(event)
