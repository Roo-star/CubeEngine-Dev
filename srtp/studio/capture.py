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
        if user.GetClientRect(handle, ctypes.byref(rectangle)) and user.ClientToScreen(handle, ctypes.byref(origin)):
            ImageGrab.grab(bbox=(origin.x, origin.y, origin.x + rectangle.right,
                                origin.y + rectangle.bottom), all_screens=True).save(str(path))
            return
    window.grab().save(str(path))
