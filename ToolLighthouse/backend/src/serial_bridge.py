"""
ToolLighthouse - Serial Bridge Controller
Gestiona la comunicacion UART con el Arduino Nano para el control de
iluminacion LED por zonas en la maqueta 3D.

Protocolo (2 bytes ASCII por mensaje):
  "+X" -> herramienta X aparece    (el LED X se enciende y se mantiene)
  "-X" -> herramienta X desaparece (el Arduino apaga el LED X 15 s despues)
  "0"  -> apaga todo de inmediato
X = 1..9  (clase YOLO 0..8 + 1; LED 1 = D2 ... LED 9 = D10)
"""

import time
import logging
from typing import Optional, Set

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("SerialController")


class SerialController:
    """
    Controlador UART con:
    - Autoconexion con retardo de seguridad DTR (2 s).
    - Fallback automatico a modo MOCK si no se detecta el hardware.
    - Eventos de aparicion (+X) y desaparicion (-X) filtrados por cambio de estado.
    """

    NUM_ZONES = 9

    def __init__(
        self,
        port: str = "COM3",
        baudrate: int = 115200,
        timeout: float = 1.0,
        force_mock: bool = False,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.mock_mode = force_mock
        self.serial_conn = None
        self.active_classes: Set[int] = set()       # clases actualmente encendidas
        self.last_sent_byte: Optional[bytes] = None  # ultimo mensaje enviado (para el HUD/logs)
        self.connect()

    # ------------------------------------------------------------------ conexion
    def connect(self) -> bool:
        """Abre el puerto serie o entra en MODO MOCK. True = hardware real."""
        if self.mock_mode:
            logger.info("[MODO MOCK FORZADO] Simulador serie activo.")
            return False

        try:
            import serial  # importacion tardia: no falla si pyserial falta hasta aqui
            logger.info(f"Intentando conexion serie en {self.port} a {self.baudrate} baudios...")
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
            )
            logger.info("Conexion establecida. Esperando 2.0s por reinicio DTR del microcontrolador...")
            time.sleep(2.0)
            logger.info(f"Comunicacion UART lista y sincronizada en {self.port}.")
            self.mock_mode = False
            return True

        except Exception as exc:
            logger.warning(
                f"[ADVERTENCIA] No fue posible conectar al puerto serie '{self.port}': {exc}. "
                "Activando automaticamente MODO MOCK (simulacion en terminal)."
            )
            self.serial_conn = None
            self.mock_mode = True
            return False

    # ------------------------------------------------------------------ eventos
    @classmethod
    def _zone(cls, class_id: Optional[int]) -> Optional[int]:
        """Clase YOLO 0..8 -> zona 1..9. None si el id no es valido."""
        if class_id is None:
            return None
        cid = int(class_id)
        if not (0 <= cid < cls.NUM_ZONES):
            return None
        return cid + 1

    def send_appearance_event(self, class_id: Optional[int]) -> bool:
        """Envia '+X'. Devuelve True solo si hubo cambio de estado."""
        zone = self._zone(class_id)
        if zone is None or class_id in self.active_classes:
            return False
        self._transmit(f"+{zone}".encode("ascii"))
        self.active_classes.add(class_id)
        return True

    def send_disappearance_event(self, class_id: Optional[int]) -> bool:
        """Envia '-X'. Devuelve True solo si hubo cambio de estado."""
        zone = self._zone(class_id)
        if zone is None or class_id not in self.active_classes:
            return False
        self._transmit(f"-{zone}".encode("ascii"))
        self.active_classes.discard(class_id)
        return True

    def send_class_event(self, class_id: Optional[int]) -> bool:
        """Compatibilidad: None/-1 -> desaparece lo activo; id -> aparece."""
        if class_id is None or class_id == -1:
            changed = False
            for cid in list(self.active_classes):
                changed |= self.send_disappearance_event(cid)
            return changed
        return self.send_appearance_event(class_id)

    def reset_state(self) -> None:
        """Apaga todos los LED de inmediato enviando '0'."""
        self._transmit(b"0")
        self.active_classes.clear()

    # ------------------------------------------------------------------ bajo nivel
    def _transmit(self, data: bytes) -> None:
        """Despacha el mensaje al microcontrolador o lo simula en terminal."""
        self.last_sent_byte = data
        text = data.decode("ascii", errors="replace")

        if self.mock_mode or self.serial_conn is None:
            print(f"[SERIAL MOCK] Enviando: {text}")
            return

        try:
            self.serial_conn.write(data)
            self.serial_conn.flush()
            logger.info(f"[SERIAL UART] Enviado: {text} a {self.port}")
        except Exception as exc:
            logger.error(f"[ERROR UART] Fallo al escribir en {self.port}: {exc}. Conmutando a MODO MOCK.")
            self.mock_mode = True
            print(f"[SERIAL MOCK] Enviando: {text}")

    def close(self) -> None:
        """Apaga los LED y cierra el canal de comunicacion."""
        if self.serial_conn is not None:
            try:
                if self.serial_conn.is_open:
                    self.reset_state()
                    self.serial_conn.close()
                    logger.info(f"Puerto serie {self.port} cerrado correctamente.")
            except Exception as exc:
                logger.debug(f"Error cerrando puerto serie: {exc}")
            finally:
                self.serial_conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


if __name__ == "__main__":
    print("=== Probando SerialController ===")
    controller = SerialController(port="COM3", baudrate=115200, force_mock=True)

    controller.send_appearance_event(3)      # +4 (cautin)
    controller.send_appearance_event(3)      # ignorado (sin cambio)
    controller.send_appearance_event(8)      # +9 (ventilador_pasco)
    controller.send_disappearance_event(3)   # -4
    controller.send_disappearance_event(8)   # -9
    controller.reset_state()                 # 0

    controller.close()
    print("=== Prueba finalizada ===")
