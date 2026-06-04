# Los archivos contenidos en este proyecto se corren de la siguiente forma:
# - pc/main.py -> aplicación principal en la PC, que accede a la webcam, detecta gestos y envía comandos a la Pi.
# - rasp/main.py -> servidor TCP en la Raspberry Pi, que recibe comandos de la PC y los traduce a señales UART para el microcontrolador.
# - atemega/ -> Codigo para el microcontrolador, que recibe comandos UART y controla los servos del brazo robótico.
