"""Provide no-follow Windows file handles for WorkspaceReader's bounded list/read operations.

CreateFile opens every ancestor without delete/write sharing and rejects reparse points from the
opened handle, preventing junction traversal and rename/reparse replacement during path-based
scandir. Directory handles request LIST_DIRECTORY as well as READ_ATTRIBUTES so Windows enforces
share checks. File handles become binary CRT descriptors for the same bounded reader used on Unix.
Win32 imports are lazy so other platforms never load Windows DLLs. No third-party dependency.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator


def valid_component(name: str) -> bool:
    """Exclude Win32 aliases, DOS devices and alternate data streams before opening any path."""
    stem = name.split(".", 1)[0].upper()
    return bool(name) and name not in {".", ".."} and not name.endswith((".", " ")) and not (
        any(ord(c) < 32 or c in '<>:"/\\|?*' for c in name)
        or stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
        or (len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3] in "123456789¹²³")
    )


@contextmanager
def open_path(path: Path, *, directory: bool) -> Iterator[int | str]:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    info = kernel.GetFileInformationByHandleEx
    info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    info.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    file_type = kernel.GetFileType
    file_type.argtypes = [wintypes.HANDLE]
    file_type.restype = wintypes.DWORD
    final_name = kernel.GetFinalPathNameByHandleW
    final_name.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    final_name.restype = wintypes.DWORD

    if not path.is_absolute() or any(not valid_component(part) for part in path.parts[1:]):
        raise OSError("unsafe Windows workspace path")
    handles = []
    descriptor = None
    try:
        for current in [*reversed(path.parents), path]:
            is_directory = current != path or directory
            value = str(current)
            if not value.startswith("\\\\?\\"):
                value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
            # LIST_DIRECTORY | READ_ATTRIBUTES for directories; GENERIC_READ for files.
            # Attribute-only handles bypass share checks. Data access plus SHARE_READ only
            # pins every traversed name and prevents concurrent reparse replacement.
            handle = create(value, 0x81 if is_directory else 0x80000000, 1, None, 3,
                            0x00200000 | 0x02000000, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append(handle)
            attributes = (wintypes.DWORD * 2)()
            if not info(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes)):
                raise ctypes.WinError(ctypes.get_last_error())
            if attributes[0] & 0x400 or bool(attributes[0] & 0x10) != is_directory or file_type(handle) != 1:
                raise OSError("unsafe Windows workspace entry")
            # Reject 8.3 aliases too: a short name must not bypass .env/.ssh/secret filtering.
            buffer = ctypes.create_unicode_buffer(32768)
            length = final_name(handle, buffer, len(buffer), 0)
            if not length or length >= len(buffer):
                raise OSError("Windows workspace name cannot be verified")
            if current.name and Path(buffer.value).name.casefold() != current.name.casefold():
                raise OSError("Windows workspace path aliases are not readable")
        if directory:
            yield str(path)
        else:
            descriptor = msvcrt.open_osfhandle(handles[-1], os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
            handles.pop()  # The CRT descriptor now owns this handle.
            yield descriptor
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for handle in reversed(handles):
            close(handle)
