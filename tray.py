from __future__ import annotations

import ctypes
import math
import queue
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum, IntEnum
from os import PathLike
from queue import SimpleQueue


WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_CONTEXTMENU = 0x007B
WM_APP_UPDATE = 0x8001
WM_APP_STOP = 0x8002
TRAY_CALLBACK_MESSAGE = 0x8003

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIM_SETVERSION = 0x00000004

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NOTIFYICON_VERSION_4 = 4
NIIF_INFO = 1
MF_BYCOMMAND = 0x00000000
MF_ENABLED = 0x00000000
MF_GRAYED = 0x00000001
MF_STRING = 0x00000000
MF_UNCHECKED = 0x00000000
MF_CHECKED = 0x00000008
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
HWND_MESSAGE = wintypes.HWND(-3)

LRESULT = ctypes.c_ssize_t
UINT_PTR = ctypes.c_size_t
HINSTANCE = ctypes.c_void_p
HICON = ctypes.c_void_p
HCURSOR = ctypes.c_void_p
HBRUSH = ctypes.c_void_p
HMENU = ctypes.c_void_p
HANDLE = ctypes.c_void_p

WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", HINSTANCE),
        ("hIcon", HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", HICON),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _NID_UNION(ctypes.Union):
    _fields_ = [("uTimeout", wintypes.UINT), ("uVersion", wintypes.UINT)]


class NOTIFYICONDATAW(ctypes.Structure):
    _anonymous_ = ("version",)
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("version", _NID_UNION),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", HICON),
    ]


class MenuCommand(IntEnum):
    LOCK = 1
    UNLOCK = 2
    TOGGLE = 3
    SETTINGS = 4
    STARTUP = 5
    EXIT = 6


class TrayAction(Enum):
    LOCK = "lock"
    UNLOCK = "unlock"
    TOGGLE = "toggle"
    SETTINGS = "settings"
    STARTUP = "startup"
    EXIT = "exit"


@dataclass(frozen=True)
class MenuItemState:
    command: MenuCommand
    label: str
    is_enabled: bool = True
    is_checked: bool = False


@dataclass(frozen=True)
class MenuState:
    items: tuple[MenuItemState, ...]

    def _item(self, command: MenuCommand) -> MenuItemState:
        for item in self.items:
            if item.command == command:
                return item
        raise KeyError(command)

    def enabled(self, command: MenuCommand) -> bool:
        return self._item(command).is_enabled

    def checked(self, command: MenuCommand) -> bool:
        return self._item(command).is_checked


def build_menu_state(*, locked: bool, startup_enabled: bool) -> MenuState:
    return MenuState(
        items=(
            MenuItemState(MenuCommand.LOCK, "Lock", not locked),
            MenuItemState(MenuCommand.UNLOCK, "Unlock", locked),
            MenuItemState(MenuCommand.TOGGLE, "Toggle"),
            MenuItemState(MenuCommand.SETTINGS, "Settings", not locked),
            MenuItemState(MenuCommand.STARTUP, "Start with Windows", True, startup_enabled),
            MenuItemState(MenuCommand.EXIT, "Exit"),
        )
    )


_COMMAND_TO_ACTION = {
    MenuCommand.LOCK: TrayAction.LOCK,
    MenuCommand.UNLOCK: TrayAction.UNLOCK,
    MenuCommand.TOGGLE: TrayAction.TOGGLE,
    MenuCommand.SETTINGS: TrayAction.SETTINGS,
    MenuCommand.STARTUP: TrayAction.STARTUP,
    MenuCommand.EXIT: TrayAction.EXIT,
}


class TrayState:
    def __init__(
        self,
        actions: SimpleQueue[TrayAction],
        locked: bool = False,
        startup_enabled: bool = False,
        notifications_enabled: bool = True,
        tooltip: str | None = None,
    ) -> None:
        self.actions = actions
        self.locked = locked
        self.startup_enabled = startup_enabled
        self.notifications_enabled = notifications_enabled
        self.tooltip = tooltip or _tooltip(locked)

    def handle_menu_command(self, command: MenuCommand | int) -> None:
        self.actions.put(_COMMAND_TO_ACTION[MenuCommand(command)])

    def apply_update(self, update: TrayUpdate) -> tuple[bool, bool]:
        previous_locked = self.locked
        previous_tooltip = self.tooltip
        if update.locked is not None:
            self.locked = bool(update.locked)
            self.tooltip = update.tooltip or _tooltip(self.locked)
        elif update.tooltip is not None:
            self.tooltip = update.tooltip
        if update.startup_enabled is not None:
            self.startup_enabled = bool(update.startup_enabled)
        if update.notifications_enabled is not None:
            self.notifications_enabled = bool(update.notifications_enabled)
        return (
            previous_locked != self.locked,
            previous_tooltip != self.tooltip,
        )


def _tooltip(locked: bool) -> str:
    return "CatLocker — Keyboard Locked" if locked else "CatLocker — Keyboard Unlocked"


@dataclass(frozen=True, slots=True)
class TrayUpdate:
    locked: bool | None = None
    startup_enabled: bool | None = None
    notifications_enabled: bool | None = None
    tooltip: str | None = None
    reason: str | None = None


class TrayStopped(RuntimeError):
    pass


class TrayTimeout(TimeoutError):
    pass


def _require_finite_timeout(timeout: float, operation: str) -> float:
    try:
        value = float(timeout)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"{operation} timeout must be finite and non-negative."
        ) from exc
    if not math.isfinite(value) or value < 0 or value > threading.TIMEOUT_MAX:
        raise ValueError(
            f"{operation} timeout must be finite and non-negative, "
            f"and no greater than {threading.TIMEOUT_MAX}."
        )
    return value


def _raise_win32_error(message: str) -> None:
    raise ctypes.WinError(ctypes.get_last_error(), message)


class Win32TrayApi:
    """Small Win32 seam used by :class:`NativeTray`."""

    def __init__(self) -> None:
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.shell32 = ctypes.WinDLL("shell32", use_last_error=True)

        self.user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        self.user32.RegisterClassExW.restype = wintypes.ATOM
        self.user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            HMENU,
            HINSTANCE,
            wintypes.LPVOID,
        ]
        self.user32.CreateWindowExW.restype = wintypes.HWND
        self.user32.DestroyWindow.argtypes = [wintypes.HWND]
        self.user32.DestroyWindow.restype = wintypes.BOOL
        self.user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.DefWindowProcW.restype = LRESULT
        self.user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        self.user32.RegisterWindowMessageW.restype = wintypes.UINT
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = HINSTANCE
        self.shell32.Shell_NotifyIconW.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(NOTIFYICONDATAW),
        ]
        self.shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        self.user32.LoadImageW.argtypes = [
            HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self.user32.LoadImageW.restype = HANDLE
        self.user32.DestroyIcon.argtypes = [HICON]
        self.user32.DestroyIcon.restype = wintypes.BOOL
        self.user32.CreatePopupMenu.argtypes = []
        self.user32.CreatePopupMenu.restype = HMENU
        self.user32.AppendMenuW.argtypes = [
            HMENU,
            wintypes.UINT,
            UINT_PTR,
            wintypes.LPCWSTR,
        ]
        self.user32.AppendMenuW.restype = wintypes.BOOL
        self.user32.EnableMenuItem.argtypes = [HMENU, wintypes.UINT, wintypes.UINT]
        self.user32.EnableMenuItem.restype = wintypes.BOOL
        self.user32.CheckMenuItem.argtypes = [HMENU, wintypes.UINT, wintypes.UINT]
        self.user32.CheckMenuItem.restype = wintypes.DWORD
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user32.SetForegroundWindow.restype = wintypes.BOOL
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.user32.GetCursorPos.restype = wintypes.BOOL
        self.user32.TrackPopupMenu.argtypes = [
            HMENU,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        ]
        self.user32.TrackPopupMenu.restype = wintypes.UINT
        self.user32.DestroyMenu.argtypes = [HMENU]
        self.user32.DestroyMenu.restype = wintypes.BOOL
        self.user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.GetMessageW.restype = wintypes.BOOL
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.TranslateMessage.restype = wintypes.BOOL
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.DispatchMessageW.restype = LRESULT
        self.user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.PostMessageW.restype = wintypes.BOOL
        self.user32.PostQuitMessage.argtypes = [ctypes.c_int]
        self.user32.PostQuitMessage.restype = None

        self.taskbar_created_message = 0

    def load_icon(self, icon_path: PathLike[str] | str) -> HANDLE:
        icon = self.user32.LoadImageW(
            None,
            str(icon_path),
            IMAGE_ICON,
            0,
            0,
            LR_LOADFROMFILE,
        )
        if not icon:
            _raise_win32_error("LoadImageW failed")
        return icon

    def destroy_icon(self, icon_handle: HANDLE) -> bool:
        return bool(self.user32.DestroyIcon(icon_handle))

    def create_window(self, window_class: WNDCLASSEXW) -> wintypes.HWND:
        module_handle = self.kernel32.GetModuleHandleW(None)
        if not module_handle:
            _raise_win32_error("GetModuleHandleW failed")
        window_class.hInstance = module_handle
        atom = self.user32.RegisterClassExW(ctypes.byref(window_class))
        if not atom and ctypes.get_last_error() != 1410:
            _raise_win32_error("RegisterClassExW failed")
        window_handle = self.user32.CreateWindowExW(
            0,
            window_class.lpszClassName,
            "CatLocker",
            0,
            0,
            0,
            0,
            0,
            HWND_MESSAGE,
            None,
            module_handle,
            None,
        )
        if not window_handle:
            _raise_win32_error("CreateWindowExW failed")
        return window_handle

    def destroy_window(self, window_handle: wintypes.HWND) -> bool:
        return bool(self.user32.DestroyWindow(window_handle))

    def register_taskbar_created(self) -> int:
        message = self.user32.RegisterWindowMessageW("TaskbarCreated")
        if not message:
            _raise_win32_error("RegisterWindowMessageW failed")
        self.taskbar_created_message = int(message)
        return self.taskbar_created_message

    @staticmethod
    def _notify(data: NOTIFYICONDATAW, operation: int) -> bool:
        raise NotImplementedError

    def add_icon(self, data: NOTIFYICONDATAW) -> bool:
        if not self.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            _raise_win32_error("NIM_ADD failed")
        return True

    def set_version(self, data: NOTIFYICONDATAW) -> bool:
        data.uVersion = NOTIFYICON_VERSION_4
        data.uFlags = NIF_MESSAGE
        if not self.shell32.Shell_NotifyIconW(
            NIM_SETVERSION, ctypes.byref(data)
        ):
            _raise_win32_error("NIM_SETVERSION failed")
        return True

    def modify_icon(self, data: NOTIFYICONDATAW) -> bool:
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        if not self.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data)):
            _raise_win32_error("NIM_MODIFY failed")
        return True

    def delete_icon(self, data: NOTIFYICONDATAW) -> bool:
        return bool(
            self.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
        )

    def show_menu(self, state: MenuState, window_handle: wintypes.HWND) -> int:
        menu = self.user32.CreatePopupMenu()
        if not menu:
            _raise_win32_error("CreatePopupMenu failed")
        try:
            for item in state.items:
                if not self.user32.AppendMenuW(
                    menu,
                    MF_STRING,
                    UINT_PTR(int(item.command)),
                    item.label,
                ):
                    _raise_win32_error("AppendMenuW failed")
                enable_flags = MF_ENABLED if item.is_enabled else MF_GRAYED
                self.user32.EnableMenuItem(
                    menu,
                    int(item.command),
                    MF_BYCOMMAND | enable_flags,
                )
                check_flags = MF_CHECKED if item.is_checked else MF_UNCHECKED
                self.user32.CheckMenuItem(
                    menu,
                    int(item.command),
                    MF_BYCOMMAND | check_flags,
                )
            self.user32.SetForegroundWindow(window_handle)
            cursor = wintypes.POINT()
            if not self.user32.GetCursorPos(ctypes.byref(cursor)):
                _raise_win32_error("GetCursorPos failed")
            return int(
                self.user32.TrackPopupMenu(
                    menu,
                    TPM_RETURNCMD | TPM_RIGHTBUTTON,
                    cursor.x,
                    cursor.y,
                    0,
                    window_handle,
                    None,
                )
            )
        finally:
            self.user32.DestroyMenu(menu)

    def show_notification(
        self,
        data: NOTIFYICONDATAW,
        title: str,
        message: str,
    ) -> bool:
        notification = NOTIFYICONDATAW.from_buffer_copy(bytes(data))
        notification.uFlags = NIF_INFO
        notification.szInfoTitle = title
        notification.szInfo = message
        notification.dwInfoFlags = NIIF_INFO
        if not self.shell32.Shell_NotifyIconW(
            NIM_MODIFY, ctypes.byref(notification)
        ):
            _raise_win32_error("NIF_INFO notification failed")
        return True

    def post_message(
        self,
        window_handle: wintypes.HWND,
        message: int,
        wparam: int = 0,
        lparam: int = 0,
    ) -> bool:
        return bool(
            self.user32.PostMessageW(window_handle, message, wparam, lparam)
        )

    def quit_message(self) -> None:
        self.user32.PostQuitMessage(0)

    def message_loop(self, window_proc: WNDPROC) -> None:
        message = wintypes.MSG()
        while True:
            result = int(self.user32.GetMessageW(ctypes.byref(message), None, 0, 0))
            if result == -1:
                _raise_win32_error("GetMessageW failed")
            if result == 0:
                return
            self.user32.TranslateMessage(ctypes.byref(message))
            self.user32.DispatchMessageW(ctypes.byref(message))

    def def_window_proc(
        self,
        window_handle: wintypes.HWND,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        return int(
            self.user32.DefWindowProcW(window_handle, message, wparam, lparam)
        )


class NativeTray:
    """Own the notification icon and its window from one native thread."""

    _window_class_name = "CatLockerTrayWindow"
    _notification_reasons = frozenset(("toggle", "emergency"))

    def __init__(
        self,
        *,
        actions: SimpleQueue[TrayAction],
        startup_enabled: bool,
        notifications: bool,
        icon_path: PathLike[str] | str,
        api: Win32TrayApi | object | None = None,
    ) -> None:
        self.api = api if api is not None else Win32TrayApi()
        self.actions = actions
        self.icon_path = icon_path
        self.state = TrayState(
            actions=actions,
            startup_enabled=startup_enabled,
            notifications_enabled=notifications,
        )

        self.thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.thread_id: int | None = None
        self.window_handle: wintypes.HWND | int | None = None
        self.icon_handle: HANDLE | int | None = None
        self.notify_data: NOTIFYICONDATAW | None = None
        self.window_class: WNDCLASSEXW | None = None
        self.window_proc: WNDPROC | None = None
        self.taskbar_created_message: int | None = None
        self.installation_exception: BaseException | None = None
        self.message_loop_exception: BaseException | None = None
        self.cleanup_exception: BaseException | None = None

        self._updates: SimpleQueue[TrayUpdate] = SimpleQueue()
        self._startup_condition = threading.Condition()
        self._startup_cancelled = threading.Event()
        self._cleanup_done = False
        self._icon_destroyed = False
        self._icon_deleted = False
        self._window_destroyed = False

    def is_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, timeout: float = 1.0) -> None:
        timeout = _require_finite_timeout(timeout, "start")
        if self.thread is not None:
            raise TrayStopped("Native tray has already been started.")
        self.thread = threading.Thread(
            target=self._run,
            name="CatLockerNativeTray",
            daemon=True,
        )
        self.thread.start()
        deadline = time.monotonic() + timeout
        with self._startup_condition:
            while not self.ready.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._startup_cancelled.set()
                    self._startup_condition.notify_all()
                    raise TrayTimeout("Timed out starting native tray.")
                self._startup_condition.wait(remaining)
        if self.installation_exception is not None:
            raise self.installation_exception

    def stop(self, timeout: float = 1.0) -> None:
        timeout = _require_finite_timeout(timeout, "stop")
        thread = self.thread
        if thread is None or not thread.is_alive():
            return
        if thread is threading.current_thread():
            raise TrayStopped("Native tray cannot stop itself.")

        deadline = time.monotonic() + timeout
        if self.window_handle is None:
            self._startup_cancelled.set()
        else:
            try:
                posted = self.api.post_message(
                    self.window_handle,
                    WM_APP_STOP,
                    0,
                    0,
                )
            except BaseException as exc:
                raise TrayStopped(
                    "Native tray stopped accepting messages."
                ) from exc
            if not posted:
                raise TrayStopped("Native tray stopped accepting messages.")

        remaining = max(0.0, deadline - time.monotonic())
        thread.join(remaining)
        if thread.is_alive():
            raise TrayTimeout("Timed out stopping native tray.")
        if self.cleanup_exception is not None:
            raise TrayStopped("Native tray cleanup failed.") from self.cleanup_exception

    def post_update(self, update: TrayUpdate) -> None:
        if not isinstance(update, TrayUpdate):
            raise TypeError("Native tray updates must be TrayUpdate values.")
        self._updates.put(update)
        window_handle = self.window_handle
        if self.is_alive() and window_handle is not None:
            try:
                posted = self.api.post_message(window_handle, WM_APP_UPDATE, 0, 0)
            except BaseException as exc:
                raise TrayStopped(
                    "Native tray stopped accepting updates."
                ) from exc
            if not posted:
                raise TrayStopped("Native tray stopped accepting updates.")

    def post_notifications_enabled(self, enabled: bool) -> None:
        self.post_update(TrayUpdate(notifications_enabled=bool(enabled)))

    def _run(self) -> None:
        startup_succeeded = False
        try:
            self.thread_id = threading.get_ident()
            self._initialize_owner()
            self._signal_ready(success=True)
            startup_succeeded = True
            try:
                self.api.message_loop(self.window_proc)
            except BaseException as exc:
                self.message_loop_exception = exc
        except BaseException as exc:
            self.installation_exception = exc
        finally:
            if not startup_succeeded and self.installation_exception is None:
                self.installation_exception = TrayTimeout(
                    "Native tray startup was cancelled."
                )
            self._cleanup_owner()
            if not self.ready.is_set():
                self._signal_ready(success=False)

    def _initialize_owner(self) -> None:
        self.icon_handle = self.api.load_icon(self.icon_path)
        if not self.icon_handle:
            raise OSError("Native tray icon load returned a null handle.")
        self.notify_data = self._new_notify_data()
        self.window_proc = WNDPROC(self._window_proc)
        self.window_class = WNDCLASSEXW(
            cbSize=ctypes.sizeof(WNDCLASSEXW),
            style=0,
            lpfnWndProc=self.window_proc,
            cbClsExtra=0,
            cbWndExtra=0,
            hInstance=None,
            hIcon=None,
            hCursor=None,
            hbrBackground=None,
            lpszMenuName=None,
            lpszClassName=self._window_class_name,
            hIconSm=None,
        )
        self._check_startup_cancelled()
        self.window_handle = self.api.create_window(self.window_class)
        if not self.window_handle:
            raise OSError("Native tray window creation returned a null handle.")
        self.notify_data.hWnd = self.window_handle
        self._check_startup_cancelled()
        self.taskbar_created_message = int(self.api.register_taskbar_created())
        if not self.taskbar_created_message:
            raise OSError("TaskbarCreated registration returned a null message.")
        self._check_startup_cancelled()
        self._add_current_icon()
        self._check_startup_cancelled()
        self._drain_updates()

    def _new_notify_data(self) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = TRAY_CALLBACK_MESSAGE
        data.hIcon = self.icon_handle
        data.szTip = self.state.tooltip
        return data

    def _check_startup_cancelled(self) -> None:
        if self._startup_cancelled.is_set():
            raise TrayTimeout("Timed out starting native tray.")

    def _signal_ready(self, *, success: bool) -> None:
        with self._startup_condition:
            self.ready.set()
            self._startup_condition.notify_all()

    def _add_current_icon(self) -> None:
        if self.notify_data is None:
            raise TrayStopped("Native tray notification data is unavailable.")
        result = self.api.add_icon(self.notify_data)
        if result is False:
            raise OSError("NIM_ADD failed.")
        self._icon_added = True
        result = self.api.set_version(self.notify_data)
        if result is False:
            raise OSError("NIM_SETVERSION failed.")

    def _modify_current_icon(self) -> None:
        if self.notify_data is None:
            return
        self.notify_data.szTip = self.state.tooltip
        result = self.api.modify_icon(self.notify_data)
        if result is False:
            raise OSError("NIM_MODIFY failed.")

    def _drain_updates(self) -> TrayUpdate | None:
        newest = None
        while True:
            try:
                newest = self._updates.get_nowait()
            except queue.Empty:
                break
        if newest is None:
            return None
        locked_changed, tooltip_changed = self.state.apply_update(newest)
        if locked_changed or tooltip_changed:
            self._modify_current_icon()
        if (
            newest.reason in self._notification_reasons
            and self.state.notifications_enabled
        ):
            self._show_notification(newest)
        return newest

    def _show_notification(self, update: TrayUpdate) -> None:
        if self.notify_data is None:
            return
        if self.state.locked:
            message = "Cat Mode ON — Keyboard Locked"
        else:
            message = "Cat Mode OFF — Keyboard Unlocked"
        try:
            result = self.api.show_notification(
                self.notify_data,
                "CatLocker",
                message,
            )
            if result is False:
                return
        except Exception:
            return

    def _window_proc(
        self,
        window_handle: wintypes.HWND,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        message = int(message)
        if message == WM_APP_UPDATE:
            self._drain_updates()
            return 0
        if message == WM_APP_STOP:
            self.api.quit_message()
            return 0
        if message == self.taskbar_created_message:
            self._add_current_icon()
            return 0
        if message == TRAY_CALLBACK_MESSAGE:
            event = int(lparam) & 0xFFFF
            if event in (WM_RBUTTONUP, WM_LBUTTONUP, WM_CONTEXTMENU):
                menu_state = build_menu_state(
                    locked=self.state.locked,
                    startup_enabled=self.state.startup_enabled,
                )
                selected = self.api.show_menu(menu_state, window_handle)
                if selected:
                    self.state.handle_menu_command(selected)
            return 0
        def_window_proc = getattr(self.api, "def_window_proc", None)
        if def_window_proc is None:
            return 0
        return int(def_window_proc(window_handle, message, wparam, lparam))

    def _cleanup_owner(self) -> None:
        if self._cleanup_done:
            return
        self._cleanup_done = True
        if self.notify_data is not None and not self._icon_deleted:
            self._icon_deleted = True
            try:
                self.api.delete_icon(self.notify_data)
            except Exception as exc:
                self.cleanup_exception = self.cleanup_exception or exc
        if self.window_handle is not None and not self._window_destroyed:
            self._window_destroyed = True
            try:
                self.api.destroy_window(self.window_handle)
            except Exception as exc:
                self.cleanup_exception = self.cleanup_exception or exc
        if self.icon_handle is not None and not self._icon_destroyed:
            self._icon_destroyed = True
            try:
                self.api.destroy_icon(self.icon_handle)
            except Exception as exc:
                self.cleanup_exception = self.cleanup_exception or exc
