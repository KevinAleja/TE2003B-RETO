"""
==============================================================
backed de la interfaz gráfica con Tkinter.
Contiene la clase InterfazBrazo, que encapsula toda la lógica de 
la ventana, los widgets y su actualización.
==============================================================
"""

import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageTk
import numpy as np
import cv2


# Colores de la interfaz
BG        = "#0a0e14"
PANEL     = "#2a2525"
BORDER    = "#cecfd1"
CYAN      = "#00d4ff"
GREEN     = "#00ff88"
RED       = "#ff3d5a"
AMBER     = "#ffaa00"
DIM       = "#e0e0e0"
TEXT      = "#c8dce8"
TEXT_DIM  = "#3d5a72"

FINGER_NAMES = ["Muñeca", "Pulgar", "Índice", "Medio", "Anular", "Meñique"]


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


class InterfazBrazo:
    def __init__(
        self,
        on_mode_game,
        on_mode_mirror,
        on_close,
        cam_width:  int = 640,
        cam_height: int = 480,
    ):
        self._on_close  = on_close
        self._cam_w     = cam_width
        self._cam_h     = cam_height
        self._photo     = None
        self._angles    = [0] * 6
        self._mode      = "JUEGO"

        # Ventana principal
        self.root = tk.Tk()
        self.root.title("Equipo 1")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_close)

        # Fuentes monoespaciadas
        self._f_title  = tkfont.Font(family="Courier New", size=13, weight="bold")
        self._f_label  = tkfont.Font(family="Courier New", size=9)
        self._f_value  = tkfont.Font(family="Courier New", size=14, weight="bold")
        self._f_small  = tkfont.Font(family="Courier New", size=8)
        self._f_status = tkfont.Font(family="Courier New", size=10)
        self._f_team   = tkfont.Font(family="Courier New", size=11, weight="bold")
        self._f_big    = tkfont.Font(family="Courier New", size=18, weight="bold")

        # Layout 
        main = tk.Frame(self.root, bg=BG)
        main.pack(padx=12, pady=12)

        left   = tk.Frame(main, bg=BG)
        center = tk.Frame(main, bg=BG)
        right  = tk.Frame(main, bg=BG)

        left.grid  (row=0, column=0, padx=(0, 10), sticky="ns")
        center.grid(row=0, column=1)
        right.grid (row=0, column=2, padx=(10, 0), sticky="ns")

        # ══════════════════════════════════════════════════════════
        # PANEL IZQUIERDO — Botones
        # ══════════════════════════════════════════════════════════
        self._build_left(left, on_mode_game, on_mode_mirror)

        # ══════════════════════════════════════════════════════════
        # PANEL CENTRAL — Cámara
        # ══════════════════════════════════════════════════════════
        self._build_center(center)

        # ══════════════════════════════════════════════════════════
        # PANEL DERECHO — Ángulos + nombre equipo
        # ══════════════════════════════════════════════════════════
        self._build_right(right)

        # ── Teclado ───────────────────────────────────────────────
        self.root.bind("<KeyPress-g>", lambda _: on_mode_game())
        self.root.bind("<KeyPress-t>", lambda _: on_mode_mirror())
        self.root.bind("<KeyPress-q>", lambda _: self._handle_close())

    # ── Construcción de paneles ───────────────────────────────────

    def _build_left(self, parent, on_game, on_mirror):
        # Separador vertical izquierdo
        panel_h = self._cam_h + 60
        tk.Frame(parent, bg=BORDER, width=2, height=panel_h).pack(
            side="right", fill="y", padx=(8, 0)
        )

        btn_frame = tk.Frame(parent, bg=BG)
        btn_frame.pack(expand=True, fill="both", padx=(0, 10))

        # Etiqueta superior
        tk.Label(
            btn_frame, text="CONTROL", bg=BG, fg=TEXT_DIM,
            font=self._f_small, anchor="w"
        ).pack(anchor="w", pady=(20, 12))

        # Botón JUEGO
        self._btn_game = self._make_btn(
            btn_frame, "▶  JUEGO", on_game, GREEN, active=True
        )
        self._btn_game.pack(fill="x", pady=(0, 8))

        # Botón TRACKING
        self._btn_mirror = self._make_btn(
            btn_frame, "◈  TRACKING", on_mirror, CYAN, active=False
        )
        self._btn_mirror.pack(fill="x", pady=(0, 8))

        # Botón APAGAR
        self._btn_off = self._make_btn(
            btn_frame, "■  APAGAR", self._handle_close, RED, active=False
        )
        self._btn_off.pack(fill="x", pady=(0, 8))

        # Indicador de modo activo
        tk.Frame(btn_frame, bg=BORDER, height=1).pack(fill="x", pady=(16, 8))
        tk.Label(btn_frame, text="MODO ACTIVO", bg=BG, fg=TEXT_DIM,
                 font=self._f_small).pack(anchor="w")
        self._mode_label = tk.Label(
            btn_frame, text="▶ JUEGO", bg=BG, fg=GREEN, font=self._f_title
        )
        self._mode_label.pack(anchor="w", pady=(4, 0))

        # Gesto detectado
        tk.Frame(btn_frame, bg=BORDER, height=1).pack(fill="x", pady=(16, 8))
        tk.Label(btn_frame, text="GESTO", bg=BG, fg=TEXT_DIM,
                 font=self._f_small).pack(anchor="w")
        self._status_label = tk.Label(
            btn_frame, text="---", bg=BG, fg=TEXT, font=self._f_value
        )
        self._status_label.pack(anchor="w", pady=(4, 0))

        # Hint de teclas
        tk.Frame(btn_frame, bg=BORDER, height=1).pack(fill="x", pady=(16, 8))
        for line in ["G → juego", "T → tracking", "Q → salir"]:
            tk.Label(btn_frame, text=line, bg=BG, fg=TEXT_DIM,
                     font=self._f_small).pack(anchor="w")

    def _build_center(self, parent):
        # Título centrado arriba del canvas
        header = tk.Frame(parent, bg=BG)
        header.pack(fill="x", pady=(0, 6))

        tk.Label(
            header, text="CÁMARA",
            bg=BG, fg=TEXT_DIM, font=self._f_small
        ).pack(side="left")

        # Canvas con borde de color
        cam_border = tk.Frame(parent, bg=CYAN, padx=2, pady=2)
        cam_border.pack()

        self.canvas = tk.Canvas(
            cam_border,
            width=self._cam_w,
            height=self._cam_h,
            bg="#000", highlightthickness=0,
        )
        self.canvas.pack()

        # Barra de estado inferior
        status_bar = tk.Frame(parent, bg=PANEL, pady=5)
        status_bar.pack(fill="x", pady=(4, 0))

        self._conn_label = tk.Label(
            status_bar, text="◉ SIN CONEXIÓN CON LA RASP",
            bg=PANEL, fg=RED, font=self._f_small
        )
        self._conn_label.pack(side="left", padx=10)

    def _build_right(self, parent):
        # Separador vertical derecho
        tk.Frame(parent, bg=BORDER, width=2).pack(side="left", fill="y", padx=(0, 10))

        content = tk.Frame(parent, bg=BG)
        content.pack(fill="both", expand=True)

        # ── Banner del equipo ──────────────────────────────────────
        team_box = tk.Frame(content, bg=PANEL, padx=14, pady=10)
        team_box.pack(fill="x", pady=(20, 0))

        tk.Label(
            team_box, text="EQUIPO", bg=PANEL, fg=TEXT_DIM, font=self._f_small
        ).pack(anchor="w")

        # Titulo 
        tk.Label(
            team_box,
            text="Equipo \n1",
            bg=PANEL, fg=CYAN,
            font=self._f_big,
            justify="left",
        ).pack(anchor="w")

        tk.Label(
            team_box, text="Diseño de Sistemas en Chip  |  ITESM, 04/2026",
            bg=PANEL, fg=TEXT_DIM, font=self._f_small
        ).pack(anchor="w", pady=(4, 0))

        # ── Panel de ángulos ───────────────────────────────────────
        tk.Frame(content, bg=BORDER, height=1).pack(fill="x", pady=(16, 0))

        tk.Label(
            content, text="ÁNGULOS",
            bg=BG, fg=TEXT_DIM, font=self._f_small
        ).pack(anchor="w", pady=(8, 8))

        self._angle_bars   = []
        self._angle_values = []

        for i, name in enumerate(FINGER_NAMES):
            row = tk.Frame(content, bg=BG)
            row.pack(fill="x", pady=3)

            # Nombre del dedo
            tk.Label(
                row, text=f"{name:<8}", bg=BG, fg=TEXT_DIM,
                font=self._f_label, width=8, anchor="w"
            ).pack(side="left")

            # Barra de progreso contenedor
            bar_bg = tk.Frame(row, bg=DIM, width=100, height=10)
            bar_bg.pack(side="left", padx=(4, 6))
            bar_bg.pack_propagate(False)

            bar_fill = tk.Frame(bar_bg, bg=CYAN, width=0, height=10)
            bar_fill.place(x=0, y=0, height=10)

            # Valor numérico
            val = tk.Label(
                row, text="  0°", bg=BG, fg=CYAN,
                font=self._f_label, width=5, anchor="e"
            )
            val.pack(side="left")

            self._angle_bars.append((bar_bg, bar_fill))
            self._angle_values.append(val)

        # Total de ángulos
        tk.Frame(content, bg=BORDER, height=1).pack(fill="x", pady=(12, 4))


    def update_frame(self, bgr_frame: np.ndarray) -> None:
        rgb   = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        photo = ImageTk.PhotoImage(image=Image.fromarray(rgb))
        self.canvas.create_image(0, 0, anchor="nw", image=photo)
        self._photo = photo

    def set_mode(self, modo):
        self._mode = modo
        if modo == "JUEGO":
            self._mode_label.config(text="▶ JUEGO", fg=GREEN)
            self._btn_game.config(  bg=GREEN,  fg=BG)
            self._btn_mirror.config(bg=DIM,    fg=CYAN)
            # Borde del canvas en verde en modo juego
            self.canvas.master.config(bg=GREEN)
        else:
            self._mode_label.config(text="◈ TRACKING", fg=CYAN)
            self._btn_mirror.config(bg=CYAN,  fg=BG)
            self._btn_game.config(  bg=DIM,   fg=GREEN)
            self.canvas.master.config(bg=CYAN)

    def set_status(self, texto):
        self._status_label.config(text=texto.upper() if texto else "---")

    def set_angles(self, angles: list[int]) -> None:
        """Actualiza las barras de ángulos. angles = lista de 6 ints 0-180."""
        for i, angle in enumerate(angles[:6]):
            bg_frame, fill_frame = self._angle_bars[i]
            bg_w  = 100
            fill_w = int(bg_w * angle / 180)
            fill_frame.place(x=0, y=0, width=fill_w, height=10)

            # Color de la barra según el valor del ángulo
            color = CYAN if angle < 120 else AMBER
            fill_frame.config(bg=color)

            self._angle_values[i].config(text=f"{angle:3d}°", fg=color)

    def set_connection(self, connected: bool) -> None:
        if connected:
            self._conn_label.config(text="◉ CONECTADO A LA RASP", fg=GREEN)
        else:
            self._conn_label.config(text="◉ SIN CONEXIÓN CON LA RASP", fg=RED)

    def after(self, ms: int, func) -> None:
        self.root.after(ms, func)

    def mainloop(self) -> None:
        self.root.mainloop()


    def _make_btn(self, parent, text, cmd, color, active):
        bg = color if active else DIM
        fg = BG    if active else color
        return tk.Button(
            parent, text=text, command=cmd,
            bg=bg, fg=fg,
            font=self._f_status,
            relief="flat", cursor="hand2",
            padx=10, pady=6, anchor="w",
            activebackground=color, activeforeground=BG,
            width=14,
        )

    # Cierra la ventana y llama al callback de limpieza
    def _handle_close(self):
        self._on_close()
        self.root.destroy()