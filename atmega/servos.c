
#include <avr/io.h>
#include <avr/interrupt.h>
#include <util/delay.h>
#include <stdint.h>

/* ─────────────────────────────────────────────
 * Configuración de hardware
 * ───────────────────────────────────────────── */

#define F_CPU           8000000UL
#define BAUD            115200UL
#define UBRR_VAL        ((F_CPU / (16UL * BAUD)) - 1)   /* = 3 a 8 MHz */

/* Buzzer en PD4 */
#define BUZZER_DDR      DDRD
#define BUZZER_PORT     PORTD
#define BUZZER_PIN      PD4

#define SERVO_MIN_TICKS  500U
#define SERVO_MAX_TICKS  2400U
#define SERVO_PERIOD     20000U   /* ticks para 20 ms con prescaler 8 @ 8MHz */

/* ─────────────────────────────────────────────
 * Gestos (mismos valores que el protocolo UART)
 * ───────────────────────────────────────────── */

#define GESTO_PIEDRA    0x01
#define GESTO_PAPEL     0x02
#define GESTO_TIJERA    0x03
#define CMD_MIRROR      0x04

/* ─────────────────────────────────────────────
 * Ángulos predefinidos por gesto (0–180°)
 * Ajusta estos valores según tu construcción mecánica
 *
 * Convención: 0° = dedo cerrado, 180° = dedo extendido
 * ───────────────────────────────────────────── */

typedef struct {
    uint8_t muneca;
    uint8_t pulgar;
    uint8_t indice;
    uint8_t medio;
    uint8_t anular;
    uint8_t menique;
} Postura;

static const Postura POSTURA_PIEDRA = {
    .muneca  = 90,
    .pulgar  = 30,
    .indice  = 10,
    .medio   = 10,
    .anular  = 10,
    .menique = 10,
};

static const Postura POSTURA_PAPEL = {
    .muneca  = 90,
    .pulgar  = 160,
    .indice  = 170,
    .medio   = 170,
    .anular  = 170,
    .menique = 170,
};

static const Postura POSTURA_TIJERA = {
    .muneca  = 90,
    .pulgar  = 30,
    .indice  = 170,
    .medio   = 170,
    .anular  = 10,
    .menique = 10,
};

static const Postura POSTURA_REPOSO = {
    .muneca  = 90,
    .pulgar  = 90,
    .indice  = 90,
    .medio   = 90,
    .anular  = 90,
    .menique = 90,
};

/* ─────────────────────────────────────────────
 * Buffer UART para modo MIRROR
 * ───────────────────────────────────────────── */

#define MIRROR_BYTES    7          /* 0x04 + 6 ángulos */
static volatile uint8_t uart_buf[MIRROR_BYTES];
static volatile uint8_t uart_idx  = 0;
static volatile uint8_t cmd_ready = 0;   /* flag: hay comando listo para procesar */

/* ─────────────────────────────────────────────
 * Conversión ángulo → ticks PWM
 * ───────────────────────────────────────────── */

static inline uint16_t angle_to_ticks(uint8_t angle) {
    /* Interpola linealmente entre SERVO_MIN y SERVO_MAX */
    return SERVO_MIN_TICKS +
           ((uint32_t)(SERVO_MAX_TICKS - SERVO_MIN_TICKS) * angle) / 180U;
}


static void timers_init(void) {
    DDRB |= (1 << PB1) | (1 << PB2);
    ICR1  = SERVO_PERIOD;
    OCR1A = angle_to_ticks(90);   /* Medio  — posición central */
    OCR1B = angle_to_ticks(90);   /* Anular — posición central */
    TCCR1A = (1 << COM1A1) | (1 << COM1B1) | (1 << WGM11);
    TCCR1B = (1 << WGM13)  | (1 << WGM12)  | (1 << CS11);  /* prescaler 8 */

    /* ── Timer0 (8-bit): pines PD5 (OC0B) y PD6 (OC0A)
     *    Modo: Fast PWM, TOP = 0xFF
     *    Prescaler: 1024 → período ≈ 32.7 ms @ 8 MHz
     *    Nota: período no es exactamente 20 ms pero suficiente para SG90
     *    Resolución reducida: se remapea el rango de ticks
     */
    DDRD |= (1 << PD5) | (1 << PD6);
    TCCR0A = (1 << COM0A1) | (1 << COM0B1) | (1 << WGM01) | (1 << WGM00);
    TCCR0B = (1 << CS02) | (1 << CS00);   /* prescaler 1024 */
    OCR0A  = 128;   /* Índice  — posición central */
    OCR0B  = 128;   /* Pulgar  — posición central */

    /* ── Timer2 (8-bit): pines PB3 (OC2A) y PD3 (OC2B)
     *    Igual que Timer0 pero en Timer2
     *    Prescaler: 1024
     */
    DDRB |= (1 << PB3);
    DDRD |= (1 << PD3);
    TCCR2A = (1 << COM2A1) | (1 << COM2B1) | (1 << WGM21) | (1 << WGM20);
    TCCR2B = (1 << CS22) | (1 << CS21) | (1 << CS20);   /* prescaler 1024 */
    OCR2A  = 128;   /* Meñique — posición central */
    OCR2B  = 128;   /* Muñeca  — posición central */
}

/* ─────────────────────────────────────────────
 * Escritura de ángulo en cada servo
 * Timer1 usa ticks de 16-bit (alta resolución)
 * Timer0 y Timer2 usan escala de 8-bit (baja resolución)
 * ───────────────────────────────────────────── */

/* Convierte ángulo a valor 8-bit para Timer0/Timer2
 * Rango útil de pulso: ~12 (0°) a ~58 (180°) con prescaler 1024 @ 8MHz */
static inline uint8_t angle_to_8bit(uint8_t angle) {
    return (uint8_t)(12U + ((uint16_t)angle * 46U) / 180U);
}

static void set_servo_muneca(uint8_t angle) {
    OCR2B = angle_to_8bit(angle);
}

static void set_servo_pulgar(uint8_t angle) {
    OCR0B = angle_to_8bit(angle);
}

static void set_servo_indice(uint8_t angle) {
    OCR0A = angle_to_8bit(angle);
}

static void set_servo_medio(uint8_t angle) {
    OCR1A = angle_to_ticks(angle);
}

static void set_servo_anular(uint8_t angle) {
    OCR1B = angle_to_ticks(angle);
}

static void set_servo_menique(uint8_t angle) {
    OCR2A = angle_to_8bit(angle);
}

/* Aplica una postura completa */
static void aplicar_postura(const Postura *p) {
    set_servo_muneca (p->muneca);
    set_servo_pulgar (p->pulgar);
    set_servo_indice (p->indice);
    set_servo_medio  (p->medio);
    set_servo_anular (p->anular);
    set_servo_menique(p->menique);
}

/* ─────────────────────────────────────────────
 * Buzzer activo
 * ───────────────────────────────────────────── */

static void buzzer_init(void) {
    BUZZER_DDR  |=  (1 << BUZZER_PIN);
    BUZZER_PORT &= ~(1 << BUZZER_PIN);   /* apagado por defecto */
}

static void buzzer_on(void)  { BUZZER_PORT |=  (1 << BUZZER_PIN); }
static void buzzer_off(void) { BUZZER_PORT &= ~(1 << BUZZER_PIN); }

/* Patrones de tono para cada resultado */
static void tono_victoria(void) {
    /* Dos pitidos cortos — subida */
    buzzer_on();  _delay_ms(100); buzzer_off(); _delay_ms(80);
    buzzer_on();  _delay_ms(200); buzzer_off();
}

static void tono_derrota(void) {
    /* Un pitido largo descendente */
    buzzer_on();  _delay_ms(400); buzzer_off();
}

static void tono_empate(void) {
    /* Tres pitidos cortos iguales */
    for (uint8_t i = 0; i < 3; i++) {
        buzzer_on();  _delay_ms(80);
        buzzer_off(); _delay_ms(80);
    }
}

static void ejecutar_gesto_juego(uint8_t cmd) {
    const Postura *p = &POSTURA_REPOSO;

    switch (cmd) {
        case GESTO_PIEDRA:
            p = &POSTURA_PIEDRA;
            break;
        case GESTO_PAPEL:
            p = &POSTURA_PAPEL;
            break;
        case GESTO_TIJERA:
            p = &POSTURA_TIJERA;
            break;
        default:
            return;
    }

    aplicar_postura(p);
    _delay_ms(800);          /* espera a que los servos lleguen */


    tono_empate();           /* tono neutro — reemplazar cuando llegue resultado */

    _delay_ms(2000);         /* mantiene la postura visible */
    aplicar_postura(&POSTURA_REPOSO);
}

/* ─────────────────────────────────────────────
 * UART
 * ───────────────────────────────────────────── */

static void uart_init(void) {
    UBRR0H = (uint8_t)(UBRR_VAL >> 8);
    UBRR0L = (uint8_t)(UBRR_VAL);
    UCSR0B = (1 << RXEN0) | (1 << RXCIE0);   /* habilita RX e interrupción */
    UCSR0C = (1 << UCSZ01) | (1 << UCSZ00);  /* 8 bits de datos, 1 stop */
}


ISR(USART_RX_vect) {
    uint8_t byte = UDR0;

    if (uart_idx == 0) {
        /* Primer byte: siempre es el comando */
        uart_buf[0] = byte;
        if (byte == CMD_MIRROR) {
            uart_idx = 1;          /* esperar 6 bytes más */
        } else {
            cmd_ready = 1;         /* comando de 1 byte listo */
            uart_idx  = 0;
        }
    } else {
        /* Bytes de ángulos para MIRROR */
        uart_buf[uart_idx++] = byte;
        if (uart_idx >= MIRROR_BYTES) {
            uart_idx  = 0;
            cmd_ready = 1;
        }
    }
}

/* ─────────────────────────────────────────────
 * Procesamiento del comando en el loop principal
 * ───────────────────────────────────────────── */

static void procesar_comando(void) {
    uint8_t cmd = uart_buf[0];

    if (cmd == CMD_MIRROR) {
        /* uart_buf[1..6] = ángulos: muñeca, pulgar, índice, medio, anular, meñique */
        set_servo_muneca (uart_buf[1]);
        set_servo_pulgar (uart_buf[2]);
        set_servo_indice (uart_buf[3]);
        set_servo_medio  (uart_buf[4]);
        set_servo_anular (uart_buf[5]);
        set_servo_menique(uart_buf[6]);
    } else {
        ejecutar_gesto_juego(cmd);
    }
}

/* ─────────────────────────────────────────────
 * main
 * ───────────────────────────────────────────── */

int main(void) {
    buzzer_init();
    timers_init();
    uart_init();
    sei();   /* habilitar interrupciones globales */

    /* Postura de bienvenida: abre y cierra la mano una vez */
    aplicar_postura(&POSTURA_PAPEL);
    _delay_ms(800);
    aplicar_postura(&POSTURA_PIEDRA);
    _delay_ms(800);
    aplicar_postura(&POSTURA_REPOSO);

    /* Tono de listo */
    tono_victoria();

    while (1) {
        if (cmd_ready) {
            cmd_ready = 0;         
            procesar_comando();
        }
    }

    return 0;
}