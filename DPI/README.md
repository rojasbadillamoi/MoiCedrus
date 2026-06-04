# DPI — Dendro Pixel Interface

> Herramienta de escritorio de código abierto para la **medición interactiva de anillos de árboles** y **co-datación visual** de series dendrocronológicas.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![PyQt6](https://img.shields.io/badge/UI-PyQt6-green.svg)](https://riverbankcomputing.com/software/pyqt/)

---

## Índice

- [Descripción](#descripción)
- [Características](#características)
  - [Módulo de Medición](#️-módulo-de-medición)
  - [Detección Automática de Anillos](#-detección-automática-de-anillos)
  - [Intensidad de Madera](#-intensidad-de-madera)
  - [Estimación de Médula Faltante](#-estimación-de-médula-faltante)
  - [Co-datación Visual](#-co-datación-visual)
- [Instalación](#instalación)
- [Uso rápido](#uso-rápido)
- [Flujo de trabajo detallado](#flujo-de-trabajo-detallado)
- [Atajos de teclado](#atajos-de-teclado)
- [Formatos de archivo](#formatos-de-archivo)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Configuración](#configuración)
- [Compatibilidad con CooRecorder](#compatibilidad-con-coorecorder)
- [Referencia científica](#referencia-científica)
- [Licencia](#licencia)

---

## Descripción

DPI (Dendro Pixel Interface) es una aplicación de escritorio multiplataforma para la medición de ancho de anillos de árboles a partir de imágenes escaneadas de alta resolución. Combina un **visor interactivo de imágenes** con un conjunto completo de herramientas de edición, un **motor de detección automática** configurable por especie, cálculo de **intensidad de madera** (Blue Intensity, Red Intensity, Green Intensity) y un panel de **co-datación visual** basado en correlación de Pearson por ventana deslizante.

El programa está pensado para investigadores en dendrocronología que trabajen con secciones transversales, tarugos o tablillas de madera. Las series exportadas son compatibles con el ecosistema estándar de herramientas dendrocronológicas (COFECHA, ARSTAN, CDendro, dplR, etc.).

---

## Características

### 📷 Módulo de Medición

El visor central de DPI permite trabajar con imágenes PNG, JPG y TIFF de cualquier resolución. Los DPI se detectan automáticamente de los metadatos de la imagen; si no están disponibles, se usa un valor por defecto configurable.

**Modos de interacción:**

| Modo | Atajo | Descripción |
|---|---|---|
| 📍 Medir | `Ctrl+A` | Clic izquierdo para añadir límites de anillo. Clic derecho para insertar un salto. |
| 🔀 Salto | `Ctrl+D` | Marca una discontinuidad en el camino (no cuenta como anillo). |
| ⊘ Quiebre | `Ctrl+E` | Delimita un espacio intra-anillo: se suma la distancia pero no cierra el anillo. |
| ➕ Insertar | `Ctrl+W` | Inserta un nuevo punto sobre un segmento ya trazado. |
| 🖐 Mover | `Ctrl+S` | Arrastra puntos existentes o hace pan de la imagen. |
| ✕ Borrar | `Ctrl+X` | Elimina el punto más cercano al clic. |
| 🍂 EW/LW | `Ctrl+L` | Inserta el límite earlywood/latewood dentro de un anillo (un solo límite por anillo). |
| 🔴 Anomalías | `Ctrl+Q` | Anota eventos especiales sobre anillos ya medidos. |
| 🎯 Pith | `Ctrl+P` | Activa el modo de estimación de médula faltante. |
| 🔍 Auto | — | Marca médula y corteza para lanzar la detección automática. |

**Funciones adicionales del visor:**

- **Punto de ancla inteligente**: en modo Medir, el primer clic establece el punto de partida (ancla). Los clics sucesivos extienden el camino desde esa ancla, permitiendo añadir anillos en ambas direcciones sin importar el orden de medición.
- **Vista dividida (`Ctrl+B`)**: abre un segundo panel de solo lectura con zoom y posición independientes. Puede mostrar la misma imagen en una zona distinta o cualquier otra imagen abierta en otra pestaña, con sincronización de zoom opcional.
- **Visualización por canal**: selector de canal de visualización (RGB, R, G, B, Gris) con atajos numéricos `1`–`5`. Incluye sliders de brillo y contraste implementados con tabla LUT de 256 entradas, eficientes para imágenes de 500 MB o más. Los ajustes se guardan por especie y canal entre sesiones.
- **Gráfico en vivo con crosshair**: el gráfico de anchuras se actualiza en tiempo real mientras se mide. Al pasar el mouse por encima, un crosshair muestra el año y los valores de todas las series visibles. Al hacer clic en el gráfico, la imagen se centra automáticamente en el anillo correspondiente con un indicador visual transitorio.
- **Múltiples radios por imagen**: permite guardar varios radios medidos sobre la misma imagen (p. ej. A, B, C de la misma sección), visualizarlos superpuestos con colores distintos y editarlos de forma independiente.
- **Persistencia automática del progreso**: el estado de medición se guarda automáticamente en un archivo sidecar oculto junto a la imagen y se restaura al volver a abrirla.
- **Historial deshacer/rehacer**: hasta 50 pasos (`Ctrl+Z` / `Ctrl+Y`).
- **Tooltip por año**: al pasar el cursor sobre un punto marcado en la imagen, aparece el año asignado.

**Anotación de anomalías:**

El modo Anomalías permite marcar eventos dendrocronológicamente relevantes sobre anillos ya medidos:

| Ícono | Tipo | Color |
|---|---|---|
| ❄ | Frost ring | Azul claro (#00BFFF) |
| ☀ | Light ring | Amarillo (#FFD700) |
| 🔥 | Fuego | Naranja-rojo (#FF4500) |
| ▼ | Compresión de madera | Naranja (#FF6600) |
| ▲ | Tensión | Púrpura (#9933CC) |
| ⊘ | Falso anillo | Azul (#3399FF) |
| ◆ | Cuña | Dorado (#CCAA00) |
| ● | Canal de resina | Marrón (#8B4513) |
| ★ | Otro / Genérico | Rojo oscuro (#CC0000) |

Las anomalías se persisten en el sidecar `.anom.json` junto al `.rwl` exportado.

**Calibración de escala:**

Hay dos métodos para establecer la relación px/mm:
1. **DPI manual** (`📐 DPI`): ingreso directo del valor de resolución del escáner.
2. **Regla interactiva** (`📏 Regla`): clic en dos puntos de referencia de distancia conocida sobre la imagen.

---

### 🤖 Detección Automática de Anillos

El módulo de auto-detección propone automáticamente los límites de anillos a partir de un perfil de intensidad extraído entre dos puntos marcados por el usuario.

**Pipeline completo:**

1. El usuario activa el modo Auto-detect y hace clic en la médula y luego en la corteza.
2. Se extrae un perfil de intensidad a lo largo de esa línea, promediando una banda de N píxeles de ancho perpendicular al segmento en cada posición.
3. El perfil se suaviza con un filtro Savitzky-Golay de ventana configurable.
4. Se detectan mínimos o máximos locales con `scipy.signal.find_peaks` según los parámetros del perfil de especie activo.
5. Se muestra un diálogo de previsualización interactivo donde el usuario puede ajustar la prominencia mínima en tiempo real antes de confirmar.
6. Los puntos detectados se colocan en el lienzo. Si ya había puntos en el tramo, se ofrece la opción de reemplazarlos o insertarlos sin borrar los existentes.

**Perfiles de especie:**

Los parámetros de detección se guardan por especie en `species_profiles.json`. Cada perfil define:

| Parámetro | Descripción |
|---|---|
| `canal` | Canal de intensidad para extraer el perfil: `"gray"`, `"R"`, `"G"`, `"B"` |
| `invertir` | Invertir la señal (para especies con latewood más claro que el earlywood) |
| `suavizado` | Ventana del filtro Savitzky-Golay en píxeles |
| `prominencia` | Prominencia mínima de pico para `find_peaks` |
| `anchura_min_px` | Ancho mínimo de anillo aceptado en píxeles |
| `anchura_max_px` | Ancho máximo de anillo aceptado en píxeles |
| `banda_px` | Ancho de la banda de promediado perpendicular al radio |

Los perfiles se crean, editan y eliminan desde la interfaz gráfica. La distribución incluye un perfil genérico inicial para coníferas.

**Detección automática de EW/LW:**

El botón `🔍 Auto-detectar EW/LW` procesa todos los anillos del radio activo en un hilo secundario con barra de progreso cancelable. Para cada anillo:

1. Construye un perfil de intensidad gris píxel a píxel a lo largo del camino del anillo, promediando en banda perpendicular.
2. Suaviza con Savitzky-Golay (ventana ≈ 10% del largo del anillo, mínimo 5 muestras).
3. Localiza el punto de máxima tasa de oscurecimiento (mínimo de la primera derivada), que corresponde a la transición earlywood → latewood.
4. Como respaldo, si la primera derivada no muestra oscurecimiento, usa el primer cruce del perfil por debajo de su media en el rango de búsqueda (15%–95% del anillo).

---

### 🎨 Intensidad de Madera

Implementa el método de **Blue Intensity** (Maxwell et al., 2017), extensible a los canales Rojo (RI), Verde (GI) y Gris.

**Cálculo por anillo:**

Para cada anillo, el algoritmo recorre el camino medido segmento a segmento. En cada punto, promedia los píxeles del canal elegido en una banda perpendicular de ancho configurable. Las métricas calculadas por anillo son `mean`, `min` y `max` del canal seleccionado. Los tramos marcados como `espacio` o `quiebre_ini` se excluyen del promedio sin interrumpir la agregación del anillo.

**Ratios disponibles:**

Además de los canales individuales, se calculan los ratios B/R y B/G (normalizados al rango observado).

**Exportación:**

Un diálogo de selección permite elegir exactamente qué combinaciones de canal y métrica exportar. Cada combinación seleccionada genera un archivo `.rwl` independiente (valores escalados ×1000, compatible con el estándar Tucson). Los archivos se guardan en la carpeta que el usuario indique.

---

### 🌱 Estimación de Médula Faltante

El módulo de estimación de pith offset calcula el número de anillos no visibles entre el centro geométrico del árbol y el primer anillo medido. Ofrece tres métodos combinables:

**1. Geométrico por arco**

El usuario marca 3 o más puntos sobre el arco interno visible con el modo Pith (`Ctrl+P`). El sistema ajusta el círculo de mejor ajuste por mínimos cuadrados (algoritmo de Kasa), determina el radio del árbol en ese punto y calcula la distancia faltante. Mientras se añaden puntos, se muestra un preview en tiempo real del círculo ajustado, el centro estimado y la distancia al primer anillo.

**2. DAP manual**

Para tarugos rectos donde no hay curvatura visible, el usuario ingresa el diámetro a la altura del pecho (DAP) en mm. El sistema utiliza ese valor como diámetro para estimar la distancia faltante.

**3. Serie de referencia**

En lugar de asumir un ancho constante para los anillos no visibles, escala el crecimiento relativo de una serie de referencia ya cargada en el mismo período. Esto produce estimaciones más realistas cuando la cronología de referencia está cofechada con la muestra.

El resultado (`pith_offset`) ajusta retroactivamente el año de médula y se guarda en el sidecar. La operación es completamente deshacible con el botón `↩ Deshacer estimación`.

---

### 📊 Co-datación Visual

El panel de co-datación permite verificar el fechado de series dendrocronológicas comparándolas entre sí o contra cronologías maestras de referencia.

**Carga de series:**

- Formato Tucson (`.rwl`, `.txt`) — formato estándar de intercambio.
- Formato CooRecorder (`.wid`) — cronologías de anchura.
- Cualquier serie puede designarse como "maestra" frente a la cual se calculan las correlaciones del resto.

**Motor estadístico:**

- Correlación de Pearson por **ventana deslizante** de tamaño configurable (por defecto 40 años).
- Solapamiento mínimo configurable antes de calcular r (por defecto 20 años para análisis, 10 para el motor split-window).
- Los segmentos del gráfico se colorean según tres umbrales configurables:
  - **Verde (anclado)**: r ≥ umbral de ancla → segmento bien cofechado.
  - **Rojo (desfasado)**: r ≤ umbral de desfase → posible error de fechado.
  - **Amarillo (sospechoso)**: existe una posición alternativa que mejoraría r en al menos el umbral de mejora → posible anillo faltante o sobrante.

**Búsqueda de anillos faltantes o sobrantes:**

El motor de búsqueda exhaustiva prueba sistemáticamente todas las combinaciones de ±N anillos (por defecto ±3) para cada serie activa e identifica los desplazamientos que mejorarían significativamente la correlación con la maestra. Los resultados se presentan en una tabla ordenable con la corrección sugerida. Las correcciones se aplican en memoria sin modificar los archivos originales en disco.

**Interacción gráfica:**

- Clic en la leyenda para mostrar/ocultar series individualmente.
- Normalización automática a z-scores cuando hay más de una serie cargada.
- La serie maestra se dibuja con línea discontinua para distinguirla visualmente.

---

## Instalación

### Requisitos del sistema

- Python 3.10 o superior
- Windows, macOS o Linux

### Dependencias Python

```bash
pip install PyQt6 pyqtgraph opencv-python numpy pandas scipy Pillow openpyxl
```

Tabla detallada:

| Paquete | Versión mínima sugerida | Uso |
|---|---|---|
| PyQt6 | 6.4 | Interfaz gráfica |
| pyqtgraph | 0.13 | Gráficos interactivos (co-datación, BI) |
| opencv-python | 4.6 | Carga y procesamiento de imágenes |
| numpy | 1.23 | Operaciones numéricas y vectorización |
| pandas | 1.5 | Manejo de datos tabulares y exportación |
| scipy | 1.9 | Detección de picos (`find_peaks`, `savgol_filter`) |
| Pillow | 9.0 | Lectura de metadatos DPI de imágenes |
| openpyxl | 3.0 | Exportación a Excel (.xlsx) |

### Clonar e instalar

```bash
git clone https://github.com/tu_usuario/dpi-dendro.git
cd dpi-dendro
pip install -r requirements.txt
python main_anillos.py
```

---

## Uso rápido

```bash
python main_anillos.py
```

La aplicación detecta el tema claro u oscuro del sistema operativo en el primer lanzamiento. Puede alternarse en cualquier momento con el botón 🌓 de la barra superior. La preferencia se persiste entre sesiones.

---

## Flujo de trabajo detallado

### 1. Abrir y calibrar la imagen

1. Clic en `📁 Abrir Imagen(es)`. Se pueden cargar varias a la vez; cada una se abre en su propia pestaña.
2. Si los DPI no están en los metadatos, el programa avisa y usa el valor por defecto (2400 DPI). Para corregirlo:
   - `🔧 Herramientas → 📐 DPI`: ingreso directo del valor del escáner.
   - `🔧 Herramientas → 📏 Regla`: clic en dos puntos de referencia con distancia conocida.

### 2. Medir anillos

**Modo manual:**

1. Activar `📍 Medir` (`Ctrl+A`).
2. Primer clic: establece el punto de ancla (extremo inicial del radio).
3. Clics sucesivos: añaden límites de anillo desde el ancla activa.
4. Para marcar una discontinuidad: clic derecho o activar `🔀 Salto`.
5. Para marcar un quiebre intra-anillo: `⊘ Quiebre` → clic en el inicio y luego en el fin del quiebre.

**Modo automático:**

1. Seleccionar la especie en el combo `🌿` de la barra superior.
2. Activar `🤖 Auto-detectar`.
3. Clic 1 en la médula, clic 2 en la corteza.
4. En el diálogo de previsualización, ajustar la prominencia si es necesario y confirmar.
5. Corregir manualmente con `➕ Insertar`, `🖐 Mover` y `✕ Borrar`.

### 3. Completar metadatos

En la barra superior:

- **Cód**: código de la serie (máximo 8 caracteres, estándar Tucson).
- **Dir**: dirección de medición (`Médula→Corteza` o `Corteza→Médula`).
- **Médula / Corteza**: año del primer y último anillo. Modificar uno recalcula el otro automáticamente.
- **🌱 Médula / 🪵 Corteza**: indica si la muestra llega físicamente a la médula o a la corteza (pith present / bark present).

### 4. Anotar anomalías (opcional)

1. `🔧 Herramientas → 🔴 Anomalías` (`Ctrl+Q`).
2. Seleccionar el tipo de anomalía en el combo desplegable.
3. Clic sobre el anillo afectado en la imagen.

### 5. Marcar EW/LW (opcional)

- **Manual**: `🍂 EW/LW → 🍂 Marcar EW/LW` (`Ctrl+L`), clic sobre la línea azul del anillo a dividir.
- **Automático**: `🍂 EW/LW → 🔍 Auto-detectar EW/LW` — procesa todos los anillos en segundo plano con barra de progreso.
- **Eliminar**: `🍂 EW/LW → 🗑 Eliminar todos EW/LW` — borra todos los límites EW/LW del radio activo.

### 6. Estimar médula faltante (opcional)

1. `🔧 Herramientas → 🎯 Anillos al centro` (`Ctrl+P`).
2. Elegir método en el diálogo: arco (≥3 clics), DAP manual o serie de referencia.
3. Confirmar con `Enter`. El año de médula se ajusta automáticamente.
4. Para deshacer: `↩ Deshacer estimación`.

### 7. Exportar

1. Clic en `💾 Exportar`.
2. Elegir formato: **Tucson (.rwl/.txt)**, **CSV** o **Excel (.xlsx)**.
3. Las anomalías, los checkboxes de médula/corteza y los datos de estimación de pith se guardan automáticamente en el sidecar oculto junto al `.rwl`.

### 8. Múltiples radios por imagen

Para muestras con más de un radio (p. ej., sección transversal con radios A, B, C):

1. Medir el primer radio normalmente.
2. `💾 Guardar radio` → asignar un identificador.
3. El lienzo queda limpio para medir el siguiente radio.
4. Los radios guardados se muestran superpuestos con colores distintos y pueden activarse/desactivarse individualmente desde la lista de radios.
5. Cada radio se exporta de forma independiente.

### 9. Co-datar

1. Ir a la pestaña `📊 Co-datación Visual`.
2. `📂 Cargar serie` para cargar una o más series `.rwl`.
3. Opcionalmente, cargar una cronología maestra de referencia.
4. Configurar la ventana deslizante y los umbrales estadísticos.
5. Revisar los segmentos coloreados. Los segmentos rojos o amarillos requieren atención.
6. Usar el motor de búsqueda para detectar posibles anillos faltantes o sobrantes.
7. Aplicar correcciones en memoria para verificar su efecto antes de modificar los archivos.

---

## Atajos de teclado

### Modos de medición

| Atajo | Acción |
|---|---|
| `Ctrl+A` | Modo Medir |
| `Ctrl+D` | Modo Salto |
| `Ctrl+E` | Modo Quiebre intra-anillo |
| `Ctrl+W` | Modo Insertar punto en segmento |
| `Ctrl+S` | Modo Mover |
| `Ctrl+X` | Modo Borrar |
| `Ctrl+L` | Modo EW/LW manual |
| `Ctrl+Q` | Modo Anomalías |
| `Ctrl+P` | Iniciar estimación de médula faltante |

### Edición

| Atajo | Acción |
|---|---|
| `Ctrl+Z` | Deshacer |
| `Ctrl+Y` | Rehacer |

### Visualización

| Atajo | Acción |
|---|---|
| `Ctrl+B` | Activar/desactivar vista dividida |
| `1` | Canal RGB completo |
| `2` | Canal R (rojo) |
| `3` | Canal G (verde) |
| `4` | Canal B (azul) |
| `5` | Canal Gris |
| Rueda del ratón | Zoom en la imagen |
| Botón central del ratón | Pan de la imagen (cualquier modo) |
| Arrastrar (modo Mover, sin punto) | Pan de la imagen |

### En modo Pith

| Atajo | Acción |
|---|---|
| `Enter` | Confirmar estimación de médula |
| `Esc` | Cancelar estimación |

### En modo Quiebre

| Atajo | Acción |
|---|---|
| `Esc` | Cancelar selección del quiebre en curso |

---

## Formatos de archivo

### Tucson (.rwl / .txt)

Formato estándar de intercambio dendrocronológico. Cada fila contiene:
- El código de serie (8 caracteres, justificado a la izquierda).
- El año base de la década (4 dígitos).
- Hasta 10 mediciones en **décimas de milímetro** (enteros de 6 dígitos).

La serie termina con el valor `-9999` al final de la última fila de datos.

Valores especiales manejados por DPI:
- `9999` → año sin dato (faltante).
- El valor legítimo `999` se codifica internamente como `998` para evitar colisión con el marcador de fin de serie.

Los archivos se generan con fin de línea `CRLF` para máxima compatibilidad con COFECHA y ARSTAN.

### CooRecorder (.pos)

Formato de posiciones de CooRecorder. DPI lee archivos `.pos` preservando la resolución DPI declarada en el encabezado (`#DPI`), el año datado (`#C DATED`) y las marcas de salto. El código de serie se extrae del nombre del archivo. La importación es compatible con la lógica de ancla y con la co-datación en vivo de DPI.

DPI también lee cronologías `.wid` de CooRecorder como series de referencia en el panel de co-datación.

### Sidecar de anomalías (.anom.json)

Archivo JSON **oculto** generado automáticamente junto a cada `.rwl` exportado. El nombre sigue el patrón `.<nombre_rwl>.anom.json`. Se carga automáticamente al reimportar el `.rwl`. Contiene:

```json
{
  "code": "SERIE001",
  "medula": true,
  "corteza": false,
  "anomalias": {
    "1823": "frost_ring",
    "1877": "fire"
  },
  "pith_estimated": true,
  "pith_offset": 12,
  "pith_method": "arc"
}
```

Se mantiene compatibilidad retroactiva con el formato sin punto inicial (`.anom.json` anexado directamente al nombre del `.rwl`).

### Perfiles de especie (species_profiles.json)

Diccionario JSON ubicado en el directorio del ejecutable. Ejemplo de entrada:

```json
{
  "Conífera (defecto)": {
    "canal": "gray",
    "invertir": false,
    "suavizado": 7,
    "prominencia": 12.0,
    "anchura_min_px": 15,
    "anchura_max_px": 3000,
    "banda_px": 15
  }
}
```

---

## Estructura del proyecto

```
.
├── main_anillos.py          # Punto de entrada — ventana principal (DPI)
├── modulo_medicion.py       # Visor interactivo, exportador, PestanaImagen
├── modulo_codatacion.py     # Panel de co-datación visual y motor estadístico
├── modulo_autodeteccion.py  # Detección automática de límites de anillos
├── modulo_intensidad.py     # Cálculo de Blue/Red/Green Intensity
├── modulo_pith.py           # Estimación de médula faltante (pith offset)
├── constantes.py            # Configuración global centralizada
├── estilos.py               # Hojas de estilo Qt (tema oscuro / claro)
├── species_profiles.json    # Perfiles de especie para auto-detección
├── requirements.txt         # Dependencias de Python
└── LICENSE                  # GNU General Public License v3
```

---

## Configuración

Todos los valores globales están centralizados en `constantes.py`. Modificar este archivo permite ajustar el comportamiento del programa sin tocar el código de los módulos.

| Constante | Valor por defecto | Descripción |
|---|---|---|
| `DPI_DEFECTO` | 2400 | DPI asumido cuando la imagen no contiene metadatos de resolución |
| `MAX_PASOS_HISTORIAL` | 50 | Pasos máximos de deshacer/rehacer |
| `TOLERANCIA_CLICK_PUNTO` | 15 px | Radio de selección de un punto existente (en píxeles de pantalla) |
| `TOLERANCIA_CLICK_SEGMENTO` | 20 px | Radio para detectar un segmento al insertar un punto |
| `VENTANA_COFECHADO_DEFECTO` | 40 años | Ventana deslizante de co-datación |
| `OVERLAP_MINIMO_ANALISIS` | 20 años | Solapamiento mínimo para calcular r |
| `OVERLAP_MINIMO_GRAFICO` | 10 años | Solapamiento mínimo en el motor split-window |
| `RANGO_SHIFT_SIMULADOR` | ±5 años | Rango de búsqueda del simulador de anillos faltantes |
| `UMBRAL_ANCLA_DEFECTO` | 0.40 | r mínimo para que un segmento se considere bien cofechado |
| `UMBRAL_DESFASE_DEFECTO` | 0.25 | r máximo para que un segmento se marque como desfasado |
| `UMBRAL_MEJORA_DEFECTO` | 0.35 | Mejora mínima de r para sugerir una corrección de fechado |
| `VENTANA_SUPRESION_ANOMALIAS` | 7 años | Radio de supresión de detecciones duplicadas de anomalías |

Las preferencias de **tema claro/oscuro**, **última carpeta de trabajo** y los **ajustes de brillo/contraste por canal y especie** se guardan entre sesiones mediante `QSettings` (clave `MoiCedrus/DPI`).

---

## Compatibilidad con CooRecorder

DPI está diseñado para usarse junto a CooRecorder o como alternativa:

- **Importar `.pos`**: carga un archivo de posiciones de CooRecorder con su resolución DPI declarada, año datado y marcas de salto. El código de serie se extrae del nombre del archivo (máximo 8 caracteres, en mayúsculas).
- **Cargar `.wid`**: carga una cronología CooRecorder como serie maestra de referencia en el panel de co-datación.
- **Exportar Tucson**: los `.rwl` generados por DPI son compatibles con CDendro, COFECHA, ARSTAN y dplR.

---

## Referencia científica

El módulo de intensidad de madera está basado en:

> Maxwell, J.T., Harley, G.L., Matheus, T.J., Van Aken, B.L., Au, T.F., & Robeson, S.M. (2017). *Blue intensity: A pilot study to develop a new proxy for reconstructing past climate from tree rings.* Dendrochronologia, 44, 1–7. https://doi.org/10.1016/j.dendro.2017.01.003

---

## Licencia

Copyright © 2024 — Los autores de DPI

Este programa es software libre: puedes redistribuirlo y/o modificarlo bajo los términos de la **GNU General Public License** publicada por la Free Software Foundation, ya sea la versión 3 de la Licencia, o (a tu elección) cualquier versión posterior.

Este programa se distribuye con la esperanza de que sea útil, pero **sin ninguna garantía**; incluso sin la garantía implícita de **comerciabilidad** o **idoneidad para un propósito particular**. Consulta la GNU General Public License para más detalles.

Deberías haber recibido una copia de la GNU General Public License junto con este programa. Si no es así, consulta <https://www.gnu.org/licenses/>.

```
SPDX-License-Identifier: GPL-3.0-or-later
```
