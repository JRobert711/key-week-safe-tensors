"""
ToolLighthouse - Serial Bridge Controller
Gestiona la comunicacion UART con el microcontrolador (Arduino / ESP32) para
el control de iluminacion LED por zonas en la maqueta 3D.
"""

import time
import logging
from typing import Optional

# Configuracion de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("SerialController")


class SerialController:
    """
    Controlador de comunicacion serie UART para ToolLighthouse.
    
    Implementa:
    - Autoconexion con retardo de seguridad DTR (2s).
    - Tolerancia a fallos: fallback automatico a modo MOCK si no se detecta el hardware.
    - Mapeo estricto de IDs de clases (0-8 -> b'1'-b'9') y reposo (b'0').
    - Eventos de APARICION: byte positivo (b'1'..b'9') al confirmar deteccion estable.
    - Eventos de DESAPARICION: byte negativo (b'-1'..b'-9') al confirmar ausencia estable.
    - Filtro de transmision para disparar eventos exclusivamente en cambios de estado.
    """

    # Mapeo oficial de clases ToolLighthouse a bytes UART (aparicion)
    CLASS_BYTE_MAP = {
        None: b'0',  # Reposo / Sin deteccion
        -1: b'0',    # Reposo alternativo
        0: b'1',     # cautin -> Zona 1
        1: b'2',     # multimetro_digital -> Zona 2
        2: b'3',     # multimetro_analogico -> Zona 3
        3: b'4',     # estacion_soldadura -> Zona 4
        4: b'5',     # caja_sensores -> Zona 5
        5: b'6',     # ventilador_pasco -> Zona 6
        6: b'7',     # bascula -> Zona 7
        7: b'8',     # capacitor_variable -> Zona 8
        8: b'9',     # fuente_voltaje -> Zona 9
    }

    # Mapeo de clases a bytes UART para eventos de DESAPARICION (signo negativo)
    CLASS_BYTE_MAP_NEG = {
        0: b'-1',    # cautin desaparecio
        1: b'-2',    # multimetro_digital desaparecio
        2: b'-3',    # multimetro_analogico desaparecio
        3: b'-4',    # estacion_soldadura desaparecio
        4: b'-5',    # caja_sensores desaparecio
        5: b'-6',    # ventilador_pasco desaparecio
        6: b'-7',    # bascula desaparecio
        7: b'-8',    # capacitor_variable desaparecio
        8: b'-9',    # fuente_voltaje desaparecio
    }

    def __init__(
        self,
        port: str = "COM3",
        baudrate: int = 115200,
        timeout: float = 1.0,
        force_mock: bool = False
    ) -> None:
        """
        Inicializa el controlador.

        :param port: Puerto serie (ej. 'COM3', '/dev/ttyUSB0').
        :param baudrate: Velocidad en baudios (por defecto 115200).
        :param timeout: Tiempo de espera en lectura/escritura (segundos).
        :param force_mock: Si es True, fuerza el modo de simulacion sin intentar abrir puerto.
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.mock_mode = force_mock
        self.serial_conn = None
        self.last_sent_byte: Optional[bytes] = None

        # Intentar conexion inicial
        self.connect()

    def connect(self) -> bool:
        """
        Establece la conexion UART o entra en MODO MOCK si el puerto no esta disponible.
        
        :return: True si esta conectado a hardware real, False si operando en MOCK.
        """
        if self.mock_mode:
            logger.info("[MODO MOCK FORZADO] Simulador serie activo.")
            return False

        try:
            import serial  # Importacion tardia para evitar bloqueos si pyserial falta
            logger.info(f"Intentando conexion serie en {self.port} a {self.baudrate} baudios...")
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout
            )
            # Pausa de seguridad indispensable para absorber reinicio por linea DTR en Arduino/ESP32
            logger.info("Conexion establecida. Esperando 2.0s para estabilizar microcontrolador tras reinicio DTR...")
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

    def send_class_event(self, class_id: Optional[int]) -> bool:
        """
        Traduce el ID de clase detectado a su byte de APARICION correspondiente
        y lo transmite unicamente si representa una transicion de estado.

        :param class_id: ID numerico de la clase detectada (0 a 8) o None si reposo.
        :return: True si se transmitio un cambio de estado, False si se omitio por repeticion.
        """
        target_byte = self.CLASS_BYTE_MAP.get(class_id, b'0')

        # Filtro de transmision: ignorar si no hay cambio de estado
        if target_byte == self.last_sent_byte:
            return False

        # Transmitir el nuevo estado
        self._transmit_byte(target_byte)
        self.last_sent_byte = target_byte
        return True

    def send_disappearance_event(self, class_id: Optional[int]) -> bool:
        """
        Envia el byte negativo correspondiente a class_id cuando el objeto
        deja de ser detectado de forma estable (>= 7 de 10 frames sin deteccion).

        Por ejemplo, si class_id=0 (cautin), envia b'-1'.
        Solo transmite si representa una transicion de estado frente al ultimo envio.

        :param class_id: ID numerico de la clase que desaparecio (0 a 8).
        :return: True si se transmitio el evento, False si se omitio por repeticion o class_id invalido.
        """
        if class_id is None:
            return False

        target_byte = self.CLASS_BYTE_MAP_NEG.get(class_id)
        if target_byte is None:
            return False

        # Filtro de transmision: ignorar si no hay cambio de estado
        if target_byte == self.last_sent_byte:
            return False

        # Transmitir el evento de desaparicion
        self._transmit_byte(target_byte)
        self.last_sent_byte = target_byte
        return True

    def _transmit_byte(self, byte_val: bytes) -> None:
        """
        Metodo interno de bajo nivel para despachar el byte al microcontrolador
        o emitir la simulacion en terminal si se esta en MODO MOCK.
        """
        char_repr = byte_val.decode("ascii", errors="replace")

        if self.mock_mode or self.serial_conn is None:
            print(f"[SERIAL MOCK] Enviando byte: {char_repr}")
            return

        try:
            self.serial_conn.write(byte_val)
            self.serial_conn.flush()
            logger.info(f"[SERIAL UART] Enviado byte: {char_repr} a {self.port}")
        except Exception as exc:
            logger.error(f"[ERROR UART] Fallo al escribir en {self.port}: {exc}. Conmutando a MODO MOCK.")
            self.mock_mode = True
            print(f"[SERIAL MOCK] Enviando byte: {char_repr}")

    def reset_state(self) -> None:
        """Fuerza el envio de estado de reposo (b'0')."""
        self.send_class_event(None)

    def close(self) -> None:
        """Cierra de forma limpia el canal de comunicacion."""
        if self.serial_conn is not None:
            try:
                if self.serial_conn.is_open:
                    self.send_class_event(None)  # Apagar LEDs antes de salir
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
    # Test independiente del serial bridge
    print("=== Probando SerialController en modo de prueba ===")
    controller = SerialController(port="COM3", baudrate=115200)

    print("\n--- Simulando transiciones de estado ---")
    # Reposo inicial
    controller.send_class_event(None)
    
    # Repetir reposo (debe ser ignorado por el filtro)
    controller.send_class_event(None)

    # Detectar cautin (Clase 0 -> byte '1')
    controller.send_class_event(0)
    controller.send_class_event(0)  # Ignorado por repeticion

    # Detectar fuente de voltaje (Clase 8 -> byte '9')
    controller.send_class_event(8)

    # Regresar a reposo (byte '0')
    controller.send_class_event(None)

    controller.close()
    print("=== Prueba finalizada con exito ===")
