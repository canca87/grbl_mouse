# grbl_mouse

Repurposes a wired Kensington Expert Mouse into a dedicated jog controller for a
GRBL-based XYZ stage (Arduino + GRBL firmware), instead of using it as a system
pointing device.

- Ball X/Y motion → stage X/Y jog moves
- Scroll wheel → stage Z jog moves
- Left-main/right-main buttons → adjust jog **gain** (sensitivity); feed rate is held
  fixed (`presets.FIXED_FEEDRATE`) — see [presets.py](src/grbl_mouse/presets.py)
- Raw HID reports are read directly from the device (bypassing the OS mouse/cursor
  pipeline); the device's normal system-pointer behavior is detached while in use as a
  controller.
- Jog commands are sent to GRBL over serial using `$J=` relative jog syntax, with the
  real-time jog-cancel byte (`0x85`) for responsiveness.
- No proprietary Kensington driver/SDK — the device enumerates as a standard
  boot-protocol HID mouse, so raw report parsing is sufficient.

## ⚠️ Safety note

This software can move a physical machine. By default, all motion-capable code paths
run in a disabled/dry-run mode — jog commands are built and logged but **not** written
to the serial port — until motion is explicitly authorized for a given run
(`--confirm-motion`; see [safety.py](src/grbl_mouse/safety.py) and
[CLAUDE.md](CLAUDE.md)). Before enabling real motion, confirm the stage's travel area
is clear and an e-stop is within reach. This project's specific hardware relies on GRBL
hardware limit switches rather than soft limits (`$20`–`$22`) — check your own machine's
configuration.

## Status

Core functionality (HID input, GRBL link, jog dispatch, CLI, GUI) is done and
hardware-confirmed on the reference macOS setup described below. Cross-platform
packaging ([.github/workflows/build.yml](.github/workflows/build.yml), builds tagged
releases for Linux/Windows/macOS via PyInstaller) builds successfully on all three
platforms, but only macOS has been run against real HID/serial hardware — Windows
specifically needed real platform-specific work, not just "it should work the same":

> **Windows note**: opening this device the way macOS does (which detaches it from the
> OS pointer pipeline for free) does *not* work on Windows — the device keeps working as
> a normal system mouse the whole time, and reading it concurrently can fail outright.
> Windows uses a different mechanism entirely
> ([win32_raw_input_backend.py](src/grbl_mouse/hid_input/win32_raw_input_backend.py)),
> the Raw Input API, exactly as this project's original brief specified. Hardware-
> confirmed via a standalone diagnostic probe: device detection, button mapping, and
> wheel scaling are all correct, and events are correctly filtered to only the selected
> device (other mice on the machine are ignored). **Known limitation, by design**: the
> Expert Mouse continues to work as a normal system pointer the whole time it's also
> delivering jog data — real testing showed the cursor keeps moving/clicking normally.
> There's no supported per-device fix for this on Windows (the APIs that pin/hide the
> cursor act on the single shared system cursor, not per-device, and would affect every
> other connected mouse too — this was tested and deliberately rejected; a flag that was
> supposed to help here, `RIDEV_NOLEGACY`, was tried and then removed after it turned out
> to also make the `--gui` window itself unresponsive to all mouse input, for no actual
> benefit). Workaround: keep the Expert Mouse away from anything clickable while jogging;
> this doesn't affect motion safety, since jog commands still require the separate
> `--confirm-motion` gate regardless. If jog input looks wrong in some other way (wrong
> direction, Z-axis way too sensitive or dead), that module's docstring and
> [win32_translate.py](src/grbl_mouse/hid_input/win32_translate.py)'s docstring cover
> what's been verified vs. still assumed. Set `GRBL_MOUSE_WIN32_DEBUG=1` to print raw
> pre-translation data to stderr.

- **HID layer** ([hid_input/](src/grbl_mouse/hid_input/)): Kensington Expert Mouse
  (VID=0x047d PID=0x1020) confirmed; it exposes two HID collections sharing the same
  VID/PID — `usage=0x02` ("Mouse", the real input) and `usage=0x01` ("Pointer", an
  unrelated telemetry stream). Raw report layout and multi-device disambiguation
  (`--hid-serial`, macOS/Linux only — Raw Input doesn't expose HID serial numbers on
  Windows) are hardware-verified. Exclusive-capture (detaching the device from the OS
  pointer pipeline) is hardware-confirmed on macOS; Windows uses a different mechanism,
  see the note above.
- **GRBL link** ([grbl_link/](src/grbl_mouse/grbl_link/)): serial handshake, status
  polling, jog dispatch (`$J=`, `$X`, jog-cancel), and alarm-code awareness
  (`ALARM_DESCRIPTIONS` in [serial_link.py](src/grbl_mouse/grbl_link/serial_link.py))
  are all hardware-verified against a real GRBL 1.1h board.
- **Jog algorithm** ([velocity_jog.py](src/grbl_mouse/grbl_link/velocity_jog.py)): the
  continuous ball-to-jog dispatch went through three designs before it was safe —
  see that module's docstring for the full rationale (two real hardware incidents drove
  the final design: bounded/clamped segment distances sized from this machine's real
  acceleration settings, decoupled poll/segment timing, explicit reversal handling, and
  an independent deadman timeout).
- **Full app loop** ([app.py](src/grbl_mouse/app.py)): wires HID → gain control → jog
  dispatch, with a hardware-confirmed failure-mode review covering GRBL Alarm/Hold/Door
  recovery, HID disconnect/reconnect, total GRBL communication loss (e.g. a
  power-cutting e-stop) vs. transient lag (e.g. settling after a hard-limit halt), and
  dual-identical-device disambiguation.
- **GUI** ([gui.py](src/grbl_mouse/gui.py), `--gui`): Tkinter status display, on-screen
  jog pad (press-and-hold, independent of ball gain for predictable feel), gain and
  axis-mapping controls, and alarm/resume — runs `app.run_gui_worker` on a background
  thread, reusing the same controllers and connect/recovery logic as the CLI path
  rather than a separate implementation (see that function's docstring for why it's a
  parallel loop instead of a retrofit of `_run`'s hardware-hardened one).

## Requirements

- Kensington Expert Mouse (wired, USB HID)
- Arduino running GRBL, connected via USB serial
- Python 3, used only inside this project's own virtual environment (never system
  Python)
- macOS for initial development; Windows/Linux are later deployment targets built via
  a platform-abstracted HID/serial layer
- For the GUI (`--gui`) only: Tk support for Python. On Homebrew Python this is a
  separate formula from Python itself — `pip install` cannot provide it:
  ```bash
  brew install python-tk@3.14   # match your Python's minor version
  ```
  The CLI (`app.py` without `--gui`) doesn't need this at all — `tkinter` is only
  imported when `--gui` is actually passed.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Dependencies are not installed automatically — each install is proposed and confirmed
explicitly (see [CLAUDE.md](CLAUDE.md)). Once confirmed:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

## Usage

### Diagnostic/inspection tools

This project's device-specific constants (VID/PID, raw report byte layout, button
bitmask, jog-timing constants derived from this machine's acceleration settings — see
`app.py`) were all worked out empirically against one specific Expert Mouse unit and
GRBL board. A different unit or machine will likely need to redo that discovery; these
tools are all read-only/no-motion and safe to run without any authorization:

```bash
python -m grbl_mouse.hid_input.debug_dump              # list/inspect HID devices, dump raw reports
python -m grbl_mouse.hid_input.exclusive_capture ...    # verify OS-pointer detachment
python -m grbl_mouse.grbl_link.debug_connect --port ... # verify the GRBL serial handshake
python -m grbl_mouse.grbl_link.debug_settings --port ... # dump $$ settings (accel/max-rate)
```

`debug_jog.py` is the exception — it sends real jog moves and requires
`--confirm-motion`, per the safety note above:

```bash
python -m grbl_mouse.grbl_link.debug_jog --port ... --confirm-motion --unlock --axis X --distance 1 --feedrate 100
```

### Main controller

Dry run (decodes and logs everything, sends nothing to GRBL):

```bash
python -m grbl_mouse.app --port /dev/cu.usbmodemXXXX
```

Live, with motion explicitly authorized (only after the safety checklist above):

```bash
python -m grbl_mouse.app --port /dev/cu.usbmodemXXXX --confirm-motion --unlock
```

Run `python -m grbl_mouse.app --help` for the full flag list (gain/feed tuning, axis
swap/invert, HID device selection, poll/deadman timing).

GUI mode (status display, on-screen jog pad — requires `python-tk`, see Requirements):

```bash
python -m grbl_mouse.app --port /dev/cu.usbmodemXXXX --gui                  # dry run
python -m grbl_mouse.app --port /dev/cu.usbmodemXXXX --gui --confirm-motion --unlock
```

## Building a standalone binary

Tagged pushes (`v*`) trigger [.github/workflows/build.yml](.github/workflows/build.yml),
which builds a single-file executable for Linux/Windows/macOS via PyInstaller and
attaches them to a GitHub Release. To build locally:

```bash
pip install pyinstaller
pyinstaller -y grbl_mouse.spec
./dist/grbl_mouse --help
```

The console-mode build is deliberate — `--port` is a required argument even in `--gui`
mode, so this is always launched from a terminal, not double-clicked.

## Testing

```bash
pytest
```

Unit tests run against fixture data captured from the real hardware; they do not open
a live HID device or serial port. Hardware-in-the-loop verification (HID report
capture, OS pointer detachment, GRBL serial handshake, and any real jog motion) is done
by hand, with the user present, per the project's milestone gates.

## License

[MIT](LICENSE)
