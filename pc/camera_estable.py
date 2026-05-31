"""
devices.py — Visión computacional con MediaPipe
================================================
Maneja la webcam y la detección de gestos de la mano.

Modos:
    - MODE_GAME   : Detecta piedra / papel / tijera para el juego
    - MODE_MIRROR : Calcula los ángulos de cada dedo para replicar el gesto

Uso standalone (prueba en laptop sin hardware):
    python devices.py

Uso como módulo desde main.py:
    from devices import HandDetector, MODE_GAME, MODE_MIRROR
"""

import random
import time
from dataclasses import dataclass, field
from enum import Enum, auto

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

# ─────────────────────────────────────────────
# Constantes de modo
# ─────────────────────────────────────────────

class Mode(Enum):
    GAME   = auto()   # Piedra, papel, tijera
    MIRROR = auto()   # Réplica de movimiento


# Resultado del juego
class GameResult(Enum):
    WIN  = "¡Ganaste!"
    LOSE = "¡Perdiste!"
    TIE  = "¡Empate!"


# Gestos reconocidos
class Gesture(Enum):
    ROCK     = "Piedra"
    PAPER    = "Papel"
    SCISSORS = "Tijera"
    UNKNOWN  = "---"


# ─────────────────────────────────────────────
# Estructuras de datos
# ─────────────────────────────────────────────

@dataclass
class MirrorCommand:
    """Ángulos de servo para el modo réplica (0–180°)."""
    wrist:  int = 90   # Muñeca
    thumb:  int = 90   # Pulgar
    index:  int = 90   # Índice
    middle: int = 90   # Medio
    ring:   int = 90   # Anular
    pinky:  int = 90   # Meñique

    def to_bytes(self) -> bytes:
        """Serializa los 6 ángulos para envío UART / socket."""
        return bytes([
            self.wrist,
            self.thumb,
            self.index,
            self.middle,
            self.ring,
            self.pinky,
        ])

    def to_dict(self) -> dict:
        return {
            "wrist":  self.wrist,
            "thumb":  self.thumb,
            "index":  self.index,
            "middle": self.middle,
            "ring":   self.ring,
            "pinky":  self.pinky,
        }


@dataclass
class GameCommand:
    """Resultado de una ronda de piedra, papel y tijera."""
    player_gesture:  Gesture    = Gesture.UNKNOWN
    robot_gesture:   Gesture    = Gesture.UNKNOWN
    result:          GameResult = GameResult.TIE

    # Byte de comando para el ATmega
    COMMAND_MAP = {
        Gesture.ROCK:     0x01,
        Gesture.PAPER:    0x02,
        Gesture.SCISSORS: 0x03,
    }

    def to_byte(self) -> int:
        return self.COMMAND_MAP.get(self.robot_gesture, 0x00)

    def to_dict(self) -> dict:
        return {
            "mode":           "game",
            "player_gesture": self.player_gesture.value,
            "robot_gesture":  self.robot_gesture.value,
            "result":         self.result.value,
            "command_byte":   self.to_byte(),
        }


# ─────────────────────────────────────────────
# Lógica de detección de gestos
# ─────────────────────────────────────────────

# Índices de los 21 landmarks de MediaPipe para cada dedo
# Referencia: https://developers.google.com/mediapipe/solutions/vision/hand_landmarker
FINGER_TIPS = [4, 8, 12, 16, 20]   # Punta de cada dedo (pulgar → meñique)
FINGER_MIDS = [3, 6, 10, 14, 18]   # Nudillo intermedio


def _fingers_extended(landmarks) -> list[bool]:
    """
    Devuelve una lista de 5 booleanos indicando si cada dedo está extendido.
    Orden: [pulgar, índice, medio, anular, meñique]

    Estrategia:
        - Pulgar  : compara posición X de la punta vs. la articulación base
                    (funciona para mano derecha frente a la cámara)
        - Resto   : la punta (tip) debe estar más arriba (menor Y) que el
                    nudillo intermedio (pip)
    """
    extended = []

    # Pulgar — comparación horizontal
    thumb_tip  = landmarks[FINGER_TIPS[0]]
    thumb_base = landmarks[FINGER_MIDS[0]]
    extended.append(thumb_tip.x < thumb_base.x)  # Mano derecha

    # Índice, medio, anular, meñique — comparación vertical
    for tip_idx, mid_idx in zip(FINGER_TIPS[1:], FINGER_MIDS[1:]):
        tip = landmarks[tip_idx]
        mid = landmarks[mid_idx]
        extended.append(tip.y < mid.y)

    return extended  # [pulgar, índice, medio, anular, meñique]


def classify_gesture(landmarks) -> Gesture:
    """
    Clasifica el gesto de la mano basándose en qué dedos están extendidos.

        Piedra  : ningún dedo extendido (o solo pulgar)
        Papel   : todos los dedos extendidos
        Tijera  : índice y medio extendidos, resto cerrados
    """
    ext = _fingers_extended(landmarks)
    # ext = [pulgar, índice, medio, anular, meñique]

    index, middle, ring, pinky = ext[1], ext[2], ext[3], ext[4]

    if index and middle and not ring and not pinky:
        return Gesture.SCISSORS

    if index and middle and ring and pinky:
        return Gesture.PAPER

    if not index and not middle and not ring and not pinky:
        return Gesture.ROCK

    return Gesture.UNKNOWN


# ─────────────────────────────────────────────
# Lógica del juego
# ─────────────────────────────────────────────

_GESTURES_ALL = [Gesture.ROCK, Gesture.PAPER, Gesture.SCISSORS]

# Tabla de victorias: quien gana a quien
_WINS_AGAINST = {
    Gesture.ROCK:     Gesture.SCISSORS,
    Gesture.SCISSORS: Gesture.PAPER,
    Gesture.PAPER:    Gesture.ROCK,
}


def play_round(player: Gesture) -> GameCommand:
    """
    Genera la jugada aleatoria del robot y determina el resultado.
    Solo se llama cuando el jugador tiene un gesto válido (no UNKNOWN).
    """
    robot  = random.choice(_GESTURES_ALL)
    result = _determine_result(player, robot)
    return GameCommand(player_gesture=player, robot_gesture=robot, result=result)


def _determine_result(player: Gesture, robot: Gesture) -> GameResult:
    if player == robot:
        return GameResult.TIE
    if _WINS_AGAINST[player] == robot:
        return GameResult.WIN
    return GameResult.LOSE


# ─────────────────────────────────────────────
# Cálculo de ángulos para modo mirror
# ─────────────────────────────────────────────

def compute_mirror_command(landmarks) -> MirrorCommand:
    """
    Calcula el ángulo de cada servo (0–180°) a partir de los landmarks.

    Estrategia simplificada:
        - Mide la distancia relativa entre la punta del dedo y su base
          normalizada por el tamaño de la palma.
        - 0° = dedo completamente cerrado, 180° = completamente extendido.
        - Muñeca: usa la inclinación del eje de la palma.
    """
    lm = landmarks

    # Tamaño de referencia: distancia muñeca (0) → base del medio (9)
    palm_size = _distance(lm[0], lm[9])
    if palm_size < 1e-6:
        return MirrorCommand()

    def finger_angle(tip_idx: int, base_idx: int) -> int:
        d = _distance(lm[tip_idx], lm[base_idx])
        ratio = np.clip(d / palm_size, 0.0, 1.5)
        return int(np.interp(ratio, [0.3, 1.4], [0, 180]))

    # Ángulo de muñeca: inclinación del eje palma
    wrist_angle = _wrist_angle(lm)

    return MirrorCommand(
        wrist  = wrist_angle,
        thumb  = finger_angle(4,  1),
        index  = finger_angle(8,  5),
        middle = finger_angle(12, 9),
        ring   = finger_angle(16, 13),
        pinky  = finger_angle(20, 17),
    )


def _distance(a, b) -> float:
    return np.sqrt((a.x - b.x)**2 + (a.y - b.y)**2)


def _wrist_angle(lm) -> int:
    """
    Estima la rotación de la muñeca comparando la posición del landmark
    de la muñeca (0) respecto al centro de la palma (9).
    Devuelve un valor entre 0 y 180°.
    """
    dx = lm[9].x - lm[0].x
    dy = lm[9].y - lm[0].y
    angle_rad = np.arctan2(-dy, dx)           # -dy porque Y crece hacia abajo
    angle_deg = np.degrees(angle_rad)         # -180 a 180
    mapped    = int(np.interp(angle_deg, [-90, 90], [0, 180]))
    return int(np.clip(mapped, 0, 180))


# ─────────────────────────────────────────────
# Helpers para la Tasks API
# ─────────────────────────────────────────────

def _get_model_path() -> str:
    """
    Descarga el modelo hand_landmarker.task si no existe localmente.
    MediaPipe 0.10.x requiere el modelo descargado explícitamente.
    """
    import urllib.request
    from pathlib import Path

    model_path = Path("hand_landmarker.task")
    if not model_path.exists():
        url = (
            "https://storage.googleapis.com/mediapipe-models/"
            "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
        )
        print(f"Descargando modelo MediaPipe → {model_path} ...")
        urllib.request.urlretrieve(url, model_path)
        print("Modelo descargado.")
    return str(model_path)


def _draw_landmarks_on_frame(frame: np.ndarray, landmarks: list) -> None:
    """
    Dibuja los 21 landmarks y sus conexiones sobre el frame usando OpenCV puro.
    Compatible con mediapipe >= 0.10.x donde mp.solutions ya no está disponible.
    """
    h, w = frame.shape[:2]

    # Conexiones entre landmarks (mismo grafo que MediaPipe HAND_CONNECTIONS)
    CONNECTIONS = [
        (0,1),(1,2),(2,3),(3,4),         # Pulgar
        (0,5),(5,6),(6,7),(7,8),          # Índice
        (0,9),(9,10),(10,11),(11,12),     # Medio
        (0,13),(13,14),(14,15),(15,16),   # Anular
        (0,17),(17,18),(18,19),(19,20),   # Meñique
        (5,9),(9,13),(13,17),             # Palma
    ]

    # Convertir coordenadas normalizadas → píxeles
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]

    # Dibujar conexiones
    for a, b in CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], (0, 200, 255), 2, cv2.LINE_AA)

    # Dibujar puntos
    for i, (x, y) in enumerate(pts):
        # Puntas de dedo en blanco, resto en verde
        color = (255, 255, 255) if i in (4, 8, 12, 16, 20) else (0, 180, 0)
        cv2.circle(frame, (x, y), 5, color, -1, cv2.LINE_AA)
        cv2.circle(frame, (x, y), 5, (0, 0, 0), 1, cv2.LINE_AA)  # borde negro


# ─────────────────────────────────────────────
# Clase principal: HandDetector
# ─────────────────────────────────────────────

class HandDetector:
    """
    Encapsula MediaPipe Hands y expone métodos de alto nivel
    para detectar gestos en los dos modos del proyecto.

    Parámetros
    ----------
    mode : Mode
        Modo de operación inicial (puede cambiarse en caliente).
    min_detection_confidence : float
        Umbral mínimo de confianza para detección inicial.
    min_tracking_confidence : float
        Umbral mínimo de confianza para seguimiento de frames.
    game_hold_seconds : float
        Segundos que el usuario debe mantener el gesto antes de jugar
        una ronda (evita disparos accidentales).
    """

    def __init__(
        self,
        mode: Mode = Mode.GAME,
        min_detection_confidence: float = 0.75,
        min_tracking_confidence:  float = 0.75,
        game_hold_seconds:        float = 1.5,
    ):
        self.mode = mode
        self.game_hold_seconds = game_hold_seconds

        # ── Tasks API (mediapipe >= 0.10.x) ──────────────────────────────
        base_options = mp_python.BaseOptions(
            model_asset_path=_get_model_path()
        )
        options = mp_vision.HandLandmarkerOptions(
            base_options            = base_options,
            running_mode            = mp_vision.RunningMode.VIDEO,
            num_hands               = 1,
            min_hand_detection_confidence = min_detection_confidence,
            min_hand_presence_confidence  = min_detection_confidence,
            min_tracking_confidence       = min_tracking_confidence,
        )
        self._landmarker = mp_vision.HandLandmarker.create_from_options(options)
        self._frame_ts_ms: int = 0   # timestamp acumulado para VIDEO mode

        # Estado interno para el modo juego
        self._current_gesture:  Gesture   = Gesture.UNKNOWN
        self._gesture_start:    float     = 0.0
        self._last_game_result: GameCommand | None = None
        self._result_display_until: float = 0.0   # muestra resultado N segundos

    # ── Interfaz pública ────────────────────────────────────────────────

    def process_frame(self, frame: np.ndarray) -> tuple[
        np.ndarray,
        GameCommand | MirrorCommand | None
    ]:
        """
        Procesa un frame de la webcam.

        Devuelve:
            frame_annotated : frame con landmarks y texto superpuestos
            command         : GameCommand, MirrorCommand, o None si no hay mano
        """
        # Tasks API requiere mp.Image y un timestamp incremental en VIDEO mode
        self._frame_ts_ms += 33   # ~30 fps
        rgb        = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image   = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        detection  = self._landmarker.detect_for_video(mp_image, self._frame_ts_ms)
        command    = None

        if detection.hand_landmarks:
            landmarks = detection.hand_landmarks[0]   # lista de NormalizedLandmark

            # Dibujar landmarks con la API de solutions (sigue disponible para dibujo)
            _draw_landmarks_on_frame(frame, landmarks)

            if self.mode == Mode.GAME:
                command = self._process_game(landmarks, frame)
            else:
                command = compute_mirror_command(landmarks)
                self._draw_mirror_overlay(frame, command)
        else:
            # Sin mano — resetear estado del juego
            self._current_gesture = Gesture.UNKNOWN
            self._gesture_start   = 0.0

        self._draw_mode_banner(frame)
        self._draw_game_result(frame)

        return frame, command

    def set_mode(self, mode: Mode) -> None:
        """Cambia el modo de operación en caliente."""
        self.mode = mode
        self._current_gesture = Gesture.UNKNOWN
        self._gesture_start   = 0.0
        self._last_game_result = None

    # ── Procesamiento interno ───────────────────────────────────────────

    def _process_game(self, landmarks, frame: np.ndarray) -> GameCommand | None:
        """
        Detecta el gesto, espera game_hold_seconds para confirmar,
        luego genera la jugada del robot.
        """
        gesture = classify_gesture(landmarks)
        now     = time.time()

        if gesture == Gesture.UNKNOWN:
            self._current_gesture = Gesture.UNKNOWN
            self._gesture_start   = 0.0
            self._draw_gesture_label(frame, gesture)
            return None

        # ¿Es el mismo gesto que antes?
        if gesture != self._current_gesture:
            self._current_gesture = gesture
            self._gesture_start   = now

        hold_elapsed = now - self._gesture_start
        self._draw_gesture_label(frame, gesture, hold_elapsed, self.game_hold_seconds)

        # Barra de progreso de confirmación
        self._draw_hold_bar(frame, hold_elapsed, self.game_hold_seconds)

        # ¿Ya pasó el tiempo de hold y no estamos mostrando resultado previo?
        if hold_elapsed >= self.game_hold_seconds and now > self._result_display_until:
            cmd = play_round(gesture)
            self._last_game_result    = cmd
            self._result_display_until = now + 3.0   # mostrar 3 s
            self._gesture_start        = now          # resetear hold
            return cmd

        return None

    # ── Dibujo / anotaciones ────────────────────────────────────────────

    def _draw_mode_banner(self, frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        label = f"MODO: {'JUEGO' if self.mode == Mode.GAME else 'MIRROR'}"
        cv2.rectangle(frame, (0, 0), (w, 40), (30, 30, 30), -1)
        cv2.putText(frame, label, (10, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 180), 2)

    def _draw_gesture_label(
        self,
        frame: np.ndarray,
        gesture: Gesture,
        hold: float = 0.0,
        required: float = 0.0,
    ) -> None:
        h, w = frame.shape[:2]
        text  = gesture.value
        color = (255, 255, 255) if gesture != Gesture.UNKNOWN else (100, 100, 100)
        cv2.putText(frame, text, (10, h - 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)

    def _draw_hold_bar(
        self,
        frame: np.ndarray,
        elapsed: float,
        required: float,
    ) -> None:
        """Barra de progreso que indica cuánto falta para confirmar el gesto."""
        h, w = frame.shape[:2]
        bar_x, bar_y, bar_h = 10, h - 40, 14
        bar_w = w - 20
        progress = min(elapsed / required, 1.0) if required > 0 else 0

        cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h),
                      (60, 60, 60), -1)
        filled = int(bar_w * progress)
        if filled > 0:
            color = (0, 255, 100) if progress < 1.0 else (0, 200, 255)
            cv2.rectangle(frame, (bar_x, bar_y), (bar_x + filled, bar_y + bar_h),
                          color, -1)
        cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h),
                      (150, 150, 150), 1)

    def _draw_game_result(self, frame: np.ndarray) -> None:
        """Muestra el resultado de la última ronda mientras está vigente."""
        if self._last_game_result is None:
            return
        if time.time() > self._result_display_until:
            return

        cmd = self._last_game_result
        h, w = frame.shape[:2]

        # Fondo semitransparente
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, h // 2 - 80), (w, h // 2 + 80),
                      (20, 20, 20), -1)
        cv2.addWeighted(overlay, 0.65, frame, 0.35, 0, frame)

        result_color = {
            GameResult.WIN:  (0, 255, 100),
            GameResult.LOSE: (0, 80, 255),
            GameResult.TIE:  (0, 200, 255),
        }[cmd.result]

        texts = [
            (f"Tu: {cmd.player_gesture.value}",   h // 2 - 50, (200, 200, 200), 0.9),
            (f"Robot: {cmd.robot_gesture.value}",  h // 2 - 10, (200, 200, 200), 0.9),
            (cmd.result.value,                     h // 2 + 50, result_color,   1.4),
        ]
        for text, y, color, scale in texts:
            cv2.putText(frame, text, (w // 2 - 130, y),
                        cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2)

    def _draw_mirror_overlay(self, frame: np.ndarray, cmd: MirrorCommand) -> None:
        """Muestra los ángulos calculados en modo mirror."""
        h = frame.shape[0]
        labels = [
            f"Muneca : {cmd.wrist:3d}°",
            f"Pulgar : {cmd.thumb:3d}°",
            f"Indice : {cmd.index:3d}°",
            f"Medio  : {cmd.middle:3d}°",
            f"Anular : {cmd.ring:3d}°",
            f"Menique: {cmd.pinky:3d}°",
        ]
        for i, label in enumerate(labels):
            cv2.putText(frame, label, (10, 60 + i * 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 230, 255), 1)

    def release(self) -> None:
        self._landmarker.close()


# ─────────────────────────────────────────────
# Ejecución standalone — prueba en laptop
# ─────────────────────────────────────────────

def main():
    """
    Ejecuta el detector en la webcam local.
    Teclas:
        G  → cambiar a modo JUEGO
        M  → cambiar a modo MIRROR
        Q  → salir
    """
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError("No se pudo abrir la webcam.")

    detector = HandDetector(mode=Mode.GAME)
    print("Iniciando... Presiona G (juego), M (mirror), Q (salir)")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame = cv2.flip(frame, 1)   # espejo horizontal — más intuitivo
        annotated, command = detector.process_frame(frame)

        if command is not None:
            if isinstance(command, GameCommand):
                print(f"[JUEGO]  {command.to_dict()}")
            elif isinstance(command, MirrorCommand):
                # En mirror imprime solo cuando hay cambio notable
                pass

        cv2.imshow("Mano Robotica — devices.py", annotated)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("g"):
            detector.set_mode(Mode.GAME)
            print("→ Modo JUEGO activado")
        elif key == ord("m"):
            detector.set_mode(Mode.MIRROR)
            print("→ Modo MIRROR activado")

    cap.release()
    detector.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()