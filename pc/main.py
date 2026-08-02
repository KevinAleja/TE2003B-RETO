"""
Frontend principal: captura video, detecta gestos, muestra la UI
y envía comandos a la Rasp inicializando un socket TCP
"""

import argparse
import json
import sys
import yaml
from pathlib import Path

import cv2

sys.path.append(str(Path(__file__).parent.parent))
from rasp.protocolo import Modo, Gestos

from pc.devices import HandDetector, SocketClient
from pc.backend import InterfazBrazo

# Abrimos el YAML
with open(r"C:\Users\kevin\Documents\ITESM\Diseno_de_Sistemas_en_Chip\RETO_FINAL\params.yaml", "r") as file:
    config = yaml.safe_load(file)

DEFAULT_HOST = config["socket"]["host"]
DEFAULT_PORT = config["socket"]["port"]


class AppController:
    def __init__(self, args):
        self._client = SocketClient(
            host    = args.host,
            port    = args.port,
            enabled = not args.no_socket,
        )

        self._cap = cv2.VideoCapture(1)
        if self._cap.isOpened():
            print("Webcam secundaria detectada (índice 1).")
        else:
            self._cap = cv2.VideoCapture(0)
            if not self._cap.isOpened():
                raise RuntimeError("No se pudo abrir la webcam.")

        initial_mode   = Modo.MIRROR if args.mirror else Modo.GAME
        self._detector = HandDetector(mode=initial_mode)

        self._gui = InterfazBrazo(
            on_mode_game   = self._set_game,
            on_mode_mirror = self._set_mirror,
            on_close       = self._cleanup,
        )
        self._gui.set_mode("JUEGO" if initial_mode == Modo.GAME else "TRACKING")
        self._gui.set_connection(self._client._sock is not None)

        # Flags para recibir respuesta de la Pi en tick separado
        self._waiting_response = False
        self._last_jugador     = "?"
        self._response_ticks   = 0

    def _set_game(self):
        self._detector.set_mode(Modo.GAME)
        self._gui.set_mode("JUEGO")
        self._gui.set_status("---")
        self._gui.set_angles([0] * 5)
        print("→ Modo JUEGO")

    def _set_mirror(self):
        self._detector.set_mode(Modo.MIRROR)
        self._gui.set_mode("TRACKING")
        self._gui.set_status("---")
        print("→ Modo TRACKING")

    def _tick(self):
        ret, frame = self._cap.read()
        if not ret:
            self._gui.after(30, self._tick)
            return

        frame = cv2.flip(frame, 1)
        annotated, payload = self._detector.process_frame(frame)

        self._gui.update_frame(annotated)

        # Gesto actual en la barra lateral
        gesture = self._detector._current_gesture
        if gesture != Gestos.UNKNOWN:
            self._gui.set_status(gesture.value)

        # Enviar payload
        if payload is not None:
            sent = self._client.send(payload)
            self._gui.set_connection(sent)

            msg = json.loads(payload.decode("utf-8"))
            tag = "[→ Pi]" if sent else "[local]"
            print(f"{tag} {msg}")

            # Ángulos en modo tracking
            if "angulos" in msg:
                self._gui.set_angles(msg["angulos"])

            # En modo juego, activar flag para leer respuesta en tick siguiente
            if msg.get("modo") == "ppt" and sent:
                self._waiting_response = True
                self._last_jugador     = gesture.value if gesture != Gestos.UNKNOWN else "?"

        # Leer respuesta pendiente de la Pi (reintenta hasta 30 ticks ~900ms)
        if self._waiting_response:
            self._response_ticks += 1
            resp = self._client.receive()
            if resp:
                robot     = resp.get("robot", "?")
                resultado = resp.get("resultado", "?")
                self._gui.set_resultado(self._last_jugador, robot, resultado)
                print(f"[← Pi] Robot: {robot} | {resultado}")
                self._waiting_response = False
                self._response_ticks   = 0
            elif self._response_ticks > 30:
                print("[Warn] No llegó respuesta de la Pi")
                self._waiting_response = False
                self._response_ticks   = 0

        self._gui.after(30, self._tick)

    def run(self):
        self._gui.after(30, self._tick)
        self._gui.mainloop()

    def _cleanup(self):
        print("Cerrando...")
        self._cap.release()
        self._detector.release()
        self._client.close()


def main():
    parser = argparse.ArgumentParser(description="Super Bracito Robótico")
    parser.add_argument("--host",      default=DEFAULT_HOST)
    parser.add_argument("--port",      default=DEFAULT_PORT, type=int)
    parser.add_argument("--no-socket", action="store_true")
    parser.add_argument("--mirror",    action="store_true")
    args = parser.parse_args()

    AppController(args).run()


if __name__ == "__main__":
    main()