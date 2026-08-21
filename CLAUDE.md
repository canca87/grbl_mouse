# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repository.

## What this project is

Repurposes a wired Kensington Expert Mouse (standard boot-protocol USB HID device) as a
dedicated jog controller for a GRBL-based XYZ stage (Arduino + GRBL firmware). The
device is read via raw HID reports — never through the OS mouse/cursor pipeline — and
drives the stage over serial using GRBL's `$J=` jog syntax.

Architecture:

- `src/grbl_mouse/hid_input/` — raw HID access (platform-abstracted `backend.py`
  protocol), report decoding (`report_parser.py`: dx/dy/wheel/buttons), and platform
  selection (`backend_factory.py`, used by every call site instead of instantiating a
  concrete backend directly — see below for why this matters). `cli_common.py` holds
  shared device-selection logic for the `debug_*` inspection tools in this package.
  - `hidapi_backend.py` (macOS/Linux) — the cross-platform `hidapi` library.
  - `win32_raw_input_backend.py` + `win32_translate.py` (Windows) — a real hardware bug
    report showed `hidapi`'s approach doesn't work on Windows the way it does on macOS:
    opening the device there doesn't preempt the OS's own mouse-class driver, so the
    device keeps working as a normal system pointer and concurrent raw reads can fail
    outright (this manifested as an infinite HID disconnect/reconnect loop). Windows
    needs the Raw Input API (`RegisterRawInputDevices`) instead — a completely different
    mechanism, exactly as this project's original brief specified.
    `win32_translate.py` is the pure, unit-tested translation of Windows'
    already-semantically-parsed `RAWMOUSE` data back into the same 4-byte report format
    `report_parser.py` expects everywhere else; `win32_raw_input_backend.py` is the
    ctypes/Win32 plumbing around it. Hardware-confirmed (via a standalone .NET Raw Input
    probe, independent of this code): device enumeration, button-bit mapping, and
    wheel-delta scaling are all correct — see that module's docstring for what was
    actually found and fixed (a real bug: Windows classifies HID mice as
    `RIM_TYPEMOUSE`, whose info struct has no VID/PID field at all — that has to be
    parsed from the device name string instead). Registration deliberately does NOT use
    `RIDEV_NOLEGACY`, only `RIDEV_INPUTSINK` — see that module's docstring for the two
    real hardware findings that killed it: it never actually detached the Expert Mouse
    from the OS pointer pipeline (confirmed: the cursor kept moving/clicking normally the
    whole time), and worse, with `--gui` it made the Tkinter window permanently
    unresponsive to all mouse input (unclickable, uncloseable) from the moment it opened
    — its legacy-message suppression applies per-application, not just to the specific
    window that registered it, so it silently broke the GUI's own window. Given it
    provided no working benefit while causing that regression, it was dropped entirely.
    The dual-pointer behavior (Expert Mouse keeps working as a normal system pointer
    alongside delivering jog data) is a deliberate, accepted tradeoff either way (see
    that module's docstring for why the alternative — `ClipCursor`/`ShowCursor` — was
    rejected: those act on the single shared system cursor, not per-device, and would
    have frozen every *other* mouse on the machine too). Not yet verified: jog dispatch
    (`$J=` sending) through the compiled `.exe` end-to-end with motion enabled.
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
`hid_input/backend.py` abstraction (via `backend_factory.py`'s `make_hid_backend()` —
never instantiate `HidApiBackend`/`Win32RawInputBackend` directly in application code)
rather than calling platform-specific APIs directly. This paid off concretely: macOS's
"opening the device already detaches it from the OS pointer pipeline" behavior turned
out not to hold on Windows at all (see the architecture section above), and the fix was
a second backend behind the same interface, not a rewrite of anything that calls it.

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

- Linux has no dedicated HID backend yet — falls back to `hidapi_backend.py` (same as
  macOS), unverified on real Linux hardware. Likely has the same driver-contention
  problem Windows did (opening `hidraw` doesn't detach a device from the desktop's
  cursor pipeline by default either). The planned real fix, once Linux hardware is
  available to test against: keep reading jog data from `/dev/hidrawX` exactly like
  macOS (same 4-byte report format, no translation layer needed, unlike Windows), and
  separately open that same physical device's sibling `/dev/input/eventX` node purely
  to call the `EVIOCGRAB` ioctl on it — a genuine per-device exclusive grab that Windows
  has no equivalent of (see the Windows dual-pointer note below). Needs its own backend
  behind `backend_factory.py`, same pattern as `win32_raw_input_backend.py`, and
  correlating the two device nodes for the same physical device via sysfs.
- Windows (`win32_raw_input_backend.py`): enumeration, button-bit mapping, and
  wheel-delta scaling are now hardware-confirmed correct (see that module's docstring).
  A real hardware bug found and fixed in the first Windows build: `RegisterRawInputDevices`
  can only register for the whole Mouse usage class, not a single device, so WM_INPUT
  fired for every mouse on the machine — moving an unrelated PC mouse was driving jog
  motion too. Fixed by resolving and storing the selected device's specific `hDevice`
  handle at `open()` time and filtering every event against it
  (`_find_device_handle()`); this filtering is required for safe operation, not
  optional. **Known, accepted limitation, separate from the above**: the Expert Mouse continues to behave as a normal
  system pointer on Windows the whole time it's also delivering jog data — Windows has
  no supported per-device way to stop this (`RIDEV_NOLEGACY` doesn't touch cursor
  rendering; `ClipCursor`/`ShowCursor` act on the single shared system cursor and would
  affect every other connected mouse too, tested and explicitly rejected for that
  reason). If a genuine future need for true per-device detachment on Windows comes up,
  the real fix is a driver swap (e.g. via Zadig/WinUSB) away from the inbox HID-mouse
  driver, paired with rewriting this module around WinUSB instead of Raw Input — a much
  bigger effort, not started. `GRBL_MOUSE_WIN32_DEBUG=1` prints raw pre-translation data
  if something still looks off. Not yet verified: jog dispatch (`$J=` sending) through
  the compiled `.exe` end-to-end with motion enabled — only read-only HID capture has
  been hardware-tested on Windows so far.
- Windows `--gui` froze solid (unclickable/undraggable/uncloseable, the OS-level "not
  responding" state) from the moment the window opened, regardless of trackball
  activity — status text kept updating fine the whole time (driven by `WM_TIMER` via
  Tk's `.after()`, unaffected), and jog dispatch itself worked correctly. First
  diagnosed (wrongly) as GIL/scheduler contention from the raw-input thread firing a
  Python callback per HID report; that theory was ruled out once hardware testing showed
  the freeze was constant and independent of mouse activity. **Real root cause**:
  `RIDEV_NOLEGACY` — its legacy-mouse-message suppression turned out to apply
  per-application, not just to the specific window that registered it, so it silently
  broke the GUI's own separate Tkinter window in the same process. Fixed by dropping
  `RIDEV_NOLEGACY` entirely (see the architecture section above and that module's
  docstring) — `RIDEV_INPUTSINK` alone is sufficient for `WM_INPUT` delivery and has
  neither problem. The two speculative GIL-contention mitigations
  (`sys.setswitchinterval` in `gui.py`, `SetThreadPriority` in the message-loop thread)
  are harmless and left in place, but were not the actual fix.
- Cross-platform packaging (PyInstaller + GitHub Actions) builds successfully
  (verified with a local macOS build) but is otherwise unverified — no Windows/Linux
  hardware has been available to test the resulting binaries against real devices.
