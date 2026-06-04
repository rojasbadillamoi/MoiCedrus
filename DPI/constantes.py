# =============================================================================
# constantes.py — Configuración global de la Plataforma Dendrocronológica
# Centraliza todos los valores "mágicos" del proyecto para facilitar
# mantenimiento y futuros ajustes sin buscar en múltiples archivos.
# =============================================================================

# --- Resolución y escala ---
DPI_DEFECTO = 2400
MM_POR_PULGADA = 25.4
PIXELES_POR_MM_DEFECTO = DPI_DEFECTO / MM_POR_PULGADA  # ≈ 94.49

# --- Formato Tucson (.rwl) ---
TUCSON_VALOR_FIN = -9999       # Marca fin de serie
TUCSON_VALOR_FALTANTE = 9999   # Año sin dato
TUCSON_VALOR_999 = 998         # Valor 999 codificado (colisiona con marcador)
TUCSON_MAX_CHARS_ID = 8        # Longitud máxima del identificador de serie
TUCSON_DIVISOR = 1000.0        # Conversión décimas → mm

# --- Historial / Deshacer ---
MAX_PASOS_HISTORIAL = 50

# --- Tolerancias de interacción (en píxeles de pantalla, se escalan con zoom) ---
TOLERANCIA_CLICK_PUNTO = 15    # px — para seleccionar/arrastrar/borrar un punto
TOLERANCIA_CLICK_SEGMENTO = 20 # px — para insertar un punto sobre un segmento

# --- Ventana deslizante de co-datación ---
VENTANA_COFECHADO_DEFECTO = 40
OVERLAP_MINIMO_ANALISIS = 10   # Años mínimos de solapamiento para calcular r
OVERLAP_MINIMO_GRAFICO = 5    # Años mínimos para subventanas del motor split-window
RANGO_SHIFT_SIMULADOR = 5      # ±años que prueba el simulador de anillos

# --- Umbrales estadísticos por defecto (panel co-datación) ---
UMBRAL_ANCLA_DEFECTO = 0.40    # r mínimo para considerar un segmento "cofechado"
UMBRAL_DESFASE_DEFECTO = 0.25  # r máximo para considerar un segmento "con error"
UMBRAL_MEJORA_DEFECTO = 0.35   # Mejora de r mínima para sugerir cambio

# --- Supresión de no-máximos en detección de anomalías ---
VENTANA_SUPRESION_ANOMALIAS = 7  # Años de radio para suprimir duplicados

# --- Tipos de anomalía: clave → (icono, etiqueta, color_hex) ---
# Fuente única de verdad — importar desde aquí en todos los módulos.
TIPOS_ANOMALIA: dict[str, tuple[str, str, str]] = {
    "frost_ring":  ("❄",  "Frost ring",    "#00BFFF"),
    "light_ring":  ("☀",  "Light ring",    "#FFD700"),
    "fire":        ("🔥", "Fuego",         "#FF4500"),
    "compression": ("▼",  "Comp. madera",  "#FF6600"),
    "tension":     ("▲",  "Tensión",       "#9933CC"),
    "false_ring":  ("⊘",  "Falso anillo",  "#3399FF"),
    "wedge":       ("◆",  "Cuña",          "#CCAA00"),
    "resin":       ("●",  "Resina",        "#8B4513"),
    "event":       ("★",  "Otro/Genérico", "#CC0000"),
}

# --- Colores (RGB tuples para pyqtgraph) ---
COLORES_SERIES = [
    (255, 51, 51),
    (51, 136, 255),
    (51, 204, 51),
    (255, 204, 51),
    (204, 51, 255),
]
COLORES_SERIES_INACTIVAS = [
    (51, 204, 51),
    (255, 204, 51),
    (204, 51, 255),
    (255, 102, 0),
]

COLOR_PUNTO_ACTIVO = "#FF3333"
COLOR_LINEA_ACTIVA = "#3388FF"
COLOR_SALTO = "#FFD700"
COLOR_GUIA = "#aaaaaa"
COLOR_CALIBRACION = "#33FF33"
COLOR_PUNTO_INACTIVO = "#5cb85c"
COLOR_EWLW           = "#FF8C00"   # naranja — límite earlywood/latewood

# --- Marcadores de décadas en la imagen ---
INTERVALO_MARCADOR_DECADA = 10
INTERVALO_MARCADOR_CINCUENTENA = 50
INTERVALO_MARCADOR_CENTENA = 100
