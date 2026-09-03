from __future__ import annotations

import ctypes
from ctypes import wintypes

from hotkeys import KeyEvent


WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
WM_APP_COMMAND = 0x8001
PM_NOREMOVE = 0x0000
LLKHF_INJECTED = 0x10

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


def event_from_message(message: int, data: KBDLLHOOKSTRUCT) -> KeyEvent | None:
    if message in (WM_KEYDOWN, WM_SYSKEYDOWN):
        is_keydown = True
    elif message in (WM_KEYUP, WM_SYSKEYUP):
        is_keydown = False
    else:
        return None
    return KeyEvent(
        int(data.vkCode),
        is_keydown,
        bool(data.flags & LLKHF_INJECTED),
    )


HHOOK = ctypes.c_void_p
HINSTANCE = ctypes.c_void_p
HOOKPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class Win32Api:
    def __init__(self) -> None:
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self.user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            HOOKPROC,
            HINSTANCE,
            wintypes.DWORD,
        ]
        self.user32.SetWindowsHookExW.restype = HHOOK

        self.user32.CallNextHookEx.argtypes = [
            HHOOK,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.CallNextHookEx.restype = LRESULT

        self.user32.UnhookWindowsHookEx.argtypes = [HHOOK]
        self.user32.UnhookWindowsHookEx.restype = wintypes.BOOL

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

        self.user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.PostThreadMessageW.restype = wintypes.BOOL

        self.user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.PeekMessageW.restype = wintypes.BOOL

        self.kernel32.GetCurrentThreadId.argtypes = []
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = HINSTANCE

        self.user32.PostQuitMessage.argtypes = [ctypes.c_int]
        self.user32.PostQuitMessage.restype = None

    def install_hook(self, callback: HOOKPROC) -> HHOOK:
        module_handle = self.kernel32.GetModuleHandleW(None)
        hook = self.user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            callback,
            module_handle,
            0,
        )
        if not hook:
            raise ctypes.WinError(ctypes.get_last_error())
        return hook

    def initialize_message_queue(self) -> int:
        message = wintypes.MSG()
        return int(
            self.user32.PeekMessageW(
                ctypes.byref(message),
                None,
                0,
                0,
                PM_NOREMOVE,
            )
        )

    def call_next(
        self,
        hook: HHOOK,
        n_code: int,
        w_param: int,
        l_param: int,
    ) -> int:
        return int(self.user32.CallNextHookEx(hook, n_code, w_param, l_param))

    def unhook(self, hook: HHOOK) -> bool:
        return bool(self.user32.UnhookWindowsHookEx(hook))

    def get_message(self, message: wintypes.MSG) -> int:
        return int(self.user32.GetMessageW(ctypes.byref(message), None, 0, 0))

    def translate(self, message: wintypes.MSG) -> bool:
        return bool(self.user32.TranslateMessage(ctypes.byref(message)))

    def dispatch(self, message: wintypes.MSG) -> int:
        return int(self.user32.DispatchMessageW(ctypes.byref(message)))

    def post_thread_message(
        self,
        thread_id: int,
        message: int,
        w_param: int = 0,
        l_param: int = 0,
    ) -> bool:
        return bool(
            self.user32.PostThreadMessageW(
                thread_id,
                message,
                w_param,
                l_param,
            )
        )

    def post_quit(self, exit_code: int = 0) -> None:
        self.user32.PostQuitMessage(exit_code)
