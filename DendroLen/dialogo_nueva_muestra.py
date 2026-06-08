# dialogo_nueva_muestra.py — DendroLen

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QSpinBox, QComboBox, QPushButton, QSizePolicy
)
from PyQt6.QtCore import Qt, QSettings
from constantes import ESPECIES, SETTINGS_ORG, SETTINGS_APP, SETTINGS_ESPECIE
from tema_manager import tema as _tema_mgr


def _cargar_especie():
    try:
        return QSettings(SETTINGS_ORG, SETTINGS_APP).value(SETTINGS_ESPECIE, "No registrada")
    except: return "No registrada"

def _guardar_especie(e):
    try: QSettings(SETTINGS_ORG, SETTINGS_APP).setValue(SETTINGS_ESPECIE, e)
    except: pass


class DialogoNuevaMuestra(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nueva muestra")
        self.setFixedWidth(380)
        self._result = None
        self._build_ui()

    def _build_ui(self):
        t = _tema_mgr.t
        self.setStyleSheet(f"background-color:{t['BG_CARD']};border-radius:10px;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(14)

        t1 = QLabel("Nueva muestra")
        t1.setStyleSheet(f"font-size:16px;font-weight:700;color:{t['TEXT_TITLE']};font-family:'Sora','Segoe UI',sans-serif;")
        t2 = QLabel("El radio actual se guardará automáticamente.")
        t2.setStyleSheet(f"font-size:12px;color:{t['TEXT_DIM']};")
        lay.addWidget(t1); lay.addWidget(t2)

        # Código + Año
        row1 = QHBoxLayout(); row1.setSpacing(12)
        c1 = QVBoxLayout(); c1.setSpacing(5)
        c1.addWidget(self._lbl("Código"))
        self.inp_codigo = QLineEdit()
        self.inp_codigo.setPlaceholderText("ej. MUE002A")
        self.inp_codigo.setMinimumHeight(34)
        c1.addWidget(self.inp_codigo)
        c2 = QVBoxLayout(); c2.setSpacing(5)
        c2.addWidget(self._lbl("Año inicio"))
        self.spn_anio = QSpinBox()
        self.spn_anio.setRange(-10000, 2200)
        self.spn_anio.setValue(1850)
        self.spn_anio.setMinimumHeight(34)
        c2.addWidget(self.spn_anio)
        row1.addLayout(c1, 3); row1.addLayout(c2, 2)
        lay.addLayout(row1)

        # Especie editable
        e_lay = QVBoxLayout(); e_lay.setSpacing(5)
        e_lay.addWidget(self._lbl("Especie"))
        self.cmb_especie = QComboBox()
        self.cmb_especie.setEditable(True)
        self.cmb_especie.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        self.cmb_especie.setMinimumHeight(34)
        self.cmb_especie.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for e in ESPECIES: self.cmb_especie.addItem(e)
        ultima = _cargar_especie()
        idx = self.cmb_especie.findText(ultima)
        if idx >= 0: self.cmb_especie.setCurrentIndex(idx)
        else: self.cmb_especie.setEditText(ultima)
        e_lay.addWidget(self.cmb_especie)
        lay.addLayout(e_lay)

        # Botones
        foot = QHBoxLayout(); foot.setSpacing(10)
        foot.addStretch()
        cancel = QPushButton("Cancelar")
        cancel.setMinimumHeight(34)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("▷  Iniciar")
        ok.setMinimumHeight(34)
        ok.setStyleSheet(f"""
            QPushButton {{
                background-color:{t['GREEN']};border:none;border-radius:7px;
                color:white;font-size:14px;font-weight:700;padding:0 20px;
            }}
            QPushButton:hover{{background-color:#2ecc71;}}
        """)
        ok.clicked.connect(self._confirmar)
        foot.addWidget(cancel); foot.addWidget(ok)
        lay.addLayout(foot)

    def _lbl(self, text):
        t = _tema_mgr.t
        l = QLabel(text.upper())
        l.setStyleSheet(f"font-size:11px;color:{t['TEXT_DIM']};letter-spacing:1px;font-weight:700;")
        return l

    def _confirmar(self):
        codigo = self.inp_codigo.text().strip()
        if not codigo: self.inp_codigo.setFocus(); return
        especie = self.cmb_especie.currentText().strip() or "No registrada"
        _guardar_especie(especie)
        self._result = {
            "codigo": codigo, "anio": self.spn_anio.value(),
            "serie": codigo, "especie": especie,
            "archivo": f"{codigo}.rwl",
        }
        self.accept()

    def get_result(self):
        return self._result
