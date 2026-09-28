import builtins
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def cp1252_default_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decode text files opened without an explicit encoding as cp1252.

    On Windows, `open()` and `Path.read_text()` fall back to the ANSI code page
    (cp1252 on Western European installs) rather than UTF-8. Config files are
    written as UTF-8, so any read that relies on the default misdecodes them.
    """
    original_open = builtins.open
    original_path_open = Path.open

    def _default_encoding(mode: str, encoding: str | None) -> str | None:
        if "b" not in mode and encoding in (None, "locale"):
            return "cp1252"
        return encoding

    def _open(
        file: Any, mode: str = "r", buffering: int = -1, encoding=None, *args, **kwargs
    ):
        return original_open(
            file, mode, buffering, _default_encoding(mode, encoding), *args, **kwargs
        )

    def _path_open(
        self: Path, mode: str = "r", buffering: int = -1, encoding=None, *args, **kwargs
    ):
        return original_path_open(
            self, mode, buffering, _default_encoding(mode, encoding), *args, **kwargs
        )

    monkeypatch.setattr(builtins, "open", _open)
    monkeypatch.setattr(Path, "open", _path_open)
