from pathlib import Path
import pytest

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="session")
def data_dir():
    return DATA


@pytest.fixture
def windows_text_encoding(monkeypatch):
    """Exercise text I/O using the legacy Windows default without changing the OS."""
    import builtins
    import io

    original_open = io.open

    def windows_open(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, **kwargs):
        if "b" not in mode and encoding in (None, "locale"):
            encoding = "cp1252"
        return original_open(file, mode, buffering, encoding, errors, newline, **kwargs)

    monkeypatch.setattr(builtins, "open", windows_open)
    monkeypatch.setattr(io, "open", windows_open)
