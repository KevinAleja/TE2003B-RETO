#include <Servo.h>

// ── Servos ────────────────────────────────────────────────────────
Servo pulgar;
Servo indice;
Servo medio;
Servo anular;
Servo menique;

// ── Protocolo UART ────────────────────────────────────────────────
// Modo juego:  'a' = piedra | 'b' = papel | 'c' = tijera
// Modo mirror: 0x04 + 5 bytes de ángulos [pulgar, indice, medio, anular, menique]

#define CMD_MIRROR 0x04
#define N_SERVOS   5

void setup() {
  pulgar.attach(2);
  indice.attach(3);
  medio.attach(4);
  anular.attach(5);
  menique.attach(6);

  Serial.begin(9600);
  Serial.println("--- Super Bracito Robotico Listo ---");

  papel();   // posición inicial: mano abierta
}

void loop() {
  if (Serial.available() > 0) {
    byte cmd = Serial.read();

    if (cmd == CMD_MIRROR) {
      // Esperar los 5 bytes de ángulos
      byte angles[N_SERVOS];
      int received = 0;

      // Espera con timeout de 100ms por si llegan con pequeño delay
      unsigned long t = millis();
      while (received < N_SERVOS && millis() - t < 200) {
        if (Serial.available()) {
          angles[received++] = Serial.read();
        }
      }

      if (received == N_SERVOS) {
        mirror(angles);
      } else {
        Serial.println("[Warn] Mirror: faltan bytes de angulos");
      }

    } else {
      // Modo juego: letra a/b/c
      switch (cmd) {
        case 'a': case 'A':
          Serial.println(">> PIEDRA");
          piedra();
          break;
        case 'b': case 'B':
          Serial.println(">> PAPEL");
          papel();
          break;
        case 'c': case 'C':
          Serial.println(">> TIJERA");
          tijera();
          break;
        case '\n': case '\r':
          break;   // ignorar saltos de línea del monitor serie
        default:
          Serial.print("[Warn] Comando desconocido:");
          Serial.println(cmd, HEX);
          break;
      }
    }
  }
}

// ── Jugadas fijas ─────────────────────────────────────────────────

void piedra() {
  pulgar.write(180);
  indice.write(180);
  medio.write(180);
  anular.write(129);
  menique.write(94);
}

void papel() {
  pulgar.write(0);
  indice.write(48);
  medio.write(0);
  anular.write(0);
  menique.write(0);
}

void tijera() {
  pulgar.write(180);
  indice.write(0);
  medio.write(0);
  anular.write(129);
  menique.write(94);
}

// ── Modo mirror: mueve cada servo al ángulo recibido ─────────────

void mirror(byte angles[]) {
  pulgar.write(angles[0]);
  indice.write(angles[1]);
  medio.write(angles[2]);
  anular.write(angles[3]);
  menique.write(angles[4]);
}
