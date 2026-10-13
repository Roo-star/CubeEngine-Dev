"""Capture the complete Windows client area, including embedded native renderers."""
from pathlib import Path
import sys


def capture_window(window, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        from PIL import ImageGrab
        handle = int(window.winId())
        rectangle = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        user = ctypes.windll.user32
        user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        user.GetForegroundWindow.restype = wintypes.HWND
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        # Desktop pixels are valid only while this window is foreground.
        # Otherwise capture the Qt client itself, never another app covering it.
        foreground = user.GetForegroundWindow()
        if user.GetAncestor(foreground, 2) != user.GetAncestor(handle, 2):
            window.grab().save(str(path))
            return
        if user.GetClientRect(handle, ctypes.byref(rectangle)) and user.ClientToScreen(handle, ctypes.byref(origin)):
            ImageGrab.grab(bbox=(origin.x, origin.y, origin.x + rectangle.right,
                                origin.y + rectangle.bottom), all_screens=True).save(str(path))
            return
    window.grab().save(str(path))
