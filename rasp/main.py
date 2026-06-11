import argparse
import json
import random
import socket
import sys
import yaml
from enum import Enum
from pathlib import Path

from protocolo import Gestos, Modo, decode

try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False



with open("params.yaml", "r") as f:
    config = yaml.safe_load(f)

DEFAULT_PORT        = config["socket"]["port"]
DEFAULT_SERIAL_PORT = config["serial"]["port"]
DEFAULT_BAUD        = config["serial"]["baudrate"]


GESTURE_TO_CHAR = {
    Gestos.ROCK:     b'a',
    Gestos.PAPER:    b'b',
    Gestos.SCISSORS: b'c',
}

MIRROR_CMD = 0x04



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
    robot = random.choice(_ALL_GESTURES)
    if player == robot:
        result = GameResult.TIE
    elif _WINS_AGAINST[player] == robot:
        result = GameResult.WIN
    else:
        result = GameResult.LOSE
    return robot, result


class SerialController:
    def __init__(self, port: str, baud: int, enabled: bool = True):
        self.enabled = enabled and SERIAL_AVAILABLE
        self._ser    = None

        if self.enabled:
            try:
                self._ser = serial.Serial(port, baud, timeout=1)
                print(f"[Serial] Conectado a {port} @ {baud} baud")
            except serial.SerialException as e:
                print(f"[Serial] Error: {e}. Corriendo sin Arduino.")
                self._ser    = None
                self.enabled = False

    def send_game_cmd(self, gesture: Gestos) -> None:
        char = GESTURE_TO_CHAR.get(gesture, b'b')
        self._write(char)
        print(f"[Serial → Arduino] '{char.decode()}' ({gesture.value})")

    def send_mirror_cmd(self, angles: list[int]) -> None:
        clamped = [max(0, min(180, int(a))) for a in angles[:5]]
        payload = bytes([MIRROR_CMD] + clamped)
        self._write(payload)

    def _write(self, data: bytes) -> None:
        if self._ser and self._ser.is_open:
            self._ser.write(data)
        else:
            print(f"[Serial SIMULADO] {list(data)}")

    def close(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()


def handle_message(data: bytes, serial_ctrl: SerialController, conn: socket.socket) -> None:
    try:
        msg = decode(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"[Error] Mensaje inválido: {e}")
        return

    mode = msg.get("modo")

    if mode == Modo.GAME.value:
        gesture_str = msg.get("jugada", "desconocido")
        try:
            player = Gestos(gesture_str)
        except ValueError:
            print(f"[Error] Gesto desconocido: {gesture_str}")
            return

        if player == Gestos.UNKNOWN:
            return

        # Generar jugada del robot y resultado
        robot, result = play_round(player)

        print(f"[JUEGO] Jugador: {player.value:8s} | "
              f"Robot: {robot.value:8s} | "
              f"Resultado: {result.value}")

        # Arduino forma la jugada del robot con los servos
        serial_ctrl.send_game_cmd(robot)

        # Responder a la PC con la jugada del robot y el resultado
        resp = {
            "robot":     robot.value,
            "resultado": result.value,
        }
        try:
            data = json.dumps(resp).encode("utf-8")
            print(f"[→ PC] Intentando enviar: {data}")  
            conn.sendall(data)
            print(f"[→ PC] Enviado OK")
        except Exception as e:
            print(f"[Error] Tipo: {type(e).__name__} | {e}")

    elif mode == Modo.MIRROR.value:
        angles = msg.get("angulos", [90] * 5)
        serial_ctrl.send_mirror_cmd(angles)

    else:
        print(f"[Warn] Modo desconocido: {mode}")



def run_server(host: str, port: int, serial_ctrl: SerialController) -> None:
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


def _handle_connection(conn, addr, serial_ctrl):
    try:
        while True:
            data = conn.recv(1024)
            if not data:
                print(f"[Server] PC desconectada: {addr}")
                break
            handle_message(data, serial_ctrl, conn)
    except (ConnectionResetError, OSError) as e:
        print(f"[Server] Conexión cerrada: {e}")
    finally:
        conn.close()


# ── Main ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Super Bracito Robótico — Raspberry Pi")
    parser.add_argument("--host",      default="0.0.0.0")
    parser.add_argument("--port",      default=DEFAULT_PORT,        type=int)
    parser.add_argument("--serial",    default=DEFAULT_SERIAL_PORT)
    parser.add_argument("--baud",      default=DEFAULT_BAUD,        type=int)
    parser.add_argument("--no-serial", action="store_true")
    args = parser.parse_args()

    serial_ctrl = SerialController(
        port    = args.serial,
        baud    = args.baud,
        enabled = not args.no_serial,
    )
    run_server(args.host, args.port, serial_ctrl)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Server] Apagando...")
        sys.exit(0)
    except Exception as e:
        print(f"[Error fatal] {e}")
        sys.exit(1)