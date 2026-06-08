# 🌲 DendroLen v2.0

**Aplicación de escritorio para medición de anillos de árboles con carro de medición serial.**

Desarrollada por **Moisés E. Rojas Badilla** como parte del proyecto **MoiCedrus**.

---

## Descripción

DendroLen es una herramienta especializada para dendrocronólogos que permite medir anchos de anillos de árboles en tiempo real mediante un carro de medición VRO conectado por puerto USB-serial. 
Las mediciones se exportan directamente al formato estándar Tucson (`.rwl`), compatible con COFECHA, ARSTAN, dplR y el resto del ecosistema dendrocronológico.

Incluye un módulo de co-datación en vivo con serie maestra, que calcula y visualiza la correlación entre la serie que se está midiendo y una cronología de referencia, 
actualizado en tiempo real a medida que avanza la medición.

---

## Características principales

- **Medición en tiempo real** mediante carro VRO con comunicación USB-serial
- **Exportación Tucson** (`.rwl`) con terminador `-9999` y formato estándar
- **Registro de anomalías** con sidecar `.rwl.anom.json`: Frost ring, Light ring, Anillo falso, Missing ring, Fuego, Golpe, Otro
- **Co-datación en vivo** con serie maestra: correlación global y deslizante estilo COFECHA
- **Visualización en tiempo real** de la serie en construcción vs. la referencia
- **Temas oscuro y claro** con detección automática del tema del sistema operativo
- **Interfaz redimensionable** con paneles ajustables mediante splitters

---

## Requisitos

- Python 3.10 o superior
- Linux, macOS o Windows

### Dependencias Python

```bash
pip install PyQt6 matplotlib pyserial pandas numpy openpyxl
```

### Hardware

- Carro de medición con encoder VRO y salida USB-serial (adaptador FTDI, CH340, CP210x o similar)
- Puerto USB-A o USB-C disponible

---

## Instalación

```bash
# Clonar o descomprimir en la carpeta deseada
cd DendroLen

# Crear entorno virtual (recomendado)
python3 -m venv ../MoiCedrus
source ../MoiCedrus/bin/activate

# Instalar dependencias
pip install PyQt6 matplotlib pyserial pandas numpy openpyxl

# Ejecutar
python3 main_dendro.py
```

### Script de lanzamiento (Linux/macOS)

Crea un archivo `DendroLen.sh`:

```bash
#!/bin/bash
cd "/ruta/a/tu/entorno/MoiCedrus"
source bin/activate
cd "/ruta/a/DendroLen"
python3 main_dendro.py
```

```bash
chmod +x DendroLen.sh
./DendroLen.sh
```

---

## Estructura del proyecto

```
DendroLen/
├── main_dendro.py          # Punto de entrada, verificación de dependencias
├── constantes.py           # Parámetros globales, especies, anomalías, temas
├── tema_manager.py         # Gestor de tema oscuro/claro (singleton)
├── ventana_principal.py    # Ventana principal, navegación entre pantallas
├── dialogo_config.py       # Pantalla 1: asistente de configuración
├── dialogo_nueva_muestra.py # Modal de nueva muestra
├── dialogo_ayuda.py        # Modal de atajos de teclado
├── panel_medicion.py       # Pantalla 2: panel de medición en tiempo real
├── serial_worker.py        # Hilo QThread de lectura del puerto serie
├── serie_maestra.py        # Panel de co-datación con serie maestra
└── tucson_writer.py        # Escritura de archivos Tucson y sidecar JSON
```

---

## Uso rápido

### 1. Configurar la sesión

Al iniciar el programa aparece el asistente de configuración:

- **Código de muestra**: identificador de la serie (ej. `CHI001A`). El radio va incluido en el nombre.
- **Año de inicio**: año del primer anillo medido (médula). Rango: −10.000 a 2.200.
- **Especie**: selecciona de la lista o escribe libremente si la especie no aparece.
- **Puerto serie**: se detecta automáticamente. Usa el botón ⟳ para refrescar.
- **Velocidad (baud)**: 9600 por defecto (VRO estándar).
- **Archivo de salida**: nombre del archivo `.rwl`. Si no tiene ruta absoluta, el programa preguntará dónde guardarlo al primer guardado.

### 2. Fijar la referencia

Una vez conectado el carro, colócalo en la posición de inicio (médula) y **presiona el botón del carro** para fijar la referencia. El programa mostrará el mensaje de confirmación en el registro.

### 3. Medir

Desplaza el carro hacia la corteza. Cada vez que el encoder registra un nuevo anillo, aparece automáticamente en:
- La barra de información superior (último valor, año actual, n° anillos)
- El registro de medición
- La vista previa Tucson
- El gráfico de co-datación

### 4. Guardar

| Acción | Atajo | Descripción |
|--------|-------|-------------|
| Guardar y continuar | `Ctrl+G` | Guarda el archivo y sigue midiendo |
| Guardar y cerrar radio | `Ctrl+S` | Guarda y vuelve al asistente |

El primer guardado abre un explorador para elegir la ubicación. Los siguientes guardados usan la misma ruta.

---

## Atajos de teclado

| Tecla | Acción |
|-------|--------|
| `Ctrl+G` | Guardar y continuar midiendo |
| `Ctrl+S` | Guardar y cerrar radio |
| `Ctrl+Z` | Deshacer / remedir el último anillo |
| `Ctrl+P` | Pausar / reanudar recepción del carro |
| `Ctrl+Y` | Editar año de inicio en caliente |
| `F1` | Mostrar ayuda (tabla de atajos) |
| `F2` | Marcar Frost ring (FR) |
| `F3` | Marcar Light ring (LR) |
| `F4` | Marcar Anillo falso (AF) |
| `F5` | Marcar Missing ring (MR) |
| `F6` | Marcar Fuego (FU) |
| `F7` | Marcar Golpe (GO) |
| `F8` | Marcar Otro (OT) |
| `F9` | Reconectar puerto serie |

> Las anomalías se marcan sobre el **último anillo medido** y funcionan como toggle: presionar la misma tecla nuevamente elimina la marca.

---

## Formatos de serie maestra

El panel de co-datación acepta los siguientes formatos:

| Formato | Extensión | Descripción |
|---------|-----------|-------------|
| Tucson | `.rwl`, `.txt`, `.TXT` | Formato estándar dendrocronológico |
| CooRecorder | `.wid` | Archivo de cronologías CooRecorder |
| Excel | `.xlsx`, `.xls` | Primera columna: año, segunda columna: mm |
| CSV | `.csv` | Primera columna: año, segunda columna: mm |

Para Excel y CSV, el nombre de los encabezados no importa — el programa usa la posición de las columnas.

---

## Archivos generados

### Archivo Tucson (`.rwl`)

Formato estándar con identificador de 8 caracteres, año de 4 dígitos y valores en milésimas de mm, 10 valores por línea, terminado en `-9999`.

### Sidecar de anomalías (`.rwl.anom.json`)

Generado automáticamente al registrar anomalías. Estructura:

```json
{
  "CHI001A": {
    "anio_medula": 1750,
    "anio_corteza": 2023,
    "especie": "Nothofagus obliqua",
    "eventos": {
      "1816": "FR",
      "1960": "LR"
    },
    "eventos_por_tipo": {
      "FR": [1816],
      "LR": [1960]
    }
  }
}
```

---

## Co-datación en vivo

El panel de co-datación (parte inferior de la pantalla de medición) muestra:

- **Curva azul**: serie en construcción (normalizada z-score cuando hay solapamiento)
- **Curva naranja punteada**: serie maestra de referencia
- **Fondos coloreados**: correlación deslizante por segmentos
  - 🟢 Verde oscuro: r ≥ 0.50
  - 🟢 Verde claro: r ≥ 0.35
  - 🟡 Naranja: r ≥ 0.15
  - 🔴 Rojo: r < 0.15
- **Etiquetas r=**: valor de correlación al fondo de cada segmento
- **Barra inferior**: r global, símbolo de calidad (●●●), y r por ventanas temporales

### Ventana de correlación

El selector de ventana ofrece: 5, 10, 15, 20, **30** (por defecto), 50, 100 años.

### Zoom e interacción

- **Rueda del mouse**: zoom centrado en la posición del cursor
- **Doble clic**: volver al rango automático (autorange)
- En modo automático, el gráfico sigue la medición en tiempo real

---

## Notas técnicas

- Las mediciones se almacenan internamente en **milésimas de mm** (enteros), igual que el formato Tucson original
- La correlación usa **diferencias logarítmicas** (método estándar COFECHA)
- La normalización z-score se aplica automáticamente cuando hay solapamiento ≥ 5 años
- El lector Tucson acepta años en el rango −10.000 a 2.200 para series subfósiles
- La detección de puerto prioriza adaptadores USB-serial (FTDI, CH340, CP210x) e incluye verificación por `hwid`

---

## Proyecto MoiCedrus — DPI

DendroLen es parte de la suite **MoiCedrus**, un conjunto de herramientas dendrocronológicas open source desarrolladas en Python/PyQt6 para investigadores y estudiantes. Otras aplicaciones de la suite incluyen DendroPixelInterface (medición sobre imagen), DendroEventos (análisis de eventos cambiales), DendroRespo (función de respuesta climática) y DendroUnir (gestión de archivos Tucson).

---

## Autor

**Moisés E. Rojas Badilla**  
Universidad Austral de Chile  
moisese.rojasbadilla@gmail.com
