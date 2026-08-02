"""
Devices.py contiene toda la logica para la detección de la mano, 
clasificación de gestos, cálculo de ángulos y comunicación socket
con la Raspberry Pi. Es el núcleo del procesamiento de visión
computacional y la interfaz con el hardware.
"""

import json
import socket
import time
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
import yaml
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

import sys
sys.path.append(str(Path(__file__).parent.parent))
from rasp.protocolo import Gestos, Modo, encode_game, encode_mirror

with open(r"C:\Users\kevin\Documents\ITESM\Diseno_de_Sistemas_en_Chip\RETO_FINAL\params.yaml", "r") as file:
    config = yaml.safe_load(file)

# Configuración del juego
CONFIDENCE = config["game"]["confidence"]
GAME_HOLD_SECONDS = config["game"]["hold_seconds"]
TRACKING_CONFIDENCE = config["game"]["tracking_confidence"]



FINGER_TIPS = [4, 8, 12, 16, 20]
FINGER_MIDS = [3, 6, 10, 14, 18]


# Carga el modelo de MediaPipe 
def _get_model_path():
    model_path = Path("hand_landmarker.task")
    if not model_path.exists():
        url = (
            "https://storage.googleapis.com/mediapipe-models/"
            "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
        )
        print(f"Descargando modelo MediaPipe -> {model_path} ...")
        urllib.request.urlretrieve(url, model_path)
        print("Modelo descargado.")
    return str(model_path)


_HAND_CONNECTIONS = [
    (0,1),(1,2),(2,3),(3,4),
    (0,5),(5,6),(6,7),(7,8),
    (0,9),(9,10),(10,11),(11,12),
    (0,13),(13,14),(14,15),(15,16),
    (0,17),(17,18),(18,19),(19,20),
    (5,9),(9,13),(13,17),
]

def _draw_landmarks(frame, landmarks):
    h, w = frame.shape[:2]
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
    for a, b in _HAND_CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], (0, 200, 255), 2, cv2.LINE_AA)
    for i, (x, y) in enumerate(pts):
        color = (255, 255, 255) if i in (4, 8, 12, 16, 20) else (0, 180, 0)
        cv2.circle(frame, (x, y), 5, color, -1, cv2.LINE_AA)
        cv2.circle(frame, (x, y), 5, (0, 0, 0), 1, cv2.LINE_AA)


# ── Detección de gestos ───────────────────────────────────────────

def _fingers_extended(landmarks):
    extended = []
    thumb_tip  = landmarks[FINGER_TIPS[0]]
    thumb_base = landmarks[FINGER_MIDS[0]]
    extended.append(thumb_tip.x < thumb_base.x)
    for tip_idx, mid_idx in zip(FINGER_TIPS[1:], FINGER_MIDS[1:]):
        extended.append(landmarks[tip_idx].y < landmarks[mid_idx].y)
    return extended


def classify_gesture(landmarks):
    ext = _fingers_extended(landmarks)
    index, middle, ring, pinky = ext[1], ext[2], ext[3], ext[4]
    if index and middle and not ring and not pinky:
        return Gestos.SCISSORS
    if index and middle and ring and pinky:
        return Gestos.PAPER
    if not index and not middle and not ring and not pinky:
        return Gestos.ROCK
    return Gestos.UNKNOWN


# Calculo de ángulos para modo espejo y poder transformar gestos de la mano a 
# angulos de servo. La función compute_angles devuelve una lista de 5 ángulos (0-180)

def _distance(a, b):
    return np.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2)


def compute_angles(landmarks):
    """Devuelve [thumb, index, middle, ring, pinky] en grados 0-180."""
    lm        = landmarks
    palm_size = _distance(lm[0], lm[9])
    if palm_size < 1e-6:
        return [90] * 5

    def finger_angle(tip_idx, base_idx):
        ratio = np.clip(_distance(lm[tip_idx], lm[base_idx]) / palm_size, 0.0, 1.5)
        return int(np.interp(ratio, [0.3, 1.4], [0, 180]))

    return [
        finger_angle(4,  1),   # pulgar
        finger_angle(8,  5),   # índice
        finger_angle(12, 9),   # medio
        finger_angle(16, 13),  # anular
        finger_angle(20, 17),  # meñique
    ]



class SocketClient:
    def __init__(self, host, port, enabled = True):
        self.host    = host
        self.port    = port
        self.enabled = enabled
        self._sock= None
        if self.enabled:
            self._connect()

    def _connect(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.settimeout(3)
            self._sock.connect((self.host, self.port))
            print(f"[Socket] Conectado a {self.host}:{self.port}")
        except (ConnectionRefusedError, OSError) as e:
            print(f"[Socket] No se pudo conectar: {e}. Corriendo sin Pi.")
            self._sock = None

    def send(self, data):
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

    def receive(self):
        """
        Lee la respuesta de la Pi (non-blocking, timeout 50ms).
        Devuelve el dict parseado o None si no hay datos / error.
        """
        if not self.enabled or self._sock is None:
            return None
        try:
            self._sock.settimeout(0.2)
            data = self._sock.recv(1024)
            if data:
                return json.loads(data.decode("utf-8"))
        except (socket.timeout, OSError, json.JSONDecodeError):
            pass
        finally:
            try:
                self._sock.settimeout(3)
            except OSError:
                pass
        return None

    def close(self):
        if self._sock:
            self._sock.close()
            self._sock = None


# Detector de la mano

class HandDetector:
    def __init__(
        self,
        mode = Modo.GAME,
        min_detection_confidence = CONFIDENCE,
        min_tracking_confidence = TRACKING_CONFIDENCE,
        game_hold_seconds = GAME_HOLD_SECONDS,
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
        self._landmarker      = mp_vision.HandLandmarker.create_from_options(options)
        self._frame_ts_ms     = 0
        self._current_gesture = Gestos.UNKNOWN
        self._gesture_start   = 0.0
        self._last_sent_at    = 0.0

    def process_frame(self, frame):
        """
        Procesa un frame BGR.
        Devuelve (frame_anotado, payload_bytes | None).
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

    def release(self):
        self._landmarker.close()



    def _process_game(self, landmarks, frame):
        gesture = classify_gesture(landmarks)
        now     = time.time()

        if gesture == Gestos.UNKNOWN:
            self._current_gesture = Gestos.UNKNOWN
            self._gesture_start   = 0.0
            self._draw_gesture_label(frame, "---", 0)
            return None

        if gesture != self._current_gesture:
            self._current_gesture = gesture
            self._gesture_start   = now

        hold = now - self._gesture_start
        self._draw_gesture_label(frame, gesture.value, hold)
        self._draw_hold_bar(frame, hold)

        if hold >= self.game_hold_seconds and (now - self._last_sent_at) > 3.0:
            self._last_sent_at  = now
            self._gesture_start = now
            return encode_game(gesture)

        return None

    def _draw_ui(self, frame):
        h, w = frame.shape[:2]
        label = f"MODO: {'JUEGO' if self.mode == Modo.GAME else 'TRACKING'}"
        cv2.rectangle(frame, (0, 0), (w, 40), (30, 30, 30), -1)
        cv2.putText(frame, label, (10, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 180), 2)

    def _draw_gesture_label(self, frame, label, hold=0) -> None:
        h = frame.shape[0]
        cv2.putText(frame, str(label), (10, h - 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)

    def _draw_hold_bar(self, frame, elapsed):
        h, w = frame.shape[:2]
        bx, by, bh  = 10, h - 40, 14
        bw          = w - 20
        required    = self.game_hold_seconds
        progress    = min(elapsed / required, 1.0) if required > 0 else 0
        cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (60, 60, 60), -1)
        filled = int(bw * progress)
        if filled > 0:
            color = (0, 255, 100) if progress < 1.0 else (0, 200, 255)
            cv2.rectangle(frame, (bx, by), (bx + filled, by + bh), color, -1)
        cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (150, 150, 150), 1)

    def _draw_mirror_overlay(self, frame, angles) -> None:
        names = ["Pulgar", "Indice", "Medio", "Anular", "Menique"]
        for i, (name, angle) in enumerate(zip(names, angles)):
            cv2.putText(frame, f"{name}: {angle:3d}°", (10, 60 + i * 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 230, 255), 1)