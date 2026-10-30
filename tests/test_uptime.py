"""Uptime uitlezen (magicBox.getUpTime): enkel lezen, ook bij een fout geen uitzondering."""
from datetime import datetime, timezone

from custom_components.btechnics_vto.api import VTOClient


def _client(antwoord):
    c = VTOClient.__new__(VTOClient)
    c.call = lambda method, params=None, obj=None: antwoord(method)
    return c


def test_uptime_rekent_starttijd():
    c = _client(lambda m: {"result": True, "params": {"up": {"Last": 3600, "Total": 99999}}})
    u = c.uptime()
    assert u["seconden"] == 3600 and u["totaal_seconden"] == 99999
    start = datetime.fromisoformat(u["gestart_utc"])
    assert abs((datetime.now(timezone.utc) - start).total_seconds() - 3600) < 5


def test_uptime_echt_antwoord_vto4202():
    """Antwoord zoals Cafe en Kammerstraat het gaven op 30/10/2026."""
    u = _client(lambda m: {"result": True, "params": {"info": {"Last": 542193, "Total": 263764}}}).uptime()
    assert u["seconden"] == 542193 and u["totaal_seconden"] == 263764


def test_uptime_zonder_up_sleutel():
    u = _client(lambda m: {"result": True, "params": {"Last": 10, "Total": 20}}).uptime()
    assert u["seconden"] == 10


def test_uptime_geweigerd_en_fout():
    assert "fout" in _client(lambda m: {"result": False, "error": {"code": 268632064}}).uptime()

    def boem(m):
        raise OSError("time-out")
    assert "time-out" in _client(boem).uptime()["fout"]


def test_uptime_in_leeslijst():
    assert "magicBox.getUpTime" in VTOClient.READ_ONLY_RPC
    assert not any(m.split(".")[1].startswith(("set", "reboot", "reset")) for m in VTOClient.READ_ONLY_RPC)
