import ctypes
from ctypes import wintypes

from renforge.util import files


def test_win_unicode_string_assigns_a_compatible_buffer_pointer() -> None:
    value, buffer = files._win_unicode_string("activity.jsonl")

    assert value.Buffer == "activity.jsonl"
    assert buffer.value == "activity.jsonl"


def test_win_rename_uses_native_relative_handle_api(monkeypatch) -> None:
    name = "activity.jsonl"

    class FILE_RENAME_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.DWORD),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * (len(name) + 1)),
        ]

    class FakeNtSetInformationFile:
        def __init__(self) -> None:
            self.argtypes = None
            self.restype = None
            self.call = None

        def __call__(self, file_handle, _iosb, info_pointer, size, info_class):
            info = ctypes.cast(
                info_pointer, ctypes.POINTER(FILE_RENAME_INFORMATION)
            ).contents
            self.call = {
                "file_handle": file_handle,
                "root_directory": info.RootDirectory,
                "name_length": info.FileNameLength,
                "name": info.FileName,
                "size": size,
                "info_class": info_class,
            }
            return 0

    set_info = FakeNtSetInformationFile()
    ntdll = type("FakeNtdll", (), {"NtSetInformationFile": set_info})()
    monkeypatch.setattr(files, "_win_ntdll", lambda: ntdll)

    files._win_rename_at(11, 22, name)

    assert set_info.call == {
        "file_handle": 11,
        "root_directory": 22,
        "name_length": len(name) * ctypes.sizeof(wintypes.WCHAR),
        "name": name,
        "size": ctypes.sizeof(FILE_RENAME_INFORMATION),
        "info_class": 10,
    }
