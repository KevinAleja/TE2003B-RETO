# Mano Robótica con MediaPipe

Control de una mano robótica de 6 servos mediante visión computacional.
Detecta gestos de la mano en tiempo real (piedra, papel, tijera y réplica de movimiento)

usando MediaPipe en la PC, transmite los comandos a una Raspberry Pi vía socket, la cual sirve como orquestador para que un ATmega328P pueda mover lo motores de cada parte de la mano (Dedos y muñeca)

---

## Arquitectura del sistema



---

## Estructura del proyecto



---

## Crear entorno virtual e instalar dependencias

Para la creación del ambiente virtual estoy usando UV pero sientete libre de usar el manejador de paquetes de tu preferencia. Ya sea PIP, Anaconda y Micromamba c:

```bash
# Crea el entorno virtual con una versión de Python compatible
uv venv --python 3.11

# Activa el entorno
# Linux/Raspbian:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

# Instala todas las dependencias
uv pip install -r requirements.txt
```

---

## Modos de operación


---

## Comunicación entre componentes



---

## Hardware



---

## Escalabilidad del ATmega328P

El ATmega328P actúa como capa de actuadores desacoplada. Al concentrar el control
de periféricos en él, escalar el proyecto es directo sin modificar la lógica de la Rasp:
---
