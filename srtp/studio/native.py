"""Windows child-window plumbing for this process's own game window only."""
import ctypes
import os
from ctypes import wintypes

def embed_owned_window(child, parent):
    u=ctypes.windll.user32
    u.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype=wintypes.DWORD
    pid=wintypes.DWORD(); u.GetWindowThreadProcessId(child,ctypes.byref(pid))
    if pid.value!=os.getpid():raise ValueError('Only the current worker window may be embedded')
    u.GetWindowLongW.argtypes=[wintypes.HWND,ctypes.c_int]; u.GetWindowLongW.restype=ctypes.c_long
    u.SetWindowLongW.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_long]
    u.SetParent.argtypes=[wintypes.HWND,wintypes.HWND]; u.SetParent.restype=wintypes.HWND
    style=u.GetWindowLongW(child,-16)
    style=(style & ~0x80000000 & ~0x00C00000 & ~0x00040000)|0x40000000|0x10000000
    u.SetWindowLongW(child,-16,style); u.SetParent(child,parent)
    u.GetParent.argtypes=[wintypes.HWND]; u.GetParent.restype=wintypes.HWND
    if int(u.GetParent(child) or 0)!=int(parent):
        raise OSError('Unable to attach game window to Studio viewport')
    rect=wintypes.RECT(); u.GetClientRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
    u.GetClientRect(parent,ctypes.byref(rect))
    resize_owned_window(child,rect.right,rect.bottom)

def resize_owned_window(child,width,height):
    u=ctypes.windll.user32
    u.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint]
    u.SetWindowPos(child,None,0,0,max(64,int(width)),max(64,int(height)),0x0010|0x0004|0x0040|0x0020)
