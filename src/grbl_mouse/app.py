"""CLI entry point wiring HID input -> presets -> GRBL jog dispatch.

Uses the velocity-based jogging model ("Option B" — see grbl_link/
velocity_jog.py): feed rate is held fixed (presets.FIXED_FEEDRATE), and
buttons adjust a gain multiplier instead of feed rate.

Failure modes this loop is specifically built to handle, beyond the core
jog-dispatch safety already in velocity_jog.py:

- **GRBL not responding at all on connect** (wrong --port, board unpowered
  or held in reset e.g. by an e-stop that cuts power, or wrong --baudrate —
  these are indistinguishable from here; see serial_link.py's welcome-
  timeout message).
- **GRBL alarm/hold/door states**, proactively detected (a periodic status
  poll, not just reactively when a jog happens to fail) and human-readable
  (see serial_link.py's ALARM_DESCRIPTIONS) — jogging pauses and a button
  press attempts the state-appropriate recovery ($X for Alarm, `~` for
  Hold; Door needs the physical door closed first) rather than crashing.
- **HID device disconnect**, detected proactively by periodically
  re-checking the device is still enumerated (not by waiting for a read to
  fail, since normal idle - no reports arriving - looks identical from
  inside a single read call).
- **HID device reconnect**: once disconnected, this loop waits and
  auto-resumes when the device reappears — HID is just an input device, so
  this is judged lower-risk than auto-resuming after a *serial* disconnect
  (see below) would be.
- **Two identical HID devices**: device selection requires an exact,
  unambiguous match (vendor/product/usage, optionally + --hid-serial); if
  more than one candidate matches, this refuses to guess, both at startup
  and while waiting to reconnect.
- **Total GRBL communication loss** (e.g. a power-cutting e-stop — confirmed
  on a serial monitor that this board sends nothing at all while e-stopped
  and a fresh welcome banner once released): waits and redoes the full
  handshake (fresh welcome, fresh status) rather than exiting, same
  philosophy as the HID reconnect — but jogging stays paused afterward
  until an explicit button press, since a full board reset means position
  tracking is definitely invalid (unlike HID, which is just an input
  device with no state of its own to re-validate).
- **Transient GRBL lag vs. a real reset**: a hard-limit halt doesn't reboot
  the board, so a bare timeout is retried a few times on the *existing*
  connection first (see GRBL_LAG_RETRY_ATTEMPTS) — escalating straight to
  "wait for a fresh welcome banner" would hang forever on a board that
  never actually reset and so never sends one.
- **Any other unexpected exception**: still attempts a jog-cancel before
  the program exits (see the broad `except Exception` below) — an earlier
  version only caught GrblError/GrblAlarm/KeyboardInterrupt, so a
  TimeoutError from an overloaded GRBL crashed the program without ever
  trying to stop the machine.

Motion is only ever sent when --confirm-motion is passed; without it, ball
motion, wheel ticks, and button presses are decoded and printed but never
written to GRBL — same convention as debug_jog.py.
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import safety
from .grbl_link.jog import cancel_jog, resume_hold, send_jog, unlock_alarm
from .grbl_link.pyserial_transport import PySerialTransport
from .grbl_link.serial_link import GrblAlarm, GrblError, GrblReset, SerialLink, Welcome
from .grbl_link.status import query_status
from .grbl_link.velocity_jog import CancelAction, SendAction, VelocityJogController
from .hid_input import cli_common
from .hid_input.backend import HidDeviceInfo
from .hid_input.hidapi_backend import HidApiBackend
from .hid_input.report_parser import BUTTON_TOP_LEFT, decode
from .presets import (
    FIXED_FEEDRATE,
    Z_GAIN_MULTIPLIER,
    ButtonPressDetector,
    GainControl,
    handle_buttons,
)

# Known values for this specific Expert Mouse unit (see hid_input/report_parser.py).
DEFAULT_HID_VENDOR = 0x047D
DEFAULT_HID_PRODUCT = 0x1020
DEFAULT_HID_USAGE_PAGE = 0x0001
DEFAULT_HID_USAGE = 0x02

BUTTON_RESUME_ALARM = BUTTON_TOP_LEFT

# GRBL states in which jogging is blocked. Hold/Door can carry a ":N"
# substate suffix (e.g. "Hold:0") - always compare the base name.
PAUSED_STATES = {"Alarm", "Hold", "Door"}

POLL_INTERVAL_S = 0.15  # how often we check input & send updated jog commands

# Derived from this machine's real $110-$122 settings (see debug_settings.py):
# at FIXED_FEEDRATE=500mm/min (8.33mm/s), Y is the limiting axis at
# $121=15mm/sec^2 acceleration, so time-to-cruise ~= 0.556s. A segment
# needs to be *sized* for at least this much motion to actually reach
# cruise speed - this also converges closely with GRBL-Plotter's
# independently-established 0.5s default for the same problem.
SEGMENT_DURATION_S = 0.5

OVERSHOOT_FACTOR = SEGMENT_DURATION_S / POLL_INTERVAL_S
MAX_SEGMENT_DISTANCE_MM = (FIXED_FEEDRATE / 60.0) * SEGMENT_DURATION_S

SERIAL_TIMEOUT_S = 0.5  # shorter than the debug tools' default, so a stuck GRBL is noticed fast

MAINTENANCE_INTERVAL_S = 1.0  # cadence for the proactive HID-presence + GRBL-status checks
HID_RECONNECT_POLL_S = 0.5

GRBL_RECONNECT_POLL_S = 1.0  # pause between connection attempts while waiting for GRBL
GRBL_WELCOME_TIMEOUT_S = 10.0  # per-attempt wait for a welcome banner once the port is open

# A hard-limit halt (etc.) doesn't reboot the board - it just needs a
# moment to settle before reliably answering '?' again. A TimeoutError
# alone doesn't distinguish that from a full reset (e.g. a power-cutting
# e-stop, confirmed via serial monitor to go completely silent), so retry
# on the *existing* connection a few times first: escalating straight to a
# full reconnect (which waits for a fresh welcome banner) would hang
# forever on a board that never actually reset and so never prints one.
GRBL_LAG_RETRY_ATTEMPTS = 3
GRBL_LAG_RETRY_INTERVAL_S = 1.0


def _base_state(state: str) -> str:
    return state.split(":", 1)[0]


def _matching_hid_devices(
    devices: list[HidDeviceInfo],
    *,
    vendor_id: int,
    product_id: int,
    usage_page: int,
    usage: int,
    serial_number: str | None,
) -> list[HidDeviceInfo]:
    return [
        d
        for d in devices
        if d.vendor_id == vendor_id
        and d.product_id == product_id
        and d.usage_page == usage_page
        and d.usage == usage
        and (serial_number is None or d.serial_number == serial_number)
    ]


def _select_hid_device(
    args: argparse.Namespace,
    emit: Callable[[str], None] = print,
    stop_event: threading.Event | None = None,
) -> HidDeviceInfo | None:
    """Returns None only if `stop_event` was set while waiting (GUI mode's
    way of aborting a wait without relying on KeyboardInterrupt, which
    doesn't reliably interrupt a background thread).
    """
    devices = HidApiBackend().list_devices()
    matches = _matching_hid_devices(
        devices,
        vendor_id=args.hid_vendor,
        product_id=args.hid_product,
        usage_page=args.hid_usage_page,
        usage=args.hid_usage,
        serial_number=args.hid_serial,
    )
    if len(matches) == 1:
        return matches[0]

    criteria = (
        f"vendor=0x{args.hid_vendor:04x} product=0x{args.hid_product:04x} "
        f"usage_page=0x{args.hid_usage_page:04x} usage=0x{args.hid_usage:02x}"
    )
    if args.hid_serial:
        criteria += f" serial={args.hid_serial!r}"

    if len(matches) > 1:
        detail = "\n".join(cli_common.format_device(i, d) for i, d in enumerate(devices))
        raise SystemExit(
            f"expected exactly one HID device matching {criteria}, found {len(matches)}.\n"
            f"Multiple identical devices found — pass --hid-serial to disambiguate.\n"
            f"All devices:\n{detail}"
        )

    # Zero matches: don't hard-fail on a device that just isn't plugged in
    # yet — wait for it, same as a mid-session reconnect. Ambiguity (>1
    # match, above) is different: that needs a decision only the user can
    # make, so it exits rather than waiting.
    emit(f"No HID device found yet matching {criteria}.")
    emit("Waiting for it to be connected... (Ctrl+C to abort)")
    return _wait_for_hid_reconnect(args, emit=emit, stop_event=stop_event)


def _hid_device_present(args: argparse.Namespace) -> bool:
    matches = _matching_hid_devices(
        HidApiBackend().list_devices(),
        vendor_id=args.hid_vendor,
        product_id=args.hid_product,
        usage_page=args.hid_usage_page,
        usage=args.hid_usage,
        serial_number=args.hid_serial,
    )
    return len(matches) >= 1


def _wait_for_hid_reconnect(
    args: argparse.Namespace,
    emit: Callable[[str], None] = print,
    stop_event: threading.Event | None = None,
) -> HidDeviceInfo | None:
    """Returns None only if `stop_event` was set while waiting."""
    ambiguity_warned = False
    while True:
        if stop_event is not None and stop_event.is_set():
            return None
        matches = _matching_hid_devices(
            HidApiBackend().list_devices(),
            vendor_id=args.hid_vendor,
            product_id=args.hid_product,
            usage_page=args.hid_usage_page,
            usage=args.hid_usage,
            serial_number=args.hid_serial,
        )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1 and not ambiguity_warned:
            emit(
                f"  {len(matches)} matching devices found — can't tell which one to "
                "reconnect to. Unplug all but one, or pass --hid-serial to disambiguate."
            )
            ambiguity_warned = True
        elif len(matches) <= 1:
            ambiguity_warned = False
        time.sleep(HID_RECONNECT_POLL_S)


def _read_poll_reports(hid_backend: HidApiBackend, poll_interval: float) -> list[bytes]:
    """Collect HID reports arriving within one bounded poll window."""
    reports: list[bytes] = []
    deadline = time.monotonic() + poll_interval
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        raw = hid_backend.read(timeout_ms=max(1, int(remaining * 1000)))
        if raw is None:
            break
        reports.append(raw)
    return reports


def _clamp(delta: float) -> float:
    return max(-MAX_SEGMENT_DISTANCE_MM, min(MAX_SEGMENT_DISTANCE_MM, delta))


def _safe_cancel(link: SerialLink) -> None:
    try:
        cancel_jog(link)
    except Exception:
        pass


def _safe_close_hid(hid_backend: HidApiBackend) -> None:
    try:
        hid_backend.close()
    except Exception:
        pass


def _handle_hid_disconnect(
    args: argparse.Namespace,
    link: SerialLink,
    hid_backend: HidApiBackend,
    emit: Callable[[str], None] = print,
    stop_event: threading.Event | None = None,
) -> tuple[HidApiBackend, ButtonPressDetector, VelocityJogController] | None:
    """Cancel any active jog, close the (now-dead) HID handle, wait for a
    matching device to reappear, and return a fresh backend/detector/
    controller for it. Shared by both the proactive periodic check and the
    exception-triggered path (a read on an already-removed device tends to
    raise immediately — faster than the periodic check's ~1s cadence would
    otherwise catch it). Returns None only if `stop_event` was set while
    waiting.
    """
    emit("\nHID device disconnected — pausing motion and waiting to reconnect...")
    _safe_cancel(link)
    _safe_close_hid(hid_backend)
    device_info = _wait_for_hid_reconnect(args, emit=emit, stop_event=stop_event)
    if device_info is None:
        return None
    new_backend = HidApiBackend()
    new_backend.open(device_info)
    emit("HID device reconnected. Resuming.\n")
    return new_backend, ButtonPressDetector(), VelocityJogController(deadman_timeout=args.deadman_timeout)


def _connect_to_grbl(
    args: argparse.Namespace,
    emit: Callable[[str], None] = print,
    stop_event: threading.Event | None = None,
) -> tuple[PySerialTransport, SerialLink, Welcome] | None:
    """Open the serial port and wait for GRBL's welcome banner, retrying
    indefinitely (Ctrl+C to abort, or `stop_event` for GUI mode) rather
    than failing on the first attempt. Used both at startup and to recover
    from a total communication loss — confirmed on a serial monitor: this
    board sends nothing at all while e-stopped (a power-cutting e-stop,
    not just a GRBL-level alarm) and a fresh welcome banner once released.
    Handles both the port staying available the whole time (only GRBL's
    logic resets) and the whole USB device disappearing and reappearing (a
    fresh port-open attempt every retry). Returns None only if
    `stop_event` was set while waiting.
    """
    printed_waiting = False
    while True:
        if stop_event is not None and stop_event.is_set():
            return None
        try:
            transport = PySerialTransport(args.port, baudrate=args.baudrate, timeout=SERIAL_TIMEOUT_S)
        except Exception:
            if not printed_waiting:
                emit(f"Waiting for GRBL on {args.port}... (Ctrl+C to abort)")
                printed_waiting = True
            time.sleep(GRBL_RECONNECT_POLL_S)
            continue

        link = SerialLink(transport)
        try:
            welcome = link.read_welcome(timeout=GRBL_WELCOME_TIMEOUT_S)
        except (TimeoutError, OSError):
            transport.close()
            if not printed_waiting:
                emit(f"Waiting for GRBL on {args.port}... (Ctrl+C to abort)")
                printed_waiting = True
            time.sleep(GRBL_RECONNECT_POLL_S)
            continue

        return transport, link, welcome


def _handle_grbl_disconnect(
    args: argparse.Namespace,
    transport: PySerialTransport,
    link: SerialLink,
    emit: Callable[[str], None] = print,
    stop_event: threading.Event | None = None,
) -> tuple[PySerialTransport, SerialLink, bool] | None:
    """Close the (now-dead) GRBL connection, wait for it to come back
    (redoing the full handshake — port open + fresh welcome banner), and
    return a fresh (transport, link, alarmed). `alarmed` is recomputed
    from the fresh status, same as at startup, so jogging stays paused
    until the user explicitly resumes — a full board reset (e.g. a
    power-cutting e-stop) means position tracking is definitely invalid.
    Returns None only if `stop_event` was set while waiting.
    """
    emit("\nGRBL connection lost — waiting for it to reconnect...")
    _safe_cancel(link)
    try:
        transport.close()
    except Exception:
        pass

    reconnected = _connect_to_grbl(args, emit=emit, stop_event=stop_event)
    if reconnected is None:
        return None
    new_transport, new_link, welcome = reconnected
    emit(f"GRBL reconnected: {welcome.raw}")
    report = query_status(new_link)
    emit(f"Status: {report.state}  MPos={report.machine_position}")
    alarmed = _base_state(report.state) in PAUSED_STATES
    if alarmed:
        emit(f"Still in {report.state} — press the top-left button to attempt to resume.")
    else:
        emit("Resumed.")
    return new_transport, new_link, alarmed


def _status_survives_transient_lag(link: SerialLink) -> bool:
    """Retry a status query a few times on the existing connection, for a
    board that's still running but briefly unresponsive (see
    GRBL_LAG_RETRY_ATTEMPTS). Returns True as soon as GRBL answers at
    all — including with an alarm/error, since that still proves the link
    itself is alive. Returns False (give up, caller should do a full
    reconnect) on a confirmed GrblReset (no point burning remaining
    retries once we know for certain it's a real reset, not just lag) or
    after repeated TimeoutError/OSError.
    """
    for _ in range(GRBL_LAG_RETRY_ATTEMPTS):
        time.sleep(GRBL_LAG_RETRY_INTERVAL_S)
        try:
            query_status(link)
        except GrblReset:
            return False
        except (TimeoutError, OSError):
            continue
        except (GrblError, GrblAlarm):
            return True  # link is alive, even though the query itself found an alarm
        else:
            return True
    return False


def _attempt_resume(link: SerialLink, emit: Callable[[str], None] = print) -> bool:
    """Attempt to recover from a paused (Alarm/Hold/Door) state, using the
    state-appropriate real-time command. Returns the new `alarmed` value
    (True if still paused).
    """
    report = query_status(link)
    base = _base_state(report.state)

    if base == "Alarm":
        emit("Sending $X to clear the alarm...")
        try:
            unlock_alarm(link)
        except (GrblError, GrblAlarm) as e:
            emit(f"Unlock failed: {e}")
    elif base == "Hold":
        emit("Sending resume (~)...")
        try:
            resume_hold(link)
        except (GrblError, GrblAlarm) as e:
            emit(f"Resume failed: {e}")
    elif base == "Door":
        emit("GRBL is in a Door state — close the safety door, then press top-left again.")
        return True

    report = query_status(link)
    emit(f"Status: {report.state}  MPos={report.machine_position}")
    still_paused = _base_state(report.state) in PAUSED_STATES
    if still_paused:
        emit(f"Still {report.state} — check the machine before trying again.")
    return still_paused


GUI_JOG_DIRECTIONS = ("x+", "x-", "y+", "y-", "z+", "z-")

# Fixed per-tick jog distance for on-screen buttons, deliberately
# independent of gain — clicking/holding a GUI button should feel the
# same regardless of the ball's current sensitivity setting.
GUI_JOG_MM_PER_TICK = 0.5


@dataclass
class JogSettings:
    """Mutable runtime settings the GUI can toggle live. The CLI equivalents
    are static argparse flags (--swap-xy/--invert-x/--invert-y/--invert-z);
    this exists so the GUI can change them without restarting the process.
    """

    swap_xy: bool
    invert_x: bool
    invert_y: bool
    invert_z: bool

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "JogSettings":
        return cls(
            swap_xy=args.swap_xy,
            invert_x=args.invert_x,
            invert_y=args.invert_y,
            invert_z=args.invert_z,
        )


@dataclass(frozen=True)
class GuiStatus:
    grbl_state: str | None
    machine_position: tuple[float, float, float] | None
    gain: float
    alarmed: bool
    confirm_motion: bool


def run_gui_worker(
    args: argparse.Namespace,
    jog_settings: JogSettings,
    gui_commands: "queue.Queue[tuple]",
    status_sink: Callable[[GuiStatus], None],
    emit: Callable[[str], None],
    stop_event: threading.Event,
) -> None:
    """GUI-mode counterpart to `_run`'s main loop — runs on a background
    thread, driven by gui.py.

    Reuses the actual safety-critical logic unchanged: VelocityJogController,
    GainControl, safety.py's motion gate, jog.py, and the connect/reconnect
    helpers above. Kept as a separate loop rather than retrofitting
    threading into `_run` itself, since that loop is hardware-hardened
    through several rounds of real incidents (see velocity_jog.py) and
    supporting two very different execution models (blocking CLI vs.
    threaded GUI) in one function risked regressing it. Only the
    orchestration wiring differs here: `stop_event` is checked throughout
    (a background thread can't rely on KeyboardInterrupt the way the CLI
    loop does), GUI-originated commands are drained each tick alongside HID
    reports, and status is reported via `status_sink`/`emit` instead of a
    live-printed display.
    """
    held_directions: set[str] = set()

    def drain_gui_commands(gain_control: GainControl) -> bool:
        """Apply all pending GUI commands. Returns True if Resume was clicked."""
        resume_clicked = False
        while True:
            try:
                cmd = gui_commands.get_nowait()
            except queue.Empty:
                break
            if cmd[0] == "jog_press":
                held_directions.add(cmd[1])
            elif cmd[0] == "jog_release":
                held_directions.discard(cmd[1])
            elif cmd[0] == "stop":
                held_directions.clear()
            elif cmd[0] == "gain_increase":
                gain_control.increase()
            elif cmd[0] == "gain_decrease":
                gain_control.decrease()
            elif cmd[0] == "resume":
                resume_clicked = True
            elif cmd[0] == "toggle_swap_xy":
                jog_settings.swap_xy = not jog_settings.swap_xy
            elif cmd[0] == "toggle_invert_x":
                jog_settings.invert_x = not jog_settings.invert_x
            elif cmd[0] == "toggle_invert_y":
                jog_settings.invert_y = not jog_settings.invert_y
            elif cmd[0] == "toggle_invert_z":
                jog_settings.invert_z = not jog_settings.invert_z
        return resume_clicked

    device_info = _select_hid_device(args, emit=emit, stop_event=stop_event)
    if device_info is None:
        return
    hid_backend = HidApiBackend()
    hid_backend.open(device_info)

    connected = _connect_to_grbl(args, emit=emit, stop_event=stop_event)
    if connected is None:
        hid_backend.close()
        return
    transport, link, welcome = connected

    gain_control = GainControl()
    detector = ButtonPressDetector()
    controller = VelocityJogController(deadman_timeout=args.deadman_timeout)
    alarmed = False

    try:
        emit(f"GRBL: {welcome.raw}")
        report = query_status(link)
        emit(f"Status: {report.state}  MPos={report.machine_position}")

        if args.confirm_motion:
            safety.enable_motion()
            emit("\nMotion explicitly authorized for this run.")
            if args.unlock:
                unlock_alarm(link)
                report = query_status(link)
                emit(f"Status after unlock: {report.state}  MPos={report.machine_position}")
            alarmed = _base_state(report.state) in PAUSED_STATES
            if alarmed:
                emit(f"Still in {report.state} — use Resume to attempt recovery.")
        else:
            emit("\n--confirm-motion not given: DRY RUN. Jogs are logged, not sent.")

        last_maintenance = time.monotonic()

        while not stop_event.is_set():
            try:
                reports = _read_poll_reports(hid_backend, args.poll_interval)
            except OSError as e:
                emit(f"\nHID read error ({e}) — treating as disconnect.")
                result = _handle_hid_disconnect(args, link, hid_backend, emit=emit, stop_event=stop_event)
                if result is None:
                    break
                hid_backend, detector, controller = result
                continue

            dx_raw = dy_raw = dz_raw = 0.0
            hid_resume_requested = False

            for raw in reports:
                mouse_report = decode(raw)
                newly_pressed = detector.pressed_since_last(mouse_report.buttons)

                new_gain = handle_buttons(gain_control, newly_pressed)
                if new_gain is not None:
                    emit(f"Gain -> {new_gain:.3f}")
                if alarmed and (newly_pressed & BUTTON_RESUME_ALARM):
                    hid_resume_requested = True

                if jog_settings.swap_xy:
                    grbl_dx_raw, grbl_dy_raw = mouse_report.dy, mouse_report.dx
                else:
                    grbl_dx_raw, grbl_dy_raw = mouse_report.dx, mouse_report.dy
                dx_raw += grbl_dx_raw * (-1 if jog_settings.invert_x else 1)
                dy_raw += grbl_dy_raw * (-1 if jog_settings.invert_y else 1)
                dz_raw += mouse_report.wheel * (-1 if jog_settings.invert_z else 1)

            gui_resume_clicked = drain_gui_commands(gain_control)
            resume_requested = alarmed and (hid_resume_requested or gui_resume_clicked)

            try:
                if resume_requested and args.confirm_motion:
                    alarmed = _attempt_resume(link, emit=emit)
                    if not alarmed:
                        controller = VelocityJogController(deadman_timeout=args.deadman_timeout)
                        emit("Resumed.")

                now_check = time.monotonic()
                if now_check - last_maintenance >= MAINTENANCE_INTERVAL_S:
                    last_maintenance = now_check

                    if args.confirm_motion and not alarmed:
                        try:
                            report = query_status(link)
                        except (GrblError, GrblAlarm) as e:
                            alarmed = True
                            _safe_cancel(link)
                            emit(f"\nGRBL reported: {e}")
                            emit("Pausing. Use Resume to attempt recovery.")
                        else:
                            base = _base_state(report.state)
                            if base in PAUSED_STATES:
                                alarmed = True
                                _safe_cancel(link)
                                emit(f"\nGRBL entered {report.state} — pausing. Use Resume to attempt recovery.")

                    if not _hid_device_present(args):
                        result = _handle_hid_disconnect(args, link, hid_backend, emit=emit, stop_event=stop_event)
                        if result is None:
                            break
                        hid_backend, detector, controller = result

                status_sink(
                    GuiStatus(
                        grbl_state=report.state,
                        machine_position=report.machine_position,
                        gain=gain_control.current,
                        alarmed=alarmed,
                        confirm_motion=args.confirm_motion,
                    )
                )

                if alarmed:
                    continue

                gain = gain_control.current
                gui_dx = GUI_JOG_MM_PER_TICK * (("x+" in held_directions) - ("x-" in held_directions))
                gui_dy = GUI_JOG_MM_PER_TICK * (("y+" in held_directions) - ("y-" in held_directions))
                gui_dz = GUI_JOG_MM_PER_TICK * (("z+" in held_directions) - ("z-" in held_directions))

                dx = _clamp(dx_raw * gain * OVERSHOOT_FACTOR + gui_dx)
                dy = _clamp(dy_raw * gain * OVERSHOOT_FACTOR + gui_dy)
                dz = _clamp(dz_raw * gain * Z_GAIN_MULTIPLIER * OVERSHOOT_FACTOR + gui_dz)

                now = time.monotonic()
                had_input = bool(reports) or bool(held_directions)
                for action in controller.tick(dx, dy, dz, had_input=had_input, now=now):
                    if isinstance(action, SendAction):
                        if args.confirm_motion:
                            try:
                                send_jog(link, dx=action.dx, dy=action.dy, dz=action.dz, feedrate=FIXED_FEEDRATE)
                                emit(f"jog dx={action.dx:.3f} dy={action.dy:.3f} dz={action.dz:.3f} F={FIXED_FEEDRATE:g}")
                            except (GrblError, GrblAlarm) as e:
                                emit(f"GRBL reported: {e}")
                                _safe_cancel(link)
                                alarmed = True
                                break
                        else:
                            emit(f"[dry run] would jog dx={action.dx:.3f} dy={action.dy:.3f} dz={action.dz:.3f}")
                    elif isinstance(action, CancelAction):
                        if args.confirm_motion:
                            cancel_jog(link)
                            emit("jog-cancel")
                        else:
                            emit("[dry run] would send jog-cancel")

            except (GrblReset, OSError):
                result = _handle_grbl_disconnect(args, transport, link, emit=emit, stop_event=stop_event)
                if result is None:
                    break
                transport, link, alarmed = result
                controller = VelocityJogController(deadman_timeout=args.deadman_timeout)
            except TimeoutError:
                emit("\nGRBL didn't respond — retrying on the existing connection in case it's a transient lag...")
                if _status_survives_transient_lag(link):
                    emit("GRBL responded again — continuing without a full reconnect.\n")
                else:
                    result = _handle_grbl_disconnect(args, transport, link, emit=emit, stop_event=stop_event)
                    if result is None:
                        break
                    transport, link, alarmed = result
                    controller = VelocityJogController(deadman_timeout=args.deadman_timeout)

    except Exception:
        emit("\nUnexpected error in GUI worker — stopping.")
        raise
    finally:
        _safe_cancel(link)
        safety.disable_motion()
        _safe_close_hid(hid_backend)
        try:
            transport.close()
        except Exception:
            pass
        emit("\nStopped.")


def _run(args: argparse.Namespace) -> int:
    try:
        device_info = _select_hid_device(args)
    except KeyboardInterrupt:
        print("\nAborted waiting for HID device.")
        return 1
    hid_backend = HidApiBackend()
    hid_backend.open(device_info)

    try:
        transport, link, welcome = _connect_to_grbl(args)
    except KeyboardInterrupt:
        print("\nAborted waiting for GRBL.")
        hid_backend.close()
        return 1

    gain_control = GainControl()
    detector = ButtonPressDetector()
    controller = VelocityJogController(deadman_timeout=args.deadman_timeout)
    alarmed = False

    try:
        print(f"GRBL: {welcome.raw}")
        report = query_status(link)
        print(f"Status: {report.state}  MPos={report.machine_position}")
        print(
            f"Feed: {FIXED_FEEDRATE:g} mm/min (fixed)  Gain: {gain_control.current:.3f}  "
            f"Poll: {args.poll_interval*1000:.0f}ms  Segment: {SEGMENT_DURATION_S*1000:.0f}ms"
        )

        if args.confirm_motion:
            safety.enable_motion()
            print("\nMotion explicitly authorized for this run.")
            if args.unlock:
                unlock_alarm(link)
                report = query_status(link)
                print(f"Status after unlock: {report.state}  MPos={report.machine_position}")
            alarmed = _base_state(report.state) in PAUSED_STATES
            if alarmed:
                print(f"Still in {report.state} — press the top-left button to attempt to resume.")
        else:
            print("\n--confirm-motion not given: DRY RUN. Jogs are logged, not sent.")

        print("\nRoll the ball / scroll ring / press buttons. Ctrl+C to stop.\n")

        last_maintenance = time.monotonic()

        while True:
            try:
                reports = _read_poll_reports(hid_backend, args.poll_interval)
            except OSError as e:
                print(f"\nHID read error ({e}) — treating as disconnect.")
                hid_backend, detector, controller = _handle_hid_disconnect(args, link, hid_backend)
                continue

            dx_raw = dy_raw = dz_raw = 0.0
            resume_requested = False

            for raw in reports:
                mouse_report = decode(raw)
                newly_pressed = detector.pressed_since_last(mouse_report.buttons)

                new_gain = handle_buttons(gain_control, newly_pressed)
                if new_gain is not None:
                    print(f"Gain -> {new_gain:.3f}")
                if alarmed and (newly_pressed & BUTTON_RESUME_ALARM):
                    resume_requested = True

                if args.swap_xy:
                    grbl_dx_raw, grbl_dy_raw = mouse_report.dy, mouse_report.dx
                else:
                    grbl_dx_raw, grbl_dy_raw = mouse_report.dx, mouse_report.dy
                dx_raw += grbl_dx_raw * (-1 if args.invert_x else 1)
                dy_raw += grbl_dy_raw * (-1 if args.invert_y else 1)
                dz_raw += mouse_report.wheel * (-1 if args.invert_z else 1)

            try:
                if resume_requested and args.confirm_motion:
                    alarmed = _attempt_resume(link)
                    if not alarmed:
                        controller = VelocityJogController(deadman_timeout=args.deadman_timeout)
                        print("Resumed.")

                now_check = time.monotonic()
                if now_check - last_maintenance >= MAINTENANCE_INTERVAL_S:
                    last_maintenance = now_check

                    if args.confirm_motion and not alarmed:
                        try:
                            report = query_status(link)
                        except (GrblError, GrblAlarm) as e:
                            # GRBL can push these unprompted (e.g. a hard-limit
                            # trip isn't necessarily the direct reply to
                            # whatever we last sent) - query_status_line()
                            # surfaces them instead of silently discarding them
                            # while scanning for a status line.
                            alarmed = True
                            _safe_cancel(link)
                            print(f"\nGRBL reported: {e}")
                            print("Pausing. Press top-left to attempt to resume.")
                        else:
                            base = _base_state(report.state)
                            if base in PAUSED_STATES:
                                alarmed = True
                                _safe_cancel(link)
                                print(f"\nGRBL entered {report.state} — pausing. Press top-left to attempt to resume.")

                    if not _hid_device_present(args):
                        # Backstop for a disconnect that a read() call didn't
                        # itself raise on (see _handle_hid_disconnect — the
                        # main path is the except OSError above, which reacts
                        # immediately rather than waiting for this ~1s check).
                        hid_backend, detector, controller = _handle_hid_disconnect(args, link, hid_backend)

                if alarmed:
                    continue

                gain = gain_control.current
                dx = _clamp(dx_raw * gain * OVERSHOOT_FACTOR)
                dy = _clamp(dy_raw * gain * OVERSHOOT_FACTOR)
                dz = _clamp(dz_raw * gain * Z_GAIN_MULTIPLIER * OVERSHOOT_FACTOR)

                now = time.monotonic()
                for action in controller.tick(dx, dy, dz, had_input=bool(reports), now=now):
                    if isinstance(action, SendAction):
                        if args.confirm_motion:
                            try:
                                send_jog(link, dx=action.dx, dy=action.dy, dz=action.dz, feedrate=FIXED_FEEDRATE)
                                print(f"jog dx={action.dx:.3f} dy={action.dy:.3f} dz={action.dz:.3f} F={FIXED_FEEDRATE:g}")
                            except (GrblError, GrblAlarm) as e:
                                print(f"GRBL reported: {e}")
                                _safe_cancel(link)
                                alarmed = True
                                break
                        else:
                            print(f"[dry run] would jog dx={action.dx:.3f} dy={action.dy:.3f} dz={action.dz:.3f}")
                    elif isinstance(action, CancelAction):
                        if args.confirm_motion:
                            cancel_jog(link)
                            print("jog-cancel")
                        else:
                            print("[dry run] would send jog-cancel")

            except (GrblReset, OSError):
                # Confirmed (GrblReset) or likely (OSError, e.g. "Device
                # not configured") total communication loss - wait for
                # GRBL to come back and redo the handshake, rather than
                # exiting. Jogging stays paused until an explicit resume.
                transport, link, alarmed = _handle_grbl_disconnect(args, transport, link)
                controller = VelocityJogController(deadman_timeout=args.deadman_timeout)
            except TimeoutError:
                # Ambiguous on its own - could be the same total loss as
                # above (e.g. a power-cutting e-stop, confirmed via serial
                # monitor to go completely silent), or just a board that's
                # still running but briefly unresponsive (e.g. settling
                # right after a hard-limit halt). Try the existing
                # connection a few times before assuming it's really gone.
                print("\nGRBL didn't respond — retrying on the existing connection in case "
                      "it's a transient lag rather than a full reset...")
                if _status_survives_transient_lag(link):
                    print("GRBL responded again — continuing without a full reconnect.\n")
                else:
                    transport, link, alarmed = _handle_grbl_disconnect(args, transport, link)
                    controller = VelocityJogController(deadman_timeout=args.deadman_timeout)

    except KeyboardInterrupt:
        print("\nStopping — sending jog-cancel.")
        _safe_cancel(link)
    except (GrblError, GrblAlarm) as e:
        print(f"GRBL reported: {e}")
        _safe_cancel(link)
        return 1
    except GrblReset as e:
        # Backstop only — communication loss during normal operation is
        # handled inside the main loop (see _handle_grbl_disconnect), which
        # waits and reconnects rather than exiting. This fires only for the
        # narrow pre-loop window (the initial unlock/status calls right
        # after connecting).
        print(f"\n{e}")
        print("The board itself appears to have restarted before the main loop even "
              "started. Exiting; re-run to reconnect with a fresh handshake.")
        _safe_cancel(link)
        return 1
    except TimeoutError as e:
        print(f"\nNo response from GRBL: {e}")
        print("(This is a startup-only backstop — communication loss once running is "
              "handled by waiting and reconnecting, not exiting.)")
        _safe_cancel(link)
        return 1
    except OSError as e:
        print(f"\nLost connection: {e}")
        print("(This is a startup-only backstop — communication loss once running is "
              "handled by waiting and reconnecting, not exiting.)")
        _safe_cancel(link)
        return 1
    except Exception:
        print("\nUnexpected error — sending jog-cancel before stopping.")
        _safe_cancel(link)
        raise
    finally:
        safety.disable_motion()
        hid_backend.close()
        transport.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", required=True, help="GRBL serial port, e.g. /dev/cu.usbmodem13201")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--hid-vendor", type=lambda s: int(s, 0), default=DEFAULT_HID_VENDOR)
    parser.add_argument("--hid-product", type=lambda s: int(s, 0), default=DEFAULT_HID_PRODUCT)
    parser.add_argument("--hid-usage-page", type=lambda s: int(s, 0), default=DEFAULT_HID_USAGE_PAGE)
    parser.add_argument("--hid-usage", type=lambda s: int(s, 0), default=DEFAULT_HID_USAGE)
    parser.add_argument(
        "--hid-serial",
        default=None,
        help="disambiguate two identical HID devices by exact serial number",
    )
    parser.add_argument(
        "--confirm-motion",
        action="store_true",
        help="required to actually send jogs; without it, everything is a logged dry run",
    )
    parser.add_argument("--unlock", action="store_true", help="send $X to clear an Alarm state at startup")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=POLL_INTERVAL_S,
        help=f"how often to check input and send updated jogs, in seconds (default {POLL_INTERVAL_S})",
    )
    parser.add_argument(
        "--deadman-timeout",
        type=float,
        default=None,
        help="force-cancel if no HID input arrives for this long (default: 3x --poll-interval)",
    )
    parser.add_argument(
        "--swap-xy",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="ball dx drives GRBL Y and ball dy drives GRBL X (default: on, matches this machine's wiring)",
    )
    parser.add_argument("--invert-x", action="store_true", help="invert the GRBL X jog direction")
    parser.add_argument("--invert-y", action="store_true", help="invert the GRBL Y jog direction")
    parser.add_argument("--invert-z", action="store_true", help="invert the GRBL Z jog direction")
    parser.add_argument(
        "--gui",
        action="store_true",
        help="launch the Tkinter GUI (status display, on-screen jog pad) instead of the CLI loop",
    )
    args = parser.parse_args(argv)
    if args.deadman_timeout is None:
        args.deadman_timeout = args.poll_interval * 3

    if args.gui:
        from .gui import run_gui  # deferred: only needs tkinter when --gui is actually used

        return run_gui(args)
    return _run(args)


if __name__ == "__main__":
    sys.exit(main())
