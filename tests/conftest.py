import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def clean_archive(hass):
    """De testomgeving deelt één configmap: elk test een leeg toegangsarchief geven."""
    import os
    from custom_components.btechnics_vto.const import ARCHIVE_FILE

    base = hass.config.path(ARCHIVE_FILE)

    def _rm():
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(base + suffix):
                os.remove(base + suffix)

    _rm()
    yield
    _rm()


@pytest.fixture(autouse=True)
def no_realtime_events(monkeypatch):
    """Geen DHIP-verbinding in de tests (geen netwerk, geen achtergrondthreads)."""
    import custom_components.btechnics_vto as integ
    monkeypatch.setattr(integ, "EVENTS_ENABLED", False)
