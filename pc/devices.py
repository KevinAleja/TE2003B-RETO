"""
Devices.py contiene toda la logica para la detección de la mano, 
clasificación de gestos, cálculo de ángulos y comunicación socket
 con la Raspberry Pi. Es el núcleo del procesamiento de visión
 computacional y la interfaz con el hardware.

Uso para pruebas sin Rasp:
    python devices.py --no-socket

Uso normal (con Rasp conectada):
    python devices.py --host 192.168.1.XXX --port 5000
"""

# Librerias 
import argparse
import json
import socket
import time
import urllib.request
from enum import Enum
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

import sys
# Agregar la carpeta pc al path
sys.path.append(str(Path(__file__).parent.parent)) 
from rasp.protocolo import Gestos, Modo, encode_game, encode_mirror



# Valores de los landmarks para las puntas y bases de los dedos
FINGER_TIPS = [4, 8, 12, 16, 20]
FINGER_MIDS = [3, 6, 10, 14, 18]


# Modelo de MediaPipe: Descarga y uso del modelo 
def _get_model_path() -> str:
    model_path = Path("hand_landmarker.task")
    if not model_path.exists():
        # Ruta al modelo de MediaPipe
        url = (
            "https://storage.googleapis.com/mediapipe-models/"
            "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
        )
        print(f"Descargando modelo MediaPipe -> {model_path} ...")
        urllib.request.urlretrieve(url, model_path)
        print("Modelo descargado.")
    return str(model_path)


# Según la documentación de MediaPipe, el modelo de HandLandmarker devuelve 21 landmarks por mano.
# aqui definimos las conexiones entre ellos para dibujar el esqueleto de la mano en la imagen.
_HAND_CONNECTIONS = [
    (0,1),(1,2),(2,3),(3,4),
    (0,5),(5,6),(6,7),(7,8),
    (0,9),(9,10),(10,11),(11,12),
    (0,13),(13,14),(14,15),(15,16),
    (0,17),(17,18),(18,19),(19,20),
    (5,9),(9,13),(13,17),
]

def _draw_landmarks(frame: np.ndarray, landmarks: list) -> None:
    h, w = frame.shape[:2]
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
    for a, b in _HAND_CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], (0, 200, 255), 2, cv2.LINE_AA)
    for i, (x, y) in enumerate(pts):
        # Colores de la mano y de las puntas 
        color = (255, 255, 255) if i in (4, 8, 12, 16, 20) else (0, 180, 0)
        cv2.circle(frame, (x, y), 5, color, -1, cv2.LINE_AA)
        cv2.circle(frame, (x, y), 5, (0, 0, 0), 1, cv2.LINE_AA)


# ─────────────────────────────────────────────
# Detección de gestos
# ─────────────────────────────────────────────

def _fingers_extended(landmarks) -> list[bool]:
    extended = []
    thumb_tip  = landmarks[FINGER_TIPS[0]]
    thumb_base = landmarks[FINGER_MIDS[0]]
    extended.append(thumb_tip.x < thumb_base.x)   # Pulgar (mano derecha)
    for tip_idx, mid_idx in zip(FINGER_TIPS[1:], FINGER_MIDS[1:]):
        extended.append(landmarks[tip_idx].y < landmarks[mid_idx].y)
    return extended


def classify_gesture(landmarks) -> Gestos:
    ext = _fingers_extended(landmarks)
    index, middle, ring, pinky = ext[1], ext[2], ext[3], ext[4]

    if index and middle and not ring and not pinky:
        return Gestos.SCISSORS
    if index and middle and ring and pinky:
        return Gestos.PAPER
    if not index and not middle and not ring and not pinky:
        return Gestos.ROCK
    return Gestos.UNKNOWN


# ─────────────────────────────────────────────
# Cálculo de ángulos para modo mirror
# ─────────────────────────────────────────────

def _distance(a, b) -> float:
    return np.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2)


def _wrist_angle(lm) -> int:
    dx = lm[9].x - lm[0].x
    dy = lm[9].y - lm[0].y
    angle_deg = np.degrees(np.arctan2(-dy, dx))
    return int(np.clip(np.interp(angle_deg, [-90, 90], [0, 180]), 0, 180))


def compute_angles(landmarks) -> list[int]:
    """Devuelve [wrist, thumb, index, middle, ring, pinky] en grados 0-180."""
    lm        = landmarks
    palm_size = _distance(lm[0], lm[9])
    if palm_size < 1e-6:
        return [90] * 6

    def finger_angle(tip_idx, base_idx) -> int:
        ratio = np.clip(_distance(lm[tip_idx], lm[base_idx]) / palm_size, 0.0, 1.5)
        return int(np.interp(ratio, [0.3, 1.4], [0, 180]))

    return [
        _wrist_angle(lm),
        finger_angle(4,  1),
        finger_angle(8,  5),
        finger_angle(12, 9),
        finger_angle(16, 13),
        finger_angle(20, 17),
    ]

class SocketClient:
    """
    Wrapper sobre socket TCP con reconexión automática.
    Si no hay Pi disponible (modo --no-socket), todas las operaciones
    son no-op y el programa corre igual.
    """

    def __init__(self, host: str, port: int, enabled: bool = True):
        self.host    = host
        self.port    = port
        self.enabled = enabled
        self._sock: socket.socket | None = None
        if self.enabled:
            self._connect()

    def _connect(self) -> None:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.settimeout(3)
            self._sock.connect((self.host, self.port))
            print(f"[Socket] Conectado a {self.host}:{self.port}")
        except (ConnectionRefusedError, OSError) as e:
            print(f"[Socket] No se pudo conectar: {e}. Corriendo sin Pi.")
            self._sock = None

    def send(self, data: bytes) -> bool:
        if not self.enabled or self._sock is None:
            return False
        try:
            self._sock.sendall(data)
            return True
        except (BrokenPipeError, OSError):
            print("[Socket] Conexión perdida. Reintentando...")
            self._sock = None
            self._connect()
            return False

    def close(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None


# ─────────────────────────────────────────────
# Clase principal: HandDetector
# ─────────────────────────────────────────────

class HandDetector:
    def __init__(
        self,
        mode: Modo = Modo.GAME,
        min_detection_confidence: float = 0.75,
        min_tracking_confidence:  float = 0.75,
        game_hold_seconds:        float = 1.5,
    ):
        self.mode              = mode
        self.game_hold_seconds = game_hold_seconds

        options = mp_vision.HandLandmarkerOptions(
            base_options = mp_python.BaseOptions(model_asset_path=_get_model_path()),
            running_mode = mp_vision.RunningMode.VIDEO,
            num_hands    = 1,
            min_hand_detection_confidence = min_detection_confidence,
            min_hand_presence_confidence  = min_detection_confidence,
            min_tracking_confidence       = min_tracking_confidence,
        )
        self._landmarker    = mp_vision.HandLandmarker.create_from_options(options)
        self._frame_ts_ms   = 0

        # Estado interno 
        self._current_gesture = Gestos.UNKNOWN
        self._gesture_start   = 0.0
        self._last_sent_at    = 0.0   # evita spam de envíos

    def process_frame(self, frame: np.ndarray) -> tuple[np.ndarray, bytes | None]:
        """
        Procesa un frame.
        Devuelve (frame_anotado, payload_bytes | None)
        payload_bytes es lo que hay que enviar por socket a la Pi.
        """
        self._frame_ts_ms += 33
        rgb       = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image  = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        detection = self._landmarker.detect_for_video(mp_image, self._frame_ts_ms)
        payload   = None

        if detection.hand_landmarks:
            lm = detection.hand_landmarks[0]
            _draw_landmarks(frame, lm)

            if self.mode == Modo.GAME:
                payload = self._process_game(lm, frame)
            else:
                angles  = compute_angles(lm)
                payload = encode_mirror(angles)
                self._draw_mirror_overlay(frame, angles)
        else:
            self._current_gesture = Gestos.UNKNOWN
            self._gesture_start   = 0.0

        self._draw_ui(frame)
        return frame, payload

    def set_mode(self, modo):
        self.mode             = modo
        self._current_gesture = Gestos.UNKNOWN
        self._gesture_start   = 0.0

    # ── Internos ────────────────────────────────────────────────────────

    def _process_game(self, landmarks, frame: np.ndarray) -> bytes | None:
        gesture = classify_gesture(landmarks)
        now     = time.time()

        if gesture == Gestos.UNKNOWN:
            self._current_gesture = Gestos.UNKNOWN
            self._gesture_start   = 0.0
            self._draw_gesture_label(frame, "---", 0, self.game_hold_seconds)
            return None

        if gesture != self._current_gesture:
            self._current_gesture = gesture
            self._gesture_start   = now

        hold = now - self._gesture_start
        self._draw_gesture_label(frame, gesture.value, hold, self.game_hold_seconds)
        self._draw_hold_bar(frame, hold, self.game_hold_seconds)

        # Enviar solo cuando se cumple el hold y no se envió hace poco
        if hold >= self.game_hold_seconds and (now - self._last_sent_at) > 3.0:
            self._last_sent_at  = now
            self._gesture_start = now
            return encode_game(gesture)

        return None

    # ── Dibujo ──────────────────────────────────────────────────────────

    def _draw_ui(self, frame: np.ndarray):
        h, w = frame.shape[:2]
        label = f"MODO: {'JUEGO' if self.mode == Modo.GAME else 'Tracking'}"
        cv2.rectangle(frame, (0, 0), (w, 40), (30, 30, 30), -1)
        cv2.putText(frame, label, (10, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 180), 2)

    def _draw_gesture_label(self, frame, label, hold, required):
        h = frame.shape[0]
        cv2.putText(frame, str(label), (10, h - 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)

    def _draw_hold_bar(self, frame, elapsed, required):
        h, w = frame.shape[:2]
        bx, by, bh = 10, h - 40, 14
        bw       = w - 20
        progress = min(elapsed / required, 1.0) if required > 0 else 0
        cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (60, 60, 60), -1)
        filled = int(bw * progress)
        if filled > 0:
            color = (0, 255, 100) if progress < 1.0 else (0, 200, 255)
            cv2.rectangle(frame, (bx, by), (bx + filled, by + bh), color, -1)
        cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (150, 150, 150), 1)

    def _draw_mirror_overlay(self, frame, angles):
        names = ["Muneca", "Pulgar", "Indice", "Medio ", "Anular", "Menique"]
        for i, (name, angle) in enumerate(zip(names, angles)):
            cv2.putText(frame, f"{name}: {angle:3d}°", (10, 60 + i * 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 230, 255), 1)

    def release(self):
        self._landmarker.close()
