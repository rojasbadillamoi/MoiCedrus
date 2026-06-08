# tema_manager.py — DendroLen

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QFont

from constantes import (
    OSCURO, CLARO, get_stylesheet, detectar_tema_sistema,
    SETTINGS_ORG, SETTINGS_APP, SETTINGS_TEMA, FONT_SIZE
)


class TemaManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._inicializado = False
        return cls._instance

    def __init__(self):
        if self._inicializado:
            return
        self._inicializado = True
        self._callbacks = []
        self._tema_nombre = self._cargar_tema_guardado()
        self._tema = OSCURO if self._tema_nombre == "oscuro" else CLARO

    @property
    def t(self) -> dict:
        return self._tema

    @property
    def nombre(self) -> str:
        return self._tema_nombre

    @property
    def es_oscuro(self) -> bool:
        return self._tema_nombre == "oscuro"

    def alternar(self):
        self.aplicar("claro" if self._tema_nombre == "oscuro" else "oscuro")

    def aplicar(self, nombre: str):
        self._tema_nombre = nombre
        self._tema = OSCURO if nombre == "oscuro" else CLARO
        app = QApplication.instance()
        if app:
            font = QFont("DejaVu Sans Mono", FONT_SIZE)
            font.setWeight(QFont.Weight.DemiBold)
            font.setStyleHint(QFont.StyleHint.Monospace)
            app.setFont(font)
            app.setStyleSheet(get_stylesheet(self._tema))
        self._guardar_tema(nombre)
        for cb in list(self._callbacks):
            try:
                cb(self._tema)
            except Exception:
                pass

    def suscribir(self, cb):
        if cb not in self._callbacks:
            self._callbacks.append(cb)

    def desuscribir(self, cb):
        if cb in self._callbacks:
            self._callbacks.remove(cb)

    def _cargar_tema_guardado(self) -> str:
        try:
            s = QSettings(SETTINGS_ORG, SETTINGS_APP)
            v = s.value(SETTINGS_TEMA, "")
            if v in ("oscuro", "claro"):
                return v
        except Exception:
            pass
        return detectar_tema_sistema()

    def _guardar_tema(self, nombre: str):
        try:
            s = QSettings(SETTINGS_ORG, SETTINGS_APP)
            s.setValue(SETTINGS_TEMA, nombre)
        except Exception:
            pass


tema = TemaManager()
