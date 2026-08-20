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

Important, non-obvious limitation: RIDEV_NOLEGACY operates per HID usage
class (Generic Desktop Page / Mouse), not per specific device — while this
backend is open, ALL mice on the system (not just the target Expert
Mouse) stop generating normal system-pointer movement, since Windows has
no native way to seize just one device of a given class. This matches
what the original brief specified, but is a real, user-visible side
effect worth knowing about.

See win32_translate.py for how Windows' already-parsed RAWMOUSE data gets
turned back into the same 4-byte report format report_parser.py expects on
every platform, and for the specific assumptions (button mapping, wheel
scaling) that are UNVERIFIED against real Windows hardware.

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
RIDI_DEVICEINFO = 0x2000000B
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


class RID_DEVICE_INFO_MOUSE(ctypes.Structure):
    _fields_ = [
        ("dwId", wintypes.DWORD),
        ("dwNumberOfButtons", wintypes.DWORD),
        ("dwSampleRate", wintypes.DWORD),
        ("fHasHorizontalWheel", wintypes.BOOL),
    ]


class RID_DEVICE_INFO_KEYBOARD(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSubType", wintypes.DWORD),
        ("dwKeyboardMode", wintypes.DWORD),
        ("dwNumberOfFunctionKeys", wintypes.DWORD),
        ("dwNumberOfIndicators", wintypes.DWORD),
        ("dwNumberOfKeysTotal", wintypes.DWORD),
    ]


class RID_DEVICE_INFO_HID(ctypes.Structure):
    _fields_ = [
        ("dwVendorId", wintypes.DWORD),
        ("dwProductId", wintypes.DWORD),
        ("dwVersionNumber", wintypes.DWORD),
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
    ]


class _RID_DEVICE_INFO_UNION(ctypes.Union):
    _fields_ = [
        ("mouse", RID_DEVICE_INFO_MOUSE),
        ("keyboard", RID_DEVICE_INFO_KEYBOARD),
        ("hid", RID_DEVICE_INFO_HID),
    ]


class RID_DEVICE_INFO(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("dwType", wintypes.DWORD),
        ("u", _RID_DEVICE_INFO_UNION),
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


def _get_device_hid_info(hdevice: wintypes.HANDLE) -> RID_DEVICE_INFO | None:
    info = RID_DEVICE_INFO()
    info.cbSize = ctypes.sizeof(RID_DEVICE_INFO)
    size = wintypes.UINT(ctypes.sizeof(RID_DEVICE_INFO))
    result = user32.GetRawInputDeviceInfoW(hdevice, RIDI_DEVICEINFO, ctypes.byref(info), ctypes.byref(size))
    if result == 0 or result == 0xFFFFFFFF:
        return None
    return info


def _get_device_name(hdevice: wintypes.HANDLE) -> str:
    size = wintypes.UINT(0)
    user32.GetRawInputDeviceInfoW(hdevice, RIDI_DEVICENAME, None, ctypes.byref(size))
    if size.value == 0:
        return ""
    buf = ctypes.create_unicode_buffer(size.value)
    user32.GetRawInputDeviceInfoW(hdevice, RIDI_DEVICENAME, buf, ctypes.byref(size))
    return buf.value


def list_raw_input_mice() -> list[HidDeviceInfo]:
    """Enumerate connected Raw Input devices, filtered to HID-type devices
    recognized as a mouse (matching what report_parser.py/app.py expect —
    usage_page=0x0001 usage=0x0002). Unlike hidapi on macOS/Linux, Raw
    Input doesn't expose HID string descriptors, so product_string/
    manufacturer_string/serial_number are always None here.
    """
    count = wintypes.UINT(0)
    user32.GetRawInputDeviceList(None, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
    if count.value == 0:
        return []
    device_list = (RAWINPUTDEVICELIST * count.value)()
    written = user32.GetRawInputDeviceList(device_list, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
    if written == 0xFFFFFFFF:
        return []

    devices: list[HidDeviceInfo] = []
    for i in range(written):
        entry = device_list[i]
        if entry.dwType != RIM_TYPEMOUSE:
            continue
        info = _get_device_hid_info(entry.hDevice)
        if info is None:
            continue
        devices.append(
            HidDeviceInfo(
                vendor_id=info.hid.dwVendorId,
                product_id=info.hid.dwProductId,
                path=_get_device_name(entry.hDevice) or f"raw-input-handle-{int(entry.hDevice)}",
                usage_page=info.hid.usUsagePage,
                usage=info.hid.usUsage,
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
        user32.RegisterClassW(ctypes.byref(wndclass))

        hwnd = user32.CreateWindowExW(
            0, _WINDOW_CLASS_NAME, "grbl_mouse raw input", 0, 0, 0, 0, 0, HWND_MESSAGE, None, wndclass.hInstance, None
        )
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
        user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(RAWINPUTDEVICE))

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
