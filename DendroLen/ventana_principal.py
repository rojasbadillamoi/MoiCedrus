# ventana_principal.py — DendroLen

import sys
import os

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QStackedWidget,
    QLabel, QHBoxLayout, QVBoxLayout, QPushButton
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QPixmap

from constantes import APP_NAME, APP_VERSION
from tema_manager import tema


class VentanaPrincipal(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}  ·  MoiCedrus")
        self.resize(1280, 800)
        self.setMinimumSize(960, 640)
        self._modo_actual     = "config"
        self._config_guardada = None
        tema.suscribir(self._on_tema_cambio)
        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header_widget = self._make_header()
        root.addWidget(self.header_widget)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)
        self.mostrar_config()

    def _make_header(self) -> QWidget:
        t = tema.t
        bar = QWidget()
        bar.setFixedHeight(46)
        bar.setObjectName("main_header")
        bar.setStyleSheet(f"""
            QWidget#main_header {{
                background-color: {t['BG_PANEL']};
                border-bottom: 2px solid {t['BORDER']};
            }}
        """)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)

        logo = QLabel()
        logo.setFixedSize(32, 32)
        icon_path = os.path.join(
            getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))),
            "DendroLen.ico"
        )
        pix = QPixmap(icon_path)
        if not pix.isNull():
            logo.setPixmap(pix.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        else:
            logo.setText("🌲")
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet("background: transparent; border: none;")
        name_lbl = QLabel(APP_NAME)
        name_lbl.setStyleSheet(f"""
            font-size: 16px; font-weight: 700; color: {t['TEXT_TITLE']};
            font-family: 'Sora', 'Segoe UI', sans-serif; background: transparent;
        """)
        sub_lbl = QLabel("MoiCedrus  ·  Carro de Medición")
        sub_lbl.setStyleSheet(f"""
            font-size: 10px; color: {t['TEXT_DIM']};
            letter-spacing: 1px; background: transparent;
        """)
        nc = QWidget()
        nc.setStyleSheet("background: transparent;")
        nl = QVBoxLayout(nc)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(1)
        nl.addWidget(name_lbl)
        nl.addWidget(sub_lbl)

        lay.addWidget(logo)
        lay.addWidget(nc)
        lay.addStretch()

        self.badge_lbl = QLabel("  Configuración  ")
        self.badge_lbl.setStyleSheet(f"""
            background-color: {t['BG_CARD']};
            border: 1px solid {t['BORDER']};
            border-radius: 10px; padding: 3px 12px;
            font-size: 11px; color: {t['TEXT_DIM']};
        """)
        lay.addWidget(self.badge_lbl)

        sep = QWidget()
        sep.setFixedSize(1, 24)
        sep.setStyleSheet(f"background-color: {t['BORDER']};")
        lay.addWidget(sep)

        self.btn_tema = QPushButton("☀" if tema.es_oscuro else "🌙")
        self.btn_tema.setFixedSize(38, 32)
        self.btn_tema.setToolTip(
            "Cambiar a tema claro" if tema.es_oscuro else "Cambiar a tema oscuro")
        self.btn_tema.setStyleSheet(f"""
            QPushButton {{
                background-color: {t['BG_CARD']}; border: 1px solid {t['BORDER']};
                border-radius: 6px; color: {t['TEXT_TITLE']};
                font-size: 17px; padding: 0; min-height: 0;
            }}
            QPushButton:hover {{ background-color: {t['BORDER']}; }}
        """)
        self.btn_tema.clicked.connect(tema.alternar)
        lay.addWidget(self.btn_tema)

        ver_lbl = QLabel(f"v{APP_VERSION}")
        ver_lbl.setStyleSheet(f"font-size: 10px; color: {t['TEXT_DIM']}; background: transparent;")
        lay.addWidget(ver_lbl)
        return bar

    # ── Cambio de tema ────────────────────────────────────────────────────────
    def _on_tema_cambio(self, t: dict):
        """
        Al cambiar tema:
        - Si estamos en config: reconstruir pantalla (sin worker serial)
        - Si estamos midiendo: NO reconstruir el panel — solo reconstruir el header
          y actualizar el badge. El panel se repinta solo con el stylesheet global.
        """
        # Reconstruir header
        root_lay = self.centralWidget().layout()
        root_lay.removeWidget(self.header_widget)
        self.header_widget.deleteLater()
        self.header_widget = self._make_header()
        root_lay.insertWidget(0, self.header_widget)
        self._set_badge(
            "Configuración" if self._modo_actual == "config"
            else f"Midiendo  ·  {self._config_guardada.get('serie','') if self._config_guardada else ''}"
        )

        # Solo reconstruir si estamos en config (no hay worker serial)
        if self._modo_actual == "config":
            QTimer.singleShot(0, self.mostrar_config)
        # Si estamos midiendo: NO tocar el panel — el stylesheet global ya se aplicó

    # ── Navegación ────────────────────────────────────────────────────────────
    def mostrar_config(self, precargar: dict = None):
        from dialogo_config import DialogoConfig
        self._modo_actual = "config"
        self._limpiar_stack()
        config = DialogoConfig()
        config.sesion_iniciada.connect(self.iniciar_medicion)
        if precargar:
            config.inp_codigo.setText(precargar.get("codigo", ""))
            config.inp_anio.setValue(precargar.get("anio", 1850))
        self.stack.addWidget(config)
        self.stack.setCurrentWidget(config)
        self._set_badge("Configuración")

    def iniciar_medicion(self, config: dict,
                         meds_previas=None, eventos_previos=None):
        try:
            from panel_medicion import PanelMedicion
            self._modo_actual     = "medir"
            self._config_guardada = config
            self._limpiar_stack()
            # Mediciones pueden venir del dict (sesión retomada) o como parámetro
            mp = meds_previas   if meds_previas   is not None else config.get("meds_previas",   [])
            ep = eventos_previos if eventos_previos is not None else config.get("eventos_previos", {})
            panel = PanelMedicion(config, meds_previas=mp, eventos_previos=ep)
            panel.nueva_muestra_solicitada.connect(self._nueva_muestra)
            self.stack.addWidget(panel)
            self.stack.setCurrentWidget(panel)
            self._set_badge(f"Midiendo  ·  {config.get('serie', '')}")
        except Exception as e:
            import traceback, os, sys
            log = os.path.join(os.path.dirname(sys.executable), "crash_log.txt")
            with open(log, "w") as f:
                traceback.print_exc(file=f)
            traceback.print_exc()

    def _nueva_muestra(self, parcial: dict):
        self.mostrar_config(precargar=parcial)

    def _limpiar_stack(self):
        while self.stack.count():
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
            # Detener worker serial si existe antes de destruir
            if hasattr(w, '_stop_serial'):
                w._stop_serial()
            w.deleteLater()

    def _set_badge(self, texto: str):
        if hasattr(self, 'badge_lbl'):
            if "Configuración" in texto:
                self.badge_lbl.hide()
            else:
                self.badge_lbl.setText(f"  {texto}  ")
                self.badge_lbl.show()

    def closeEvent(self, event):
        tema.desuscribir(self._on_tema_cambio)
        super().closeEvent(event)
