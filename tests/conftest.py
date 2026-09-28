import pytest


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """Las pruebas nunca escriben en la carpeta de datos real del usuario."""
    monkeypatch.setenv("FENORUBUS_DATA", str(tmp_path / "appdata"))
