"""Windows-specific HidBackend implementation using the Raw Input API
(RegisterRawInputDevices + WM_INPUT), not the cross-platform `hidapi`
library used on macOS/Linux.

Why this exists: on Windows, opening a device the OS already recognizes as
a system mouse via `hidapi`'s CreateFile-based access does NOT preempt the
OS's own mouse-class driver the way macOS's IOHIDManager exclusive-open
does — the device keeps working as a normal system pointer the whole
time, and concurrent raw report reads from that second handle can fail
intermittently. That's what a real hardware bug report on Windows showed:
an infinite HID disconnect/reconnect loop, since `hid_read()` kept raising
errors and the code (correctly, per its own logic) kept treating that as
the device having been unplugged. This project's own original brief
anticipated exactly this, specifying RegisterRawInputDevices with
RIDEV_NOLEGACY as the Windows-specific mechanism — which is what this
module implements.

Important, hardware-confirmed limitation: RIDEV_NOLEGACY does NOT stop the
Expert Mouse from also continuing to work as a normal system pointer while
this backend is open — real hardware testing (a standalone .NET Raw Input
probe, independent of this module) showed the on-screen cursor keeps
moving and clicking normally the whole time raw jog data is also being
captured correctly. This contradicts what the docs originally assumed
(that NOLEGACY would detach the device the way macOS's IOHIDManager
exclusive-open does) — in reality, NOLEGACY only suppresses legacy
WM_MOUSEMOVE/WM_*BUTTONDOWN *messages* to other windows; it does not touch
the separate, lower-level mechanism that actually renders/moves the
cursor. There is no supported per-device way to fix this on Windows: the
system cursor is a single resource shared by every connected mouse, and
the only APIs that pin/hide it (ClipCursor/ShowCursor) act on that shared
cursor, not on any one device — using them here would also freeze/hide
the cursor for every *other* mouse on the machine, which was tested and
explicitly rejected for that reason. This is deliberately NOT implemented;
the Expert Mouse simply continues to behave as a normal pointer alongside
delivering jog data, and other connected mice are completely unaffected.
If a genuine future need for true per-device detachment on Windows comes
up, the real fix is a driver swap (e.g. via Zadig/WinUSB) so Windows never
binds the inbox HID-mouse driver to this device at all, paired with a
rewrite of this module to talk WinUSB directly instead of Raw Input — a
significantly bigger effort than anything here, not started. The actual
long-term plan for true per-device detachment is Linux (see
backend_factory.py's TODO / CLAUDE.md's open items): evdev's EVIOCGRAB
ioctl genuinely does support single-device exclusive grab, unlike
anything Windows offers.

See win32_translate.py for how Windows' already-parsed RAWMOUSE data gets
turned back into the same 4-byte report format report_parser.py expects on
every platform. The button-bit mapping and wheel-delta scaling assumptions
documented there are now hardware-confirmed correct (verified via the same
standalone Raw Input probe: all 4 buttons produced the expected up/down
flags, one wheel detent produced the expected ±120 delta). What remains
unverified on real hardware is jog dispatch through the compiled
PyInstaller .exe end-to-end (motion-enabled) — enumeration and raw input
decode are now confirmed, actual `$J=` sending has not been.

Set the GRBL_MOUSE_WIN32_DEBUG=1 environment variable to print each raw
RAWMOUSE event (before translation) to stderr, to speed up diagnosing a
wrong assumption from a single test run instead of several round trips.
"""

from __future__ import annotations

import sys

if sys.platform != "win32":
    # Checked before any Windows-only import (ctypes.wintypes references
    # ctypes.windll, which doesn't exist elsewhere) so importing this
    # module by mistake on macOS/Linux fails with this clear message
    # instead of a confusing AttributeError from deep inside ctypes.
    raise ImportError("win32_raw_input_backend is only usable on Windows")

import ctypes
import os
import queue
import re
import threading
from ctypes import wintypes

from .backend import HidDeviceInfo
from .win32_translate import ButtonStateTracker, build_report

_DEBUG = os.environ.get("GRBL_MOUSE_WIN32_DEBUG") == "1"

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- constants (winuser.h) ---
RIDEV_INPUTSINK = 0x00000100
RIDEV_NOLEGACY = 0x00000030
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1
RIM_TYPEHID = 2
WM_INPUT = 0x00FF
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
HWND_MESSAGE = wintypes.HWND(-3)

HID_USAGE_PAGE_GENERIC = 0x01
HID_USAGE_GENERIC_MOUSE = 0x02

# GetRawInputDeviceInfo(RIDI_DEVICENAME) device path, e.g.
# \\?\HID#VID_047D&PID_1020&...#{...}. For RIM_TYPEMOUSE devices this path
# string is the ONLY source of VID/PID - see the note on _DEVICE_NAME_VID_PID_RE
# below for why the RIDI_DEVICEINFO struct can't be used instead.
_DEVICE_NAME_VID_PID_RE = re.compile(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})")


# --- structs (winuser.h) ---


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class _RAWMOUSE_BUTTONS(ctypes.Structure):
    _fields_ = [
        ("usButtonFlags", wintypes.USHORT),
        ("usButtonData", wintypes.USHORT),
    ]


class _RAWMOUSE_BUTTONS_UNION(ctypes.Union):
    _fields_ = [
        ("ulButtons", wintypes.ULONG),
        ("buttons", _RAWMOUSE_BUTTONS),
    ]


class RAWMOUSE(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("u", _RAWMOUSE_BUTTONS_UNION),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]


class RAWHID(ctypes.Structure):
    _fields_ = [
        ("dwSizeHid", wintypes.DWORD),
        ("dwCount", wintypes.DWORD),
        ("bRawData", ctypes.c_byte * 1),
    ]


class _RAWINPUT_DATA(ctypes.Union):
    _fields_ = [
        ("mouse", RAWMOUSE),
        ("hid", RAWHID),
    ]


class RAWINPUT(ctypes.Structure):
    _fields_ = [
        ("header", RAWINPUTHEADER),
        ("data", _RAWINPUT_DATA),
    ]


class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [
        ("hDevice", wintypes.HANDLE),
        ("dwType", wintypes.DWORD),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

_WINDOW_CLASS_NAME = "GrblMouseRawInputWindow"


# --- explicit argtypes/restype for every WinAPI call used below ---
#
# ctypes defaults to interpreting a function's return value as a SIGNED
# 32-bit int unless told otherwise. Several of these functions return UINT
# and signal errors with (UINT)-1 (0xFFFFFFFF) - left at ctypes' default,
# that comes back to Python as -1, not 0xFFFFFFFF, so a naive
# `if result == 0xFFFFFFFF` check silently never fires, and a real error
# return gets treated as success with whatever garbage was left in the
# output buffer. This was found to be the actual cause of a real "device
# is physically present but never found" bug report - explicit restype is
# what makes the 0xFFFFFFFF checks below actually work.
user32.GetRawInputDeviceList.argtypes = [
    ctypes.POINTER(RAWINPUTDEVICELIST),
    ctypes.POINTER(wintypes.UINT),
    wintypes.UINT,
]
user32.GetRawInputDeviceList.restype = wintypes.UINT

user32.GetRawInputDeviceInfoW.argtypes = [
    wintypes.HANDLE,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.UINT),
]
user32.GetRawInputDeviceInfoW.restype = wintypes.UINT

user32.GetRawInputData.argtypes = [
    wintypes.HANDLE,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.UINT),
    wintypes.UINT,
]
user32.GetRawInputData.restype = wintypes.UINT

user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
user32.RegisterRawInputDevices.restype = wintypes.BOOL

user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.WORD  # ATOM, 0 on failure

user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HANDLE,  # HMENU - same ABI as HANDLE, avoids depending on wintypes.HMENU existing
    wintypes.HINSTANCE,
    ctypes.c_void_p,
]
user32.CreateWindowExW.restype = wintypes.HWND

user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t

user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL

user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostQuitMessage.restype = None

user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL

user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int  # genuinely a signed 3-way result (-1/0/nonzero) per docs

user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = wintypes.BOOL

user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def _get_device_name(hdevice: wintypes.HANDLE) -> str:
    size = wintypes.UINT(0)
    user32.GetRawInputDeviceInfoW(hdevice, RIDI_DEVICENAME, None, ctypes.byref(size))
    if size.value == 0:
        return ""
    buf = ctypes.create_unicode_buffer(size.value)
    user32.GetRawInputDeviceInfoW(hdevice, RIDI_DEVICENAME, buf, ctypes.byref(size))
    return buf.value


def list_raw_input_mice() -> list[HidDeviceInfo]:
    """Enumerate connected Raw Input devices, filtered to devices Windows
    classifies as RIM_TYPEMOUSE (matching what report_parser.py/app.py
    expect — usage_page=0x0001 usage=0x0002). Unlike hidapi on macOS/Linux,
    Raw Input doesn't expose HID string descriptors, so product_string/
    manufacturer_string/serial_number are always None here.

    VID/PID come from parsing the RIDI_DEVICENAME device path string
    (`\\\\?\\HID#VID_047D&PID_1020&...`), NOT from GetRawInputDeviceInfo's
    RID_DEVICE_INFO struct. This was a real bug found via hardware testing:
    that struct is a union keyed by dwType, and for RIM_TYPEMOUSE devices
    (which is how Windows classifies ALL HID-class mice, not just non-mouse
    HID devices) the populated member is RID_DEVICE_INFO_MOUSE — id/button
    count/sample rate, no VID/PID field at all. Reading `.hid.dwVendorId`
    off a mouse-type device silently reinterprets those unrelated fields as
    VID/PID, producing plausible-looking but wrong values (confirmed via a
    standalone .NET probe script: a real Expert Mouse enumerated as
    RIM_TYPEMOUSE with garbage VID instead of 0x047D), so the VID/PID
    device-selection filter in app.py/cli_common.py silently matched
    nothing on real hardware. Since usage_page/usage for a RIM_TYPEMOUSE
    device is definitionally Generic Desktop/Mouse, those are set directly
    rather than read from the struct too.
    """
    count = wintypes.UINT(0)
    first_result = user32.GetRawInputDeviceList(None, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
    if _DEBUG:
        print(
            f"[win32 raw input] GetRawInputDeviceList(size query) result={first_result} "
            f"count={count.value} last_error={ctypes.get_last_error()}",
            file=sys.stderr,
        )
    if first_result == 0xFFFFFFFF or count.value == 0:
        return []
    device_list = (RAWINPUTDEVICELIST * count.value)()
    written = user32.GetRawInputDeviceList(device_list, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
    if _DEBUG:
        print(
            f"[win32 raw input] GetRawInputDeviceList(fetch) written={written} last_error={ctypes.get_last_error()}",
            file=sys.stderr,
        )
    if written == 0xFFFFFFFF:
        return []

    devices: list[HidDeviceInfo] = []
    for i in range(written):
        entry = device_list[i]
        if _DEBUG:
            print(f"[win32 raw input] device[{i}]: dwType={entry.dwType}", file=sys.stderr)
        if entry.dwType != RIM_TYPEMOUSE:
            continue
        name = _get_device_name(entry.hDevice)
        match = _DEVICE_NAME_VID_PID_RE.search(name)
        if _DEBUG:
            print(
                f"[win32 raw input] device[{i}]: name={name!r} "
                f"vid_pid_match={match.groups() if match else None}",
                file=sys.stderr,
            )
        if match is None:
            continue
        devices.append(
            HidDeviceInfo(
                vendor_id=int(match.group(1), 16),
                product_id=int(match.group(2), 16),
                path=name,
                usage_page=HID_USAGE_PAGE_GENERIC,
                usage=HID_USAGE_GENERIC_MOUSE,
            )
        )
    return devices


class Win32RawInputBackend:
    """HidBackend implementation using Windows' Raw Input API. See the
    module docstring for why this exists instead of using `hidapi` here
    too, and for the RIDEV_NOLEGACY-affects-all-mice caveat.
    """

    def __init__(self) -> None:
        self._device_path: str | None = None
        self._reports: "queue.Queue[bytes]" = queue.Queue()
        self._button_state = ButtonStateTracker()
        self._hwnd: wintypes.HWND | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._wndproc_ref = None  # keep a strong reference so it isn't GC'd

    def list_devices(self) -> list[HidDeviceInfo]:
        return list_raw_input_mice()

    def open(self, device: HidDeviceInfo) -> None:
        if self._thread is not None:
            raise RuntimeError("device already open; call close() first")
        self._device_path = device.path
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise OSError("timed out setting up the Windows raw input window")

    def read(self, timeout_ms: int | None = None) -> bytes | None:
        timeout = None if timeout_ms is None else max(0.0, timeout_ms / 1000.0)
        try:
            return self._reports.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        if self._hwnd is not None:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        self._hwnd = None

    # --- internals: run entirely on the dedicated message-loop thread ---

    def _wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if msg == WM_INPUT:
            self._handle_wm_input(lparam)
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _handle_wm_input(self, lparam: int) -> None:
        size = wintypes.UINT(0)
        user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER))
        if size.value == 0:
            return
        buf = ctypes.create_string_buffer(size.value)
        written = user32.GetRawInputData(
            lparam, RID_INPUT, buf, ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER)
        )
        if written != size.value:
            return
        raw = ctypes.cast(buf, ctypes.POINTER(RAWINPUT)).contents
        if raw.header.dwType != RIM_TYPEMOUSE:
            return

        mouse = raw.data.mouse
        if _DEBUG:
            print(
                f"[win32 raw input] flags={mouse.usFlags:#06x} "
                f"buttonFlags={mouse.buttons.usButtonFlags:#06x} "
                f"buttonData={mouse.buttons.usButtonData} "
                f"x={mouse.lLastX} y={mouse.lLastY}",
                file=sys.stderr,
            )

        buttons = self._button_state.apply(mouse.buttons.usButtonFlags)
        report = build_report(
            buttons=buttons,
            last_x=mouse.lLastX,
            last_y=mouse.lLastY,
            button_flags=mouse.buttons.usButtonFlags,
            button_data=mouse.buttons.usButtonData,
        )
        self._reports.put(report)

    def _message_loop(self) -> None:
        wndproc = WNDPROC(self._wndproc)
        self._wndproc_ref = wndproc  # prevent garbage collection while registered

        wndclass = WNDCLASSW()
        wndclass.lpfnWndProc = ctypes.cast(wndproc, ctypes.c_void_p)
        wndclass.hInstance = kernel32.GetModuleHandleW(None)
        wndclass.lpszClassName = _WINDOW_CLASS_NAME
        atom = user32.RegisterClassW(ctypes.byref(wndclass))
        if _DEBUG:
            print(f"[win32 raw input] RegisterClassW atom={atom} last_error={ctypes.get_last_error()}", file=sys.stderr)

        hwnd = user32.CreateWindowExW(
            0, _WINDOW_CLASS_NAME, "grbl_mouse raw input", 0, 0, 0, 0, 0, HWND_MESSAGE, None, wndclass.hInstance, None
        )
        if _DEBUG:
            print(f"[win32 raw input] CreateWindowExW hwnd={hwnd} last_error={ctypes.get_last_error()}", file=sys.stderr)
        if not hwnd:
            self._ready.set()
            return
        self._hwnd = hwnd

        rid = RAWINPUTDEVICE(
            usUsagePage=HID_USAGE_PAGE_GENERIC,
            usUsage=HID_USAGE_GENERIC_MOUSE,
            dwFlags=RIDEV_NOLEGACY | RIDEV_INPUTSINK,
            hwndTarget=hwnd,
        )
        registered = user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(RAWINPUTDEVICE))
        if _DEBUG:
            print(
                f"[win32 raw input] RegisterRawInputDevices ok={bool(registered)} "
                f"last_error={ctypes.get_last_error()}",
                file=sys.stderr,
            )

        self._ready.set()

        msg = MSG()
        while not self._stop.is_set():
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:  # WM_QUIT or an error
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        # Unregister so RIDEV_NOLEGACY stops suppressing legacy mouse
        # messages system-wide the moment we're done, not just when the
        # process exits.
        unregister = RAWINPUTDEVICE(
            usUsagePage=HID_USAGE_PAGE_GENERIC,
            usUsage=HID_USAGE_GENERIC_MOUSE,
            dwFlags=0x00000001,  # RIDEV_REMOVE
            hwndTarget=None,
        )
        user32.RegisterRawInputDevices(ctypes.byref(unregister), 1, ctypes.sizeof(RAWINPUTDEVICE))
