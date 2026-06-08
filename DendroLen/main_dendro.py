#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main_dendro.py — DendroLen v2.0
Punto de entrada de la aplicación.

Uso:
    python3 main_dendro.py

Dependencias:
    pip install PyQt6 matplotlib pyserial pandas numpy openpyxl
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Verificar dependencias (solo si NO es un exe empaquetado) ─────────────────
if not getattr(sys, '_MEIPASS', None):
    _missing = []
    for pkg, mod in [("PyQt6", "PyQt6"), ("matplotlib", "matplotlib"), ("pyserial", "serial")]:
        try:
            __import__(mod)
        except ImportError:
            _missing.append(pkg)
    if _missing:
        print("\n╔══════════════════════════════════════════════╗")
        print("║  DendroLen — Dependencias faltantes          ║")
        print("╠══════════════════════════════════════════════╣")
        for p in _missing:
            print(f"║  ✗  {p:<42}║")
        print("╠══════════════════════════════════════════════╣")
        print("║  Instalar con:                               ║")
        print(f"║  pip install {' '.join(_missing):<32}║")
        print("╚══════════════════════════════════════════════╝\n")
        sys.exit(1)

from PyQt6.QtWidgets import QApplication, QSplashScreen
from PyQt6.QtGui import QFont, QPixmap, QPainter, QColor
from PyQt6.QtCore import Qt
from constantes import APP_NAME, APP_VERSION, get_stylesheet, FONT_SIZE, ajustar_fuente_para_pantalla
from tema_manager import tema


def _crear_splash_pixmap() -> QPixmap:
    """Crea el pixmap del splash screen programáticamente."""
    width, height = 420, 260
    pix = QPixmap(width, height)
    pix.fill(QColor("#0e1621"))

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Borde sutil
    p.setPen(QColor("#2a3f5a"))
    p.drawRoundedRect(1, 1, width - 2, height - 2, 12, 12)

    # Intentar cargar el ícono
    icon_path = os.path.join(
        getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))),
        "DendroLen.ico"
    )
    icon_pix = QPixmap(icon_path)
    if not icon_pix.isNull():
        icon_scaled = icon_pix.scaled(72, 72,
                                       Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
        icon_x = (width - icon_scaled.width()) // 2
        p.drawPixmap(icon_x, 35, icon_scaled)

    # Nombre de la app
    font_title = QFont("Sora", 22)
    font_title.setWeight(QFont.Weight.Bold)
    font_title.setFamilies(["Sora", "Segoe UI", "Arial"])
    p.setFont(font_title)
    p.setPen(QColor("#ffffff"))
    p.drawText(0, 120, width, 40, Qt.AlignmentFlag.AlignCenter, APP_NAME)

    # Subtítulo
    font_sub = QFont("Segoe UI", 11)
    font_sub.setFamilies(["Segoe UI", "DejaVu Sans", "Arial"])
    p.setFont(font_sub)
    p.setPen(QColor("#9ab8d8"))
    p.drawText(0, 155, width, 25, Qt.AlignmentFlag.AlignCenter,
               "MoiCedrus  ·  Carro de Medición")

    # Versión
    font_ver = QFont("DejaVu Sans Mono", 9)
    font_ver.setFamilies(["DejaVu Sans Mono", "Courier New"])
    p.setFont(font_ver)
    p.setPen(QColor("#5a8aba"))
    p.drawText(0, 185, width, 20, Qt.AlignmentFlag.AlignCenter,
               f"v{APP_VERSION}")

    p.end()
    return pix


def _precargar_modulos(splash: QSplashScreen, app: QApplication):
    """
    Precarga los módulos pesados durante el splash.
    Esto hace que la transición config → medición sea instantánea.
    """
    msg_style = Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter
    msg_color = QColor("#5dade2")

    splash.showMessage("Cargando módulos base...", msg_style, msg_color)
    app.processEvents()
    import numpy          # noqa: F401
    import pandas         # noqa: F401

    splash.showMessage("Cargando gráficos...", msg_style, msg_color)
    app.processEvents()
    import matplotlib     # noqa: F401
    matplotlib.use('QtAgg')
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as _Canvas
    from matplotlib.figure import Figure as _Figure
    import matplotlib.patches                                         # noqa: F401

    # ── Calentar matplotlib: el PRIMER dibujo de la sesión es muy lento ───
    # Renderizar un canvas descartable ahora hace que el primer gráfico real
    # (en el panel de medición) aparezca de inmediato.
    try:
        _warm_fig = _Figure(figsize=(4, 2))
        _warm_canvas = _Canvas(_warm_fig)
        _ax = _warm_fig.add_subplot(111)
        _ax.plot([0, 1, 2], [0, 1, 0])
        _ax.set_title("warmup")
        _warm_canvas.draw()      # Fuerza la inicialización completa del backend
        app.processEvents()
        del _ax, _warm_canvas, _warm_fig
    except Exception:
        pass

    splash.showMessage("Cargando puerto serial...", msg_style, msg_color)
    app.processEvents()
    import serial                       # noqa: F401
    import serial.tools.list_ports      # noqa: F401

    splash.showMessage("Cargando interfaz...", msg_style, msg_color)
    app.processEvents()
    import constantes                   # noqa: F401
    import tema_manager                 # noqa: F401
    import tucson_writer                # noqa: F401
    import serial_worker                # noqa: F401
    import serie_maestra                # noqa: F401
    import panel_medicion               # noqa: F401
    import dialogo_config               # noqa: F401
    import dialogo_nueva_muestra        # noqa: F401
    import dialogo_ayuda                # noqa: F401

    splash.showMessage("Listo", msg_style, msg_color)
    app.processEvents()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("MoiCedrus")

    # ── Splash screen — aparece inmediatamente ────────────────────────────
    splash_pix = _crear_splash_pixmap()
    splash = QSplashScreen(splash_pix, Qt.WindowType.WindowStaysOnTopHint)
    splash.show()
    app.processEvents()

    # ── Ajustar tamaño de fuente según resolución de pantalla ─────────────
    try:
        screen = app.primaryScreen()
        alto = screen.size().height()
        ajustar_fuente_para_pantalla(alto)
    except Exception:
        pass  # Si falla, usar valores por defecto

    # Releer FONT_SIZE después del ajuste
    from constantes import FONT_SIZE as _FONT_SIZE
    # ── Configurar fuente y tema ──────────────────────────────────────────
    font = QFont("JetBrains Mono", _FONT_SIZE)
    font.setWeight(QFont.Weight.Medium)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFamilies(["JetBrains Mono", "Fira Mono", "DejaVu Sans Mono", "Courier New"])
    app.setFont(font)
    app.setStyleSheet(get_stylesheet(tema.t))

    # ── Precargar módulos pesados durante el splash ───────────────────────
    _precargar_modulos(splash, app)

    # ── Crear ventana principal ───────────────────────────────────────────
    from ventana_principal import VentanaPrincipal
    win = VentanaPrincipal()
    win.show()

    # ── Cerrar splash ─────────────────────────────────────────────────────
    splash.finish(win)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
