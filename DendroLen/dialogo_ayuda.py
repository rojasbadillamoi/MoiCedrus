# dialogo_ayuda.py — DendroLen

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QWidget   # ← QWidget faltaba
)
from PyQt6.QtCore import Qt
from constantes import ATAJOS, APP_NAME, APP_VERSION
from tema_manager import tema as _tema_mgr


class DialogoAyuda(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ayuda — Atajos de teclado")
        self.setFixedWidth(520)
        self._build_ui()

    def _build_ui(self):
        t = _tema_mgr.t
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 20)
        lay.setSpacing(14)

        title = QLabel(f"{APP_NAME}  ·  Atajos de teclado")
        title.setStyleSheet(f"""
            font-size: 16px; font-weight: 700; color: {t['TEXT_TITLE']};
            font-family: 'Sora', 'Segoe UI', sans-serif;
        """)
        lay.addWidget(title)

        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {t['BG_CARD']};
                border: 1px solid {t['BORDER']};
                border-radius: 8px;
            }}
        """)
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(0)

        for i, (key, desc) in enumerate(ATAJOS):
            row = QWidget()
            row.setStyleSheet(f"""
                QWidget {{
                    background: transparent;
                    border: none;
                    {'border-top: 1px solid ' + t['BORDER'] + ';' if i > 0 else ''}
                }}
            """)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(14, 9, 14, 9)
            kbd = QLabel(key)
            kbd.setFixedWidth(140)
            kbd.setAlignment(Qt.AlignmentFlag.AlignCenter)
            kbd.setStyleSheet(f"""
                background-color: {t['BG_BAR']};
                border: 1px solid {t['BORDER']};
                border-radius: 5px;
                color: {t['BLUE']};
                font-size: 13px;
                font-weight: 700;
                padding: 3px 8px;
            """)
            desc_l = QLabel(desc)
            desc_l.setStyleSheet(f"font-size: 13px; color: {t['TEXT']};")
            rl.addWidget(kbd)
            rl.addSpacing(14)
            rl.addWidget(desc_l)
            rl.addStretch()
            fl.addWidget(row)

        lay.addWidget(frame)

        nota = QLabel(f"v{APP_VERSION}  ·  Los atajos funcionan solo durante la medición activa.")
        nota.setStyleSheet(f"font-size: 11px; color: {t['TEXT_DIM']};")
        lay.addWidget(nota)

        btn = QPushButton("Cerrar")
        btn.setFixedHeight(36)
        btn.clicked.connect(self.accept)
        foot = QHBoxLayout()
        foot.addStretch()
        foot.addWidget(btn)
        lay.addLayout(foot)
