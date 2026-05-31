"""
Define las estructuras de datos que viajan por el socket TCP (aseguramos una comunicación consistente) 
entre la PC y la Raspberry Pi, así como el formato de los mensajes JSON para tener 
diccionarios claros y evitar errores de parsing.
"""

import json
from enum import Enum

# Para implemnetaciones futuras, se podrían agregar más modos o gestos sin romper la compatibilidad.
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
    msg = {"modo": Modo.GAME.value, "Jugada": gesture.value}
    return json.dumps(msg).encode("utf-8")


def encode_mirror(angles):
    """
    PC → Pi: envía los 6 ángulos de servo en modo mirror.
    angles = [wrist, thumb, index, middle, ring, pinky]
    """
    assert len(angles) == 6, "Se esperan exactamente 6 ángulos"
    msg = {"modo": Modo.MIRROR.value, "angulos": angles}
    return json.dumps(msg).encode("utf-8")


def decode(data):
    """Pi: decodifica un mensaje recibido del socket."""
    return json.loads(data.decode("utf-8"))