# serial_worker.py — DendroLen
# Detección de puerto y lectura serial.
# Lógica copiada del código original DendroLen1_06.py que funcionaba correctamente.

import re
import time
import glob

from PyQt6.QtCore import QThread, pyqtSignal

from constantes import DEFAULT_BAUD, PORT_GLOBS, USB_KEYWORDS

RE_FLOAT = re.compile(r"[-+]?[0-9]*\.?[0-9]+")


def parsear_valor_linea(linea: str):
    """Extrae el primer número flotante de la trama. Igual al original."""
    if not linea:
        return None
    m = RE_FLOAT.search(linea)
    if not m:
        return None
    try:
        return float(m.group())
    except Exception:
        return None


def listar_puertos():
    """
    Retorna lista de (device, description) de puertos disponibles.
    Copia exacta de la lógica de detectar_puerto() del original:
    - Usa serial.tools.list_ports
    - Filtra por hwid, description y manufacturer buscando indicios USB
    - Excluye Bluetooth explícitamente
    - Fallback a glob si no hay resultados
    """
    try:
        import serial.tools.list_ports
        ports = list(serial.tools.list_ports.comports())
    except Exception:
        ports = []

    usb_ports = []
    otros     = []

    for p in ports:
        desc = (p.description  or "").lower()
        manu = (p.manufacturer or "").lower()
        hwid = (p.hwid         or "").lower()

        # Excluir Bluetooth explícitamente (igual que el original)
        if "bluetooth" in desc or "bluetooth" in manu or "bth" in hwid:
            continue

        # Buscar indicios de puerto USB/FTDI/CH340/CP210/USB Serial
        # Igual que el original — incluye hwid
        if ("usb" in desc or "usb" in manu or "usb serial" in desc
                or "ftdi" in desc  or "ftdi" in manu
                or "ch340" in desc or "cp210" in desc
                or "prolific" in desc or "silicon labs" in manu
                or "usb" in hwid):
            usb_ports.append((p.device, p.description or p.device))
        else:
            otros.append((p.device, p.description or p.device))

    # Si no hay candidatos USB, usar cualquier no-Bluetooth
    if not usb_ports and otros:
        return otros

    if usb_ports:
        return usb_ports

    # Fallback glob (útil en Linux cuando list_ports no encuentra nada)
    resultado = []
    for pattern in PORT_GLOBS:
        for dev in glob.glob(pattern):
            resultado.append((dev, dev))
    return resultado


def detectar_puerto_auto():
    """Devuelve el primer puerto USB-serial disponible, o None."""
    puertos = listar_puertos()
    return puertos[0][0] if puertos else None


class SerialWorker(QThread):
    """
    Hilo de lectura del puerto serie.
    Lógica idéntica al lector_serial_worker() del original.

    El VRO envía posición acumulada en mm (flotante, ej. "12.450").
    Se convierte a milésimas (× 1000) y se calcula el delta entre lecturas.
    delta > 0 → ancho del anillo en milésimas → se emite por valor_recibido.

    NO reconecta automáticamente. Si se pierde la conexión avisa y para.
    El usuario reconecta con F9.
    """

    valor_recibido   = pyqtSignal(int)     # milésimas de mm
    log_message      = pyqtSignal(str, str)
    conexion_ok      = pyqtSignal(str)
    conexion_perdida = pyqtSignal()

    def __init__(self, port: str, baud: int = DEFAULT_BAUD, parent=None):
        super().__init__(parent)
        self.port   = port
        self.baud   = baud
        self._stop  = False
        self._paused= False

    def stop(self):
        self._stop = True
        self.quit()

    def set_paused(self, paused: bool):
        self._paused = paused

    def run(self):
        import serial

        # ── Abrir puerto (igual que el original) ─────────────────────────────
        try:
            ser = serial.Serial(self.port, self.baud, timeout=0.2)
            time.sleep(0.5)   # igual que el original
            self.log_message.emit("ok",
                f"Puerto abierto: {self.port} @ {self.baud}")
            self.conexion_ok.emit(self.port)
        except Exception as e:
            self.log_message.emit("err",
                f"Error abriendo puerto {self.port}: {e}")
            self.conexion_perdida.emit()
            return

        # ── Estado acumulado ─────────────────────────────────────────────────
        waiting_reference = True
        last_cumulative   = None   # int, milésimas de mm

        # ── Bucle de lectura (igual que lector_serial_worker del original) ───
        try:
            while not self._stop:
                raw = ser.readline()
                if not raw:
                    continue
                try:
                    s = raw.decode(errors="ignore").strip()
                except Exception:
                    s = str(raw)

                val_float = parsear_valor_linea(s)
                if val_float is None:
                    continue

                # Convertir a milésimas de mm (igual que original: mm_val * 1000)
                current_mil = int(round(val_float * 1000))

                # ── Lógica de referencia y delta ─────────────────────────────
                if waiting_reference:
                    # Primera lectura válida → fijar baseline (igual al original)
                    last_cumulative   = current_mil
                    waiting_reference = False
                    self.log_message.emit("ok",
                        f"Referencia fijada: {current_mil/1000:.3f} mm acumulado")
                    continue

                if self._paused:
                    continue

                delta = current_mil - last_cumulative

                if delta > 0:
                    last_cumulative = current_mil
                    self.valor_recibido.emit(delta)

                elif delta < 0:
                    # Reset/retroceso del encoder → resincronizar (igual al original)
                    last_cumulative = current_mil
                    self.log_message.emit("warn",
                        f"Reset detectado — nueva referencia: {current_mil/1000:.3f} mm")
                # delta == 0 → ignorar silenciosamente

        except Exception as e:
            self.log_message.emit("err", f"Error de lectura serial: {e}")
            self.conexion_perdida.emit()
        finally:
            try:
                ser.close()
                self.log_message.emit("info", "Puerto serial cerrado.")
            except Exception:
                pass
