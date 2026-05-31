"""
pi/main.py — Servidor socket + lógica del juego
================================================
Corre ÚNICAMENTE en la Raspberry Pi.

Responsabilidades:
    - Servidor TCP que recibe mensajes de la PC
    - Lógica de piedra / papel / tijera
    - Envío de comandos al ATmega328P vía UART serial

Flujo:
    PC  →(socket TCP)→  main.py  →(UART)→  ATmega328P  →(PWM)→  Servos + Buzzer

Uso:
    python main.py
    python main.py --port 5000 --serial /dev/ttyAMA0 --baud 115200
    python main.py --no-serial   # prueba sin ATmega conectado
"""

import argparse
import json
import random
import socket
import sys
from enum import Enum
from pathlib import Path

# Importar protocolo compartido
from rasp.protocolo import Gestos, Modo, decode

# pyserial — solo disponible en la Pi
try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

"""
==========================================================
DEFINICION DE CONSTANTES Y CONFIGURACION DEL PROTOCOLO
==========================================================
"""

DEFAULT_PORT        = 5000
DEFAULT_SERIAL_PORT = "/dev/ttyAMA0"
DEFAULT_BAUD        = 115200


# ─────────────────────────────────────────────
# Protocolo UART → ATmega328P
# Byte único por comando
# ─────────────────────────────────────────────

class ATmegaCmd:
    ROCK     = 0x01   # Formar PIEDRA   + tono resultado
    PAPER    = 0x02   # Formar PAPEL    + tono resultado
    SCISSORS = 0x03   # Formar TIJERA   + tono resultado
    MIRROR   = 0x04   # Modo mirror: seguido de 6 bytes de ángulos

GESTURE_TO_CMD = {
    Gestos.ROCK:     ATmegaCmd.ROCK,
    Gestos.PAPER:    ATmegaCmd.PAPER,
    Gestos.SCISSORS: ATmegaCmd.SCISSORS,
}


# ─────────────────────────────────────────────
# Lógica del juego
# ─────────────────────────────────────────────

class GameResult(Enum):
    WIN  = "Ganaste"
    LOSE = "Perdiste"
    TIE  = "Empate"


_ALL_GESTURES = [Gestos.ROCK, Gestos.PAPER, Gestos.SCISSORS]

_WINS_AGAINST = {
    Gestos.ROCK:     Gestos.SCISSORS,
    Gestos.SCISSORS: Gestos.PAPER,
    Gestos.PAPER:    Gestos.ROCK,
}


def play_round(player: Gestos) -> tuple[Gestos, GameResult]:
    """Genera la jugada del robot y determina el resultado."""
    robot = random.choice(_ALL_GESTURES)

    if player == robot:
        result = GameResult.TIE
    elif _WINS_AGAINST[player] == robot:
        result = GameResult.WIN
    else:
        result = GameResult.LOSE

    return robot, result


# ─────────────────────────────────────────────
# Controlador serial → ATmega
# ─────────────────────────────────────────────

class SerialController:
    """
    Envía comandos al ATmega328P por UART.
    Si --no-serial, todas las operaciones imprimen en consola.
    """

    def __init__(self, port: str, baud: int, enabled: bool = True):
        self.enabled = enabled and SERIAL_AVAILABLE
        self._ser    = None

        if self.enabled:
            try:
                self._ser = serial.Serial(port, baud, timeout=1)
                print(f"[Serial] Conectado a {port} @ {baud} baud")
            except serial.SerialException as e:
                print(f"[Serial] Error: {e}. Corriendo sin ATmega.")
                self._ser     = None
                self.enabled  = False

    def send_game_cmd(self, robot_gesture: Gestos) -> None:
        """Envía el byte de comando del gesto del robot."""
        cmd = GESTURE_TO_CMD.get(robot_gesture, 0x00)
        self._write(bytes([cmd]))
        print(f"[Serial -> ATmega] CMD: 0x{cmd:02X} ({robot_gesture.value})")

    def send_mirror_cmd(self, angles: list[int]) -> None:
        """Envía 0x04 seguido de 6 bytes de ángulos (0-180)."""
        payload = bytes([ATmegaCmd.MIRROR] + [int(a) for a in angles])
        self._write(payload)

    def _write(self, data: bytes) -> None:
        if self._ser and self._ser.is_open:
            self._ser.write(data)
        else:
            print(f"[Serial SIMULADO] {list(data)}")

    def close(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()


def handle_message(data: bytes, serial_ctrl: SerialController) -> None:
    """
    Procesa un mensaje recibido de la PC y actúa en consecuencia.
    """
    try:
        msg = decode(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"[Error] Mensaje inválido: {e}")
        return

    mode = msg.get("mode")

    if mode == Modo.GAME.value:
        gesture_str = msg.get("gesture", "unknown")
        try:
            player = Gestos(gesture_str)
        except ValueError:
            print(f"[Error] Gesto desconocido: {gesture_str}")
            return

        if player == Gestos.UNKNOWN:
            return

        robot, result = play_round(player)

        print(f"[JUEGO] Jugador: {player.value:8s} | "
              f"Robot: {robot.value:8s} | "
              f"Resultado: {result.value}")

        serial_ctrl.send_game_cmd(robot)

    elif mode == Modo.MIRROR.value:
        angles = msg.get("angUlos", [90] * 6)
        serial_ctrl.send_mirror_cmd(angles)
        # En mirror no imprimimos cada frame para no saturar la consola

    else:
        print(f"[Warn] Modo desconocido: {mode}")


"""
=========================================================
DEFINICION DEL SOCKET TCP Y FLUJO PRINCIPAL DEL SERVIDOR
=========================================================
"""

def run_server(host: str, port: int, serial_ctrl: SerialController) -> None:
    """
    Escucha conexiones entrantes de la PC.
    Solo acepta una conexión a la vez (una PC, una Pi).
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen()
    print(f"[Server] Escuchando en {host}:{port} ...")

    try:
        while True:
            conn, addr = server.accept()
            print(f"[Server] PC conectada desde {addr}")
            _handle_connection(conn, addr, serial_ctrl)
    except KeyboardInterrupt:
        print("\n[Server] Apagando...")
    finally:
        server.close()
        serial_ctrl.close()


def _handle_connection(
    conn: socket.socket,
    addr,
    serial_ctrl: SerialController,
) -> None:
    """Maneja todos los mensajes de una conexión activa."""
    try:
        while True:
            data = conn.recv(1024)
            if not data:
                print(f"[Server] PC desconectada: {addr}")
                break
            handle_message(data, serial_ctrl)
    except (ConnectionResetError, OSError) as e:
        print(f"[Server] Conexión cerrada inesperadamente: {e}")
    finally:
        conn.close()


"""
=========================================================
            INICIO DEL SERVIDOR 
=========================================================
"""

def main():
    parser = argparse.ArgumentParser(description="Super Manita Robótica — Raspberry Pi server")
    parser.add_argument("--host",      default="0.0.0.0",          help="Interface a escuchar")
    parser.add_argument("--port",      default=DEFAULT_PORT,        type=int)
    parser.add_argument("--serial",    default=DEFAULT_SERIAL_PORT, help="Puerto serial del ATmega")
    parser.add_argument("--baud",      default=DEFAULT_BAUD,        type=int)
    parser.add_argument("--no-serial", action="store_true",         help="Correr sin ATmega conectado")
    args = parser.parse_args()

    serial_ctrl = SerialController(
        port    = args.serial,
        baud    = args.baud,
        enabled = not args.no_serial,
    )

    run_server(args.host, args.port, serial_ctrl)


if __name__ == "__main__":
    main()