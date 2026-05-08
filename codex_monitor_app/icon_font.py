import ctypes
import ctypes.util
from pathlib import Path
from typing import Optional

MATERIAL_SYMBOLS_FAMILY = "Material Symbols Rounded"


def register_material_symbols_font() -> bool:
    font_path = Path(__file__).resolve().parent.joinpath(
        "assets",
        "fonts",
        "MaterialSymbolsRounded.ttf",
    )
    if not font_path.exists():
        return False

    core_foundation_path = ctypes.util.find_library("CoreFoundation")
    core_text_path = ctypes.util.find_library("CoreText")
    if not core_foundation_path or not core_text_path:
        return False

    try:
        core_foundation = ctypes.CDLL(core_foundation_path)
        core_text = ctypes.CDLL(core_text_path)

        core_foundation.CFURLCreateFromFileSystemRepresentation.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_long,
            ctypes.c_bool,
        ]
        core_foundation.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
        core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
        core_foundation.CFRelease.restype = None
        core_text.CTFontManagerRegisterFontsForURL.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        core_text.CTFontManagerRegisterFontsForURL.restype = ctypes.c_bool

        encoded_path = str(font_path).encode("utf-8")
        font_url = core_foundation.CFURLCreateFromFileSystemRepresentation(
            None,
            encoded_path,
            len(encoded_path),
            False,
        )
        if not font_url:
            return False

        try:
            return bool(
                core_text.CTFontManagerRegisterFontsForURL(
                    font_url,
                    1,
                    None,
                )
            )
        finally:
            core_foundation.CFRelease(font_url)
    except Exception:
        return False


def material_symbol(codepoint: str) -> Optional[str]:
    try:
        return chr(int(codepoint, 16))
    except ValueError:
        return None
