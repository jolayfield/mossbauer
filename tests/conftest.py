import os
from pathlib import Path
import pytest

DATA = Path(__file__).parent / "data"
PRIVATE_TESTS = "MOSSBAUER_PRIVATE_TESTS"


def pytest_configure(config):
    """Opt-in extra tests kept outside the repository (for example, checks against unpublished calculations):
    when MOSSBAUER_PRIVATE_TESTS names a folder of tests, a plain ``pytest`` run collects it as well as tests/."""
    folder = os.environ.get(PRIVATE_TESTS)
    if not folder:
        return
    path = Path(folder).expanduser().resolve()
    if not path.is_dir():
        raise pytest.UsageError(f"{PRIVATE_TESTS}={folder} is not a folder")
    if config.args_source == pytest.Config.ArgsSource.TESTPATHS:
        config.args.append(str(path))


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
