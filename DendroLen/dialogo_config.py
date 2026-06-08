# dialogo_config.py — DendroLen
# Pantalla 1: configuración de la sesión.
# Sin tarjeta flotante — el contenido llena la ventana directamente.

import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QSpinBox, QComboBox, QPushButton, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSettings

from constantes import (
    ESPECIES, BAUD_RATES,
    SETTINGS_ORG, SETTINGS_APP, SETTINGS_ESPECIE,
    SETTINGS_ULTIMA_RUTA, SETTINGS_ESPECIES_CUSTOM
)
from tema_manager import tema as _tema_mgr
from serial_worker import listar_puertos, detectar_puerto_auto


def _cargar_especie() -> str:
    try:
        return QSettings(SETTINGS_ORG, SETTINGS_APP).value(SETTINGS_ESPECIE, "No registrada")
    except Exception:
        return "No registrada"


def _guardar_especie(especie: str):
    try:
        s = QSettings(SETTINGS_ORG, SETTINGS_APP)
        s.setValue(SETTINGS_ESPECIE, especie)
        # Si es nueva, agregarla a la lista persistente de especies custom
        if especie and especie not in ESPECIES:
            custom = _cargar_especies_custom()
            if especie not in custom:
                custom.append(especie)
                s.setValue(SETTINGS_ESPECIES_CUSTOM, custom)
    except Exception:
        pass


def _cargar_especies_custom() -> list:
    """Carga las especies agregadas por el usuario."""
    try:
        val = QSettings(SETTINGS_ORG, SETTINGS_APP).value(SETTINGS_ESPECIES_CUSTOM, [])
        if isinstance(val, list):
            return val
        if isinstance(val, str) and val:
            return [val]
        return []
    except Exception:
        return []


def _cargar_ultima_ruta() -> str:
    """Carga la última ruta usada para guardar."""
    try:
        return QSettings(SETTINGS_ORG, SETTINGS_APP).value(SETTINGS_ULTIMA_RUTA, "")
    except Exception:
        return ""


def _guardar_ultima_ruta(ruta: str):
    """Guarda la carpeta de la última ruta usada."""
    try:
        QSettings(SETTINGS_ORG, SETTINGS_APP).setValue(SETTINGS_ULTIMA_RUTA, os.path.dirname(ruta))
    except Exception:
        pass


def _leer_mediciones_rwl(ruta: str):
    """
    Lee las mediciones de un archivo .rwl existente.
    Devuelve (codigo, anio_inicio, mediciones_en_milesimas) o (None, None, []).
    """
    from constantes import TUCSON_SAMPLE_ID_WIDTH, TUCSON_TERMINATOR
    try:
        meds = []
        cod, anio = None, None
        with open(ruta, encoding="utf-8", errors="ignore") as f:
            for linea in f:
                linea = linea.rstrip()
                if not linea or linea.startswith(";"):
                    continue
                if len(linea) < 12:
                    continue
                c = linea[:TUCSON_SAMPLE_ID_WIDTH].strip()
                try:
                    a = int(linea[TUCSON_SAMPLE_ID_WIDTH:TUCSON_SAMPLE_ID_WIDTH+4])
                except ValueError:
                    continue
                if cod is None:
                    cod, anio = c, a
                pos = TUCSON_SAMPLE_ID_WIDTH + 4
                while pos + 6 <= len(linea):
                    try:
                        v = int(linea[pos:pos+6].strip())
                        if v == TUCSON_TERMINATOR or v == -9999:
                            break
                        meds.append(v)
                    except ValueError:
                        break
                    pos += 6
        return cod, anio, meds
    except Exception:
        return None, None, []


class DialogoConfig(QWidget):
    sesion_iniciada = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ruta_retomar = None
        self._port_timer = QTimer(self)
        self._port_timer.setSingleShot(True)
        self._port_timer.timeout.connect(self._auto_detect_port)
        self._build_ui()
        self._port_timer.start(500)

    def _build_ui(self):
        t = _tema_mgr.t
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 20)
        root.setSpacing(18)

        # ── Estilo común para campos editables ────────────────────────────
        input_style = f"""
            QLineEdit, QSpinBox, QComboBox {{
                background-color: {t['BG_CARD']};
                color: {t['TEXT_TITLE']};
                border: 2px solid {t['BORDER']};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 15px;
                font-weight: 500;
                min-height: 36px;
            }}
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
                border: 2px solid {t['BLUE']};
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                subcontrol-origin: border;
                width: 24px;
                background-color: {t['BORDER_SOFT']};
                border: 1px solid {t['BORDER']};
            }}
            QSpinBox::up-button {{
                subcontrol-position: top right;
                border-top-right-radius: 5px;
            }}
            QSpinBox::down-button {{
                subcontrol-position: bottom right;
                border-bottom-right-radius: 5px;
            }}
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
                background-color: {t['BORDER']};
            }}
            QSpinBox::up-arrow {{
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-bottom: 6px solid {t['TEXT']};
                width: 0; height: 0;
            }}
            QSpinBox::down-arrow {{
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid {t['TEXT']};
                width: 0; height: 0;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 28px;
                border-left: 1px solid {t['BORDER']};
                background-color: {t['BORDER_SOFT']};
                border-top-right-radius: 5px;
                border-bottom-right-radius: 5px;
            }}
            QComboBox::drop-down:hover {{
                background-color: {t['BORDER']};
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid {t['TEXT']};
                width: 0; height: 0;
            }}
            QComboBox QAbstractItemView {{
                background-color: {t['BG_CARD']};
                color: {t['TEXT_TITLE']};
                border: 1px solid {t['BORDER']};
                selection-background-color: {t['BLUE']};
                selection-color: #ffffff;
                padding: 4px;
            }}
        """

        # ── Título con botón "Unir archivos" a la derecha ────────────────
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)

        titulo = QLabel("Nueva Serie")
        titulo.setStyleSheet(f"font-size:20px;font-weight:700;color:{t['TEXT_TITLE']};font-family:'Sora','Segoe UI',sans-serif;")
        title_row.addWidget(titulo)
        title_row.addStretch()

        btn_unir = QPushButton("🔗  Unir archivos")
        btn_unir.setFixedHeight(38)
        btn_unir.setToolTip("Unir múltiples archivos .rwl en uno solo")
        btn_unir.setStyleSheet(f"""
            QPushButton {{
                background-color: {t['BG_CARD']};
                border: 2px solid {t['BORDER']};
                border-radius: 7px;
                color: {t['BLUE']};
                font-size: 14px;
                font-weight: 600;
                padding: 0 18px;
            }}
            QPushButton:hover {{
                border-color: {t['BLUE']};
                background-color: {t['BORDER_SOFT']};
            }}
        """)
        btn_unir.clicked.connect(self._abrir_unir)
        title_row.addWidget(btn_unir)
        root.addLayout(title_row)

        # ── Separador ─────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{t['BORDER']};")
        root.addWidget(sep)

        # ── Fila 1: Código + Año + Especie ────────────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(14)

        c_cod = QVBoxLayout()
        c_cod.setSpacing(5)
        c_cod.addWidget(self._lbl("Código de muestra"))
        self.inp_codigo = QLineEdit()
        self.inp_codigo.setPlaceholderText("ej. MUE001A")
        self.inp_codigo.setStyleSheet(input_style)
        c_cod.addWidget(self.inp_codigo)

        c_anio = QVBoxLayout()
        c_anio.setSpacing(5)
        c_anio.addWidget(self._lbl("Año inicio"))
        self.inp_anio = QSpinBox()
        self.inp_anio.setRange(-10000, 2200)
        self.inp_anio.setValue(1850)
        self.inp_anio.setStyleSheet(input_style)
        c_anio.addWidget(self.inp_anio)

        c_esp = QVBoxLayout()
        c_esp.setSpacing(5)
        c_esp.addWidget(self._lbl("Especie"))
        self.cmb_especie = QComboBox()
        self.cmb_especie.setEditable(True)
        self.cmb_especie.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        self.cmb_especie.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.cmb_especie.setStyleSheet(input_style)
        for e in ESPECIES:
            self.cmb_especie.addItem(e)
        # Agregar especies custom del usuario
        for e in _cargar_especies_custom():
            if self.cmb_especie.findText(e) < 0:
                self.cmb_especie.addItem(e)
        ultima = _cargar_especie()
        idx = self.cmb_especie.findText(ultima)
        if idx >= 0:
            self.cmb_especie.setCurrentIndex(idx)
        else:
            self.cmb_especie.setEditText(ultima)
        self.cmb_especie.currentTextChanged.connect(_guardar_especie)
        self.cmb_especie.lineEdit().editingFinished.connect(
            lambda: _guardar_especie(self.cmb_especie.currentText()))
        c_esp.addWidget(self.cmb_especie)

        row1.addLayout(c_cod, 3)
        row1.addLayout(c_anio, 2)
        row1.addLayout(c_esp, 4)
        root.addLayout(row1)

        # ── Fila 2: Puerto serie ──────────────────────────────────────────
        root.addWidget(self._lbl("Puerto serie"))
        port_row = QHBoxLayout()
        port_row.setSpacing(10)
        self.port_box = QLineEdit()
        self.port_box.setReadOnly(True)
        self.port_box.setPlaceholderText("Buscando puertos USB...")
        self.port_box.setStyleSheet(f"""
            QLineEdit {{
                background-color: {t['BG_BAR']};
                color: {t['ORANGE']};
                border: 2px solid {t['BORDER']};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 14px;
                min-height: 36px;
            }}
        """)
        detect_btn = QPushButton("⟳")
        detect_btn.setFixedSize(40, 40)
        detect_btn.setToolTip("Detectar puertos USB")
        detect_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {t['BORDER_SOFT']};
                border: 1px solid {t['BORDER']};
                border-radius: 6px;
                color: {t['TEXT_TITLE']};
                font-size: 18px;
                font-weight: 700;
                padding: 0;
            }}
            QPushButton:hover {{ background-color: {t['BORDER']}; }}
        """)
        detect_btn.clicked.connect(self._detect_port_manual)
        port_row.addWidget(self.port_box, 1)
        port_row.addWidget(detect_btn)
        root.addLayout(port_row)
        self.port_hint = QLabel("Se priorizan adaptadores USB-serial (FTDI, CH340, CP210x)")
        self.port_hint.setStyleSheet(f"font-size:12px;color:{t['TEXT_DIM']};")
        root.addWidget(self.port_hint)

        # ── Fila 3: Baud + Unidades + Archivo ────────────────────────────
        row3 = QHBoxLayout()
        row3.setSpacing(14)

        bc = QVBoxLayout()
        bc.setSpacing(5)
        bc.addWidget(self._lbl("Velocidad (baud)"))
        self.cmb_baud = QComboBox()
        self.cmb_baud.setStyleSheet(input_style)
        for b in BAUD_RATES:
            self.cmb_baud.addItem(str(b))
        bc.addWidget(self.cmb_baud)

        uc = QVBoxLayout()
        uc.setSpacing(5)
        uc.addWidget(self._lbl("Unidades del carro"))
        self.cmb_unidades = QComboBox()
        self.cmb_unidades.setStyleSheet(input_style)
        self.cmb_unidades.addItems(["0.001 mm (µm)", "0.01 mm"])
        uc.addWidget(self.cmb_unidades)

        ac = QVBoxLayout()
        ac.setSpacing(5)
        ac.addWidget(self._lbl("Archivo de salida (.rwl)"))
        self.inp_archivo = QLineEdit()
        self.inp_archivo.setStyleSheet(input_style)
        ac.addWidget(self.inp_archivo)

        row3.addLayout(bc, 2)
        row3.addLayout(uc, 2)
        row3.addLayout(ac, 4)
        root.addLayout(row3)

        # ── Fila 4: Estado del puerto + Botón iniciar ────────────────────
        foot_row = QHBoxLayout()
        foot_row.setContentsMargins(0, 8, 0, 0)
        self.port_status_lbl = QLabel("⚠  Sin puerto detectado")
        self.port_status_lbl.setStyleSheet(f"font-size:13px;color:{t['ORANGE']};")
        foot_row.addWidget(self.port_status_lbl)
        foot_row.addStretch()
        self.btn_iniciar = QPushButton("▷   Iniciar medición")
        self.btn_iniciar.setFixedHeight(40)
        self.btn_iniciar.setStyleSheet(f"""
            QPushButton {{
                background-color:{t['GREEN']};border:none;border-radius:8px;
                color:white;font-size:15px;font-weight:700;padding:0 28px;
            }}
            QPushButton:hover{{background-color:#2ecc71;}}
        """)
        self.btn_iniciar.clicked.connect(self._iniciar)
        foot_row.addWidget(self.btn_iniciar)
        root.addLayout(foot_row)

        # ── Separador ─────────────────────────────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color:{t['BORDER']};")
        root.addWidget(sep2)

        # ── Mediciones recientes ──────────────────────────────────────────
        root.addWidget(self._build_recientes())
        root.addStretch()

        self.inp_codigo.textChanged.connect(self._auto_archivo)

    def _build_recientes(self):
        t = _tema_mgr.t
        w = QWidget()
        w.setStyleSheet("background:transparent;border:none;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("MEDICIONES RECIENTES")
        lbl.setStyleSheet(f"font-size:12px;color:{t['TEXT_DIM']};letter-spacing:1px;font-weight:700;")

        btn_browse = QPushButton("📂  Buscar en otras carpetas...")
        btn_browse.setFixedHeight(36)
        btn_browse.setStyleSheet(f"""
            QPushButton {{
                background-color: {t['BG_CARD']};
                border: 2px solid {t['BORDER']};
                border-radius: 7px;
                color: {t['BLUE']};
                font-size: 14px;
                font-weight: 600;
                padding: 0 18px;
            }}
            QPushButton:hover {{
                border-color: {t['BLUE']};
                background-color: {t['BORDER_SOFT']};
            }}
        """)
        btn_browse.clicked.connect(self._browse_rwl)
        hdr_row.addWidget(lbl)
        hdr_row.addStretch()
        hdr_row.addWidget(btn_browse)
        lay.addLayout(hdr_row)

        frame = QFrame()
        frame.setStyleSheet(f"QFrame{{background-color:{t['BG_PANEL']};border:1px solid {t['BORDER']};border-radius:8px;}}")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(0)

        hdr = QLabel("  Últimos 5 archivos .rwl en la carpeta actual · clic para cargar")
        hdr.setFixedHeight(32)
        hdr.setStyleSheet(f"font-size:12px;color:{t['TEXT_DIM']};background-color:{t['BG_CARD']};border-bottom:1px solid {t['BORDER']};border-radius:8px 8px 0 0;padding-left:12px;")
        fl.addWidget(hdr)

        sesiones = self._escanear_rwl()
        if not sesiones:
            v = QLabel("  Sin archivos .rwl en la carpeta actual")
            v.setFixedHeight(40)
            v.setStyleSheet(f"font-size:13px;color:{t['TEXT_DIM']};padding-left:12px;")
            fl.addWidget(v)
        else:
            for i, s in enumerate(sesiones):
                row = QWidget()
                row.setFixedHeight(44)
                row.setStyleSheet(f"""QWidget{{background:transparent;border:none;{'border-top:1px solid '+t['BORDER']+';' if i>0 else ''}}}QWidget:hover{{background-color:{t['BG_CARD']};}}""")
                row.setCursor(Qt.CursorShape.PointingHandCursor)
                rl = QHBoxLayout(row)
                rl.setContentsMargins(12, 0, 12, 0)

                code_l = QLabel(s['cod'])
                code_l.setStyleSheet(f"font-size:14px;font-weight:700;color:{t['TEXT_TITLE']};min-width:90px;")
                info_l = QLabel(f"año {s['anio']} · {s['n']} anillos")
                info_l.setStyleSheet(f"font-size:12px;color:{t['TEXT_DIM']};min-width:130px;")
                arch_l = QLabel(s['archivo'])
                arch_l.setStyleSheet(f"font-size:11px;color:{t['BORDER']};")

                rl.addWidget(code_l)
                rl.addWidget(info_l)
                rl.addWidget(arch_l)
                rl.addStretch()

                _cod, _anio, _ruta = s['cod'], s['anio'], s.get('ruta')
                row.mousePressEvent = lambda e, c=_cod, a=_anio, r=_ruta: self._load_session(c, a, r)
                fl.addWidget(row)

        lay.addWidget(frame)
        return w

    def _abrir_unir(self):
        """Abre el diálogo de unión de archivos .rwl."""
        from modulo_unir import DialogoUnirRwl
        dlg = DialogoUnirRwl(self)
        dlg.exec()
        # Después de cerrar, refrescar la lista de mediciones recientes
        # (puede haber un nuevo .rwl en la carpeta actual)
        self._recargar_recientes()

    def _recargar_recientes(self):
        """Reconstruye el bloque de mediciones recientes."""
        # Esta función es opcional - solo refresca si el panel existe.
        # Si no quieres recargar, simplemente no llamar a este método.
        pass

    def _browse_rwl(self):
        from PyQt6.QtWidgets import QFileDialog
        t = _tema_mgr.t
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Abrir archivo de medición",
            os.path.expanduser("~"),
            "Tucson (*.rwl *.txt *.TXT);;Todos los archivos (*)",
            options=QFileDialog.Option.DontUseNativeDialog
        )
        if not ruta:
            return
        from dialogo_config import _leer_mediciones_rwl
        cod, anio, meds = _leer_mediciones_rwl(ruta)
        if cod:
            self._load_session(cod, str(anio) if anio else "1850", ruta)
            import os as _os
            self.port_hint.setText(f"✓ Archivo cargado: {_os.path.basename(ruta)} · {len(meds)} anillos")
            self.port_hint.setStyleSheet(f"font-size:12px;color:{t['BLUE']};")
        else:
            self.port_hint.setText("⚠  No se pudo leer el archivo seleccionado.")
            self.port_hint.setStyleSheet(f"font-size:12px;color:{t['ORANGE']};")

    def _escanear_rwl(self):
        import glob
        from constantes import TUCSON_SAMPLE_ID_WIDTH
        archivos = sorted(glob.glob(os.path.join(os.getcwd(), "*.rwl")),
                          key=os.path.getmtime, reverse=True)[:5]
        sesiones = []
        for ruta in archivos:
            try:
                cod, anio, n = "?", "?", 0
                with open(ruta, encoding="utf-8", errors="ignore") as f:
                    for linea in f:
                        linea = linea.strip()
                        if not linea or linea.startswith(";"): continue
                        if len(linea) >= 12:
                            cod = linea[:TUCSON_SAMPLE_ID_WIDTH].strip()
                            try: anio = linea[TUCSON_SAMPLE_ID_WIDTH:TUCSON_SAMPLE_ID_WIDTH+4].strip()
                            except: pass
                        n += 1
                sesiones.append({"cod": cod, "anio": anio, "n": max(0,(n-1)*10),
                                  "archivo": os.path.basename(ruta), "ruta": ruta})
            except: continue
        return sesiones

    def _lbl(self, text):
        t = _tema_mgr.t
        l = QLabel(text.upper())
        l.setStyleSheet(f"font-size:12px;color:{t['TEXT_DIM']};letter-spacing:1px;font-weight:700;")
        return l

    def _auto_archivo(self):
        codigo = self.inp_codigo.text().strip() or "MUE001"
        self.inp_archivo.setText(f"{codigo}.rwl")

    def _auto_detect_port(self):
        port = detectar_puerto_auto()
        if port: self._set_port_ok(port)
        else: self._set_port_none()

    def _detect_port_manual(self):
        self.port_box.setText("Buscando...")
        QTimer.singleShot(800, self._auto_detect_port)

    def _set_port_ok(self, port):
        t = _tema_mgr.t
        puertos = listar_puertos()
        desc = next((d for p,d in puertos if p==port), port)
        self.port_box.setText(f"{port}  ·  {desc}")
        self.port_box.setStyleSheet(f"QLineEdit{{background-color:{t['GREEN_DIM']};color:{t['GREEN']};border:2px solid {t['GREEN']};border-radius:6px;padding:6px 10px;font-size:14px;min-height:36px;}}")
        self.port_hint.setText("✓ Puerto USB-serial detectado")
        self.port_hint.setStyleSheet(f"font-size:12px;color:{t['GREEN']};")
        self.port_status_lbl.setText(f"✓ Puerto listo: {port}")
        self.port_status_lbl.setStyleSheet(f"font-size:13px;color:{t['GREEN']};")

    def _set_port_none(self):
        t = _tema_mgr.t
        self.port_box.setText("No se encontró ningún puerto USB-serial")
        self.port_box.setStyleSheet(f"QLineEdit{{background-color:{t['BG_BAR']};color:{t['RED']};border:2px solid {t['RED']};border-radius:6px;padding:6px 10px;font-size:14px;min-height:36px;}}")
        self.port_hint.setText("⚠  Conecta el carro al USB y presiona ⟳")
        self.port_hint.setStyleSheet(f"font-size:12px;color:{t['ORANGE']};")
        self.port_status_lbl.setText("⚠  Sin puerto — el carro no responderá hasta conectar")
        self.port_status_lbl.setStyleSheet(f"font-size:13px;color:{t['ORANGE']};")

    def _load_session(self, codigo, anio, ruta=None):
        self.inp_codigo.setText(codigo)
        try: self.inp_anio.setValue(int(anio))
        except: pass
        self.inp_archivo.setText(os.path.basename(ruta) if ruta else f"{codigo}.rwl")
        self._ruta_retomar = ruta

    def _iniciar(self):
        codigo   = self.inp_codigo.text().strip() or "MUE001"
        anio     = self.inp_anio.value()
        especie  = self.cmb_especie.currentText().strip() or "No registrada"
        archivo  = self.inp_archivo.text().strip() or f"{codigo}.rwl"
        baud     = int(self.cmb_baud.currentText())
        unidades = "micras" if self.cmb_unidades.currentIndex()==0 else "0.01mm"
        port_txt = self.port_box.text()
        port     = port_txt.split("  ·  ")[0].strip() if "·" in port_txt else None
        if port in (None, "No se encontró ningún puerto USB-serial", "Buscando..."): port = None
        _guardar_especie(especie)

        meds_previas, eventos_previos = [], {}
        ruta_abs = self._ruta_retomar
        if ruta_abs and os.path.exists(ruta_abs):
            _, _, meds_previas = _leer_mediciones_rwl(ruta_abs)
            if meds_previas:
                self.log_retoma = len(meds_previas)
            archivo = ruta_abs

        self.sesion_iniciada.emit({
            "codigo": codigo, "anio": anio, "serie": codigo,
            "especie": especie, "archivo": archivo,
            "baud": baud, "unidades": unidades, "port": port,
            "meds_previas": meds_previas,
            "eventos_previos": eventos_previos,
            "ultima_ruta": _cargar_ultima_ruta(),
        })
