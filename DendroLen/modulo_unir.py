"""
modulo_unir.py — Diálogo de unión de archivos .rwl Tucson para DendroLen
Adaptado de DPI/modulo_unir.py para usar las constantes de DendroLen.

Funcionalidades:
  - Selección de archivos vía botón o drag & drop sobre la lista
  - Vista previa por archivo: cantidad de series, rango de años
  - Detección automática de IDs duplicados entre archivos
  - Estrategias de resolución de conflictos (renombrar / primero / combinar)
  - Exportación en formato Tucson .rwl (estándar dendrocronológico)
  - Exportación en Excel: UNA hoja con series en columnas y años como índice
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

from constantes import (
    TUCSON_SAMPLE_ID_WIDTH, TUCSON_TERMINATOR, TUCSON_VALUES_PER_LINE,
)
from tema_manager import tema as _tema_mgr


# =============================================================================
# Helpers
# =============================================================================

def _clave_orden_natural(texto: str) -> list:
    """Genera una clave de orden alfanumérico natural.
    Permite que ['a1','a2','a10'] se ordenen correctamente en vez de
    como ['a1','a10','a2'] (orden lexicográfico)."""
    partes = re.split(r'(\d+)', texto)
    return [int(p) if p.isdigit() else p.lower() for p in partes]


def leer_tucson_multi(ruta: str) -> dict:
    """
    Lee un archivo Tucson .rwl con UNA o MÚLTIPLES series.
    Devuelve {serie_id: DataFrame(Anio, Valor)} donde Valor es entero (raw del archivo).
    """
    series_dict: dict[str, dict[int, int]] = {}

    with open(ruta, encoding='utf-8', errors='ignore') as f:
        for linea in f:
            linea = linea.rstrip()
            if not linea or linea.startswith(';'):
                continue
            if len(linea) < 12:
                continue
            sid = linea[:TUCSON_SAMPLE_ID_WIDTH].strip()
            if not sid:
                continue
            try:
                anio_inicio = int(linea[TUCSON_SAMPLE_ID_WIDTH:TUCSON_SAMPLE_ID_WIDTH + 4])
            except ValueError:
                continue

            pos = TUCSON_SAMPLE_ID_WIDTH + 4
            idx_anio = anio_inicio
            while pos + 6 <= len(linea):
                campo = linea[pos:pos + 6].strip()
                if not campo:
                    pos += 6
                    idx_anio += 1
                    continue
                try:
                    v = int(campo)
                except ValueError:
                    break
                if v == TUCSON_TERMINATOR or v == -9999:
                    break
                series_dict.setdefault(sid, {})[idx_anio] = v
                pos += 6
                idx_anio += 1

    result = {}
    for sid, anios_dict in series_dict.items():
        if anios_dict:
            df = pd.DataFrame({
                "Anio": list(anios_dict.keys()),
                "Valor": list(anios_dict.values()),
            }).sort_values("Anio").reset_index(drop=True)
            result[sid] = df
    return result


def _escribir_tucson_serie(codigo: str, anio_inicio: int,
                           valores_int: list, buffer: list) -> None:
    """Agrega líneas Tucson de UNA serie al buffer (formato estándar DendroLen)."""
    codigo = codigo[:TUCSON_SAMPLE_ID_WIDTH]
    n = len(valores_int)
    lineas_serie: list[str] = []
    idx = 0

    # Primera fila parcial si el año no es múltiplo de 10
    resto = anio_inicio % 10
    primeros = (10 - resto) if resto != 0 else 10
    if primeros < 10:
        cantidad = min(primeros, n - idx)
        if cantidad > 0:
            fila = f"{codigo:<{TUCSON_SAMPLE_ID_WIDTH}}{anio_inicio + idx:4d}"
            for v in valores_int[idx: idx + cantidad]:
                fila += f"{v:6d}"
            lineas_serie.append(fila)
            idx += cantidad

    # Filas completas de 10 valores
    while idx < n:
        cantidad = min(TUCSON_VALUES_PER_LINE, n - idx)
        fila = f"{codigo:<{TUCSON_SAMPLE_ID_WIDTH}}{anio_inicio + idx:4d}"
        for v in valores_int[idx: idx + cantidad]:
            fila += f"{v:6d}"
        lineas_serie.append(fila)
        idx += cantidad

    # Marcador fin de serie
    if n == 0:
        lineas_serie = [f"{codigo:<{TUCSON_SAMPLE_ID_WIDTH}}{anio_inicio:4d}{TUCSON_TERMINATOR:6d}"]
    else:
        ultima = lineas_serie[-1]
        n_vals_ultima = (len(ultima) - 12) // 6
        if n_vals_ultima >= TUCSON_VALUES_PER_LINE:
            lineas_serie.append(
                f"{codigo:<{TUCSON_SAMPLE_ID_WIDTH}}{anio_inicio + idx:4d}{TUCSON_TERMINATOR:6d}")
        else:
            lineas_serie[-1] += f"{TUCSON_TERMINATOR:6d}"

    buffer.extend(lineas_serie)


def escribir_tucson_merge(series_dict: dict, ruta: str) -> None:
    """Escribe un archivo Tucson .rwl con todas las series."""
    if not ruta.lower().endswith((".rwl", ".txt")):
        ruta += ".rwl"

    buffer: list[str] = []
    for sid, df in series_dict.items():
        if "Anio" in df.columns:
            df_local = df.set_index("Anio")
        else:
            df_local = df
        df_local = df_local.sort_index()
        anio_inicio = int(df_local.index.min())
        valores = [int(round(v)) for v in df_local["Valor"].tolist()]
        _escribir_tucson_serie(sid, anio_inicio, valores, buffer)

    with open(ruta, "w", encoding="utf-8", newline="\r\n") as f:
        for ln in buffer:
            f.write(ln.rstrip() + "\n")


def escribir_excel_merge(series_dict: dict, ruta: str) -> None:
    """Escribe todas las series en una hoja Excel.
    Estructura: columna A = Año, columnas B+ = una por serie."""
    if not ruta.lower().endswith((".xlsx", ".xls")):
        ruta += ".xlsx"

    todos_anios = set()
    for sid, df in series_dict.items():
        if "Anio" in df.columns:
            todos_anios.update(df["Anio"].tolist())
        else:
            todos_anios.update(df.index.tolist())

    if not todos_anios:
        return

    anios_ord = sorted(int(a) for a in todos_anios)
    cols = {}
    for sid, df in series_dict.items():
        if "Anio" in df.columns:
            df_local = df.set_index("Anio")
        else:
            df_local = df
        cols[sid] = df_local["Valor"]

    merged = pd.DataFrame(cols, index=anios_ord)
    merged.index.name = "Año"
    merged.to_excel(ruta)


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
    """QListWidget que acepta archivos arrastrados desde el explorador."""

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
    """Diálogo modal para unir archivos .rwl Tucson en uno solo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unir archivos .rwl")
        self.setMinimumSize(700, 600)

        self._archivos: list[_InfoArchivo] = []
        self._construir_ui()
        self._aplicar_tema()
        self._actualizar_resumen()

    def _aplicar_tema(self):
        """Aplica el tema actual al diálogo."""
        t = _tema_mgr.t
        self.setStyleSheet(f"""
            QDialog {{ background-color: {t['BG']}; color: {t['TEXT']}; }}
            QLabel {{ color: {t['TEXT']}; background: transparent; }}
            QGroupBox {{
                color: {t['TEXT_TITLE']};
                font-weight: 700;
                border: 1px solid {t['BORDER']};
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 8px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }}
            QPushButton {{
                background-color: {t['BG_CARD']};
                color: {t['TEXT_TITLE']};
                border: 1px solid {t['BORDER']};
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                border: 1px solid {t['BLUE']};
                background-color: {t['BORDER_SOFT']};
            }}
            QPushButton:disabled {{
                background-color: {t['BG_BAR']};
                color: {t['TEXT_DIM']};
            }}
            QListWidget {{
                background-color: {t['BG_CARD']};
                color: {t['TEXT']};
                border: 1px solid {t['BORDER']};
                border-radius: 4px;
            }}
            QLineEdit, QComboBox {{
                background-color: {t['BG_CARD']};
                color: {t['TEXT_TITLE']};
                border: 1px solid {t['BORDER']};
                border-radius: 4px;
                padding: 4px 8px;
                min-height: 28px;
            }}
            QLineEdit:focus, QComboBox:focus {{
                border: 2px solid {t['BLUE']};
            }}
            QRadioButton {{
                color: {t['TEXT']};
                background: transparent;
                padding: 3px;
            }}
        """)

    def _construir_ui(self):
        t = _tema_mgr.t
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        # Título
        titulo = QLabel("🔗  Unir archivos Tucson .rwl")
        titulo.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {t['TEXT_TITLE']};"
            f"font-family: 'Sora','Segoe UI',sans-serif;"
        )
        layout.addWidget(titulo)

        descripcion = QLabel(
            "Selecciona múltiples archivos .rwl y se unirán en un solo archivo. "
            "Puedes arrastrar archivos directamente sobre la lista."
        )
        descripcion.setWordWrap(True)
        descripcion.setStyleSheet(f"color: {t['TEXT_DIM']}; font-style: italic;")
        layout.addWidget(descripcion)

        # Lista de archivos
        grupo_archivos = QGroupBox("Archivos a unir")
        v_archivos = QVBoxLayout(grupo_archivos)

        self.lista_archivos = ListaArchivosDrop()
        self.lista_archivos.archivos_arrastrados.connect(self._agregar_archivos)
        self.lista_archivos.setMinimumHeight(150)
        v_archivos.addWidget(self.lista_archivos)

        fila_botones_lista = QHBoxLayout()
        btn_agregar = QPushButton("➕  Agregar archivos")
        btn_agregar.clicked.connect(self._dialog_agregar)
        fila_botones_lista.addWidget(btn_agregar)

        btn_quitar = QPushButton("➖  Quitar seleccionados")
        btn_quitar.clicked.connect(self._quitar_seleccionados)
        fila_botones_lista.addWidget(btn_quitar)

        btn_limpiar = QPushButton("🗑  Limpiar lista")
        btn_limpiar.clicked.connect(self._limpiar_lista)
        fila_botones_lista.addWidget(btn_limpiar)
        fila_botones_lista.addStretch()
        v_archivos.addLayout(fila_botones_lista)
        layout.addWidget(grupo_archivos)

        # Resumen
        self.lbl_resumen = QLabel()
        self.lbl_resumen.setWordWrap(True)
        self.lbl_resumen.setStyleSheet(
            f"QLabel {{ background-color: {t['BG_CARD']}; "
            f"color: {t['TEXT']}; padding: 10px; border-radius: 4px; "
            f"border: 1px solid {t['BORDER']}; }}"
        )
        layout.addWidget(self.lbl_resumen)

        # Estrategia para IDs duplicados
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

        # Formato y destino
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
        btn_examinar = QPushButton("📁  Examinar...")
        btn_examinar.clicked.connect(self._elegir_destino)
        fila_destino.addWidget(btn_examinar)
        v_salida.addLayout(fila_destino)
        layout.addWidget(grupo_salida)

        # Botones de acción
        fila_accion = QHBoxLayout()
        fila_accion.addStretch()
        btn_cancelar = QPushButton("Cancelar")
        btn_cancelar.clicked.connect(self.reject)
        fila_accion.addWidget(btn_cancelar)

        self.btn_unir = QPushButton("✓  Unir archivos")
        self.btn_unir.setDefault(True)
        self.btn_unir.setStyleSheet(
            f"QPushButton {{ background-color: {t['GREEN']}; color: white; "
            f"font-weight: 700; padding: 8px 18px; border-radius: 6px; "
            f"border: none; }}"
            f"QPushButton:hover {{ background-color: #2ecc71; }}"
            f"QPushButton:disabled {{ background-color: {t['BG_BAR']}; "
            f"color: {t['TEXT_DIM']}; }}"
        )
        self.btn_unir.clicked.connect(self._ejecutar_merge)
        fila_accion.addWidget(self.btn_unir)
        layout.addLayout(fila_accion)

    # ── Gestión de archivos ────────────────────────────────────────────────

    def _dialog_agregar(self):
        carpeta_inicial = (os.path.dirname(self._archivos[-1].ruta)
                           if self._archivos else "")
        rutas, _ = QFileDialog.getOpenFileNames(
            self, "Seleccionar archivos .rwl", carpeta_inicial,
            "Tucson (*.rwl *.RWL *.txt *.TXT *.crn *.CRN);;Todos los archivos (*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if rutas:
            self._agregar_archivos(rutas)

    def _agregar_archivos(self, rutas: list):
        rutas_existentes = {a.ruta for a in self._archivos}
        agregados = 0
        for ruta in rutas:
            if not os.path.isfile(ruta) or ruta in rutas_existentes:
                continue
            self._archivos.append(_InfoArchivo(ruta))
            agregados += 1
        if agregados == 0:
            return
        self._archivos.sort(key=lambda a: _clave_orden_natural(a.nombre))
        self.lista_archivos.clear()
        for info in self._archivos:
            self._agregar_item_lista(info)
        self._actualizar_resumen()

    def _agregar_item_lista(self, info: _InfoArchivo):
        t = _tema_mgr.t
        item = QListWidgetItem(info.resumen())
        if info.error or not info.series:
            item.setForeground(QBrush(QColor(t['RED'])))
        elif info.anio_min is None:
            item.setForeground(QBrush(QColor(t['ORANGE'])))
        item.setData(Qt.ItemDataRole.UserRole, info.ruta)
        item.setToolTip(info.ruta)
        self.lista_archivos.addItem(item)

    def _quitar_seleccionados(self):
        items_sel = self.lista_archivos.selectedItems()
        if not items_sel:
            return
        rutas_quitar = {it.data(Qt.ItemDataRole.UserRole) for it in items_sel}
        self._archivos = [a for a in self._archivos if a.ruta not in rutas_quitar]
        self.lista_archivos.clear()
        for info in self._archivos:
            self._agregar_item_lista(info)
        self._actualizar_resumen()

    def _limpiar_lista(self):
        if not self._archivos:
            return
        if QMessageBox.question(
            self, "Confirmar", "¿Quitar todos los archivos de la lista?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._archivos.clear()
        self.lista_archivos.clear()
        self._actualizar_resumen()

    def _actualizar_resumen(self):
        t = _tema_mgr.t
        validos = [a for a in self._archivos if a.es_valido()]

        if not validos:
            self.lbl_resumen.setText(
                "📊 <b>Resumen:</b> ningún archivo válido cargado todavía."
            )
            self.btn_unir.setEnabled(False)
            return

        n_total_series = sum(len(a.series) for a in validos)
        anios_min = [a.anio_min for a in validos if a.anio_min is not None]
        anios_max = [a.anio_max for a in validos if a.anio_max is not None]
        anio_min_g = min(anios_min) if anios_min else None
        anio_max_g = max(anios_max) if anios_max else None

        contador_id: dict[str, int] = {}
        for a in validos:
            for sid in a.series.keys():
                contador_id[sid] = contador_id.get(sid, 0) + 1
        ids_dup = [sid for sid, n in contador_id.items() if n > 1]

        l1 = (f"📊 <b>{n_total_series} series totales</b> en "
              f"<b>{len(validos)} archivo{'s' if len(validos) != 1 else ''}</b>")
        if anio_min_g is not None:
            n_anios = anio_max_g - anio_min_g + 1
            l2 = f"<br>📅 Rango de años: <b>{anio_min_g}–{anio_max_g}</b> ({n_anios} años)"
        else:
            l2 = ""

        if ids_dup:
            preview = ", ".join(ids_dup[:5])
            extras = f" y {len(ids_dup) - 5} más" if len(ids_dup) > 5 else ""
            l3 = (f"<br>⚠ <b>{len(ids_dup)} ID{'s' if len(ids_dup) != 1 else ''} "
                  f"duplicado{'s' if len(ids_dup) != 1 else ''}</b> entre archivos: "
                  f"<i>{preview}{extras}</i>")
            l3 += f"<br><span style='color:{t['TEXT_DIM']}'>Se aplicará la estrategia seleccionada abajo.</span>"
        else:
            l3 = "<br>✓ Sin conflictos de IDs duplicados."

        self.lbl_resumen.setText(l1 + l2 + l3)
        self.btn_unir.setEnabled(True)

    def _actualizar_extension_destino(self):
        texto = self.input_destino.text().strip()
        if not texto:
            return
        base, _ = os.path.splitext(texto)
        idx = self.combo_formato.currentIndex()
        if idx == 0:
            self.input_destino.setText(base + ".rwl")
        elif idx == 1:
            self.input_destino.setText(base + ".xlsx")
        else:
            self.input_destino.setText(base)

    def _elegir_destino(self):
        idx_f = self.combo_formato.currentIndex()
        if idx_f == 0:
            filtro = "Tucson (*.rwl);;Todos los archivos (*)"
            ext_default = ".rwl"
        elif idx_f == 1:
            filtro = "Excel (*.xlsx);;Todos los archivos (*)"
            ext_default = ".xlsx"
        else:
            filtro = "Todos los archivos (*)"
            ext_default = ""

        carpeta_inicial = (os.path.dirname(self._archivos[0].ruta)
                           if self._archivos else "")
        nombre_sugerido = "unido" + ext_default

        ruta, _ = QFileDialog.getSaveFileName(
            self, "Archivo de salida",
            os.path.join(carpeta_inicial, nombre_sugerido),
            filtro, options=QFileDialog.Option.DontUseNativeDialog,
        )
        if ruta:
            if ext_default and not ruta.lower().endswith(ext_default):
                ruta += ext_default
            self.input_destino.setText(ruta)

    def _ejecutar_merge(self):
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
        if idx_formato == 0:
            rutas_escribir = [(ruta_destino, "tucson")]
        elif idx_formato == 1:
            rutas_escribir = [(ruta_destino, "excel")]
        else:
            base, _ = os.path.splitext(ruta_destino)
            rutas_escribir = [(base + ".rwl", "tucson"), (base + ".xlsx", "excel")]

        for ruta, _ in rutas_escribir:
            if os.path.exists(ruta):
                if QMessageBox.question(
                    self, "Sobrescribir",
                    f"El archivo ya existe:\n{ruta}\n\n¿Sobrescribirlo?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                ) != QMessageBox.StandardButton.Yes:
                    return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            series_finales = self._combinar_series(validos)
            archivos_escritos = []
            for ruta, tipo in rutas_escribir:
                if tipo == "tucson":
                    escribir_tucson_merge(series_finales, ruta)
                else:
                    escribir_excel_merge(series_finales, ruta)
                archivos_escritos.append(ruta)
            QApplication.restoreOverrideCursor()

            n = len(series_finales)
            if len(archivos_escritos) == 1:
                msg = (f"✓ Se unieron <b>{n}</b> series en:<br>"
                       f"<i>{archivos_escritos[0]}</i>")
            else:
                lista_html = "<br>".join(f"<i>{r}</i>" for r in archivos_escritos)
                msg = (f"✓ Se unieron <b>{n}</b> series en <b>{len(archivos_escritos)} archivos</b>:"
                       f"<br>{lista_html}")
            QMessageBox.information(self, "Archivos unidos", msg)
            self.accept()

        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Error al unir archivos",
                                 f"Ocurrió un error:\n\n{e}")

    def _combinar_series(self, archivos: list) -> dict:
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
                if estrategia == "primero":
                    continue
                elif estrategia == "renombrar":
                    n = 2
                    while f"{sid}_{n}"[:TUCSON_SAMPLE_ID_WIDTH] in resultado:
                        n += 1
                    nuevo_sid = f"{sid}_{n}"[:TUCSON_SAMPLE_ID_WIDTH]
                    resultado[nuevo_sid] = df.copy()
                else:  # combinar
                    df_existente = resultado[sid]
                    df_b = df.set_index("Anio") if "Anio" in df.columns else df
                    df_a = (df_existente.set_index("Anio")
                            if "Anio" in df_existente.columns else df_existente)
                    combinado = pd.concat([df_a, df_b])
                    combinado = combinado.groupby(combinado.index).mean()
                    combinado.index.name = "Anio"
                    resultado[sid] = combinado

        return resultado
