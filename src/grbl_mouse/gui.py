"""Tkinter GUI for grbl_mouse — status display, on-screen jog controls,
gain/settings controls, and alarm/resume, so the app can be used/tested
without the physical Expert Mouse (per the original project brief).

Runs `app.run_gui_worker` on a background thread (reusing the same
VelocityJogController/GainControl/safety.py/connect-reconnect machinery as
the CLI path) and only ever touches Tkinter widgets from the main thread,
communicating with the worker via thread-safe queues — Tkinter itself is
not thread-safe, so the worker must never call back into widget code
directly.

Requires Tk support (`_tkinter`), which on Homebrew Python is a separate
formula from Python itself (e.g. `brew install python-tk@3.14`) — plain
`pip install` cannot provide it.
"""

from __future__ import annotations

import argparse
import queue
import threading
import tkinter as tk
from tkinter import ttk

from . import app as app_module

STATUS_POLL_MS = 100
LOG_MAX_LINES = 500
CLOSE_POLL_MS = 200


class GrblMouseGui:
    def __init__(self, root: tk.Tk, args: argparse.Namespace) -> None:
        self.root = root
        self.args = args
        self.jog_settings = app_module.JogSettings.from_args(args)
        self.gui_commands: "queue.Queue[tuple]" = queue.Queue()
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.status_queue: "queue.Queue[app_module.GuiStatus]" = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()

        root.title("grbl_mouse")
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_widgets()

        self.worker_thread = threading.Thread(target=self._worker_entry, daemon=True)
        self.worker_thread.start()

        self.root.after(STATUS_POLL_MS, self._poll)

    # --- worker thread entry point ---

    def _worker_entry(self) -> None:
        try:
            app_module.run_gui_worker(
                self.args,
                self.jog_settings,
                self.gui_commands,
                self._on_status,
                self._on_log,
                self.stop_event,
            )
        except SystemExit as e:
            self._on_log(f"\n{e}")
        except Exception as e:
            self._on_log(f"\nWorker thread crashed: {e!r}")

    # --- thread-safe callbacks (called FROM the worker thread) ---

    def _on_status(self, status: app_module.GuiStatus) -> None:
        try:
            self.status_queue.get_nowait()
        except queue.Empty:
            pass
        self.status_queue.put(status)

    def _on_log(self, message: str) -> None:
        self.log_queue.put(message)

    # --- widget construction (main thread only) ---

    def _build_widgets(self) -> None:
        pad = {"padx": 6, "pady": 4}

        status_frame = ttk.LabelFrame(self.root, text="Status")
        status_frame.grid(row=0, column=0, columnspan=2, sticky="ew", **pad)

        self.mode_var = tk.StringVar(value="LIVE" if self.args.confirm_motion else "DRY RUN")
        self.state_var = tk.StringVar(value="Connecting…")
        self.position_var = tk.StringVar(value="—")
        self.gain_var = tk.StringVar(value="—")
        self.alarm_var = tk.StringVar(value="")

        ttk.Label(status_frame, text="Mode:").grid(row=0, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.mode_var).grid(row=0, column=1, sticky="w")
        ttk.Label(status_frame, text="GRBL:").grid(row=1, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.state_var).grid(row=1, column=1, sticky="w")
        ttk.Label(status_frame, text="Position:").grid(row=2, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.position_var).grid(row=2, column=1, sticky="w")
        ttk.Label(status_frame, text="Gain:").grid(row=3, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.gain_var).grid(row=3, column=1, sticky="w")
        self.alarm_label = ttk.Label(status_frame, textvariable=self.alarm_var, foreground="red")
        self.alarm_label.grid(row=4, column=0, columnspan=2, sticky="w")

        self.resume_button = ttk.Button(status_frame, text="Resume", command=self._on_resume, state="disabled")
        self.resume_button.grid(row=5, column=0, sticky="ew", pady=(4, 0))
        self.stop_button = ttk.Button(status_frame, text="Stop", command=self._on_stop)
        self.stop_button.grid(row=5, column=1, sticky="ew", pady=(4, 0))

        jog_frame = ttk.LabelFrame(self.root, text="Jog (press and hold)")
        jog_frame.grid(row=1, column=0, sticky="nsew", **pad)
        self._build_jog_pad(jog_frame)

        gain_frame = ttk.LabelFrame(self.root, text="Gain")
        gain_frame.grid(row=2, column=0, sticky="ew", **pad)
        ttk.Button(gain_frame, text="-", command=lambda: self.gui_commands.put(("gain_decrease",))).grid(row=0, column=0, sticky="ew")
        ttk.Button(gain_frame, text="+", command=lambda: self.gui_commands.put(("gain_increase",))).grid(row=0, column=1, sticky="ew")
        gain_frame.columnconfigure(0, weight=1)
        gain_frame.columnconfigure(1, weight=1)

        settings_frame = ttk.LabelFrame(self.root, text="Settings")
        settings_frame.grid(row=1, column=1, rowspan=2, sticky="nsew", **pad)
        self._build_settings(settings_frame)

        log_frame = ttk.LabelFrame(self.root, text="Log")
        log_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", **pad)
        self.log_text = tk.Text(log_frame, height=12, width=70, state="disabled", wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.root.rowconfigure(3, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)

    def _build_jog_pad(self, parent: ttk.Frame) -> None:
        def bind_hold(widget: ttk.Button, direction: str) -> None:
            widget.bind("<ButtonPress-1>", lambda e: self.gui_commands.put(("jog_press", direction)))
            widget.bind("<ButtonRelease-1>", lambda e: self.gui_commands.put(("jog_release", direction)))
            # Also release if the mouse leaves the button while still held,
            # so dragging off it doesn't leave a direction stuck "on".
            widget.bind("<Leave>", lambda e: self.gui_commands.put(("jog_release", direction)))

        y_plus = ttk.Button(parent, text="Y+", width=4)
        y_plus.grid(row=0, column=1)
        bind_hold(y_plus, "y+")

        x_minus = ttk.Button(parent, text="X-", width=4)
        x_minus.grid(row=1, column=0)
        bind_hold(x_minus, "x-")

        x_plus = ttk.Button(parent, text="X+", width=4)
        x_plus.grid(row=1, column=2)
        bind_hold(x_plus, "x+")

        y_minus = ttk.Button(parent, text="Y-", width=4)
        y_minus.grid(row=2, column=1)
        bind_hold(y_minus, "y-")

        ttk.Separator(parent, orient="vertical").grid(row=0, column=3, rowspan=3, sticky="ns", padx=8)

        z_plus = ttk.Button(parent, text="Z+", width=4)
        z_plus.grid(row=0, column=4)
        bind_hold(z_plus, "z+")

        z_minus = ttk.Button(parent, text="Z-", width=4)
        z_minus.grid(row=2, column=4)
        bind_hold(z_minus, "z-")

    def _build_settings(self, parent: ttk.Frame) -> None:
        self.swap_xy_var = tk.BooleanVar(value=self.jog_settings.swap_xy)
        self.invert_x_var = tk.BooleanVar(value=self.jog_settings.invert_x)
        self.invert_y_var = tk.BooleanVar(value=self.jog_settings.invert_y)
        self.invert_z_var = tk.BooleanVar(value=self.jog_settings.invert_z)

        ttk.Checkbutton(
            parent,
            text="Swap X/Y",
            variable=self.swap_xy_var,
            command=lambda: self.gui_commands.put(("toggle_swap_xy",)),
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            parent,
            text="Invert X",
            variable=self.invert_x_var,
            command=lambda: self.gui_commands.put(("toggle_invert_x",)),
        ).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(
            parent,
            text="Invert Y",
            variable=self.invert_y_var,
            command=lambda: self.gui_commands.put(("toggle_invert_y",)),
        ).grid(row=2, column=0, sticky="w")
        ttk.Checkbutton(
            parent,
            text="Invert Z",
            variable=self.invert_z_var,
            command=lambda: self.gui_commands.put(("toggle_invert_z",)),
        ).grid(row=3, column=0, sticky="w")

    # --- commands from the main thread ---

    def _on_resume(self) -> None:
        self.gui_commands.put(("resume",))

    def _on_stop(self) -> None:
        self.gui_commands.put(("stop",))

    # --- polling (main thread, via .after) ---

    def _poll(self) -> None:
        try:
            while True:
                message = self.log_queue.get_nowait()
                self._append_log(message)
        except queue.Empty:
            pass

        try:
            status = self.status_queue.get_nowait()
        except queue.Empty:
            status = None
        if status is not None:
            self._apply_status(status)

        if not self.worker_thread.is_alive() and not self.stop_event.is_set():
            # Worker exited on its own (e.g. it aborted waiting for a
            # device, or crashed - see _worker_entry, which logs either
            # case before returning).
            self.state_var.set("Stopped")
            return

        self.root.after(STATUS_POLL_MS, self._poll)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        # Cap the log so it doesn't grow unbounded over a long session.
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > LOG_MAX_LINES:
            self.log_text.delete("1.0", f"{line_count - LOG_MAX_LINES}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _apply_status(self, status: app_module.GuiStatus) -> None:
        self.mode_var.set("LIVE" if status.confirm_motion else "DRY RUN")
        self.state_var.set(status.grbl_state or "—")
        if status.machine_position is not None:
            x, y, z = status.machine_position
            self.position_var.set(f"X{x:.3f} Y{y:.3f} Z{z:.3f}")
        self.gain_var.set(f"{status.gain:.3f}")
        if status.alarmed:
            self.alarm_var.set(f"PAUSED — {status.grbl_state}")
            self.resume_button.configure(state="normal")
        else:
            self.alarm_var.set("")
            self.resume_button.configure(state="disabled")

    # --- shutdown ---

    def _on_close(self) -> None:
        self.stop_event.set()
        self._finish_close()

    def _finish_close(self) -> None:
        # Give the worker a window to notice stop_event and exit cleanly
        # (cancel jog, close connections) before tearing the window down.
        # Bounded in practice: every blocking wait in run_gui_worker checks
        # stop_event at least once per its own poll interval (<=1s), and
        # the one unbounded-looking retry (transient-lag) has a fixed
        # attempt budget, so this always resolves.
        if self.worker_thread.is_alive():
            self.root.after(CLOSE_POLL_MS, self._finish_close)
            return
        self.root.destroy()


def run_gui(args: argparse.Namespace) -> int:
    root = tk.Tk()
    GrblMouseGui(root, args)
    root.mainloop()
    return 0
