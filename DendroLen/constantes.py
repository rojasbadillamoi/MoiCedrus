# constantes.py — DendroLen v2.0

APP_NAME    = "DendroLen"
APP_VERSION = "2.0.0"
APP_AUTHOR  = "Moisés E. Rojas Badilla"
APP_EMAIL   = "moisese.rojasbadilla@gmail.com"

DEFAULT_BAUD    = 9600
BAUD_RATES      = [9600, 19200, 38400, 57600, 115200]
PORT_GLOBS      = ["/dev/ttyUSB*", "/dev/ttyACM*", "/dev/tty.*", "/dev/cu.*"]
USB_KEYWORDS    = ["usb", "ftdi", "ch340", "cp210", "prolific", "serial"]
RECONNECT_DELAY = 2000

FACTOR_MICRAS_A_TUCSON = 10
FACTOR_0_01MM_A_TUCSON = 1
TUCSON_TERMINATOR      = -9999
TUCSON_VALUES_PER_LINE = 10
TUCSON_SAMPLE_ID_WIDTH = 8
MIN_RING_VALUE_MM      = 0.02
MAX_RING_VALUE_MM      = 20.0

BEEP_DECADE_FREQ = 880
BEEP_DECADE_DUR  = 150
BEEP_SAVE_FREQ   = 660
BEEP_SAVE_DUR    = 80
BEEP_SAVE_FREQ2  = 880
BEEP_SAVE_DUR2   = 100

ESPECIES = [
    "No registrada",
    "Nothofagus obliqua",
    "Nothofagus pumilio",
    "Nothofagus dombeyi",
    "Araucaria araucana",
    "Fitzroya cupressoides",
    "Pilgerodendron uviferum",
    "Austrocedrus chilensis",
    "Polylepis tarapacana",
    "Cryptocarya alba",
]

MSG_PAUSA   = "PAUSA. ¡Momento de un respiro! Apaga la luz y ahorra energía. 💡🌱"
MSG_REANUDA = "REANUDADO — continuando medición."

# ── ANOMALÍAS ─────────────────────────────────────────────────────────────────
# (codigo, nombre completo, tecla atajo, color por defecto/oscuro)
ANOMALIAS = [
    ("FR", "Frost ring",   "F2", "#5dade2"),
    ("LR", "Light ring",   "F3", "#f0e060"),
    ("AF", "Anillo falso", "F4", "#e09030"),
    ("MR", "Missing ring", "F5", "#e74c3c"),
    ("FU", "Fuego",        "F6", "#ff6030"),
    ("GO", "Golpe",        "F7", "#a070d0"),
    ("OT", "Otro",         "F8", "#80c880"),
]
ANOMALIA_CODIGOS = {a[0]: a[1] for a in ANOMALIAS}

# Colores de anomalías por tema — el amarillo del Light ring queda invisible
# sobre fondo claro, por eso se necesitan colores específicos para cada tema.
ANOMALIA_COLORES_OSCURO = {
    "FR": "#5dade2",   # azul claro
    "LR": "#f0e060",   # amarillo
    "AF": "#e09030",   # naranja
    "MR": "#e74c3c",   # rojo
    "FU": "#ff6030",   # rojo anaranjado
    "GO": "#a070d0",   # púrpura
    "OT": "#80c880",   # verde
}
ANOMALIA_COLORES_CLARO = {
    "FR": "#0d5cb6",   # azul profundo
    "LR": "#a07000",   # ámbar oscuro (en lugar de amarillo invisible)
    "AF": "#a85e00",   # naranja oscuro
    "MR": "#b32d20",   # rojo oscuro
    "FU": "#cc3300",   # rojo-naranja oscuro
    "GO": "#6020a0",   # púrpura oscuro
    "OT": "#2d8030",   # verde oscuro
}
# Compatibilidad con código existente — usa el dict oscuro por defecto
ANOMALIA_COLORES = ANOMALIA_COLORES_OSCURO


def color_anomalia(cod: str, tema_nombre: str = "oscuro") -> str:
    """Devuelve el color de la anomalía adaptado al tema actual."""
    if tema_nombre == "claro":
        return ANOMALIA_COLORES_CLARO.get(cod, "#888888")
    return ANOMALIA_COLORES_OSCURO.get(cod, "#888888")

ATAJOS = [
    ("Ctrl+G",    "Guardar archivo y continuar midiendo"),
    ("Ctrl+S",    "Guardar y cerrar radio (volver al asistente)"),
    ("Ctrl+Z",    "Deshacer / remedir la última medición"),
    ("Ctrl+P",    "Pausar / reanudar recepción del carro"),
    ("Ctrl+Y",    "Editar año de inicio en caliente"),
    ("F1",        "Mostrar esta ayuda"),
    ("F2",        "Marcar anomalía: Frost ring (FR)"),
    ("F3",        "Marcar anomalía: Light ring (LR)"),
    ("F4",        "Marcar anomalía: Anillo falso (AF)"),
    ("F5",        "Marcar anomalía: Missing ring (MR)"),
    ("F6",        "Marcar anomalía: Fuego (FU)"),
    ("F7",        "Marcar anomalía: Golpe (GO)"),
    ("F8",        "Marcar anomalía: Otro (OT)"),
    ("F9",        "Reconectar puerto serie"),
    ("↑ ↓",       "Desplazar vista previa Tucson línea a línea"),
    ("PgUp PgDn", "Desplazar vista previa Tucson página a página"),
    ("Home / End","Ir al inicio / final de la vista previa Tucson"),
]

SETTINGS_ORG  = "MoiCedrus"
SETTINGS_APP  = "DendroLen"
SETTINGS_TEMA    = "tema"
SETTINGS_ESPECIE = "ultima_especie"
SETTINGS_ULTIMA_RUTA = "ultima_ruta_guardado"
SETTINGS_ESPECIES_CUSTOM = "especies_custom"

# Tamaño de fuente base — se aplica via QApplication.setFont() Y stylesheet
# Estos valores se ajustan automáticamente al tamaño de pantalla via ajustar_fuente_para_pantalla()
FONT_SIZE     = 15   # pt para QFont (default 1080p+)
FONT_SIZE_PX  = 16   # px para stylesheets Qt (default 1080p+)


def ajustar_fuente_para_pantalla(alto_pantalla: int):
    """
    Ajusta el tamaño de fuente global según la altura de la pantalla.
    Llamar UNA SOLA VEZ al inicio, antes de aplicar el stylesheet.

    < 720 px:    11pt / 12px  (laptops antiguos 1366x768)
    720–900:     12pt / 13px  (1366x768 estándar)
    900–1024:    13pt / 14px  (1600x900, 1440x900)
    1024–1200:   14pt / 15px  (1920x1080)
    >= 1200:     15pt / 16px  (1440p, 4K)
    """
    global FONT_SIZE, FONT_SIZE_PX
    if alto_pantalla < 720:
        FONT_SIZE, FONT_SIZE_PX = 11, 12
    elif alto_pantalla < 900:
        FONT_SIZE, FONT_SIZE_PX = 12, 13
    elif alto_pantalla < 1024:
        FONT_SIZE, FONT_SIZE_PX = 13, 14
    elif alto_pantalla < 1200:
        FONT_SIZE, FONT_SIZE_PX = 14, 15
    else:
        FONT_SIZE, FONT_SIZE_PX = 15, 16

OSCURO = {
    "BG":          "#0e1621",
    "BG_PANEL":    "#131f2e",
    "BG_CARD":     "#1a2740",
    "BG_BAR":      "#101929",
    "BORDER":      "#3a5578",
    "BORDER_SOFT": "#2c4264",
    "TEXT":        "#ffffff",
    "TEXT_DIM":    "#c5dcf2",
    "TEXT_TITLE":  "#ffffff",
    "GREEN":       "#3ee07f",
    "GREEN_DIM":   "#1a4a2a",
    "BLUE":        "#74c0ec",
    "ORANGE":      "#ffb84d",
    "RED":         "#ff6b5b",
    "LOG_OK":      "#5cf09a",
    "LOG_WARN":    "#ffd060",
    "LOG_ERR":     "#ff8070",
    "LOG_VAL":     "#a8dcff",
    "LOG_DECADE":  "#ffb84d",
    "LOG_INFO":    "#c5dcf2",
    "LOG_TIME":    "#8fb4dc",
    "TUC_HDR":     "#9cc4ec",
    "TUC_LINE":    "#bfe4ff",
    "TUC_CUR":     "#6cf0a8",
    "MPL_BG":      "#0e1621",
    "MPL_SPINE":   "#3a5578",
    "MPL_TICK":    "#a8c4e0",
    "MPL_LINE":    "#74c0ec",
    "MPL_FILL":    "#74c0ec",
    "MPL_DOT":     "#3ee07f",
}

CLARO = {
    "BG":          "#eef3f9",
    "BG_PANEL":    "#dce8f5",
    "BG_CARD":     "#ffffff",
    "BG_BAR":      "#e0ebf6",
    "BORDER":      "#9ab4d0",
    "BORDER_SOFT": "#b8cce0",
    "TEXT":        "#0a1828",
    "TEXT_DIM":    "#2a4660",
    "TEXT_TITLE":  "#000000",
    "GREEN":       "#0f6e37",
    "GREEN_DIM":   "#c8ecd8",
    "BLUE":        "#0d5cb6",
    "ORANGE":      "#8f5000",
    "RED":         "#b32d20",
    "LOG_OK":      "#0f6634",
    "LOG_WARN":    "#7a4500",
    "LOG_ERR":     "#931c1c",
    "LOG_VAL":     "#0d5cb6",
    "LOG_DECADE":  "#7a4500",
    "LOG_INFO":    "#2a4660",
    "LOG_TIME":    "#4a6a8a",
    "TUC_HDR":     "#0d5cb6",
    "TUC_LINE":    "#0a3258",
    "TUC_CUR":     "#0f6634",
    "MPL_BG":      "#eef3f9",
    "MPL_SPINE":   "#9ab4d0",
    "MPL_TICK":    "#2a4660",
    "MPL_LINE":    "#0d5cb6",
    "MPL_FILL":    "#0d5cb6",
    "MPL_DOT":     "#127a3e",
}


def get_stylesheet(t: dict) -> str:
    """
    Stylesheet global. Font: JetBrains Mono 16px bold-ish.
    Cada clase de widget tiene font explícito para garantizar
    que Qt en Linux respete el tamaño.
    """
    F = FONT_SIZE_PX
    FONT = "'JetBrains Mono', 'Fira Mono', 'DejaVu Sans Mono', 'Courier New', monospace"
    return f"""
QMainWindow, QDialog, QWidget, QStackedWidget, QFrame, QScrollArea {{
    background-color: {t['BG']};
    color: {t['TEXT']};
    font-family: {FONT};
    font-size: {F}px;
    font-weight: 500;
}}
QLabel {{
    background: transparent;
    color: {t['TEXT']};
    font-family: {FONT};
    font-size: {F}px;
    font-weight: 500;
}}
QLineEdit {{
    background-color: {t['BG_CARD']};
    color: {t['TEXT_TITLE']};
    border: 1px solid {t['BORDER']};
    border-radius: 6px;
    padding: 6px 10px;
    font-family: {FONT};
    font-size: {F}px;
    font-weight: 500;
    min-height: 34px;
    selection-background-color: {t['BLUE']};
    selection-color: #ffffff;
}}
QLineEdit:focus {{ border: 2px solid {t['BLUE']}; }}
QLineEdit:read-only {{
    background-color: {t['BG_BAR']};
    color: {t['TEXT_DIM']};
}}
QSpinBox {{
    background-color: {t['BG_CARD']};
    color: {t['TEXT_TITLE']};
    border: 1px solid {t['BORDER']};
    border-radius: 6px;
    padding: 6px 10px;
    font-family: {FONT};
    font-size: {F}px;
    font-weight: 500;
    min-height: 34px;
}}
QSpinBox:focus {{ border: 2px solid {t['BLUE']}; }}
QSpinBox::up-button, QSpinBox::down-button {{
    background-color: {t['BORDER_SOFT']};
    border: none; width: 20px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background-color: {t['BORDER']};
}}
QComboBox {{
    background-color: {t['BG_CARD']};
    color: {t['TEXT_TITLE']};
    border: 1px solid {t['BORDER']};
    border-radius: 6px;
    padding: 6px 10px;
    font-family: {FONT};
    font-size: {F}px;
    font-weight: 500;
    min-height: 34px;
}}
QComboBox:focus {{ border: 2px solid {t['BLUE']}; }}
QComboBox::drop-down {{ border: none; width: 26px; background: transparent; }}
QComboBox QAbstractItemView {{
    background-color: {t['BG_CARD']};
    color: {t['TEXT_TITLE']};
    border: 1px solid {t['BORDER']};
    selection-background-color: {t['BLUE']};
    selection-color: #ffffff;
    font-family: {FONT};
    font-size: {F}px;
    font-weight: 500;
    padding: 4px;
}}
QPushButton {{
    background-color: {t['BORDER_SOFT']};
    color: {t['TEXT_TITLE']};
    border: 1px solid {t['BORDER']};
    border-radius: 7px;
    padding: 7px 16px;
    font-family: {FONT};
    font-size: {F}px;
    font-weight: 700;
    min-height: 34px;
}}
QPushButton:hover {{
    background-color: {t['BORDER']};
    border-color: {t['BLUE']};
    color: {t['TEXT_TITLE']};
}}
QPushButton:pressed {{ background-color: {t['BG_CARD']}; }}
QPushButton:disabled {{
    background-color: {t['BG_BAR']};
    color: {t['TEXT_DIM']};
    border-color: {t['BORDER_SOFT']};
}}
QTextEdit {{
    background-color: {t['BG']};
    color: {t['TUC_LINE']};
    border: none;
    font-family: {FONT};
    font-size: {F}px;
    font-weight: 500;
    padding: 4px 8px;
    selection-background-color: {t['BLUE']};
    selection-color: #ffffff;
}}
QScrollBar:vertical {{
    background-color: {t['BG']};
    width: 8px; border: none; margin: 0;
}}
QScrollBar::handle:vertical {{
    background-color: {t['BORDER']};
    border-radius: 4px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background-color: {t['BLUE']}; }}
QScrollBar:horizontal {{
    background-color: {t['BG']};
    height: 8px; border: none; margin: 0;
}}
QScrollBar::handle:horizontal {{
    background-color: {t['BORDER']};
    border-radius: 4px; min-width: 24px;
}}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none; border: none;
}}
QSplitter::handle {{ background-color: {t['BORDER']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QFrame[frameShape="4"] {{ color: {t['BORDER']}; }}
QFrame[frameShape="5"] {{ color: {t['BORDER']}; }}
QToolTip {{
    background-color: {t['BG_CARD']};
    color: {t['TEXT']};
    border: 1px solid {t['BORDER']};
    font-family: {FONT};
    font-size: {F}px;
    padding: 5px 9px;
}}
QDialog {{ background-color: {t['BG_CARD']}; }}
QInputDialog QLabel, QMessageBox QLabel {{
    color: {t['TEXT']};
    font-family: {FONT};
    font-size: {F}px;
}}
"""


def detectar_tema_sistema() -> str:
    """Detecta el tema del SO. Devuelve 'oscuro' o 'claro'."""
    try:
        import subprocess
        r = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True, text=True, timeout=2)
        if "dark" in r.stdout.lower():
            return "oscuro"
        r2 = subprocess.run(
            ["kreadconfig5", "--group", "General", "--key", "ColorScheme"],
            capture_output=True, text=True, timeout=2)
        if "dark" in r2.stdout.lower():
            return "oscuro"
    except Exception:
        pass
    try:
        import subprocess
        r = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True, text=True, timeout=2)
        if "dark" in r.stdout.lower():
            return "oscuro"
    except Exception:
        pass
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app and app.palette().window().color().lightness() < 128:
            return "oscuro"
    except Exception:
        pass
    return "claro"


ESTILO_OSCURO = get_stylesheet(OSCURO)
ESTILO_CLARO  = get_stylesheet(CLARO)
