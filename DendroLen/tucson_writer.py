# tucson_writer.py — DendroLen
# Escritura del formato Tucson (.rwl) y sidecar de eventos (.rwl.anom.json)
# Formato idéntico al de DPI: primera línea parcial si el año no es década,
# terminador -9999 al final de la última línea si cabe.

import os
import json
from typing import List, Dict, Optional
from constantes import TUCSON_TERMINATOR, TUCSON_VALUES_PER_LINE, TUCSON_SAMPLE_ID_WIDTH


def escribir_tucson(codigo: str, anio_inicio: int,
                    mediciones: List[int], ruta: str) -> None:
    """
    Escribe las mediciones en formato Tucson estándar (.rwl).

    Formato estándar ITRDB:
    - ID de serie: 8 caracteres, justificado a la izquierda
    - Primera línea: parcial si el año no es múltiplo de 10
    - Líneas siguientes: 10 valores por línea en años de década
    - Terminador -9999 al final de la última línea si cabe,
      o en línea nueva si la última tiene 10 valores
    - Valor 999 se reemplaza por 998
    """
    if not mediciones:
        return

    serie = codigo[:TUCSON_SAMPLE_ID_WIDTH]
    mods = [998 if v == 999 else v for v in mediciones]
    n = len(mods)
    lineas: list[str] = []
    idx = 0

    # ── Primera fila parcial (si el año no es múltiplo de 10) ─────────────
    resto = anio_inicio % 10
    primeros = (10 - resto) if resto != 0 else 10
    if primeros < 10:
        cantidad = min(primeros, n - idx)
        if cantidad > 0:
            fila = f"{serie:8s}{anio_inicio + idx:4d}"
            for v in mods[idx: idx + cantidad]:
                fila += f"{v:6d}"
            lineas.append(fila)
            idx += cantidad

    # ── Filas completas de 10 valores ─────────────────────────────────────
    while idx < n:
        cantidad = min(10, n - idx)
        fila = f"{serie:8s}{anio_inicio + idx:4d}"
        for v in mods[idx: idx + cantidad]:
            fila += f"{v:6d}"
        lineas.append(fila)
        idx += cantidad

    # ── Marcador de fin de serie (-9999) ──────────────────────────────────
    if n == 0:
        lineas = [f"{serie:8s}{anio_inicio:4d}{TUCSON_TERMINATOR:6d}"]
    else:
        # Contar cuántos valores tiene la última línea
        ultima = lineas[-1]
        n_vals_ultima = (len(ultima) - 12) // 6
        if n_vals_ultima >= 10:
            # Línea llena → terminador en línea nueva
            anio_nueva = anio_inicio + n
            lineas.append(f"{serie:8s}{anio_nueva:4d}{TUCSON_TERMINATOR:6d}")
        else:
            # Cabe en la misma línea
            lineas[-1] += f"{TUCSON_TERMINATOR:6d}"

    with open(ruta, 'w', encoding='utf-8', newline='\r\n') as f:
        for ln in lineas:
            f.write(ln.rstrip() + '\n')


def preview_tucson(codigo: str, anio_inicio: int,
                   mediciones: List[int],
                   eventos: Dict[int, str] = None,
                   max_lines: int = 2000) -> str:
    """
    Genera la cadena de texto de la vista previa Tucson.
    Marca las líneas que contienen años con eventos con un comentario al final.
    Formato idéntico a escribir_tucson.
    """
    eventos = eventos or {}
    serie = codigo[:TUCSON_SAMPLE_ID_WIDTH]
    header = (f"; DendroLen v2.0 · MoiCedrus\n"
              f"; Serie: {codigo}\n"
              f"; Año inicio: {anio_inicio}\n")

    if not mediciones:
        return header + f"{serie:8s}{anio_inicio:4d}   ···\n"

    mods = [998 if m == 999 else m for m in mediciones]
    n = len(mods)
    lineas: list[str] = [header]
    idx = 0

    # Primera fila parcial
    resto = anio_inicio % 10
    primeros = (10 - resto) if resto != 0 else 10
    if primeros < 10:
        cantidad = min(primeros, n - idx)
        if cantidad > 0:
            anio_fila = anio_inicio + idx
            vals_str = "".join(f"{v:6d}" for v in mods[idx: idx + cantidad])
            anos_fila = range(anio_fila, anio_fila + cantidad)
            marcas = [f"{a}:{eventos[a]}" for a in anos_fila if a in eventos]
            sufijo = "  ; " + "  ".join(marcas) if marcas else ""
            lineas.append(f"{serie:8s}{anio_fila:4d}{vals_str}{sufijo}")
            idx += cantidad

    # Filas completas
    while idx < n:
        cantidad = min(10, n - idx)
        anio_fila = anio_inicio + idx
        vals_str = "".join(f"{v:6d}" for v in mods[idx: idx + cantidad])
        anos_fila = range(anio_fila, anio_fila + cantidad)
        marcas = [f"{a}:{eventos[a]}" for a in anos_fila if a in eventos]
        sufijo = "  ; " + "  ".join(marcas) if marcas else ""
        lineas.append(f"{serie:8s}{anio_fila:4d}{vals_str}{sufijo}")
        idx += cantidad

    # Terminador
    if n == 0:
        lineas.append(f"{serie:8s}{anio_inicio:4d}{TUCSON_TERMINATOR:6d}")
    else:
        ultima_pura = lineas[-1].split(";")[0]
        n_vals_ultima = (len(ultima_pura.rstrip()) - 12) // 6
        if n_vals_ultima >= 10:
            anio_nueva = anio_inicio + n
            lineas.append(f"{serie:8s}{anio_nueva:4d}{TUCSON_TERMINATOR:6d}")
        else:
            if ";" in lineas[-1]:
                partes = lineas[-1].split(";", 1)
                lineas[-1] = partes[0].rstrip() + f"{TUCSON_TERMINATOR:6d}" + "  ; " + partes[1]
            else:
                lineas[-1] += f"{TUCSON_TERMINATOR:6d}"

    return '\n'.join(lineas[-min(len(lineas), max_lines):])


def escribir_sidecar(codigo: str, anio_inicio: int, anio_corteza: int,
                     especie: str, eventos: Dict[int, str],
                     ruta_rwl: str) -> str:
    """
    Escribe el sidecar .rwl.anom.json.
    """
    por_tipo: Dict[str, List[int]] = {}
    for anio, codigo_anom in eventos.items():
        por_tipo.setdefault(codigo_anom, []).append(anio)
    for tipo in por_tipo:
        por_tipo[tipo].sort()

    sidecar = {
        codigo: {
            "anio_medula":    anio_inicio,
            "anio_corteza":   anio_corteza,
            "especie":        especie,
            "eventos":        {str(k): v for k, v in sorted(eventos.items())},
            "eventos_por_tipo": {k: v for k, v in sorted(por_tipo.items())}
        }
    }
    ruta_sidecar = ruta_rwl + ".anom.json"
    with open(ruta_sidecar, 'w', encoding='utf-8') as f:
        json.dump(sidecar, f, indent=2, ensure_ascii=False)
    return ruta_sidecar


def leer_sidecar(ruta_sidecar: str) -> Optional[Dict]:
    if not os.path.exists(ruta_sidecar):
        return None
    try:
        with open(ruta_sidecar, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None
