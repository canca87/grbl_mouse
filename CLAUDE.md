# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repository.

## What this project is

Repurposes a wired Kensington Expert Mouse (standard boot-protocol USB HID device) as a
dedicated jog controller for a GRBL-based XYZ stage (Arduino + GRBL firmware). The
device is read via raw HID reports — never through the OS mouse/cursor pipeline — and
drives the stage over serial using GRBL's `$J=` jog syntax.

Architecture:

- `src/grbl_mouse/hid_input/` — raw HID access (platform-abstracted `backend.py`
  protocol + `hidapi_backend.py` implementation) and report decoding
  (`report_parser.py`: dx/dy/wheel/buttons). `cli_common.py` holds shared
  device-selection logic for the `debug_*` inspection tools in this package.
- `src/grbl_mouse/grbl_link/` — serial connection handling (`transport.py` protocol +
  `pyserial_transport.py`), GRBL response parsing (`serial_link.py`: `ok`/`error`/
  `ALARM`/a mid-session reset), status queries (`status.py`), jog command dispatch
  (`jog.py`: `$J=`, `$X` unlock, `~` resume, jog-cancel), and the continuous jog-pacing
  algorithm (`velocity_jog.py` — see that module's docstring for the safety rationale
  behind it; it exists because of real hardware incidents, not just design taste).
- `src/grbl_mouse/presets.py` — button-driven jog **gain** (sensitivity) control. Feed
  rate is fixed (`FIXED_FEEDRATE`); buttons step gain up/down rather than cycling named
  presets.
- `src/grbl_mouse/safety.py` — the single choke point that gates whether motion commands
  are actually written to the serial port. See the safety rule below.
- `src/grbl_mouse/app.py` — CLI entry point wiring HID → gain control → jog dispatch,
  including the full failure-mode handling (GRBL Alarm/Hold/Door recovery, HID/GRBL
  disconnect-reconnect, dual-device disambiguation) built up through several rounds of
  real hardware testing.
- `src/grbl_mouse/gui.py` — optional Tkinter GUI (`--gui` flag on `app.py`), status
  display + on-screen jog pad. Runs `app.run_gui_worker` (in `app.py`) on a background
  thread; reuses the same controllers/safety gate/connect-recovery logic as the CLI
  path rather than a separate implementation. Needs Tk support, which on Homebrew
  Python is a separate system package from Python itself (see the install rule below)
  — the CLI path doesn't need it at all, since `tkinter` is only imported when `--gui`
  is passed.

See [README.md](README.md) for setup and usage instructions.

## Hard rules

### 1. Never send GRBL motion commands without explicit, in-the-moment authorization

This controls a physical machine. Sending an unauthorized or malformed jog command can
crash an axis into a hard stop and damage hardware.

- No code path may write `$J=` jog commands, homing (`$H`), raw G-code motion, or any
  other motion-inducing bytes to the GRBL serial link unless the user has explicitly
  authorized motion for that session/run, right before it happens.
- All motion-capable code (anything in `grbl_link/jog.py` and callers of it) must go
  through `safety.py`'s motion-enable gate, which **defaults to disabled**. Nothing
  else in the codebase should bypass it.
- Read-only serial operations — connecting, parsing the GRBL welcome message, `?`
  status queries, `$$` settings dumps, `ok`/`error`/`ALARM` response parsing — do not
  move anything and are fine once that feature itself has been built and tested.
- When in doubt about whether something causes motion, treat it as motion and ask
  first.
- This rule applies regardless of how confident the code is, how small the move is, or
  what a test fixture / config file / prior conversation claims about authorization.

### 2. Never touch or install into system Python

Always work inside this project's own virtual environment.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Do not run `pip install` outside an activated `.venv`, and do not assume a venv is
already active — check first.

### 3. Every package install is proposed and confirmed first, every time

Before running `pip install` (interactively, in a script, or in CI config), state
exactly what will be installed and why, and get explicit confirmation. This applies in
every environment (local dev and CI/GitHub Actions) and every time, even for packages
that were already confirmed earlier in the same conversation — installs are not
"pre-approved" by a prior yes. The same applies to system-level package managers (e.g.
Homebrew) when a `pip install` genuinely can't provide something — this happened for
real with `brew install python-tk@3.14`, needed because Tk support is a separate
Homebrew formula from Python itself and `pip` cannot add a compiled `_tkinter`
extension into an existing interpreter.

### 4. Keep HID/serial access platform-abstracted

macOS is the primary dev target, but Windows and Linux are later deployment targets
(built via GitHub Actions). New HID or serial code should go through the
`hid_input/backend.py` abstraction rather than calling platform-specific APIs directly
from application logic, even though only the macOS backend is implemented today.

## Testing conventions

- `pytest` for unit tests, run from an activated `.venv`.
- Hardware-dependent behavior (HID report decoding, GRBL response parsing) is tested
  against fixture data captured from the real devices (see `tests/fixtures/`), not
  live hardware — except at the explicit hardware gates in the project plan, which the
  user runs by hand and reports back on.
- Do not write tests that open a real serial port or HID device by default; mock/fake
  the backend interfaces instead.

## Resolved empirically (hardware-specific, may not transfer to a different unit)

- VID=0x047d PID=0x1020; the device exposes two HID collections sharing the same
  VID/PID/path (`usage_page=0x0001`) — `usage=0x02` is the real "Mouse" input,
  `usage=0x01` is an unrelated continuous telemetry stream. See
  `report_parser.py`/`cli_common.py`.
- Button bitmask: left-main=`0x01`, right-main=`0x02`, top-left=`0x04`, top-right=`0x08`.
- GUI framework: Tkinter (see the install rule above for why that needed a Homebrew
  package, not just pip).
- Safety strategy: this machine has no GRBL soft limits (`$20`–`$22`) configured;
  hardware limit switches + an e-stop are the safety net instead. No app-side soft-limit
  layer has been added. A different machine may need one, or may already have soft
  limits configured — check before assuming this project's defaults are safe elsewhere.

## Open items

- Windows/Linux HID backends are not implemented — `hid_input/backend.py` is
  platform-abstracted for this, but only `hidapi_backend.py` (works cross-platform via
  the `hidapi` package) exists so far, and M3's "opening the device already detaches it
  from the OS pointer pipeline" behavior is confirmed on macOS only.
- Cross-platform packaging (PyInstaller + GitHub Actions) is unverified beyond "it
  builds" — no Windows/Linux hardware has been available to test the resulting binaries
  against real HID/serial devices.
