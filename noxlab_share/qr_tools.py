from __future__ import annotations

import ctypes
import io
import platform
from pathlib import Path

import qrcode
from PIL import Image


def create_qr_image(data: str, size: int = 280) -> Image.Image:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=3,
    )
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return image.resize((size, size), Image.Resampling.NEAREST)


def save_qr_image(image: Image.Image, path: Path) -> None:
    image.save(path, format="PNG")


def copy_image_to_clipboard(image: Image.Image) -> bool:
    """Copy a PIL image to the Windows clipboard as CF_DIB."""
    if platform.system() != "Windows":
        return False

    output = io.BytesIO()
    image.convert("RGB").save(output, "BMP")
    dib_data = output.getvalue()[14:]
    output.close()

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    cf_dib = 8
    gmem_moveable = 0x0002

    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_int
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = ctypes.c_int
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = ctypes.c_int
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.c_int
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.restype = ctypes.c_void_p

    if not user32.OpenClipboard(None):
        return False

    handle = None
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(gmem_moveable, len(dib_data))
        if not handle:
            return False

        locked = kernel32.GlobalLock(handle)
        if not locked:
            kernel32.GlobalFree(handle)
            handle = None
            return False

        ctypes.memmove(locked, dib_data, len(dib_data))
        kernel32.GlobalUnlock(handle)

        if not user32.SetClipboardData(cf_dib, handle):
            kernel32.GlobalFree(handle)
            handle = None
            return False

        handle = None
        return True
    finally:
        if handle:
            kernel32.GlobalFree(handle)
        user32.CloseClipboard()
