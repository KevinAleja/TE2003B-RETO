"""
Define las estructuras de datos que viajan por el socket TCP (aseguramos una comunicación consistente) 
entre la PC y la Raspberry Pi, así como el formato de los mensajes JSON para tener 
diccionarios claros y evitar errores.
"""
 
import json
from enum import Enum
 
 
class Modo(Enum):
    GAME   = "ppt"
    MIRROR = "espejo"
 
 
class Gestos(Enum):
    ROCK     = "piedra"
    PAPER    = "papel"
    SCISSORS = "tijera"
    UNKNOWN  = "desconocido"
 
 
def encode_game(gesture):
    """PC → Pi: envía el gesto detectado en modo juego."""
    msg = {"modo": Modo.GAME.value, "jugada": gesture.value}
    return json.dumps(msg).encode("utf-8")
 
 
def encode_mirror(angles):
    """
    PC → Pi: envía los 5 ángulos de servo en modo mirror.
    angles = [thumb, index, middle, ring, pinky]  ← sin muñeca
    """
    assert len(angles) == 5, "Se esperan exactamente 5 ángulos (sin muñeca)"
    msg = {"modo": Modo.MIRROR.value, "angulos": angles}
    return json.dumps(msg).encode("utf-8")
 
 
def decode(data):
    """Pi: decodifica un mensaje recibido del socket."""
    return json.loads(data.decode("utf-8"))