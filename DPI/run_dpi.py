#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_dpi.py — Lanzador de DPI con splash screen.

Punto de entrada principal. Muestra el ícono como splash mientras
se cargan los módulos pesados.

Uso:  python run_dpi.py
"""

import sys
import os
import logging

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _ruta_recurso(nombre: str) -> str:
    if getattr(sys, '_MEIPASS', None):
        return os.path.join(sys._MEIPASS, nombre)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), nombre)


def crear_splash(app):
    from PyQt6.QtWidgets import QSplashScreen
    from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont, QPen
    from PyQt6.QtCore import Qt

    W, H = 320, 370
    pixmap = QPixmap(W, H)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillRect(0, 0, W, H, QColor("#2b2b2b"))

    # Ícono centrado (200px)
    ico_path = _ruta_recurso("dpi_icon.ico")
    if os.path.exists(ico_path):
        ico_pix = QPixmap(ico_path)
        if not ico_pix.isNull():
            s = ico_pix.scaled(200, 200,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((W - s.width()) // 2, 25, s)

    # Subtítulo
    p.setPen(QColor(255, 200, 150, 200))
    p.setFont(QFont("Arial", 12))
    p.drawText(0, 245, W, 20, Qt.AlignmentFlag.AlignCenter,
               "Dendro Pixel Interface")

    # Suite
    p.setPen(QColor(150, 150, 150, 140))
    p.setFont(QFont("Arial", 9))
    p.drawText(0, 268, W, 18, Qt.AlignmentFlag.AlignCenter,
               "MoiCedrus Dendrochronological Suite")

    # Barra inferior
    p.fillRect(0, H - 28, W, 28, QColor(0, 0, 0, 100))
    p.setPen(QPen(QColor("#FF8C00")))
    p.drawLine(0, H - 28, W, H - 28)

    p.end()
    splash = QSplashScreen(pixmap)
    splash.show()
    app.processEvents()
    return splash


def splash_msg(splash, msg):
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QApplication
    splash.showMessage(f"  {msg}",
                       Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                       QColor(200, 200, 200))
    QApplication.processEvents()


def main():
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QIcon

    app = QApplication(sys.argv)
    ico_path = _ruta_recurso("dpi_icon.ico")
    if os.path.exists(ico_path):
        app.setWindowIcon(QIcon(ico_path))

    splash = crear_splash(app)

    splash_msg(splash, "Cargando numpy y opencv...")
    import numpy; import cv2  # noqa

    splash_msg(splash, "Cargando motor de gráficos...")
    import pyqtgraph  # noqa

    splash_msg(splash, "Cargando interfaz de medición...")
    from modulo_medicion import PestanaImagen, ExportadorDendro  # noqa

    splash_msg(splash, "Cargando co-datación...")
    from modulo_codatacion import PanelCodatacion  # noqa

    splash_msg(splash, "Preparando ventana principal...")
    from main_anillos import VentanaAnillos
    ventana = VentanaAnillos()
    ventana.show()
    splash.finish(ventana)
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass
    except Exception:
        import traceback
        log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash_log.txt")
        with open(log, "w") as f:
            traceback.print_exc(file=f)
        traceback.print_exc()
