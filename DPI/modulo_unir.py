"""
modulo_unir.py — Diálogo de unión de archivos .rwl Tucson

Reemplazo integrado de la antigua utilidad DendroUnir, accesible como
botón en la ventana principal de DPI. Permite a un usuario abrir el
programa SOLO para unir archivos sin tener que cargar imágenes ni
co-fechar.

Funcionalidades:
  - Selección de archivos vía botón o drag & drop sobre la lista
  - Vista previa por archivo: cantidad de series, rango de años
  - Detección automática de IDs duplicados entre archivos
  - Estrategias de resolución de conflictos (renombrar / primero / todos)
  - Exportación en formato Tucson .rwl (estándar dendrocronológico)
  - Exportación en Excel: UNA hoja con series en columnas y años como
    índice (formato preferido del usuario para análisis posterior)

Reutiliza:
  - leer_tucson_multi() de modulo_codatacion.py
  - Helpers de escritura Tucson en este mismo módulo (basados en
    ExportadorDendro._escribir_tucson de modulo_medicion.py)
"""

from __future__ import annotations

import os
import re
import pandas as pd

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QBrush
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QFileDialog, QMessageBox,
    QComboBox, QFrame, QLineEdit, QGroupBox, QRadioButton,
    QButtonGroup, QApplication,
)

from modulo_codatacion import leer_tucson_multi
from constantes import (
    TUCSON_MAX_CHARS_ID, TUCSON_VALOR_FIN, TUCSON_VALOR_999,
    TUCSON_DIVISOR,
)


# =============================================================================
# Helpers de ordenamiento
# =============================================================================

def _clave_orden_natural(texto: str) -> list:
    """Genera una clave de orden alfanumérico natural.

    Permite que ['a1', 'a2', 'a10'] se ordenen como [a1, a2, a10] en
    lugar del orden lexicográfico clásico [a1, a10, a2]. Esto es
    importante en dendrocronología donde los códigos suelen tener
    sufijos numéricos (AUST001, AUST002, ..., AUST010) y el orden
    lexicográfico no respeta la secuencia natural.

    Funciona partiendo el texto en runs alternados de dígitos y no
    dígitos, convirtiendo los runs numéricos a int para que la
    comparación los ordene como números, no como strings.
    """
    partes = re.split(r'(\d+)', texto)
    return [int(p) if p.isdigit() else p.lower() for p in partes]


# =============================================================================
# Helpers de escritura
# =============================================================================

def _escribir_tucson_serie(codigo: str, anio_inicio: int,
                            mediciones_mm: list[float], buffer: list[str]) -> None:
    """Agrega las líneas Tucson de UNA serie a una lista de líneas.

    Sigue el formato fijo de columnas (8 chars id + 4 chars año + 10 valores
    de 6 chars), con primera línea PARCIAL si el año de inicio no es múltiplo
    de 10, y marcador `TUCSON_VALOR_FIN` al final.

    Réplica de la lógica probada de ExportadorDendro._escribir_tucson
    (modulo_medicion.py), generalizada para escribir varias series al mismo
    buffer compartido.
    """
    codigo = codigo[:TUCSON_MAX_CHARS_ID]

    # Convertir mediciones mm → centésimas de mm (entero), reemplazar 999
    mods: list[int] = []
    for v in mediciones_mm:
        try:
            ent = int(round(float(v) * TUCSON_DIVISOR))
        except (TypeError, ValueError):
            ent = 0
        if ent == 999:
            ent = TUCSON_VALOR_999
        mods.append(ent)

    n = len(mods)
    lineas_serie: list[str] = []
    idx = 0

    # Primera fila parcial si el año no es múltiplo de 10
    resto = anio_inicio % 10
    primeros = (10 - resto) if resto != 0 else 10
    if primeros < 10:
        cantidad = min(primeros, n - idx)
        if cantidad > 0:
            fila = f"{codigo:8s}{anio_inicio + idx:4d}"
            for v in mods[idx: idx + cantidad]:
                fila += f"{v:6d}"
            lineas_serie.append(fila)
            idx += cantidad

    # Filas completas de 10 valores
    while idx < n:
        cantidad = min(10, n - idx)
        fila = f"{codigo:8s}{anio_inicio + idx:4d}"
        for v in mods[idx: idx + cantidad]:
            fila += f"{v:6d}"
        lineas_serie.append(fila)
        idx += cantidad

    # Marcador de fin de serie
    if n == 0:
        lineas_serie = [f"{codigo:8s}{anio_inicio:4d}{TUCSON_VALOR_FIN:6d}"]
    else:
        # Si la última fila tenía 10 valores, agregamos una nueva fila con
        # solo el marcador de fin para no exceder la columna
        ultima = lineas_serie[-1]
        # Contar valores en la última fila (cada uno ocupa 6 chars desde col 12)
        n_valores_ultima = (len(ultima) - 12) // 6
        if n_valores_ultima >= 10:
            lineas_serie.append(
                f"{codigo:8s}{anio_inicio + idx:4d}{TUCSON_VALOR_FIN:6d}")
        else:
            lineas_serie[-1] += f"{TUCSON_VALOR_FIN:6d}"

    buffer.extend(lineas_serie)


def escribir_tucson_merge(series_dict: dict[str, pd.DataFrame], ruta: str) -> None:
    """Escribe un archivo Tucson .rwl con TODAS las series en `series_dict`.

    `series_dict[id] = DataFrame` con index Anio y columna Ancho_mm.
    Cada serie va una tras otra, con un encabezado de comentario para
    documentar el origen.
    """
    if not ruta.lower().endswith((".rwl", ".txt")):
        ruta += ".rwl"

    buffer: list[str] = []
    for sid, df in series_dict.items():
        # df puede tener index Anio o columna; uniformamos
        if "Anio" in df.columns:
            df_local = df.set_index("Anio")
        else:
            df_local = df

        df_local = df_local.sort_index()
        anio_inicio = int(df_local.index.min())
        mediciones = df_local["Ancho_mm"].tolist()
        _escribir_tucson_serie(sid, anio_inicio, mediciones, buffer)

    with open(ruta, "w", encoding="utf-8", newline="\r\n") as f:
        for ln in buffer:
            f.write(ln.rstrip() + "\n")


def escribir_excel_merge(series_dict: dict[str, pd.DataFrame], ruta: str) -> None:
    """Escribe TODAS las series en UNA hoja Excel.

    Estructura:
      - Columna A: Año
      - Columnas B, C, D, ...: una por serie (header = ID de la serie)
      - Celdas: ancho en mm; vacías donde la serie no tiene dato para
        ese año

    Este formato es el preferido del usuario porque facilita análisis
    posterior (importar a R, hacer cofechado, etc.) sin tener que
    deshacer la separación por hojas.
    """
    if not ruta.lower().endswith((".xlsx", ".xls")):
        ruta += ".xlsx"

    # Construir DataFrame combinado con Año como index, una columna por serie
    series_alineadas: dict[str, pd.Series] = {}
    for sid, df in series_dict.items():
        if "Anio" in df.columns:
            df_local = df.set_index("Anio")
        else:
            df_local = df
        s = df_local["Ancho_mm"].copy()
        s.name = sid
        series_alineadas[sid] = s

    combinado = pd.DataFrame(series_alineadas)
    combinado.sort_index(inplace=True)
    combinado.index.name = "Anio"

    # Exportar: incluir el index (Año) como primera columna
    combinado.to_excel(ruta, sheet_name="Series", index=True)


# =============================================================================
# Análisis de archivos cargados
# =============================================================================

class _InfoArchivo:
    """Estructura simple para guardar información de un archivo cargado."""

    def __init__(self, ruta: str):
        self.ruta = ruta
        self.nombre = os.path.basename(ruta)
        self.series: dict[str, pd.DataFrame] = {}
        self.error: str | None = None
        self.anio_min: int | None = None
        self.anio_max: int | None = None

        try:
            self.series = leer_tucson_multi(ruta)
            if self.series:
                todos_anios = []
                for df in self.series.values():
                    if "Anio" in df.columns:
                        todos_anios.extend(df["Anio"].tolist())
                    else:
                        todos_anios.extend(df.index.tolist())
                if todos_anios:
                    self.anio_min = int(min(todos_anios))
                    self.anio_max = int(max(todos_anios))
        except Exception as e:
            self.error = str(e)

    def es_valido(self) -> bool:
        return self.error is None and bool(self.series)

    def resumen(self) -> str:
        if self.error:
            return f"✗ {self.nombre}  —  Error: {self.error}"
        n = len(self.series)
        if n == 0:
            return f"⚠ {self.nombre}  —  sin series válidas"
        rango = f"{self.anio_min}–{self.anio_max}" if self.anio_min is not None else "?"
        return f"✓ {self.nombre}  —  {n} serie{'s' if n != 1 else ''}, años {rango}"


# =============================================================================
# Lista de archivos con drag & drop
# =============================================================================

class ListaArchivosDrop(QListWidget):
    """QListWidget que acepta archivos arrastrados desde el explorador.

    Emite la señal `archivos_arrastrados` con la lista de rutas cuando
    el usuario suelta archivos sobre el widget.
    """

    archivos_arrastrados = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            rutas = []
            for url in event.mimeData().urls():
                ruta = url.toLocalFile()
                if ruta:
                    rutas.append(ruta)
            if rutas:
                self.archivos_arrastrados.emit(rutas)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


# =============================================================================
# Diálogo principal
# =============================================================================

class DialogoUnirRwl(QDialog):
    """Diálogo modal para unir archivos .rwl Tucson en uno solo.

    Flujo de uso:
      1. Usuario agrega archivos (botón + o drag & drop)
      2. El diálogo parsea cada archivo en background-ish, muestra resumen
      3. Detecta IDs duplicados y avisa
      4. Usuario elige estrategia de duplicados y formato de salida
      5. Usuario indica archivo destino y presiona "Unir archivos"
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🔗 Unir archivos .rwl")
        self.setMinimumSize(700, 600)

        # Estructura interna
        self._archivos: list[_InfoArchivo] = []

        self._construir_ui()
        self._actualizar_resumen()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _construir_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Título y descripción
        titulo = QLabel("🔗 Unir archivos Tucson .rwl")
        f = QFont(); f.setBold(True); f.setPointSize(12)
        titulo.setFont(f)
        layout.addWidget(titulo)

        descripcion = QLabel(
            "Selecciona múltiples archivos .rwl y se unirán en un solo "
            "archivo de salida. Puedes arrastrar los archivos directamente "
            "sobre la lista o usar el botón para agregarlos."
        )
        descripcion.setWordWrap(True)
        descripcion.setStyleSheet("color: #888; font-style: italic;")
        layout.addWidget(descripcion)

        # ── Sección: lista de archivos ─────────────────────────────────────
        grupo_archivos = QGroupBox("Archivos a unir")
        v_archivos = QVBoxLayout(grupo_archivos)

        self.lista_archivos = ListaArchivosDrop()
        self.lista_archivos.archivos_arrastrados.connect(self._agregar_archivos)
        self.lista_archivos.setMinimumHeight(150)
        v_archivos.addWidget(self.lista_archivos)

        # Botones de gestión de la lista
        fila_botones_lista = QHBoxLayout()
        btn_agregar = QPushButton("➕ Agregar archivos")
        btn_agregar.clicked.connect(self._dialog_agregar)
        fila_botones_lista.addWidget(btn_agregar)

        btn_quitar = QPushButton("➖ Quitar seleccionados")
        btn_quitar.clicked.connect(self._quitar_seleccionados)
        fila_botones_lista.addWidget(btn_quitar)

        btn_limpiar = QPushButton("🗑 Limpiar lista")
        btn_limpiar.clicked.connect(self._limpiar_lista)
        fila_botones_lista.addWidget(btn_limpiar)
        fila_botones_lista.addStretch()
        v_archivos.addLayout(fila_botones_lista)

        layout.addWidget(grupo_archivos)

        # ── Sección: resumen del merge ─────────────────────────────────────
        self.lbl_resumen = QLabel()
        self.lbl_resumen.setWordWrap(True)
        self.lbl_resumen.setStyleSheet(
            "QLabel { background-color: rgba(80, 120, 180, 0.10); "
            "padding: 10px; border-radius: 4px; }"
        )
        layout.addWidget(self.lbl_resumen)

        # ── Sección: estrategia para IDs duplicados ────────────────────────
        grupo_dup = QGroupBox("Si hay IDs de series duplicados entre archivos")
        v_dup = QVBoxLayout(grupo_dup)
        self.grupo_radio_dup = QButtonGroup(self)

        self.rb_renombrar = QRadioButton(
            "Renombrar agregando sufijo (_2, _3, ...) — recomendado")
        self.rb_renombrar.setChecked(True)
        self.grupo_radio_dup.addButton(self.rb_renombrar)
        v_dup.addWidget(self.rb_renombrar)

        self.rb_primero = QRadioButton(
            "Mantener solo la primera aparición (descarta el resto)")
        self.grupo_radio_dup.addButton(self.rb_primero)
        v_dup.addWidget(self.rb_primero)

        self.rb_combinar = QRadioButton(
            "Combinar valores por año (promediar) — útil si son re-mediciones")
        self.grupo_radio_dup.addButton(self.rb_combinar)
        v_dup.addWidget(self.rb_combinar)

        layout.addWidget(grupo_dup)

        # ── Sección: formato y destino ─────────────────────────────────────
        grupo_salida = QGroupBox("Formato y archivo de salida")
        v_salida = QVBoxLayout(grupo_salida)

        fila_formato = QHBoxLayout()
        fila_formato.addWidget(QLabel("Formato:"))
        self.combo_formato = QComboBox()
        self.combo_formato.addItems([
            "Tucson .rwl  —  estándar dendrocronológico",
            "Excel .xlsx  —  series en columnas, años como filas",
            "Ambos formatos  —  genera .rwl y .xlsx al mismo tiempo",
        ])
        self.combo_formato.currentIndexChanged.connect(self._actualizar_extension_destino)
        fila_formato.addWidget(self.combo_formato, 1)
        v_salida.addLayout(fila_formato)

        fila_destino = QHBoxLayout()
        fila_destino.addWidget(QLabel("Archivo:"))
        self.input_destino = QLineEdit()
        self.input_destino.setPlaceholderText("(elige el archivo de salida)")
        fila_destino.addWidget(self.input_destino, 1)
        btn_examinar = QPushButton("📁 Examinar...")
        btn_examinar.clicked.connect(self._elegir_destino)
        fila_destino.addWidget(btn_examinar)
        v_salida.addLayout(fila_destino)

        layout.addWidget(grupo_salida)

        # ── Botones de acción ──────────────────────────────────────────────
        fila_accion = QHBoxLayout()
        fila_accion.addStretch()
        btn_cancelar = QPushButton("Cancelar")
        btn_cancelar.clicked.connect(self.reject)
        fila_accion.addWidget(btn_cancelar)

        self.btn_unir = QPushButton("✓ Unir archivos")
        self.btn_unir.setDefault(True)
        self.btn_unir.setStyleSheet(
            "QPushButton { background: #8e44ad; color: white; "
            "font-weight: bold; padding: 8px 16px; border-radius: 4px; } "
            "QPushButton:hover { background: #9b59b6; } "
            "QPushButton:disabled { background: #666; color: #aaa; }"
        )
        self.btn_unir.clicked.connect(self._ejecutar_merge)
        fila_accion.addWidget(self.btn_unir)
        layout.addLayout(fila_accion)

    # ── Gestión de archivos ────────────────────────────────────────────────

    def _dialog_agregar(self):
        """Abre el diálogo para seleccionar archivos .rwl.

        Tres detalles importantes:
          - `DontUseNativeDialog` fuerza el diálogo de Qt, que respeta el
            tema (claro/oscuro) de la aplicación. Sin esta opción Linux
            usa el diálogo nativo del sistema que se ve BLANCO incluso
            con tema oscuro activado.
          - Carpeta inicial = la del último archivo agregado, si hay,
            para que el usuario no tenga que navegar de cero cada vez
            que agrega más archivos de la misma carpeta.
          - Filtro Tucson cubre mayúsculas y minúsculas (en Linux el
            matching es case-sensitive: si los archivos terminan en
            ".RWL" no aparecen con un patrón ".rwl"). Por eso antes
            algunos archivos no se veían hasta cambiar a "Todos".
        """
        # Carpeta inicial: la del último archivo agregado, o la home
        if self._archivos:
            carpeta_inicial = os.path.dirname(self._archivos[-1].ruta)
        else:
            carpeta_inicial = ""

        rutas, _ = QFileDialog.getOpenFileNames(
            self,
            "Seleccionar archivos .rwl",
            carpeta_inicial,
            "Tucson (*.rwl *.RWL *.txt *.TXT *.crn *.CRN);;"
            "Todos los archivos (*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if rutas:
            self._agregar_archivos(rutas)

    def _agregar_archivos(self, rutas: list[str]):
        """Agrega una lista de archivos a la lista, parseando cada uno.

        Después de agregar se reordena la lista completa con orden
        alfanumérico natural por nombre de archivo. Esto facilita
        detectar duplicados visualmente (archivos con códigos cercanos
        quedan juntos: AUST001, AUST002, AUST003... en vez del orden
        en que fueron seleccionados, que puede ser aleatorio si vienen
        de un drag & drop o si el sistema operativo los devuelve sin
        ordenar).
        """
        # Evitar duplicados (mismo path)
        rutas_existentes = {a.ruta for a in self._archivos}
        agregados = 0
        for ruta in rutas:
            if not os.path.isfile(ruta):
                continue
            if ruta in rutas_existentes:
                continue
            info = _InfoArchivo(ruta)
            self._archivos.append(info)
            agregados += 1

        if agregados == 0:
            return

        # Reordenar TODA la lista (la previa + la recién agregada) con
        # orden alfanumérico natural por nombre de archivo. No se ordena
        # por ruta completa porque archivos del mismo proyecto pueden
        # estar dispersos en subcarpetas con prefijos distintos.
        self._archivos.sort(key=lambda a: _clave_orden_natural(a.nombre))

        # Reconstruir la lista visual completa para reflejar el orden
        self.lista_archivos.clear()
        for info in self._archivos:
            self._agregar_item_lista(info)

        self._actualizar_resumen()

    def _agregar_item_lista(self, info: _InfoArchivo):
        """Agrega un item visual a la lista con código de color por estado."""
        item = QListWidgetItem(info.resumen())
        if info.error or not info.series:
            # Rojo claro: archivo con problemas
            item.setForeground(QBrush(QColor("#d94545")))
        elif info.anio_min is None:
            item.setForeground(QBrush(QColor("#cca72e")))
        # Si todo OK, color por defecto del tema
        item.setData(Qt.ItemDataRole.UserRole, info.ruta)
        item.setToolTip(info.ruta)
        self.lista_archivos.addItem(item)

    def _quitar_seleccionados(self):
        """Quita los archivos seleccionados en la lista."""
        items_sel = self.lista_archivos.selectedItems()
        if not items_sel:
            return
        rutas_a_quitar = {it.data(Qt.ItemDataRole.UserRole) for it in items_sel}
        self._archivos = [a for a in self._archivos if a.ruta not in rutas_a_quitar]

        # Reconstruir la lista visual
        self.lista_archivos.clear()
        for info in self._archivos:
            self._agregar_item_lista(info)
        self._actualizar_resumen()

    def _limpiar_lista(self):
        """Vacía completamente la lista de archivos."""
        if not self._archivos:
            return
        if QMessageBox.question(
            self, "Confirmar",
            "¿Quitar todos los archivos de la lista?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._archivos.clear()
        self.lista_archivos.clear()
        self._actualizar_resumen()

    # ── Análisis / resumen ─────────────────────────────────────────────────

    def _actualizar_resumen(self):
        """Recalcula el resumen del merge y actualiza el label informativo."""
        validos = [a for a in self._archivos if a.es_valido()]

        if not validos:
            self.lbl_resumen.setText(
                "📊 <b>Resumen:</b> ningún archivo válido cargado todavía."
            )
            self.btn_unir.setEnabled(False)
            return

        # Calcular totales
        n_total_series = sum(len(a.series) for a in validos)
        anios_min = [a.anio_min for a in validos if a.anio_min is not None]
        anios_max = [a.anio_max for a in validos if a.anio_max is not None]
        anio_min_global = min(anios_min) if anios_min else None
        anio_max_global = max(anios_max) if anios_max else None

        # Detectar IDs duplicados entre archivos
        ids_por_archivo: list[set[str]] = [set(a.series.keys()) for a in validos]
        contador_id: dict[str, int] = {}
        for s_set in ids_por_archivo:
            for sid in s_set:
                contador_id[sid] = contador_id.get(sid, 0) + 1
        ids_duplicados = [sid for sid, n in contador_id.items() if n > 1]

        # Armar el texto
        linea1 = (f"📊 <b>{n_total_series} series totales</b> en "
                  f"<b>{len(validos)} archivo{'s' if len(validos) != 1 else ''}</b>")
        if anio_min_global is not None:
            n_anios = anio_max_global - anio_min_global + 1
            linea2 = (f"<br>📅 Rango de años: <b>{anio_min_global}–{anio_max_global}</b> "
                      f"({n_anios} años)")
        else:
            linea2 = ""

        if ids_duplicados:
            preview = ", ".join(ids_duplicados[:5])
            extras = (f" y {len(ids_duplicados) - 5} más"
                      if len(ids_duplicados) > 5 else "")
            linea3 = (f"<br>⚠ <b>{len(ids_duplicados)} ID{'s' if len(ids_duplicados) != 1 else ''} "
                      f"duplicado{'s' if len(ids_duplicados) != 1 else ''}</b> "
                      f"entre archivos: <i>{preview}{extras}</i>")
            linea3 += "<br><span style='color:#888'>Se aplicará la estrategia seleccionada abajo.</span>"
        else:
            linea3 = "<br>✓ Sin conflictos de IDs duplicados."

        self.lbl_resumen.setText(linea1 + linea2 + linea3)
        self.btn_unir.setEnabled(True)

    # ── Destino ────────────────────────────────────────────────────────────

    def _actualizar_extension_destino(self):
        """Ajusta la extensión del archivo destino al cambiar el formato.

        - Tucson → .rwl
        - Excel  → .xlsx
        - Ambos  → sin extensión (se quita; al guardar se generan ambos
          archivos a partir del nombre base)
        """
        texto_actual = self.input_destino.text().strip()
        if not texto_actual:
            return
        # Quitar extensión actual
        base, _ = os.path.splitext(texto_actual)
        idx = self.combo_formato.currentIndex()
        if idx == 0:
            self.input_destino.setText(base + ".rwl")
        elif idx == 1:
            self.input_destino.setText(base + ".xlsx")
        else:
            # "Ambos": dejamos solo el nombre base; el código de
            # ejecución agrega .rwl y .xlsx al guardar.
            self.input_destino.setText(base)

    def _elegir_destino(self):
        """Abre el diálogo para elegir el archivo de salida.

        - Carpeta inicial: la del primer archivo cargado, sugiriendo
          "unido.rwl/xlsx" o un nombre coherente para "ambos formatos".
        - `DontUseNativeDialog` para respetar el tema oscuro/claro (en
          Linux el diálogo nativo siempre se ve blanco).
        """
        idx_formato = self.combo_formato.currentIndex()
        if idx_formato == 0:        # Tucson
            filtro = "Tucson (*.rwl);;Todos los archivos (*)"
            ext_default = ".rwl"
        elif idx_formato == 1:      # Excel
            filtro = "Excel (*.xlsx);;Todos los archivos (*)"
            ext_default = ".xlsx"
        else:                        # Ambos
            # Para "ambos" pedimos el nombre BASE (sin extensión).
            # El diálogo permite cualquier extensión; el código quitará
            # la extensión y agregará .rwl y .xlsx por separado.
            filtro = "Todos los archivos (*)"
            ext_default = ""

        # Sugerir nombre por defecto basado en el primer archivo
        sugerencia = ""
        if self._archivos:
            base = os.path.dirname(self._archivos[0].ruta)
            if idx_formato == 2:
                sugerencia = os.path.join(base, "unido")
            else:
                sugerencia = os.path.join(base, "unido" + ext_default)

        ruta, _ = QFileDialog.getSaveFileName(
            self, "Guardar archivo unido", sugerencia, filtro,
            options=(
                QFileDialog.Option.DontUseNativeDialog
                # `DontConfirmOverwrite` evita la confirmación interna de
                # Qt ("X already exists. Do you want to replace it?") que
                # aparece en INGLÉS aunque la app tenga traducción al
                # español cargada. Tenemos nuestra propia confirmación
                # en `_ejecutar_merge`, que SÍ está en español y además
                # maneja el caso "ambos formatos" (dos archivos).
                | QFileDialog.Option.DontConfirmOverwrite
            ),
        )
        if ruta:
            # Para Tucson/Excel asegurar la extensión correcta
            if ext_default and not ruta.lower().endswith(ext_default):
                ruta += ext_default
            self.input_destino.setText(ruta)

    # ── Ejecución del merge ────────────────────────────────────────────────

    def _ejecutar_merge(self):
        """Realiza el merge y guarda en el archivo destino.

        Para "Ambos formatos" se generan dos archivos a partir del nombre
        base ingresado: uno .rwl y otro .xlsx. Si alguno existe se
        confirma sobreescritura individualmente.
        """
        validos = [a for a in self._archivos if a.es_valido()]
        if not validos:
            QMessageBox.warning(self, "Sin archivos",
                                 "No hay archivos válidos para unir.")
            return

        ruta_destino = self.input_destino.text().strip()
        if not ruta_destino:
            QMessageBox.warning(self, "Sin destino",
                                 "Indica el archivo de salida primero.")
            return

        idx_formato = self.combo_formato.currentIndex()

        # Determinar qué archivos vamos a escribir
        if idx_formato == 0:
            rutas_a_escribir = [(ruta_destino, "tucson")]
        elif idx_formato == 1:
            rutas_a_escribir = [(ruta_destino, "excel")]
        else:
            # Ambos formatos: quitar extensión si la hubiera y generar dos
            base, _ = os.path.splitext(ruta_destino)
            rutas_a_escribir = [
                (base + ".rwl", "tucson"),
                (base + ".xlsx", "excel"),
            ]

        # Verificar sobreescritura para cada archivo
        for ruta, _tipo in rutas_a_escribir:
            if os.path.exists(ruta):
                if QMessageBox.question(
                    self, "Sobrescribir",
                    f"El archivo ya existe:\n{ruta}\n\n¿Sobrescribirlo?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                ) != QMessageBox.StandardButton.Yes:
                    return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            # Combinar todas las series aplicando la estrategia de duplicados
            series_finales = self._combinar_series(validos)

            # Escribir cada archivo según su tipo
            archivos_escritos = []
            for ruta, tipo in rutas_a_escribir:
                if tipo == "tucson":
                    escribir_tucson_merge(series_finales, ruta)
                else:
                    escribir_excel_merge(series_finales, ruta)
                archivos_escritos.append(ruta)

            QApplication.restoreOverrideCursor()

            n = len(series_finales)
            if len(archivos_escritos) == 1:
                mensaje = (f"✓ Se unieron <b>{n}</b> series en:<br>"
                           f"<i>{archivos_escritos[0]}</i>")
            else:
                lista_html = "<br>".join(
                    f"<i>{r}</i>" for r in archivos_escritos)
                mensaje = (f"✓ Se unieron <b>{n}</b> series en "
                           f"<b>{len(archivos_escritos)} archivos</b>:<br>"
                           f"{lista_html}")

            QMessageBox.information(self, "Archivos unidos", mensaje)
            self.accept()

        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(
                self, "Error al unir archivos",
                f"Ocurrió un error durante la unión:\n\n{e}",
            )

    def _combinar_series(self,
                         archivos: list[_InfoArchivo]) -> dict[str, pd.DataFrame]:
        """Aplica la estrategia de duplicados elegida y devuelve un dict
        final de series listas para exportar.

        Estrategias:
          - Renombrar: cada duplicado recibe sufijo _2, _3, ...
          - Mantener primero: descartar todas las apariciones posteriores
          - Combinar: promediar valores año por año
        """
        if self.rb_renombrar.isChecked():
            estrategia = "renombrar"
        elif self.rb_primero.isChecked():
            estrategia = "primero"
        else:
            estrategia = "combinar"

        resultado: dict[str, pd.DataFrame] = {}

        for info in archivos:
            for sid, df in info.series.items():
                if sid not in resultado:
                    resultado[sid] = df.copy()
                    continue

                # ID ya existe → aplicar estrategia
                if estrategia == "primero":
                    continue
                elif estrategia == "renombrar":
                    # Encontrar el siguiente sufijo libre
                    n = 2
                    while f"{sid}_{n}"[:TUCSON_MAX_CHARS_ID] in resultado:
                        n += 1
                    nuevo_sid = f"{sid}_{n}"[:TUCSON_MAX_CHARS_ID]
                    resultado[nuevo_sid] = df.copy()
                else:  # combinar
                    # Promediar valores por año entre las dos series
                    df_existente = resultado[sid]
                    if "Anio" in df.columns:
                        df_b = df.set_index("Anio")
                    else:
                        df_b = df
                    if "Anio" in df_existente.columns:
                        df_a = df_existente.set_index("Anio")
                    else:
                        df_a = df_existente
                    # Concatenar y promediar por año
                    combinado = pd.concat([df_a, df_b])
                    combinado = combinado.groupby(combinado.index).mean()
                    combinado.index.name = "Anio"
                    resultado[sid] = combinado

        return resultado
